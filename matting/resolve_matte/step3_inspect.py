#!/usr/bin/env python3
"""检查 MagicMask 工具的输入/输出/参数。"""
import sys

sys.path.insert(0, "/home/liangruochong/文档/01_活跃项目/video_tools/matting/resolve_matte")

from resolve_ctl import get_resolve

resolve = get_resolve()
proj = resolve.GetProjectManager().GetCurrentProject()
tl = proj.GetCurrentTimeline()
item = tl.GetItemListInTrack("video", 1)[0]
comp = item.GetFusionCompCount() and item.GetFusionCompByIndex(1) or item.AddFusionComp()

mm = comp.AddTool("MagicMask")
print("== MagicMask input list ==")
inp = mm.GetInputList()
for k, v in sorted(inp.items()):
    try:
        print(k, v.GetAttrs().get("INPS_Name"), "|", v.GetAttrs().get("INPS_DataType"))
    except Exception as e:
        print(k, "err", e)

print("== outputs ==")
outl = mm.GetOutputList()
for k, v in sorted(outl.items()):
    try:
        print(k, v.GetAttrs())
    except Exception as e:
        print(k, "err", e)
