#!/usr/bin/env bash
# 新建 vtx 视频项目脚手架：目录结构 + 模板 timeline.json
# 用法：new_project.sh <项目目录> [时长秒，默认30]
set -euo pipefail
DIR="${1:?用法: new_project.sh <项目目录> [时长秒]}"
SECS="${2:-30}"
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # skill 根目录（本脚本在 scripts/ 下）
VTX_DIR="$SKILL_DIR/vtx"
[ -d "$VTX_DIR" ] || { echo "找不到 vtx 目录：$VTX_DIR"; exit 1; }

mkdir -p "$DIR"/{assets,gfx,cache,out}
FRAMES=$((SECS * 30))
cat > "$DIR/timeline.json" <<EOF
{
  "version": 2,
  "project": { "fps": 30, "width": 1920, "height": 1080, "duration_frames": $FRAMES },
  "sources": [
    { "id": "v01", "path": "assets/v01.mp4", "transform": "crop_center_scale" }
  ],
  "video_tracks": [
    { "name": "aroll", "kind": "aroll",
      "clips": [ { "src": "v01", "src_in": 0, "src_out": $FRAMES, "dst_in": 0, "dur": $FRAMES } ] },
    { "name": "gfx", "kind": "gfx",
      "clips": [ { "src": "gfx/gfxA.html", "dst_in": 0, "dur": 192 } ] }
  ],
  "subtitles": [
    { "in": 12, "out": 88, "text": "示例字幕" }
  ],
  "audio_tracks": [
    { "name": "bed", "events": [ { "src": "v01", "in": 0, "out": $FRAMES, "volume": 0.25, "loop": true } ] },
    { "name": "voice", "events": [ { "src": "v01", "src_in": 0, "in": 12, "out": 200, "volume": 0.9 } ] }
  ]
}
EOF
cp "$VTX_DIR/timeline.schema.json" "$DIR/timeline.schema.json"
# 保留用户已有页面（包括符号链接），重跑脚手架不覆盖自定义 gfx。
if [ ! -e "$DIR/gfx/gfxA.html" ] && [ ! -L "$DIR/gfx/gfxA.html" ]; then
  cp "$SKILL_DIR/scripts/templates/gfx-starter.html" "$DIR/gfx/gfxA.html"
fi
cat <<'EOF'
✓ 项目已创建。下一步：
  1. 素材放 assets/（CFR 30fps（D24 换基）、含音轨，竖版会自动中心裁 16:9）
  2. gfx/gfxA.html 为协议合规样页，可直接 vtx all 跑通，改文案/配色即可；协议见 docs/skill/references/gfx.md
  3. 编辑 timeline.json 完成编排
  4. python3 ~/.agents/skills/video/vtx/vtx.py -t <项目>/timeline.json validate
  5. 校验通过后 ... all → out/final.mp4
EOF
