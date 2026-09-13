#!/usr/bin/env bash
# vtx P0 基线演练执行器（冻结口径的一部分，P1/P5 复验须逐字节同构重跑）。
#
# 前置（影子区准备，本脚本不含，须先手工完成）：
#   1. rm -rf <影子区>/voice-ime-shadow
#   2. cd <影子区> && cp -rs <live 绝对路径> voice-ime-shadow
#      （必须绝对源路径：GNU cp 对相对源路径在子目录建链接会报
#        “只能在当前目录中创建相对的符号链接”）
#   3. 补齐项目外兄弟引用：timeline 的 sources 引用 ../_library/...（素材库），
#      影子比 live 深一层，须 ln -s ../_library <影子区>/_library
#      （否则源缺失 → 回执指纹算出 MISSING → 依赖门全灭，validate 直接阵亡）
#   4. 去链接化 vtx 写目标（防 write_text/ffmpeg 直写穿透符号链接到 live）：
#      cd voice-ime-shadow
#      rm -f out/audio.wav out/audio_report.json out/qc_report.json
#      rm -f cache/report_audio/stem_voice.wav cache/report_audio/stem_bed_raw.wav \
#            cache/report_audio/stem_bed_duck.wav
#      （※ audio --report 的 3 个 stem 落在固定名目录 cache/report_audio/
#        （audio_report.py:356），非 tmp_run——首跑演练曾因漏摘此 3 链接
#        写穿 live，已定为红线事故并在基线 README 留档；下轮起必删）
#      cp -L <live>/out/receipt.json out/.r && mv out/.r out/receipt.json
#      cp -L <live>/cache/manifest.json cache/.m && mv cache/.m cache/manifest.json
#      （※ receipt/manifest 必须从 live 拷贝实体——依赖门/记账要读旧状态）
#
# 执行顺序（冻结）：prepare → 重播种回执 → validate → diff → subs check → qc → audio --report
#   0. prepare（影子内冷抽 stem，~1.7G 只写影子 cache/audio）：stem 键 = sha1(绝对
#      路径+stat+采样哈希)——影子路径不同 → 键全变 → audio 阶段所需的 stem 必须
#      在影子内重新抽取（loudness/免检键不含路径，不受影响，照常命中 live 回执）。
#   1. 重播种回执：begin_stage("prepare") 会按 DOWNSTREAM 表撤销 audio/mux/qc 的
#      成功回执，须从 live 重拷实体 receipt.json 恢复 mux 门（qc 需要）。
#   2. qc 必须在 audio 之前：begin_stage("audio") 同样会撤销 mux/qc 回执。
#
# diff 对照旧版（冻结）：timeline.v4.2-frozen.json（与现行 timeline.json 有实质
#   差异的最近旧版；v4.3~v4.6-frozen 与现行 timeline.json 逐字节相同，diff 无信息量）。
set -u
# VTX 参数化（P1 复验）：默认 v1 主工作区（首跑基线口径）；复验 v2 时
# VTX=/home/liangruochong/文档/01_活跃项目/video_tools-v2/vtx/vtx.py bash run-baseline.sh <raw目录>
VTX="${VTX:-/home/liangruochong/文档/01_活跃项目/video_tools/vtx/vtx.py}"
# RAW 输出目录参数化（P0 复审 P1-6）：不传参 → 影子区时间戳目录；指向基线目录 → 拒绝
# （防止复跑覆盖首跑原始证据）
RAW="${1:-/home/liangruochong/文档/04_bilibili视频/_shadow_v2/raw-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$RAW"
case "$(cd "$RAW" && pwd)" in
  */docs/plans-v2/baseline*) echo "✗ RAW 指向基线目录（防覆盖首跑证据），显式传其他目录"; exit 9;;
esac
LIVE=/home/liangruochong/文档/04_bilibili视频/voice-ime
S=/home/liangruochong/文档/04_bilibili视频/_shadow_v2/voice-ime-shadow

cd "$S" || { echo "✗ 影子目录不存在: $S"; exit 9; }
# 防呆：receipt/manifest 必须已去链接化（实体文件），否则写入会穿透到 live
[ -f out/receipt.json ] && [ ! -L out/receipt.json ] || { echo "✗ out/receipt.json 仍是符号链接，先去链接化"; exit 9; }
[ -f cache/manifest.json ] && [ ! -L cache/manifest.json ] || { echo "✗ cache/manifest.json 仍是符号链接，先去链接化"; exit 9; }
# 防呆（P0 复审 P0-2，机器强制不靠注释）：三个 report stem 若仍是符号链接，
# ffmpeg 直写会穿透 live（首跑红线事故的重放路径）；subs_check.json 防御性删除
# （live 若曾跑过 subs check，影子重建会带该链接，subs.py write_text 直写穿透）。
for f in cache/report_audio/stem_voice.wav cache/report_audio/stem_bed_raw.wav \
         cache/report_audio/stem_bed_duck.wav; do
  if [ -L "$f" ]; then echo "✗ $f 仍是符号链接（写穿 live 风险），先 rm 再跑"; exit 9; fi
done
rm -f out/subs_check.json out/audit_report.json   # 红队 P2-5：audit_bundle 亦为固定名直写（偏离冻结程序时防穿透）

run_one() {
  name="$1"; shift
  "$@" > "$RAW/.tmp.log" 2>&1
  rc=$?
  { echo "# exit=$rc"; cat "$RAW/.tmp.log"; } > "$RAW/$name.log"
  echo "[$(date -Iseconds)] $name exit=$rc"
}

run_one prepare env PYTHONDONTWRITEBYTECODE=1 python3 "$VTX" -t timeline.json prepare
# 重播种：prepare 撤销了 mux/qc 门，从 live 恢复（fingerprint 与路径无关，必然匹配）
cp -L "$LIVE/out/receipt.json" out/.r && mv out/.r out/receipt.json
run_one validate env PYTHONDONTWRITEBYTECODE=1 python3 "$VTX" -t timeline.json validate
run_one diff     env PYTHONDONTWRITEBYTECODE=1 python3 "$VTX" -t timeline.json diff timeline.v4.2-frozen.json timeline.json
run_one subs     env PYTHONDONTWRITEBYTECODE=1 python3 "$VTX" -t timeline.json subs check
run_one qc       env PYTHONDONTWRITEBYTECODE=1 python3 "$VTX" -t timeline.json qc
run_one audio    env PYTHONDONTWRITEBYTECODE=1 python3 "$VTX" -t timeline.json audio --report
echo ALL_DONE
