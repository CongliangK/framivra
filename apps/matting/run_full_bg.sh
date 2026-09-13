#!/bin/bash
# 全量背景替换：precrop 素材 → 2048 抽帧 → BiRefNet_HR 首帧mask → MatAnyone2 → 4K合成 → crop+NVENC+音轨
# 路径参数化（P2a 随迁）：voice-ime 项目根 = 参数1 > env VOICE_IME > ~ 展开默认；
# works 目录（runner 脚本+克隆所在）= env WORKS > ~ 展开默认（原硬编码平移）。
set -e
VOICE_IME="${1:-${VOICE_IME:-$HOME/文档/04_bilibili视频/voice-ime}}"
WORKS="${WORKS:-$HOME/文档/04_bilibili视频/_library/bg_test/works}"
PYC=~/miniconda3/envs/comfyui/bin/python
cd "$VOICE_IME"
FULL=cache/bg_gen/full
mkdir -p $FULL
mv cache/bg_gen/sample_v1/样片_视频一_换背景.mp4 "$FULL/视频一.mp4"

for n in 二 三 四 五 六 七 八 九; do
  V="视频$n"
  W="cache/bg_gen/w_$n"
  echo "===== $V ====="
  rm -rf "$W"; mkdir -p "$W/frames" "$W/frames2048"
  ffmpeg -nostdin -y -v error -i "cache/av_sync/aroll_backup_precrop/$V.mp4" -vsync 0 "$W/frames/f%04d.png"
  ffmpeg -nostdin -y -v error -i "cache/av_sync/aroll_backup_precrop/$V.mp4" -vsync 0 -vf scale=2048:-2 "$W/frames2048/f%04d.png"
  (cd "$WORKS" && $PYC run_birefnet_hr.py "$VOICE_IME/$W/frames/f0001.png" "$VOICE_IME/$W/mask.png")
  $PYC -c "from PIL import Image; Image.open('$W/mask.png').resize((2048,1152), Image.LANCZOS).save('$W/mask_2048.png')"
  (cd "$WORKS" && $PYC run_matanyone2.py "$VOICE_IME/$W/frames2048" "$VOICE_IME/$W/mask_2048.png" "$VOICE_IME/$W/alpha_raw" "$VOICE_IME/$W/alpha")
  mkdir -p "$W/comp"
  $PYC - "$W" << 'PYEOF'
import cv2, numpy as np, os, sys, json
W = sys.argv[1]
cn = '视频' + W.split('_')[-1]
bg = cv2.imread('assets/bg/background_4k.png').astype(np.float32)
plan = json.load(open('cache/av_sync/crop_plan_v3.json'))[cn]
cw, ch, tx, ty = plan['crop']
n = len(os.listdir(f'{W}/alpha'))
for i in range(n):
    f = cv2.imread(f'{W}/frames/f{i+1:04d}.png')
    a = cv2.imread(f'{W}/alpha/a{i:04d}.png', 0)
    a = cv2.resize(a, (3840, 2160), interpolation=cv2.INTER_LANCZOS4)
    af = a[..., None].astype(np.float32) / 255.0
    comp = (f.astype(np.float32) * af + bg * (1 - af)).astype(np.uint8)
    if i % 100 == 0: print(f'{cn} {i}/{n}', flush=True)
    # 直接落 crop 后的帧（省 4K 全幅 IO）
    cv2.imwrite(f'{W}/comp/f{i+1:04d}.png', comp[ty:ty+ch, tx:tx+cw])
PYEOF
  ffmpeg -nostdin -y -v error -framerate 30000/1001 -i "$W/comp/f%04d.png" -i "assets/aroll/$V.mp4" \
    -map 0:v -map 1:a -vf "scale=3840:2160:flags=lanczos" \
    -c:v h264_nvenc -preset p6 -tune hq -rc vbr -b:v 150M -maxrate 170M -bufsize 300M \
    -c:a copy -movflags +faststart "$FULL/$V.mp4"
  rm -rf "$W/frames" "$W/comp"     # 释放磁盘（alpha/frames2048 留存以防复跑）
  echo "===== $V DONE ====="
done
echo "ALL DONE"
