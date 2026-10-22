"""vtx audio --report 音频一等报告（方案五阶段3）：在 audio 阶段产物上自动出报告。

不依赖 mux/成片：判级对象 = out/audio.wav（整片 LUFS/TP、静音空洞）+ 由 timeline 与
cache/audio 源 wav 现场渲染的轨级 stem（人声活动响度 vs duck 床乐响度差、duck 咬合/
回升时间）。所有判级阈值集中本模块头，出处 = v4.1–v4.3 实测事故值（voice-ime），
可被 timeline `_meta.audio_report` 覆盖（同名键覆盖同值；TP 只能收紧权威阈）。

判级阈值表（出处）：
  LUFS  |I-(-14)|≤1.5 PASS / ≤2.5 WARN / 其余 FAIL
        —— qc.QC_LOUDNESS_TOL=1.5（成片 qc 同一口径）；2.5 档为本报告新增中间档
  TP    >-1 dBTP FAIL / ≤-1 PASS（v2 §1.3，2026-09-13：-1.0 dBFS 为唯一权威阈，与
        gates G3 / 内核 qc「解码域真峰」同一口径——limiter 已并入 vtx audio
        （ARCHITECTURE §1.2），audio.wav 域 TP 应稳定 ≤-1，旧 -1~+3「设计内」INFO
        档（post_loudness 可选时代 v4.2 实测 TP +0.7~+0.8 的口径）已废除；wav 域
        达标仍不代表成片达标——aac 解码过冲实测 +8.5dB，成片终检在内核 qc）
  人声-床乐响度差（人声活动窗内 S 均值差） <19 FAIL / 19-21 WARN / 21-29 PASS
        / >29 WARN（床乐过弱）
        —— 口径：stem 级 ebur128 短时响度 S（3s 积分窗）均值差。v4.2 定稿实测锚
        22.0dB（本模块实跑，见提交报告）、v4.1 事故态 = 锚-4.6dB ≈ 17.4 必须判
        FAIL。方案书 nominal 12-17 PASS 基于人读分析（≈14dB，口径未存档），与本
        S 口径不可比（3s 积分把 duck 深谷拉高床乐均值）——阈值按实测锚重校，
        事故判级语义（v4.1→FAIL / v4.2→PASS）不变；可用 _meta.audio_report 覆盖
  duck 咬合 attack <0.1s PASS（v4.2 attack=20ms 参数；v4.2 现状实测 0.05s）
        / 0.1-0.3 WARN；>0.3 FAIL
  duck 回升 release ≤2.5s PASS（v4.2 现状实测锚 2.41s = release 1500ms 参数 +
        ebur128 M 400ms 积分窗 + 回归 -3dB 判定口径的拖尾；方案书「1.4s 量级」为
        参数口径，本测量口径含拖尾故按实测锚校）/ 2.5-4.0 WARN；>4.0 或未回升 FAIL
  静音空洞 片头 >1.5s 即 FAIL（v4.3 事故：片头 3.6s 无音乐）；片中 >3s FAIL /
        >1.5s WARN；片尾（最后 0.5s 内结束）>3s 只 WARN——BGM 淡出属正常编排

测量口径（voice-ime v4.2 手工分析的固化）：
  人声活动窗：参考方法为 Silero VAD 切人声段；本仓库仅依赖 ffmpeg（vtx 约定），
  回退口径 = 对人声 stem silencedetect(noise=-35dB,d=0.3) 取补集为活动窗。与 Silero
  相比在噪声底高的素材上会略放大活动窗（把低能量噪声判为有声），voice-ime 干声
  经降噪后差异可忽略；素材噪声底 >-35dB 时建议人工核对。
  响度差：人声 stem ebur128 短时响度 S 在活动窗内均值 vs duck 后床乐 stem 同窗 S
  均值之差（stem 均为响度归一前的原始混音，差值不受整片静态增益影响）。
  duck 包络：bed_raw 与 bed_duck 双 stem 的 ebur128 瞬时响度 M 之差（10Hz 采样）
  = 床乐增益包络；attack/release 取各活动窗测量值的中位。
  静音空洞：对整片 audio.wav silencedetect(noise=-45dB,d=1.5)。

CLI 语义（最小惊讶裁定）：`audio --report` = 照常跑 audio 混音阶段（含依赖门/receipt）
后附加出报告；`audio --report-only` = 纯报告模式，只读已有 out/audio.wav + cache/audio
出报告，不重混音、不做 receipt（audio.wav 缺失报错）——「再跑一遍混音」对迭代中的
用户是惊喜，显式分开两个开关。报告写 out/audio_report.json（--json PATH 可改），
checks 结构与 qc_report 同构（name/pass/detail），`vtx diff` 可直接对比两份报告。
退出码：有 FAIL=1；WARN=0（报告为信息性检查，WARN 不阻断——与 audit 同裁定；
FAIL 表示事故级问题必须处理）。
"""
import json, math, re, statistics, time
from pathlib import Path

