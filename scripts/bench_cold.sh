#!/usr/bin/env bash
# bench_cold.sh — vtx 全链冷跑基准编排（方案三阶段3 生产化重做，2026-09-13）
#
# 口径（方案三.4：真实冷清理，不允许假冷跑）：每轮
#   rm -rf $PROJ/cache && mkdir -p $PROJ/cache && rm -f $PROJ/out/receipt.json
# ——gfx/seg/段(vseg)/音频/回执/免检记录/响度回执全在 cache/ 下整删即全清；
# out/receipt.json 单列删除（回执口径：out/ 产物本体保留，轮转记账照常累积）。
#
# 验证链（每轮逐步计时，替代旧版 `vtx all` 一条龙——一条龙会把 mux/qc 白跑一遍；
# 方案三阶段3 `all --until video` 消灭双跑。v2 §1.2（2026-09-13）：limiter 已并入
# vtx audio（ARCHITECTURE §1.2），audio 产出的 out/audio.wav 恒为「归一+限幅」
# 终态，原独立 post_loudness 步骤与计时行移除）：
#   1) env python3 $VTX -t … all --until video   # 串行头+并行波+video 尾（audio 含限幅）
#   2) env python3 $VTX -t … mux                 # libfdk_aac 192k 封装
#   3) env python3 $VTX -t … qc                  # 预期 74/75、exit 1=c4_mini 已知
#                                               # 探针假阴性，不中断基准
#   4) env python3 apps/gates/content_gates.py -t … --media …/out/final.mp4  # 五门
#
# --with-4k（默认出列，方案三.6「4K 剔出冷跑关键路径」）：验证链后追加发布链分项
#   export_4k.py（计时）+ check_4k.sh（断言）——单列「发布链预算」，不计入验证链合计。
#
# 轮摘要提取：串行头（validate→prepare 墙钟）/ 并行波 [timing] 总耗时（gfx 关键
# 路径）/ video 尾（波结束→video 完成墙钟）/ mux/qc/gates 各步（/usr/bin/time）
# / 验证链合计（validate→gates 墙钟）/ qc 通过数 / 五门结论。全部轮结束后输出
# 各阶段中位数表 + 波动（(max-min)/中位，>15% 打 ⚠）。
#
# 日志：逐轮 /tmp/vtx_bench_<date>_r<i>.log（每行 "<epoch> <HH:MM:SS> | 原文"，
# 逐行时间戳 tee 终端+日志——模式沿用旧版 gawk，打点器换 python3 -u 取毫秒 epoch：
# 串行头 ~6s 级阶段的 1s 粒度会让 <15% 波动判定失真）；分步计时/RC 另落
# /tmp/vtx_bench_<date>_times/（含跨调用的中位数表数据文件，脚本启动时清旧）。
# 全部轮结束后把逐轮日志+汇总追加写 $PROJ/cache/bench_cold_<date>.log（轮后写，
# 避开每轮冷清理）。
#
# 用法: bench_cold.sh [轮数，默认3] [项目目录，默认 ~/文档/04_bilibili视频/bench_p2] [--with-4k]
# 机器纪律：起跑前与轮间等 1min load<3 且无 ffmpeg/google-chrome（最多 10 分钟，
# 超时放行并警告）；不触碰 baseline_threads1/（x264-era 对照组）。
# 基准数字归档：docs/DEV.md §一「性能基准」。
set -euo pipefail

# ---- 参数（--with-4k 可写在任意位置；前两个位置参数 = 轮数/项目） ----
ROUNDS=""; PROJ=""; WITH_4K=0
for arg in "$@"; do
  case "$arg" in
    --with-4k) WITH_4K=1 ;;
    *) if [ -z "$ROUNDS" ]; then ROUNDS="$arg"
       elif [ -z "$PROJ" ]; then PROJ="$arg"
       else echo "多余参数：$arg（用法：bench_cold.sh [轮数] [项目目录] [--with-4k]）"; exit 1; fi ;;
  esac
