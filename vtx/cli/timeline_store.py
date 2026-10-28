"""Thin JSON-only adapter; never constructs Ctx or expands subtitles."""
import json
from pathlib import Path

from ..timeline_store import (timeline_transaction, timeline_store_status,
                              timeline_store_capabilities)
from ..timeline_structure import strict_loads


def run_store_command(args, parser):
    allowed = {"command", "timeline", "request", "subs_extra"}
    unused = [k for k, v in vars(args).items() if k not in allowed and v != parser.get_default(k)]
    extra = args.subs_extra
    usage = unused or (args.request is not None and bool(extra)) or (
        args.request is None and extra not in (["status"], ["recover"], ["capabilities"]))
    if usage:
        result = dict(schema="framivra.timeline.store/v1", ok=False, code="cli.arguments",
                      message="use store --request PATH | store status | store recover | store capabilities; no render/dry-run flags")
        code = 2
    else:
        if args.request is not None:
            try:
                request = strict_loads(Path(args.request).expanduser().read_bytes())
                result = timeline_transaction(args.timeline, request)
            except (OSError, ValueError, UnicodeError, RecursionError) as error:
                result = dict(schema="framivra.timeline.store/v1", ok=False, code="input.request", message=str(error))
        elif extra == ["capabilities"]:
            result = timeline_store_capabilities()
        else:
            result = timeline_store_status(args.timeline, recover=extra == ["recover"])
        code = 0 if result["ok"] else 1
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return code