from ..hygiene import audio_key
from ..runtime import fstxn
from .audio_filters import event_chain
from ..pluginlib import hooks as _plugin_hooks

# ---- 阈值常量（出处见模块头判级阈值表）----
LUFS_TARGET = -14.0        # stages.TARGET_LUFS / qc.TARGET_LUFS 同一权威值
LUFS_TOL = 1.5             # qc.QC_LOUDNESS_TOL（PASS 容差）
LUFS_WARN_TOL = 2.5        # WARN 容差（本报告新增中间档）
TP_WARN_DBTP = -1.0        # v2 §1.3 权威阈：-1.0 dBFS（gates G3 / 内核 qc 同一值）
TP_FAIL_DBTP = -1.0        # 同上（> -1 即 FAIL）；双常量保留命名以兼容 _meta 覆盖键
DIFF_FAIL_BELOW = 19.0     # v4.1 事故态 = v4.2 锚(22.0)-4.6 ≈ 17.4，19 留 1.6dB 余量
DIFF_WARN_BELOW = 21.0     # 实测锚 ±1dB 抖动带
DIFF_PASS_MAX = 29.0       # 锚 +7；超过 = 床乐过弱
DUCK_DEPTH_MIN = 3.0       # duck 深度 <3dB = 避让未生效
DUCK_ATTACK_PASS = 0.10    # v4.2 attack=20ms
DUCK_ATTACK_FAIL = 0.30
DUCK_RELEASE_PASS = 2.5    # v4.2 现状实测锚 2.41s（口径见模块头）
DUCK_RELEASE_FAIL = 4.0
HOLE_WARN = 1.5            # v4.3：片头 3.6s 无音乐
HOLE_FAIL = 3.0
VOICE_SILENCE_NOISE = "-35dB"   # 人声 stem 活动窗回退口径（见模块头）
VOICE_SILENCE_MIN_D = 0.3
HOLE_NOISE = "-45dB"       # 整片空洞检测噪声底
HOLE_MIN_D = 1.5
# _meta.audio_report 可覆盖的阈值键
OVERRIDABLE = ("lufs_tol", "lufs_warn_tol", "tp_warn", "tp_fail", "diff_fail",
               "diff_warn", "diff_pass_max", "duck_attack_pass", "duck_attack_fail",
               "duck_release_pass", "duck_release_fail", "hole_warn", "hole_fail")


# ---------------------------------------------------------------- 判级纯函数
def grade_lufs(i, tol=LUFS_TOL, warn_tol=LUFS_WARN_TOL, target=LUFS_TARGET):
    d = abs(i - target)
    if d <= tol: return "PASS", f"实测 {i:.1f} LUFS（目标 {target:g}±{tol}）"
    if d <= warn_tol: return "WARN", f"实测 {i:.1f} LUFS，偏差 {i-target:+.1f}（容差 {tol}~{warn_tol}）"
    return "FAIL", f"实测 {i:.1f} LUFS，偏差 {i-target:+.1f} 超 WARN 容差 {warn_tol}"


