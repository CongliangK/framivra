#!/usr/bin/env bash
# gen_fixture.sh —— skill-demo 最小素材生成器（v2 验收门 4 关停测试道具）。
# 产出 v01.mp4：testsrc2 90 帧 1920x1080 CFR30 + 440Hz sine 音轨。
# 编码用 libx264（yuv420p）：不用 NVENC——硬编码器对极短素材有最小帧尺寸/
# 会话约束，x264 无此坑（任务口径：确保尺寸与帧数确定性）。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ffmpeg -y -hide_banner -loglevel error \
  -f lavfi -i "testsrc2=size=1920x1080:rate=30" \
  -f lavfi -i "sine=frequency=440:sample_rate=48000" \
  -frames:v 90 -r 30 \
  -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p \
  -c:a aac -b:a 128k -ac 2 -ar 48000 -shortest \
  "$HERE/v01.mp4"

frames=$(ffprobe -v error -select_streams v:0 -count_frames \
  -show_entries stream=nb_read_frames -of csv=p=0 "$HERE/v01.mp4")
[ "$frames" = "90" ] || { echo "FAIL: v01.mp4 帧数 $frames ≠ 90"; exit 1; }
echo "✓ v01.mp4：1920x1080 CFR30 × 90 帧（含 48k stereo 音轨）"
