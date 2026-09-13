#!/usr/bin/env python3
"""端到端：100 帧 MagicMask 人物抠像 → ProRes4444 alpha 渲染。"""
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from resolve_ctl import get_resolve, ensure_project

PROJ = "resolve_matte_dev"
# 素材：env MATTE_SRC 覆盖 > ~ 展开默认（原硬编码平移）；输出目录随迁移 HERE 相对
SRC = (os.environ.get("MATTE_SRC")
       or os.path.expanduser("~/文档/04_bilibili视频/voice-ime/cache/bg_gen/centered9/视频一.mp4"))
OUT_DIR = os.environ.get("MATTE_OUT") or str(HERE / "out")
FRAMES = 100  # 追加 100 帧（源帧 108000..108100）

resolve = get_resolve()
pm = resolve.GetProjectManager()
proj = ensure_project(pm, PROJ)
mp = proj.GetMediaPool()
tl = proj.GetCurrentTimeline()
items = tl.GetItemListInTrack("video", 1) or []
item = items[0] if items else None
if item is None:
    # 时间线为空：从媒体池取素材追加 100 帧
    mpi = (mp.ImportMedia([SRC]) or [None])[0]
    mp.AppendToTimeline([{"mediaPoolItem": mpi, "startFrame": 108000, "endFrame": 108000 + FRAMES}])
    item = tl.GetItemListInTrack("video", 1)[0]
mpi = item.GetMediaPoolItem()
print("pool item:", mpi.GetClipProperty()["File Path"] if mpi else None)

# 新建专用时间线放 100 帧短片段（重名则复用当前时间线）
tl = mp.CreateEmptyTimeline("matte_test")
if tl is None:
    tl = proj.GetCurrentTimeline()
    print("复用时间线:", tl.GetName())
appended = mp.AppendToTimeline([{"mediaPoolItem": mpi, "startFrame": 108000, "endFrame": 108000 + FRAMES}])
item = tl.GetItemListInTrack("video", 1)[0]
print("item:", item.GetName(), item.GetStart(), item.GetEnd(), "frames:", item.GetEnd() - item.GetStart())

names = item.GetFusionCompNameList() or []
print("comp names:", names)
comp = item.GetFusionCompByName(names[0]) if names else item.AddFusionComp()

media_in = comp.FindTool("MediaIn1")
media_out = comp.FindTool("MediaOut1")
mm = comp.FindTool("MagicMask") or comp.AddTool("MagicMask")

# 接线：MediaIn -> MagicMask -> MediaOut
mm.FindMainInput(1).ConnectTo(media_in.FindMainOutput(1))
media_out.FindMainInput(1).ConnectTo(mm.FindMainOutput(1))
print("mode current:", mm.GetInput("Mode"))
try:
    mm.SetInput("Mode", "Person")
    print("mode set Person ->", mm.GetInput("Mode"))
except Exception as e:
    print("Mode set err:", repr(e))
mm.SetInput("PostMultiplyImage", 1.0)
mm.SetInput("UseLegacyMagicMask", 0.0)

# 触发双向跟踪
print("trigger tracking ...")
mm.SetInput("TrackBidirectional", 1.0)
t0 = time.time()
last = -1
while True:
    time.sleep(5)
    done = mm.GetInput("ProcessedFramesHigh") or 0
    if done != last:
        print(f"  processed={done} t={time.time()-t0:.0f}s")
        last = done
    if done >= FRAMES:
        break
    if time.time() - t0 > 900:
        print("TIMEOUT waiting tracking")
        break
print("tracking done:", mm.GetInput("ProcessedFramesHigh"))

# 渲染设置：QuickTime ProRes 4444 + alpha
fmts = proj.GetRenderFormats()
print("formats sample:", {k: v for k, v in list(fmts.items())[:10]})
qt = next((k for k in fmts if "QuickTime" in k), None)
codecs = proj.GetRenderCodecs(qt)
print("qt codecs:", codecs)
codec = next((v for k, v in codecs.items() if "4444" in k or "4444" in str(v)), None)
print("using:", qt, codec)
proj.SetCurrentRenderFormatAndCodec(qt, codec)
os.makedirs(OUT_DIR, exist_ok=True)
ok = proj.SetRenderSettings({
    "SelectAllFrames": True,
    "TargetDir": OUT_DIR,
    "CustomName": "matte_test",
    "UniqueFilenameStyle": 0,
    "ExportVideo": True,
    "ExportAudio": False,
    "ExportAlpha": True,
    "AlphaMode": 1,  # straight
})
print("render settings:", ok)
job = proj.AddRenderJob()
print("job:", job)
proj.StartRendering(job)
while proj.IsRenderingInProgress():
    time.sleep(3)
    st = proj.GetRenderJobStatus(job)
    print("  status:", st.get("JobStatus"), st.get("CompletionPercentage"))
print("final:", proj.GetRenderJobStatus(job))