def _finite_number(value):
    """阈值/测量只接受有限数字，不把布尔值或字符串当作数值。"""
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _tp_threshold(warn, fail):
    """兼容双覆盖键，但有效阈永不放宽权威上限；无效配置没有有效阈。"""
    if not all(_finite_number(value) for value in (warn, fail)):
        return None
    return min(TP_FAIL_DBTP, warn, fail)


def grade_tp(tp, warn=TP_WARN_DBTP, fail=TP_FAIL_DBTP):
    """TP 判级（v2 §1.3 权威口径）：阈 -1.0 dBFS，> 阈即 FAIL——limiter 已并入
    vtx audio（§1.2），audio.wav 域 TP 应稳定 ≤-1，超阈 = 上游削波/限幅失效；
    ≤ 阈 PASS。warn/fail 双参数仅为 _meta.audio_report 覆盖键兼容保留，判定取
    两者与权威阈的最严值；无效覆盖/测量判 FAIL，不允许 NaN 绕过比较。"""
    thr = _tp_threshold(warn, fail)
    if thr is None:
        return "FAIL", "TP 阈值无效（tp_warn/tp_fail 必须为有限数值）"
    if not _finite_number(tp):
        return "FAIL", "TP 测量无效（必须为有限数值）"
    if tp > thr:
        return "FAIL", f"实测 TP {tp:.1f} dBTP > {thr:g}（vtx audio 已内置 alimiter，超阈请查上游削波）"
    return "PASS", f"实测 TP {tp:.1f} dBTP（≤{thr:g}）"


def grade_loudness_diff(diff, fail_b=DIFF_FAIL_BELOW, warn_b=DIFF_WARN_BELOW,
                        pass_max=DIFF_PASS_MAX):
    """diff = 人声活动响度 - 床乐(duck后)响度（dB，正值=床乐更安静）。
    阈值按 v4.2 实测锚 22.0dB 重校（出处见模块头），事故语义同方案书：
    v4.1 态（锚-4.6）必须 FAIL、v4.2 态必须 PASS。"""
    if diff < fail_b:
        return "FAIL", f"人声-床乐差 {diff:.1f}dB < {fail_b:g}（v4.1 事故形态：BGM 压不住人声）"
    if diff < warn_b:
        return "WARN", f"人声-床乐差 {diff:.1f}dB（{fail_b:g}~{warn_b:g} 边缘带）"
    if diff <= pass_max:
        return "PASS", f"人声-床乐差 {diff:.1f}dB（v4.2 定稿实测锚 22.0dB）"
    return "WARN", f"人声-床乐差 {diff:.1f}dB > {pass_max:g}（床乐过弱）"


def grade_duck(depth, attack, release):
    """depth=duck 深度(dB，负值)；attack/release=秒（release None=窗后未回升）。"""
    if depth is None or depth > -DUCK_DEPTH_MIN:
        return "FAIL", f"duck 深度 {depth if depth is None else round(depth, 1)}dB（|深度|<{DUCK_DEPTH_MIN:g}dB = 避让未生效）"
    a_ok = attack is not None and attack <= DUCK_ATTACK_PASS
    r_ok = release is not None and release <= DUCK_RELEASE_PASS
    if a_ok and r_ok:
        return "PASS", (f"咬合 {attack:.2f}s（<{DUCK_ATTACK_PASS:g}s，v4.2 attack=20ms 锚，实测 0.05s）"
                        f"，回升 {release:.2f}s（≤{DUCK_RELEASE_PASS:g}s，v4.2 实测锚）")
    if (attack is None or attack > DUCK_ATTACK_FAIL) or (release is None or release > DUCK_RELEASE_FAIL):
        return "FAIL", (f"咬合 {attack if attack is None else round(attack, 2)}s（>{DUCK_ATTACK_FAIL:g}s 判 FAIL）"
                        f"，回升 {release if release is None else round(release, 2)}s"
                        f"（>{DUCK_RELEASE_FAIL:g}s 或未回升判 FAIL）")
    return "WARN", (f"咬合 {attack:.2f}s / 回升 {release:.2f}s：显著劣化于 v4.2 锚"
                    f"（attack {DUCK_ATTACK_PASS:g}s / release {DUCK_RELEASE_PASS:g}s）")


