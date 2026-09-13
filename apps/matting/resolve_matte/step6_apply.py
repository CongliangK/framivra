#!/usr/bin/env python3
"""在新素材上套用 DRX + CreateMagicMask 重算蒙版。"""
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from resolve_ctl import get_resolve, ensure_project

# 素材：env MATTE_SRC 覆盖 > ~ 展开默认（原硬编码平移）；DRX 随迁移 HERE 相对
SRC = (os.environ.get("MATTE_SRC")
       or os.path.expanduser("~/文档/04_bilibili视频/voice-ime/cache/bg_gen/centered9/视频二.mp4"))
DRX = os.environ.get("MATTE_DRX") or str(HERE / "out" / "matte_template_1.1.1.drx")

r = get_resolve()
pm = r.GetProjectManager()
p = ensure_project(pm, "resolve_matte_dev")
mp = p.GetMediaPool()

tl = mp.CreateEmptyTimeline("matte_drx_test") or p.GetCurrentTimeline()
print("timeline:", tl.GetName())
items = tl.GetItemListInTrack("video", 1) or []
if not items:
    mpi = (mp.ImportMedia([SRC]) or [None])[0]
    mp.AppendToTimeline([{"mediaPoolItem": mpi, "startFrame": 108000, "endFrame": 108100}])
item = tl.GetItemListInTrack("video", 1)[0]
print("clip:", item.GetName(), item.GetStart(), "->", item.GetEnd())

graph = item.GetNodeGraph()
print("nodes before:", graph.GetNumNodes())
ok = graph.ApplyGradeFromDRX(DRX, 0)
print("apply drx:", ok, "| nodes:", graph.GetNumNodes())
print("tools in node1:", graph.GetToolsInNode(1))

# 切到调色页并把播放头放进片段，再触发 Magic Mask 重算
r.OpenPage("color")
time.sleep(2)
p.SetCurrentTimeline(tl)
tl.SetCurrentTimecode("01:00:00:10")
mm_ok = item.CreateMagicMask("BI")
print("CreateMagicMask:", mm_ok)
