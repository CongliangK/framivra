"""Private Linux operator test, not a preview-window CLI or a sandbox.

Run with the documented cgroup limits and offline, read-only inputs on a fresh
project copy. The operator must review the page and its inputs: legacy HTML can
execute script and does not prove dependency closure. Stdout is verified MP4.
"""
import argparse
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import socket
import sys
import time

from vtx.cli.timeline_preview_window import _cancellation
from vtx.pipeline import range as ranges, stages
from vtx.render.png_artifact import project_identity
from vtx.runtime import fstxn
from vtx.timeline_preview_window import (
    MEDIA_LIMIT, _context, _plugins, _request, _safe_environment, _timeline_reader, _verified_media,
)
from vtx.timeline_structure import strict_loads, structure_errors


def _render(path, start, version, check):
    _safe_environment()
    root = path.parent
    identity = project_identity(root)
    read_timeline = _timeline_reader(path, identity, version, check)
    stored = strict_loads(read_timeline())
    errors = structure_errors(stored, preserve_unmanaged=True)
    if errors:
        raise ValueError('; '.join(errors[:16]))
    _plugins(stored)
    if 'subtitles_src' in stored:
        raise ValueError('operator test requires inline subtitles')
    project = stored['project']
    stop = start + 30
    if (project['width'], project['height'], project['fps']) != (1920, 1080, 30) or not (
            0 <= start < stop <= project['duration_frames']):
        raise ValueError('operator test requires a complete 1080p30 one-second window')

    status = {'diagnostics': []}
    try:
        with _request(root, identity, status) as (request, request_identity):
            workspace = request / 'render'
            fstxn.mkdir(workspace, root=root)
            media_inputs = {str(root / source['path']) for source in stored['sources']}
            c = _context(path, check, os.environ.copy(), read_timeline, epoch_sources=media_inputs)
            c.frame_root_identity = identity
            errors = stages.validate_core(c)
            if errors:
                raise ValueError('; '.join(errors[:16]))
            c.write_root = workspace
            output = ranges._render_native_window(
                c, start, stop, workspace=workspace, check_cancel=check, expected_svg_pack=None,
                _trust_html=True)
            data, facts = _verified_media(output, workspace, request, request_identity,
                                          start, stop, c.frame_rate, check, limit=MEDIA_LIMIT)
        c.verify_inputs(None)
        return data, dict(facts, content_version=version)
    finally:
        for diagnostic in status['diagnostics']:
            print(json.dumps(diagnostic), file=sys.stderr)


def _check_operator_environment(root):
    # Guard against accidental bare invocation. These checks are not an
    # isolation or job-settlement certificate for public consumers.
    if sys.platform != 'linux' or {name for _, name in socket.if_nameindex()} != {'lo'}:
        raise ValueError('operator test requires an offline Linux network namespace')
    if not os.statvfs(root).f_flag & os.ST_RDONLY or (
            os.statvfs(root / 'cache' / 'preview').f_flag & os.ST_RDONLY):
        raise ValueError('operator test requires a read-only project with writable preview scratch')
    leaf = next((line[3:] for line in Path('/proc/self/cgroup').read_text().splitlines()
                 if line.startswith('0::/')), '')
    group = Path('/sys/fs/cgroup') / leaf.lstrip('/')
    if (not leaf or (group / 'memory.max').read_text().strip() != str(8192 * 1024**2)
            or (group / 'memory.swap.max').read_text().strip() != '0'):
        raise ValueError('operator test requires the 8 GiB / zero-swap test group')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('timeline', type=Path)
    parser.add_argument('--frame', type=int, required=True)
    parser.add_argument('--expected-content-version', required=True)
    parser.add_argument('--trust-html', action='store_true', required=True)
    args = parser.parse_args()
    began = time.monotonic()
    path = args.timeline.absolute()
    _check_operator_environment(path.parent)
    with _cancellation() as check, redirect_stdout(sys.stderr):
        data, facts = _render(path, args.frame, args.expected_content_version, check)
        check()
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()
    print(json.dumps(dict(facts, request_seconds=round(time.monotonic() - began, 3))), file=sys.stderr)


if __name__ == '__main__':
    main()
