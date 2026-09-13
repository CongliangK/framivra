#!/usr/bin/env python3
"""连通性探针：连接 Resolve、打印版本/授权/工程，并探测 Fusion MagicMask 工具是否存在。"""
import sys

sys.path.insert(0, "/opt/resolve/Developer/Scripting/Modules")
import DaVinciResolveScript as dvr

resolve = dvr.scriptapp("Resolve")
if resolve is None:
    print("FAIL: cannot connect to Resolve (is it running? Preferences>System>General>external scripting?)")
    sys.exit(1)

print("resolve version:", resolve.GetVersionString())
print("page:", resolve.GetCurrentPage())
pm = resolve.GetProjectManager()
proj = pm.GetCurrentProject()
print("project:", proj.GetName() if proj else None)
if proj:
    print("timeline:", proj.GetCurrentTimeline().GetName() if proj.GetCurrentTimeline() else None)
    tl = proj.GetCurrentTimeline()
    if tl:
        items = tl.GetItemListInTrack("video", 1) or []
        print("video track1 items:", len(items))
        for it in items[:3]:
            print("  item:", it.GetName(), it.GetStart(), it.GetEnd())
            comps = it.GetFusionCompCount()
            print("  fusion comps:", comps)
            if comps == 0:
                comp = it.AddFusionComp()
            else:
                comp = it.GetFusionCompByIndex(1)
            # 探测 Fusion 工具注册表里有没有 MagicMask / 关键 keyer
            try:
                tool_ids = comp.GetToolList()
                print("  existing tools:", list(tool_ids.values()) if tool_ids else None)
            except Exception as e:
                print("  toollist err:", e)
            for tid in ("MagicMask", "DeltaKeyer", "UltraKeyer", "ChromaKeyer", "LumaKeyer"):
                try:
                    t = comp.AddTool(tid)
                    print(f"  AddTool({tid}):", "OK" if t else "None")
                except Exception as e:
                    print(f"  AddTool({tid}) err:", e)
            break
