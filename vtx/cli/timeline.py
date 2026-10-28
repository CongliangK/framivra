"""JSON-only CLI adapter for the public read API. No project-side outputs."""
import json
import re

from ..timeline import (_error, _result, inspect_timeline, query_timeline,
                        timeline_capabilities)


def _integer(value):
    # Preserve invalid values so Python and CLI share the API's diagnostics.
    if isinstance(value, str) and re.fullmatch(r"[+-]?[0-9]+", value):
        try:
            return int(value)
        except ValueError:
            pass
    return value


def run_read_command(args, parser):
    """Print exactly the API result. Exit 0 success, 1 input error, 2 usage."""
    query_fields = {"family", "track_index", "block_index", "frame", "interval"}
    allowed = {"command", "timeline", "dry_run"}
    if args.command == "query":
        allowed |= query_fields
    unused = [key for key, value in vars(args).items()
              if key not in allowed and value != parser.get_default(key)
              and not (key == "subs_extra" and not value)]
    if unused:
        result = _result(args.command)
        _error(result, "cli.arguments", "", "unsupported arguments: " + ", ".join(unused))
        code = 2
    else:
        if args.command == "capabilities":
            result = timeline_capabilities()
        elif args.command == "inspect":
            result = inspect_timeline(args.timeline)
        else:
            result = query_timeline(
                args.timeline, family=args.family,
                track_index=_integer(args.track_index), block_index=_integer(args.block_index),
                frame=_integer(args.frame),
                interval=[_integer(v) for v in args.interval] if args.interval is not None else None)
        code = 0 if result["ok"] else 1
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return code
