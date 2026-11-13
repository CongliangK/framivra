#!/usr/bin/env python3
"""Resolve the repository-owned music CLI; never discover an external music repo."""
import os
import shutil
import subprocess
import sys
from pathlib import Path


def bgm_cmd(cfg_get):
    """Return (argv, env), or (None, reason) for a missing built-in dependency.

    bgm.python selects an interpreter, not a replacement renderer. Deprecated
    bgm.cli/bgm.repo settings deliberately cannot redirect this built-in app.
    bgm.runtime is relative to the active VTX_CONFIG file when not absolute.
    The app itself also accepts MUSIC_RUNTIME for direct CLI use.
    """
    app = Path(__file__).resolve().parents[2] / "apps" / "bgm" / "bgm.py"
    if not app.is_file():
        return None, f"内置音乐组件缺失：{app}（需要完整 Framivra 源码安装）"
    configured = cfg_get("bgm.python", "")
    python = os.path.expandvars(os.path.expanduser(configured)) if configured else (
        sys.executable if os.name == "nt" else "python3")
    resolved = shutil.which(python)
    if not resolved:
        return None, f"音乐解释器不可执行：{python}（配置 bgm.python）"
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    runtime = cfg_get("bgm.runtime", "")
    if runtime:
        from vtx.pluginlib.config import config_path
        path = Path(os.path.expandvars(os.path.expanduser(runtime)))
        if not path.is_absolute():
            path = config_path().resolve().parent / path
        env["MUSIC_RUNTIME"] = str(path.resolve())
    return [resolved, str(app)], env


def main() -> int:
    from vtx.pluginlib import config

    cfg = config.load_config()
    cmd, env = bgm_cmd(lambda key, default: config.cfg_get(cfg, key, default))
    if cmd is None:
        print(f"[MISS] {env}")
        return 1
    try:
        r = subprocess.run(cmd + ["--help"], capture_output=True, timeout=15, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"[MISS] 内置音乐 CLI 不可执行：{e}")
        return 1
    print(f"[{'OK' if r.returncode == 0 else 'MISS'}] 内置音乐 CLI（{cmd[1]}）"
          + ("" if r.returncode == 0 else f"——--help 退出码 {r.returncode}"))
    return 0 if r.returncode == 0 else 1


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    sys.exit(main())
