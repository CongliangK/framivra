#!/usr/bin/env python3
"""口播标准降噪链 v2（R-20 P2 攻坚定案；v1 2026-08-26 → v2 2026-09-20）

mono s16 规范化(ffmpeg) → Resemble 仅降噪级 → afftdn 谱降噪 + 回 48kHz（链内延迟补偿）。
用法: denoise_voice.py <in.wav> <out.wav>
依赖: ~/miniconda3/envs/denoise/bin/python + ~/models/resemble-enhance/
      （DENOISE_PY / RESEMBLE_WEIGHTS 环境变量可覆盖，与 denoise_init.sh 同名同义）

本模块零 vtx 依赖（独立 CLI 自治）；plugins/denoise 包的 prepare_source 钩子
复用本模块的 chain()（同一实现，缝 A 产物与手动 CLI 永不分叉）。stereo 参数：
CLI 保持 mono 输出，插件产物传 True（对齐内核裸提取的 48k stereo 契约，防混音
graph 通道协商分叉）。

## v2 升级依据（R-20 P1 基线实证 → P2 攻坚；测量区 _denoise_bench/ 可复现）

v1 对 white/pink 式宽频噪声近乎无效（P1 基线 PESQ 分族 white 1.27/pink 1.40；
diag 实测 v1 输出白噪帧仅压 ~1dB）。P2 探针两轮（r1/r2，测量区 tools/）定位
两处根因并逐一收口（§二.2 自由度：陷波参数族/预降噪滤波器/后处理/链序重排）：
  1. **afftdn 谱降噪入链**（nr=12:nf=-25，48k 域）——对稳态白/粉噪实测压噪帧
     12~17dB（v1 ≈1dB）；hum 族由 Resemble+afftdn 实测压至 19~30dB segSNR；
  2. **链内确定性延迟补偿**——afftdn 有 25.000ms 算法延迟（时不变），冻结测量
     器只做整数对齐，分数残差会以假性误差吞没降噪增益；终步 atrim 按常量
     TRIM_48K 精确扣除（实测落测量域 lag=0、无漂移嫌疑）；
  3. **陷波级弃用**（v1 NOTCH 串移除）——highpass/anequalizer 系 IIR，其频率
     相关群延迟 = 分数相位失真：对照实验（同一链 有/无 NOTCH）实测无 NOTCH
     侧 segSNR +9.5dB 且漂移嫌疑清零（有 NOTCH 侧 lag=-1、T 族全段嫌疑）；
     NOTCH 的 50-150Hz 去嗡职能由 Resemble+afftdn 承接（合成 hum 族实测在档）。
     陷波参数本属 §二.2 攻坚自由度，弃选理由随 P2 报告录台账；
  4. **aresample 显式化**——终步重采样并入 -af（swr，与 v1 -ar 同引擎）。
三步子进程形态与 v1 逐同（ffmpeg → conda Resemble → ffmpeg），CLI 命令面、
fail-hard、stereo 契约零变化；首步保留为 mono s16 规范化步（推理输入整形）。
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# v2 终步滤波串：afftdn 谱降噪（nr=12dB 减噪量/nf=-25dB 噪声地板，实测对
# 10dB 注入稳态噪最优档；P2 探针 r1/r2 扫描在案）。POST 在 48k 域执行。
# v1 的 NOTCH 陷波串（highpass=f=80+anequalizer 三点）自 v2 弃用——弃选理由
# 见模块头注 §3（IIR 群延迟=分数相位失真，实测 segSNR -9.5dB+漂移嫌疑）；
# 去嗡职能由 Resemble+afftdn 承接。
POST = "afftdn=nr=12:nf=-25"

# afftdn 算法延迟 = 25.000 ms（时不变，与输入采样率无关；@16k 域实测 400 样本，
# P2 探针 r1）。终步 48k 域补偿：25 ms × 48 kHz = 1200 样本（P2 探针 r2 实测
# 全部落测量域 lag=0 且无漂移嫌疑——补偿值与实测延迟吻合的机器证据）。
TRIM_48K = 1200


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
                    "（已有环境探测：bash plugins/denoise/denoise_init.sh）")
    if not (run_dir() / "ds/G/default/mp_rank_00_model_states.pt").is_file():
        miss.append(f"Resemble 权重 {run_dir()}"
                    "（旧 --download 为 BLOCKED；许可见 docs/third-party-licenses.md；离线证据：python3 scripts/policy/model_license_policy.py plan resemble-enhance，不下载、不授予许可）")
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
    """三步链（v2：mono s16 规范化 → conda Resemble 仅降噪 CPU → afftdn+回
    48kHz，终步含 TRIM_48K 延迟补偿）。stereo=True 终步加 -ac 2（内核缝 A
    产物用）。中间产物走 TemporaryDirectory（mono s16 数十 MB 级，与 v1 同款）。
    子进程数与 v1 逐同（3），命令面不变；fail-hard 不产半链产物。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rd = run_dir()
    with tempfile.TemporaryDirectory(prefix="denoise-") as td:
        normed = str(Path(td) / "normed.wav")
        denoised = str(Path(td) / "denoised.wav")
        _sh(["ffmpeg", "-y", "-v", "error", "-i", str(in_path),
             "-ac", "1", "-c:a", "pcm_s16le", normed])
        code = '''
import sys, torch, soundfile as sf
from pathlib import Path
from resemble_enhance.enhancer.inference import denoise
normed, run_directory, denoised = sys.argv[1:]
data, sr = sf.read(normed, dtype="float32")
out, sr_out = denoise(torch.from_numpy(data), sr, "cpu", run_dir=Path(run_directory))
sf.write(denoised, out.cpu().numpy(), sr_out)
'''
        _sh([str(conda_python()), "-c", code, normed, str(rd), denoised])
        last = ["ffmpeg", "-y", "-v", "error", "-i", denoised,
                "-af", f"{POST},aresample=48000,"
                       f"atrim=start_sample={TRIM_48K},asetpts=PTS-STARTPTS"]
        if stereo:
            last += ["-ac", "2"]
        last += ["-c:a", "pcm_s16le", str(out_path)]
        _sh(last)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="denoise_voice.py",
        description="口播标准降噪链 v2：Resemble 仅降噪(conda,CPU) → afftdn 谱降噪"
                    "+回 48kHz(mono,链内延迟补偿)。环境缺失 fail-hard 不产半链产物。")
    parser.add_argument("in_wav", metavar="in.wav", help="输入 wav")
    parser.add_argument("out_wav", metavar="out.wav", help="输出 wav（48kHz mono）")
    args = parser.parse_args(argv)
    miss = missing_env()
    if miss:
        print("✗ denoise 环境缺失：", file=sys.stderr)
        for m in miss:
            print(f"    - {m}", file=sys.stderr)
        return 1
    chain(args.in_wav, args.out_wav)
    print(f"OK -> {args.out_wav}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
