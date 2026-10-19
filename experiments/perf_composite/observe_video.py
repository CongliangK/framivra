"""Explicit Mac-only observer launcher for a NEW isolated project copy.

The observer writes only a new independent JSON report, but the delegated video
or all command DOES render and write cache/out; all may launch a real browser.
Never point this launcher at the user's existing project or final deliverable.
No command runs unless --ack-render-writes and a command after -- are supplied.
"""
import argparse
import json
import os
from pathlib import Path
import sys

from .observer import Observer


def check_invocation(argv, isolated_project, acknowledged, *, platform=sys.platform):
    if not acknowledged:
        raise ValueError('Rendering writes cache/out. Require --ack-render-writes for a NEW isolated copy.')
    if platform != 'darwin':
        raise ValueError('Real observation runs are Mac-only; cloud execution is not allowed.')
    # Parse only the supported full N1 CPU invocation. Do not silently accept
    # unrelated maintenance commands or flags that change the tested render.
    native = argparse.ArgumentParser(add_help=False, exit_on_error=False)
    native.add_argument('-t', '--timeline', type=Path, required=True)
    native.add_argument('--events-jsonl', type=Path)
    native.add_argument('command', choices=('video', 'all'))
    args, extra = native.parse_known_args(argv)
    if extra:
        raise ValueError('Observer accepts only -t/--timeline, optional --events-jsonl, and video or all.')
    project = isolated_project.resolve(strict=True)
    timeline = args.timeline.resolve(strict=True)
    if not project.is_dir() or timeline.parent != project:
        raise ValueError('Timeline must belong directly to the acknowledged new isolated project copy.')
    # No claim that this proves provenance of a copy. The explicit operator
    # acknowledgement and independently checked Mac launch remain mandatory.
    for filename in ('final.mp4', 'video_noaudio.mp4', 'draft.mp4'):
        if (project / 'out' / filename).exists():
            raise ValueError('Existing rendered output found; use a NEW isolated project copy.')
    if os.environ.get('VTX_VIDEO_SEGMENTS') != '1':
        raise ValueError('This measurement requires the approved N1 profile (VTX_VIDEO_SEGMENTS=1); it never changes settings.')
    if args.events_jsonl and args.events_jsonl.exists():
        raise ValueError('The optional production event destination must be new.')
    return project


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--isolated-project', required=True, type=Path)
    parser.add_argument('--ack-render-writes', action='store_true')
    parser.add_argument('native', nargs=argparse.REMAINDER,
                        help='-- -t NEW_COPY/timeline.json [--events-jsonl NEW_FILE] video|all')
    args = parser.parse_args(argv)
    if not args.native or args.native[0] != '--' or len(args.native) < 2:
        parser.error('Supply an explicit native video or all command after --; there is no default run.')
    native_argv = args.native[1:]
    try:
        project = check_invocation(native_argv, args.isolated_project, args.ack_render_writes)
        report_path = args.report.absolute()
        if project == report_path.parent.resolve() or project in report_path.parent.resolve().parents:
            raise ValueError('Report must be outside the rendered project, in a new evidence directory.')
        # Exclusive creation prevents overwrite and proves the evidence output
        # is writable BEFORE invoking production. Never create parent folders.
        report_file = report_path.open('x', encoding='utf-8')
    except (ValueError, OSError) as error:
        parser.error(str(error))
    observer = Observer()
    failure = None
    result = None
    previous = sys.argv
    try:
        with observer.installed():
            from vtx import cli
            sys.argv = ['vtx', *native_argv]
            result = cli.main()
        return result
    except BaseException as error:
        failure = error
        raise
    finally:
        sys.argv = previous
        try:
            report = observer.report()
            report['invocation_outcome'] = ('cancelled' if isinstance(failure, KeyboardInterrupt)
                                            else 'raised' if failure is not None else 'returned')
            # Only numeric status: exception messages/argv/environment stay private.
            if isinstance(failure, SystemExit):
                code = failure.code
                report['exit_code'] = 0 if code is None else code if isinstance(code, int) else 1
            else:
                report['exit_code'] = (130 if isinstance(failure, KeyboardInterrupt) else
                                       1 if failure is not None else (result or 0))
            json.dump(report, report_file, indent=2)
            report_file.write('\n')
            report_file.close()
        except BaseException:
            # Evidence failure must not replace a production cancellation/error.
            print('Observer report incomplete; measurement is UNKNOWN.', file=sys.stderr)
            if failure is None:
                raise


if __name__ == '__main__':
    sys.exit(main())
