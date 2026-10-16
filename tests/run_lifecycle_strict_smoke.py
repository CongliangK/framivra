"""Separate cold strict+publication smoke. Reuses the #47 worker; no new observer."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
LIMIT = 4 * 1024 * 1024


def source_identity():
    def git(*args):
        return subprocess.check_output(['git', '-C', str(REPO), *args], text=True, timeout=5).strip()
    value = dict(head=git('rev-parse', 'HEAD'), tree=git('rev-parse', 'HEAD^{tree}'), status=git('status', '--porcelain'))
    if value['status'] or value['head'] != os.environ.get('EXPECTED_HEAD'):
        raise ValueError('strict smoke requires clean actual expected head')
    return value


def check_pixels(context, clips):
    from PIL import Image
    from vtx.hygiene import gfx_dir
    rows = []
    for index, clip in enumerate(clips):
        frames = sorted(gfx_dir(context, clip).glob('f*.png'))
        assert len(frames) == 2
        for frame in frames:
            with Image.open(frame) as image:
                image.load(); rgba = image.convert('RGBA'); rgb = rgba.convert('RGB')
                assert rgba.size == (1920, 1080)
                assert rgb.getpixel((72, 8)) == (34, 68, 102)
                if index == 0:
                    assert rgb.getpixel((8, 8)) == (238, 34, 17)
                    assert rgb.getpixel((40, 8)) == (17, 68, 238)
                else:
                    assert rgb.getpixel((104, 8)) == (34, 204, 68)
                assert sum(max(pixel) < 100 for pixel in rgb.crop((0, 26, 75, 50)).getdata()) > 30
                rows.append(dict(src=clip['src'], frame=frame.name,
                    rgba_sha256=hashlib.sha256(rgba.tobytes()).hexdigest()))
    assert len(rows) == 4
    return rows


def run(root):
    from tests import run_gfx_dependency_acceptance as accepted
    from vtx.infra import Ctx
    from vtx.gfx_dependencies import resolve
    evidence = root / 'evidence'
    evidence.mkdir(parents=True, exist_ok=True)
    report = dict(schema='vtx.strict_publication_smoke_report/1', passed=False,
                  full_issue63_complete=False, max_frames=4)
    try:
        identity = source_identity(); report['identity'] = identity
        chrome = shutil.which('google-chrome') or shutil.which('google-chrome-stable')
        if not chrome:
            raise ValueError('official installed Chrome required')
        os.environ['VTX_CHROME'] = chrome
        os.environ.update(VTX_GFX='cdp', VTX_GFX_PAGES='1', VTX_GFX_TABS='1',
            VTX_GFX_SPLIT_FRAMES='0', VTX_GFX_NAVFREE='0', VTX_GFX_STATIC='0', VTX_DOCTOR_OFFLINE='1')
        os.environ.pop('VTX_GFX_CACHE_NS', None)
        font = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
        provenance = accepted.font_provenance(font)
        (evidence/'font-provenance.json').write_text(json.dumps(provenance, indent=2))
        (evidence/'font-LICENSE.txt').write_text(provenance['notice_text'])
        project = root / 'project'
        project.mkdir()  # No cache reuse or deletion of an existing project.
        timeline = project/'timeline.json'
        tl = accepted.fixture(project, font, provenance)
        timeline.write_text(json.dumps(tl))
        context = Ctx(timeline); clips = tl['video_tracks'][0]['clips']
        records = [resolve(context, clip) for clip in clips]
        (evidence/'dependencies.json').write_text(json.dumps(records, indent=2))
        (evidence/'timeline.json').write_bytes(timeline.read_bytes())
        trace_path = evidence/'strict-trace.json'
        producer_path = evidence/'strict-producer.json'
        accepted.worker(timeline, trace_path, publication_evidence=producer_path)
        trace = json.loads(trace_path.read_text())
        accepted.check_round_counts(trace, [0, 1])
        urls = {request['url'] for record in records for request in record['resource_requests']}
        accepted.check_trace(trace, urls)
        receipt_path = project/'out/receipt.json'
        receipt = json.loads(receipt_path.read_text())
        (evidence/'receipt.json').write_bytes(receipt_path.read_bytes())
        report['native_binding'] = accepted.check_native_events(
            trace_path.with_suffix('.events.jsonl'), trace, timeline=timeline,
            stage='gfx', identity=identity, receipt=receipt, gfx_attempts=2)
        report['frames'] = check_pixels(context, clips)
        producer = json.loads(producer_path.read_text())
        assert producer['complete'] is True and sum(len(i['frames']) for i in producer['instances']) == 4
        assert producer['invocation'] == trace['invocation']
        assert source_identity() == identity
        report['passed'] = True
    finally:
        (evidence/'smoke-report.json').write_text(json.dumps(report, indent=2))


REQUIRED = {'smoke-report.json','strict-producer.json','strict-producer.runtime.json',
    'strict-trace.json','strict-trace.events.jsonl','receipt.json','dependencies.json',
    'timeline.json','font-provenance.json','font-LICENSE.txt','exit-code.txt','smoke.log',
    'strict-producer.status.json'}


def validate_retained(root, payload):
    """Recheck the original validators and real files, not a passed boolean."""
    from scripts import gfx_evidence as verifier
    from tests import run_gfx_dependency_acceptance as accepted
    from vtx import events, gfx_runtime
    from vtx.infra import Ctx
    from vtx.gfx_dependencies import resolve
    from vtx.hygiene import gfx_dir
    import uuid
    def obj(name):
        value = json.loads(payload[name], parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
        verifier.digest(value)
        return value
    identity = source_identity()
    report = obj('smoke-report.json'); producer = obj('strict-producer.json')
    trace = obj('strict-trace.json'); receipt = obj('receipt.json')
    if report.get('schema')!='vtx.strict_publication_smoke_report/1' or producer.get('schema')!='vtx.strict_publication_smoke/1':
        raise ValueError('unknown smoke schema')
    if report.get('identity') != identity or report.get('passed') is not True or report.get('full_issue63_complete') is not False:
        raise ValueError('report source/outcome mismatch')
    timeline = root/'project/timeline.json'
    if timeline.read_bytes() != payload['timeline.json']:
        raise ValueError('retained timeline differs from producer input')
    context = Ctx(timeline)
    project = context.tl['project']
    if any(project.get(k) != v for k,v in dict(fps=30,width=1920,height=1080,duration_frames=4,gfx_dependency_policy='strict').items()):
        raise ValueError('wrong strict fixture dimensions/policy')
    clips = [cl for track in context.tl['video_tracks'] if track.get('kind')=='gfx' for cl in track['clips']]
    required = [('a.html',2),('b.html',2)]
    if [(cl['src'],cl['dur']) for cl in clips] != required:
        raise ValueError('wrong required pages/frames')
    records = [resolve(context,cl) for cl in clips]
    if records != obj('dependencies.json'):
        raise ValueError('retained dependencies changed')
    provenance = obj('font-provenance.json')
    if provenance != accepted.font_provenance(Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')):
        raise ValueError('font package provenance changed')
    if (payload['font-LICENSE.txt'] != provenance['notice_text'].encode() or
            hashlib.sha256((root/'project/font.ttf').read_bytes()).hexdigest() != provenance['font_sha256']):
        raise ValueError('font/notice fixture mismatch')
    native = accepted.check_native_events(root/'evidence/strict-trace.events.jsonl',trace,
        timeline=timeline,stage='gfx',identity=identity,receipt=receipt,gfx_attempts=2)
    if native != report.get('native_binding'):
        raise ValueError('report native binding mismatch')
    accepted.check_round_counts(trace,[0,1])
    accepted.check_trace(trace,{r['url'] for record in records for r in record['resource_requests']})
    if producer.get('invocation') != trace['invocation'] or producer.get('complete') is not True:
        raise ValueError('producer invocation/outcome mismatch')
    if producer.get('timeline_path') != str(timeline) or producer.get('events_path') != str(root/'evidence/strict-trace.events.jsonl'):
        raise ValueError('producer paths mismatch')
    if str(uuid.UUID(producer['run_id'])) != producer['run_id']:
        raise ValueError('invalid producer run id')
    items = verifier.instances(producer.get('instances'))
    if set(items) != set(required):
        raise ValueError('missing/extra producer instances')
    for clip in clips:
        item = items[clip['src'],clip['dur']]
        if item['frame_dir'] != str(gfx_dir(context,clip)) or set(item['frames']) != {'0000','0001'}:
            raise ValueError('wrong producer frame coverage/directory')
        for frame, record in item['frames'].items():
            if record.get('run_id') != producer['run_id'] or record.get('origin') != 'rendered':
                raise ValueError('non-current strict frame')
            path=Path(item['frame_dir'])/('f'+frame+'.png')
            if path.is_symlink() or path.stat().st_nlink != 1:
                raise ValueError('aliased strict frame')
    config = {'scope':'strict_smoke','lifecycle':True,'process_samples':False,'trace':False}
    if producer.get('config') != config or producer.get('env_manifest',{}).get('git_sha') != identity['head']:
        raise ValueError('producer source/config mismatch')
    runtime = producer['runtime_identity']
    verifier._manifest(producer,dict(backend_id='cdp',config=config,
        effective_backend=runtime['effective_backend'],chromium_version=runtime['chromium_version']),'1920x1080')
    plan = dict(required_instances=[dict(src=s,dur=d) for s,d in required],
        input_fingerprint=producer['input_fingerprint'],runtime_identity=runtime,host_identity=producer['host_identity'])
    verifier._producer_binding(producer,plan,'1920x1080')
    raw = obj('strict-producer.runtime.json')
    if not isinstance(raw,dict) or not raw or {verifier.digest(v):v for v in raw.values()} != producer['producer_binding']['runtime']:
        raise ValueError('raw runtime set mismatch')
    for value in raw.values():
        if gfx_runtime.identity(value) != runtime:
            raise ValueError('runtime identity mismatch')
    if hashlib.sha256(payload['strict-trace.events.jsonl']).hexdigest()!=producer.get('events_sha256'):
        raise ValueError('retained event digest mismatch')
    parsed = events.read_events(payload['strict-trace.events.jsonl'].decode())
    commits = [row for row in parsed['events'] if row['event']=='frame_commit']
    seqs = {r['publication_seq'] for item in items.values() for r in item['frames'].values()}
    if len(commits)!=4 or len(seqs)!=4 or seqs!={r['seq'] for r in commits}:
        raise ValueError('wrong strict publication count/linkage')
    pixels=check_pixels(context,clips)
    if pixels != report.get('frames') or report.get('max_frames') != 4:
        raise ValueError('full-frame report mismatch')
    for row in pixels:
        if items[row['src'],2]['frames'][row['frame'][1:-4]]['sha256'] != row['rgba_sha256']:
            raise ValueError('producer full-frame hash mismatch')
    if source_identity()!=identity:
        raise ValueError('source drift during seal')
    return identity




def retained_return_code(directory):
    from scripts.pack_lifecycle_evidence import read_regular
    try:
        value = int(read_regular(directory/'exit-code.txt',32))
        return value if -255 <= value <= 255 else None
    except Exception:
        return None

def retained_runtime_status(directory):
    from scripts.pack_lifecycle_evidence import read_regular
    from scripts.bench_lifecycle import validate_runtime_observation_status
    try:
        return validate_runtime_observation_status(json.loads(read_regular(
            directory/'strict-producer.status.json', 512 * 1024)))
    except Exception:
        return {'schema': 'issue63.runtime_observation_status/1',
                'state': 'unavailable', 'observations': []}

def safe_partial(destination, *, reason, error_type=None, return_code=None, inventory=None, runtime_status=None, legacy_status=None):
    """Small fixed-schema diagnostic; never includes raw exception/log text."""
    destination.mkdir(parents=True,exist_ok=False)
    value=dict(schema='vtx.strict_smoke_artifact/2',complete=False,full_issue63_complete=False,
        reason=reason,error_type=error_type,return_code=return_code,
        originals_retained_locally=True,inventory=inventory or {},
        runtime_diagnostics=runtime_status,legacy_status=legacy_status)
    (destination/'partial-status.json').write_text(json.dumps(value,indent=2))
    (destination/'artifact-manifest.json').write_text(json.dumps({**value,'files':{
        'partial-status.json':{'bytes':(destination/'partial-status.json').stat().st_size,
        'sha256':hashlib.sha256((destination/'partial-status.json').read_bytes()).hexdigest()}}},indent=2))
    return value


def seal(root):
    """Always produce bounded upload selection; originals are never trimmed."""
    from scripts.pack_lifecycle_evidence import read_regular
    root=root.resolve(); evidence=root/'evidence'; output=root/'upload'
    payload={}; inventory={}; rc=None; phase='selection'
    try:
        if not evidence.is_dir() or evidence.is_symlink():raise ValueError('missing evidence')
        outcome=evidence/'exit-code.txt'
        if outcome.exists():rc=int(read_regular(outcome,32).decode())
        paths=sorted(evidence.iterdir())
        if len(paths)>24 or {p.name for p in paths}- (REQUIRED-{'smoke.log'}):
            raise ValueError('unexpected evidence files; no success trimming')
        for name in sorted(REQUIRED):
            path=root/'smoke.raw.log' if name=='smoke.log' else evidence/name
            if not path.exists():continue
            size=path.lstat().st_size
            inventory[name]={'bytes':size,'read_ok':False}
            payload[name]=read_regular(path,LIMIT-sum(map(len,payload.values())))
            inventory[name]['read_ok']=True
        if set(payload)!=REQUIRED:raise ValueError('missing required evidence')
        rc=int(payload['exit-code.txt'].decode())
        if rc!=0:raise ValueError('smoke command failed')
        phase='semantic_validation'
        identity=validate_retained(root,payload)
        from scripts.bench_lifecycle import validate_runtime_observation_status, runtime_observation_status
        status = validate_runtime_observation_status(json.loads(payload['strict-producer.status.json']))
        if status != runtime_observation_status(json.loads(payload['strict-producer.runtime.json'])):
            raise ValueError('runtime diagnostic differs from original observation')
        files={n:dict(bytes=len(b),sha256=hashlib.sha256(b).hexdigest()) for n,b in payload.items()}
        result=dict(schema='vtx.strict_smoke_artifact/2',complete=True,return_code=rc,
                    identity=identity,files=files,full_issue63_complete=False)
        metadata=json.dumps(result,indent=2).encode()
        if len(metadata)+sum(map(len,payload.values()))>LIMIT:raise ValueError('metadata exceeds budget')
    except Exception as error:
        return safe_partial(output,reason='strict_'+phase+'_invalid_or_incomplete',
            error_type=type(error).__name__,return_code=rc,inventory=inventory,
            runtime_status=retained_runtime_status(evidence))
    output.mkdir(parents=True,exist_ok=False)
    for name,data in payload.items():(output/name).write_bytes(data)
    (output/'artifact-manifest.json').write_bytes(metadata)
    return result


def select_uploads(root,bundle, *, legacy_parent=None):
    """A shared32MiB selection, or a tiny explicit failure-only selection."""
    from scripts.pack_lifecycle_evidence import read_regular
    root=root.resolve(); bundle=bundle.resolve(); output=root/'selected-upload'; payload={}
    try:
        for category,directory in [('strict',root/'upload'),('lifecycle',bundle)]:
            if not directory.is_dir() or directory.is_symlink():raise ValueError('missing upload source')
            paths=sorted(directory.iterdir())
            if not paths or len(paths)>24:raise ValueError('invalid upload source count')
            for path in paths:
                if path.is_symlink() or not path.is_file():raise ValueError('invalid upload member')
                payload[category+'/'+path.name]=read_regular(path,32*1024*1024-sum(map(len,payload.values())))
        output.mkdir(parents=True,exist_ok=False)
        for name,data in payload.items():
            path=output/name;path.parent.mkdir(exist_ok=True);path.write_bytes(data)
        return True
    except Exception as error:
        # All validation happens before output creation. Fixed reason/type only.
        safe_partial(output,reason='shared_evidence_missing_invalid_or_overbudget',error_type=type(error).__name__,
            runtime_status=retained_runtime_status(root/'evidence'),
            legacy_status=('not_run' if legacy_parent is not None and not legacy_parent.exists() else
                'bundle_unavailable' if not bundle.exists() else 'bundle_invalid_or_overbudget'),
            return_code=retained_return_code(root/'evidence'))
        return False


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--seal',action='store_true')
    parser.add_argument('--select-uploads',action='store_true')
    parser.add_argument('--bundle',type=Path)
    parser.add_argument('--legacy-parent',type=Path)
    args=parser.parse_args()
    if args.select_uploads:
        if args.bundle is None:parser.error('--bundle required')
        raise SystemExit(0 if select_uploads(args.output,args.bundle,legacy_parent=args.legacy_parent) else 1)
    if args.seal:raise SystemExit(0 if seal(args.output)['complete'] else 1)
    run(args.output)


if __name__=='__main__':main()
