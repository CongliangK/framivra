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
    if plan.get('schema') == 'render-pilot/2':
        from .profiles import validate_selection
        validate_selection(plan['schema'], plan.get('target_profile'),
                           plan.get('mode'), plan.get('group_plan'))
    else:
        require(plan.get('schema') == 'render-pilot/1' and 'target_profile' not in plan
                and plan.get('mode') in ('fast-png','group'),
                'Unsupported plan schema/profile')
    bound = read_bound(plan['bound']['root'],plan['bound']['inventory_path'])
    current = build_plan(bound,plan['mode'],plan['group_plan'],
                         target_profile=plan.get('target_profile'))
    require(canonical(current) == canonical(plan) if plan.get('schema') == 'render-pilot/2' else current == plan,
            'Plan or inputs changed; prepare a new plan')


def render_arm(spec_file, arm):
    """Child only. Production render_page is neither copied nor given a fallback."""
    from vtx import gfx_render, gfx_environment, proc_supervisor
    from experiments.fast_png_pilot.adapter import capture_scope
    spec = json.loads(Path(spec_file).read_bytes())
    plan, root = spec['plan'], Path(spec['output']) / arm
    report = {'schema':'render-pilot-arm/1','arm':arm,'attempts':[], 'status':'FAILED',
              'process_exit_code':1,'resource_observations':'unknown; single worker retained'}
    started = clock_now()
    binding = None
    if plan.get('schema') == 'render-pilot/2':
        report.update(schema='render-pilot-arm/2', runtime_identity={
                          'status':'UNKNOWN','namespace':spec.get('namespace'),'run':spec['output'],
                          'arm':arm,'pid':os.getpid(),'ppid':os.getppid(),'pgid':os.getpgid(0),
                          'argv':list(sys.argv),'cwd':os.getcwd(),'reason':'source/code checks not yet complete'},
                      resource_observations={'status':'DISABLED','values':None,'promotion':'UNKNOWN',
                                             'reason':plan['resource_policy']['reason']})
    write_json(root/'result.json',report)
    try:
        require(spec.get('clock_kind')==CLOCK_KIND,'Unknown shared deadline clock')
        require(all(os.environ.get(k) == v for k,v in PROFILE.items()), 'Profile differs')
        verify_plan(plan)
        git_identity(ROOT, **{'expected_commit':spec['implementation']['commit'],
                            'expected_tree':spec['implementation']['tree']},deadline=spec['deadline_monotonic'])
        if plan.get('schema') == 'render-pilot/2':
            from .runtime_identity import WorkerBinding
            binding = WorkerBinding(spec, spec_file, arm, sys.modules[__name__])
            report['runtime_identity'] = binding.report
            write_json(root/'result.json',report)
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
            if binding is not None:
                attempt['runtime_identity'] = {'namespace':spec['namespace'], 'arm':arm, 'attempt':index,
                    'pid':binding.report['pid'], 'worker_record':'../result.json#runtime_identity'}
            if plan.get('mode')=='poll-observation':
                attempt.update(capture_scope_status='NOT_STARTED',poll_observation={
                    'status':'NOT_STARTED','persistence':'MISSING','reason':'capture scope has not started'})
            report['attempts'].append(attempt)
            write_json(root/'result.json',report)
            before = clock_now()
            trace = None
            render_started = None
            try:
                scope_options={}
                if plan.get('mode')=='poll-observation':
                    scope_options={'poll_observation':plan['poll_observation'][arm],
                                   'job_context':poll_job_context(spec,job,index)}
                    attempt['capture_scope_status']='ENTERING'
                with capture_scope(mode=mode,evidence_dir=target/'capture',**scope_options) as trace:
                    if plan.get('mode')=='poll-observation':attempt['capture_scope_status']='ACTIVE'
                    if binding is not None:
                        attempt['runtime_delegation'] = binding.installed(poll_observation=scope_options['poll_observation']) \
                            if scope_options else binding.installed()
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
                if plan.get('mode')=='poll-observation':
                    attempt['capture_scope_status']='SUCCEEDED'
                    attempt['poll_observation']=trace.poll_observation_report()
                if plan.get('schema') == 'render-pilot/2':
                    capture_count = trace.report()['capture_count']
                    require(type(capture_count) is int and capture_count >= job['expected_rendered'],
                            'Capture evidence cannot account for every fresh target')
                    attempt.update(target_capture_count=job['expected_rendered'], actual_capture_count=capture_count,
                                   non_target_capture_count=capture_count-job['expected_rendered'],
                                   capture_partition_basis='fixed override-only renderer: one screenshot per target; remaining setup probes')
                from vtx.gfx_cli import png_invalid_reason
                expected = {f"f{r['local']:04d}.png" for r in job['mapping']}
                require({p.name for p in frames_dir.iterdir()} == expected, 'Unexpected/missing output frame')
                attempt['png'] = []
                for row in job['mapping']:
                    p = frames_dir/f"f{row['local']:04d}.png"
                    require(png_invalid_reason(p,width=WIDTH,height=HEIGHT) is None,'Invalid output PNG')
                    attempt['png'].append(dict(row,path=str(p),sha256=file_hash(p),bytes=p.stat().st_size))
                if plan.get('mode')=='poll-observation':
                    attempt['poll_validation']=verify_poll_attempt(attempt,spec)
                attempt.update(status='RENDERED_UNVERIFIED',exit_code=0)
            except BaseException as exc:
                if plan.get('mode')=='poll-observation' and attempt['capture_scope_status']!='SUCCEEDED':
                    attempt['capture_scope_status']='FAILED'
                from vtx.events import exit_code
                if render_started is not None and 'render_page_wall_seconds' not in attempt:
                    attempt['render_page_wall_seconds']=clock_now()-render_started
                attempt.update(error_type=type(exc).__name__,error=str(exc),exit_code=exit_code(exc))
                raise
            finally:
                attempt['attempt_wall_seconds'] = clock_now()-before
                poll_error=None
                if trace is not None:
                    attempt['capture'] = trace.report()
                    if plan.get('mode')=='poll-observation':
                        try:attempt['poll_observation']=trace.poll_observation_report()
                        except BaseException as exc:
                            poll_error=exc
                            attempt['poll_evidence_error']={'type':type(exc).__name__,'error':str(exc)}
                write_json(root/'result.json',report)
                if poll_error is not None and 'error_type' not in attempt:raise poll_error
        if plan.get('mode')=='poll-observation':
            report['status']='RENDERED_UNVERIFIED'
            report['poll_observation']=verify_poll_arm(report,spec)
        verify_plan(plan)
        require(gfx_environment.collect('cdp',spec['chrome']) == env, 'Environment changed during arm')
        report.update(status='RENDERED_UNVERIFIED',process_exit_code=0)
    except BaseException as exc:
        from vtx.events import exit_code
        report.update(error_type=type(exc).__name__,error=str(exc),process_exit_code=exit_code(exc))
        traceback.print_exc()
    finally:
        if plan.get('mode')=='poll-observation' and 'poll_observation' not in report:
            report['poll_observation']={'status':'FAILED' if report['attempts'] else 'NOT_STARTED',
                'scopes':[{'index':a['index'],'scope_status':a.get('capture_scope_status','UNKNOWN'),
                           'descriptor':a.get('poll_observation',{'status':'UNKNOWN'})} for a in report['attempts']]}
        if binding is not None:
            try:
                binding.restored()
            except BaseException as exc:
                report.update(status='FAILED', process_exit_code=1)
                binding.report['restoration'] = 'UNKNOWN'
                report['runtime_identity_error'] = {'type':type(exc).__name__, 'error':str(exc)}
        report['arm_wall_seconds'] = clock_now()-started
        # Per-page p95 cannot be aggregated into a combined percentile.
        report['batch_totals'] = {}
        for batch in plan.get('batches', BATCHES):
            attempts = [a for a in report['attempts'] if a['job']['batch']==batch]
            report['batch_totals'][batch] = {
                'render_page_calls_wall_seconds':sum(a.get('render_page_wall_seconds',0) for a in attempts),
                'attempts_wall_seconds':sum(a.get('attempt_wall_seconds',0) for a in attempts),
                'rendered_layer_frames':sum(a.get('raw_stats',{}).get('rendered',0) for a in attempts if a.get('raw_stats')),
                'timing_sum_ms':{k:sum(a.get('timings_ms',{}).get(k,{}).get('sum',0) for a in attempts)
                                 for k in ('nav','export','decode','write')},
                'p95':'per attempt only; summaries are not pooled samples',
                'throughput_extrapolation':'prohibited' if batch=='diagnostic' else 'local window only'}
            if plan.get('schema') == 'render-pilot/2':
                report['batch_totals'][batch].update(
                    fresh_target_png_bytes=sum(p['bytes'] for a in attempts for p in a.get('png',[])),
                    actual_captures=sum(a.get('actual_capture_count',0) for a in attempts),
                    actual_setup_probe_captures=sum(a.get('non_target_capture_count',0) for a in attempts),
                    capture_counts_complete=bool(attempts) and all('actual_capture_count' in a for a in attempts))
        write_json(root/'result.json',report)
    return report['process_exit_code']


