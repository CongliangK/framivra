"""ASR-owned fake IME assets and isolated child environment; no live resources."""
import os
from pathlib import Path


def ime_home(directory):
    home = Path(directory) / "home"
    home.mkdir()
    ime = home / "文档/01_活跃项目/type/ibus-voice-ime-github"
    model = ime / "vendor/models/qwen3-asr/Qwen3-ASR-1.7B"
    model.mkdir(parents=True)
    (model / "model.safetensors").write_bytes(b"FAKE-IME-WEIGHT")
    (model / "config.json").write_text("{}", encoding="utf-8")
    env = dict(os.environ)
    env["HOME"] = str(home)
    for name in ("VT_ASR_IME_ROOT", "VTX_CONFIG", "PYTHONPATH", "PYTHONHOME",
                 "PYTHONSTARTUP", "PYTHONUSERBASE", "PYTHONNOUSERSITE"):
        env.pop(name, None)
    return home, ime, model, env
