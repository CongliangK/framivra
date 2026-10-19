#!/usr/bin/env python3
"""Bounded synthetic CPU-only FrameSource preparation measurement, opt-in."""
import argparse
import cProfile
import hashlib
import json
from pathlib import Path
import platform
import pstats
import statistics
import struct
import sys
import tempfile
import time
import zlib


def png(width, height, frame, rgb=False):
    """Deterministic RGBA, including hidden RGB, or opaque RGB fixture."""
    def chunk(tag, payload):
        return (struct.pack('>I', len(payload)) + tag + payload
                + struct.pack('>I', zlib.crc32(tag + payload) & 0xffffffff))
    bpp = 3 if rgb else 4
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(0, width, 32):
            values = ((x + frame * 17) % 256, (y * 3 + frame) % 256,
                      (x + y + 97) % 256, (0, 64, 128, 255)[(x // 32 + y) % 4])
            row.extend(bytes(values[:bpp]) * min(32, width - x))
        rows.append(b'\0' + row)
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2 if rgb else 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b''.join(rows))) + chunk(b'IEND', b''))


def measure_call(call):
    start_wall, start_cpu = time.perf_counter(), time.process_time()
    result = call()
    return result, {'wall_s': time.perf_counter() - start_wall,
                    'cpu_s': time.process_time() - start_cpu}


def profile_call(call):
    profile = cProfile.Profile()
    result = profile.runcall(call)
    rows = []
    for (file, line, name), (primitive, calls, own, cumulative, _) in pstats.Stats(profile).stats.items():
        if (name in ('_validated', '_stable_input', 'png_invalid_reason',
                     'verify_inputs', 'opaque_rgb_to_rgba', 'inspect_page', 'complete_page')
                or 'decompress' in name):
            rows.append(dict(file=Path(file).name, line=line, name=name, calls=calls,
                             own_s=own, cumulative_s=cumulative))
    return result, sorted(rows, key=lambda row: -row['cumulative_s'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--scratch', type=Path, required=True,
                        help='Owned scratch directory for temporary synthetic PNG pages; no user cache is read.')
    parser.add_argument('--frames', type=int, default=48)
    parser.add_argument('--width', type=int, default=1920)
    parser.add_argument('--height', type=int, default=1080)
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args(argv)
    if not (2 <= args.frames <= 128 and 1 <= args.width <= 1920
            and 1 <= args.height <= 1080 and 1 <= args.repeats <= 9):
        parser.error('bounded fixture: frames 2..128, width 1..1920, height 1..1080, repeats 1..9')
    sys.path.insert(0, str(args.repo.resolve()))
    from vtx.frame_source import PngSequence
    from vtx import frame_source
    args.scratch.mkdir(parents=True, exist_ok=True)
    result = dict(schema='frame-source-preparation/1', python=sys.version, platform=platform.platform(),
                  module_sha256=hashlib.sha256(Path(frame_source.__file__).read_bytes()).hexdigest(),
                  fixture=dict(frames=args.frames, width=args.width, height=args.height,
                               rgb_frames_in_mixed=1, environment_check=None), phases={})
    with tempfile.TemporaryDirectory(prefix='frame-source-', dir=args.scratch) as td:
        root = Path(td)
        payloads = [png(args.width, args.height, i) for i in range(args.frames)]
        rgb = png(args.width, args.height, args.frames // 2, rgb=True)
        result['fixture']['rgba_compressed_bytes'] = sum(map(len, payloads))
        result['fixture']['rgba_sha256'] = [hashlib.sha256(data).hexdigest() for data in payloads]
        def fixture(name, mixed=False):
            directory = root / name / 'original'
            directory.mkdir(parents=True)
            for i, data in enumerate(payloads):
                (directory / f'f{i:04d}.png').write_bytes(rgb if mixed and i == args.frames // 2 else data)
            return PngSequence(directory, 'synthetic', args.frames, 30, args.width, args.height)
        for name in ('homogeneous', 'readiness_then_input', 'mixed_cold', 'mixed_warm'):
            timings = []
            for i in range(args.repeats + 1):
                source = fixture(f'{name}-{i}', mixed=name.startswith('mixed'))
                if name == 'mixed_warm':
                    source.input_args()
                def call():
                    if name == 'readiness_then_input':
                        source.require_ready()
                    return source.input_args()
                if i == args.repeats:
                    _, profile = profile_call(call)
                else:
                    _, timing = measure_call(call)
                    timings.append(timing)
            result['phases'][name] = dict(samples=timings, profile=profile,
                median_wall_s=statistics.median(t['wall_s'] for t in timings),
                median_cpu_s=statistics.median(t['cpu_s'] for t in timings))
        source = fixture('hash_only')
        def hash_only():
            for path in sorted(source.directory.glob('*.png')):
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    while block := stream.read(65536):
                        digest.update(block)
        result['hash_only_samples'] = [measure_call(hash_only)[1] for _ in range(args.repeats)]
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
