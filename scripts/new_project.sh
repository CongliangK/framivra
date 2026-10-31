#!/usr/bin/env bash
# 新建 vtx 视频项目脚手架：目录结构 + 模板 timeline.json
# 用法：new_project.sh <项目目录> [时长秒，默认30]
set -euo pipefail
DIR="${1:?用法: new_project.sh <项目目录> [时长秒]}"
SECS="${2:-30}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
CALLER_CWD="$(pwd -P)"
# 从真实源码仓根导入公共 API，不依赖安装；随后恢复调用目录解释 DIR。
(cd "$ROOT" && python3 - "$CALLER_CWD" "$ROOT" "$DIR" "$SECS" <<'PY'
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

from vtx.api import read_timeline_for_write, timeline_write_plan, timeline_transaction

caller, root, directory, seconds = sys.argv[1:5]
os.chdir(caller)
try:
    if not re.fullmatch(r"[0-9]+", seconds):
        raise ValueError("时长必须是 ASCII 十进制整数且 >= 7 秒")
    # 不把 Python 的十进制字符串保护阈值变成项目时长上限。
    if hasattr(sys, "set_int_max_str_digits"):
        sys.set_int_max_str_digits(0)
    frames = int(seconds, 10) * 30
    if frames < 210:
        raise ValueError("时长必须 >= 7 秒（固定样例最长事件至第 200 帧）")
    # 仅建观察目标所需父目录；不得先词法折叠 symlink/..。
    Path(directory).mkdir(parents=True, exist_ok=True)
    project = Path(directory).resolve(strict=True)
    target = project / "timeline.json"
    snapshot = read_timeline_for_write(target)
    if snapshot["identity_mode"] != "preserve-unmanaged":
        raise ValueError("已有 managed timeline，拒绝覆盖或自动 adoption")
    document = {
        "version": 2,
        "project": {"fps": 30, "width": 1920, "height": 1080, "duration_frames": frames},
        "sources": [{"id": "v01", "path": "assets/v01.mp4", "transform": "crop_center_scale"}],
        "video_tracks": [
            {"name": "aroll", "kind": "aroll", "clips": [
                {"src": "v01", "src_in": 0, "src_out": frames, "dst_in": 0, "dur": frames}]},
            {"name": "gfx", "kind": "gfx", "clips": [
                {"src": "gfx/gfxA.html", "dst_in": 0, "dur": 192}]},
        ],
        "subtitles": [{"in": 12, "out": 88, "text": "示例字幕"}],
        "audio_tracks": [
            {"name": "bed", "events": [
                {"src": "v01", "in": 0, "out": frames, "volume": 0.25, "loop": True}]},
            {"name": "voice", "events": [
                {"src": "v01", "src_in": 0, "in": 12, "out": 200, "volume": 0.9}]},
        ],
    }
    creating = snapshot["raw_bytes"] is None
    request = dict(protocol=2, operation="create" if creating else "replace",
                   op_id="generic-creator-" + uuid.uuid4().hex,
                   base_revision=snapshot["revision"], identity_mode="preserve-unmanaged",
                   payload_mode="document", purpose="create" if creating else "legacy-write",
                   document=document)
    plan = timeline_write_plan(target, request)
    if not plan.get("ok"):
        raise ValueError("timeline_write_plan: " + json.dumps(plan, ensure_ascii=False))
except Exception as error:
    print(f"错误：timeline 前置失败：{error}；未复制辅助文件、未提交 TL；观察所需父目录可能保留。", file=sys.stderr)
    sys.exit(1)

# 固定 raw/base/candidate/op/request 后才复制；失败不提交 TL，辅助文件可部分保留。
try:
    for name in ("assets", "gfx", "cache", "out"):
        (project / name).mkdir(exist_ok=True)
    subprocess.run(["cp", f"{root}/vtx/timeline.schema.json", str(project / "timeline.schema.json")], check=True)
    gfx = project / "gfx/gfxA.html"
    # 保留旧 !-e && !-L 语义，含 dangling symlink。
    if not gfx.exists() and not gfx.is_symlink():
        subprocess.run(["cp", f"{root}/scripts/templates/gfx-starter.html", str(gfx)], check=True)
except Exception as error:
    print(f"错误：辅助文件准备失败：{error}；本次未提交 TL，旧 TL 未被本次覆盖/新 TL 未被本次创建；辅助文件可能部分保留。", file=sys.stderr)
    sys.exit(1)

# 同一冻结请求仅提交一次；不重读 base、不 rebase、不重试、不另行发布。
try:
    result = timeline_transaction(target, request)
except Exception as error:
    result = {"ok": False, "code": "outcome_unknown", "error": str(error)}
if not result.get("ok"):
    print("错误：timeline 提交未确认成功（可能已提交），不自动重试或回滚。", file=sys.stderr)
    print(json.dumps({"target": str(target), "request": request, "result": result}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
    print("保留以上原 op_id/请求；从源码仓根用 vtx.api.timeline_store_status(target, recover=False) 核查。恢复会写状态，须人工确认后使用 recover=True；勿换 id 重提。辅助文件/history/locks 可能保留，非目录级原子操作。", file=sys.stderr)
    sys.exit(1)
# 后续输出错误不改变已提交事实，不落入“未提交”处理。
print(f"✓ timeline 已提交：{target}（op_id={request['op_id']}）", flush=True)
PY
)
cat <<'EOF'
✓ 项目已创建。下一步：
  1. 素材放 assets/（CFR 30fps（D24 换基）、含音轨，竖版会自动中心裁 16:9）
  2. gfx/gfxA.html 为协议合规样页；先补齐素材，再改文案/配色；协议见 docs/skill/references/gfx.md
  3. 编辑 timeline.json 完成编排
  4. python3 "$HOME/.agents/skills/video/vtx/vtx.py" -t "<项目>/timeline.json" validate
  5. 校验通过后 ... all → out/final.mp4
EOF
