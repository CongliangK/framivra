#!/usr/bin/env bash
# tech-talk 模板包 · 一键项目脚手架
# 用途：为科技口播视频新建完整项目目录——工程文档模板四件 + 01-brief + timeline 骨架（占位素材
#       原位可 validate）+ gates/mixing 预设，尾声自动跑 vtx validate 并打印下一步指引。
# 用法：bash new_project.sh <项目目录> [--title "视频标题"] [--id 项目代号]
# 依赖：bash + coreutils（mkdir/cp/date）+ python3（渲染占位符与 vtx validate）。
#       无需安装：vtx 路径自定位——本脚本位于 <video_tools>/skills/tech-talk/scripts/，
#       仓根 = 脚本向上三级（与 scripts/new_project.sh 的 BASH_SOURCE 定位法同源）。
# 幂等：已存在的目标文件不覆盖、只警告；目录重复创建无害；尾声 validate 总会执行。
set -euo pipefail

DIR=""
TITLE=""
ID=""
while [ $# -gt 0 ]; do
  case "$1" in
    --title) TITLE="${2:?--title 需要一个值}"; shift 2 ;;
    --id)    ID="${2:?--id 需要一个值}"; shift 2 ;;
    *)  if [ -z "$DIR" ]; then DIR="$1"; shift
        else echo "错误：多余参数「$1」" >&2; exit 1; fi ;;
  esac
done
[ -n "$DIR" ] || { grep '^# 用法' "$0" | head -1 >&2; exit 1; }
TITLE="${TITLE:-未定标题}"
ID="${ID:-$(basename "$DIR")}"
DATE="$(date +%F)"

# ── 自定位与源件前置检查（缺件明确报错） ─────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"          # skills/tech-talk/
ROOT="$(cd "$SKILL_DIR/../.." && pwd)"             # video_tools 仓根
VTX="$ROOT/vtx/vtx.py"

MISSING=0
need() { [ -f "$1" ] || { echo "错误：缺少源文件 $1" >&2; MISSING=1; }; }
for f in "$SKILL_DIR/templates/00-structure.md" \
         "$SKILL_DIR/templates/02-script.md" \
         "$SKILL_DIR/templates/03-assets-plan.md" \
         "$SKILL_DIR/templates/06-teleprompter.md" \
         "$SKILL_DIR/assets/brief-template.md" \
         "$SKILL_DIR/assets/presets/timeline.skeleton.json" \
         "$SKILL_DIR/assets/presets/placeholders/placeholder_av.mp4" \
         "$SKILL_DIR/assets/presets/placeholders/placeholder_audio.wav" \
         "$SKILL_DIR/assets/presets/placeholders/placeholder_gfx.html" \
         "$SKILL_DIR/assets/presets/gates.json" \
         "$SKILL_DIR/assets/presets/mixing.json" \
         "$VTX"; do need "$f"; done
[ "$MISSING" -eq 0 ] || { echo "前置检查失败：以上源件缺失（模板包不完整，先修复 skills/tech-talk/）" >&2; exit 1; }

# ── 渲染器：只替换项目级占位符，内容级 {{}} 留给创作者 ────────────
# 映射：{{标题}}/{{项目名}}/{{选题名}}→标题，{{id}}→项目代号，{{日期}}→日期
render() {  # render <src> <dst>
  python3 - "$1" "$2" "$TITLE" "$ID" "$DATE" <<'PY'
import sys
src, dst, title, pid, date = sys.argv[1:6]
text = open(src, encoding="utf-8").read()
for k in ("标题", "项目名", "选题名"):
    text = text.replace("{{%s}}" % k, title)
text = text.replace("{{id}}", pid).replace("{{日期}}", date)
open(dst, "w", encoding="utf-8").write(text)
PY
}

# ── 1. 目录树 ────────────────────────────────────────────────────
mkdir -p "$DIR"/assets/{aroll,bgm,gfx,media} "$DIR"/gfx "$DIR"/cache "$DIR"/out "$DIR"/cover "$DIR"/docs "$DIR"/presets

