"""sfz 引擎：SFZ 乐器 → wav（sfizz_render 子进程，R1 免 sudo 落位版）。

payload（v1）：
    sfz    绝对路径，或相对 assets_root/sfz/ 的路径。
           相对路径支持别名前缀归一（VSCO2→VSCO2_CE、Karoryfer→karoryfer），
           也支持纯文件名在 sfz 树内按 sorted 顺序唯一匹配（如 "CelloEnsSusVib.sfz"）。
    notes  [[pitch, beat, dur, vel], ...]（BG-D7 统一记谱）
    bpm    默认 120

v3 R2 增（PLAN_v3 §3.2 机制 B）：
    events  控制事件数组（cc/pitch_bend/keyswitch/program_change/note 载体投影后）；
            非空时改走 midi_util.events_to_smf（notes 投影为 note 事件经
            event_sort_key 合并编码进同一 SMF）；无 events 保持 write_notes_smf
            旧路径字节不动（v1 位级锚点）。
    bend_range  弯音量程半音数（缺省 2.0；轨级声明经 track_projection 透传——
                SFZ 文件级 pitch_wheel_range 逐文件不同，工程轨含弯音必须显式声明）。

实现：midi_util 写临时 SMF → sfizz_render.sh --sfz/--midi/--wav -s spec.sr → spec.out。
stderr 出现 "Cannot set current thread scheduling" 属无害噪音（JACK 探测失败），
不视为失败；只检查退出码与产物存在。
determinism="deterministic"：同 sr 下逐位一致；不同 sr 下 sfizz 内部重采样
路径不同，时长存在微差（已知且可接受，PLAN.md R2 验收口径=同 spec 同 sr）。
"""

import os
import shutil
import subprocess
import tempfile
import time

from .base import (BaseEngine, EngineError, default_out, measure_wav,
                   register_engine)
from . import midi_util

_DIR_ALIAS = {"VSCO2": "VSCO2_CE", "vsco2": "VSCO2_CE",
              "Karoryfer": "karoryfer", "karoryfer": "karoryfer"}

# 输出体积闸/墙钟超时的余量（v3 R4 冻机事故修复，见 _run_bounded）
_TAIL_ALLOW_S = 30.0        # 尾巴余量。R4-W1 命运工程实测校准：VPO SEC-sustain
                            # 族正常收尾尾音音色相关可达 ~21s（oboe 谱面 316.7s
                            # 实渲 337.7s；flute 仅 317.7s）——原 15s 误拦正常
                            # 收尾。失控拦截不受影响：sfizz offline 写满速率
                            # 100+MB/s（§10.7：30s 写 4.28GB），30s 余量上限
                            # 仍秒级触闸（watchdog 回归锁口径 <60s 不变）。
_WATCH_POLL_S = 0.25


def _expected_max_beat(payload):
    """notes 与事件的最大结束 beat（估计渲染时长用；控制事件按其 t 计）。"""
    mb = 0.0
    for n in payload.get("notes") or []:
        try:
            mb = max(mb, float(n[1]) + float(n[2]))
        except (TypeError, ValueError, IndexError):
            pass
    for ev in payload.get("events") or []:
        try:
            mb = max(mb, float(ev.get("t", 0)))
        except (TypeError, ValueError):
            pass
    return mb


def _run_bounded(cmd, out_path, cap_bytes, timeout_s):
    """sfizz_render 有界执行（v3 R4 冻机事故修复）。

    事故（2026-08-23 实测两形态）：VPO PERF 族含 loop_continuous 采样
    （无视 note_off 永续循环），sfizz 1.2.3 对其渲染不终止——旧实现
    （subprocess.run+capture）30s 可写出 4.28GB（约 6.8h 音频），失控产物
    的全量度量加载曾致整机内存耗尽冻死。三重护栏：
      ① 输出体积闸：wav 超过 (预期时长+尾巴)×2 上限 → SIGKILL；
      ② 增长停滞：已写出后 60s 零增长（挂起，如管道背压/收尾死锁）→ SIGKILL；
      ③ 墙钟超时：预期×4+120s → SIGKILL。
    stdout/stderr 一律落临时文件（绝不接 PIPE——实测 sfizz 噪音日志会撑爆
    64KB 管道缓冲把子进程堵死在半途）。返回 (returncode, stderr_tail)。"""
    t0 = time.monotonic()
    err_fh = tempfile.NamedTemporaryFile(prefix="sfizz_err_", delete=False)
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                stderr=err_fh)
        err_fh.close()
        why = None
        last_sz, last_change = 0, t0
        while proc.poll() is None:
            try:
                sz = os.path.getsize(out_path)
            except OSError:
                sz = 0
            if sz > cap_bytes:
                why = (f"输出超过预期上限（已写 {sz / 1e6:.0f}MB > 上限 "
                       f"{cap_bytes / 1e6:.0f}MB）——音色疑似含无限循环采样"
                       f"（如 VPO PERF 族 loop_continuous），sfizz 渲染不终止")
                break
            if sz > 0:
                if sz != last_sz:
                    last_sz, last_change = sz, time.monotonic()
                elif time.monotonic() - last_change > 60.0:
                    why = (f"渲染停滞（输出 {sz / 1e6:.0f}MB 已 60s 零增长，"
                           f"疑似 sfizz 挂起）")
                    break
            if time.monotonic() - t0 > timeout_s:
                why = f"渲染超时（>{timeout_s:.0f}s）"
                break
            time.sleep(_WATCH_POLL_S)
        if why:
            proc.kill()
            proc.wait()
            try:
                os.unlink(out_path)
            except OSError:
                pass
            with open(err_fh.name, "rb") as f:
                tail = f.read()[-2000:].decode(errors="replace")
            raise EngineError(f"sfizz_render 已被护栏终止：{why}\n"
                              f"stderr 尾部：{tail}\ncmd: {cmd}")
        proc.wait()
        with open(err_fh.name, "rb") as f:
            tail = f.read()[-2000:].decode(errors="replace")
        return proc.returncode, tail
    finally:
        try:
            os.unlink(err_fh.name)
        except OSError:
            pass


