"""demucs 引擎（stem 分轨，PLAN.md BG-D10 降维闸口；CLI 子命令 `bgm stem` 由 R3 包装）。

payload：
    input       必填，输入音频路径
    model       可选，默认 "htdemucs"
    out_dir     可选，stems 根目录；缺省 = <调用者 cwd>/out/smoke/demucs/
    device      可选，"auto"(默认)|"gpu"|"cpu"——auto/gpu 走 BG-D8 显存闸
                （默认阈值 4GB、等待 120s；auto 超时降级 cpu 并记录，gpu 超时抛错）
    two_stems   可选，如 "vocals"（两分轨模式；默认四分轨 vocals/drums/bass/other）
    vram_need_mb   可选，默认 4096
    vram_timeout_s 可选，默认 120
    offline     只允许 True；强制 --repo 本地模型仓，禁止隐式下载

产物目录约定（W2B）：{out_dir}/{track}/{stem}.wav，track = 输入文件名去扩展名。
  （demucs CLI 原始产物在 {out_dir}/_raw/{model}/{track}/，本引擎搬移到约定位置。）

环境（W1B RUNBOOK 坑表）：
    MUSIC_RUNTIME 的 demucs.repo 必须是用户提供的本地模型仓（YAML + .th）；
    --repo 使用 LocalRepo，不使用可下载权重的 RemoteRepo。环境只读，不创建权重。

sr 勘误（R1 reviewer MINOR-1）：htdemucs 恒输出 44100Hz，与输入 sr 无关。
spec.sr 声明其他值 → 引擎内 ffmpeg 重采样全部 stems（BG-D5 引擎内归一）。

出参：path = vocals stem（惯例主输出；纯器乐输入接近无声是正确路由，见 RUNBOOK §5.5），
result["stems"] 附全部分轨度量。

determinism = "tolerant"（GPU 推理非逐位确定；分级证据见 tests/baselines_w2b.json）。

v3 R1-W1：device 枚举等 payload 校验由描述符驱动（base.validate_payload，
descriptors/demucs.json）；本引擎保留语义守卫：input 文件存在性、runtime
demucs.python/HF_HOME、产物存在性。
"""

import glob
import os
import shutil
import subprocess
import tempfile
import time

from .base import (BaseEngine, EngineError, default_out, measure_wav,
                   register_engine)
from .gpu_guard import wait_for_vram

DEFAULT_MODEL = "htdemucs"
NATIVE_SR = 44100  # htdemucs 恒定输出（W1B RUNBOOK 勘误）


def _resample(path, sr):
    """ffmpeg 原地重采样到 sr。"""
    tmp = tempfile.NamedTemporaryFile(suffix=os.path.splitext(path)[1], delete=False)
    tmp.close()
    try:                                       # m-1：异常路径确保临时件清场
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path,
                        "-ar", str(int(sr)), tmp.name], check=True)
        shutil.move(tmp.name, path)  # /tmp 与产物目录可能跨设备，不能用 os.replace
    finally:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)


