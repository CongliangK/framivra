#!/usr/bin/env python3
"""bgm-render 插件 health 探针（vt doctor 经 manifest health.command 子进程调用）。

探测：bgm CLI 可解析（config bgm.cli > ~/.local/bin/bgm > bgm.repo + python3 -m
bgm_gen，与插件 _bgm_cmd 同一解析序）+ ``--help`` 子命令可达。口径区分（denoise
同款）：health 允许报 unhealthy 不崩（安装期探测）；渲染链的 fail-hard 在
probe_source/prepare_source（bgm 不可用即阶段失败退出）。
"""
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # vtx.api 导入锚（探针脚本模式）
from vtx.api import cfg_get                                      # noqa: E402


def main() -> int:
    cli = cfg_get("bgm.cli", "")
    if cli:
        cmd = shlex.split(os.path.expanduser(cli))
        via = f"config bgm.cli"
    else:
        wrapper = Path.home() / ".local" / "bin" / "bgm"
        if wrapper.is_file():
            cmd = [str(wrapper)]
            via = "~/.local/bin/bgm"
        else:
            repo = Path(os.path.expanduser(cfg_get("bgm.repo",
                                                   "~/文档/01_活跃项目/music_tools")))
            py = shutil.which("python3")
            if repo.is_dir() and py:
                cmd = [py, "-m", "bgm_gen"]
                via = f"python3 -m bgm_gen（PYTHONPATH={repo}）"
            else:
                print(f"[MISS] bgm CLI 不可解析：未配置 bgm.cli、~/.local/bin/bgm 不在、"
                      f"bgm.repo（{repo}）不可用——见 config.example.json bgm 节")
                return 1
    try:
        r = subprocess.run(cmd + ["--help"], capture_output=True, text=True,
                           timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"[MISS] bgm CLI（{via}）不可执行：{e}")
        return 1
    print(f"[{'OK' if r.returncode == 0 else 'MISS'}] bgm CLI（经 {via}）"
          + ("" if r.returncode == 0 else f"——--help 退出码 {r.returncode}"))
    return 0 if r.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
