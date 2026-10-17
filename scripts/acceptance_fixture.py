#!/usr/bin/env python3
"""Public synthetic CPU project; write only a new output directory."""
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vtx.acceptance import build, safe_path, safe_environment, verify, write_json


def tamper_negatives(root, digest):
    """Retain actual rendered-package corruption cases, with an unchanged anchor."""
    negatives = root / 'tamper-negatives'
    negatives.mkdir()
    reports = []
    for name, target in [('artifact', 'artifacts/final.mp4'),
                         ('input', 'inputs/source.mp4'), ('manifest', 'manifest.json')]:
        package = negatives / name
        shutil.copytree(root / 'package', package)
        path = package / target
        with path.open('r+b') as stream:
            stream.seek(path.stat().st_size // 2)
            byte = stream.read(1)
            stream.seek(-1, 1)
            stream.write(bytes([byte[0] ^ 1]))
        try:
            verify(package, digest)
        except ValueError as error:
            reports.append({'case': name, 'rejected': True, 'error': str(error)})
        else:
            raise RuntimeError(f'{name} tampering was not rejected')
    source = root / 'source/source.mp4'
    original, stat = source.read_bytes(), source.stat()
    changed = bytearray(original)
    changed[len(changed) // 2] ^= 1
    try:
        source.write_bytes(changed)
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        try:
            verify(root / 'package', digest, root / 'source')
        except ValueError as error:
            reports.append({'case': 'live_input_same_stat', 'rejected': True, 'error': str(error)})
        else:
            raise RuntimeError('same-stat live input tampering was not rejected')
    finally:
        source.write_bytes(original)
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    write_json(root / 'tamper-results.json', reports)
    verify(root / 'package', digest, root / 'source')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', help='NEW directory under an existing trusted parent')
    args = parser.parse_args()
    root = safe_path(args.output)
    root.mkdir(mode=0o700)
    project = root / 'source'
    project.mkdir()
    command = ['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=320x180:r=30:d=2',
               '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-c:v', 'libx264',
               '-threads', '1', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
               '-c:a', 'aac', '-shortest', str(project / 'source.mp4')]
    with (root / 'source-generation.log').open('xb') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, env=safe_environment(root))
    timeline = {'version': 2,
        'project': {'fps': 30, 'width': 320, 'height': 180, 'duration_frames': 60},
        'sources': [{'id': 'source', 'path': 'source.mp4'}],
        'video_tracks': [{'name': 'base', 'kind': 'aroll', 'clips': [
            {'src': 'source', 'src_in': 0, 'src_out': 60, 'dst_in': 0, 'dur': 60}]}],
        'subtitles': [], 'audio_tracks': [{'name': 'voice', 'events': [
            {'src': 'source', 'in': 0, 'out': 60, 'volume': 1.0}]}]}
    write_json(project / 'timeline.json', timeline)
    result = build(project / 'timeline.json', root / 'package')
    write_json(root / 'anchor.json', result)
    report = verify(root / 'package', result['manifest_sha256'], project)
    write_json(root / 'verification.json', report)
    if not report['integrity_verified'] or any(report['gates'][x]['status'] != 'passed'
            for x in ('validate', 'prepare', 'video', 'audio', 'mux', 'qc', 'process', 'input_stability')):
        raise RuntimeError('fixture build or structural QC failed; inspect retained evidence')
    tamper_negatives(root, result['manifest_sha256'])
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