@register_engine("demucs")
class DemucsEngine(BaseEngine):
    determinism = "tolerant"
    summary = "htdemucs 分轨（comfyui env 子进程；四分轨默认，GPU 串行保护 BG-D8）"

    def render(self, spec):
        p = spec.get("payload") or {}
        src = p.get("input")
        if not src or not os.path.isfile(src):
            raise EngineError(f"demucs payload.input 不存在：{src!r}")
        model = p.get("model", DEFAULT_MODEL)
        device = p.get("device", "auto")
        two_stems = p.get("two_stems")
        out_dir = os.path.abspath(p.get("out_dir") or default_out("smoke", "demucs"))
        track = os.path.splitext(os.path.basename(src))[0]

        configured_python = (self.rt.get("demucs") or {}).get("python") or self.rt.get("python_torch")
        py = shutil.which(configured_python) if configured_python else None
        if not py:
            raise EngineError(f"runtime.json 缺 demucs.python/python_torch：{py!r}")
        # HF_HUB_OFFLINE alone does not stop torch.hub downloads. Always use
        # Demucs LocalRepo (--repo), never its default RemoteRepo/download path.
        config = self.rt.get("demucs") or {}
        repo = config.get("repo")
        if not repo or not os.path.isdir(repo):
            raise EngineError("MUSIC_RUNTIME 缺 demucs.repo 本地权重目录；请自行准备模型，禁止自动下载")
        weights = glob.glob(os.path.join(repo, "*.th"))
        model_present = (os.path.isfile(os.path.join(repo, model + ".yaml"))
                         or any(os.path.basename(f).split("-", 1)[0] == model
                                or os.path.splitext(os.path.basename(f))[0] == model
                                for f in weights))
        if not weights or not model_present:
            raise EngineError(f"demucs 本地权重/模型定义缺失：{model!r} in {repo}；不会下载")
        if not p.get("offline", True):
            raise EngineError("demucs offline=false 不受支持：本 app 禁止隐式下载权重")
        env = dict(os.environ)
        env.update(config.get("env") or {})
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"

        raw_dir = os.path.join(out_dir, "_raw")
        stems_dir = os.path.join(out_dir, track)
        os.makedirs(stems_dir, exist_ok=True)
        log = tempfile.NamedTemporaryFile(suffix=".demucs.log", delete=False)
        log.close()

        state = {"dev": device if device == "cpu" else None, "elapsed": None,
                 "guard": None}

        # M-2：墙钟上限（参考 sfz._run_bounded 的护栏思路）——demucs GPU 分离
        # 通常近实时或更快，max(600s, 输入时长×4) 已给足权重加载/冷启动余量；
        # 超时强杀并抛 EngineError，防 CUDA 死锁/挂起把整条产线拖死。
        est_dur_s = measure_wav(src)["duration_s"]
        timeout_s = max(600.0, est_dur_s * 4.0)

        def _run(dev):
            cmd = [py, "-m", "demucs", "--repo", os.path.abspath(repo),
                   "-n", model, "--device", dev, "-o", raw_dir]
            if two_stems:
                cmd += ["--two-stems", two_stems]
            cmd.append(os.path.abspath(src))
            t0 = time.time()
            try:
                r = subprocess.run(cmd, capture_output=True, text=True,
                                   env=env, timeout=timeout_s)
            except subprocess.TimeoutExpired as e:
                so, se = e.stdout or "", e.stderr or ""
                if isinstance(so, bytes):
                    so = so.decode(errors="replace")
                if isinstance(se, bytes):
                    se = se.decode(errors="replace")
                with open(log.name, "ab") as f:
                    f.write((so + "\n---stderr(截断)---\n" + se).encode())
                raise EngineError(
                    f"demucs 超过墙钟上限被终止（>{timeout_s:.0f}s，输入约 "
                    f"{est_dur_s:.0f}s、device={dev}，log={log.name}）——"
                    "疑似 CUDA 死锁/权重加载挂起：检查 nvidia-smi 占用与 log 尾部；"
                    "确认无误后可调大输入时长系数或分段处理") from e
            with open(log.name, "ab") as f:
                f.write(((r.stdout or "") + "\n---stderr---\n" + (r.stderr or "")).encode())
            if r.returncode != 0:
                tail = ((r.stderr or "") + (r.stdout or ""))[-1500:]
                raise EngineError(
                    f"demucs 失败（exit={r.returncode}, device={dev}）：…{tail}")
            return round(time.time() - t0, 1)

        # BG-D8：显存闸（cpu 显式指定则直接跑，不占锁不探测）。
        # cuda：任务体在锁内执行（与 ai/其他本地 GPU 任务互斥）；
        # 降级 cpu：Guard 已在 __enter__ 内放锁，任务体无锁执行（不占 GPU 不阻塞队列）。
        if device == "cpu":
            state["guard"] = {"device": "cpu", "degraded": False,
                              "reason": "payload 显式 cpu"}
            state["elapsed"] = _run("cpu")
        else:
            on_timeout = "cpu" if device == "auto" else "raise"
            with wait_for_vram(need_mb=int(p.get("vram_need_mb", 4096)),
                               timeout_s=float(p.get("vram_timeout_s", 120)),
                               on_timeout=on_timeout) as rec:
                guard_rec = dict(rec)
                state["guard"] = guard_rec
                state["elapsed"] = _run(rec["device"])

        # 产物搬移：{out_dir}/_raw/{model}/{track}/*.wav → {out_dir}/{track}/
        produced = sorted(glob.glob(os.path.join(raw_dir, model, track, "*.wav")))
        if not produced:
            raise EngineError(
                f"demucs 产物未找到：{os.path.join(raw_dir, model, track)}（log={log.name}）")
        stem_paths = {}
        for f in produced:
            dst = os.path.join(stems_dir, os.path.basename(f))
            shutil.move(f, dst)
            stem_paths[os.path.splitext(os.path.basename(f))[0]] = dst
        shutil.rmtree(raw_dir, ignore_errors=True)

        # sr 勘误处理：恒 44100；spec 声明其他值 → 全部 stems 重采样（BG-D5 引擎内归一）
        want_sr = spec.get("sr")
        resampled = False
        first = stem_paths[next(iter(stem_paths))]
        if measure_wav(first)["sr"] != NATIVE_SR:
            raise EngineError("demucs 输出 sr != 44100，与 W1B RUNBOOK 勘误不符，需人工核查")
        if want_sr is not None and int(want_sr) != NATIVE_SR:
            for f in stem_paths.values():
                _resample(f, int(want_sr))
            resampled = True

        stems = {name: {**measure_wav(f), "path": f} for name, f in stem_paths.items()}
        primary = stems.get("vocals") or next(iter(stems.values()))
        return {"path": primary["path"], "sr": primary["sr"],
                "channels": primary["channels"],
                "duration_s": round(primary["duration_s"], 3),
                "rms_db": round(primary["rms_db"], 2),
                "stems": stems, "model": model,
                "device": state["guard"]["device"],
                "two_stems": two_stems, "track": track,
                "native_sr": NATIVE_SR, "resampled": resampled,
                "elapsed_s": state["elapsed"], "gpu_guard": state["guard"],
                "log": log.name}
