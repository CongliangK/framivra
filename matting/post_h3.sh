#!/bin/bash
# H3 成片后处理：找到 ComfyUI 输出 → 补原视频音轨 → 拉伸到 1920×1080 → 抽质检帧
set -e
SRC="/home/liangruochong/文档/xwechat_files/wxid_0jbagaqb72aq22_9bb6/msg/video/2026-08/240c742f929946a01ea9264ed675897c_raw.mp4"
OUT_DIR="$HOME/文档/04_bilibili视频/_library/bg_test/out"
IN=$(ls -t /home/liangruochong/AI/ComfyUI/output/bg_test_h3_r2v_videoref* 2>/dev/null | head -1)
[ -z "$IN" ] && { echo "no h3 output found"; exit 1; }
echo "input: $IN"
ffmpeg -y -v error -i "$IN" -i "$SRC" -filter_complex "[0:v]scale=1920:1080:flags=lanczos,setsar=1[v]" -map "[v]" -map 1:a -c:v libx264 -crf 18 -preset medium -c:a aac -shortest "$OUT_DIR/plan2_h3.mp4"
for t in 1 30 60 90 110; do ffmpeg -y -v error -ss 0.04 -i "$OUT_DIR/plan2_h3.mp4" -vf "select='eq(n\,$t)'" -vsync 0 -frames:v 1 -update 1 "$OUT_DIR/shotH3_$t.png"; done
echo "done: $OUT_DIR/plan2_h3.mp4"
