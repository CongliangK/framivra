#!/usr/bin/env python3
"""Read-only source/media checks. No renderer, font install or host changes."""
import argparse, hashlib, json, math, re, shutil, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
    return h.hexdigest()
def decode(path,codec='pcm_s24le',fmt='s24le'):
    result=subprocess.run(['ffmpeg','-nostdin','-v','error','-xerror','-i',str(path),'-map','0:a:0','-c:a',codec,'-f',fmt,'-'],capture_output=True,check=True,timeout=120)
    return result.stdout

def waveform_errors(value,binding):
    errors=[]
    try:
        assert value['format']=='framivra-waveform/1'
        assert value['analysis_frame_window']==[1680,2160] and value['analysis_sample_window']==[2688000,3456000]
        assert value['bins']==480 and len(value['samples'])==480
        assert binding['format']=='framivra-native-postmix-reference/2' and binding['evidence_scope']=='PORTABLE_REFERENCE_METADATA_NOT_AN_EXECUTION_RECEIPT'
        p=value['provenance']
        assert all(re.fullmatch('[0-9a-f]{64}',p[k]) for k in ['sha256','pcm_sha256','mix_recipe_sha256'])
        assert p['source']=='out/audio.wav' and p['source_codec']=='pcm_s16le'
        assert p['kind']=='actual-pcm' and p['stage']=='framivra-audio-stage'
        assert (p['sample_rate'],p['channels'],p['sample_frames'])==(48000,2,5760000)
        assert p['sha256']==binding['postmix']['sha256'] and p['pcm_sha256']==binding['postmix']['pcm_sha256']
        assert p['pcm_hash_representation']==binding['postmix']['pcm_hash_representation']
        assert value['amplitude_mapping']['kind']=='stereo-channel-union' and value['amplitude_mapping']['fixed_scale']==1
        for index,bin in enumerate(value['samples']):
            frame=1680+index
            assert (bin['frame'],bin['sample_start'],bin['sample_end'])==(frame,frame*1600,(frame+1)*1600)
            values=[bin[k] for k in ['min','max','value']]
            assert all(type(v) in (int,float) and math.isfinite(v) for v in values)
            assert -1<=bin['min']<=bin['max']<=1 and 0<=bin['value']<=1
            assert bin['value']==max(abs(bin['min']),abs(bin['max']))
    except (AssertionError,KeyError,TypeError,ValueError):errors.append('malformed or unbound real postmix waveform')
    return errors

