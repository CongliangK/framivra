"""混音总线（PLAN.md §2 FX 层）：stems → 轨道链 → duck → 求和 → 母带。

三段式设计（为逐位确定性，2026-08-22 R3 实测拍板）：
    A. 每轨单输入 ffmpeg 链 → stems/<track>.bus.wav（float32）：
       adelay（clip 窗平移）→ volume(静态 gain_db) → gain_db/pan 自动化
       → pan（静态）→ bus 滤镜直传 → apad+atrim（BG-D5：补零/截断到工程
       时长）；lowpass_hz 自动化随后由 numpy 扫描原地施加。
    A'. 空间化级（v3.2 空间化轮）：轨 space 声明 → 深度模型（预延迟/电平/
       高频滚降/宽度，core.space.apply_track_space）原地施加于 bus；
       master.space_ir 声明 → wet 总线（送量求和 → 合成厅堂 IR OLA 卷积）。
       无 space 轨 = 完全旁路（现有工程渲染位级不变）。
    B. sidechain duck：numpy 包络跟随器（阈值/比率/attack/release）从 trigger
       bus 波形计算增益曲线，乘到 target bus 波形（确定性 DSP）。
    C. amix 求和（normalize=0；v3.2 起 dry+wet）→ mix_premaster.wav
       （float32，防削波）→（可选 master.pre_limit 母前峰值限幅，C' 段）→
       engines/master 母带（loudnorm 两遍 / matchering）。

为什么不用 [main][sc]sidechaincompress（vtx 产线验证过的用法）：
    ffmpeg 8.1.2 实测其双输入帧同步是位级非确定的——隔离复现（仅 2 输入 +
    sidechaincompress 的最小图）10 次渲染得 3 个不同 sha；完整总线下表现为
    duck 包络在 trigger 尾部 ±90ms 窗口内是否触发存在随机差异（幅度差达
    0.0285）。-threads/-filter_threads 1 均不能消除。音质上无碍（ vtx 场景
    可用），但违背本产线"构造即事实"的逐位复现要求（PLAN R5），故 duck
    改为等语义的确定性实现。

自动化实现（全部实测）：
    gain_db → volume='if(lt(t,..),lerp(..),..)':eval=frame 分段线性。
              linear=dB 直线（表达式内 pow(10,db/20)）；exp=线性增益几何插值。
    pan → channelsplit + 每声道独立 volume 表达式 + join
          （pan 滤镜不接受表达式，实测报 "Expected in channel name"）。
    lowpass_hz → numpy OLA 频域扫描（_apply_lowpass_sweep）。
          任务书原建议 asendcmd 驱动 lowpass.frequency 已实测否决：ffmpeg
          lowpass 每次收命令重置滤波器状态，50Hz 命令率产生宽带拉链噪声
          （2-20kHz 倍频程能量被抬 13~29dB，r3smoke 频谱实测）；OLA 扫描
          平滑且逐位确定。
"""

import math
import os
import subprocess

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from ..engines import run_engine
from .project import ProjectError
from .space import (apply_track_space, depth_default_send_db, make_hall_ir,
                    ola_convolve, write_f32_wav as _write_f32_wav)

ENV_RATE_HZ = 1000.0              # duck 包络分析率（检测/平滑在此粒度，逐样本插值）


