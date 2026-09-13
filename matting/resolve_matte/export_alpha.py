#!/usr/bin/env python3
"""逐轨导出 grid9 为 ProRes4444（带阿尔法）。

用户确认的方案：在 grid9 时间线上，每次禁用其余 8 轨只留 1 轨，
并按该轨片段长度设置渲染 in/out（MarkIn=0, MarkOut=帧数-1）。
注意：达芬奇只能渲染到家目录下的文件夹。
用法: env python3 export_alpha.py [工程名] [输出目录]
"""
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resolve_ctl import get_resolve, ensure_project

OUT_DIR = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "alpha_v8")
N_TRACKS = 9

r = get_resolve()
p = ensure_project(r.GetProjectManager(), sys.argv[1] if len(sys.argv) > 1 else "matte_auto_v8")
tl = p.GetCurrentTimeline()
if tl.GetName() != "grid9":
    for i in range(1, (p.GetTimelineCount() or 0) + 1):
        t = p.GetTimelineByIndex(i)
        if t.GetName() == "grid9":
            p.SetCurrentTimeline(t)
            tl = t
            break
print("时间线:", tl.GetName())

clips = {}
for k in range(1, N_TRACKS + 1):
    items = tl.GetItemsInTrack("video", k)
    it = items[1] if items else None
    clips[k] = (it.GetName(), it.GetEnd() - it.GetStart())
    print(f"V{k}: {clips[k][0]} {clips[k][1]} 帧")

p.SetCurrentRenderFormatAndCodec("mov", "ProRes4444")
os.makedirs(OUT_DIR, exist_ok=True)
OUT_DIR = os.path.abspath(OUT_DIR)

results = []
for k in range(1, N_TRACKS + 1):
    frames = clips[k][1]
    # 轨道启用切换必须在剪辑页进行（交付页调用不生效）
    r.OpenPage("edit")
    time.sleep(1)
    for i in range(1, N_TRACKS + 1):
        tl.SetTrackEnable("video", i, i == k)
    r.OpenPage("deliver")
    time.sleep(1)
    ok = p.SetRenderSettings({
        "SelectAllFrames": False,
        "MarkIn": 108000,              # 时间线起始时码 01:00:00:00 的帧基准
        "MarkOut": 108000 + frames - 1,
        "TargetDir": OUT_DIR,
        "CustomName": f"matte_V{k}",
        "UniqueFilenameStyle": 0,
        "ExportVideo": True,
        "ExportAudio": False,
        "ExportAlpha": True,
        "AlphaMode": 1,
    })
    if not ok:
        raise RuntimeError(f"V{k} 渲染设置失败")
    job = p.AddRenderJob()
    p.StartRendering(job)
    t0 = time.time()
    while p.IsRenderingInProgress():
        time.sleep(2)
    st = p.GetRenderJobStatus(job)
    print(f"V{k}: {st.get('JobStatus')} {st.get('CompletionPercentage')}% 用时 {time.time()-t0:.0f}s (预期 {frames} 帧)", flush=True)
    p.DeleteRenderJob(job)
    results.append((k, os.path.join(OUT_DIR, f"matte_V{k}.mov"), frames))

# 恢复全部轨道启用
for i in range(1, N_TRACKS + 1):
    tl.SetTrackEnable("video", i, True)

print("\n=== 校验 ===")
for k, path, frames in results:
    if not os.path.exists(path):
        print(f"V{k}: 缺失! {path}")
        continue
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=pix_fmt,nb_frames", "-of", "csv=p=0", path],
        capture_output=True, text=True).stdout.strip()
    stats = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", path, "-vf", "extractplanes=a,signalstats", "-f", "null", "-"],
        capture_output=True, text=True).stderr
    m = re.findall(r"YMIN:\s*(\d+)|YMAX:\s*(\d+)", stats)
    # signalstats 打印 YMIN/YMAX/YAVG；alpha 平面的 YMIN/YMAX 即透明度范围
    ymin = re.search(r"YMIN:\s*(\d+)", stats)
    ymax = re.search(r"YMAX:\s*(\d+)", stats)
    # 尾部 2 秒的 alpha 上限：若轨道没切对（例如始终渲染了较短的 V1），
    # 片段结尾会是全透明，TMAX=0
    tail = subprocess.run(
        ["ffmpeg", "-hide_banner", "-sseof", "-2", "-i", path,
         "-vf", "extractplanes=a,signalstats", "-f", "null", "-"],
        capture_output=True, text=True).stderr
    tmax = re.search(r"YMAX:\s*(\d+)", tail)
    nb = probe.split(",")[-1]
    tail_ok = tmax and int(tmax.group(1)) > 0
    print(f"V{k}: {probe}  alpha[{ymin.group(1) if ymin else '?'},{ymax.group(1) if ymax else '?'}]"
          f"  尾部alphaMax={tmax.group(1) if tmax else '?'}"
          f"  帧数{'✓' if nb == str(frames) else '✗ 预期'+str(frames)}"
          f"  尾部{'✓' if tail_ok else '✗ 全透明!'}")
