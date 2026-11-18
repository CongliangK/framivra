"""Exact, JSON-only CLI ingress for the Linux preview-window candidate."""
import argparse
from contextlib import contextmanager, redirect_stdout
import json
import signal
import sys
import threading

from .timeline import _integer
from ..timeline_preview_window import WindowError, error_result, preview_window


def matches(argv):
    # The group and operation are adjacent, after the optional global timeline
    # selector. Do not hijack filenames or positional arguments of other commands.
    if argv[:1] in (['-t'], ['--timeline']):
        argv = argv[2:]
    elif argv and argv[0].startswith(('--timeline=', '-t=')):
        argv = argv[1:]
    return argv[:2] == ['timeline', 'preview-window']


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise WindowError('cli.arguments', message)


@contextmanager
def _cancellation():
    if threading.current_thread() is not threading.main_thread():
        raise WindowError('render.unsettled', 'preview window requires the CLI main thread')
    cancelled = False

    def mark(_signum, _frame):
        nonlocal cancelled
        cancelled = True

    def check():
        if cancelled:
            raise WindowError('render.cancelled', 'preview window cancelled; no success returned')

    previous = {}
    try:
        # Do not unwind out of a kernel subprocess/worker thread midway through
        # its own reaping. Direct commands poll; inherited kernel commands settle
        # synchronously before the next Context/checkpoint, and Host also owns
        # the process group. A hard kill cannot certify cleanup.
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, mark)
        yield check
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def run_preview_window_command(argv):
    parser = _Parser(allow_abbrev=False, add_help=False)
    parser.add_argument('-t', '--timeline', required=True, action='append')
    parser.add_argument('group', choices=['timeline'])
    parser.add_argument('operation', choices=['preview-window'])
    parser.add_argument('--frame', required=True, action='append')
    parser.add_argument('--expected-content-version', required=True, action='append')
    for option in ('--render', '--include-media', '--prepare-inputs'):
        parser.add_argument(option, required=True, action='count')
    try:
        args = parser.parse_intermixed_args(argv)
        if (any(len(v) != 1 for v in (args.timeline, args.frame, args.expected_content_version))
                or (args.render, args.include_media, args.prepare_inputs) != (1, 1, 1)):
            raise WindowError('cli.arguments', 'preview window flags and selectors must be supplied exactly once')
        with _cancellation() as check_cancelled, redirect_stdout(sys.stderr):
            result = preview_window(args.timeline[0], start=_integer(args.frame[0]),
                                    expected_content_version=args.expected_content_version[0],
                                    check_cancelled=check_cancelled)
            if result['ok']:
                check_cancelled()
        code = 0 if result['ok'] else 1
    except WindowError as error:
        result = error_result(error.code, str(error)[:2000])
        code = 2 if error.code == 'cli.arguments' else 1
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return code