def mix_project(proj, plan, out_dir, runtime=None):
    """执行三段式混音 + 母带。返回路径 dict 与总线摘要。"""
    tracks = plan["tracks"]
    sr, ch = proj["meta"]["sr"], proj["meta"]["channels"]
    if ch != 2 and (any(abs(t["pan"]) > 1e-9 for t in tracks)
                    or any(a["param"] == "pan" for a in proj["automation"])):
        raise ProjectError("meta.channels=1 工程不支持 pan/pan 自动化（需立体声）")
    dur = plan["duration_s"]

    # ---- A. 轨道链（单输入图，逐位确定；v3.4 资源感知并行）+ lowpass_hz 自动化
    #      每条轨链 = 独立 ffmpeg 子进程（GIL 不挡，真并行）；结果按轨序收集，
    #      输出路径/位级与顺序版一致。GPU 引擎不在此层（A 段恒为 ffmpeg CPU 图）。
    from . import resources as _res
    stems_dir = os.path.join(out_dir, "stems")
    chains = _res.run_pool(
        [(i, t) for i, t in enumerate(tracks)],
        lambda it: _render_track_chain(proj, plan, it[1], it[0], stems_dir),
        per_task_gb=_res.PER_TASK_GB["track_chain"])
    bus = {t["name"]: p for t, p in zip(tracks, chains)}
    # lowpass_hz 自动化原地施加（A 段后）：per-track 独立 numpy——进池受 GIL
    # 限制收益小，保持顺序（后续轨间无依赖，仅资源权衡）。
    for t in tracks:
        for a in proj["automation"]:
            if a["target_track"] == t["name"] and a["param"] == "lowpass_hz":
                _apply_lowpass_sweep(bus[t["name"]], a, plan["spb"], sr, ch)

    # ---- A'. 空间化级（v3.2）：轨深度模型 + wet 厅堂总线
    #     （无 space 轨 = 完全旁路，位级不变；见 _apply_spatial）
    wet_path, space_note = _apply_spatial(proj, plan, bus, sr, ch, out_dir)

    # ---- B. sidechain duck（确定性包络跟随器）
    sc = proj["sidechain"]
    ducked = []
    if sc:
        trig_path = bus[sc["trigger_track"]]
        for tgt in sc["targets"]:
            bus[tgt] = _apply_duck(trig_path, bus[tgt], sc, sr,
                                   os.path.join(out_dir, "stems"), ch=ch)
            ducked.append(tgt)

    # ---- C. 求和 → premix →（可选母前限幅）→ 母带
    #     wet（v3.2 空间化）长于 dry bus 一个 IR 尾（amix duration=longest
    #     自动补零），reverb 尾自然收尾进母带。
    premix = os.path.join(out_dir, "mix_premaster.wav")
    _sum_buses([bus[t["name"]] for t in tracks]
               + ([wet_path] if wet_path else []), premix, sr, ch)

    mst = proj["master"]
    master_src = premix
    if mst.get("pre_limit"):
        master_src = _apply_premaster_limit(premix, mst["pre_limit"], sr, ch,
                                            out_dir)
    master_wav = os.path.join(out_dir, "mix_master.wav")
    spec = {"engine": "master", "spec_version": "1", "sr": sr, "channels": ch,
            "payload": {"input": master_src, "mode": mst["chain"],
                        "target_lufs": mst["target_lufs"],
                        "true_peak": mst["true_peak_db"], "out": master_wav}}
    if mst["chain"] == "matchering":
        spec["payload"]["ref"] = mst["ref"]
    mres = run_engine(spec, runtime=runtime)

    return {"premaster": premix,
            "premaster_limited": (master_src if master_src != premix else None),
            "master": master_wav,
            "lufs": mres.get("lufs"), "true_peak_db": mres.get("true_peak_db"),
            "normalization_type": (mres.get("detail") or {}).get("normalization_type"),
            "summary": {"n_tracks": len(tracks),
                        "automation": [f"{a['target_track']}.{a['param']}"
                                       for a in proj["automation"]],
                        "sidechain": (f"{sc['trigger_track']}→{sc['targets']}"
                                      f"（numpy duck：thr={sc['threshold_db']}dB "
                                      f"ratio={sc['ratio']:g} att={sc['attack_ms']:g}ms "
                                      f"rel={sc['release_ms']:g}ms）" if sc else None),
                        "space": space_note,
                        "master_chain": mst["chain"],
                        "pre_limit": (f"premix 峰值限幅 ceiling={mst['pre_limit']['ceiling_db']:g}dB"
                                      f" att={mst['pre_limit'].get('attack_ms', 5):g}ms"
                                      f" rel={mst['pre_limit'].get('release_ms', 150):g}ms"
                                      if mst.get("pre_limit") else None)}}


# ------------------------------------------------------------------ A 轨道链

