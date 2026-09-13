#!/usr/bin/env python3
"""验证 DRX 链：抓取画廊静帧 → 导出 DRX。"""
import os
import sys

sys.path.insert(0, "/home/liangruochong/文档/01_活跃项目/video_tools/matting/resolve_matte")
from resolve_ctl import get_resolve, ensure_project

OUT_DIR = "/home/liangruochong/文档/01_活跃项目/video_tools/matting/resolve_matte/out"

r = get_resolve()
pm = r.GetProjectManager()
p = ensure_project(pm, "resolve_matte_dev")
tl = p.GetCurrentTimeline()
print("timeline:", tl.GetName())
item = tl.GetItemListInTrack("video", 1)[0]
print("clip:", item.GetName())
# 确保调色页是当前页（抓帧依赖调色页当前片段）
r.OpenPage("color")
import time
time.sleep(2)

gallery = p.GetGallery()
album = gallery.GetCurrentStillAlbum()
print("album:", album.GetName())
st = gallery.GrabStill()
print("grabbed:", st)
if st:
    os.makedirs(OUT_DIR, exist_ok=True)
    ok = album.ExportStills([st], OUT_DIR, "matte_template", "drx")
    print("export drx:", ok)
    print("files:", [f for f in os.listdir(OUT_DIR) if f.endswith(".drx")])
