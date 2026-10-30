#!/usr/bin/env python3
"""Subtitle-only adapter to the public timeline publisher.

The old four positional arguments and summary keys remain supported. Standalone
calls capture a base at helper entry; callers doing slow work MUST pass their
original snapshot and expected_base. The raw stdin CLI uses the public strict
write parser before ordinary JSON decoding; Python callers supply already-decoded
values and cannot recover numeric precision previously lost upstream.
No late read upgrades that base. Keep the
snapshot, op_id and inputs to retry exactly the same operation after an uncertain
outcome; alternatively inspect/recover that operation via vtx store.
"""
import copy
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path


def _api():
    # Prefer an installed kernel, including isolated Python consumers. Source
    # scripts have no repository root on sys.path; fallback only if vtx is absent.
    try:
        from vtx import api
    except ModuleNotFoundError as exc:
        if exc.name != "vtx":
            raise
        sys.path.append(str(Path(__file__).resolve().parents[2]))
        from vtx import api
    return api


def read_snapshot(path):
    try:
        snap = _api().read_timeline_for_write(path)
    except ValueError as exc:
        raise RuntimeError("timeline.json 不可安全写入（%s），已保持原文件不动：%s"
                           % (exc, path)) from exc
    if snap["document"] is None:
        raise RuntimeError("timeline.json 不存在：%s" % path)
    if snap["document"].get("subtitles_src"):
        raise ValueError("subtitles_src 外部字幕存在；拒绝内联合并，不删除外部路径")
    return snap


def _validate_subs(subs):
    if not isinstance(subs, list):
        raise ValueError("subtitles 必须是数组")
    for i, s in enumerate(subs):
        if not isinstance(s, dict) or not {"in", "out", "text"} <= set(s):
            raise ValueError("第 %d 条缺少 in/out/text 键" % (i + 1))
        if type(s["in"]) is not int or type(s["out"]) is not int:
            raise ValueError("第 %d 条 in/out 必须是整数帧" % (i + 1))
        if not isinstance(s["text"], str):
            raise ValueError("第 %d 条 text 必须是字符串" % (i + 1))
        if not 0 <= s["in"] < s["out"]:
            raise ValueError("第 %d 条违反 0 <= in < out" % (i + 1))


def prepare_subtitle_write(snapshot, new_subs, op_id):
    """Pure adapter: fixed IDs and exact subtitle collection intent, no IO."""
    _validate_subs(new_subs)
    tl = snapshot["document"]
    if tl.get("subtitles_src"):
        raise ValueError("subtitles_src 外部字幕存在；拒绝内联合并，不删除外部路径")
    managed = snapshot["identity_mode"] == "managed"
    request = dict(protocol=2, operation="reconcile" if managed else "replace",
                   op_id=op_id, base_revision=snapshot["revision"],
                   identity_mode=snapshot["identity_mode"], payload_mode="document",
                   purpose="edit" if managed else "legacy-write")
    if not managed:
        doc = copy.deepcopy(tl)
        doc["subtitles"] = copy.deepcopy(new_subs)
        request["document"] = doc
        return request
    old = {s["id"]: s for s in tl.get("subtitles", [])}
    retained = {s["id"] for s in new_subs if s.get("id") in old}
    operations = [{"op": "block.remove", "id": i} for i in old if i not in retained]
    new_ids = []
    seen = set()
    for index, sub in enumerate(new_subs):
        data = copy.deepcopy(sub)
        identity = data.get("id")
        if identity in old:
            # An existing stable ID means preserve, not positional reassignment.
            # Strict typed comparison also prevents True==1 extension drift.
            if json.dumps(data, sort_keys=True) != json.dumps(old[identity], sort_keys=True):
                raise ValueError("已有字幕 ID %s 内容变化：请用显式编辑；不可按位置重签" % identity)
            operations.append(dict(op="block.move", id=identity,
                                   track_id="subtitles", at=data["in"]))
        else:
            if identity is None:
                identity = "asr-" + uuid.uuid5(uuid.NAMESPACE_URL, op_id + ":" + str(index)).hex
                data["id"] = identity
            new_ids.append(identity)
            operations.append(dict(op="block.insert", track_id="subtitles", data=data))
        if identity in seen:
            raise ValueError("重复字幕 ID：%s" % identity)
        seen.add(identity)
    request.update(owner="asr.subtitles", owned_ids=list(old), new_ids=new_ids,
                   operations=operations)
    return request