def _render_track_chain(proj, plan, t, idx, stems_dir):
    """单轨 stem → bus wav（float32，工程全长）。单输入 ffmpeg 图。"""
    autos = {a["param"]: a for a in proj["automation"]
             if a["target_track"] == t["name"]}
    spb, sr, ch = plan["spb"], proj["meta"]["sr"], proj["meta"]["channels"]
    dur = plan["duration_s"]
    end_sample = int(round(dur * sr))

    src = os.path.join(stems_dir, f"{t['name']}.{t['engine']}.wav")
    fl = []
    if t["start_s"] > 0:
        fl.append(f"adelay=delays={int(round(t['start_s'] * 1000))}:all=1")
    if abs(t["gain_db"]) > 1e-9:
        fl.append(f"volume={10 ** (t['gain_db'] / 20):.6f}")
    if "gain_db" in autos:
        fl.append(_volume_filter(autos["gain_db"], spb))
    pan_auto = autos.get("pan")
    if abs(t["pan"]) > 1e-9 and not pan_auto:
        lg = min(1.5, max(0.0, 1 - t["pan"] * 0.5))
        rg = min(1.5, max(0.0, 1 + t["pan"] * 0.5))
        fl.append(f"pan=stereo|c0={lg:.4f}*c0|c1={rg:.4f}*c1")
    # lowpass_hz 自动化不在链内做（asendcmd 拉链噪声，见模块 docstring），
    # 由 mix_project 在链渲染后调 _apply_lowpass_sweep 原地施加。
    fl += list(t["bus"])
    fl += [f"apad=whole_dur={dur:.6f}",
           f"atrim=end_sample={end_sample},asetpts=PTS-STARTPTS"]

    out = os.path.join(stems_dir, f"{t['name']}.bus.wav")
    graph = ([f"[0:a]{','.join(fl)},channelsplit=channel_layout=stereo[bL][bR]",
              f"[bL]volume=volume='{_pan_expr(pan_auto, spb, 'L')}':eval=frame[bLe]",
              f"[bR]volume=volume='{_pan_expr(pan_auto, spb, 'R')}':eval=frame[bRe]",
              f"[bLe][bRe]join=inputs=2:channel_layout=stereo[out]"]
             if pan_auto else
             [f"[0:a]{','.join(fl)}[out]"])
    cmd = ["ffmpeg", "-y", "-hide_banner", "-nostats", "-loglevel", "error",
           "-i", src, "-filter_complex", ";".join(graph), "-map", "[out]",
           "-c:a", "pcm_f32le", "-ar", str(sr), "-ac", str(ch), out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise ProjectError(f"轨 {t['name']} 链渲染失败：{(r.stderr or '')[-1500:]}\n"
                           f"graph: {graph}")
    return out


def _sum_buses(paths, premix, sr, ch):
    """bus wavs → amix 求和 → premix（normalize=0，float32）。"""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-nostats", "-loglevel", "error"]
    for p in paths:
        cmd += ["-i", p]
    if len(paths) == 1:
        graph = "[0:a]anull[premix]"
    else:
        graph = ("".join(f"[{i}:a]" for i in range(len(paths)))
                 + f"amix=inputs={len(paths)}:duration=longest:normalize=0[premix]")
    cmd += ["-filter_complex", graph, "-map", "[premix]",
            "-c:a", "pcm_f32le", "-ar", str(sr), "-ac", str(ch), premix]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise ProjectError(f"amix 求和失败：{(r.stderr or '')[-1500:]}")


# ------------------------------------------------------------------ A' 空间化级
#（v3.2 空间化轮：轨道深度模型 + wet 厅堂总线；DSP 实现在 core/space.py，
#  编排在 _apply_spatial/_spatialize_track，挂载点=mix_project 的 A 与 B 之间。）

def _apply_spatial(proj, plan, bus, sr, ch, out_dir):
    """空间化级编排：返回 (wet wav 路径或 None, 摘要或 None)。

    无 space 轨 → (None, None) 完全旁路（现有工程渲染位级不变——回归锚点
    保护）。有 space 轨 → 逐轨深度模型原地施加于 bus wav；master.space_ir
    声明时另建 wet 总线：全部轨送量求和 → make_hall_ir 共享厅堂 IR →
    ola_convolve → mix_wet.wav（进 C 段求和；含 IR 尾长，reverb 自然收尾）。
    space 声明而 space_ir 缺省 → 仅 dry 深度处理，无 wet（零观点：不强制）。

    **wet 尾 × QC silent 门边界（v3.2 审收 M-3 声明）**：premix 含 IR 尾长
    （rt60+30ms）。若工程结尾安静且编制整体 depth 偏低（wet 末电平 W0 低于
    约 -44dBFS@rt60=2s 量级），混音尾部可能出现 >silent_window_max_s 的净
    无声窗 → qc silent 门显式 fail（报警非静默错误）。应用方（seating 表）
    需保证结尾段 wet 送量足够（W0 余量），或接受门限上调并注记。
    """
    spaced = [t for t in plan["tracks"] if t.get("space")]
    if not spaced:
        return None, None
    sir = proj["master"].get("space_ir")
    sends = []
    for t in spaced:
        s = _spatialize_track(bus[t["name"]], t["space"], sr, ch,
                              want_send=bool(sir))
        if s:
            sends.append(s)
    if not sir:
        note = (f"{len(spaced)} 轨深度化（dry-only，无 master.space_ir 声明"
                f"→不建 wet 总线）")
        return None, note
    ir = make_hall_ir(sir["rt60_s"], seed=sir["seed"], sr=sr, channels=ch)
    send_sum = os.path.join(out_dir, "mix_sends.wav")
    _sum_buses(sends, send_sum, sr, ch)
    wet = ola_convolve(_read_audio_f32(send_sum, sr, ch), ir)
    wet_path = os.path.join(out_dir, "mix_wet.wav")
    _write_f32_wav(wet_path, np.ascontiguousarray(wet, dtype=np.float32), sr)
    depths = [t["space"]["depth"] for t in spaced]
    note = (f"{len(spaced)} 轨深度化（depth {min(depths):g}..{max(depths):g}）"
            f"+ wet hall rt60={sir['rt60_s']:g}s seed={sir['seed']}"
            f"（IR {ir.shape[0]} 样本，尾长 {ir.shape[0] / sr:.3f}s）")
    return wet_path, note


def _spatialize_track(bus_path, sp, sr, ch, want_send):
    """单轨空间化：读 bus → apply_track_space（深度模型）→ 原地写回 bus
    （与 _apply_lowpass_sweep 同手法）→ 按需写送量 stem（<track>.send.wav）。

    送量电平 = depth 处理后信号 × 10^(send_db/20)（send_db 缺省按 depth 推导，
    core.space.depth_default_send_db = -12+depth×9——越远越湿）。
    """
    x = _read_audio_f32(bus_path, sr, ch)
    y = apply_track_space(x, sr, sp["depth"], sp.get("width", 0.3))
    _write_f32_wav(bus_path, np.ascontiguousarray(y, dtype=np.float32), sr)
    if not want_send:
        return None
    send_db = sp.get("send_db", depth_default_send_db(sp["depth"]))
    send = y * 10.0 ** (send_db / 20.0)
    dst = bus_path.replace(".bus.wav", ".send.wav")
    return _write_f32_wav(dst, np.ascontiguousarray(send, dtype=np.float32), sr)


# ------------------------------------------------------------------ B duck

def _apply_duck(trigger_path, target_path, sc, sr, stems_dir, ch=2):
    """确定性 sidechain duck。

    语义（feed-forward 压缩器增益计算，与 sidechaincompress 同参数集的近似）：
      检测 = trigger 单声道混缩的 1kHz 分帧 RMS → dB；
      增益目标 = level>threshold 时 (threshold-level)*(1-1/ratio)（负 dB）；
      平滑 = dB 域一阶指数：下行走 attack_ms、上行走 release_ms（1kHz 粒度）；
      应用 = 增益曲线线性插值到逐样本，乘到 target 波形（float32 落盘）。
    """
    trig = _read_audio_f32(trigger_path, sr, ch)
    tgt = _read_audio_f32(target_path, sr, ch)
    n = tgt.shape[0]
    gain = _duck_gain_curve(trig, sr, sc["threshold_db"], sc["ratio"],
                            sc["attack_ms"], sc["release_ms"], n)
    out = np.clip(tgt * gain[:, None], -1.0, 1.0)
    dst = target_path.replace(".bus.wav", ".duck.wav")
    _write_f32_wav(dst, out.astype(np.float32), sr)
    return dst


def _duck_gain_curve(trigger, sr, threshold_db, ratio, attack_ms, release_ms, n_out):
    mono = trigger.mean(axis=1) if trigger.ndim > 1 else trigger
    blk = max(1, int(round(sr / ENV_RATE_HZ)))
    rate = sr / blk
    m = len(mono) - len(mono) % blk
    if m <= 0:
        return np.ones(n_out)
    env = np.sqrt(np.mean(mono[:m].reshape(-1, blk) ** 2, axis=1))
    level_db = 20.0 * np.log10(np.maximum(env, 1e-9))
    target = np.where(level_db > threshold_db,
                      (threshold_db - level_db) * (1.0 - 1.0 / ratio), 0.0)

    dt = 1.0 / rate
    k_att = 1.0 - math.exp(-dt / max(attack_ms / 1000.0, 1e-6))
    k_rel = 1.0 - math.exp(-dt / max(release_ms / 1000.0, 1e-6))
    gr = 0.0
    sm = np.empty_like(target)
    for i, tg in enumerate(target):                 # 1kHz 粒度：180s → 18 万步，<0.2s
        k = k_att if tg < gr else k_rel
        gr += (tg - gr) * k
        sm[i] = gr
    gain_env = 10.0 ** (sm / 20.0)
    t_env = np.arange(len(sm)) / rate
    t_out = np.arange(n_out) / sr
    return np.interp(t_out, t_env, gain_env)        # 端点外 np.interp 恒持边界值


# 注：_write_f32_wav（float32 wav 写出）实现体上移 core/space.write_f32_wav，
# 本模块顶部经 `write_f32_wav as _write_f32_wav` 别名导入（space/mixer 共用，
# 防两份手写头漂移；字节格式不变，位级锚点不受影响）。


# ------------------------------------------------------------ B' lowpass 扫描

def _automation_value_at(points_s, t, curve):
    """自动化点列（秒域）在 t 处的插值：linear=原值直线，exp=对数值直线。"""
    if t <= points_s[0][0]:
        return points_s[0][1]
    if t >= points_s[-1][0]:
        return points_s[-1][1]
    for k in range(len(points_s) - 1):
        t0, v0 = points_s[k]
        t1, v1 = points_s[k + 1]
        if t0 <= t <= t1:
            u = (t - t0) / (t1 - t0)
            if curve == "exp":
                return math.exp(math.log(v0) + u * (math.log(v1) - math.log(v0)))
            return v0 + u * (v1 - v0)
    return points_s[-1][1]


def _read_audio_f32(path, sr, ch):
    """任意音频 → float32 (n,ch)，不经 16bit 量化（与 bus/duck float 链路配套）。"""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path,
                          "-ar", str(sr), "-ac", str(ch), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    a = np.frombuffer(raw, dtype=np.float32).reshape(-1, ch)
    return a.astype(np.float64)


def _apply_lowpass_sweep(bus_path, auto, spb, sr, ch):
    """lowpass_hz 自动化：确定性 numpy 频域扫描（原地改写 bus wav）。

    为什么不用 asendcmd 驱动 lowpass.frequency（任务书原建议）：
        实测 ffmpeg lowpass 每次收命令都重置滤波器状态，50Hz 命令率产生
        宽带拉链噪声——对比无自动化版本，2-20kHz 各倍频程能量被抬高
        13~29dB（out/r3smoke 实测频谱）。降命令率只把蜂音变咔哒声。
        故改为块式重叠相加（OLA）：1024 点周期 Hann 窗、50% 跳步、
        每块按块中心时刻的截止频率施加 Butterworth 2 阶幅频响应
        |H| = 1/sqrt(1+(f/fc)^4)（零相位），OLA 重建平滑无阶梯。
    """
    audio = _read_audio_f32(bus_path, sr, ch)
    n = audio.shape[0]
    pts_s = [(b * spb, v) for b, v in auto["points"]]
    # 块长 4096（~93ms@44.1k）：零相位块滤波的循环卷积残留随块长急降
    # （纯音实测 1-2kHz 伪影：N=1024→-79.5dB，N=4096→-117.3dB）；
    # 扫描时间分辨率 = hop（~46ms），对滤波器自动化足够。
    N, hop = 4096, 2048
    # 分析窗=周期 Hann、合成不再加窗：Σ hann(k)+hann(k+N/2) ≡ 1（COLA），
    # H≡1 时 OLA 严格直通（分析+合成双重加窗会破坏 COLA，实测重建误差 0.5）。
    w = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N) / N)
    freqs = np.fft.rfftfreq(N, 1 / sr)
    padded = np.vstack([np.zeros((N, ch)), audio, np.zeros((N, ch))])
    out = np.zeros_like(padded)
    for start in range(0, padded.shape[0] - N + 1, hop):
        t_center = (start - N + N / 2) / sr
        fc = max(10.0, _automation_value_at(pts_s, t_center, auto["curve"]))
        H = 1.0 / np.sqrt(1.0 + (freqs / fc) ** 4)
        block = padded[start:start + N] * w[:, None]
        out[start:start + N] += np.fft.irfft(
            np.fft.rfft(block, axis=0) * H[:, None], n=N, axis=0)
    result = out[N:N + n]
    _write_f32_wav(bus_path, np.clip(result, -1.0, 1.0).astype(np.float32), sr)

