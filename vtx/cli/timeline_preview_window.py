"""Exact, JSON-only CLI ingress for the Linux preview-window candidate."""
import argparse
from contextlib import contextmanager, redirect_stdout
import json
import os
import re
import signal
import sys
import threading

from .timeline import _integer
from ..runtime import preview_window_stream as stream
from ..timeline_preview_window import WARM_SVG_SCHEMA, WIRE_LIMIT, WindowError, error_result, preview_window


# Private launcher interface, not proof of qualification from the ordinary CLI.
FIXED_WHEEL_LAUNCHER = 'framivra.preview-window.fixed-wheel/v1'
INPUT_EPOCH_ABI = 'framivra.preview-window.inputs/v1'
WARM_SVG_MEDIA_ABI = 'framivra.preview-window.warm-svg-media/v1'
PROGRAM_AUDIO_ABI = 'framivra.preview-window.program-audio/v1'
FIRST_FRAME_ABI = stream.ABI
MANAGED_FONTS_ABI = 'framivra.preview-window.managed-fonts/v1'
_QUALIFICATION_KEYS = ('launcher', 'wheel_sha256', 'runtime_projection_sha256')


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


def run_preview_window_command(argv, *, qualification=None, managed_font_profile=None):
    """Only a trusted launcher supplies qualification; argv cannot select it."""
    code, wire = _command(argv, qualification, managed_font_profile=managed_font_profile)
    print(wire)
    return code


def run_first_frame_command(argv, *, qualification, nonce, reverify_profile, emit, managed_font_profile=None):
    """Fixed-wheel launcher ABI; ordinary CLI ingress cannot select it."""
    if qualification is None or not stream.NONCE.fullmatch(nonce):
        raise WindowError('input_epoch.unavailable', 'first frame requires a qualified launcher and nonce')
    return _command(argv, qualification, first_frame=(nonce, reverify_profile, emit),
                    managed_font_profile=managed_font_profile)


