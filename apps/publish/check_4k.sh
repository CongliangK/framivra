#!/usr/bin/env bash
# check_4k.sh · 4K 投稿版成片规格断言（通用化收编自 voice-ime/cache/v39/check_4k_v39.sh，D31/D39）
#
# 用途：对 4K 放大投稿版（进 B 站高码率池策略，voice-ime D31）做发布前硬门断言。
# 用法：
#   bash check_4k.sh <4k文件> [期望帧数]
#     · 期望帧数缺省时：依次尝试 <4k文件所在目录>/timeline.json 与 ../timeline.json，
#       读 project.duration_frames 自动推导；仍取不到则跳过 6b 帧数门（其余门照跑）。
#   BITRATE_CAP_MBPS=20.5 bash check_4k.sh ...   # 码率门覆盖（Mbps）
# 断言项（任一 FAIL 退出码 1）：
#   1) 3840x2160  2) 帧率 30/1 双验  3) h264 High  4) yuv420p  5) 平均码率 ≤ cap
#   6) 时长与帧数推导值一致 ±0.05s / 6b) 帧数=期望（可推导）  7) aac 48k + copy 区间
#   8) I 帧间隔 <300 帧  9) faststart（moov 前 64 字节）
# 来源项目：voice-ime（v3.9 基线 14/14：16746f/558.2s/14.77Mbps 文件口径）
set -u
F="${1:?用法: check_4k.sh <4k文件> [期望帧数]}"
EXPECT_FRAMES="${2:-}"
CAP="${BITRATE_CAP_MBPS:-20.5}"
PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "PASS  $1"; }
bad()  { FAIL=$((FAIL+1)); echo "FAIL  $1"; }
[ -f "$F" ] || { echo "FATAL: $F 不存在"; exit 2; }

# 期望帧数自动推导（timeline.json project.duration_frames；须在 readlink 解析符号链接前，
# 保证「symlink 到 live 文件 + /tmp 放 timeline.json」的隔离测试路径可用）
if [ -z "$EXPECT_FRAMES" ]; then
  D=$(dirname "$F")
  for TL in "$D/timeline.json" "$D/../timeline.json"; do
    if [ -f "$TL" ]; then
      EXPECT_FRAMES=$(env python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["project"]["duration_frames"])' "$TL" 2>/dev/null) && break || EXPECT_FRAMES=""
    fi
  done
fi
F=$(readlink -f "$F")

FPS=30
DUR_S="?"
if [ -n "$EXPECT_FRAMES" ]; then
  DUR_S=$(env python3 -c 'import sys; print(float(sys.argv[1])/float(sys.argv[2]))' "$EXPECT_FRAMES" "$FPS")
fi

# ---- 1-4: 流参数 ----
WIDTH=$(ffprobe -v error -select_streams v:0 -show_entries stream=width -of csv=p=0 "$F")
HEIGHT=$(ffprobe -v error -select_streams v:0 -show_entries stream=height -of csv=p=0 "$F")
RFR=$(ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate -of csv=p=0 "$F")
AFR=$(ffprobe -v error -select_streams v:0 -show_entries stream=avg_frame_rate -of csv=p=0 "$F")
CODEC=$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name -of csv=p=0 "$F")
PROFILE=$(ffprobe -v error -select_streams v:0 -show_entries stream=profile -of csv=p=0 "$F")
PIXFMT=$(ffprobe -v error -select_streams v:0 -show_entries stream=pix_fmt -of csv=p=0 "$F")
NB=$(ffprobe -v error -select_streams v:0 -count_packets -show_entries stream=nb_read_packets -of csv=p=0 "$F")

[ "$WIDTH" = "3840" ] && [ "$HEIGHT" = "2160" ] && ok "1 分辨率 ${WIDTH}x${HEIGHT} = 3840x2160" || bad "1 分辨率 ${WIDTH}x${HEIGHT} ≠ 3840x2160"
[ "$RFR" = "30/1" ] && ok "2a r_frame_rate=${RFR} (30/1 精确)" || bad "2a r_frame_rate=${RFR} ≠ 30/1"
[ "$AFR" = "30/1" ] && ok "2b avg_frame_rate=${AFR} (30/1 精确)" || bad "2b avg_frame_rate=${AFR} ≠ 30/1"
[ "$CODEC" = "h264" ] && ok "3a 编码 ${CODEC} = h264" || bad "3a 编码 ${CODEC} ≠ h264"
echo "$PROFILE" | grep -qi "high" && ok "3b profile=${PROFILE} (High)" || bad "3b profile=${PROFILE} 非 High"
[ "$PIXFMT" = "yuv420p" ] && ok "4 pix_fmt=${PIXFMT} (8bit yuv420p)" || bad "4 pix_fmt=${PIXFMT} ≠ yuv420p"