def grade_holes(holes, total_dur=None, open_fail_min=HOLE_WARN, warn=HOLE_WARN, fail=HOLE_FAIL):
    """holes=[(s,e)]（秒）；片头（s<0.2）超 open_fail_min 即 FAIL（v4.3 事故）；
    片中 >fail FAIL；片尾（e 距片尾 <0.5s）>fail 只 WARN（BGM 淡出属正常编排）。"""
    if not holes: return "PASS", f"无 >{warn:g}s 近静音空洞"
    def is_tail(h): return total_dur is not None and total_dur - h[1] < 0.5
    fails = [h for h in holes
             if (h[1] - h[0] > fail and not is_tail(h))
             or (h[0] < 0.2 and h[1] - h[0] > open_fail_min)]
    fmt = "、".join(f"{s:.1f}-{e:.1f}s({e-s:.1f}s)" for s, e in holes[:8])
    if fails:
        return "FAIL", f"{len(holes)} 处空洞（FAIL {len(fails)} 处）：{fmt}"
    return "WARN", f"{len(holes)} 处空洞：{fmt}"


def merge_thresholds(tl):
    """timeline `_meta.audio_report` 覆盖阈值（白名单键，写明出处注释随 _meta 走）。"""
    ov = {}
    try:
        meta = (tl or {}).get("_meta", {}).get("audio_report", {})
        ov = {k: meta[k] for k in OVERRIDABLE if k in meta}
    except AttributeError:
        pass
    return ov


# ---------------------------------------------------------------- ffmpeg 测量
def ebur128_summary(c, wav):
    """→ (I LUFS | None, TP dBFS | None)；失败/无效值不可作通过依据。"""
    r = c.run(["ffmpeg", "-nostats", "-i", str(wav), "-af", "ebur128=peak=true:framelog=quiet",
               "-f", "null", "-"])
    if r.returncode != 0:
        return None, None

    def value(pattern):
        match = re.search(pattern, r.stderr)
        if match and re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?",
                                  match.group(1)):
            try:
                measured = float(match.group(1))
            except ValueError:
                return None
            return measured if _finite_number(measured) else None
        return None

    return (value(r"I:\s*(\S+)[ \t]+LUFS(?!\S)"),
            value(r"True peak:\s*Peak:\s*(\S+)[ \t]+dBFS(?!\S)"))


def ebur128_series(c, wav, ss=None, t=None):
    """→ [(t, M, S)]：ebur128 逐 100ms 帧行（framelog=info + -v info）。"""
    cmd = ["ffmpeg", "-nostats", "-v", "info"]
    if ss is not None: cmd += ["-ss", f"{ss:.3f}"]
    if t is not None: cmd += ["-t", f"{t:.3f}"]
    cmd += ["-i", str(wav), "-af", "ebur128=framelog=info", "-f", "null", "-"]
    r = c.run(cmd)
    out = []
    for m in re.finditer(r"t:\s*([\d.]+)\s+TARGET:.*?M:\s*(-?[\d.]+)\s+S:\s*(-?[\d.]+)", r.stderr):
        out.append((float(m.group(1)), float(m.group(2)), float(m.group(3))))
    return out


def silences(c, wav, noise, min_d):
    """→ [(s,e)]：silencedetect 静音段（秒）。"""
    r = c.run(["ffmpeg", "-nostats", "-v", "info", "-i", str(wav),
               "-af", f"silencedetect=noise={noise}:d={min_d}", "-f", "null", "-"])
    st = [float(m) for m in re.findall(r"silence_start:\s*(-?[\d.]+)", r.stderr)]
    en = [float(m) for m in re.findall(r"silence_end:\s*([\d.]+)", r.stderr)]
    return [(max(0.0, s), e) for s, e in zip(st, en)]


