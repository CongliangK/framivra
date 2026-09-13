#!/usr/bin/env python3
"""抠像流水线 phase1：prep（纯 API，避开创_race 的高危接口）。

用法: env python3 prep.py [工程名]
流程: 建工程 → 立刻设帧率/分辨率 → 导入 9 视频 → 建 9 轨道时间线 →
      每轨追加前归零播放头 → 校验对齐 → 打开调色页等待人工/鼠标序列操作。
"""
import sys
import time

sys.path.insert(0, "/home/liangruochong/文档/01_活跃项目/video_tools/matting/resolve_matte")
from resolve_ctl import get_resolve, ensure_project

BASE = "/home/liangruochong/文档/04_bilibili视频/voice-ime/cache/bg_gen/centered9"
NAMES = ["视频一", "视频二", "视频三", "视频四", "视频五", "视频六", "视频七", "视频八", "视频九"]
TC_START = "01:00:00:00"

project = sys.argv[1] if len(sys.argv) > 1 else "matte_auto_v3"

r = get_resolve()
p = ensure_project(r.GetProjectManager(), project)
mp = p.GetMediaPool()

# 必须在导入任何素材前设置（帧率建工程后很快锁死）
p.SetSetting("timelineFrameRate", "29.97")
p.SetSetting("timelineResolutionWidth", "2048")
p.SetSetting("timelineResolutionHeight", "1152")
time.sleep(0.5)
fps = p.GetSetting("timelineFrameRate")
print("工程帧率:", repr(fps))
if not fps or abs(float(fps) - 29.97) > 0.01:
    sys.exit("帧率设置失败，中止（否则片段会被变速吸附）")

clips = {}
for n in NAMES:
    c = (mp.ImportMedia([f"{BASE}/{n}.mp4"]) or [None])[0]
    time.sleep(0.2)
    if c is None:
        sys.exit(f"导入失败: {n}")
    clips[f"{n}.mp4"] = c
print(f"导入 {len(clips)} 个素材")

tl = mp.CreateEmptyTimeline("grid9")
time.sleep(1)
tl = tl or p.GetCurrentTimeline()
while (tl.GetTrackCount("video") or 1) < 9:
    tl.AddTrack("video")
    time.sleep(0.1)
print("视频轨道数:", tl.GetTrackCount("video"))

def reset_playhead(tl):
    """把播放头归位到 TC_START，读回校验（异步生效，最多重试 10 次）。"""
    for _ in range(10):
        tl.SetCurrentTimecode(TC_START)
        time.sleep(0.2)
        cur = tl.GetCurrentTimecode()
        if cur == TC_START:
            return True
        time.sleep(0.3)
    return False


# 追加顺序：V1 留到最后（追加锚点 = V1 内容末尾，V1 为空时 = 时间线起点，
# 因此先放 V2..V9，最后放 V1，全部对齐时间线起点）
order = list(range(2, 10)) + [1]
for i in order:
    n = NAMES[i - 1]
    mp.AppendToTimeline([{"mediaPoolItem": clips[f"{n}.mp4"], "trackIndex": i}])
    time.sleep(0.3)

# 校验：9 轨全部对齐到同一起点、时长与源一致
ok = True
starts = set()
for t, n in enumerate(NAMES, start=1):
    its = tl.GetItemListInTrack("video", t) or []
    if not its:
        print(f"V{t}: 空！"); ok = False; continue
    it = its[0]
    starts.add(it.GetStart())
    if it.GetName() != f"{n}.mp4":
        print(f"V{t}: 片段错位 {it.GetName()}"); ok = False
if len(starts) > 1:
    print("起点不一致:", sorted(starts)); ok = False
print("对齐校验:", "通过" if ok else "失败")
if not ok:
    sys.exit(1)

r.OpenPage("color")
print("prep 完成，已切到调色页，等待鼠标序列。")