done
ROUNDS="${ROUNDS:-3}"
PROJ="${PROJ:-$HOME/文档/04_bilibili视频/bench_p2}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VTX="$REPO/vtx/vtx.py"
GATES="$REPO/apps/gates/content_gates.py"
DATE="$(date +%Y%m%d)"

[ -f "$PROJ/timeline.json" ] || { echo "防呆：$PROJ/timeline.json 不存在，退出"; exit 1; }
[ -f "$VTX" ] || { echo "防呆：找不到 $VTX"; exit 1; }
[ -f "$GATES" ] || { echo "防呆：找不到 $GATES"; exit 1; }
case "$ROUNDS" in ''|*[!0-9]*) echo "轮数须为正整数"; exit 1;; esac
[ "$ROUNDS" -ge 1 ] || { echo "轮数须 ≥1"; exit 1; }
if [ -e "$PROJ/baseline_threads1" ]; then
  echo "（baseline_threads1/ 在场：本脚本不触碰，rm -rf 只作用于 cache/）"
fi

LOGDIR="/tmp"
TDIR="$LOGDIR/vtx_bench_${DATE}_times"; mkdir -p "$TDIR"
# 中位数表数据文件按本次调用清旧（同日多次调用——正式轮/A-B/4K 轮——不得互串）
rm -f "$TDIR"/{head,wave,gfx,votail,mux,qc,gates,chain,exp4k}_* 2>/dev/null || true
SUMMARY="$TDIR/summary_${DATE}_$$.txt"
: > "$SUMMARY"

# ---- 基础设施 ----
wait_quiet() {   # 负载回落等待（骨架沿用+机器纪律）：load<3 且无 ffmpeg/google-chrome
  local waited=0 load ff ch
  while :; do
    load=$(cut -d' ' -f1 /proc/loadavg | cut -d. -f1)
    ff=$(pgrep -c ffmpeg 2>/dev/null || true); ff="${ff:-0}"
    ch=$(pgrep -c -x chrome 2>/dev/null || true); ch="${ch:-0}"
    if [ "$load" -lt 3 ] && [ "$ff" -eq 0 ] && [ "$ch" -eq 0 ]; then return 0; fi
    if [ "$waited" -ge 600 ]; then
      echo "警告：10 分钟未回落（load=$load ffmpeg=$ff chrome=$ch），超时放行（数字可能偏脏）"
      return 1
    fi
    sleep 15; waited=$((waited+15))
  done
}

LOG=""   # 当前轮日志路径（run_logged/mark 使用）
# 逐行时间戳打点器（格式沿用旧版 gawk 模式 "<epoch> <HH:MM:SS> | 原文"，tee 终端+
# 日志；换 python3 -u 取毫秒 epoch——串行头等 ~6s 阶段的 <15% 波动判定需亚秒粒度）
run_logged() {
  env python3 -u -c '
import sys, time
for line in sys.stdin:
    t = time.time()
    ts = time.strftime("%T", time.localtime(t))
    out = "%.3f %s | %s" % (t, ts, line)
    sys.stdout.write(out); sys.stdout.flush()
    with open(sys.argv[1], "a", encoding="utf-8") as f:
        f.write(out)
' "$LOG"
}
mark() {   # 分界标记行（同格式直接落日志，供轮摘要按行取 epoch）
  local t l
  t=$(date +%s.%3N); l="$t $(date +%T) | $1"
  echo "$l"; echo "$l" >> "$LOG"
}
epoch_of() { grep -F -- "$1" "$LOG" | head -1 | cut -d' ' -f1; }
tval() {   # 读 /usr/bin/time 计时值：非零退出时 GNU time 会把诊断行混进 -o 文件，取末行
  if [ -s "$1" ]; then tail -n 1 "$1"; else echo "n/a"; fi
}

run_step() {   # run_step <标签> <命令…>：/usr/bin/time 计时 + 逐行时间戳日志；
  local label="$1"; shift              # RC 落 $TDIR/rc_<轮>_<标签>（调用方判读）
  mark "#### step=<$label> START"
  set +e
  PYTHONUNBUFFERED=1 /usr/bin/time -f '%e' -o "$TDIR/t_${ROUND}_${label}" \
    "$@" 2>&1 | run_logged
  local rc=${PIPESTATUS[0]}
  set -e
  echo "$rc" > "$TDIR/rc_${ROUND}_${label}"
  mark "#### step=<$label> END rc=$rc"
  return "$rc"
}

