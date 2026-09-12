#!/usr/bin/env bash
# bench_cold.sh — vtx 全链冷跑基准编排（方案二阶段4）
#
# 用途：清空项目 cache 后跑 `vtx all`，逐行打时间戳 tee 到日志，轮后提取各阶段摘要。
# 口径：冷 = cache 整目录全清（rm -rf cache && mkdir cache）。
# 典型耗时：voice-ime ~5min/轮；轮间 sleep 10 回落。
# 基准数字归档：docs/DEV.md §一「性能基准」。
#
# 用法: bench_cold.sh [轮数N，默认1] [项目目录，默认 ~/文档/04_bilibili视频/bench_p2]
# 注意：不触碰 baseline_threads1/（x264-era 对照组）。
set -euo pipefail

ROUNDS="${1:-1}"
PROJ="${2:-$HOME/文档/04_bilibili视频/bench_p2}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VTX="$REPO/vtx/vtx.py"
DATE="$(date +%Y%m%d)"

[ -f "$PROJ/timeline.json" ] || { echo "防呆：$PROJ/timeline.json 不存在，退出"; exit 1; }
[ -f "$VTX" ] || { echo "防呆：找不到 $VTX"; exit 1; }
case "$ROUNDS" in ''|*[!0-9]*) echo "轮数须为正整数"; exit 1;; esac
[ "$ROUNDS" -ge 1 ] || { echo "轮数须 ≥1"; exit 1; }

LOGDIR="/tmp"
SUMMARY="$LOGDIR/vtx_bench_${DATE}_summary.txt"
: > "$SUMMARY"

echo "== vtx 冷跑基准：$ROUNDS 轮，项目 $PROJ，起始 $(date '+%F %T') =="

for i in $(seq 1 "$ROUNDS"); do
  LOG="$LOGDIR/vtx_bench_${DATE}_r${i}.log"
  echo "--- 第 $i/$ROUNDS 轮（日志 $LOG，负载 $(uptime | sed 's/.*load average: //')）---"
  rm -rf "$PROJ/cache"; mkdir -p "$PROJ/cache"

  # 逐行时间戳 + tee 到日志（vtx all 因 QC 74/75 已知探针假阴性退出码 1，属预期，不中断基准）
  set +e
  PYTHONUNBUFFERED=1 env python3 "$VTX" -t "$PROJ/timeline.json" all 2>&1 \
    | gawk '{ print systime()" "strftime("%T", systime())" | "$0 > "/dev/stderr"; fflush() }' 2> "$LOG"
  VTX_RC=$?
  set -e
  echo "vtx 退出码: $VTX_RC（QC 74/75 已知 c4_mini 探针假阴性 → 1 属预期）"

  # 摘要提取：追加到汇总文件 + stdout
  {
    echo "### 轮 $i · $(date '+%F %T') · 日志 $LOG"
    head -1 "$LOG" | awk -F' \\| ' '{ print "起: "$2 }'
    tail -1 "$LOG" | awk -F' \\| ' '{ print "止: "$2 }'
    START=$(head -1 "$LOG" | cut -d' ' -f1)
    END=$(tail -1 "$LOG" | cut -d' ' -f1)
    echo "全链总墙钟: $((END - START)) s"
    grep -E '\[(wave|pipeline)\]|\[video\]|\[seg\]|预编码|波' "$LOG" | sed 's/^[0-9]* | //' | head -30
    grep -Ei '拼接|自检|qc|帧' "$LOG" | sed 's/^[0-9]* | //' | tail -15
    echo ""
  } | tee -a "$SUMMARY"

  [ "$i" -lt "$ROUNDS" ] && {
    echo "轮间回落：等 1min load <3（最多 10 分钟）…"
    sleep 10
    waited=10
    while [ "$(cut -d' ' -f1 /proc/loadavg | cut -d. -f1)" -ge 3 ]; do
      [ "$waited" -ge 600 ] && { echo "警告：load 10 分钟未回落 <3，超时放行（本轮数字可能偏脏）"; break; }
      sleep 15; waited=$((waited+15))
    done
  }
done

echo "===== 各轮汇总（$SUMMARY）====="
cat "$SUMMARY"
