"""Read a timeline and time original N1 command construction, without media IO."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import platform
import statistics
import time
from types import SimpleNamespace

from vtx.composite import RenderPlan
from vtx.frame_source import PngSequence


def fixture(timeline):
    """Model input paths only. These are NOT real content-addressed cache keys."""
    project = timeline['project']
    root = Path('/unmaterialized-planning-fixture')
    commands = []
    context = SimpleNamespace(tl=timeline, dry=True, gpu=False,
                              fps=project['fps'], spf=1 / project['fps'],
                              dur=project['duration_frames'], cache=root / 'cache',
                              run=commands.append)
    layers = []
    descriptors = []
    for ti, track in enumerate(timeline['video_tracks']):
        for clip in track['clips']:
            gfx = track.get('kind') == 'gfx'
            descriptor = {k: v for k, v in clip.items() if k != 'dst_in'}
            token = json.dumps(descriptor, sort_keys=True, ensure_ascii=False)
            identity = hashlib.sha256(token.encode()).hexdigest()[:12]
            layer = dict(cl=clip, x=clip.get('x', 0), y=clip.get('y', 0),
                         w2=None, h2=None, gfx=gfx, ti=ti)
            if gfx:
                layer['source'] = PngSequence(root / 'cache' / 'gfx' / identity,
                                             identity, clip['dur'], project['fps'],
                                             project['width'], project['height'])
            else:
                layer['seg'] = root / 'cache' / 'seg' / (identity + '.mp4')
                descriptors.append(token)
            layers.append(layer)
    plan = RenderPlan(c=context, draft=False, f=1, W=project['width'],
                      H=project['height'], nseg=1, layers=layers)
    return plan, commands, Counter(descriptors)


def measure(timeline_bytes, repeats=1000):
    if not 1 <= repeats <= 10000:
        raise ValueError('repeats must be in [1,10000]')
    timeline = json.loads(timeline_bytes)
    plan, commands, descriptors = fixture(timeline)
    output = plan.c.cache.parent / 'out' / 'video_noaudio.mp4'

    def once():
        commands.clear()
        plan.composite(plan.layers, output, True)

    for _ in range(min(50, repeats)):
        once()
    wall, cpu = [], []
    for _ in range(repeats):
        start, start_cpu = time.perf_counter_ns(), time.process_time_ns()
        once()
        cpu.append(time.process_time_ns() - start_cpu)
        wall.append(time.perf_counter_ns() - start)
    command = commands[0]
    graph = command[command.index('-filter_complex') + 1]
    return dict(
        schema='perf-composite-dry-plan/1', python=platform.python_version(),
        platform=platform.system(), timeline_sha256=hashlib.sha256(timeline_bytes).hexdigest(),
        measured_scope='Original N1 dry command construction; modeled paths only',
        excluded=['Ctx construction', 'budget', 'manifest', 'input validation',
                  'media IO', 'FFmpeg execution', 'Mac production wall'],
        repeats=repeats, wall_ms_median=statistics.median(wall) / 1e6,
        wall_ms_p95=sorted(wall)[max(0, (95 * repeats + 99) // 100 - 1)] / 1e6,
        cpu_ms_median=statistics.median(cpu) / 1e6,
        layers=len(plan.layers), gfx_layers=sum(layer['gfx'] for layer in plan.layers),
        non_gfx_layers=sum(descriptors.values()),
        non_gfx_descriptor_multiplicities=sorted(descriptors.values(), reverse=True),
        descriptor_note='Exact clip dictionaries excluding dst_in; not real seg-key verification',
        input_count_including_canvas=command.count('-i'),
        overlay_count=graph.count('overlay='), filtergraph_bytes=len(graph.encode()),
        filtergraph_sha256=hashlib.sha256(graph.encode()).hexdigest(),
        mac_300_frame_gate='UNRUN', mac_full_film_gate='UNRUN')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('timeline', type=Path)
    parser.add_argument('--repeats', type=int, default=1000)
    args = parser.parse_args()
    print(json.dumps(measure(args.timeline.read_bytes(), args.repeats), indent=2))


if __name__ == '__main__':
    main()
