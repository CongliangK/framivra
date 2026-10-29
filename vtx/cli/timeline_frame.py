"""JSON-only adapter for explicitly authorized timeline frame rendering."""
import json
import signal
import threading
from contextlib import contextmanager


@contextmanager
def _cancel_on_sigterm():
    """CLI-owned main thread only; never mutate Python API caller handlers."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGTERM)
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)

from .timeline import _integer
from ..timeline import _error
from ..timeline_frame import frame_timeline, _SCHEMA


def run_frame_command(args, parser):
    allowed = {"command", "timeline", "subs_extra", "frame", "expected_content_version",
               "render", "max_png_bytes"}
    unused = [key for key, value in vars(args).items()
              if key not in allowed and value != parser.get_default(key)]
    if args.subs_extra != ["frame"] or unused:
        result = {"schema": _SCHEMA, "operation": "frame", "ok": True,
                  "content_version": None, "reference": None, "image": None, "diagnostics": []}
        _error(result, "cli.arguments", "", "expected timeline frame; unsupported arguments: " + ", ".join(unused))
        code = 2
    else:
        kwargs = {"frame": _integer(args.frame), "render": args.render,
                  "expected_content_version": args.expected_content_version}
        if args.max_png_bytes is not None:
            kwargs["max_png_bytes"] = _integer(args.max_png_bytes)
        try:
            with _cancel_on_sigterm():
                result = frame_timeline(args.timeline, **kwargs)
        except KeyboardInterrupt:
            result = {"schema": _SCHEMA, "operation": "frame", "ok": True,
                      "content_version": None, "reference": None, "image": None, "diagnostics": []}
            _error(result, "render.cancelled", "", "frame rendering cancelled; no success returned")
        code = 0 if result["ok"] else 1
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return code
