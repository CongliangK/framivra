"""Opt-in, serial pilot; every execution uses a new directory and existing supervisor."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback
from types import SimpleNamespace

from .contract import (PROFILE, WIDTH, HEIGHT, FPS, BASE_COMMIT, BATCHES,
                       DEADLINE_SECONDS, build_plan, canonical, digest, file_hash,
                       git_identity, read_bound, require, validate_stats, remaining_timeout, clock_now, CLOCK_KIND, clock_probe)

ROOT = Path(__file__).resolve().parents[2]


def write_json(path, value):
    from vtx.fstxn import atomic_write
    atomic_write(Path(path), json.dumps(value, indent=2, allow_nan=False) + '\n')


def fresh(path):
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in (path,*path.parents)), 'Symlink output refused')
    path.mkdir(parents=False, exist_ok=False)
    return path


def verify_plan(plan):
    bound = read_bound(plan['bound']['root'],plan['bound']['inventory_path'])
    current = build_plan(bound,plan['mode'],plan['group_plan'])
    require(current == plan, 'Plan or inputs changed; prepare a new plan')


def render_arm(spec_file, arm):
    """Child only. Production render_page is neither copied nor given a fallback."""
    from vtx import gfx_render, gfx_environment, proc_supervisor
    from experiments.fast_png_pilot.adapter import capture_scope
    spec = json.loads(Path(spec_file).read_bytes())
    plan, root = spec['plan'], Path(spec['output']) / arm
    report = {'schema':'render-pilot-arm/1','arm':arm,'attempts':[], 'status':'FAILED',
              'process_exit_code':1,'resource_observations':'unknown; single worker retained'}
    started = clock_now()
    write_json(root/'result.json',report)
    try:
        require(spec.get('clock_kind')==CLOCK_KIND,'Unknown shared deadline clock')
        require(all(os.environ.get(k) == v for k,v in PROFILE.items()), 'Profile differs')
        verify_plan(plan)
        git_identity(ROOT, **{'expected_commit':spec['implementation']['commit'],
                            'expected_tree':spec['implementation']['tree']},deadline=spec['deadline_monotonic'])
        require(proc_supervisor.supervision_available(), 'Existing supervision unavailable; no fallback')
        ctx = SimpleNamespace(root=root,cache=root/'cache',out=root/'out',w=WIDTH,h=HEIGHT,fps=FPS)
        env = gfx_environment.collect('cdp',spec['chrome'])
        require(env.get('status') == 'input-bound', 'Actual renderer environment unknown')
        report['environment'] = env
        report['environment_sha256'] = gfx_environment.digest(env)
        report['profile_identity'] = digest(canonical({'implementation':spec['implementation'],
            'plan_sha256':digest(canonical(plan)),'environment_sha256':report['environment_sha256'],
            'arm':arm,'mode':plan['mode'],'profile':PROFILE}))
        report['profile_storage'] = str(ctx.cache/'tmp')
        mode = 'fast' if plan['mode']=='fast-png' and arm=='candidate' else 'baseline'
        for index,job in enumerate(j for j in plan['jobs'] if j['arm']==arm):
            require(clock_now() < spec['deadline_monotonic'], 'Pair deadline reached')
            prefix = f"{index:02d}-{job['batch']}-{job['entry']['entryId']}"
            target = fresh(root/prefix)
            frames_dir = fresh(target/'frames')
            attempt = {'index':index,'job':job,'directory':str(target),'status':'FAILED',
                       'exit_code':1,'raw_stats':None}
            report['attempts'].append(attempt)
            write_json(root/'result.json',report)
            before = clock_now()
            trace = None
            render_started = None
            try:
                with capture_scope(mode=mode,evidence_dir=target/'capture') as trace:
                    from .group_gate import group_capture_gate
                    gate = group_capture_gate(gfx_render,trace,
                        {r['global'] for r in job['mapping']},attempt.setdefault('group_state_checks',[])) \
                        if plan['mode']=='group' and arm=='candidate' else contextlib.nullcontext()
                    from .cleanup import browser_cleanup_scope
                    cleanup_checks=attempt.setdefault('cleanup_checks',[])
                    with gate,browser_cleanup_scope(gfx_render,trace,cleanup_checks,target/'browser-cleanup.json',
                                                   deadline=spec['deadline_monotonic']):
                        render_started=clock_now()
                        stats = gfx_render.render_page(
                            str(Path(job['project'])/job['entry']['src']),str(frames_dir),
                            job['entry']['duration'],chrome_path=spec['chrome'],tabs=1,
                            width=WIDTH,height=HEIGHT,frames=[r['local'] for r in job['mapping']],ctx=ctx)
                        attempt['render_page_wall_seconds']=clock_now()-render_started
                        attempt['raw_stats'] = stats
                    require(len(cleanup_checks)==1 and cleanup_checks[0]['status']=='PASS',
                            'One verified browser cleanup required per render_page call')
                    attempt['timings_ms'] = validate_stats(stats,job['expected_rendered'])
                    trace.assert_valid(stats,expected_frames=job['expected_rendered'])
                from vtx.gfx_cli import png_invalid_reason
                expected = {f"f{r['local']:04d}.png" for r in job['mapping']}
                require({p.name for p in frames_dir.iterdir()} == expected, 'Unexpected/missing output frame')
                attempt['png'] = []
                for row in job['mapping']:
                    p = frames_dir/f"f{row['local']:04d}.png"
                    require(png_invalid_reason(p,width=WIDTH,height=HEIGHT) is None,'Invalid output PNG')
                    attempt['png'].append(dict(row,path=str(p),sha256=file_hash(p),bytes=p.stat().st_size))
                attempt.update(status='RENDERED_UNVERIFIED',exit_code=0)
            except BaseException as exc:
                from vtx.events import exit_code
                if render_started is not None and 'render_page_wall_seconds' not in attempt:
                    attempt['render_page_wall_seconds']=clock_now()-render_started
                attempt.update(error_type=type(exc).__name__,error=str(exc),exit_code=exit_code(exc))
                raise
            finally:
                attempt['attempt_wall_seconds'] = clock_now()-before
                if trace is not None:
                    attempt['capture'] = trace.report()
                write_json(root/'result.json',report)
        verify_plan(plan)
        require(gfx_environment.collect('cdp',spec['chrome']) == env, 'Environment changed during arm')
        report.update(status='RENDERED_UNVERIFIED',process_exit_code=0)
    except BaseException as exc:
        from vtx.events import exit_code
        report.update(error_type=type(exc).__name__,error=str(exc),process_exit_code=exit_code(exc))
        traceback.print_exc()
    finally:
        report['arm_wall_seconds'] = clock_now()-started
        # Per-page p95 cannot be aggregated into a combined percentile.
        report['batch_totals'] = {}
        for batch in BATCHES:
            attempts = [a for a in report['attempts'] if a['job']['batch']==batch]
            report['batch_totals'][batch] = {
                'render_page_calls_wall_seconds':sum(a.get('render_page_wall_seconds',0) for a in attempts),
                'attempts_wall_seconds':sum(a.get('attempt_wall_seconds',0) for a in attempts),
                'rendered_layer_frames':sum(a.get('raw_stats',{}).get('rendered',0) for a in attempts if a.get('raw_stats')),
                'timing_sum_ms':{k:sum(a.get('timings_ms',{}).get(k,{}).get('sum',0) for a in attempts)
                                 for k in ('nav','export','decode','write')},
                'p95':'per attempt only; summaries are not pooled samples',
                'throughput_extrapolation':'prohibited' if batch=='diagnostic' else 'local window only'}
        write_json(root/'result.json',report)
    return report['process_exit_code']


def execute_pair(plan, output, chrome, commit, tree, *, arm_order=None):
    """One 900-second deadline, including identity checks, both arms and quality."""
    if arm_order is not None:
        require(plan.get('mode') == 'fast-png', 'Explicit arm order is fast-png only')
        require(arm_order in ('AB','BA'), 'Arm order must be AB or BA')
    arms_in_order = ('candidate','baseline') if arm_order == 'BA' else ('baseline','candidate')
    from vtx import proc_supervisor
    start = clock_now()
    deadline = start + DEADLINE_SECONDS
    require(sys.platform == 'darwin', 'Real pilot is Mac-only; no cloud browser execution')
    require(proc_supervisor.supervision_available(), 'Existing process supervision required')
    clock_evidence=clock_probe(deadline)
    implementation = git_identity(ROOT,commit,tree,deadline=deadline)
    require(Path(chrome).is_file(), 'Explicit browser executable required')
    proposed=Path(output).absolute()
    protected=[Path(plan['bound']['root']).resolve(),ROOT.resolve()]
    if plan.get('group_plan'):
        protected.append(Path(plan['group_plan']).resolve().parent)
    require(all(proposed!=p and p not in proposed.parents for p in protected),
            'Pilot outputs must be outside original inputs and source checkout')
    root = fresh(output)
    summary = {'schema':'render-pilot-result/1','status':'FAILED','implementation':implementation,
               'base':plan['base'],'plan_sha256':digest(canonical(plan)),'processes':[],
               'deadline_seconds':DEADLINE_SECONDS,'clock':clock_evidence,'full_scene_quality':'UNRUN',
               'performance_claim':'none; first local pair only'}
    order_evidence = {'arm_order':arm_order or 'AB','arm_sequence':list(arms_in_order)} \
        if plan.get('mode') == 'fast-png' else {}
    summary.update(order_evidence)
    try:
        verify_plan(plan)
        # No experimental implementation may silently alter production/core or the fixed source template.
        diff = subprocess.run(['git','-C',str(ROOT),'diff','--name-only',BASE_COMMIT,'HEAD','--',
                               'vtx','templates/promo-demo/v1/production'],check=True,capture_output=True,text=True,
                               timeout=remaining_timeout(deadline)).stdout
        require(not diff.strip(),'Pilot implementation changes production or fixed source')
        spec = {'plan':plan,'implementation':implementation,'output':str(root),'chrome':str(Path(chrome).resolve()),
                'deadline_monotonic':deadline,'clock_kind':CLOCK_KIND}
        spec.update(order_evidence)
        write_json(root/'spec.json',spec)
        env = dict(os.environ,**PROFILE,VTX_CHROME=spec['chrome'])
        env['PYTHONPATH'] = str(ROOT)
        ctx = SimpleNamespace(root=root,cache=root/'cache')
        for arm in arms_in_order:
            remaining = deadline-clock_now()
            require(remaining>0,'Pair deadline reached before '+arm)
            fresh(root/arm)
            record={'arm':arm,'returncode':None,'deadline_expired':False}
            summary['processes'].append(record)
            write_json(root/'result.json',summary)
            before=clock_now()
            with open(root/arm/'stdout.log','wb') as stdout,open(root/arm/'stderr.log','wb') as stderr:
                supervisor = proc_supervisor.SupervisedProcessGroup(ctx,
                    [sys.executable,'-m','experiments.render_pilot_runner.runner','_arm',
                     '--spec',str(root/'spec.json'),'--arm',arm],backend_id='pilot_'+arm,
                    stdout=stdout,stderr=stderr,env=env)
                try:
                    from .cleanup import checked_process_group
                    with checked_process_group(supervisor,record,deadline=deadline):
                        try:
                            record['returncode']=supervisor.proc.wait(timeout=max(0.001,deadline-clock_now()))
                        except subprocess.TimeoutExpired as error:
                            record['deadline_expired']=True
                            record['primary_error']={'type':type(error).__name__,'message':str(error),'cancelled':False}
                finally:
                    record['returncode_after_cleanup']=supervisor.proc.returncode if supervisor.proc else None
                    from .cleanup import nested_postflight
                    record['nested_cleanup']=nested_postflight(root/arm,deadline=deadline,
                        observation_deadline=record.get('cleanup',{}).get('observation_deadline_monotonic'))
                    record['complete_wall_seconds']=clock_now()-before
                    write_json(root/'result.json',summary)
            require(record['nested_cleanup']['status']=='PASS','Nested browser cleanup remains unverified')
            require(not record['deadline_expired'] and record['returncode']==0,'Arm failed or deadline expired')
        arms=[json.loads((root/a/'result.json').read_bytes()) for a in ('baseline','candidate')]
        require(arms[0]['environment_sha256']==arms[1]['environment_sha256'],'A/B environment differs')
        for arm in arms:
            require(arm.get('attempts') and all(
                len(a.get('cleanup_checks',[]))==1 and a['cleanup_checks'][0]['status']=='PASS'
                for a in arm['attempts']),'Nested browser cleanup evidence missing or failed')
        require(clock_now()<deadline,'Pair deadline reached before quality comparison')
        fresh(root/'quality')
        record={'arm':'quality','returncode':None,'deadline_expired':False}
        summary['processes'].append(record)
        with open(root/'quality'/'stdout.log','wb') as stdout,open(root/'quality'/'stderr.log','wb') as stderr:
            supervisor=proc_supervisor.SupervisedProcessGroup(ctx,
                [sys.executable,'-m','experiments.render_pilot_runner.runner','_quality',
                 '--spec',str(root/'spec.json')],backend_id='pilot_quality',stdout=stdout,stderr=stderr,env=env)
            try:
                from .cleanup import checked_process_group
                with checked_process_group(supervisor,record,deadline=deadline):
                    try:
                        record['returncode']=supervisor.proc.wait(timeout=max(0.001,deadline-clock_now()))
                    except subprocess.TimeoutExpired as error:
                        record['deadline_expired']=True
                        record['primary_error']={'type':type(error).__name__,'message':str(error),'cancelled':False}
            finally:
                record['returncode_after_cleanup']=supervisor.proc.returncode if supervisor.proc else None
        require(not record['deadline_expired'] and record['returncode']==0,'Quality failed or deadline expired')
        summary['quality']=json.loads((root/'quality'/'result.json').read_bytes())
        require(summary['quality']['equal'],'Pixel comparison failed')
        require(clock_now()<deadline,'Pair deadline expired before final verification')
        verify_plan(plan)
        git_identity(ROOT,commit,tree,deadline=deadline)
        summary['status']=summary['quality']['status']
    except BaseException as exc:
        summary.update(error_type=type(exc).__name__,error=str(exc))
    finally:
        summary['complete_wall_seconds']=clock_now()-start
        summary['deadline_exceeded']=clock_now()>deadline
        if summary['deadline_exceeded']:
            summary['status']='FAILED'
            summary.setdefault('error','Pair deadline reached; completed local gates cannot score this attempt')
        cleanup=[r.get('cleanup',{'status':'UNKNOWN'}) for r in summary['processes']]
        cleanup += [r.get('nested_cleanup',{'status':'UNKNOWN'}) for r in summary['processes'] if r['arm']!='quality']
        states={r['status'] for r in cleanup}
        summary['cleanup_status']='FAIL' if 'FAIL' in states else 'UNKNOWN' if 'UNKNOWN' in states else 'PASS'
        if summary['cleanup_status']!='PASS':summary['status']='FAILED'
        summary['cleanup_note']='Existing bounded owner cleanup may exceed scoring deadline; unknown/residual blocks next arm'
        write_json(root/'result.json',summary)
        from vtx.fstxn import atomic_write
        atomic_write(root/'SUMMARY.txt',f"{summary['status']}\nFull wall: {summary['complete_wall_seconds']:.3f}s\n"
                     'No full-scene, CI, stable speed, audio or product acceptance claim.\n')
    return 0 if summary['status'].startswith('LOCAL_') and not summary['deadline_exceeded'] else 1


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('clock-probe',help='One bounded parent/child shared-clock preflight; no browser')
    p=sub.add_parser('plan'); p.add_argument('--bound-project',required=True); p.add_argument('--inventory',required=True)
    p.add_argument('--mode',choices=['fast-png','group'],required=True); p.add_argument('--group-plan')
    p=sub.add_parser('run'); p.add_argument('--plan',required=True); p.add_argument('--output',required=True)
    p.add_argument('--chrome',required=True); p.add_argument('--implementation-commit',required=True)
    p.add_argument('--implementation-tree',required=True)
    p.add_argument('--arm-order',choices=['AB','BA'],
                   help='fast-png only: AB=baseline then candidate (default), BA=candidate then baseline')
    p=sub.add_parser('_quality'); p.add_argument('--spec',required=True); p.add_argument('--parent-alive-fd')
    p=sub.add_parser('_arm'); p.add_argument('--spec',required=True); p.add_argument('--arm',choices=['baseline','candidate'],required=True)
    p.add_argument('--parent-alive-fd')
    args=parser.parse_args(argv)
    if args.command=='clock-probe':
        print(json.dumps(clock_probe(),indent=2));return 0
    if args.command=='plan':
        print(json.dumps(build_plan(read_bound(args.bound_project,args.inventory),args.mode,args.group_plan),indent=2))
        return 0
    if args.command=='_arm':return render_arm(args.spec,args.arm)
    if args.command=='_quality':
        from .quality import run_quality
        return run_quality(args.spec)
    return execute_pair(json.loads(Path(args.plan).read_bytes()),args.output,args.chrome,
                        args.implementation_commit,args.implementation_tree,arm_order=args.arm_order)

if __name__=='__main__':
    sys.exit(main())
