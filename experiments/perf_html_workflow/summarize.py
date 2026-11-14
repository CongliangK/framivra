"""Read study evidence; never runs a browser or infers missing timers."""
import argparse
import json
from pathlib import Path
import statistics

from experiments.perf_cdp_cost.report import summarize_events, union


def spans(rows):
    return {'calls': len(rows), 'failed_calls': sum(r['status'] != 'ok' for r in rows),
            'work_s': sum(r['end_s'] - r['start_s'] for r in rows),
            'union_s': union([(r['start_s'], r['end_s']) for r in rows])}


def summarize(root):
    identity = json.loads((root / 'identity.json').read_text())
    result = {'root': str(root), 'tabs': identity['environment']['VTX_GFX_TABS'], 'runs': [],
              'boundary': 'Work sums can overlap; unions of different categories also overlap. Never add to wall.'}
    for path in sorted(root.glob('*-observation.json')):
        name = path.name.removesuffix('-observation.json')
        observation = json.loads(path.read_text())
        resources = json.loads((root / name / 'result.json').read_text())
        events = summarize_events((root / (name + '-events.jsonl')).read_bytes())
        rows = observation['records']
        selected = {
            'browser_start': [r for r in rows if r['operation'] == 'browser_start'],
            'navigate_including_ready': [r for r in rows if r['operation'] == 'navigate'],
            'capture_png_transport_json': [r for r in rows if r.get('method') == 'Page.captureScreenshot'],
            'atomic_png_write': [r for r in rows if r['operation'] == 'png_write'],
            'render_page': [r for r in rows if r['operation'] == 'render_page'],
            'hydrate_graphics': [r for r in rows if r['operation'] == 'hydrate_graphics'],
            'compact_graphics': [r for r in rows if r['operation'] == 'compact_graphics']}
        record = {'name': name, 'external_wall_s': resources['wall_s'],
                  'exit_code': resources['exit_code'], 'observation_exit_code': observation['exit_code'],
                  'operations': {k: spans(v) for k, v in selected.items()},
                  'resources': resources, 'events': events,
                  'renderer_stats': [r['stats'] for r in rows if 'stats' in r],
                  'stage_intervals': events['intervals']['stage']['intervals']}
        reuse = root / name / 'cache-reuse.json'
        if reuse.exists():
            record['reuse'] = json.loads(reuse.read_text())
        result['runs'].append(record)
    for state in ('cold', 'warm'):
        values = [r['external_wall_s'] for r in result['runs'] if r['name'].endswith(state)]
        result[state + '_median_s'] = statistics.median(values) if values else None
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('roots', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = [summarize(root) for root in args.roots]
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')


if __name__ == '__main__':
    main()
