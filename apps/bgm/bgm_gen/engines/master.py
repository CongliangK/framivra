"""master 引擎：母带两策略（PLAN.md §2 FX 层/master）。

payload：
    input       必填，输入音频路径
    mode        可选，"loudnorm"(默认) | "matchering"
    target_lufs 可选，默认 -14（仅 loudnorm；B 站 EBU R128，AGENTS.md D16）
    true_peak   可选，默认 -1（仅 loudnorm，dBTP）
    ref         matchering 模式必填，参考音频路径
    out         可选，输出路径；缺省 = <仓库根>/out/smoke/master/{mode}_{track}.wav

loudnorm 策略 = ffmpeg 两遍法：
    第一遍 -af loudnorm=I:TP:LRA:print_format=json -f null - 取测量值；
    第二遍带 measured_I/TP/LRA/thresh/offset + linear=true 应用（输出保持输入 sr）。

matchering 策略 = comfyui env 子进程跑 mx.process(target, reference, results=[pcm16])：
    入口 2.0.6 是 mx.process（无 CLI，W1B RUNBOOK §3）；stderr 噪音忽略，exit!=0 才报错。

出参附加：lufs / true_peak_db（对成品 ffmpeg ebur128 复测）、normalization_type（loudnorm）。

determinism = "deterministic"（同输入同参数 → 逐位一致；两遍法测量值确定、
matchering 为 CPU 确定性 DSP；分级证据见 tests/baselines_w2b.json）。

v3 R1-W1：mode 枚举/matchering ref 必填等 payload 校验由描述符驱动
（base.validate_payload，descriptors/master.json invariants）；本引擎保留
语义守卫：input/ref 文件存在性。
"""

import json
import os
import re
import shutil
import subprocess
import tempfile

from .base import (BaseEngine, EngineError, default_out, measure_wav,
                   register_engine)


def _ff(args, timeout=300):
    """跑 ffmpeg，返回 (returncode, stdout+stderr)。"""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats"] + args,
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def measure_lufs(path):
    """ffmpeg ebur128（peak=true）测综合响度与真峰值。返回 {"lufs", "true_peak_db"}。"""
    rc, out = _ff(["-i", path, "-filter_complex", "ebur128=peak=true",
                   "-f", "null", "-"])
    if rc != 0:
        raise EngineError(f"ebur128 失败：…{out[-800:]}")
    # 只取 Summary 段（此前每 100ms 的行也含 "I: -xx LUFS"，且 t≈0 时恒 -70）
    summary = out[out.rindex("Summary:"):] if "Summary:" in out else out
    m = re.search(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", summary)
    p = re.search(r"Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS", summary)
    if not m:
        raise EngineError(f"ebur128 解析失败：…{out[-800:]}")
    return {"lufs": float(m.group(1)),
            "true_peak_db": float(p.group(1)) if p else None}


def _loudnorm_two_pass(src, dst, target_lufs, true_peak):
    # 第一遍：测量
    rc, out = _ff(["-i", src, "-af",
                   f"loudnorm=I={target_lufs}:TP={true_peak}:LRA=11:print_format=json",
                   "-f", "null", "-"])
    if rc != 0:
        raise EngineError(f"loudnorm 第一遍失败：…{out[-800:]}")
    try:
        j = json.loads(out[out.index("{"):out.rindex("}") + 1])
    except Exception as e:
        raise EngineError(f"loudnorm 测量 JSON 解析失败：{e}；…{out[-800:]}") from e
    mi, mtp = float(j["input_i"]), float(j["input_tp"])
    mlra, mthr, off = float(j["input_lra"]), float(j["input_thresh"]), float(j["target_offset"])

    # 第二遍：应用（linear 优先；ffmpeg 在约束不满足时自动回退 dynamic 并在 summary 说明）
    sr = measure_wav(src)["sr"]
    af = (f"loudnorm=I={target_lufs}:TP={true_peak}:LRA=11"
          f":measured_I={mi}:measured_TP={mtp}:measured_LRA={mlra}"
          f":measured_thresh={mthr}:offset={off}:linear=true:print_format=summary")
    rc, out = _ff(["-y", "-i", src, "-af", af, "-ar", str(int(sr)), dst])
    if rc != 0:
        raise EngineError(f"loudnorm 第二遍失败：…{out[-800:]}")
    ntype = None
    m = re.search(r"Normalization\s+[Tt]ype:\s*(\w+)", out)
    if m:
        ntype = m.group(1)
    return {"measured": {"input_i": mi, "input_tp": mtp, "input_lra": mlra,
                         "input_thresh": mthr, "target_offset": off},
            "normalization_type": ntype}


def _matchering(src, ref, dst, python_torch):
    python_torch = shutil.which(python_torch) if python_torch else None
    if not python_torch:
        raise EngineError(f"runtime.json 缺 matchering.python/python_torch：{python_torch!r}")
    script = tempfile.NamedTemporaryFile(mode="w", suffix="_mx.py", delete=False)
    script.write(
        "import sys\n"
        "import matchering as mx\n"
        "mx.process(target=sys.argv[1], reference=sys.argv[2],\n"
        "           results=[mx.pcm16(sys.argv[3])], config=mx.Config())\n")
    script.close()
    r = subprocess.run([python_torch, script.name, os.path.abspath(src),
                        os.path.abspath(ref), os.path.abspath(dst)],
                       capture_output=True, text=True, timeout=1800)
    os.unlink(script.name)
    if r.returncode != 0:
        raise EngineError(f"matchering 失败（exit={r.returncode}）："
                          f"…{((r.stderr or '') + (r.stdout or ''))[-1500:]}")
    return {"log_tail": ((r.stdout or "") + (r.stderr or ""))[-600:]}


@register_engine("master")
class MasterEngine(BaseEngine):
    determinism = "deterministic"
    summary = "母带：loudnorm 两遍法（-14LUFS/-1dBTP）/ matchering 参考匹配"

    def render(self, spec):
        p = spec.get("payload") or {}
        src = p.get("input")
        if not src or not os.path.isfile(src):
            raise EngineError(f"master payload.input 不存在：{src!r}")
        mode = p.get("mode", "loudnorm")
        track = os.path.splitext(os.path.basename(src))[0]
        out = p.get("out") or spec.get("out") or default_out(
            "smoke", "master", f"{mode}_{track}.wav")
        out = os.path.abspath(out)
        os.makedirs(os.path.dirname(out), exist_ok=True)

        detail = {}
        if mode == "loudnorm":
            target_lufs = float(p.get("target_lufs", -14))
            true_peak = float(p.get("true_peak", -1))
            detail = _loudnorm_two_pass(src, out, target_lufs, true_peak)
            detail.update({"target_lufs": target_lufs, "true_peak": true_peak})
        else:
            ref = p.get("ref")
            if not os.path.isfile(ref or ""):
                raise EngineError(f"master payload.ref 不存在：{ref!r}")
            py = (self.rt.get("matchering") or {}).get("python") or self.rt.get("python_torch")
            detail = _matchering(src, ref, out, py)
            detail["ref"] = os.path.abspath(ref)

        m = measure_wav(out)
        loud = measure_lufs(out)
        return {"path": out, "sr": m["sr"], "channels": m["channels"],
                "duration_s": round(m["duration_s"], 3),
                "rms_db": round(m["rms_db"], 2),
                "mode": mode, "lufs": loud["lufs"],
                "true_peak_db": loud["true_peak_db"], "detail": detail}
