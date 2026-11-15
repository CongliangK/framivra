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


def qualified_median(rows, state):
    values = [r['external_wall_s'] for r in rows
              if r['name'].endswith('-' + state) and r['qualified_pair']]
    return statistics.median(values) if values else None


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
        qualification = root / name / 'cdp-qualification.json'
        record['cdp_qualification'] = json.loads(qualification.read_text()) if qualification.exists() else None
        record['measurement_qualified'] = bool(resources['exit_code'] == 0
            and observation['exit_code'] == 0 and events['evidence_complete'] and events['exit_code'] == 0
            and record['cdp_qualification'] and record['cdp_qualification']['valid']
            and (name.endswith('cold') or record.get('reuse', {}).get('all_existing_unchanged') is True))
        result['runs'].append(record)
    for row in result['runs']:
        pair = row['name'].rsplit('-', 1)[0]
        members = [r for r in result['runs'] if r['name'].rsplit('-', 1)[0] == pair]
        row['qualified_pair'] = (len(members) == 2 and
            {r['name'].rsplit('-', 1)[1] for r in members} == {'cold', 'warm'} and
            all(r['measurement_qualified'] for r in members))
    for state in ('cold', 'warm'):
        result[state + '_median_s'] = qualified_median(result['runs'], state)
    result['aggregation_boundary'] = 'Only complete measured cold/warm pairs qualify; failed/incomplete/unqualified attempts remain listed. This does not grant independent quality or performance acceptance.'
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
