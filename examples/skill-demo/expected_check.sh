#!/usr/bin/env bash
# expected_check.sh —— skill-demo 断言脚本（v2 验收门 4 关停测试道具，ARCHITECTURE §9 门 4）。
# 跑通全链（validate + all）后断言：out/final.mp4 存在、成片帧数 90、
# QC 全 PASS（含 v2 §1.3 解码域真峰 TP 项）——「仅内核仍可出片」的机器判据。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

# 仓库根定位（P4 审查 P2-3）：VTX_REPO 覆盖 > 向上搜索含 vtx/vtx.py 的祖先
#（包复制到任意第三方位置不断裂）；$VTX 加引号防空格路径。
if [ -n "${VTX_REPO:-}" ]; then
  REPO="$VTX_REPO"
else
  REPO="$HERE"
  while [ "$REPO" != "/" ] && [ ! -f "$REPO/vtx/vtx.py" ]; do
    REPO="$(dirname "$REPO")"
  done
  [ -f "$REPO/vtx/vtx.py" ] || { echo "FAIL: 找不到 vtx/vtx.py——设置 VTX_REPO 指向 video_tools 仓"; exit 1; }
fi

[ -f v01.mp4 ] || { echo "FAIL: 缺 v01.mp4——先跑 bash gen_fixture.sh"; exit 1; }

VTX="env python3 $REPO/vtx/vtx.py -t timeline.json"

echo "== validate =="
$VTX validate

echo "== all（全链：prepare→gfx→video→audio(响度+limiter)→mux→qc）=="
$VTX all

echo "== 断言 =="
# ① 产物存在
[ -f out/final.mp4 ] || { echo "FAIL: out/final.mp4 不存在"; exit 1; }
echo "✓ out/final.mp4 在盘"

# ② 成片帧数 = 90（count_frames 真解码）
frames=$(ffprobe -v error -select_streams v:0 -count_frames \
  -show_entries stream=nb_read_frames -of csv=p=0 out/final.mp4)
[ "$frames" = "90" ] || { echo "FAIL: 成片帧数 $frames ≠ 90"; exit 1; }
echo "✓ 成片帧数 90"

# ③ QC 全 PASS（P4 审查 P2-7：--strict 复跑禁用免检旁路——像素级检查强制全检，
#    断言不依赖首跑回执温态）
qc_out=$($VTX qc --strict)
echo "$qc_out"
n_pass=$(echo "$qc_out" | grep -c '^  ✓' || true)
n_fail=$(echo "$qc_out" | grep -c '^  ✗' || true)
[ "$n_fail" = "0" ] || { echo "FAIL: QC 有 ✗ 项"; exit 1; }
[ "$n_pass" -ge 1 ] || { echo "FAIL: QC 未输出任何检查项"; exit 1; }
echo "$qc_out" | grep -q '解码域真峰 TP ≤ -1 dBFS' \
  || { echo "FAIL: QC 缺 TP 判定项（v2 §1.3）"; exit 1; }
echo "✓ QC $n_pass 项全 PASS（--strict 全检，含 TP 项）"

echo "✅ skill-demo expected_check 全部通过（仅内核出片）"