# ── 2. 工程文档模板四件 + 01-brief（渲染后落位） ──────────────────
copy_render() {  # copy_render <src> <dst>（已存在则警告跳过）
  local src="$1" dst="$2"
  if [ -e "$dst" ]; then
    echo "⚠ 已存在，不覆盖：$dst"
  else
    render "$src" "$dst"
    echo "✓ 生成 $dst"
  fi
}
copy_render "$SKILL_DIR/templates/00-structure.md"   "$DIR/00-structure.md"
copy_render "$SKILL_DIR/templates/02-script.md"      "$DIR/02-script.md"
copy_render "$SKILL_DIR/templates/03-assets-plan.md" "$DIR/03-assets-plan.md"
copy_render "$SKILL_DIR/templates/06-teleprompter.md" "$DIR/06-teleprompter.md"
copy_render "$SKILL_DIR/assets/brief-template.md"    "$DIR/01-brief.md"

# ── 3. 占位素材 → assets/placeholders/（骨架 validate 依赖） ─────
for f in placeholder_av.mp4 placeholder_audio.wav placeholder_gfx.html; do
  if [ -e "$DIR/assets/placeholders/$f" ]; then
    echo "⚠ 已存在，不覆盖：$DIR/assets/placeholders/$f"
  else
    mkdir -p "$DIR/assets/placeholders"
    cp "$SKILL_DIR/assets/presets/placeholders/$f" "$DIR/assets/placeholders/$f"
    echo "✓ 生成 $DIR/assets/placeholders/$f"
  fi
done

# ── 4. timeline 骨架 → timeline.json（gfx/占位源相对路径改指 assets/placeholders/） ──
if [ -e "$DIR/timeline.json" ]; then
  echo "⚠ 已存在，不覆盖：$DIR/timeline.json"
else
  python3 - "$SKILL_DIR/assets/presets/timeline.skeleton.json" "$DIR/timeline.json" <<'PY'
import sys, json
src, dst = sys.argv[1:3]
tl = json.load(open(src, encoding="utf-8"))
# 骨架原位时占位件在 ./placeholders/；落位项目后在 assets/placeholders/
def fix(o):
    if isinstance(o, dict):
        return {k: fix(v) for k, v in o.items()}
    if isinstance(o, list):
        return [fix(v) for v in o]
    if isinstance(o, str) and o.startswith("placeholders/"):
        return "assets/placeholders/" + o[len("placeholders/"):]
    return o
tl = fix(tl)
tl.setdefault("_meta", {})["scaffolded"] = "由 skills/tech-talk/scripts/new_project.sh 自 presets/timeline.skeleton.json 实例化；实例化步骤见 _meta.instantiate_steps"
json.dump(tl, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY
  echo "✓ 生成 $DIR/timeline.json（占位源已改指 assets/placeholders/）"
fi

# ── 5. gates/mixing 预设 ─────────────────────────────────────────
for f in gates.json mixing.json; do
  if [ -e "$DIR/presets/$f" ]; then
    echo "⚠ 已存在，不覆盖：$DIR/presets/$f"
  else
    cp "$SKILL_DIR/assets/presets/$f" "$DIR/presets/$f"
    echo "✓ 生成 $DIR/presets/$f"
  fi
done

# ── 6. 尾声：validate 硬门 + 下一步指引 ──────────────────────────
echo
echo "── vtx validate（timeline 骨架 + 占位素材原位应通过）──"
python3 "$VTX" -t "$DIR/timeline.json" validate

cat <<EOF

✓ tech-talk 项目脚手架完成：$DIR（标题「$TITLE」/ 代号 $ID / $DATE）
下一步（按 SKILL.md 六阶段流程）：
  ① 调研立项（R0 起）：读 references/research.md，多智能体采集+score_topic 评分，
     按九节契约填写 01-brief.md（占位符已渲染标题与日期，内容级 {{}} 待填）→ 人工拍板
  ② 稿子与结构：01-brief 定稿后填 02-script / 00-structure / 03-assets-plan / 06-teleprompter
  ⑤ 时间线编排：删 timeline.json 内 ph_* 占位源与占位 clips/events，按帧预算铺真实编排
     （实例化步骤见其 _meta.instantiate_steps）；改后必跑：
     python3 $VTX -t $DIR/timeline.json validate
  预设已就位：presets/gates.json（项目内容门）/ presets/mixing.json（混音量化口径）
EOF