def poll_job_context(spec, job, index):
    prefix = f"{index:02d}-{job['batch']}-{job['entry']['entryId']}"
    return {'job_id':prefix,'batch':job['batch'],'arm':job['arm'],'run_id':spec['namespace'],
            'attempt':index,'entry':{k:job['entry'][k] for k in ('entryId','src','windowStart')},
            'mapping':job['mapping']}


def verify_poll_attempt(attempt, spec):
    """Read persisted originals; COMPLETE sidecar alone never qualifies a scope."""
    from experiments.fast_png_pilot.poll_observer import disabled_observation
    from experiments.fast_png_pilot.adapter import RENDERER_SHA256
    plan=spec['plan']; job=attempt['job']; context=poll_job_context(spec,job,attempt['index'])
    directory=Path(spec['output'])/job['arm']/context['job_id']
    require(attempt['directory']==str(directory) and attempt.get('capture_scope_status')=='SUCCEEDED',
            'Poll evidence requires the original successful capture scope')
    capture=directory/'capture'; policy=plan['poll_observation']; enabled=policy[job['arm']]
    def read(path):
        with path.open('rb') as stream:data=stream.read(policy['limits']['evidence_bytes']+1)
        require(len(data)<=policy['limits']['evidence_bytes'],'Observation evidence exceeds fixed cap')
        return json.loads(data),{'path':str(path),'bytes':len(data),'sha256':digest(data)}
    trace,trace_index=read(capture/'trace.json')
    require(trace.get('schema')=='fast-png-pilot-v1' and trace.get('mode')=='baseline'
            and trace.get('status')=='request-and-render-contract-checked'
            and trace.get('requested_capture_params')=={'format':'png'}
            and trace.get('errors')==[] and trace.get('protocol_failures')==[]
            and trace.get('recovery_attempts')==0 and trace.get('browser_start_attempts')==1,
            'Capture trace is not a clean baseline PNG scope')
    require(trace.get('validations')==[{'expected_frames':job['expected_rendered'],'stats':attempt['raw_stats']}],
            'Capture validation is not this exact render_page result')
    validate_stats(attempt['raw_stats'],job['expected_rendered'])
    descriptor=attempt.get('poll_observation')
    if not enabled:
        require(descriptor==disabled_observation() and 'poll_observation' not in trace
                and not (capture/'poll-observation.json').exists(), 'Off scope must stay explicitly disabled')
        return {'status':'DISABLED','capture_trace':trace_index,'descriptor':descriptor}
    require(isinstance(descriptor,dict) and descriptor.get('schema')==policy['schema']
            and descriptor.get('status')=='COMPLETE' and descriptor.get('qualification')=='COMPLETE'
            and descriptor.get('persistence')=='WRITTEN' and trace.get('poll_observation')==descriptor,
            'Poll sidecar/trace index is incomplete or inconsistent')
    data,index=read(capture/'poll-observation.json')
    require(all(descriptor.get(k)==index[k] for k in ('path','bytes','sha256')),
            'Persisted poll sidecar bytes differ from the trace index')
    require(data.get('schema')==policy['schema'] and data.get('status')=='COMPLETE'
            and data.get('qualification')=='COMPLETE' and data.get('limits')==policy['limits']
            and data.get('clock_kind')==policy['clock_kind'] and data.get('unit')=='seconds'
            and data.get('scope')=={'kind':'one-capture-scope/render-page','id':str(capture),'job_context':context}
            and data.get('renderer_sha256')==RENDERER_SHA256 and data.get('caller_source_code_binding') is True
            and data.get('restoration')=={'installed_references_intact':True,'original_references_restored':True},
            'Poll source, clock, limits or job scope differs from the frozen contract')
    require(data.get('overflow') is False and data.get('incomplete') is False
            and data.get('observation_errors')==[] and data.get('primary_error_type') is None
            and data.get('original_trace_error_count')==0 and data.get('recovery_attempts')==0
            and data.get('browser_start_attempts')==1, 'Poll scope contains failed or incomplete observations')
    counts=data['counts']; navs=data['navigations']; polls=data['polls']
    require(all(counts.get(k)==0 for k in ('failed_navigation','failed_poll','unparseable','dropped_navigation',
                                          'dropped_poll','observation_error','unknown_navigation')),
            'Poll scope contains dropped, unknown or failed samples')
    require(type(data.get('navigation_count')) is int and type(data.get('poll_count')) is int
            and len(navs)==counts['navigation']==data['navigation_count']==descriptor['navigation_count']>0
            and len(polls)==counts['poll']==data['poll_count']==descriptor['poll_count']>0,
            'Poll record counts are empty or inconsistent')
    require(all(n.get('status')=='returned' and n.get('enter') is not None and n.get('exit') is not None
                and n['exit']>=n['enter'] and n.get('mapping_status')=='BOUND' for n in navs)
            and all(p.get('call_status')=='returned' and p.get('wrapper_status')=='returned'
                    and all(p.get(k) is not None for k in ('call_start','call_end','wrapper_end'))
                    and p['call_start']<=p['call_end']<=p['wrapper_end'] for p in polls),
            'Poll intervals are not closed successful observations')
    targets=[(n.get('global'),n.get('local')) for n in navs if n.get('role')=='target']
    require(all(type(g) is int and type(l) is int for g,l in targets)
            and len(targets)==job['expected_rendered'] and set(targets)=={(m['global'],m['local']) for m in job['mapping']},
            'Poll target navigation coverage does not match this job')
    require(all(data.get(k) is None for k in ('actual_sleep_seconds','actual_sleep_enter','actual_sleep_exit',
                                             'browser_ready_time','recoverable_wait_seconds')),
            'Unsupported actual sleep or recoverable-wait claim')
    return {'status':'COMPLETE','capture_trace':trace_index,'descriptor':descriptor,
            'scope':data['scope'],'aggregation':'retained per scope; no pooled percentiles or off-as-zero subtraction'}


