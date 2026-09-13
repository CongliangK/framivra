#!/usr/bin/env python3
"""口播标准降噪链 v1（2026-08-26 定案；P2b 自 scripts/ 迁入 plugins/denoise/，命令面不变）

陷波(ffmpeg) → Resemble 仅降噪级 → 回 48kHz。
用法: denoise_voice.py <in.wav> <out.wav>
依赖: ~/miniconda3/envs/denoise/bin/python + ~/models/resemble-enhance/
      （DENOISE_PY / RESEMBLE_WEIGHTS 环境变量可覆盖，与 denoise_init.sh 同名同义）

本模块零 vtx 依赖（独立 CLI 自治）；plugins/denoise 包的 prepare_source 钩子
复用本模块的 chain()（同一实现，缝 A 产物与手动 CLI 永不分叉）。stereo 参数：
CLI 保持 v1 行为（mono 输出），插件产物传 True（对齐内核裸提取的 48k stereo
契约，防混音 graph 通道协商分叉）。
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

NOTCH = "highpass=f=80,anequalizer=c0 f=50 w=6 g=-30 t=1|c0 f=100 w=6 g=-20 t=1|c0 f=150 w=6 g=-15 t=1"


def conda_python() -> Path:
    """conda denoise 环境解释器（DENOISE_PY 覆盖，对齐 denoise_init.sh）。"""
    override = os.environ.get("DENOISE_PY")
    if override:
        return Path(override)
    return Path.home() / "miniconda3" / "envs" / "denoise" / ("python.exe" if os.name == "nt" else "bin/python")


def weights_dir() -> Path:
    """Resemble 权重根（RESEMBLE_WEIGHTS 覆盖，对齐 denoise_init.sh）。"""
    override = os.environ.get("RESEMBLE_WEIGHTS")
    if override:
        return Path(override)
    return Path.home() / "models" / "resemble-enhance"


def run_dir() -> Path:
    """Resemble enhancer_stage2 run_dir（denoise 推理权重目录）。"""
    return weights_dir() / "enhancer_stage2"


def missing_env() -> list:
    """环境缺失清单（空 = 就绪）：conda 解释器 / 权重 checkpoint。只查文件
    存在性不 import torch（秒回，health 探针 10s 超时内稳定）。"""
    miss = []
    if not conda_python().is_file():
        miss.append(f"conda denoise 环境解释器 {conda_python()}"
                    "（修复：bash plugins/denoise/denoise_init.sh）")
    if not (run_dir() / "ds/G/default/mp_rank_00_model_states.pt").is_file():
        miss.append(f"Resemble 权重 {run_dir()}"
                    "（修复：bash plugins/denoise/denoise_init.sh --download）")
    return miss


def _sh(cmd):
    """子进程执行；失败打印 stderr 尾并退出（fail-hard，不产半链产物）。"""
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print("✗ denoise 链失败：" + " ".join(map(str, cmd))[:200], file=sys.stderr)
        print(r.stderr[-1500:], file=sys.stderr)
        sys.exit(1)
    return r


def chain(in_path, out_path, stereo: bool = False):
    """三步链（denoise_voice.py v1 定案照搬）：陷波 mono → conda Resemble 仅
    降噪（CPU）→ 回 48kHz。stereo=True 终步加 -ac 2（内核缝 A 产物用）。
    中间产物走 TemporaryDirectory（mono s16 数十 MB 级，与 v1 同款）。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rd = run_dir()
    with tempfile.TemporaryDirectory(prefix="denoise-") as td:
        notched = str(Path(td) / "notched.wav")
        denoised = str(Path(td) / "denoised.wav")
        _sh(["ffmpeg", "-y", "-v", "error", "-i", str(in_path), "-af", NOTCH,
             "-ac", "1", "-c:a", "pcm_s16le", notched])
        code = f'''
import torch, soundfile as sf
from pathlib import Path
from resemble_enhance.enhancer.inference import denoise
data, sr = sf.read(r"{notched}", dtype="float32")
out, sr_out = denoise(torch.from_numpy(data), sr, "cpu", run_dir=Path(r"{rd}"))
sf.write(r"{denoised}", out.cpu().numpy(), sr_out)
'''
        _sh([str(conda_python()), "-c", code])
        last = ["ffmpeg", "-y", "-v", "error", "-i", denoised, "-ar", "48000"]
        if stereo:
            last += ["-ac", "2"]
        last += ["-c:a", "pcm_s16le", str(out_path)]
        _sh(last)


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("用法: denoise_voice.py <in.wav> <out.wav>", file=sys.stderr)
        return 2
    IN, OUT = args
    miss = missing_env()
    if miss:
        print("✗ denoise 环境缺失：", file=sys.stderr)
        for m in miss:
            print(f"    - {m}", file=sys.stderr)
        return 1
    chain(IN, OUT)
    print(f"OK -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
