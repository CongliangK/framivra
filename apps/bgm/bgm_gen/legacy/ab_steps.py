#!/usr/bin/env python3
"""步数-质量 A/B 实验：同 seed 同 prompt，turbo DiT 不同步数，输出无损 WAV。
风格用"干净制作"（无 vinyl crackle），把模型伪影和风格噪声分开。"""
import json, copy, os, subprocess, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
_GEN = os.path.join(_HERE, "gen.py")

base = json.load(open(os.path.join(_REPO, "workflows", "wf_ace15.json")))
save_id = next(k for k in base if base[k]["class_type"] == "SaveAudioMP3")

CLEAN = ("clean modern downtempo electronic, warm analog synth pads, smooth, pristine studio production, "
         "high fidelity, gentle rhythmic pulse, spacious, calm, instrumental, no vocals, "
         "no vinyl crackle, no tape hiss, no lo-fi texture")

runs = [
    ("clean8",  8,  1234),
    ("clean16", 16, 1234),
    ("clean32", 32, 1234),
    ("lofi32",  32, 20260821),  # 原 lo-fi 风格 + 32 步
]
for name, steps, seed in runs:
    wf = copy.deepcopy(base)
    w = wf["94"]["inputs"]
    if name.startswith("clean"):
        w["tags"] = CLEAN
    w["seed"] = seed
    wf["3"]["inputs"] = {**wf["3"]["inputs"], "seed": seed, "steps": steps}
    # 输出无损 flac
    wf[save_id] = {"class_type": "SaveAudioAdvanced",
                   "inputs": {"audio": ["18", 0], "filename_prefix": f"audio/ab_{name}", "format": "flac"}}
    json.dump(wf, open(f"wf_ab_{name}.json", "w"))
    r = subprocess.run(["python3", _GEN, f"wf_ab_{name}.json"], capture_output=True, text=True)
    last = (r.stdout.strip().splitlines() or ["ERR " + r.stderr[-150:]])[-1]
    print(f"{name}(steps={steps}): {last}", flush=True)