def _command(argv, qualification, *, first_frame=None, managed_font_profile=None):
    parser = _Parser(allow_abbrev=False, add_help=False)
    parser.add_argument('-t', '--timeline', required=True, action='append')
    parser.add_argument('group', choices=['timeline'])
    parser.add_argument('operation', choices=['preview-window'])
    parser.add_argument('--frame', required=True, action='append')
    parser.add_argument('--expected-content-version', required=True, action='append')
    for option in ('--render', '--include-media', '--prepare-inputs', '--read-warm-svg', '--use-program-audio'):
        parser.add_argument(option, action='count', default=0)
    svg_mode = None
    try:
        args = parser.parse_intermixed_args(argv)
        flags = (args.render, args.include_media, args.prepare_inputs, args.read_warm_svg)
        if (any(len(v) != 1 for v in (args.timeline, args.frame, args.expected_content_version))
                or flags not in ((1, 1, 1, 0), (0, 0, 0, 1), (1, 1, 1, 1))):
            raise WindowError('cli.arguments', 'select the complete media flags, --read-warm-svg, or both')
        if first_frame is not None and (flags != (1, 1, 1, 0) or args.use_program_audio):
            raise WindowError('cli.arguments', 'first-frame transport requires ordinary media flags only')
        if args.use_program_audio:
            suffix = ['--render', '--include-media', '--prepare-inputs']
            if args.read_warm_svg:
                suffix.append('--read-warm-svg')
            suffix.append('--use-program-audio')
            if args.use_program_audio != 1 or not args.render or argv[-len(suffix):] != suffix:
                raise WindowError('cli.arguments', 'program audio requires the complete fixed media flag sequence')
            if qualification is None:
                raise WindowError('audio.program_unsupported', 'program audio requires the fixed wheel launcher')
        if args.read_warm_svg:
            svg_mode = 'svg-media' if args.render else 'svg-read'
        if svg_mode is not None and qualification is None:
            raise WindowError('gfx.unsupported', 'warm SVG read requires the fixed wheel launcher')
        if qualification is not None and (set(qualification) != set(_QUALIFICATION_KEYS)
                or qualification['launcher'] != FIXED_WHEEL_LAUNCHER
                or any(not isinstance(qualification[k], str) or not re.fullmatch(r'[0-9a-f]{64}', qualification[k])
                       for k in _QUALIFICATION_KEYS[1:])):
            raise WindowError('input_epoch.unavailable', 'unverified fixed wheel qualification')
        if managed_font_profile is not None and (qualification is None or svg_mode or args.use_program_audio):
            raise WindowError('environment.unsupported', 'managed fonts require the ordinary managed window')
        with _cancellation() as check_cancelled, redirect_stdout(sys.stderr):
            result = preview_window(args.timeline[0], start=_integer(args.frame[0]),
                                    expected_content_version=args.expected_content_version[0],
                                    check_cancelled=check_cancelled, _qualification=qualification,
                                    _svg_mode=svg_mode, _program_audio=bool(args.use_program_audio),
                                     **({"_managed_font_profile": managed_font_profile} if managed_font_profile is not None else {}),
                                    **({"_first_frame": first_frame} if first_frame is not None else {}))
            if result['ok']:
                if svg_mode == 'svg-read':
                    result.update(qualification)
                elif qualification is not None:
                    epoch = result.get('input_epoch')
                    if not isinstance(epoch, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', epoch):
                        raise WindowError('input_epoch.unavailable', 'missing current input proof')
                    if svg_mode == 'svg-media' and result.get('warm') is not True:
                        raise WindowError('gfx.unsupported', 'missing warm SVG media proof')
                    if args.use_program_audio and result.get('program_audio') is not True:
                        raise WindowError('audio.program_unsupported', 'missing whole-program audio proof')
                    result.update(qualification)
                check_cancelled()
        code = 0 if result['ok'] else 1
    except WindowError as error:
        result = error_result(error.code, str(error)[:2000])
        code = 2 if error.code == 'cli.arguments' else 1
    if svg_mode == 'svg-read':
        result['schema'] = WARM_SVG_SCHEMA
    wire = json.dumps(result, ensure_ascii=True, allow_nan=False)
    if len(wire) + 1 > WIRE_LIMIT:
        code = 1
        result = error_result('output.limit', 'preview window exceeds JSON wire limit')
        wire = json.dumps(result)
    return code, result if first_frame is not None else wire


def _isolated_worker(args):
    # Invoked only by the fixed bootstrap argv. The renderer checks PID-1,
    # privilege state, the live mounts and the inherited input fingerprint.
    timeline, start, version, fingerprint, mode, *extra = args
    if mode.startswith('first-frame:'):
        nonce = mode.split(':', 1)[1]
        if extra or os.getpid() != 1 or not stream.NONCE.fullmatch(nonce):
            raise WindowError('input_epoch.unavailable', 'first frame requires an isolated protocol writer')
        return _stream_worker(timeline, start, version, fingerprint, nonce)
    program = mode in ('program-audio', 'svg-media-program-audio')
    mode = 'svg-media' if mode == 'svg-media-program-audio' else mode
    has_pack = mode in ('svg-read', 'svg-media')
    if (mode not in ('input-epoch', 'unqualified', 'svg-plan', 'svg-read', 'svg-media', 'program-audio')
            or len(extra) != int(has_pack) + int(program)):
        raise WindowError('input_epoch.unavailable', 'unknown isolated input proof mode')
    with _cancellation() as check_cancelled, redirect_stdout(sys.stderr):
        result = preview_window(timeline, start=_integer(start), expected_content_version=version,
                                check_cancelled=check_cancelled, _expected_inputs=fingerprint,
                                _epoch=program or mode in ('input-epoch', 'svg-media'),
                                _svg_mode=mode if mode.startswith('svg-') else None,
                                _relative=extra[0] if has_pack else None,
                                _program_manifest=extra[-1] if program else None)
        if result['ok']:
            check_cancelled()
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return 0 if result['ok'] else 1


def _stream_worker(timeline, start, version, fingerprint, nonce):
    import ctypes

    # Reuse the one bwrap stdout pipe. Only the fixed-wheel PID 1 retains its
    # private non-inheritable copy. Subtools receive ordinary stderr/captured
    # output, and cannot reopen PID 1's pipe through its /proc fd entries.
    if ctypes.CDLL(None, use_errno=True).prctl(4, 0, 0, 0, 0) != 0:  # PR_SET_DUMPABLE
        raise WindowError('isolation.unavailable', 'could not protect isolated protocol writer')
    with os.fdopen(os.dup(1), 'wb') as wire:
        os.dup2(2, 1)

        def emit(kind, result):
            wire.write(stream.pack(kind, {'nonce': nonce, 'result': result}, WIRE_LIMIT))
            wire.flush()

        def first_frame_ready(facts, environment):
            emit(stream.FIRST, stream.worker_first(facts, environment))

        with _cancellation() as check_cancelled, redirect_stdout(sys.stderr):
            result = preview_window(timeline, start=_integer(start), expected_content_version=version,
                                    check_cancelled=check_cancelled, _expected_inputs=fingerprint,
                                    _epoch=True, _first_frame_ready=first_frame_ready)
            if result['ok']:
                check_cancelled()
        emit(stream.FINAL, result)
        return 0 if result['ok'] else 1