def verify_poll_arm(report, spec):
    arm=report['arm']; jobs=[j for j in spec['plan']['jobs'] if j['arm']==arm]
    require(report.get('status')=='RENDERED_UNVERIFIED' and len(report.get('attempts',[]))==len(jobs),
            'Poll arm has missing or failed attempts')
    results=[]
    for index,(attempt,job) in enumerate(zip(report['attempts'],jobs)):
        require(attempt['index']==index and canonical(attempt['job'])==canonical(job)
                and attempt.get('status')=='RENDERED_UNVERIFIED' and attempt.get('exit_code')==0,
                'Poll attempt does not match the verified ordered jobs')
        result=verify_poll_attempt(attempt,spec)
        require(result==attempt.get('poll_validation'),'Poll evidence changed after scope validation')
        results.append(result)
    return {'arm':arm,'status':'COMPLETE' if spec['plan']['poll_observation'][arm] else 'DISABLED',
            'scopes':results,'timing_claim':'none; A/A observation disturbance only'}


def owned_worker_identity(supervisor):
    from vtx.proc_supervisor import proc_starttime
    return {'pid':supervisor.proc.pid, 'pgid':supervisor.pgid,
            'supervisor_starttime':proc_starttime(supervisor.proc.pid)}


def verify_worker_identity(report, record, spec, arm):
    identity = report.get('runtime_identity', {})
    expected = record.get('worker_identity', {})
    require(identity.get('status') == 'BOUND_CURRENT_WORKER'
            and identity.get('restoration') == 'MATCH_FROZEN_REFERENCES_AND_SOURCE',
            'Current worker runtime/source binding missing or unverified')
    require(expected and all(type(expected.get(k)) is int and expected[k] > 0
                            and identity.get(k) == expected[k] for k in
                            ('pid','pgid','supervisor_starttime')),
            'Worker identity does not match owned supervisor start')
    require(identity.get('namespace') == spec['namespace'] and identity.get('arm') == arm
            and identity.get('run') == spec['output']
            and identity.get('implementation') == spec['implementation']
            and identity.get('plan_sha256') == digest(canonical(spec['plan']))
            and identity.get('spec_sha256') == file_hash(Path(spec['output'])/'spec.json'),
            'Worker evidence does not belong to this exact run/spec/plan')