# ---- 5: 平均码率（流字段与文件口径取较大者做门，更严）----
VBR=$(ffprobe -v error -select_streams v:0 -show_entries stream=bit_rate -of csv=p=0 "$F")
ABR=$(ffprobe -v error -select_streams a:0 -show_entries stream=bit_rate -of csv=p=0 "$F")
DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$F")
SIZE=$(stat -c%s "$F")
TOTAL_MBPS=$(env python3 -c 'import sys; print(f"{float(sys.argv[1])*8/float(sys.argv[2])/1e6:.2f}")' "$SIZE" "$DUR")
VBR_K=$(env python3 -c 'import sys; print(int(float(sys.argv[1]))//1000)' "${VBR:-0}")
env python3 -c '
import sys
v, size, duration, cap = map(float, sys.argv[1:])
t=size*8/duration
avg=max(v,t)/1e6
print("OK" if avg<=cap else "NG")' "${VBR:-0}" "$SIZE" "$DUR" "$CAP" | grep -q OK \
  && ok "5 平均码率 stream=${VBR} (${VBR_K}k) 文件口径总码率=${TOTAL_MBPS}Mbps ≤${CAP}M 门" \
  || bad "5 平均码率超门 stream=${VBR} 文件口径=${TOTAL_MBPS}Mbps（cap=${CAP}M）"

# ---- 6: 时长与帧数（须有期望帧数；无则跳过 6/6b）----
if [ -n "$EXPECT_FRAMES" ]; then
  env python3 -c '
import sys
d, expected = map(float, sys.argv[1:])
sys.exit(0 if abs(d-expected)<=0.05 else 1)' "$DUR" "$DUR_S" \
    && ok "6 时长 ${DUR}s = ${DUR_S}±0.05" || bad "6 时长 ${DUR}s 偏差 >0.05s（期望 ${DUR_S}s）"
  [ "$NB" = "$EXPECT_FRAMES" ] && ok "6b 帧数 ${NB} = ${EXPECT_FRAMES}" || bad "6b 帧数 ${NB} ≠ ${EXPECT_FRAMES}"
else
  echo "SKIP 6/6b 帧数门（未传期望帧数且未找到 timeline.json）"
fi

# ---- 7: 音轨 ----
ACODEC=$(ffprobe -v error -select_streams a:0 -show_entries stream=codec_name -of csv=p=0 "$F")
ASR=$(ffprobe -v error -select_streams a:0 -show_entries stream=sample_rate -of csv=p=0 "$F")
[ "$ACODEC" = "aac" ] && ok "7a 音轨 ${ACODEC} 在" || bad "7a 音轨非 aac（${ACODEC}）"
[ "$ASR" = "48000" ] && ok "7b 采样率 ${ASR}Hz" || bad "7b 采样率 ${ASR} ≠ 48000"
env python3 -c '
import sys
a=float(sys.argv[1])
# copy 验证：fdk_aac 192k 母带直通，码率应在 180-205k 区间（重编码会漂移）
print("OK" if 180000<=a<=205000 else "NG")' "${ABR:-0}" | grep -q OK \
  && ok "7c 音频码率 ${ABR}bps（192k copy 区间 180-205k）" || bad "7c 音频码率 ${ABR} 超出 copy 区间（疑似重编码）"

# ---- 8: I 帧间隔（keyframe 间距 <300 帧）----
MAXGAP=$(ffprobe -v error -select_streams v:0 -show_entries packet=flags -of csv=p=0 "$F" \
  | env python3 -c "
import sys
last=None; gap=0; mx=0
for line in sys.stdin:
    flags=line.strip()
    if not flags: continue
    gap+=1
    if 'K' in flags:
        if last is not None: mx=max(mx,gap)
        last=gap; gap=0
mx=max(mx,gap)
print(mx)")
[ "$MAXGAP" -lt 300 ] 2>/dev/null && ok "8 I 帧最大间隔 ${MAXGAP} 帧 <300（$(( MAXGAP/30 ))s）" || bad "8 I 帧最大间隔 ${MAXGAP} 帧 ≥300（10s）"

# ---- 9: faststart（moov 在 mdat 前）----
MOOV=$(env python3 -c '
import sys
data=open(sys.argv[1],"rb").read(64)
i=data.find(b"moov"); m=data.find(b"mdat")
if i>=0 and (m<0 or i<m): print("front")
elif i>=0: print("tail")
else: print("none")' "$F")
[ "$MOOV" = "front" ] && ok "9 faststart：moov 在文件头部" || bad "9 moov 位置=${MOOV}（非 faststart）"

echo "----------------------------------------"
echo "结果: PASS=${PASS} FAIL=${FAIL}"
[ "$FAIL" -eq 0 ] && echo "4K 规格断言全部通过" || echo "存在 FAIL 项，禁止投稿"
exit $(( FAIL>0 ? 1 : 0 ))