def _activity_windows(c, voice_stem):
    """人声活动窗：silencedetect 静音的补集（回退口径见模块头）。窗口 <0.4s 丢弃。"""
    sil = silences(c, voice_stem, VOICE_SILENCE_NOISE, VOICE_SILENCE_MIN_D)
    r = c.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "csv=p=0", str(voice_stem)])
    total = float(r.stdout.strip() or 0)
    wins, cur = [], 0.0
    for s, e in sil:
        if s > cur: wins.append((cur, s))
        cur = max(cur, e)
    if cur < total: wins.append((cur, total))
    return [(s, e) for s, e in wins if e - s >= 0.4], total


def _mean_s_in(series, windows):
    """ebur128 series 的 S 值在活动窗内均值（忽略 -120 无信号帧）。"""
    vals = [s for t, m, s in series if s > -100 and any(a <= t < b for a, b in windows)]
    return statistics.fmean(vals) if vals else None


# ---------------------------------------------------------------- stem 渲染
def _pad(c):
    return f"apad=whole_dur={c.dur*c.spf:.3f},atrim=end_sample={c.dur*c.spf_samples},asetpts=PTS-STARTPTS"


def _track_group(c, tracks):
    """一组音轨 → (cmd, chains, mix_chain, k, tk_lbls)。轨间 amix、轨内事件 amix，
    全长 apad 对齐（与整片混音同一时间网格，双 stem 的 M 差才逐帧可比）。
    tk_lbls[i] = tracks[i] 的子混标签（无事件的轨为 None）。"""
    tk_lbls = []
    cmd, chains, k = ["ffmpeg", "-v", "error", "-y"], [], 0
    for tr in tracks:
        ev_lbls = []
        for e in tr["events"]:
            chains.append(event_chain(c, k, e, require_volume=False)); ev_lbls.append(f"[e{k}]"); k += 1
        if not ev_lbls:
            tk_lbls.append(None); continue
        if len(ev_lbls) == 1:
            chains.append(f"{ev_lbls[0]}{_pad(c)}[tg{k}]")
        else:
            chains.append(f"{''.join(ev_lbls)}amix=inputs={len(ev_lbls)}:duration=longest:normalize=0,"
                          f"{_pad(c)}[tg{k}]")
        tk_lbls.append(f"[tg{k}]")
    lbls = [x for x in tk_lbls if x]
    if not lbls: return cmd, chains, None, k, tk_lbls
    # amix inputs=1 非法（ffmpeg 要求 ≥2），单轨直连
    mix = (lbls[0] if len(lbls) == 1 else
           f"{''.join(lbls)}amix=inputs={len(lbls)}:duration=longest:normalize=0,") + \
          "aresample=48000[gmix]"
    return cmd, chains, mix, k, tk_lbls


def _collect_inputs(c, tracks, cmd):
    """把轨事件的源 wav 逐事件挂到 -i（与 event_chain 的 [k:a] 事件序号一一同序；
    loop 事件加 stream_loop。不去重——输入序号必须严格等于事件序号）。
    源解析走 hooks.resolve_source_wav（P2 审查 P1-1 修复）：denoise 等缝 A 插件
    源的裸键按设计永不产出，主链 _audio_graph 已走产物键，本处必须同解析——
    否则 denoise 时间线 audio --report 的轨级 stem 渲染读不存在的裸键直接 FAIL。"""
    from ..pluginlib import hooks as _hooks
    for tr in tracks:
        for e in tr["events"]:
            wav = _hooks.resolve_source_wav(c, e["src"]) or \
                (c.cache / "audio" / f"{audio_key(c, e['src'])}.wav")
            cmd += (["-stream_loop", "-1"] if e.get("loop") else []) + ["-i", str(wav)]


