#!/usr/bin/env bash
# tech-talk 模板包 · 一键项目脚手架
# 用途：为科技口播视频新建完整项目目录——工程文档模板四件 + 01-brief + timeline 骨架（占位素材
#       原位可 validate）+ gates/mixing 预设，尾声自动跑 vtx validate 并打印下一步指引。
# 用法：bash new_project.sh <项目目录> [--title "视频标题"] [--id 项目代号]
# 依赖：bash + coreutils（mkdir/cp/date）+ python3（渲染占位符与 vtx validate）。
#       无需安装：vtx 路径自定位——本脚本位于 <video_tools>/templates/tech-talk/scripts/，
#       仓根 = 脚本向上三级（与 scripts/new_project.sh 的 BASH_SOURCE 定位法同源）。
# 幂等：已存在的目标文件不覆盖、只警告；目录重复创建无害；正常尾声仍执行 validate。
# 新建 TL 经公共事务；失败可留非 TL 文件/sidecar，validate 失败不回滚已提交 TL。
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
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"          # templates/tech-talk/
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
[ "$MISSING" -eq 0 ] || { echo "前置检查失败：以上源件缺失（模板包不完整，先修复 templates/tech-talk/）" >&2; exit 1; }

# 正常从真实仓根导入公共 API；恢复调用 cwd 后才解释 DIR（不词法折叠 symlink/..）。
CALLER_CWD="$(pwd -P)"
(cd "$ROOT" && python3 - "$CALLER_CWD" "$SKILL_DIR" "$VTX" "$DIR" "$TITLE" "$ID" "$DATE" <<'PY'
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from vtx.api import parse_write_json, read_timeline_for_write, timeline_transaction

caller, skill, vtx, directory, title, pid, date = sys.argv[1:8]
os.chdir(caller)

def run(*args):
    subprocess.run(args, check=True)

def generated(dst):
    print(f"✓ 生成 {dst}", flush=True)

def skip(dst):
    print(f"⚠ 已存在，不覆盖：{dst}", flush=True)

def fix(o):
    if isinstance(o, dict):
        return {k: fix(v) for k, v in o.items()}
    if isinstance(o, list):
        return [fix(v) for v in o]
    if isinstance(o, str) and o.startswith("placeholders/"):
        return "assets/placeholders/" + o[len("placeholders/"):]
    return o

# 1. 原目录树；父目录就绪后、任何 render/cp 前固定 leaf 状态与缺失请求。
run("mkdir", "-p", *(f"{directory}/{p}" for p in (
    "assets/aroll", "assets/bgm", "assets/gfx", "assets/media", "gfx",
    "cache", "out", "cover", "docs", "presets")))
target = f"{directory}/timeline.json"
request = None
try:
    existing = os.path.exists(target)  # 保留原 -e skip；不 strict 读已有工程。
    if not existing:
        if os.path.lexists(target):
            raise ValueError("timeline.json 是断链 symlink，拒绝创建")
        snapshot = read_timeline_for_write(target)
        if any(snapshot[k] is not None for k in ("raw_bytes", "revision", "document")):
            raise ValueError("timeline.json 在首次缺失观察后出现，拒绝覆盖；请检查后独立重跑")
        op_id = "tech-talk-create-" + uuid.uuid4().hex
        raw = Path(f"{skill}/assets/presets/timeline.skeleton.json").read_bytes()
        tl = fix(parse_write_json(raw))
        if not isinstance(tl, dict):
            raise ValueError("timeline 骨架必须是 JSON object")
        tl.setdefault("_meta", {})["scaffolded"] = "由 templates/tech-talk/scripts/new_project.sh 自 presets/timeline.skeleton.json 实例化；实例化步骤见 _meta.instantiate_steps"
        request = dict(protocol=2, operation="create", op_id=op_id,
                       base_revision=None, identity_mode="preserve-unmanaged",
                       payload_mode="document", purpose="create", document=tl)
except Exception as error:
    print(f"错误：timeline 创建前置失败：{error}；已建目录可能保留。", file=sys.stderr)
    sys.exit(1)

# 2. 工程文档四件 + brief；只替换项目级占位符，内容级 {{}} 保留。
for source, name in (("templates/00-structure.md", "00-structure.md"),
                     ("templates/02-script.md", "02-script.md"),
                     ("templates/03-assets-plan.md", "03-assets-plan.md"),
                     ("templates/06-teleprompter.md", "06-teleprompter.md"),
                     ("assets/brief-template.md", "01-brief.md")):
    dst = f"{directory}/{name}"
    if os.path.exists(dst):
        skip(dst)
    else:
        text = Path(f"{skill}/{source}").read_text(encoding="utf-8")
        for key in ("标题", "项目名", "选题名"):
            text = text.replace("{{%s}}" % key, title)
        text = text.replace("{{id}}", pid).replace("{{日期}}", date)
        Path(dst).write_text(text, encoding="utf-8")
        generated(dst)

# 3. 保留真实 cp 阶段与顺序；early snapshot/request 一直在本进程中。
for name in ("placeholder_av.mp4", "placeholder_audio.wav", "placeholder_gfx.html"):
    dst = f"{directory}/assets/placeholders/{name}"
    if os.path.exists(dst):
        skip(dst)
    else:
        run("mkdir", "-p", f"{directory}/assets/placeholders")
        run("cp", f"{skill}/assets/presets/placeholders/{name}", dst)
        generated(dst)

# 4. 只提交同一个冻结的 create；不 late-skip、重取 base、换 op_id 或重试。
if existing:
    skip(target)
else:
    try:
        result = timeline_transaction(target, request)
    except Exception as error:
        result = {"ok": False, "code": "exception", "error": str(error)}
    if not result.get("ok"):
        print("错误：timeline 创建未确认成功：" + json.dumps(result, ensure_ascii=False), file=sys.stderr)
        print("保留以下 op_id/原请求供人工核查或恢复；勿自动换 id 重试。非 TL 文件及 history/locks 可能已写，不代表项目回滚。", file=sys.stderr)
        print(json.dumps(request, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        sys.exit(1)
    generated(target + "（占位源已改指 assets/placeholders/）")

# 5. gates/mixing 预设。
for name in ("gates.json", "mixing.json"):
    dst = f"{directory}/presets/{name}"
    if os.path.exists(dst):
        skip(dst)
    else:
        run("cp", f"{skill}/assets/presets/{name}", dst)
        generated(dst)

# 6. 真实 validate；失败不能撤销已提交 TL，也不是整个项目的回滚。
print("\n── vtx validate（timeline 骨架 + 占位素材原位应通过）──", flush=True)
# CLI 会 abspath；先按文件系统顺序解析父路径，避免把 DIR 的 symlink/.. 词法折叠。
validate_target = str(Path(target).parent.resolve(strict=True) / Path(target).name)
validated = subprocess.run(["python3", vtx, "-t", validate_target, "validate"])
if validated.returncode:
    state = "本次 TL 已提交" if not existing else "已有 TL 已跳过"
    print(f"错误：validate 失败；{state}，非 TL 文件/缓存可能已写，项目未完成且未回滚。", file=sys.stderr)
    sys.exit(validated.returncode if validated.returncode > 0 else 1)
PY
)

cat <<EOF

✓ tech-talk 项目脚手架完成：${DIR}（标题「${TITLE}」/ 代号 $ID / ${DATE}）
下一步（按 SKILL.md 六阶段流程）：
#   gfx 动效起步：复制 <包>/assets/gfx-templates/ 六件模板（选型见 INDEX.md「叙事阶段→推荐模板」）到 <项目>/gfx/ 改文案与数据
  ① 调研立项（R0 起）：读 references/research.md，多智能体采集+score_topic 评分，
     按九节契约填写 01-brief.md（占位符已渲染标题与日期，内容级 {{}} 待填）→ 人工拍板
  ② 稿子与结构：01-brief 定稿后填 02-script / 00-structure / 03-assets-plan / 06-teleprompter
  ⑤ 时间线编排：删 timeline.json 内 ph_* 占位源与占位 clips/events，按帧预算铺真实编排
     （实例化步骤见其 _meta.instantiate_steps）；改后必跑：
     python3 $VTX -t $DIR/timeline.json validate
  预设已就位：presets/gates.json（项目内容门）/ presets/mixing.json（混音量化口径）
EOF