median_of() {   # 中位数（文件集 TDIR/$1_<轮>；偶数取中间两数均值）
  local pat="$1" vals n
  vals=$(cat $TDIR/${pat}_* 2>/dev/null | grep -v '^$' | sort -n)
  [ -n "$vals" ] || return 1
  n=$(printf '%s\n' "$vals" | wc -l)
  printf '%s\n' "$vals" | awk -v n="$n" '{a[NR]=$1} END{
    if (n % 2) printf "%.1f", a[(n+1)/2]; else printf "%.1f", (a[n/2]+a[n/2+1])/2 }'
}

echo "== vtx 冷跑基准（方案三阶段3 验证链编排）：$ROUNDS 轮，项目 $PROJ，with-4k=$WITH_4K，起始 $(date '+%F %T') =="
echo "==   repo=$REPO  VTX_IO_WORKERS=${VTX_IO_WORKERS:-默认}  VTX_PIPELINE=${VTX_PIPELINE:-默认1}  VTX_GFX_PAGES=${VTX_GFX_PAGES:-默认7} =="
wait_quiet || true

UNEXPECTED_FAIL=0
for i in $(seq 1 "$ROUNDS"); do
  ROUND="$i"
  LOG="$LOGDIR/vtx_bench_${DATE}_r${i}.log"
  echo "--- 第 $i/$ROUNDS 轮（日志 $LOG，负载 $(uptime | sed 's/.*load average: //')，起 $(date '+%F %T')）---"
  : > "$LOG"

  # —— 真实冷清理（方案三.4）——
  mark "#### 冷清理：rm -rf cache + rm -f out/receipt.json"
  rm -rf "$PROJ/cache"; mkdir -p "$PROJ/cache"; rm -f "$PROJ/out/receipt.json"

  # —— 验证链（validate→gates）——
  mark "#### 验证链 START（validate→gates）"
  if ! run_step all_until_video env python3 "$VTX" -t "$PROJ/timeline.json" all --until video; then
    echo "⚠ step1 all --until video 异常退出（rc=$(cat "$TDIR/rc_${ROUND}_all_until_video")）——下游预期连锁失败，本轮照常采集"; UNEXPECTED_FAIL=1
  fi
  if ! run_step mux env python3 "$VTX" -t "$PROJ/timeline.json" mux; then
    echo "⚠ step2 mux 异常退出（rc=$(cat "$TDIR/rc_${ROUND}_mux")）"; UNEXPECTED_FAIL=1
  fi
  run_step qc env python3 "$VTX" -t "$PROJ/timeline.json" qc || true
  case "$(cat "$TDIR/rc_${ROUND}_qc")" in
    0) echo "  （qc rc=0：全过）";;
    1) echo "  （qc rc=1：74/75 c4_mini 已知假阴性形态，属预期，不中断）";;
    *) echo "⚠ qc 退出码 $(cat "$TDIR/rc_${ROUND}_qc") 非预期（非 0/1）"; UNEXPECTED_FAIL=1;;
  esac
  # gates 在项目目录内跑：--audio-wav 缺省 ./out/audio.wav 按 CWD 解析（G1 口径 = wav 源）
  if ! ( cd "$PROJ" && run_step gates env python3 "$GATES" -t "$PROJ/timeline.json" --media "$PROJ/out/final.mp4" ); then
    echo "⚠ step4 gates 非 0（rc=$(cat "$TDIR/rc_${ROUND}_gates" 2>/dev/null || echo '?')）——五门应全绿"; UNEXPECTED_FAIL=1
  fi
  mark "#### 验证链 END（validate→gates）"

  # —— 发布链（--with-4k，单列不计入验证链）——
  if [ "$WITH_4K" = "1" ]; then
    mark "#### 发布链（4K，预算单列）START"
    if ! run_step export_4k env python3 "$REPO/apps/publish/export_4k.py" -t "$PROJ/timeline.json"; then
      echo "⚠ export_4k 异常退出（rc=$(cat "$TDIR/rc_${ROUND}_export_4k")）"; UNEXPECTED_FAIL=1
    fi
    if ! run_step check_4k bash "$REPO/scripts/check_4k.sh" "$PROJ/out/final_4k.mp4"; then
      echo "⚠ check_4k 有 FAIL（rc=$(cat "$TDIR/rc_${ROUND}_check_4k")）"; UNEXPECTED_FAIL=1
    fi
    mark "#### 发布链（4K）END"
  fi

  # —— 轮摘要提取 ——
  STEP1_END=$(grep -F -- "#### step=<all_until_video> END" "$LOG" | head -1 | cut -d' ' -f1)
  VAL_T=$(epoch_of "== validate ==")
  WAVE_T=$(epoch_of "── 并行波")
  WAVE_DONE=$(grep -F -- "✓ 并行波完成" "$LOG" | head -1)
  WAVE_DONE_T=$(printf '%s' "$WAVE_DONE" | cut -d' ' -f1)
  WAVE_TOTAL=$(printf '%s' "$WAVE_DONE" | sed -n 's/.*总耗时 \([0-9.]*\)s.*/\1/p')
  GFX_PATH=$(sed -n 's/^.*✓ gfx 完成 \[timing\] +\([0-9.]*\)s.*/\1/p' "$LOG" | head -1)
  HEAD_S=$(awk -v a="$WAVE_T" -v b="$VAL_T" 'BEGIN{ if (a!="" && b!="") printf "%.2f", a-b }')
  VOTAIL_S=$(awk -v a="$STEP1_END" -v b="$WAVE_DONE_T" 'BEGIN{ if (a!="" && b!="") printf "%.2f", a-b }')
  CHAIN_START=$(grep -F -- "#### 验证链 START" "$LOG" | head -1 | cut -d' ' -f1)
  CHAIN_END=$(grep -F -- "#### 验证链 END" "$LOG" | head -1 | cut -d' ' -f1)
  CHAIN_S=$(awk -v a="$CHAIN_END" -v b="$CHAIN_START" 'BEGIN{ if (a!="" && b!="") printf "%.2f", a-b }')
  QC_N=$(grep -o 'QC：[0-9]*/[0-9]*' "$LOG" | head -1)
  GATES_N=$(grep -c '| ✓ G[0-9]' "$LOG" || true)
  GATES_RC=$(cat "$TDIR/rc_${ROUND}_gates" 2>/dev/null || echo "?")
  # 分步耗时落盘（中位数表用；wave/gfx 来自 [timing] 行，head/votail/chain 来自
  # 日志 epoch 差，mux/qc/gates 来自 /usr/bin/time；v2 §1.2 后无独立 post 步）
  [ -n "$HEAD_S" ] && echo "$HEAD_S" > "$TDIR/head_$i"
  [ -n "$WAVE_TOTAL" ] && echo "$WAVE_TOTAL" > "$TDIR/wave_$i"
  [ -n "$GFX_PATH" ] && echo "$GFX_PATH" > "$TDIR/gfx_$i"
  [ -n "$VOTAIL_S" ] && echo "$VOTAIL_S" > "$TDIR/votail_$i"
  [ -n "$CHAIN_S" ] && echo "$CHAIN_S" > "$TDIR/chain_$i"
  for s in mux qc gates; do
    v=$(tval "$TDIR/t_${ROUND}_${s}")
    if [ -n "$v" ]; then echo "$v" > "$TDIR/${s}_$i"; fi
  done
  if [ "$WITH_4K" = "1" ]; then
    v4=$(tval "$TDIR/t_${ROUND}_export_4k")
    if [ -n "$v4" ]; then echo "$v4" > "$TDIR/exp4k_$i"; fi
  fi

  {
    echo "### 轮 $i · $(date '+%F %T') · 日志 $LOG"
    echo "  串行头（validate→prepare）  : ${HEAD_S:-n/a}s"
    echo "  并行波 [timing] 总耗时        : ${WAVE_TOTAL:-n/a}s（gfx 路径 ${GFX_PATH:-n/a}s）"
    echo "  video 尾（波结束→video 完成）: ${VOTAIL_S:-n/a}s"
    echo "  mux / qc / gates:"
    for s in mux qc gates; do
      echo "    $s : $(tval "$TDIR/t_${ROUND}_${s}" || echo n/a)s（rc=$(cat "$TDIR/rc_${ROUND}_${s}" 2>/dev/null || echo '?')）"
    done
    echo "  验证链合计（validate→gates） : ${CHAIN_S:-n/a}s"
    echo "  qc 通过数: ${QC_N:-未检出}   五门: G 门 ✓×${GATES_N:-0}，gates rc=${GATES_RC}"
    if [ "$WITH_4K" = "1" ]; then
      echo "  发布链预算（单列）: export_4k $(tval "$TDIR/t_${ROUND}_export_4k" || echo n/a)s + check_4k rc=$(cat "$TDIR/rc_${ROUND}_check_4k" 2>/dev/null || echo '?')"
    fi
    echo ""
  } | tee -a "$SUMMARY"

  if [ "$i" -lt "$ROUNDS" ]; then
    echo "轮间回落：等 1min load<3 且无 ffmpeg/chrome（最多 10 分钟）…"
    wait_quiet || true
  fi
