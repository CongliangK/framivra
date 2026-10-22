#!/usr/bin/env python3
"""bgm-render 插件 health 探针（vt doctor 经 manifest health.command 子进程调用）。

探测：bgm CLI 可解析（config bgm.cli > ~/.local/bin/bgm > bgm.repo + python3 -m
bgm_gen，与插件共用 bgm_cmd 解析序）+ ``--help`` 子命令可达。口径区分（denoise
同款）：health 允许报 unhealthy 不崩（安装期探测）；渲染链的 fail-hard 在
probe_source/prepare_source（bgm 不可用即阶段失败退出）。
"""
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

_DEFAULT_REPO = "~/文档/01_活跃项目/music_tools"


def bgm_cmd(cfg_get):
    """bgm CLI 解析 → (argv 前缀, 额外 env)。三通道：config bgm.cli >
    ~/.local/bin/bgm > python3 -m bgm_gen（PYTHONPATH=config bgm.repo）。
    v1.2.0（R1-F F4）：通道解析目标不在盘/不可执行 = 该通道不可用，落下一通道
    （config bgm.cli 指向不存在路径原会在 _run_bgm 里 FileNotFoundError 裸栈崩）；
    全败返回 (None, 逐通道缺什么说明——含 config 通道的具体坏路径)。"""
    fails = []
    cli = cfg_get("bgm.cli", "")
    if cli:
        expanded = os.path.expanduser(cli)
        if os.name == 'nt':
            # Apply Windows argv quoting, preserving backslashes in paths.
            import ctypes
            from ctypes import wintypes
            shell = ctypes.WinDLL('shell32', use_last_error=True)
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
            shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
            kernel.LocalFree.argtypes = [ctypes.c_void_p]
            count = ctypes.c_int()
            pointer = shell.CommandLineToArgvW(expanded, ctypes.byref(count))
            if not pointer:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                argv = [pointer[i] for i in range(count.value)]
            finally:
                kernel.LocalFree(pointer)
        else:
            argv = shlex.split(expanded)
        # 存在性/可执行门：shutil.which 对含分隔符的路径按字面探测、裸命令名查
        # PATH，且只认可执行件——非执行文件过了这道门也会在 subprocess 处崩。
        if argv and shutil.which(argv[0]):
            return argv, None
        fails.append(f"config bgm.cli 指向不存在/不可执行路径（{cli}）")
    wrapper = Path.home() / ".local" / "bin" / "bgm"
    if wrapper.is_file():
        return [str(wrapper)], None
    repo = Path(os.path.expanduser(cfg_get("bgm.repo", _DEFAULT_REPO)))
    py = sys.executable if os.name == "nt" else shutil.which("python3")
    if repo.is_dir() and py:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(repo) + (os.pathsep + env["PYTHONPATH"]
                                         if env.get("PYTHONPATH") else "")
        return [py, "-m", "bgm_gen"], env
    fails.append(f"~/.local/bin/bgm 不在（{wrapper}）")
    fails.append(f"bgm.repo（{repo}）不可用或缺 python3")
    return None, ("bgm CLI 不可解析：" + "；".join(fails)
                  + "——config 配置见 config.example.json bgm 节")


def main() -> int:
    from vtx.pluginlib import config  # noqa: E402

    cmd, env = bgm_cmd(lambda key, default: config.cfg_get(config.load_config(), key, default))
    if cmd is None:
        print(f"[MISS] {env}")
        return 1
    via = cmd[0]
    try:
        r = subprocess.run(cmd + ["--help"], capture_output=True,
                           timeout=15, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"[MISS] bgm CLI（{via}）不可执行：{e}")
        return 1
    print(f"[{'OK' if r.returncode == 0 else 'MISS'}] bgm CLI（经 {via}）"
          + ("" if r.returncode == 0 else f"——--help 退出码 {r.returncode}"))
    return 0 if r.returncode == 0 else 1


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # config 导入锚（探针脚本模式）
    sys.exit(main())
