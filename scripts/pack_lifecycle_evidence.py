#!/usr/bin/env python3
"""Bounded private hosted #63 evidence bundle; never certifies full #63 completion."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import zipfile

LIMIT = 32 * 1024 * 1024
TOP = {'owner.json', 'preflight.json', 'summary.json'}
ARM = {'timeline.json', 'events.jsonl', 'runtime-raw.json', 'gfx-run.json',
       'command-result.json', 'lifecycle-summary.json'}


def read_regular(path, remaining):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > remaining:
            raise ValueError('evidence file unavailable or bundle exceeds 32 MiB')
        data = stream.read(remaining + 1)
        after = os.fstat(stream.fileno())
    if len(data) != before.st_size or before != after:
        # atime changes caused by the read itself are immaterial.
        keys = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if len(data) != before.st_size or any(getattr(before, k) != getattr(after, k) for k in keys):
            raise ValueError('evidence changed while packing')
    return data


def pack(parent, output):
    parent, output = Path(parent), Path(output)
    if parent.resolve(strict=True) != parent or not parent.is_dir():
        raise ValueError('evidence parent must be canonical')
    output.mkdir()  # never overwrite prior evidence
    status = {'schema': 'issue63.hosted_bundle/1', 'evidence_complete': False,
              'partial_acceptance': False, 'full_issue63_complete': False, 'errors': []}
    payload = {}
    def include(path, name):
        if len(payload) >= 256:
            raise ValueError('too many selected evidence files')
        payload[name] = read_regular(path, LIMIT - sum(map(len, payload.values())))
    try:
        for name in ('source.json', 'runner-result.json', 'runner.log'):
            include(parent / name, name)
        roots = list(parent.glob('issue63-*'))
        if len(roots) != 1 or roots[0].is_symlink() or not roots[0].is_dir():
            raise ValueError('expected exactly one fresh experiment root')
        root = roots[0]
        members = sorted(root.iterdir())
        if len(members) > 256:
            raise ValueError('too many evidence entries')
        for path in members:
            if path.is_symlink():
                raise ValueError('symlink in experiment evidence')
            if path.name in TOP or path.name.startswith(('plan-', 'golden-', 'comparison-')) and path.suffix == '.json':
                include(path, str(path.relative_to(parent)))
            elif path.is_dir() and (path.name == 'fixture' or path.name.startswith('round-')):
                children = sorted(path.iterdir())
                if len(children) > 256:
                    raise ValueError('too many arm entries')
                for child in children:
                    if child.name in ARM or path.name == 'fixture' and child.suffix == '.html':
                        include(child, str(child.relative_to(parent)))
        if len(payload) > 256:
            raise ValueError('too many selected evidence files')
        def parsed(relative):
            return json.loads(payload[str(root.relative_to(parent) / relative)])
        result = json.loads(payload['runner-result.json'])
        source = json.loads(payload['source.json'])
        if result.get('return_code') != 0 or source.get('head_sha') != source.get('expected_head_sha') or source.get('clean') is not True:
            raise ValueError('runner failed or source binding mismatch')
        summary = parsed('summary.json')
        expected = {(i, s) for i in range(3) for s in ('gfx', 'chain')}
        rounds = summary.get('rounds', [])
        if (summary.get('paired_runs_complete') is not True or summary.get('full_issue63_complete') is not False or
                len(rounds) != 6 or {(r['round'], r['scope']) for r in rounds} != expected):
            raise ValueError('incomplete paired matrix')
        for i, scope in sorted(expected):
            report = parsed(f'comparison-{i}-{scope}.json')
            parsed(f'plan-{i}-{scope}.json'); parsed(f'golden-{i}-{scope}.json')
            if report.get('ok') is not True or report.get('compared_frames') != 120 or report.get('hashed_frames') != 240:
                raise ValueError('incomplete full-frame comparison')
            for side in ('baseline', 'candidate'):
                for name in ARM:
                    if str(root.relative_to(parent) / f'round-{i}-{scope}-{side}' / name) not in payload:
                        raise ValueError('missing mandatory arm evidence')
        status['evidence_complete'] = status['partial_acceptance'] = True
    except (OSError, ValueError, KeyError, TypeError) as error:
        status['errors'].append(type(error).__name__ + ': ' + str(error))
    # Any over-budget collection is a failed evidence gate. Never silently trim a
    # successful receipt set. Available partial payload is explicitly incomplete.
    status['files'] = {name: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                       for name, data in sorted(payload.items())}
    status['payload_bytes'] = sum(map(len, payload.values()))
    metadata = (json.dumps(status, sort_keys=True, indent=2) + '\n').encode()
    if status['payload_bytes'] + 2 * len(metadata) + 1024 * len(payload) > LIMIT:
        status.update(evidence_complete=False, partial_acceptance=False, payload_bytes=0)
        status['errors'].append('bundle metadata exceeds total budget; payload omitted')
        payload = {}
        metadata = (json.dumps(status, sort_keys=True, indent=2) + '\n').encode()
    with zipfile.ZipFile(output / 'evidence.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('evidence-status.json', metadata)
        for name, data in sorted(payload.items()):
            archive.writestr(name, data)
    (output / 'evidence-status.json').write_bytes(metadata)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(0 if pack(args.parent, args.output)['partial_acceptance'] else 1)


if __name__ == '__main__':
    main()
