#!/usr/bin/env python3
"""测试：自动启动 Resolve → 健壮建工程 → 导入素材 → 建时间线 → 探测 Fusion keyer 工具。"""
import sys

sys.path.insert(0, "/home/liangruochong/文档/01_活跃项目/video_tools/matting/resolve_matte")
from resolve_ctl import get_resolve, ensure_project

PROJ = "resolve_matte_dev"
SRC = "/home/liangruochong/文档/04_bilibili视频/voice-ime/cache/bg_gen/centered9/视频一.mp4"

resolve = get_resolve()
pm = resolve.GetProjectManager()
proj = ensure_project(pm, PROJ)
print("project:", proj.GetName())

# 与素材对齐的工程设置
proj.SetSetting("timelineFrameRate", "29.97")
proj.SetSetting("timelineResolutionWidth", "2048")
proj.SetSetting("timelineResolutionHeight", "1152")

mp = proj.GetMediaPool()
clips = mp.ImportMedia([SRC])   # 注意：必须传列表
print("imported:", clips)
if not clips:
    # 诊断：根目录媒体池对象
    print("root folder:", mp.GetRootFolder())
    sys.exit("ImportMedia 失败")

tl = proj.GetCurrentTimeline()
if tl is None or tl.GetName() != "resolve_matte_test":
    tl = mp.CreateEmptyTimeline("resolve_matte_test")
print("timeline:", tl.GetName() if tl else None)
if not tl:
    sys.exit("建空时间线失败")
appended = mp.AppendToTimeline(clips)
print("appended:", appended)

item = tl.GetItemListInTrack("video", 1)[0]
print("item:", item.GetName(), item.GetStart(), item.GetEnd())

comp = item.AddFusionComp()
for tid in ("MagicMask", "DeltaKeyer", "UltraKeyer", "ChromaKeyer", "LumaKeyer"):
    try:
        t = comp.AddTool(tid)
        print(f"AddTool({tid}):", "OK" if t else "None")
    except Exception as e:
        print(f"AddTool({tid}) err:", repr(e))
