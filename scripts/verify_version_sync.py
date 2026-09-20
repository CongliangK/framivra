#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_version_sync.py — L6 三源版本一致性门(R-22 M2,席 D)。

三源:
  ① VERSION(仓根)——直接读取;
  ② pyproject.toml 的 `version = "..."` 行(PEP 621 project.version,行首零缩进);
  ③ 运行时值:`python3 -c "import vtx; print(vtx.__version__)"`(cwd=仓根;
     vtx/__init__.py 的 _read_version() 为动态派生,须取运行时值,不得静态读
     文件顶替)。

判定:
  三值全等 → `L6 version sync: VERSION==pyproject==vtx == <v> OK`,exit 0;
  任一不等 → `L6 FAIL: VERSION=<a> pyproject=<b> vtx=<c>`,exit 1;
  vtx import 失败(非零退出/超时/输出空)→ `L6 FAIL: vtx import error <reason>`,exit 1。

仅标准库;不写任何文件。
"""

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PYPROJECT_VERSION_RE = re.compile(r'(?m)^version\s*=\s*"([^"]*)"\s*$')


def _fail(msg):
    print(msg)
    sys.exit(1)


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

    # ③ 运行时 vtx.__version__(cwd=仓根,动态派生取运行时值)
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONPYCACHEPREFIX", None)
    try:
        proc = subprocess.run(
            [sys.executable or "python3", "-c", "import vtx; print(vtx.__version__)"],
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
    version_vtx = out_lines[-1].strip()

    # 三值全等判定
    if version_file == version_pyproject == version_vtx:
        print("L6 version sync: VERSION==pyproject==vtx == %s OK" % version_file)
        sys.exit(0)
    _fail("L6 FAIL: VERSION=%s pyproject=%s vtx=%s"
          % (version_file, version_pyproject, version_vtx))


if __name__ == "__main__":
    main()
