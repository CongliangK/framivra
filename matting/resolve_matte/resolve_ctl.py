#!/usr/bin/env python3
"""Resolve 控制基础层：连接管理（含自动启动）+ 健壮的项目创建/加载。

用法（其他脚本）:
    from resolve_ctl import get_resolve, ensure_project
    resolve = get_resolve()
    proj = ensure_project("my_proj")
"""
import os
import subprocess
import sys
import time

RESOLVE_BIN = "/opt/resolve/bin/resolve"
SCRIPT_MODULES = "/opt/resolve/Developer/Scripting/Modules"
CONNECT_TIMEOUT = 180  # 首次启动冷加载可能较慢


def _connect():
    sys.path.insert(0, SCRIPT_MODULES)
    import DaVinciResolveScript as dvr
    return dvr.scriptapp("Resolve")


def is_running():
    out = subprocess.run(["pgrep", "-x", "resolve"], capture_output=True, text=True)
    return out.returncode == 0


def get_resolve(auto_launch=True, verbose=True):
    """连接 Resolve；未运行则自动启动并等待 API 就绪。"""
    r = _connect()
    if r is not None:
        return r
    if not auto_launch:
        return None
    if verbose:
        print("[resolve_ctl] Resolve 未运行，正在启动 ...")
    if not is_running():
        env = dict(os.environ)
        subprocess.Popen(
            [RESOLVE_BIN],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    deadline = time.time() + CONNECT_TIMEOUT
    while time.time() < deadline:
        r = _connect()
        if r is not None:
            if verbose:
                print(f"[resolve_ctl] 已连接，版本 {r.GetVersionString()}")
            return r
        time.sleep(3)
    raise RuntimeError("等待 Resolve API 超时（%ds）。检查：是否卡在首启向导/授权弹窗" % CONNECT_TIMEOUT)


INVALID_NAME_CHARS = set('/\\:*?"<>|')


def ensure_project(pm, name, verbose=True):
    """健壮地拿到一个可用工程：优先复用已打开工程 → 创建 → 名字冲突则加载 →
    都失败则关闭当前工程再创建。全程打印诊断信息。"""
    def log(msg):
        if verbose:
            print(f"[ensure_project] {msg}")

    for ch in INVALID_NAME_CHARS:
        if ch in name:
            raise ValueError(f"项目名含非法字符 {ch!r}: {name!r}")

    cur = pm.GetCurrentProject()
    if cur is not None:
        if cur.GetName() == name:
            log(f"复用当前已打开工程 {name!r}")
            return cur
        log(f"当前工程为 {cur.GetName()!r}，尝试切换 ...")

    # 1) 直接创建
    p = pm.CreateProject(name)
    if p is not None:
        log(f"CreateProject 成功: {name!r}")
        return p

    # 2) 诊断：是否重名 → 直接打开
    try:
        names = pm.GetProjectListInCurrentFolder("Retrieve all projects") or []
    except Exception:
        names = pm.GetProjectListInCurrentFolder() or []
    log(f"当前数据库工程列表: {names}")
    if name in names:
        p = pm.LoadProject(name)
        if p is not None:
            log(f"打开已有工程: {name!r}")
            return p
        log(f"LoadProject({name!r}) 返回 None")

    # 3) 关闭当前工程后再创建（Resolve 常见坑：有工程打开时 CreateProject 静默失败）
    if cur is not None:
        log("尝试关闭当前工程后重试 CreateProject ...")
        ok = pm.CloseProject(cur)
        log(f"CloseProject -> {ok}")
        p = pm.CreateProject(name)
        if p is not None:
            log(f"CloseProject 后 CreateProject 成功: {name!r}")
            return p

    # 4) 最后诊断：页面对象、当前文件夹状态
    log("CreateProject 仍失败。诊断：")
    log(f"  currentFolder = {pm.GetCurrentFolder()}")
    raise RuntimeError(f"无法创建/打开工程 {name!r}；可能原因：工程管理器有模态弹窗、数据库异常、名字冲突且打开失败")
