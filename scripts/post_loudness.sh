#!/usr/bin/env bash
# post_loudness.sh · vtx audio 之后的响度后处理（通用化收编自 voice-ime/post_loudness.sh，D29/D31/D32）
#
# 用途：vtx audio 阶段之后、mux 之前必跑的响度补偿+限幅兜底（可重复执行）。
# 机制：测综合响度 I → volume=(TARGET−I)dB → alimiter(limit=0.599=−4.45dBFS，AAC 编码
#   过冲实测 +3.6dB 需 headroom，voice-ime D31 工程教训：终检必须测编码后解码 TP，
#   不信 wav 域 TP) → 复测门 ±1.5 LU。
# 注意（voice-ime D32 教训）：vtx audio 若已采用「两遍静态增益」响度链，增益收敛问题
#   已在上游解决，本脚本职责收窄为限幅兜底+复测确认；alimiter 兜底段不可省。
# 用法：
#   bash post_loudness.sh [项目目录]     # 默认当前目录；项目目录须含 out/audio.wav
#   TARGET_LUFS=-14 bash post_loudness   # 目标响度覆盖（默认 -14 = B 站 EBU R128 标准）
# 来源项目：voice-ime（决策 D16/D29/D31/D32）
set -euo pipefail

DIR="${1:-.}"
TARGET="${TARGET_LUFS:--14}"
cd "$DIR"

[ -f out/audio.wav ] || { echo "FATAL: $PWD/out/audio.wav 不存在（须在 vtx audio 之后运行）"; exit 2; }

I=$(ffmpeg -i out/audio.wav -af ebur128=peak=true -f null - 2>&1 | grep -A9 "Summary" | grep "I:" | head -1 | grep -oE "\-?[0-9]+\.[0-9]+" | head -1)
[ -n "${I:-}" ] || { echo "无法测量 I 值"; exit 1; }
VOL=$(python3 -c "print(round($TARGET - ($I), 1))")
echo "实测 I=${I} LUFS → 补增益 ${VOL}dB + alimiter(-4.45dBFS)"
ffmpeg -y -v error -i out/audio.wav -af "volume=${VOL}dB,alimiter=limit=0.599:level=false" \
  -c:a pcm_s16le -ar 48000 out/_post.wav
mv out/_post.wav out/audio.wav
I2=$(ffmpeg -i out/audio.wav -af ebur128=peak=true -f null - 2>&1 | grep -A9 "Summary" | grep "I:" | head -1 | grep -oE "\-?[0-9]+\.[0-9]+" | head -1)
echo "复测 I=${I2} LUFS（目标 ≈ ${TARGET}，容差 ±1.5）"
python3 -c "import sys; sys.exit(0 if abs(float('$I2') - ($TARGET)) <= 1.5 else 1)" \
  && echo "✓ 响度达标，可 mux" || { echo "✗ 超差，请人工检查"; exit 1; }
