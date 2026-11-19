"""Thin JSON-only adapter; never constructs Ctx or expands subtitles."""
import json
import sys
from pathlib import Path

from ..timeline_store import (EDIT_VIEW_WIRE_LIMIT, timeline_edit_view,
                              timeline_transaction, timeline_store_status,
                              timeline_store_capabilities, timeline_write_plan)
from ..timeline_structure import strict_loads


_REQUEST_MAX_BYTES = 8388608


def run_store_command(args, parser):
    allowed = {"command", "timeline", "request", "request_stdin", "subs_extra"}
    unused = [k for k, v in vars(args).items() if k not in allowed and v != parser.get_default(k)]
    extra = args.subs_extra
    has_request = args.request is not None or args.request_stdin
    usage = unused or (args.request is not None and args.request_stdin) or (
        has_request and extra not in ([], ["plan"], ["edit-view"])) or (
        extra == ["edit-view"] and not args.request_stdin) or (
        not has_request and extra not in (["status"], ["recover"], ["capabilities"]))
    if usage:
        result = dict(schema="framivra.timeline.store/v1", ok=False, code="cli.arguments",
                      message="use store [plan] (--request PATH | --request-stdin) | store edit-view --request-stdin | store status | store recover | store capabilities; no render/dry-run flags")
        code = 2
    else:
        if has_request:
            try:
                if args.request_stdin:
                    # Read the existing FD0 stream (including sockets), not a
                    # reopened /dev/stdin path. Wait for EOF before parsing;
                    # one extra byte detects oversize without an unbounded read.
                    payload = sys.stdin.buffer.read(_REQUEST_MAX_BYTES + 1)
                    if len(payload) > _REQUEST_MAX_BYTES:
                        raise ValueError("stdin request exceeds 8388608 UTF-8 bytes")
                else:
                    payload = Path(args.request).expanduser().read_bytes()
                request = strict_loads(payload)
                if extra != ["edit-view"] and isinstance(request, dict) and request.get("protocol") == 2 and request.get("payload_mode") == "exact-import":
                    # Explicit file only (no stdin, URLs, dirs, symlink or hardlink
                    # leaves). Freeze once before digest/lock; journal has bytes,
                    # not a mutable source path. Relative to caller cwd.
                    from ..timeline_store import _read_path
                    if "payload_file" not in request or "raw_bytes" in request or "raw_utf8" in request:
                        raise ValueError("exact CLI request requires payload_file only")
                    request = dict(request)
                    payload_file = request.pop("payload_file")
                    if not isinstance(payload_file, str) or not payload_file:
                        raise ValueError("payload_file must be a nonempty local file path")
                    request["raw_bytes"] = _read_path(payload_file)
                execute = {"plan": timeline_write_plan, "edit-view": timeline_edit_view}.get(
                    extra[0] if extra else None, timeline_transaction)
                result = execute(args.timeline, request)
            except (OSError, ValueError, UnicodeError, RecursionError) as error:
                result = dict(schema="framivra.timeline.store/v1", ok=False, code="input.request", message=str(error))
        elif extra == ["capabilities"]:
            result = dict(timeline_store_capabilities(), request_transport={
                "stdin": {"flag": "--request-stdin", "max_bytes": _REQUEST_MAX_BYTES}},
                edit_view={"command": "edit-view", "read_schema": "framivra.timeline.read/v1",
                           "wire_limit_bytes": EDIT_VIEW_WIRE_LIMIT, "preload_stdin": True})
        else:
            result = timeline_store_status(args.timeline, recover=extra == ["recover"])
        code = 0 if result["ok"] else 1
    text = json.dumps(result, ensure_ascii=True, allow_nan=False)
    if extra == ["edit-view"] and len(text) + 1 > EDIT_VIEW_WIRE_LIMIT:
        # Fresh success is preflighted by the store. Bound even pathological
        # rejection diagnostics without turning a possible commit into refusal.
        result = dict(schema="framivra.timeline.store/v1", ok=False,
                      code="outcome_unknown" if result["ok"] else result["code"],
                      message="edit-view response exceeded the wire limit")
        text = json.dumps(result)
        code = 1
    print(text)
    return code
