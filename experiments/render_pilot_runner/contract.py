"""Read-only, pinned input and frame contracts for the first TODAY pilot."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
import sys

BASE_COMMIT = 'b287b86d95565e308eaf8fa64900f9bc6d62ae8e'
BASE_TREE = 'd281bafa0af5073f73cae2170304be76fa040d23'
PLAN_SHA256 = '28df35c3645994491d96b2d6079d75ac23a9bc6c31d66446c9080cba4045cc1d'
INVENTORY_SHA256 = 'ac7a6a3c3b9b276fd6ec544ba21aadd37b57302fe35072238e01802cd8e38ef0'
BOUND_BUNDLE_SHA256 = 'ab9c1c65ca5f41062b691cf45b55bc9beaa1a14caa28cb0f1013ed62d1421cee'
BOUND_TIMELINE_SHA256 = '957e893ce00e8a116fe130251e60f4f23662a00bccd0b413b7b4a52efa9e9b19'
WIDTH, HEIGHT, FPS = 1920, 1080, 30
DEADLINE_SECONDS = 900
DIAGNOSTICS = [0,17,18,19,138,139,299,300,329,330,629,630,689,690,1139,1140,
               1709,1710,1711,2159,2160,2819,2820,2939,2940,3119,3120,3149,3150,3599]
BATCHES = {'continuous': list(range(1380, 1470)), 'diagnostic': DIAGNOSTICS}
TARGETS = BATCHES['continuous'] + DIAGNOSTICS
DERIVED = {'audio/native-audio-provenance.json', 'audio/native-postmix.json',
           'audio/waveform-S5.json', 'src/assets/media-bindings.json',
           'runtime/shared.js', 'entry-manifest.json', 'runtime-dependencies.json'}
PROFILE = {'VTX_GFX': 'cdp', 'VTX_GFX_PAGES': '1', 'VTX_GFX_TABS': '1',
           'VTX_GFX_SPLIT_FRAMES': '0', 'VTX_GFX_SPLIT_MAX': '1',
           'VTX_IO_WORKERS': '1', 'VTX_FILTER_THREADS': '1', 'VTX_VIDEO_SEGMENTS': '1',
           'VTX_GFX_NAVFREE': '0', 'VTX_GFX_STATIC': '1'}


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def file_hash(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def regular(root, relative):
    """Reject links and traversal; this experiment never follows source links."""
    root = Path(root).absolute()
    p = Path(relative)
    require(not p.is_absolute() and p.parts and '..' not in p.parts, 'Unsafe relative path')
    dest = root / p
    require(not any(q.is_symlink() for q in (dest, *dest.parents)), 'Symlink input refused')
    require(dest.is_file(), 'Missing regular input: ' + str(dest))
    return dest


CLOCK_KIND='time.clock_gettime(CLOCK_MONOTONIC)'


def clock_now():
    """System clock, including Mac Python 3.9; never use a per-process origin."""
    require(callable(getattr(time,'clock_gettime',None)) and hasattr(time,'CLOCK_MONOTONIC'),
            'Shared CLOCK_MONOTONIC unavailable; no fallback')
    value=time.clock_gettime(time.CLOCK_MONOTONIC)
    require(type(value) in (int,float) and math.isfinite(value) and value>=0,'Invalid shared clock value')
    return value


def clock_probe(deadline=None):
    """One short ordinary Python child; no browser, media, cache or new daemon."""
    before=clock_now()
    code=('import json; from experiments.render_pilot_runner.contract import clock_now,CLOCK_KIND; '
          'print(json.dumps({"clock_kind":CLOCK_KIND,"value":clock_now()}))')
    result=subprocess.run([sys.executable,'-c',code],cwd=str(Path(__file__).resolve().parents[2]),
                          check=True,capture_output=True,text=True,timeout=remaining_timeout(deadline,5),
                          env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[2])})
    after=clock_now();child=json.loads(result.stdout)
    require(child.get('clock_kind')==CLOCK_KIND and type(child.get('value')) in (int,float)
            and before<=child['value']<=after,'Parent/child clock origins do not agree')
    return {'clock_kind':CLOCK_KIND,'parent_before':before,'child':child['value'],
            'parent_after':after,'status':'PASS_CLOCK_ONLY','python':sys.version}


def remaining_timeout(deadline, maximum=20.0):
    if deadline is None:
        return maximum
    remaining=deadline-clock_now()
    if remaining<=0:
        raise TimeoutError('Pair deadline reached before external wait')
    return min(maximum,remaining)


def git_identity(repo, expected_commit=None, expected_tree=None, *, deadline=None):
    def git(*args):
        return subprocess.run(['git', '-C', str(repo), *args], check=True,
                              capture_output=True, text=True, timeout=remaining_timeout(deadline)).stdout.strip()
    commit, tree = git('rev-parse', 'HEAD'), git('rev-parse', 'HEAD^{tree}')
    require(expected_commit is None or commit == expected_commit, 'Implementation HEAD mismatch')
    require(expected_tree is None or tree == expected_tree, 'Implementation tree mismatch')
    require(not git('status', '--porcelain', '--untracked-files=all'), 'Implementation checkout must be clean')
    return {'commit': commit, 'tree': tree}


def read_bound(project, inventory_file):
    """Verify all 109 bound payload bytes; record, but do not claim to read, five media."""
    project = Path(project).absolute()
    inventory_bytes = Path(inventory_file).read_bytes()
    require(digest(inventory_bytes) == INVENTORY_SHA256, 'Reviewed bound inventory hash mismatch')
    inventory = json.loads(inventory_bytes)
    require(isinstance(inventory, list) and len(inventory) == 114, 'Expected reviewed 114-file inventory')
    require(len({x['path'] for x in inventory}) == 114, 'Duplicate inventory path')
    payload, media = {}, {}
    for row in inventory:
        require(re.fullmatch('[0-9a-f]{64}', row['sha256']) is not None, 'Invalid inventory digest')
        if row['excluded_media']:
            media[row['path']] = {'sha256': row['sha256'], 'bytes': row['bytes'],
                                  'verification': 'reviewed inventory only; not consumed by GFX'}
            continue
        path = regular(project, row['path'])
        require(path.stat().st_size == row['bytes'] and file_hash(path) == row['sha256'],
                'Bound payload changed: ' + row['path'])
        payload[row['path']] = row['sha256']
    require(len(payload) == 109 and len(media) == 5 and DERIVED <= payload.keys(), 'Incomplete bound input')
    require(payload['runtime/shared.js'] == BOUND_BUNDLE_SHA256, 'Source/unbound bundle is not a baseline')
    require(payload['timeline.json'] == BOUND_TIMELINE_SHA256, 'Reviewed timeline mismatch')
    manifest = json.loads(regular(project, 'entry-manifest.json').read_bytes())
    entries = manifest['entries']
    require(len(entries) == 19, 'Expected 19 original entries')
    chosen = sorted((e for e in entries if e['layerId'] in ('T08', 'T07')), key=lambda e: e['paintIndex'])
    require([(e['layerId'],e['windowStart'],e['duration'],e['paintIndex']) for e in chosen]
            == [('T08',18,3582,6),('T07',0,3600,7)], 'Unexpected pair windows/order')
    for e in chosen:
        regular(project, e['src'])
    return {'root': str(project), 'inventory_path': str(Path(inventory_file).absolute()),
            'inventory_sha256': digest(inventory_bytes), 'payload': payload, 'media': media,
            'derived_sha256': {k: payload[k] for k in sorted(DERIVED)}, 'entries': chosen,
            'content_identity': digest(canonical(payload))}


def mapped(entry, globals_):
    start, count = entry['windowStart'], entry['duration']
    require(type(start) is int and type(count) is int and start >= 0 and count > 0, 'Invalid entry window')
    require(len(globals_) == len(set(globals_)) and all(type(f) is int and 0 <= f < 3600 for f in globals_),
            'Invalid or duplicate global targets')
    return [{'global': f, 'local': f-start} for f in globals_ if start <= f < start+count]


def build_plan(bound, mode, group_plan=None):
    require(mode in ('group', 'fast-png'), 'Only independent group or fast-png modes')
    jobs = []
    group_verified = None
    for arm in ('baseline', 'candidate'):
        if mode == 'group' and arm == 'candidate':
            require(group_plan is not None, 'Group plan required')
            gp = json.loads(Path(group_plan).read_bytes())
            require(gp['schema'] == 'promo-gfx-grouping/1', 'Unsupported group plan')
            require(gp['project']=='project' and gp['page']=='gfx/group_T08_T07_0000_3600.html',
                    'Unexpected derived project/page path')
            require((gp['source_commit'],gp['source_tree']) == (BASE_COMMIT,BASE_TREE), 'Group source mismatch')
            require((gp['width'],gp['height'],gp['fps'],gp['window_start'],gp['duration'])
                    == (WIDTH,HEIGHT,FPS,0,3600), 'Group geometry/time mismatch')
            keys = ('entryId','layerId','src','windowStart','duration','paintIndex')
            require([{k:e[k] for k in keys} for e in gp['ordered_members']]
                    == [{k:e[k] for k in keys} for e in bound['entries']], 'Group membership mismatch')
            require(gp['original_entry_manifest_sha256'] == bound['payload']['entry-manifest.json'],
                    'Group bound identity mismatch')
            root = (Path(group_plan).absolute().parent / gp['project']).resolve()
            from .group_contract import validate_group
            group_verified=validate_group(gp,bound,root,Path(__file__).resolve().parents[2])
            entries = [{'entryId':'T08_T07','src':gp['page'],'windowStart':0,'duration':3600}]
        else:
            root, entries = Path(bound['root']), bound['entries']
        for batch, frames in BATCHES.items():
            for entry in entries:
                mapping = mapped(entry, frames)
                if mapping:
                    jobs.append({'arm':arm,'batch':batch,'entry':entry,'project':str(root),
                                 'mapping':mapping,'expected_rendered':len(mapping)})
    return {'schema':'render-pilot/1','status':'UNRUN','base':{'commit':BASE_COMMIT,'tree':BASE_TREE},
            'today_plan_sha256':PLAN_SHA256,'mode':mode,'bound':bound,'profile':PROFILE,
            'geometry':[WIDTH,HEIGHT,FPS],'deadline_seconds':DEADLINE_SECONDS,
            'targets':TARGETS,'batches':BATCHES,'jobs':jobs,
            'group_plan':str(Path(group_plan).absolute()) if group_plan else None,
            'group_plan_sha256':file_hash(group_plan) if group_plan else None,
            'group_verified':group_verified,
            'quality_scope':'given target GFX/pair pixels only; full N1 scene/subtitles/audio UNRUN'}


def validate_stats(stats, expected):
    require(type(stats) is dict and expected > 0, 'Empty/invalid renderer result')
    require(stats['rendered'] == expected and stats['failed'] == [], 'Missing, cached, or failed target frames')
    require(stats['tabs'] == 1 and not stats['canvas_export'], 'Unexpected path/concurrency')
    pc = stats['path_counts']
    require(pc.get('override') == expected and all(v == 0 for k,v in pc.items() if k != 'override'),
            'Fallback, copy, non-override, or failure path is not eligible')
    for category in ('nav','export','decode','write'):
        summary = stats['timings_ms'][category]
        require(set(summary) == {'sum','p50','p95'}, 'Unexpected production timing schema')
        require(all(type(v) in (int,float) and math.isfinite(v) and v >= 0 for v in summary.values()),
                'Invalid timing sample')
    # NAVFREE, static copy and retries are rejected separately, so each successful
    # override has exactly one nav/export/decode/write sample in production.
    return {k: dict(v, count=expected, count_basis='successful override frames')
            for k,v in stats['timings_ms'].items()}
