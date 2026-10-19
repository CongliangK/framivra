#!/usr/bin/env python3
"""Execute native CPU-only stages on a proven audio-only projection. No full render."""
import argparse,copy,hashlib,json,os,shutil,subprocess,sys,time
from pathlib import Path
ap=argparse.ArgumentParser(description=__doc__)
ap.add_argument('--repo',type=Path,required=True);ap.add_argument('--project',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);ap.add_argument('--expected-commit',required=True)
args=ap.parse_args();CORE=args.repo.resolve();PACKAGE=args.project.resolve();PROJECTION=args.out.resolve()
actual_commit=subprocess.run(['git','rev-parse','HEAD'],cwd=CORE,capture_output=True,text=True,check=True).stdout.strip()
assert actual_commit==args.expected_commit,'Checkout differs from the explicit reviewed commit'
assert not any(k.startswith(('DYLD_','LD_')) for k in os.environ),'Use the qualified clean toolchain environment; do not pass dynamic-loader overrides'
assert (PACKAGE/'timeline.json').is_file()
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
sys.path.insert(0,str(CORE))
from vtx.infra import Ctx
from vtx.stages import _audio_graph,TARGET_LUFS
from vtx.pluginlib.hooks import active_plugins,active_plugins_digest
from vtx.gfx_backend import selected
from vtx.hygiene import gfx_cache_ns
assert selected()=='cdp' and gfx_cache_ns()==''
assert not PROJECTION.exists(),'Preserve previous output; use a new explicitly reviewed destination'
assert PACKAGE not in PROJECTION.parents and PROJECTION!=PACKAGE,'Projection must be outside the frozen production source folder'
full=json.loads((PACKAGE/'timeline.json').read_text())
audio_ids={e['src'] for t in full['audio_tracks'] for e in t['events']};assert audio_ids=={'T17','T18','T19','T20'}
stems=json.loads((PACKAGE/'audio/stems.json').read_text())['stems']
assert len(stems)==4 and {s['id'] for s in stems}==audio_ids,'Invalid canonical stem manifest'
for source in (s for s in full['sources'] if s['id'] in audio_ids):
    item=next(s for s in stems if s['id']==source['id'])
    assert source['path']==item['path']=='audio/'+source['id']+'.flac','Audio source path differs from the canonical manifest'
    assert (PACKAGE/source['path']).is_file(),'Missing runtime audio: '+source['path']+'; import the canonical pack first'
    assert sha(PACKAGE/source['path'])==item['sha256'],'Runtime audio bytes differ from the canonical manifest'
PROJECTION.mkdir(parents=True);(PROJECTION/'audio').mkdir()
projected=copy.deepcopy(full);projected['project'].pop('cache_dependencies',None);projected['sources']=[s for s in full['sources'] if s['id'] in audio_ids];projected['video_tracks']=[];projected['subtitles']=[]
for source in projected['sources']:shutil.copy2(PACKAGE/source['path'],PROJECTION/source['path'])
(PROJECTION/'timeline.json').write_text(json.dumps(projected,indent=2)+'\n')
c=Ctx(PROJECTION/'timeline.json');whole=copy.copy(c);whole.tl=full
assert active_plugins(c)==active_plugins(whole)==[] and active_plugins_digest(c)==active_plugins_digest(whole)==''
a,b=_audio_graph(c),_audio_graph(whole);assert a==b
assert c.gfx_backend is None
relevant={'project':projected['project'],'sources':projected['sources'],'audio_tracks':projected['audio_tracks']}
record={'format':'framivra-audio-projection-proof/1','status':'PURE_GRAPH_EQUIVALENCE_BEFORE_NATIVE_EXECUTION','full_project_pass':False,'full_timeline_sha256':sha(PACKAGE/'timeline.json'),'projection_timeline_sha256':sha(PROJECTION/'timeline.json'),'audio_inputs_signature':hashlib.sha256(json.dumps(relevant,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'audio_relevant_inputs':relevant,'source_sha256':{s['id']:sha(PROJECTION/s['path']) for s in projected['sources']},'active_plugins':[],'graph_identical':True,'graph':a,'target_lufs':TARGET_LUFS,'graph_sha256':hashlib.sha256(json.dumps(a,sort_keys=True).encode()).hexdigest(),'omitted':{'video_tracks':'All15 visual tracks; native _audio_graph reads only audio_tracks, explicitly compared against the full timeline in a read-only copied context.','subtitles':'Native audio graph does not consume subtitles.','paper':'Paper source has no audio event. Its silent source stream would be prepared but is never an input to the native audio graph.','cache_dependencies':'Only GFX/source cache declarations; no audio transform parameters. Audio source IDs, bytes, fps, duration, events, volumes and no-plugin state preserved.'},'browser_or_gfx_called':False,'core_commit':subprocess.run(['git','rev-parse','HEAD'],cwd=CORE,capture_output=True,text=True,check=True).stdout.strip(),'core_source_hashes':{p:sha(CORE/p) for p in ['vtx/stages.py','vtx/infra.py','vtx/cache_state.py','vtx/hygiene.py','vtx/constraints.py','vtx/pluginlib/hooks.py','vtx/pluginlib/registry.py','vtx/cli.py']},'ffmpeg_version':subprocess.run(['ffmpeg','-version'],capture_output=True,text=True,check=True).stdout.splitlines()[0],'commands':[],'user_full_render_postmix_check_required':True}
record['projection_runner_sha256']=sha(Path(__file__).resolve())
record['toolchain']={}
for tool in ['ffmpeg','ffprobe']:
    executable=Path(shutil.which(tool)).resolve()
    record['toolchain'][tool]={'path':str(executable),'sha256':sha(executable),'version':subprocess.run([str(executable),'-version'],capture_output=True,text=True,check=True).stdout.splitlines()[0]}
(PROJECTION/'projection-proof.json').write_text(json.dumps(record,indent=2)+'\n')
for stage in ['validate','prepare','audio']:
    command=[sys.executable,'-m','vtx','-t',str(PROJECTION/'timeline.json'),stage]
    start=time.monotonic();result=subprocess.run(command,cwd=CORE,capture_output=True,text=True,env={**os.environ,'VTX_IO_WORKERS':'1'},timeout=180)
    (PROJECTION/f'{stage}.log').write_text(result.stdout+result.stderr)
    record['commands'].append({'argv':command,'cwd':str(CORE),'temporary_resource_env':{'VTX_IO_WORKERS':'1'},'seconds':time.monotonic()-start,'exit_code':result.returncode})
    (PROJECTION/'projection-proof.json').write_text(json.dumps(record,indent=2)+'\n')
    print(stage,result.returncode,flush=True)
    if result.returncode:raise SystemExit(result.returncode)
record['status']='NATIVE_AUDIO_ONLY_PROJECTION_COMPLETED';record['postmix_sha256']=sha(PROJECTION/'out/audio.wav');record['receipt_sha256']=sha(PROJECTION/'out/receipt.json')
(PROJECTION/'projection-proof.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({'status':record['status'],'postmix_sha256':record['postmix_sha256']}))