def resolve_sfz(sfz_ref, runtime):
    """sfz 引用 → 绝对路径（绝对路径直通；相对走 assets_root/sfz）。"""
    p = os.path.expanduser(sfz_ref)
    if os.path.isabs(p):
        if not os.path.exists(p):
            raise EngineError(f"sfz 文件不存在：{p}")
        return p
    from ..paths import assets_root
    root = os.path.join(assets_root(runtime), "sfz")
    cand = os.path.join(root, *p.split("/"))
    if os.path.exists(cand):
        return cand
    parts = p.split("/")
    parts[0] = _DIR_ALIAS.get(parts[0], parts[0])
    cand = os.path.join(root, *parts)
    if os.path.exists(cand):
        return cand
    # 纯文件名（或任意相对段）在树内按 sorted 唯一匹配
    base = parts[-1]
    hits = []
    for dirpath, _dirnames, filenames in os.walk(root):
        if base in filenames:
            hits.append(os.path.join(dirpath, base))
    hits.sort()
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise EngineError(f"sfz 文件名歧义（{len(hits)} 处）：{base} → {hits[:5]}")
    raise EngineError(f"sfz 未找到：{sfz_ref}（根：{root}）")


@register_engine("sfz")
class SFZEngine(BaseEngine):
    determinism = "deterministic"
    summary = "SFZ 乐器（VSCO2 CE / Karoryfer，sfizz_render 子进程）"

    def render(self, spec):
        payload = spec.get("payload", {})
        bpm = float(payload.get("bpm", 120.0))
        sr = int(spec.get("sr", 44100))
        out = spec.get("out") or default_out("smoke", self.name, "render.wav")

        sfz_path = resolve_sfz(payload["sfz"], self.rt)
        configured_tool = self.rt.get("tools", {}).get("sfizz_render")
        tool = shutil.which(configured_tool) if configured_tool else None
        if not tool:
            raise EngineError(f"MUSIC_RUNTIME 缺可执行 tools.sfizz_render：{configured_tool!r}")
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)

        mid_tmp = None
        try:
            fd, mid_tmp = tempfile.mkstemp(suffix=".mid")
            os.close(fd)
            events = payload.get("events") or []
            if events:                                  # v3 R2（PLAN_v3 §3.2）：
                combined = midi_util.notes_to_events(payload["notes"]) + list(events)
                midi_util.events_to_smf(
                    mid_tmp, combined, bpm=bpm,
                    bend_range=float(payload.get("bend_range", 2.0)))
            else:                                       # 无 events：旧路径字节不动（v1 锚点）
                midi_util.write_notes_smf(mid_tmp, payload["notes"], bpm=bpm)
            cmd = [tool, "--sfz", sfz_path, "--midi", mid_tmp,
                   "--wav", os.path.abspath(out), "-s", str(sr)]
            exp_s = max(4.0, _expected_max_beat(payload) * 60.0 / bpm)
            cap_bytes = int((exp_s + _TAIL_ALLOW_S) * sr * 2 * 2 * 2)
            rc, se = _run_bounded(cmd, os.path.abspath(out),
                                  cap_bytes, exp_s * 4.0 + 120.0)
            if rc != 0:
                raise EngineError(f"sfizz_render 退出码 {rc}\n"
                                  f"stderr: {se}\ncmd: {cmd}")
        finally:
            if mid_tmp and os.path.exists(mid_tmp):
                os.unlink(mid_tmp)
        if not os.path.exists(out):
            raise EngineError(f"sfizz_render 未产出输出文件：{out}")

        m = measure_wav(out)
        return {"path": out, "sr": m["sr"], "channels": m["channels"],
                "duration_s": m["duration_s"], "rms_db": m["rms_db"]}
