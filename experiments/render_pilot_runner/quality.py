"""Full-pixel comparisons and a dense local N1 pair gate, never a full-scene claim."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

from .contract import TARGETS, WIDTH, HEIGHT, FPS, require, file_hash, digest, canonical, clock_now, CLOCK_KIND


def png_comparison(left, right, width=WIDTH, height=HEIGHT):
    import numpy as np
    from vtx.gfx_cli import png_invalid_reason
    from vtx.gfx_render import png_decode_rgba
    arrays=[]; hashes=[]
    for p in (left,right):
        fingerprints=[];properties={}
        require(png_invalid_reason(p,width=width,height=height,_fingerprint=fingerprints,
                                   _properties=properties) is None,'Invalid comparison PNG')
        data=Path(p).read_bytes()
        require(fingerprints==[digest(data)],'PNG changed between validation and decode')
        require(properties.get('depth')==8 and properties.get('color') in (2,6)
                and not properties.get('transparency') and not properties.get('animated')
                and not properties.get('unsafe_unknown'),'Unsupported PNG variant; no silent alpha loss')
        arrays.append(png_decode_rgba(data));hashes.append((digest(data),len(data)))
    a,b=arrays
    require(a.shape == b.shape == (height,width,4),'Unexpected RGBA shape')
    delta=np.abs(a.astype(np.int16)-b.astype(np.int16))
    return {'equal':bool(np.array_equal(a,b)), 'different_pixels':int(np.any(delta,axis=2).sum()),
            'different_alpha_pixels':int((delta[:,:,3]!=0).sum()),'max_channel_delta':int(delta.max()),
            'left':{'png_sha256':hashes[0][0],'bytes':hashes[0][1],
                    'rgba_sha256':digest(a.tobytes()),'alpha_sha256':digest(a[:,:,3].tobytes())},
            'right':{'png_sha256':hashes[1][0],'bytes':hashes[1][1],
                    'rgba_sha256':digest(b.tobytes()),'alpha_sha256':digest(b[:,:,3].tobytes())}}


def frame_index(arm, expected_jobs=None):
    result={}
    require(arm['status']=='RENDERED_UNVERIFIED','Only completed fresh render arms may be compared')
    if expected_jobs is not None:
        require(canonical([a.get('job') for a in arm['attempts']]) == canonical(expected_jobs),
                'Actual jobs differ from verified plan')
    for attempt in arm['attempts']:
        require(attempt['status']=='RENDERED_UNVERIFIED' and attempt['exit_code']==0,'Failed attempt')
        if expected_jobs is not None:
            expected = {(r['global'], r['local']) for r in attempt['job']['mapping']}
            actual = [(r.get('global'), r.get('local')) for r in attempt['png']]
            require(all(type(g) is int and type(l) is int for g,l in actual)
                    and len(actual) == len(expected) and set(actual) == expected,
                    'Missing, duplicate or incorrect global/local frame coverage')
            require(len({r['path'] for r in attempt['png']}) == len(expected),
                    'Target paths must be distinct fresh frame files')
        for row in attempt['png']:
            if expected_jobs is not None:
                require(Path(row['path']).name == f"f{row['local']:04d}.png",
                        'PNG filename does not match original local frame')
            key=(attempt['job']['entry']['entryId'],row['global'])
            require(key not in result,'Duplicate rendered frame')
            p=Path(row['path'])
            require(file_hash(p)==row['sha256'],'Fresh frame changed before comparison')
            result[key]=p
    if expected_jobs is not None:
        require(len(set(result.values())) == len(result), 'A fresh target file cannot represent multiple entries')
    require(result,'Empty arm frame index')
    return result


def compare_rendered_pngs(arms,deadline,plan=None):
    if plan is not None:
        from .runner import verify_plan
        verify_plan(plan)
        require(plan.get('schema') == 'render-pilot/2', 'Expected verified v2 plan')
        require([a.get('arm') for a in arms] == ['baseline','candidate'], 'Wrong named arm identities')
        indices=[frame_index(arm, [j for j in plan['jobs'] if j['arm']==arm['arm']]) for arm in arms]
        require(all(len(i)==plan['expected_rgba_pairs'] for i in indices),
                'New profile requires its exact complete RGBA pair count')
    else:
        indices=[frame_index(arm) for arm in arms]
    require(indices[0].keys()==indices[1].keys(),'A/B targets differ')
    result={'scope':'full PNG decoded RGBA and alpha; no full-scene/audio acceptance','frames':[],'equal':True}
    for key in indices[0]:
        require(clock_now()<deadline,'Pair deadline during PNG comparison')
        comparison=png_comparison(indices[0][key],indices[1][key])
        result['frames'].append({'entry':key[0],'global':key[1],**comparison})
        result['equal'] &= comparison['equal']
    return result


def dense_sequences(arms,root,targets=TARGETS,width=WIDTH,height=HEIGHT):
    """Copy *fresh* rendered targets only. Explicit inactive fill is not a render."""
    import numpy as np
    from vtx.gfx_render import png_encode_rgba
    require(targets and len(set(targets))==len(targets),'Nonempty unique dense targets required')
    indices=[frame_index(a) for a in arms]
    wanted_baseline={('T07.01',f) for f in targets}|{('T08.01',f) for f in targets if f>=18}
    require(set(indices[0])==wanted_baseline,'Baseline global/active frame coverage differs')
    require(set(indices[1])=={('T08_T07',f) for f in targets},'Candidate global frame coverage differs')
    empty=png_encode_rgba(np.zeros((height,width,4),dtype=np.uint8))
    seqs={}; fills=[]
    expected_hashes={row['path']:row['sha256'] for arm in arms for attempt in arm['attempts'] for row in attempt['png']}
    for arm,ids in [('baseline',['T08.01','T07.01']),('candidate',['T08_T07'])]:
        for entry in ids:
            path=root/arm/entry
            path.mkdir(parents=True,exist_ok=False)
            seqs[(arm,entry)]=path
            for dense,global_ in enumerate(targets):
                out=path/f'f{dense:04d}.png'
                src=indices[0 if arm=='baseline' else 1].get((entry,global_))
                if src is None:
                    require(entry=='T08.01' and global_<18,'Cannot fabricate active/missing input')
                    out.write_bytes(empty)
                    fills.append({'arm':arm,'entry':entry,'global':global_,'dense':dense,
                                  'reason':'inactive role; transparent quality-only placeholder'})
                else:
                    shutil.copyfile(src,out)
                    require(file_hash(out)==expected_hashes[str(src)],'Fresh frame changed while copying')
    return seqs,{'dense_to_global':[{'dense':i,'global':f} for i,f in enumerate(targets)],
                'transparent_fills':fills,'non_rendering_copies':sum(len(i) for i in indices),
                'timing':'quality preparation only, excluded from GFX work count'}


def dense_n1_commands(sequences,output_root,background,count=120,width=WIDTH,height=HEIGHT):
    """Call unmodified RenderPlan full N1. Archive source argv and exact sink edits.

    Pixels already contain original global animation state; dense time is only
    the local compositing index. This does not reproduce the other 17 roles,
    subtitles, original film scheduling or N1's full scene negotiation context.
    """
    from vtx.composite import RenderPlan
    from vtx.frame_source import PngSequence
    require(background in ('transparent','black','white'),'Unsupported local background')
    require(count>0,'Empty dense N1 sequence')
    output_root=Path(output_root)
    commands=[]
    for arm,ids in [('baseline',['T08.01','T07.01']),('candidate',['T08_T07'])]:
        captured=[]
        ctx=SimpleNamespace(fps=FPS,spf=1/FPS,dur=count,cache=output_root,dry=False,gpu=False,
                            run=lambda argv:captured.append(list(argv)))
        plan=RenderPlan(ctx,False,1.0,width,height,nframes=None,nseg=1)
        layers=[]
        for index,entry in enumerate(ids):
            source=PngSequence(sequences[(arm,entry)],entry,count,FPS,width,height)
            layers.append({'cl':{'dst_in':0,'dur':count},'source':source,'gfx':True,
                           'x':0,'y':0,'w2':None,'h2':None,'ti':index})
        plan.composite(layers,output_root/f'{arm}-original.mp4',False,a=0,b=None)
        require(len(captured)==1,'Unexpected compositor command count')
        original=captured[0]
        argv=list(original)
        graph_index=argv.index('-filter_complex')+1
        edits=[]
        if background=='white':
            color_index=next(i for i,x in enumerate(argv) if x.startswith('color=c=black:'))
            argv[color_index]=argv[color_index].replace('color=c=black:','color=c=white:',1)
            edits.append('source canvas black -> white; original yuv420p canvas retained')
        if background=='transparent':
            color_index=next(i for i,x in enumerate(argv) if x.startswith('color=c=black:'))
            argv[color_index]=argv[color_index].replace('color=c=black:','color=c=black@0:',1)+',format=rgba'
            argv[graph_index]=argv[graph_index].replace('[0:v]format=yuv420p[cv]','[0:v]format=rgba[cv]',1)
            edits.append('transparent probe canvas explicitly rgba; differs from opaque full-film canvas')
        if background!='transparent':
            # The original encoder requires yuv420p. Keep that exact terminal
            # constraint so a PNG rgba sink cannot renegotiate upstream overlay
            # formats. Only then convert its pre-encode pixels for lossless I/O.
            argv[graph_index]+=';[vout]format=yuv420p,format=rgba[pilot_rgba]'
            argv[argv.index('-map')+1]='[pilot_rgba]'
            edits.append('retain original encoder-input yuv420p constraint before lossless rgba transport')
        # Keep all input options, N1 clock/padding/overlay=...:format=auto, output
        # frame cap and CFR settings. Replace only lossy codec/pix_fmt/output.
        codec=argv.index('-c:v')
        output=output_root/f'{background}-{arm}'
        argv=argv[:codec]+['-c:v','png','-pix_fmt','rgba','-threads','1',str(output/'f%04d.png')]
        # image2 defaults to frame 1; explicit zero index is part of the sink.
        argv[-1:-1]=['-start_number','0']
        argv[argv.index('-v')+1]='verbose'  # negotiation evidence, no invented timing field
        edits.append('libx264 CRF17 medium yuv420p -> lossless PNG rgba sink; not final encoding acceptance')
        commands.append({'arm':arm,'background':background,'original_n1_argv':original,'argv':argv,
                         'original_filtergraph':original[graph_index], 'filtergraph':argv[graph_index],
                         'sink_changes':edits,'output':str(output)})
    return commands


def run_quality(spec_file):
    """Runs inside the parent's existing supervised process group and deadline."""
    from .runner import write_json
    spec=json.loads(Path(spec_file).read_bytes()); root=Path(spec['output'])
    deadline=spec['deadline_monotonic']; quality=root/'quality'
    report={'schema':'render-pilot-quality/1','status':'FAILED','equal':False,'attempts':[],
            'full_n1_scene':'UNRUN','real_paper_background':'UNRUN: original prepared paper not supplied',
            'subtitles_audio_final_encoding':'UNRUN'}
    before=clock_now()
    binding=None
    if spec['plan'].get('schema')=='render-pilot/2':
        report.update(schema='render-pilot-quality/2',runtime_identity={
            'status':'UNKNOWN','namespace':spec.get('namespace'),'run':spec['output'],'arm':'quality',
            'pid':os.getpid(),'ppid':os.getppid(),'pgid':os.getpgid(0),'argv':list(sys.argv),
            'cwd':os.getcwd(),'reason':'source/code checks not yet complete'})
    try:
        require(spec.get('clock_kind')==CLOCK_KIND,'Unknown shared deadline clock')
        if spec['plan'].get('schema')=='render-pilot/2':
            from . import runner
            from .runtime_identity import WorkerBinding
            runner.verify_plan(spec['plan'])
            runner.git_identity(runner.ROOT,spec['implementation']['commit'],spec['implementation']['tree'],
                                deadline=deadline)
            entry=sys.modules['__main__']
            if getattr(entry,'__file__',None) != runner.__file__:
                entry=runner
            binding=WorkerBinding(spec,spec_file,'quality',entry)
            report['runtime_identity']=binding.report
        arms=[json.loads((root/a/'result.json').read_bytes()) for a in ('baseline','candidate')]
        if spec['plan']['mode']=='poll-observation':
            from .runner import verify_poll_arm
            report['poll_observation']=[verify_poll_arm(arm,spec) for arm in arms]
        if spec['plan']['mode'] in ('fast-png','poll-observation'):
            report['png_comparison']=compare_rendered_pngs(arms,deadline,spec['plan']) \
                if spec['plan'].get('schema')=='render-pilot/2' else compare_rendered_pngs(arms,deadline)
            report['equal']=report['png_comparison']['equal']
            require(report['equal'],'Full RGBA/alpha mismatch')
            report['status']='LOCAL_RGBA_PAIR_PASSED_FULL_SCENE_UNRUN'
        elif spec['plan']['mode']=='group':
            seqs,mapping=dense_sequences(arms,quality/'dense')
            report['mapping']=mapping
            report['compositor_sha256']=file_hash(Path(__file__).resolve().parents[2]/'vtx/composite.py')
            report['comparisons']=[]
            for background in ('transparent','black','white'):
                commands=dense_n1_commands(seqs,quality,background)
                for command in commands:
                    require(clock_now()<deadline,'Pair deadline before N1 comparison')
                    target=Path(command['output']); target.mkdir(exist_ok=False)
                    command.update(returncode=None,status='RUNNING')
                    report['attempts'].append(command)
                    write_json(quality/'result.json',report)
                    with open(target/'ffmpeg.stdout.log','wb') as out,open(target/'ffmpeg.stderr.log','wb') as err:
                        started=clock_now()
                        try:
                            proc=subprocess.run(command['argv'],stdout=out,stderr=err,
                                                timeout=max(0.001,deadline-clock_now()))
                            command['returncode']=proc.returncode
                            require(proc.returncode==0,'N1 FFmpeg attempt failed')
                            command['status']='SUCCEEDED'
                        except subprocess.TimeoutExpired:
                            command['status']='TIMED_OUT'; raise
                        finally:
                            command['wall_seconds']=clock_now()-started
                            write_json(quality/'result.json',report)
                    require(len(list(target.glob('f*.png')))==len(TARGETS),'Missing/extra N1 output frames')
                comparisons=[]
                for dense,global_ in enumerate(TARGETS):
                    require(clock_now()<deadline,'Pair deadline during N1 RGBA comparison')
                    comparisons.append({'dense':dense,'global':global_,**png_comparison(
                        Path(commands[0]['output'])/f'f{dense:04d}.png',
                        Path(commands[1]['output'])/f'f{dense:04d}.png')})
                equal=all(x['equal'] for x in comparisons)
                report['comparisons'].append({'background':background,'equal':equal,'frames':comparisons})
                write_json(quality/'result.json',report)
                require(equal,'Dense N1 local '+background+' pair differs')
            report.update(equal=True,status='LOCAL_N1_PAIR_PASSED_REAL_BACKGROUND_FULL_SCENE_UNRUN')
        else:
            raise ValueError('Unsupported quality mode')
    except BaseException as exc:
        report.update(error_type=type(exc).__name__,error=str(exc))
    finally:
        if binding is not None:
            try:
                binding.restored()
            except BaseException as exc:
                report.update(status='FAILED',equal=False)
                binding.report['restoration']='UNKNOWN'
                report['runtime_identity_error']={'type':type(exc).__name__,'error':str(exc)}
        report['wall_seconds']=clock_now()-before
        write_json(quality/'result.json',report)
    return 0 if report['equal'] else 1
