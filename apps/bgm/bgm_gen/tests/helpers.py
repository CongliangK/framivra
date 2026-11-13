"""R4 测试共享工具（tests/ 内部使用，不被 pytest 收集）。

关键坑（R3 实测）：sys.executable 在部分直呼环境被改写成宿主 AppImage 路径，
一切子进程解释器一律用 runtime.json 的 python_system 绝对路径（缺失时退 "python3"
字面量，绝不用 sys.executable）。
"""

import json
import os
import subprocess

# ---- 路径常量（绝对路径，测试不依赖 cwd） --------------------------------
PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))      # bgm_gen/
WORKSPACE = os.path.dirname(PKG_DIR)                                        # 仓库根（本地开发目录名 music_tools）
OUT_DIR = os.path.join(WORKSPACE, "out")                                    # 产物根（仓库根/out）
WORKFLOWS_DIR = os.path.join(WORKSPACE, "workflows")                        # ComfyUI 工作流
EXAMPLES_DIR = os.path.join(WORKSPACE, "examples")                          # 示例工程
LEGACY_DIR = os.path.join(PKG_DIR, "legacy")                                # 冻结契约旧脚本
ANALYSIS_DIR = os.path.join(PKG_DIR, "analysis")                            # QC 分析脚本
ASSETS_JSON = os.path.join(PKG_DIR, "assets.json")
BASELINES_W2A = os.path.join(PKG_DIR, "tests", "baselines_w2a.json")
BASELINES_W2B = os.path.join(PKG_DIR, "tests", "baselines_w2b.json")
R4_TMP = "/tmp/r4smoke"                                                     # R4 产物统一落点
# 并发隔离：BGM_TMP_ROOT 由 conftest 按进程 pid 注入（v3 R1 前置），
# 多 worker 并行跑 pytest 时不共享同一 /tmp/r4smoke 子目录。

_DOCTOR_REPORT_CACHE = None


# ---- 位级基线版本门（2026-10-11 用户裁定：外部工具版本漂移 → 显式 skip） ------
# 位级哈希锚点（out/minimal/mix.flac、baselines_w2a 的 sha256、harvest 事实表）
# 的字节由外部工具（ffmpeg/fluidsynth）参与产出，**只对描述符 binary_pin 冻结的
# 版本有效**。实测版本 ≠ pin 时相关位级断言测试显式 skip（绝不静默当绿）；
# 版本一致时静默放行、照常严格执行。详见 tests/README.md「位级基线与冻结版本」。

# 冻结 pin 的读取位置（engines/descriptors/*.json 的 binary_pin = 单一事实源，
# 与 harvest 事实表深一致；此处只读，绝不改生产数据）：
#   fluidsynth → sf2 描述符主 pin；ffmpeg → master 描述符主 pin（synth/sample/sf2
#   的 additional_pins 同值，harvest verify 保证一致）。
_FROZEN_PIN_HOST = {"fluidsynth": "sf2", "ffmpeg": "master"}
_BINARY_VERSION_CACHE = {}


def frozen_binary_pin(name):
    """冻结 pin 版本（描述符 binary_pin；测试断言口径的单一事实源）。"""
    eng = _FROZEN_PIN_HOST[name]
    pin = load_json(os.path.join(PKG_DIR, "engines", "descriptors",
                                 f"{eng}.json"))["binary_pin"]
    return pin["version"]


def probed_binary_version(name):
    """实测二进制版本（会话级缓存；解析口径与 harvest._probe_binary/doctor 一致：
    `ffmpeg -version` 首行版本号 / `fluidsynth --version` 的 runtime version）。"""
    if name not in _BINARY_VERSION_CACHE:
        import re
        try:
            if name == "ffmpeg":
                r = subprocess.run(["ffmpeg", "-version"], capture_output=True,
                                   text=True, timeout=30)
                m = re.search(r"ffmpeg version ([\d.]+)", r.stdout or "")
            elif name == "fluidsynth":
                r = subprocess.run(["fluidsynth", "--version"], capture_output=True,
                                   text=True, timeout=30)
                m = re.search(r"runtime version ([\d.]+)",
                              (r.stdout or "") + (r.stderr or ""))
            else:
                raise ValueError(f"未知的版本探测目标：{name}")
            _BINARY_VERSION_CACHE[name] = m.group(1) if m else None
        except Exception:                                        # noqa: BLE001
            _BINARY_VERSION_CACHE[name] = None
    return _BINARY_VERSION_CACHE[name]