def _num(v):
    return f"{v:.6g}"


# ------------------------------------------------------- C' 母前峰值限幅
def _apply_premaster_limit(premix_path, pl, sr, ch, out_dir):
    """master.pre_limit：确定性母前峰值限幅（PLAN_v3 §4.3 crest>13dB 预案）。

    为什么在 premix 上而不是逐轨 bus：crest 是全编制叠加的结构属性
    （R4-W1 实测：压表情/调轨增益 crest 几乎不动——各轨等比例缩放），
    限幅点必须在求和之后。用途：管弦等高动态素材 premaster TP-I 超
    loudnorm linear 约束（target_TP - target_L ≈ 12.8dB）时，先声明式
    轻限幅削峰，再进两遍 loudnorm → linear 归一化成立（Dynamic 回退
    消除）。

    算法（lookahead 峰值限幅，同 _apply_duck 的确定性手法族）：
      env[i] = |x| 在 [i, i+look) 窗内最大（滑窗最大，view 无拷贝）；
      目标增益 td = min(1, ceiling/env)，dB 域；
      释放 = dB 域一阶指数恢复到 0dB（1kHz 块粒度，块内取最保守值），
      np.interp 插回逐样本——同 _duck_gain_curve 的确定性结构；
      对齐 = 信号延迟 look-1 样本：env[i] 覆盖 [i, i+look) 作用于样本 i+look-1
      → 样本 j 的治理窗 = [j-look+1, j]，**含 j 自身**（R4 审查 B·M-2 修正：
      原延迟 look 使窗为 [j-look, j-1]，峰值样本不在自己窗内，最坏 1kHz 块
      相位（峰落块首）+ 深限幅时单样本欠衰减 ~7%@12dB，靠末段硬钳掩盖）。
      attack 平滑 = 1kHz 块 min + 线性 ramp（块锚点在块首）；末尾硬钳 ceiling
      兜孤立短峰（短于一块）的插值欠衰减。
    全整数窗 + 纯 numpy → 逐位确定（同输入两次调用 sha 相等，测试锁定）。
    """
    audio = _read_audio_f32(premix_path, sr, ch)
    n = audio.shape[0]
    ceiling = 10.0 ** (float(pl["ceiling_db"]) / 20.0)
    look = max(1, int(round(float(pl.get("attack_ms", 5.0)) * sr / 1000.0)))
    rel_ms = float(pl.get("release_ms", 150.0))

    peak = np.abs(audio).max(axis=1)
    pad = np.concatenate([peak, np.zeros(look)])      # 尾窗越界补 0（max 无害）
    win_max = sliding_window_view(pad, look)[:n].max(axis=1)
    td_db = np.where(win_max > ceiling, 20.0 * np.log10(ceiling / np.maximum(win_max, 1e-12)), 0.0)

    # 释放平滑（1kHz 块粒度，块内取最低增益 = 最保守）
    blk = max(1, int(round(sr / ENV_RATE_HZ)))
    m = n - n % blk
    if m <= 0:
        gain = np.ones(n)
    else:
        td_blk = td_db[:m].reshape(-1, blk).min(axis=1)
        rate = sr / blk
        dt = 1.0 / rate
        k = 1.0 - math.exp(-dt / max(rel_ms / 1000.0, 1e-6))
        gr = 0.0
        sm = np.empty_like(td_blk)
        for i, td in enumerate(td_blk):              # 1kHz 粒度：318s → 32 万步，<0.5s
            gr += (0.0 - gr) * k                     # 向 0dB 指数恢复
            if td < gr:
                gr = td                              # 但不得宽于本块需求
            sm[i] = gr
        gain_env = 10.0 ** (sm / 20.0)
        t_env = np.arange(len(sm)) / rate
        gain = np.interp(np.arange(n) / sr, t_env, gain_env)

    # 对齐：env[i] 覆盖 [i, i+look) → 作用于样本 i+look-1（窗含样本自身）
    d = look - 1
    gain_aligned = np.concatenate([np.ones(min(d, n)), gain[:max(0, n - d)]])
    out = audio * gain_aligned[:, None]
    out = np.clip(out, -ceiling, ceiling)            # 孤立短峰插值欠衰减兜底
    dst = os.path.join(os.path.dirname(os.path.abspath(premix_path)),
                       "mix_premaster_limited.wav")
    _write_f32_wav(dst, np.ascontiguousarray(out, dtype=np.float32), sr)
    return dst