def portable_provenance_errors(value,binding,relevant):
    """Validate a portable reference, never an execution receipt or host attestation."""
    try:
        expected={'format','scope','full_execution_proof_included','full_project_pass','user_full_render_postmix_check_required','core_commit','core_source_hashes','declared_toolchain','audio_inputs_signature','audio_relevant_inputs','source_sha256','active_plugins','target_lufs','reference_execution_summary','postmix_pcm_contract','projection_scope','evidence_notice'}
        assert set(value)==expected
        assert value['format']=='framivra-native-audio-portable-provenance/1'
        assert value['scope']==binding['evidence_scope']=='PORTABLE_REFERENCE_METADATA_NOT_AN_EXECUTION_RECEIPT'
        assert value['full_execution_proof_included'] is False and value['full_project_pass'] is False
        assert value['user_full_render_postmix_check_required'] is True
        assert re.fullmatch('[0-9a-f]{40}',value['core_commit']) and value['core_commit']==binding['core_commit']
        core={'vtx/stages.py','vtx/infra.py','vtx/cache_state.py','vtx/hygiene.py','vtx/constraints.py','vtx/pluginlib/hooks.py','vtx/pluginlib/registry.py','vtx/cli.py'}
        assert set(value['core_source_hashes'])==core
        assert all(re.fullmatch('[0-9a-f]{64}',h) for h in value['core_source_hashes'].values())
        assert set(value['declared_toolchain'])=={'ffmpeg_version','audio_profile'}
        assert re.fullmatch(r'[0-9]+[.][0-9]+[.][0-9]+',value['declared_toolchain']['ffmpeg_version'])
        assert value['audio_relevant_inputs']==relevant==binding['audio_relevant_inputs']
        signature=hashlib.sha256(json.dumps(relevant,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        assert value['audio_inputs_signature']==signature==binding['audio_inputs_signature']
        assert value['source_sha256']==binding['stems'] and value['active_plugins']==[] and value['target_lufs']==-14.0
        assert value['reference_execution_summary']=={'native_stages':['validate','prepare','audio'],'audio_graph_equivalence_recorded':True,'browser_or_gfx_called':False,'postmix_sha256':binding['postmix']['sha256']}
        assert value['postmix_pcm_contract']=={k:v for k,v in binding['postmix'].items() if k!='mix_recipe_sha256'}
        assert set(value['projection_scope'])=={'video_tracks','subtitles','paper','cache_dependencies'}
        # Portable metadata must not grow host paths, environment dumps or invocation records.
        def strings(v):
            if isinstance(v,dict):
                for k,x in v.items():yield k;yield from strings(x)
            elif isinstance(v,list):
                for x in v:yield from strings(x)
            elif isinstance(v,str):yield v
        assert not any(re.search(r'(?:^|[\s\"\'])/(?:workspace|home|Users|opt|private|tmp)/',s) for s in strings(value))
        assert isinstance(value['evidence_notice'],str) and 'does not execute' in value['evidence_notice']
    except (AssertionError,KeyError,TypeError,ValueError):
        return ['invalid portable native-audio provenance; it is not an execution receipt']
    return []

def check(caption_font=None,postmix=None):
    errors=[];pending=[];checks={}
    tl=json.loads((ROOT/'timeline.json').read_text());m=json.loads((ROOT/'entry-manifest.json').read_text())
    checks['timeline_sha256']=sha(ROOT/'timeline.json')
    if checks['timeline_sha256']!=m['timeline_sha256']:errors.append('timeline changed: rebuild exact source bindings before use')
    if (tl['project']['fps'],tl['project']['duration_frames'],len(tl['video_tracks']),len(tl['audio_tracks']),len(tl['subtitles']))!=(30,3600,15,4,8):errors.append('full-film frame or role contract drift')
    for d in json.loads((ROOT/'runtime-dependencies.json').read_text()):
        p=ROOT/d['path']
        if not p.is_file() or sha(p)!=d['sha256']:errors.append('dependency changed or missing: '+d['path'])
    recipe=json.loads((ROOT/'media/paper-recipe.json').read_text());paper=ROOT/recipe['package_path']
    checks['paper']={'path':recipe['package_path'],'expected_sha256':recipe['sha256']}
    if not paper.is_file():errors.append('missing required visual source: '+recipe['package_path'])
    else:
        checks['paper']['sha256']=sha(paper)
        if checks['paper']['sha256']!=recipe['sha256']:errors.append('paper background hash mismatch')
    audio_ids={e['src'] for t in tl['audio_tracks'] for e in t['events']}
    for source in tl['sources']:
        if source['id'] not in audio_ids and not (ROOT/source['path']).is_file():
            message='missing required visual source: '+source['path']
            if message not in errors:errors.append(message)
    manifest_path=ROOT/'audio/stems.json';manifest=None
    if manifest_path.is_file():
        try:manifest=json.loads(manifest_path.read_text());assert len(manifest['stems'])==4 and set(x['id'] for x in manifest['stems'])=={'T17','T18','T19','T20'}
        except (ValueError,KeyError,TypeError,AssertionError):errors.append('invalid audio/stems.json');manifest=None
    stems={}
    for ident in ['T17','T18','T19','T20']:
        source=next(s for s in tl['sources'] if s['id']==ident);p=ROOT/source['path']
        if not p.is_file():
            pending.append('missing '+source['path']);errors.append('missing required audio source: '+source['path']);continue
        if manifest is None:errors.append('present audio lacks a valid exact PCM manifest: '+ident);continue
        item=next(x for x in manifest['stems'] if x['id']==ident)
        if item['path']!=source['path'] or sha(p)!=item['sha256']:errors.append(ident+' source hash/path mismatch');continue
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):errors.append('FFmpeg/ffprobe required to verify present audio');continue
        try:
            probe=json.loads(subprocess.run(['ffprobe','-v','error','-show_streams','-of','json',str(p)],capture_output=True,text=True,check=True,timeout=30).stdout)['streams']
            assert len(probe)==1 and probe[0]['codec_name']=='flac'
            assert (int(probe[0]['sample_rate']),probe[0]['channels'],int(probe[0]['bits_per_raw_sample']))==(48000,2,24)
            pcm=decode(p);digest=hashlib.sha256(pcm).hexdigest();count=len(pcm)//6
            assert len(pcm)==5760000*6 and digest==item['pcm24_sha256']
            del pcm
            stems[ident]={'sha256':item['sha256'],'decoded_pcm24_sha256':digest,'sample_frames':count,'full_decode_verified':True}
        except (AssertionError,KeyError,ValueError,subprocess.SubprocessError) as e:errors.append(ident+' failed full FLAC/PCM verification: '+str(e))
    checks['audio']=stems
    envelope=ROOT/'audio/waveform-S5.json';binding_path=ROOT/'audio/native-postmix.json'
    if not envelope.is_file():pending.append('missing real postmix PCM waveform')
    else:
        try:
            value=json.loads(envelope.read_text());binding=json.loads(binding_path.read_text())
            errors.extend(waveform_errors(value,binding))
            if binding['portable_provenance_path']!='audio/native-audio-provenance.json':raise ValueError('unexpected portable provenance path')
            provenance_path=ROOT/binding['portable_provenance_path']
            if sha(provenance_path)!=binding['portable_provenance_sha256'] or value['provenance']['mix_recipe_sha256']!=binding['portable_provenance_sha256'] or binding['postmix']['mix_recipe_sha256']!=binding['portable_provenance_sha256']:errors.append('portable native-audio provenance identity mismatch')
            project={k:v for k,v in tl['project'].items() if k!='cache_dependencies'}
            relevant={'project':project,'sources':[s for s in tl['sources'] if s['id'] in audio_ids],'audio_tracks':tl['audio_tracks']}
            provenance_errors=portable_provenance_errors(json.loads(provenance_path.read_text()),binding,relevant)
            errors.extend(provenance_errors)
            signature=hashlib.sha256(json.dumps(relevant,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            if signature!=binding['audio_inputs_signature']:errors.append('timeline audio parameters differ from native postmix reference')
            if sha(envelope)!=binding['waveform_sha256']:errors.append('waveform file hash mismatch')
            if binding['stems']!={k:v['sha256'] for k,v in stems.items()}:errors.append('postmix reference does not bind these exact verified stems')
            embedded=json.loads((ROOT/'src/assets/media-bindings.json').read_text())
            if embedded['waveform']!=value:errors.append('embedded waveform differs; rebuild before rendering')
            checks['waveform']={'sha256':sha(envelope),'reference_postmix_sha256':binding['postmix']['sha256'],'reference_link_verified':not waveform_errors(value,binding),'portable_provenance_verified':not provenance_errors,'execution_receipt_verified':False,'supplied_postmix_pcm_verified':False}
            if postmix:
                pcm=decode(Path(postmix),'pcm_f32le','f32le');digest=hashlib.sha256(pcm).hexdigest()
                if len(pcm)!=5760000*8 or digest!=binding['postmix']['pcm_sha256']:errors.append('actual postmix PCM differs from the S5 waveform reference')
                else:checks['waveform']['supplied_postmix_pcm_verified']=True
        except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as e:errors.append('invalid or unbound real postmix waveform: '+str(e))
    font=json.loads((ROOT/'production/caption-font.json').read_text());chosen=Path(caption_font) if caption_font else None
    if chosen is None and shutil.which('fc-match'):
        proc=subprocess.run(['fc-match','-f','%{file}\n','Noto Sans CJK SC:style=Bold'],capture_output=True,text=True)
        line=proc.stdout.splitlines()
        if proc.returncode==0 and line:chosen=Path(line[0])
    checks['caption_font_lookup']={'candidate':str(chosen) if chosen else None,'expected_sha256':font['sha256'],'native_family':font['native_ass_family']}
    if chosen is None or not chosen.is_file():pending.append('native caption font unavailable: Noto Sans CJK SC Bold')
    elif sha(chosen)!=font['sha256']:errors.append('native caption font hash mismatch; fallback is not accepted as deterministic evidence')
    else:checks['caption_font_lookup']['exact_bytes_available']=True
    pending+=['independent combined-source review','user audio audition approval','actual-engine output and native ASS font-selection evidence']
    checks.update({'source_errors':errors,'pending_acceptance':pending,'production_ready':False,'font_installed':False,'browser_or_engine_run':False})
    return checks
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--caption-font',type=Path);ap.add_argument('--postmix',type=Path,help='Read and compare the supplied out/audio.wav PCM against the S5 reference; this does not establish who ran it.');ap.add_argument('--source-only',action='store_true',help='Source-integrity exit status only; present media is fully verified and pending acceptance stays visible.')
    args=ap.parse_args();r=check(args.caption_font,args.postmix);print(json.dumps(r,ensure_ascii=False,indent=2));sys.exit(bool(r['source_errors']) or (not args.source_only and bool(r['pending_acceptance'])))
