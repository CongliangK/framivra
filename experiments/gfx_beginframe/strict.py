#!/usr/bin/env python3
"""Produce real plan-bound independent-run #62 evidence for an isolated project.

The plan is frozen before rendering either arm. No old caches are consumed.
Backend/source data are collected from each live browser. #63 breakdown is null.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import hashlib
import random
from pathlib import Path
import sys
import time
import uuid
import threading

from run import ROOT, Shell, render, atomic_json
sys.path.insert(0,str(ROOT/'scripts'))
import gfx_evidence as ge
import gfx_golden as gg
from vtx.env_manifest import collect_env_manifest, classify_gl_backend


def manifest(shell, side):
    gpu=shell.metadata['system_info']['gpu']
    renderer=gpu.get('auxAttributes',{}).get('glRenderer','')
    backend=classify_gl_backend(renderer)
    if not backend and gpu.get('featureStatus',{}).get('gpu_compositing') in ('disabled_software','software_only'):
        backend='software_skia'
    m=collect_env_manifest('cdp',{'effective_backend':backend,'source':'cdp',
        'viewport':f'{shell.width}x{shell.height}','color_space':'srgb',
        'evidence':{'browser_version':shell.metadata['browser_version'],
                    'system_info':shell.metadata['system_info']}})
    if backend in ('software_skia','swiftshader'):
        shell.metadata['host_gpu_inventory']=m['gpu_driver_and_vram']
        m['gpu_driver_and_vram']={'status':'not_applicable','reason':'software_backend'}
    return m


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pure-function-declared',action='store_true',required=True)
    p.add_argument('--binary',required=True);p.add_argument('--timeline',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--workers',type=int,default=1)
    p.add_argument('--candidate-first',action='store_true')
    p.add_argument('--seed',type=int,default=67063)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    began_process=time.monotonic()
    order=('candidate','baseline') if a.candidate_first else ('baseline','candidate')
    if (a.output/'plan.json').exists():p.error('use a fresh output; old evidence is immutable')
    c=gg.make_ctx(a.timeline);clips=gg.gfx_clips(c)
    if c.fps != 30:p.error('fixed research tick interval requires timeline fps=30')
    shells={};runs={};plan={'schema':'vtx.gfx_comparison_plan/1',
       'required_instances':[{'src':s,'dur':d} for s,d in clips],
       'input_fingerprint':ge.input_fingerprint(c,clips),
       'variable_axes':['config.capture','config.beginframe_control','config.flags'],'arms':{}}
    orders={}
    for index,(src,dur) in enumerate(clips):
        frames=list(range(dur));random.Random(a.seed+index).shuffle(frames);orders[src,dur]=frames
    plan['frame_orders']=[{'src':s,'dur':d,'frames':orders[s,d]} for s,d in clips]
    plan['seed']=a.seed
    report={'status':'failed','internal_phase_timing':None,'performance_verdict':None,
            'wave_scope':'one fresh browser start, runtime manifest, all frame renders/PNG writes/hashes, browser exit; excludes preflight and final strict verifier'}
    # Probe both routes without rendering, then close them before freezing plan.
    # Actual arms use fresh processes; only one browser tree exists at a time.
    def config(shell, side):
        return {'capture':'navigate_screenshot' if side=='baseline' else 'render_beginframe',
                'beginframe_control':side=='candidate',
                'binary_sha256':shell.metadata['binary_sha256'],
                'protocol_sha256':shell.metadata['protocol_sha256'],
                'runner_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
                    (Path(__file__),Path(__file__).with_name('run.py'))},
                'flags':shell.metadata['flags']}
    try:
        for side in order:
            probe=Shell(a.binary,a.output/(side+'-preflight'),c.w,c.h,navigation=side=='baseline')
            try:
                m=manifest(probe,side)
                plan['arms'][side]={'run_id':uuid.uuid4().hex,'backend_id':'cdp',
                    'effective_backend':m['runtime_backend_truth']['effective_backend'],
                    'chromium_version':m['runtime_backend_truth']['chromium_version'],
                    'config':config(probe,side)}
                atomic_json(a.output/(side+'-preflight')/'runtime.json',probe.metadata)
            finally:probe.close()
        atomic_json(a.output/'plan.json',plan)
        for side in order:
            began=time.monotonic()
            shell=Shell(a.binary,a.output/side,c.w,c.h,navigation=side=='baseline')
            shells[side]=shell
            try:
                m=manifest(shell,side)
                expected=plan['arms'][side]
                # The experiment allocates run IDs before starting either arm.
                # Runtime data are freshly collected from this actual process.
                m['run_id']=expected['run_id']
                if config(shell,side)!=expected['config']:
                    raise RuntimeError('runtime configuration differs from preflight plan')
                run={'schema':'vtx.gfx_run/1','run_id':expected['run_id'],'complete':False,
                    'input_fingerprint':plan['input_fingerprint'],'config':config(shell,side),
                    'env_manifest':m,'env_sha256':ge.digest(m),'instances':[],
                    'plan_sha256':ge.digest(plan)}
                ge._manifest(run,expected,f'{c.w}x{c.h}')
                runs[side]=run
                atomic_json(a.output/side/'runtime.json',shell.metadata)
                # Target creation uses the browser socket under a lock. Frame loops
                # then own separate target sockets and can run concurrently.
                lock=threading.Lock();original=shell.target
                def target():
                    with lock:return original()
                shell.target=target
                def instance(item):
                    src,dur=item
                    directory=(a.output/side/('instance-'+str(clips.index(item)))).resolve()
                    records=render(shell,c.root/src,orders[src,dur],directory,
                                   navigation=side=='baseline',run_id=run['run_id'])
                    frames={}
                    for r in records:
                        frame=r['token']['frame'];path=directory/f'f{frame:04d}.png'
                        Path(r['path']).rename(path);r['path']=str(path)
                        frames[f'{frame:04d}']={'run_id':run['run_id'],'origin':'rendered',
                                               'sha256':r['rgba_sha256']}
                    atomic_json(directory/'requests.json',records)
                    return {'src':src,'dur':dur,'frame_dir':str(directory),'frames':frames}
                with ThreadPoolExecutor(max_workers=a.workers) as pool:
                    run['instances']=list(pool.map(instance,clips))
                if ge.input_fingerprint(c,clips)!=plan['input_fingerprint']:
                    raise RuntimeError('inputs changed')
            finally:
                shell.close();shells.pop(side,None)
            run['external_complete_wave_wall_seconds']=time.monotonic()-began
            run['complete']=True
            atomic_json(a.output/(side+'-run.json'),run)
        golden={'meta':{'fps':c.fps,'viewport':f'{c.w}x{c.h}',
            'run_evidence':runs['baseline'],'env_manifest':runs['baseline']['env_manifest']},'pages':{}}
        for i in runs['baseline']['instances']:
            golden['pages'].setdefault(i['src'],{'durs':{}})['durs'][str(i['dur'])]={
                'dur':i['dur'],'mode':'full','frames':{f:r['sha256'] for f,r in i['frames'].items()}}
        atomic_json(a.output/'golden.json',golden)
        report['verification']=ge.verify(c,clips,golden,plan,runs['candidate'])
        report['status']='complete' if report['verification']['ok'] else 'different_or_unverified'
        report['external_wave_wall_seconds']={s:r['external_complete_wave_wall_seconds'] for s,r in runs.items()}
    except Exception as error:
        report['error']=repr(error)
        raise
    finally:
        for shell in shells.values():shell.close()
        report['external_process_wall_seconds']=time.monotonic()-began_process
        report['arm_order']=list(order)
        atomic_json(a.output/'strict-report.json',report)
    print(json.dumps({'status':report['status'],'verification':report.get('verification',{}).get('errors')}))
    return 0 if report['status']=='complete' else 1

if __name__=='__main__':sys.exit(main())