done

# ---- 全轮结束：各阶段中位数表 + 波动（(max-min)/中位 >15% 打 ⚠） ----
echo ""
echo "===== 各阶段中位数表（$ROUNDS 轮；波动=(max-min)/中位）====="
{
  echo "| 阶段 | 中位 s | min | max | 波动 |"
  echo "|---|---|---|---|---|"
  for m in head wave gfx votail mux qc gates chain exp4k; do
    med=$(median_of "$m") || continue
    vals=$(cat $TDIR/${m}_* 2>/dev/null | grep -v '^$' | sort -n)
    mn=$(printf '%s\n' "$vals" | head -1); mx=$(printf '%s\n' "$vals" | tail -1)
    fl=$(awk -v mn="$mn" -v mx="$mx" -v med="$med" 'BEGIN{
      if (med+0 <= 0) print "n/a"; else printf "%.0f%%", (mx-mn)/med*100 }')
    warn=$(awk -v mn="$mn" -v mx="$mx" -v med="$med" 'BEGIN{
      print ((med+0 > 0 && (mx-mn)/med > 0.15) ? " ⚠" : "") }')
    case "$m" in
      head) label="串行头（validate→prepare）";;
      wave) label="并行波总耗时";;
      gfx) label="波内 gfx 路径";;
      votail) label="video 尾";;
      chain) label="验证链合计（validate→gates）";;
      exp4k) label="4K 导出（发布链，单列）";;
      *) label="$m";;
    esac
    echo "| $label | $med | $mn | $mx | $fl$warn |"
  done
} | tee -a "$SUMMARY"

# ---- 落盘：逐轮日志 + 汇总追加写项目 cache（轮后写，避开冷清理） ----
DEST="$PROJ/cache/bench_cold_${DATE}.log"
{
  echo ""
  echo "=================================================================="
  echo "== bench_cold 归档追加 $(date '+%F %T')（with-4k=$WITH_4K，VTX_IO_WORKERS=${VTX_IO_WORKERS:-默认}）=="
  for i in $(seq 1 "$ROUNDS"); do
    echo "---- 逐轮日志 r$i：/tmp/vtx_bench_${DATE}_r${i}.log ----"
    cat "$LOGDIR/vtx_bench_${DATE}_r${i}.log"
    echo ""
  done
  echo "---- 汇总 ----"
  cat "$SUMMARY"
} >> "$DEST"
echo ""
echo "===== 完成：汇总 $SUMMARY；归档追加 $DEST ====="
if [ "$UNEXPECTED_FAIL" -ne 0 ]; then
  echo "⚠ 本基准有非预期退出（见各步 rc）——数字可用性自查"
  exit 1
fi