def render_stems(c, voice_tracks, bed_tracks, workdir):
    """渲染 3 个 stem（响度归一前口径）：人声 stem / 床乐 raw stem / 床乐 duck 后 stem。
    bed duck 用各轨 duck_params 逐轨 sidechaincompress（key = 人声 stem），与
    stages._audio_graph 同一机制。返回 (voice_wav, bed_raw_wav, bed_duck_wav)。"""
    fstxn.mkdir(workdir, root=c.root)
    vw, rw, dw = workdir / "stem_voice.wav", workdir / "stem_bed_raw.wav", workdir / "stem_bed_duck.wav"
    # 1) 人声 stem
    cmd, chains, mix, _k, _lbls = _track_group(c, voice_tracks)
    _collect_inputs(c, voice_tracks, cmd)
    c.run(cmd + ["-filter_complex", ";".join(chains + [mix.replace("[gmix]", "[vout]")]),
                 "-map", "[vout]", "-c:a", "pcm_s16le", "-ar", "48000", str(vw)])
    # 2+3) 床乐 raw / duck 双输出（duck key = 人声 stem 文件）。每个轨子混标签要被
    # raw 混音与 sidechaincompress 各消费一次 → 先 asplit=2 复制（标签只能消费一次）
    cmd, chains, mix, n, tk_lbls = _track_group(c, bed_tracks)
    _collect_inputs(c, bed_tracks, cmd)               # 先挂床乐源 wav（序号=事件序号）
    vk = n                                            # 人声 stem 的输入序号
    cmd += ["-i", str(vw)]
    j_sc, duck_lbls, raw_lbls = 0, [], []
    for j, tr in enumerate(bed_tracks):
        lbl = tk_lbls[j]
        if lbl is None:
            continue
        chains.append(f"{lbl}asplit=2[raw{j}][tk{j}]")
        raw_lbls.append(f"[raw{j}]")
        if not tr.get("duck"):
            duck_lbls.append(f"[tk{j}]")               # 非 duck 轨原样进 duck 终混
            continue
        dp = tr.get("duck_params", {})
        chains.append(f"[tk{j}][sc{j_sc}]sidechaincompress="
                      f"threshold={dp.get('threshold', 0.0126)}:ratio={dp.get('ratio', 4)}:"
                      f"attack={dp.get('attack_ms', 20)}:release={dp.get('release_ms', 1500)}:"
                      f"makeup={dp.get('makeup', 1)}[td{j}]")
        duck_lbls.append(f"[td{j}]")
        j_sc += 1
    def _mixto(lbls, out):
        """lbls → 终链：多轨 amix 后接 aresample；单轨直连（amix inputs=1 非法）。"""
        head = "".join(lbls) + f"amix=inputs={len(lbls)}:duration=longest:normalize=0," \
            if len(lbls) > 1 else lbls[0]
        return head + f"aresample=48000[{out}]"
    chains.append(_mixto(raw_lbls, "braw"))
    if duck_lbls:
        chains.append(f"[{vk}:a]asplit={j_sc}" + "".join(f"[sc{i}]" for i in range(j_sc)))
        chains.append(_mixto(duck_lbls, "bduck"))
    else:
        chains.append("[braw]anull[bduck]")   # 无 duck 轨：raw/duck 同一信号
    # 双输出各自 -map + 编码参数（-map 默认作用于全部输出文件，会双流塞进一个 wav）
    c.run(cmd + ["-filter_complex", ";".join(chains),
                 "-map", "[braw]", "-c:a", "pcm_s16le", "-ar", "48000", str(rw),
                 "-map", "[bduck]", "-c:a", "pcm_s16le", "-ar", "48000", str(dw)])
    return vw, rw, dw


