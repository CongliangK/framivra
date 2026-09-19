"""plugins/denoise/quality —— 降噪质量测量器自包含包(R-20 §3.4;修订二十)。

五件(修订二十):quality.py(指标)/noise_gen.py(噪声生成)/corpus.py(名册与
谱系核查)/report.py(schema 与输出)/cer.py(CER 计算与讲稿对齐)。

冻结语义(修订十七):本包自 P0 末测量器哈希在档起冻结;P1–P4 期本路径任何
改动 =「改尺」流程接管(经理批准+全案公告+基线重测)。改尺前本包行为以
P0 报告在档哈希为准,逐窗事前门(L4v)复跑哈希不过不开窗。

测量域(修订五,本书 §3.3 前置段,本包实现口径):
  1. 统一测量域 = 16 kHz mono:PESQ(宽带,仅收 8000/16000)/STOI/segSNR/LSD
     全部在 16k 域计算;
  2. 下混与重采样钉死:顺序 = 先下混(stereo→mono,通道算术均值)后重采样
     (→16k);重采样器 = soxr 单一实现,禁 librosa/ffmpeg 混用(MEASURE_FS
     与 to_measure_domain 是唯一入口);
  3. 48k 原生域并行:带宽收缩与全带能量分布在原生采样率域计算(补 16k 域
     奈奎斯特 8 kHz 以上盲区);
  4. 局限声明:>8 kHz 谱细节不入 LSD;真实轨噪声底口径同 16k 域。

依赖版本(修订六):依赖版本清单 = 本目录 versions.json(importlib.metadata
采集);check_dependencies() 为启动自检,版本不符即 fail-hard(禁静默降级)。
"""
import json
import os
import sys
from pathlib import Path

# 统一测量域(修订五):16 kHz mono。全包唯一样率常量,禁旁路。
MEASURE_FS = 16000

# 测量域重采样唯一实现(修订五:soxr 单一实现,禁 librosa/ffmpeg 混用)。
RESAMPLER = "soxr"

PKG_DIR = Path(__file__).resolve().parent
VERSIONS_FILE = PKG_DIR / "versions.json"

# 修订六:被采集与自检的运行依赖(pesq 实值 0.0.4 系 importlib.metadata 形——
# v1 §1.5① 的 getattr(__version__,'ok') 回退假象证据形态自此废止)。
DEPENDENCIES = ("numpy", "scipy", "soxr", "soundfile", "pesq", "pystoi", "librosa")


def conda_python() -> Path:
    """测量器解释器 = DENOISE_PY(与链同一解释器约定,denoise_voice.conda_python
    同名同义;自包含包不 import 链实现,约定复制并在 P0 报告在档)。"""
    override = os.environ.get("DENOISE_PY")
    if override:
        return Path(override)
    return Path.home() / "miniconda3" / "envs" / "denoise" / ("python.exe" if os.name == "nt" else "bin/python")


def collect_versions() -> dict:
    """importlib.metadata 形采集(修订六;缺依赖抛异常,不回退 'ok' 假象)。"""
    import importlib.metadata as im

    return {m: im.version(m) for m in DEPENDENCIES}


def check_dependencies(versions_file: Path = None) -> dict:
    """启动自检(修订六):实测版本 vs versions.json 钉值,不符即 fail-hard。

    返回实测版本 dict;任何缺失/不符直接 RuntimeError(禁静默降级)。
    """
    vf = Path(versions_file) if versions_file else VERSIONS_FILE
    pinned = json.loads(vf.read_text(encoding="utf-8"))["dependencies"]
    actual = collect_versions()
    errors = []
    for mod, ver in pinned.items():
        got = actual.get(mod)
        if got is None:
            errors.append(f"{mod}: ABSENT(pinned {ver})")
        elif got != ver:
            errors.append(f"{mod}: pinned {ver} != actual {got}")
    if errors:
        raise RuntimeError(
            "[quality] 依赖版本自检失败(修订六 fail-hard,禁静默降级):\n  "
            + "\n  ".join(errors)
        )
    return actual


def require_measure_domain(x, fs: int):
    """测量域断言:一维 float ndarray + 16 kHz。所有指标函数入口统一走此门。"""
    import numpy as np

    arr = np.asarray(x)
    if arr.ndim != 1:
        raise ValueError(f"测量域须 mono 一维,得到 shape={arr.shape}")
    if int(fs) != MEASURE_FS:
        raise ValueError(f"测量域须 {MEASURE_FS} Hz(修订五),得到 {fs}")
    return arr.astype(np.float64, copy=False)


if __name__ == "__main__":  # 直接运行 = 版本自检 CLI(P0 报告复现命令之一)
    try:
        vers = check_dependencies()
    except Exception as e:  # noqa: BLE001(fail-hard 输出后退出码 1)
        print(e, file=sys.stderr)
        sys.exit(1)
    print(json.dumps(vers, ensure_ascii=False, indent=2))