class TimelineWriteError(RuntimeError):
    def __init__(self, receipt, request, backup=None):
        self.receipt, self.request, self.backup = receipt, request, backup
        code = receipt.get("code", "unknown")
        boundary = ("结果未知，timeline 可能已提交；用同一 snapshot/输入/op_id 重试或 store recover，不能盲提交新 op"
                    if code in ("outcome_unknown", "recovery_required") else
                    "本次未改 timeline；耗时产物或字幕备份可能已生成，不自动 rebase/重试")
        super().__init__("%s: %s；op_id=%s；%s；backup=%s" %
                         (code, receipt.get("message", ""), request["op_id"], boundary, backup))


def merge_subtitles(timeline_path, new_subs, dry_run=False, reports_dir=None, *,
                    snapshot=None, expected_base=None, op_id=None):
    """Legacy helper captures on entry only when no early snapshot/base supplied.

    expected_base without a snapshot fails closed: rereading now could silently
    turn slow caller intent into a late-base overwrite. Summary shape is stable.
    """
    tl_path = Path(timeline_path).expanduser()
    if snapshot is None:
        if expected_base is not None:
            raise ValueError("expected_base 需要同次早期 snapshot；拒绝 late-base 降级")
        snapshot = read_snapshot(tl_path)
    elif expected_base is None or expected_base != snapshot["revision"]:
        raise ValueError("snapshot 必须携带相同 expected_base")
    op_id = op_id or "asr-" + uuid.uuid4().hex
    _validate_subs(new_subs)
    if snapshot["document"].get("subtitles_src"):
        raise ValueError("subtitles_src 外部字幕存在；拒绝内联合并，不删除外部路径")
    old_subs = snapshot["document"].get("subtitles", [])
    if json.dumps(old_subs, sort_keys=True) == json.dumps(new_subs, sort_keys=True):
        # Read-only equality fence, not a late-base rebase or a write attempt.
        if read_snapshot(tl_path)["revision"] != snapshot["revision"]:
            raise RuntimeError("conflict: timeline早期base已变化；timeline未改；op_id=%s" % op_id)
        return {"old_n": len(old_subs), "new_n": len(new_subs), "backup": None,
                "dry_run": bool(dry_run)}
    request = prepare_subtitle_write(snapshot, new_subs, op_id)
    plan = _api().timeline_write_plan(tl_path, request)
    if not plan["ok"]:
        raise TimelineWriteError(plan, request)
    old_subs = snapshot["document"].get("subtitles", [])
    backup = None
    if not dry_run and not plan.get("replayed"):
        rdir = Path(reports_dir) if reports_dir else tl_path.parent / "cache" / "reports"
        rdir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-%d" % os.getpid()
        payload = json.dumps(old_subs, ensure_ascii=False, indent=1) + "\n"
        # mkstemp underneath NamedTemporaryFile uses O_EXCL, including on a
        # collision with a symlink. Close successfully before publishing main.
        # On IO failure retain the unreturned file: unlinking by name could
        # remove a foreign replacement. No failed backup authorizes a publish.
        stream = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=rdir,
                                             prefix="subtitles_backup_%s-" % stamp,
                                             suffix=".json", delete=False)
        try:
            stream.write(payload)
            stream.flush()
        except BaseException as error:
            try:
                stream.close()
            except BaseException as close_error:
                raise error from close_error
            raise
        else:
            stream.close()
        # Keep the caller's relative/absolute reports_dir spelling as before.
        backup = rdir / Path(stream.name).name
    if not dry_run:
        result = _api().timeline_transaction(tl_path, request)
        if not result["ok"]:
            raise TimelineWriteError(result, request, str(backup) if backup else None)
    return {"old_n": len(old_subs), "new_n": len(new_subs),
            "backup": str(backup) if backup else None, "dry_run": bool(dry_run)}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="stdin subtitles json → 合并进 timeline.json")
    ap.add_argument("-t", "--timeline", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        new_subs = _api().parse_write_json(sys.stdin.buffer.read())
        result = merge_subtitles(args.timeline, new_subs, dry_run=args.dry_run)
    except (RuntimeError, ValueError, OSError) as exc:
        print("✗ %s" % exc)
        sys.exit(1)
    print("✓ 合并完成：%s" % json.dumps(result, ensure_ascii=False)
          if not args.dry_run else "✓ dry-run：未写盘。%s" % json.dumps(result, ensure_ascii=False))