# ---------------------------------------------------------------- 报告主流程
def run_report(c, json_path=None):
    """出音频一等报告；返回退出码（FAIL=1 / 其余 0）。写 JSON 报告 + 人读摘要。"""
    t0 = time.time()
    thr = merge_thresholds(c.tl)
    tp_threshold = _tp_threshold(thr.get("tp_warn", TP_WARN_DBTP),
                                 thr.get("tp_fail", TP_FAIL_DBTP))
    wav = c.out / "audio.wav"
    checks = []
    def check(name, level, detail, measured=None):
        checks.append({"name": name, "level": level, "pass": level in ("PASS", "INFO"),
                       "detail": detail, **({"measured": measured} if measured else {})})
        sym = {"PASS": "✓", "WARN": "⚠", "FAIL": "✗", "INFO": "ℹ"}[level]
        print(f"  {sym} [{level}] {name}  {detail}")

    print(f"== audio report ==（{wav}）")
    if not wav.exists():
        print(f"✗ 产物不存在：{wav}（--report-only 只读已有产物，适用于上一版 audio.wav 还在的"
              f"回看场景；timeline 有新改动时先用 --report 混音+报告，或先跑 vtx audio 再 --report-only）")
        return 2

    # 1) 整片 LUFS / TP（判级对象 = audio.wav 本体，与 mux/成片无关）
    i_lufs, tp = ebur128_summary(c, wav)
    if _finite_number(i_lufs):
        lv, dt = grade_lufs(i_lufs, tol=thr.get("lufs_tol", LUFS_TOL),
                            warn_tol=thr.get("lufs_warn_tol", LUFS_WARN_TOL))
        check("整片响度 I", lv, dt, {"i_lufs": i_lufs, "target": LUFS_TARGET})
    else:
        check("整片响度 I", "FAIL", "ebur128 测量失败或无有限响度值")
    lv, dt = grade_tp(tp, warn=thr.get("tp_warn", TP_WARN_DBTP), fail=thr.get("tp_fail", TP_FAIL_DBTP))
    check("整片真峰 TP", lv, dt, {"tp_dbfs": tp if _finite_number(tp) else None})

    # 2/3) 轨级：人声 vs duck 床乐响度差 + duck 咬合/回升（stem 现场渲染）
    # Match the render graph's pure event transforms before selecting stems.
    # Keep the original timeline and final WAV/receipts untouched.
    atracks = _plugin_hooks.run_audio_events(c, c.tl["audio_tracks"])
    duck_tr = [tr for tr in atracks if tr.get("duck")]
    by_names = {n for tr in duck_tr for n in tr.get("duck_by", ["voice"])}
    voice_tr = [tr for tr in atracks if tr["name"] in by_names]
    if not duck_tr or not voice_tr:
        check("人声-床乐响度差", "INFO", "timeline 无 duck 轨或无 duck_by 人声轨，跳过轨级检查")
        check("duck 咬合/回升", "INFO", "同上")
    else:
        print(f"  渲染轨级 stem（{len(voice_tr)} 人声轨 / {len(duck_tr)} duck 轨）…")
        vw, rw, dw = render_stems(c, voice_tr, duck_tr, c.cache / "report_audio")
        wins, _total = _activity_windows(c, vw)
        sv = ebur128_series(c, vw)
        sd = ebur128_series(c, dw)
        voice_l = _mean_s_in(sv, wins)
        bed_l = _mean_s_in(sd, wins)
        if voice_l is None or bed_l is None:
            check("人声-床乐响度差", "INFO", f"活动窗响度不可测（{len(wins)} 窗）")
        else:
            lv, dt = grade_loudness_diff(voice_l - bed_l,
                                         fail_b=thr.get("diff_fail", DIFF_FAIL_BELOW),
                                         warn_b=thr.get("diff_warn", DIFF_WARN_BELOW),
                                         pass_max=thr.get("diff_pass_max", DIFF_PASS_MAX))
            check("人声-床乐响度差", lv, dt,
                  {"voice_s": round(voice_l, 1), "bed_duck_s": round(bed_l, 1),
                   "windows": len(wins)})
        # duck 包络：M_duck - M_raw（10Hz），逐活动窗测 attack/release 取中位
        sr = ebur128_series(c, rw)
        mr = {round(t, 1): m for t, m, _ in sr}
        md = {round(t, 1): m for t, m, _ in sd}
        gains = [(t, md[round(t, 1)] - mr[round(t, 1)]) for t, m, _ in sd
                 if round(t, 1) in mr and m > -70 and mr[round(t, 1)] > -70]
        attacks, releases, depths = [], [], []
        for vs, ve in wins:
            if ve - vs < 1.0: continue
            seg = [(t, g) for t, g in gains if vs <= t <= ve + 5.0]
            if len(seg) < 10: continue
            inner = [g for t, g in seg if vs <= t <= ve]
            if not inner: continue
            q = inner[-max(1, len(inner) // 3):]           # 窗尾 1/3 = 稳态 duck 深度
            depth = statistics.median(q)
            if depth > -1.0: continue                      # 该窗无避让发生
            depths.append(depth)
            half = depth / 2.0
            at = next((t - vs for t, g in seg if vs <= t <= ve and g <= half), None)
            if at is not None: attacks.append(at)
            rl = next((t - ve for t, g in seg if t > ve and g >= -3.0), None)
            releases.append(rl)                            # None = 5s 窗内未回升
        if depths:
            r_obs = [x for x in releases if x is not None]   # None=该窗后 5s 未观测到回升
            lv, dt = grade_duck(statistics.median(depths),
                                statistics.median(attacks) if attacks else None,
                                statistics.median(r_obs) if r_obs else None)
            check("duck 咬合/回升", lv, dt,
                  {"depth_db": round(statistics.median(depths), 1),
                   "windows": len(depths),
                   "no_release_windows": sum(1 for x in releases if x is None)})
        else:
            check("duck 咬合/回升", "INFO", "活动窗内未观测到有效避让段")

    # 4) 静音空洞（整片 audio.wav，含片头）
    holes = silences(c, wav, HOLE_NOISE, HOLE_MIN_D)
    dur = c.dur * c.spf
    lv, dt = grade_holes(holes, total_dur=dur,
                         warn=thr.get("hole_warn", HOLE_WARN), fail=thr.get("hole_fail", HOLE_FAIL))
    check("静音空洞", lv, dt, {"holes": [[round(s, 2), round(e, 2)] for s, e in holes]})

    # 汇总 + JSON（checks 与 qc_report 同构，vtx diff 可直接对比）
    n_fail = sum(1 for x in checks if x["level"] == "FAIL")
    n_warn = sum(1 for x in checks if x["level"] == "WARN")
    summary = "FAIL" if n_fail else ("WARN" if n_warn else "PASS")
    rep = {"summary": summary, "fail": n_fail, "warn": n_warn,
           "thresholds": {**{k: v for k, v in zip(OVERRIDABLE, [
               LUFS_TOL, LUFS_WARN_TOL, TP_WARN_DBTP, TP_FAIL_DBTP, DIFF_FAIL_BELOW,
               DIFF_WARN_BELOW, DIFF_PASS_MAX, DUCK_ATTACK_PASS, DUCK_ATTACK_FAIL,
               DUCK_RELEASE_PASS, DUCK_RELEASE_FAIL, HOLE_WARN, HOLE_FAIL])}, **thr,
               # Both legacy keys describe the one effective TP ceiling, not the request.
               "tp_warn": tp_threshold, "tp_fail": tp_threshold},
           "source": str(wav), "checks": checks}
    jp = Path(json_path) if json_path else c.out / "audio_report.json"
    fstxn.atomic_write(jp, json.dumps(rep, ensure_ascii=False, indent=2), root=None if json_path else c.root)
    print(f"音频报告：{summary}（PASS {len(checks)-n_fail-n_warn} / WARN {n_warn} / FAIL {n_fail}）"
          f"（[timing] {time.time()-t0:.1f}s，JSON {jp}）")
    return 1 if n_fail else 0
