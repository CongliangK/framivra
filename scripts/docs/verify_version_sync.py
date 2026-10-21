#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_version_sync.py — L6 三源版本一致性门(R-22 M2,席 D;B01 增 ④)。

三源:
  ① VERSION(仓根)——直接读取;
  ② pyproject.toml 的 `version = "..."` 行(PEP 621 project.version,行首零缩进);
  ③ 运行时值:`python3 -c "import vtx; print(vtx.__version__)"`(cwd=仓根;
     vtx/__init__.py 的 _read_version() 为动态派生,须取运行时值,不得静态读
     文件顶替)。
第四源(条件判,B01/F-07/#7):
  ④ importlib.metadata 的已安装发行包版本(framivra,名字取 pyproject
     [project].name 行):已安装 → 须与三源同值,不等 exit 1;未安装 → 打印
     skip 行,不判败(开发机常态不装;真正 venv+wheel 安装冒烟=C03 工单,
     版本源逻辑与布局冒烟=tests/distribution/test_version_sources.py)。
  ④ 的查询名双源同步判(B01 红队对抗补):③ 运行时同时打印 vtx._DIST_NAME
     (vtx/__init__.py 内置常量,wheel 布局的权威回退名),与 pyproject name 行
     不等 → exit 1——堵「篡改 name 行换 ④ 查询名 → 已装陈旧 dist 被静默 skip」
     的绕过;name 行缺失也 FAIL(缺名 pyproject 无法装包,fail-closed)。

判定:
  三值全等 → `L6 version sync: VERSION==pyproject==vtx == <v> OK`,exit 0;
  任一不等 → `L6 FAIL: VERSION=<a> pyproject=<b> vtx=<c>`,exit 1;
  vtx import 失败(非零退出/超时/输出空)→ `L6 FAIL: vtx import error <reason>`,exit 1;
  ④已安装但不等 → `L6 FAIL: importlib.metadata(<name>)=<x> expected <v>`,exit 1;
  查询名漂移/缺失 → `L6 FAIL: dist name ...`,exit 1。

仅标准库;不写任何文件。
"""

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PYPROJECT_VERSION_RE = re.compile(r'(?m)^version\s*=\s*"([^"]*)"\s*$')
PYPROJECT_NAME_RE = re.compile(r'(?m)^name\s*=\s*"([^"]*)"\s*$')


def _fail(msg):
    print(msg)
    sys.exit(1)


def _metadata_version(dist_name):
    """已安装发行包版本;未安装(或 metadata 不可用)返回 None——不判败。"""
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover —— Python>=3.8 恒有
        return None
    try:
        return version(dist_name)
    except PackageNotFoundError:
        return None


def main():
    # ① VERSION(仓根)
    try:
        with open(os.path.join(ROOT, "VERSION"), "rb") as f:
            version_file = f.read().decode("utf-8").strip()
    except (OSError, UnicodeDecodeError) as e:
        _fail("L6 FAIL: VERSION unreadable: %s" % e)
    if not version_file:
        _fail("L6 FAIL: VERSION empty")

    # ② pyproject.toml 的 version 行
    try:
        with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
            text = f.read().decode("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        _fail("L6 FAIL: pyproject.toml unreadable: %s" % e)
    m = PYPROJECT_VERSION_RE.search(text)
    if not m:
        _fail("L6 FAIL: pyproject.toml version line not found "
              '(expected ^version = "..." at column 0)')
    version_pyproject = m.group(1).strip()

    # ③ 运行时 vtx.__version__(cwd=仓根,动态派生取运行时值)。首行同时打印
    # vtx._DIST_NAME——④ 查询名的运行时锚(见模块头「查询名双源同步判」)
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONPYCACHEPREFIX", None)
    try:
        proc = subprocess.run(
            [sys.executable or "python3", "-c",
             "import vtx; print(vtx._DIST_NAME); print(vtx.__version__)"],
            cwd=ROOT, env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=60,
        )
    except OSError as e:
        _fail("L6 FAIL: vtx import error %s" % e)
    except subprocess.TimeoutExpired:
        _fail("L6 FAIL: vtx import error timeout after 60s")
    if proc.returncode != 0:
        err_lines = [ln for ln in proc.stderr.decode("utf-8", "replace").splitlines() if ln.strip()]
        reason = err_lines[-1] if err_lines else "exit %d" % proc.returncode
        _fail("L6 FAIL: vtx import error %s" % reason)
    out_lines = [ln for ln in proc.stdout.decode("utf-8", "replace").splitlines() if ln.strip()]
    if not out_lines:
        _fail("L6 FAIL: vtx import error empty output")
    dist_name_runtime = out_lines[0].strip()
    version_vtx = out_lines[-1].strip()
    if len(out_lines) < 2:
        # 单行输出无法区分名与版本：按名源漂移处理（fail-closed，不静默放行）
        _fail("L6 FAIL: vtx import printed %d line(s), expected _DIST_NAME + "
              "__version__ (got %r)" % (len(out_lines), out_lines[0]))

    # 三值全等判定
    if not (version_file == version_pyproject == version_vtx):
        _fail("L6 FAIL: VERSION=%s pyproject=%s vtx=%s"
              % (version_file, version_pyproject, version_vtx))
    version_ok = version_file
    print("L6 version sync: VERSION==pyproject==vtx == %s OK" % version_ok)

    # ④ importlib.metadata(已安装时;B01/F-07/#7)——发行包名取 pyproject name 行,
    # 且须与运行时 vtx._DIST_NAME 同值(查询名双源同步判,堵 name 行篡改绕过)
    n = PYPROJECT_NAME_RE.search(text)
    if not n or not n.group(1).strip():
        _fail("L6 FAIL: pyproject.toml name line not found "
              '(expected ^name = "..." at column 0; ④ 查询名失去 pyproject 侧锚)')
    dist_name = n.group(1).strip()
    if dist_name != dist_name_runtime:
        _fail("L6 FAIL: dist name diverges: pyproject=%s vtx._DIST_NAME=%s"
              "（④ 查询名漂移，已装陈旧发行包会被静默 skip；改名须两处同步）"
              % (dist_name, dist_name_runtime))
    version_meta = _metadata_version(dist_name)
    if version_meta is None:
        print("L6 version sync: importlib.metadata(%s) not installed — skipped "
              "(wheel-install smoke deferred to C03; layout smoke=tests/distribution/test_version_sources.py)"
              % dist_name)
        sys.exit(0)
    if version_meta != version_ok:
        _fail("L6 FAIL: importlib.metadata(%s)=%s expected %s (installed dist out of sync)"
              % (dist_name, version_meta, version_ok))
    print("L6 version sync: importlib.metadata(%s) == %s OK (installed)" % (dist_name, version_meta))
    sys.exit(0)


if __name__ == "__main__":
    main()