def skip_if_binary_version_drift(*names):
    """位级基线版本门：任一二进制实测版本 ≠ 冻结 pin → pytest.skip（显式计数）。

    skip reason 钉死格式「检测到 X 版本 A≠冻结 B，位级基线仅对冻结版本有效」。
    仅在"探测成功且版本不等"时 skip；探测失败（二进制缺失等）不 skip——
    让测试自然失败，如实暴露环境问题而非误判为版本漂移。
    """
    import pytest
    for name in names:
        actual = probed_binary_version(name)
        frozen = frozen_binary_pin(name)
        if actual is not None and actual != frozen:
            pytest.skip(f"检测到 {name} 版本 {actual}≠冻结 {frozen}，"
                        f"位级基线仅对冻结版本有效（安装 pin 版本或重采基线后恢复严格断言）")


def doctor_report_once():
    """doctor_report() 会话级缓存（v3 R4：fast 预算贴边 73.6/75s，doctor 二进制
    探针实测 1.86s/次 × 2 调用点；同一 pytest 会话内机器状态不变，缓存安全——
    PLAN §6 R2 候选优化，R4 主线落地）。"""
    global _DOCTOR_REPORT_CACHE
    if _DOCTOR_REPORT_CACHE is None:
        from bgm_gen.doctor import doctor_report
        _DOCTOR_REPORT_CACHE = doctor_report()
    return _DOCTOR_REPORT_CACHE


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def runtime():
    from bgm_gen.paths import effective_runtime
    return effective_runtime()


def require_external_runtime():
    """Stock-library tests are optional; configured installations stay strict."""
    import pytest
    from bgm_gen.paths import local_runtime
    if not local_runtime() and not os.environ.get("MUSIC_ASSETS_ROOT"):
        pytest.skip("optional stock assets/runtime not supplied; no bundled media or model weights")


def require_engine_assets(engine):
    import pytest
    rt = runtime()
    if engine == "sf2" and not rt.get("sf2"):
        pytest.skip("optional SF2 font mapping not supplied in MUSIC_RUNTIME")
    if engine == "sfz" and not (rt.get("tools") or {}).get("sfizz_render"):
        pytest.skip("optional tools.sfizz_render not supplied in MUSIC_RUNTIME")
    if engine == "sample" and not os.path.isdir(rt["samples_root"]):
        pytest.skip("optional sample library not supplied")


def require_minimal_assets():
    require_engine_assets("sf2")
    require_engine_assets("sample")


def require_fixture(path):
    import pytest
    if not os.path.isfile(path):
        pytest.skip(f"historical external fixture not distributed: {path}")


def py3():
    """子进程解释器：runtime.json python_system（绝对路径），缺省退 "python3"。"""
    py = runtime().get("python_system")
    return py if py and os.path.isfile(py) else "python3"


def run(cmd, cwd=None, timeout=600, check=False, env_extra=None):
    """统一子进程封装：注入 PYTHONPATH=WORKSPACE（可 import bgm_gen）。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = WORKSPACE + (os.pathsep + env["PYTHONPATH"]
                                     if env.get("PYTHONPATH") else "")
    if env_extra:
        env.update(env_extra)
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=timeout, check=check)


def in_proc_env():
    """in-process 引擎调用前把 WORKSPACE 塞进 sys.path（幂等）。"""
    import sys
    if WORKSPACE not in sys.path:
        sys.path.insert(0, WORKSPACE)
    return WORKSPACE


def tmpdir(sub=""):
    root = os.environ.get("BGM_TMP_ROOT", R4_TMP)
    d = os.path.join(root, sub) if sub else root
    os.makedirs(d, exist_ok=True)
    return d


def write_wav(path, sig, sr=22050, channels=1):
    """合成 wav（PCM16）——QC 层回归的信号源。sig 可为 mono 1D 或 (n, ch)。"""
    import numpy as np
    import wave
    a = np.asarray(sig, dtype=np.float64)
    if a.ndim == 1 and channels > 1:
        a = np.repeat(a[:, None], channels, axis=1)
    pcm = (np.clip(a, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path
