"""Bounded CPU/native-media observer control in a NEW synthetic evidence folder.

No browser, production timeline, existing cache, or user media is read. Results
are an overhead/command-wiring control, never a Mac production speed estimate.
"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import struct
import subprocess
import time
from types import SimpleNamespace
import zlib

from vtx import gfx_cli
from vtx.composite import RenderPlan
from vtx.frame_source import PngSequence

from .observer import Observer


def png_bytes(width, height, frame):
    def chunk(name, data):
        return struct.pack('>I', len(data)) + name + data + struct.pack('>I', zlib.crc32(name + data) & 0xffffffff)
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            row.extend(((x * 3 + frame * 13) % 256, (y * 5 + frame * 7) % 256,
                        (x + y + frame * 11) % 256, (x * 9 + y * 3 + frame * 17) % 256))
        rows.append(bytes(row))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>2I5B', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b''.join(rows))) + chunk(b'IEND', b''))


def timed(function, count):
    start = time.perf_counter_ns()
    for _ in range(count):
        function()
    return time.perf_counter_ns() - start


def paired(function, observed_function, count, repeats=8):
    plain, observed = [], []
    for i in range(repeats):
        order = [(function, plain), (observed_function, observed)]
        if i % 2:
            order.reverse()
        for call, values in order:
            values.append(timed(call, count))
    return dict(calls_per_batch=count, batches_per_arm=repeats,
                plain_batch_ns=plain, observed_batch_ns=observed,
                median_plain_ns_per_call=statistics.median(plain) / count,
                median_observed_ns_per_call=statistics.median(observed) / count,
                median_delta_ns_per_call=(statistics.median(observed) - statistics.median(plain)) / count)


def calibrate(root):
    root.mkdir(exist_ok=False)
    sequence = root / 'sequence'; sequence.mkdir()
    (root / 'cache').mkdir()
    for frame in range(16):
        (sequence / f'f{frame:04d}.png').write_bytes(png_bytes(96, 64, frame))
    observer = Observer()
    noop = lambda: None
    noop_control = paired(noop, observer.wrap(noop, 'noop'), 10000)
    original = gfx_cli.png_invalid_reason
    validator_observer = Observer()
    with validator_observer.installed():
        def plain_validate():
            result = original(sequence / 'f0000.png', width=96, height=64)
            if result:
                raise AssertionError(result)
        def observed_validate():
            result = gfx_cli.png_invalid_reason(sequence / 'f0000.png', width=96, height=64)
            if result:
                raise AssertionError(result)
        validation_control = paired(plain_validate, observed_validate, 300)
    source = PngSequence(sequence, 'synthetic', 16, 30, 96, 64)
    context = SimpleNamespace(cache=root / 'cache', fps=30, spf=1 / 30, dur=36,
                              dry=False, gpu=False)
    commands = []
    def execute(command):
        commands.append(list(command))
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError('Synthetic FFmpeg failed')
        return result
    context.run = execute
    layers = [dict(cl=dict(dst_in=offset, dur=16), source=source, gfx=True,
                   x=offset, y=0, w2=None, h2=None, ti=i)
              for i, offset in enumerate((0, 10))]
    plan = RenderPlan(c=context, draft=False, f=1, W=96, H=64, nseg=1, layers=layers)
    render_plain, render_observed, hashes, graphs, reports = [], [], [], [], []
    installation_ns, restoration_ns = [], []
    for index, instrumented in enumerate((False, True, True, False)):
        output = root / f'render-{index}.mp4'
        call = lambda: plan.composite(layers, output, False)
        if instrumented:
            render_observer = Observer()
            install_start = time.perf_counter_ns()
            with render_observer.installed():
                installation_ns.append(time.perf_counter_ns() - install_start)
                wrapped = render_observer.wrap(call, 'video_stage')
                render_observed.append(timed(wrapped, 1))
                restore_start = time.perf_counter_ns()
            restoration_ns.append(time.perf_counter_ns() - restore_start)
            reports.append(render_observer.report())
        else:
            render_plain.append(timed(call, 1))
        command = commands[-1]
        graph = command[command.index('-filter_complex') + 1]
        graphs.append(hashlib.sha256(graph.encode()).hexdigest())
        decoded = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(output),
                                  '-map', '0:v:0', '-f', 'framemd5', '-'],
                                 check=True, capture_output=True)
        hashes.append(hashlib.sha256(decoded.stdout).hexdigest())
    if len(set(hashes)) != 1 or len(set(graphs)) != 1:
        raise AssertionError('Observer media or graph equivalence failed')
    return dict(schema='perf-composite-calibration/1', noop=noop_control,
                png_validation=validation_control,
                native_synthetic_render=dict(plain_ns=render_plain, observed_ns=render_observed,
                                             frames=36, width=96, height=64, layers=2,
                                             observer_install_ns=installation_ns,
                                             observer_restore_ns=restoration_ns,
                                             frame_md5_equal=True, filtergraph_equal=True,
                                             framemd5_sha256=hashes[0],
                                             filtergraph_sha256=graphs[0]),
                observer_reports=reports,
                limits='Synthetic CPU/native-media control only. Different process/cache/CPU states '
                       'and wrapper costs mean these deltas cannot be subtracted from Mac wall. '
                       'No production quality, alpha equivalence, or speed claim.',
                mac_300_frame_gate='UNRUN', mac_full_film_gate='UNRUN')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('new_directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(calibrate(args.new_directory.absolute()), indent=2))


if __name__ == '__main__':
    main()
