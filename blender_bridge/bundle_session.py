#!/usr/bin/env python3
"""bundle_session.py — 单次 Blender 会话内按序执行 json2blend + verify_blend

audit_bundle.py 步骤④⑤合并（2026-09-11 提速）：原先两次 blender 冷启动各 15-25s，
现在一个会话内 runpy.run_path 依次以 __main__ 执行两脚本（不复制任何逻辑，参数仍走
各自脚本的 sys.argv 契约）。verify_blend 自身用 bpy.ops.wm.open_mainfile 打开产物，
与 json2blend 共会话无状态污染；会话加 --factory-startup 对 verify 无影响
（verify 显式 open_mainfile，不读用户 startup/preferences）。

用法（在 Blender 内被 --python 调用）：
  blender --background --factory-startup --python bundle_session.py -- \
      <timeline.json> <out.blend> <json2blend.py 路径> <verify_blend.py 路径>

退出码：json2blend 失败即停止（verify 依赖其产物）；任一步失败退出码 1。
输出标记 [bundle/step4] [bundle/step5]，供 audit_bundle 解析/转发。
"""
import runpy
import sys
import traceback


def _run_step(py_path, label):
    print(f"[bundle/{label}] begin: {py_path}", flush=True)
    try:
        runpy.run_path(py_path, run_name="__main__")
        print(f"[bundle/{label}] ok", flush=True)
        return 0
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        print(f"[bundle/{label}] exit={code}", flush=True)
        return code
    except Exception:
        traceback.print_exc()
        print(f"[bundle/{label}] exit=1（异常，见上方 traceback）", flush=True)
        return 1


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    tl_path, blend_path, j2b, verify = argv[0], argv[1], argv[2], argv[3]

    rc = _run_step(j2b, "step4")
    if rc != 0:
        print(f"[bundle] json2blend 失败（exit={rc}），跳过 verify", flush=True)
        sys.exit(rc)
    rc = _run_step(verify, "step5")
    sys.exit(rc)


main()