def _ramp_core(points_s):
    """分段线性表达式骨架（值域=points 的 value 原值）：if 嵌套 + lerp + clip。"""
    pts = [(float(t), float(v)) for t, v in points_s]
    expr = _num(pts[-1][1])                       # 末点后恒持末值
    for k in range(len(pts) - 2, -1, -1):
        t0, v0 = pts[k]
        t1, v1 = pts[k + 1]
        seg = (f"lerp({_num(v0)},{_num(v1)},"
               f"clip((t-{t0:.6f})/({t1 - t0:.6f}),0,1))")
        expr = f"if(lt(t,{t1:.6f}),{seg},{expr})"
    return expr                                   # t<首点：clip 顶到 u=0 → 首值


def _points_to_seconds(points_beat, spb):
    return [(b * spb, v) for b, v in points_beat]


def _volume_filter(auto, spb):
    """gain_db 自动化 → volume 滤镜串。"""
    pts_s = _points_to_seconds(auto["points"], spb)
    if auto["curve"] == "exp":
        lin = [(t, 10 ** (v / 20)) for t, v in pts_s]      # 线性增益几何插值
        expr = _ramp_core(lin)
    else:
        expr = f"pow(10,({_ramp_core(pts_s)})/20)"         # dB 直线，表达式内换算
    return f"volume=volume='{expr}':eval=frame"


def _pan_expr(auto, spb, side):
    """pan 自动化单声道增益表达式（lg=1-pan*0.5 / rg=1+pan*0.5，vtx 同款映射）。"""
    pts_s = _points_to_seconds(auto["points"], spb)
    sign = -1.0 if side == "L" else 1.0
    return _ramp_core([(t, 1.0 + sign * 0.5 * v) for t, v in pts_s])
