#!/usr/bin/env python3
"""口播标准降噪链 v1（2026-08-26 定案）
陷波(ffmpeg) → Resemble 仅降噪级 → 回 48kHz。
用法: denoise_voice.py <in.wav> <out.wav>
依赖: ~/miniconda3/envs/denoise/bin/python + ~/models/resemble-enhance/
"""
import subprocess, sys, tempfile, os
from pathlib import Path

IN, OUT = sys.argv[1], sys.argv[2]
RD = Path.home() / "models/resemble-enhance/enhancer_stage2"
NOTCH = "highpass=f=80,anequalizer=c0 f=50 w=6 g=-30 t=1|c0 f=100 w=6 g=-20 t=1|c0 f=150 w=6 g=-15 t=1"

with tempfile.TemporaryDirectory() as td:
    notched = os.path.join(td, "notched.wav")
    denoised = os.path.join(td, "denoised.wav")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", IN, "-af", NOTCH,
                    "-ac", "1", "-c:a", "pcm_s16le", notched], check=True)
    code = f'''
import torch, soundfile as sf
from pathlib import Path
from resemble_enhance.enhancer.inference import denoise
data, sr = sf.read(r"{notched}", dtype="float32")
out, sr_out = denoise(torch.from_numpy(data), sr, "cpu", run_dir=Path(r"{RD}"))
sf.write(r"{denoised}", out.cpu().numpy(), sr_out)
'''
    subprocess.run([os.path.expanduser("~/miniconda3/envs/denoise/bin/python"), "-c", code], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", denoised, "-ar", "48000",
                    "-c:a", "pcm_s16le", OUT], check=True)
print(f"OK -> {OUT}")
