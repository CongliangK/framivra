#!/usr/bin/env bash
# video_tools 初始化脚本
# 用途：新环境（新机 / 新 clone）快速就绪——依赖自检 + 生成本地配置（大模型能力默认关闭）。
# 幂等：重复运行无副作用；config.local.json 已存在时不覆盖。
set -uo pipefail
cd "$(dirname "$0")"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}[OK]${NC} $1"; }
miss() { echo -e "  ${RED}[缺失]${NC} $1（必需，见 README 依赖矩阵）"; FAIL=1; }
opt()  { echo -e "  ${YELLOW}[可选]${NC} $1"; }

echo "=== video_tools init ==="
FAIL=0

echo "--- 1/4 基础依赖（vtx 渲染链必需）---"
if command -v python3 >/dev/null 2>&1; then ok "python3 $(python3 --version 2>&1 | awk '{print $2}')"; else miss python3; fi
if command -v ffmpeg >/dev/null 2>&1;  then ok "ffmpeg $(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}')"; else miss ffmpeg; fi
command -v ffprobe >/dev/null 2>&1 && ok ffprobe || miss ffprobe
command -v google-chrome >/dev/null 2>&1 && ok "google-chrome（gfx 帧截图）" || miss google-chrome

echo "--- 2/4 可选依赖（按工具按需）---"
command -v blender >/dev/null 2>&1 && ok "blender（blender_bridge/ 预览桥）" || opt "blender 未装——blender_bridge/ 不可用"
command -v sox >/dev/null 2>&1 && ok "sox（部分音频后处理）" || opt "sox 未装"
if [ -d "$HOME/AI/ComfyUI" ]; then opt "检测到 ~/AI/ComfyUI——大模型能力仍默认关闭，需手动配置开启"; fi
if [ -d "$HOME/miniconda3/envs/comfyui" ]; then opt "检测到 conda env comfyui——matting/ 抠像链的运行环境（仍默认关闭）"; fi

echo "--- 3/4 本地配置（大模型默认关闭）---"
if [ -f config.local.json ]; then
  echo "  config.local.json 已存在，保持不动"
else
  cp config.example.json config.local.json
  echo "  已生成 config.local.json（comfyui / asr / tts 全部 enabled:false）"
fi
echo "  如需开启大模型能力：编辑 config.local.json，并阅读 matting/README.md「开启步骤」"

echo "--- 4/4 结果 ---"
if [ "$FAIL" -eq 0 ]; then
  echo -e "${GREEN}基础依赖齐备，工具可用。${NC}"
else
  echo -e "${RED}有必需依赖缺失（见上），补齐后重跑 init.sh。${NC}"
fi
exit $FAIL