def execute_pair(plan, output, chrome, commit, tree, *, arm_order=None):
    """One 900-second deadline, including identity checks, both arms and quality."""
    if plan.get('schema') not in (None, 'render-pilot/1') or 'target_profile' in plan or plan.get('mode')=='poll-observation':
        from .profiles import validate_selection
        validate_selection(plan.get('schema'),plan.get('target_profile'),
                           plan.get('mode'),plan.get('group_plan'))
    if arm_order is not None:
        require(plan.get('mode') in ('fast-png','poll-observation'), 'Explicit arm order requires fast-png or poll-observation')
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
        if plan.get('mode') in ('fast-png','poll-observation') else {}
    summary.update(order_evidence)
    if plan.get('schema') == 'render-pilot/2':
        summary.update(schema='render-pilot-result/2', target_profile=plan['target_profile'],
                       resource_status='UNKNOWN', promotion_eligible=False,
                       resource_policy=plan['resource_policy'])
        if plan.get('mode')=='poll-observation':
            summary['performance_claim']='none; off/on observation disturbance only'
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
        if plan.get('schema') == 'render-pilot/2':
            spec['namespace'] = digest(canonical({'output':str(root), 'start':start,
                                                 'plan_sha256':digest(canonical(plan))}))
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
                        if plan.get('schema') == 'render-pilot/2':
                            record['worker_identity'] = owned_worker_identity(supervisor)
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
            if plan.get('schema') == 'render-pilot/2':
                completed_arm=json.loads((root/arm/'result.json').read_bytes())
                verify_worker_identity(completed_arm, record, spec, arm)
                if plan.get('mode')=='poll-observation':verify_poll_arm(completed_arm,spec)
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
                    if plan.get('schema') == 'render-pilot/2':
                        record['worker_identity'] = owned_worker_identity(supervisor)
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
        if plan.get('schema') == 'render-pilot/2':
            verify_worker_identity(summary['quality'], record, spec, 'quality')
        require(clock_now()<deadline,'Pair deadline expired before final verification')
        verify_plan(plan)
        git_identity(ROOT,commit,tree,deadline=deadline)
        if plan.get('mode')=='poll-observation':summary['poll_observation']=summary['quality']['poll_observation']
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
    p.add_argument('--mode',choices=['fast-png','group','poll-observation'],required=True); p.add_argument('--group-plan')
    p.add_argument('--target-profile',choices=['continuous300'],
                   help='Explicit fixed continuous300-v1 window; omitted preserves the v1 plan')
    p=sub.add_parser('run'); p.add_argument('--plan',required=True); p.add_argument('--output',required=True)
    p.add_argument('--chrome',required=True); p.add_argument('--implementation-commit',required=True)
    p.add_argument('--implementation-tree',required=True)
    p.add_argument('--arm-order',choices=['AB','BA'],
                   help='fast-png or poll-observation: AB=baseline then candidate (default), BA=candidate then baseline')
    p=sub.add_parser('_quality'); p.add_argument('--spec',required=True); p.add_argument('--parent-alive-fd')
    p=sub.add_parser('_arm'); p.add_argument('--spec',required=True); p.add_argument('--arm',choices=['baseline','candidate'],required=True)
    p.add_argument('--parent-alive-fd')
    args=parser.parse_args(argv)
    if args.command=='clock-probe':
        print(json.dumps(clock_probe(),indent=2));return 0
    if args.command=='plan':
        if args.mode=='poll-observation' and args.target_profile is None:
            from .profiles import validate_selection, POLL120
            validate_selection('render-pilot/2',POLL120,args.mode,args.group_plan)
        if args.target_profile is not None:
            from .profiles import validate_selection
            validate_selection('render-pilot/2', 'continuous300-v1', args.mode, args.group_plan)
        print(json.dumps(build_plan(read_bound(args.bound_project,args.inventory),args.mode,args.group_plan,
                                   target_profile='continuous300-v1' if args.target_profile else None),indent=2))
        return 0
    if args.command=='_arm':return render_arm(args.spec,args.arm)
    if args.command=='_quality':
        from .quality import run_quality
        return run_quality(args.spec)
    return execute_pair(json.loads(Path(args.plan).read_bytes()),args.output,args.chrome,
                        args.implementation_commit,args.implementation_tree,arm_order=args.arm_order)

if __name__=='__main__':
    sys.exit(main())
