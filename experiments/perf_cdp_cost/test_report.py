"""Explicit ordinary offline tests; no browser, security probes or test discovery."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest

from vtx.runtime.events import ExecutionContext
from . import report as r


def trace():
    return dict(schema='fast-png-pilot-v1', mode='baseline', status='failed',
                captures=[dict(sequence=0, status='failed', cdp_call_wall_s=2),
                          dict(sequence=1, status='accepted', cdp_call_wall_s=3, base64_characters=12)],
                capture_count=2, operations=[dict(sequence=0, operation='Tab.shot', status='failed', wall_s=2),
                                             dict(sequence=1, operation='Tab.shot', status='returned', wall_s=3)],
                recovery_attempts=1, browser_start_attempts=2, errors=[dict(message='PRIVATE_ERROR')],
                protocol_failures=[], browsers=[], scope_wall_s=7)


def arm():
    return dict(schema='render-pilot-arm/1', arm='baseline', status='FAILED', process_exit_code=1,
                arm_wall_seconds=10, attempts=[dict(index=0, job=dict(batch='continuous'), status='FAILED', exit_code=1,
                 attempt_wall_seconds=7, render_page_wall_seconds=6, capture=trace(),
                 raw_stats=dict(rendered=1, failed=[2], ms_total=3000),
                 timings_ms={k:dict(sum=100, count=1, p50=100, p95=100) for k in r.TIMINGS},
                 png=[{'local':1, 'global':1, 'bytes':13, 'path':'RECORDED_PATH_NOT_READ'}])])


def window():
    return dict(status='OBSERVED', wall_ns=10, observed_union_ns=8, outside_observed_functions_ns=2,
                categories={'outer':dict(calls=1, inclusive_ns=8, exclusive_ns=3),
                            'inner':dict(calls=1, inclusive_ns=5, exclusive_ns=5),
                            'worker':dict(calls=1, inclusive_ns=8, exclusive_ns=8)},
                categories_by_thread={'1':{'outer':dict(calls=1, inclusive_ns=8, exclusive_ns=3),
                                           'inner':dict(calls=1, inclusive_ns=5, exclusive_ns=5)},
                                      '2':{'worker':dict(calls=1, inclusive_ns=8, exclusive_ns=8)}},
                thread_union_ns={'1':8, '2':8})


def observer():
    return dict(schema='perf-composite-observer/1', source_identity='PASS', truncated=False,
                spans_recorded=2, whole_invocation=window(), video_windows=[dict(stage_outcome='raised', whole_video=window())])


def event_data(retry=False):
    class Memory(io.StringIO):
        def close(self):
            pass
    stream = Memory()
    now = [0.0]
    ctx = ExecutionContext(stream, clock=lambda: now[0])
    now[0] = 1
    with ctx.task('gfx', kind='stage').scope() as task:
        if retry:
            try:
                task.attempt(lambda: (_ for _ in ()).throw(RuntimeError()))
            except RuntimeError:
                pass
            now[0] = 2
        task.attempt(lambda: None)
        now[0] = 3
    now[0] = 4
    ctx.finish()
    return stream.getvalue().encode()


class LedgerTests(unittest.TestCase):
    def test_interval_overlap_nesting(self):
        self.assertEqual(r.union([(0,10),(2,5),(8,12),(12,13)]), 13)

    def test_interval_empty_and_zero(self):
        self.assertEqual(r.union([]), 0)
        self.assertEqual(r.union([(2,2)]), 0)

    def test_bad_interval(self):
        with self.assertRaises(ValueError): r.union([(2,1)])

    def test_event_internal_wall(self):
        result = r.summarize_events(event_data())
        self.assertTrue(result['evidence_complete'])
        self.assertEqual(result['internal_run_wall_seconds']['value'], 4)
        self.assertEqual(result['intervals']['stage']['union_seconds'], 2)
        self.assertEqual(result['timing_sums_ms']['nav']['status'], 'UNOBSERVED')

    def test_event_failed_retry_not_dropped(self):
        result = r.summarize_events(event_data(retry=True))
        self.assertEqual(result['terminal_outcomes']['attempts'], {'failed':1, 'succeeded':1})
        self.assertEqual(result['retry_requested'], 1)
        self.assertIsNone(result['recovery_attempts']['value'])

    def test_event_duplicate_rejected(self):
        data = event_data(); lines=data.splitlines(keepends=True)
        with self.assertRaises(ValueError): r.summarize_events(b''.join(lines[:2]+[lines[1]]+lines[2:]))

    def test_event_empty_unknown(self):
        result = r.summarize_events(b'')
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertIsNone(result['internal_run_wall_seconds']['value'])

    def test_event_missing_terminal_unknown(self):
        result = r.summarize_events(b'\n'.join(event_data().splitlines()[:-1])+b'\n')
        self.assertFalse(result['evidence_complete'])
        self.assertIsNone(result['internal_run_wall_seconds']['value'])

    def test_external_real_separate(self):
        result = r.summarize_time(b'warning\n 4.50 real 2.0 user 1.0 sys\n')
        self.assertEqual(result['external_real_seconds']['value'], 4.5)
        self.assertNotEqual(result['external_real_seconds']['value'], r.summarize_events(event_data())['internal_run_wall_seconds']['value'])

    def test_external_real_missing_and_multiple(self):
        self.assertIsNone(r.summarize_time(b'empty')['external_real_seconds']['value'])
        with self.assertRaises(ValueError): r.summarize_time(b'1 real 0 user 0 sys\n2 real 0 user 0 sys\n')

    def test_arm_failure_and_recovery_kept(self):
        result = r.summarize_arm(arm())
        self.assertEqual(result['batches']['continuous']['outcomes'], {'failed':1})
        self.assertEqual(result['attempts'][0]['capture']['recovery_attempts']['value'], 1)
        self.assertEqual(result['attempts'][0]['failed_frames']['value'], 1)
        self.assertEqual(result['batches']['continuous']['timing_sum_ms']['nav']['value'], 100)

    def test_attempt_duplicate_index(self):
        data=arm(); data['attempts'].append(copy.deepcopy(data['attempts'][0]))
        with self.assertRaises(ValueError): r.summarize_arm(data)

    def test_missing_timing_not_zero(self):
        data=arm(); del data['attempts'][0]['timings_ms']['nav']['sum']
        result=r.summarize_arm(data)['batches']['continuous']['timing_sum_ms']['nav']
        self.assertIsNone(result['value']); self.assertEqual(result['status'], 'UNKNOWN')

    def test_empty_arm_no_invented_work(self):
        result=r.summarize_arm(dict(schema='render-pilot-arm/1', attempts=[]))
        self.assertIsNone(result['internal_arm_wall_seconds']['value'])
        self.assertEqual(result['batches'], {})

    def test_missing_raw_stats_is_unknown(self):
        data=arm(); data['attempts'][0]['raw_stats']=None
        result=r.summarize_arm(data)['attempts'][0]
        self.assertIsNone(result['rendered_layer_frames']['value'])
        self.assertIsNone(result['failed_frames']['value'])

    def test_batches_separate_no_percentile_pooling(self):
        data=arm(); second=copy.deepcopy(data['attempts'][0]); second['index']=1; second['job']['batch']='diagnostic'
        second['timings_ms']['nav']={'sum':1000, 'count':2, 'p50':500, 'p95':860}; second['capture']['scope_wall_s']=8; data['attempts'].append(second)
        result=r.summarize_arm(data)
        self.assertEqual(set(result['batches']), {'continuous','diagnostic'})
        self.assertNotIn('p95',result['batches']['continuous'])
        self.assertEqual(result['attempts'][1]['timings_ms']['nav']['p95']['value'], 860)

    def test_png_duplicate_and_missing_bytes(self):
        data=arm(); data['attempts'][0]['png'].append(copy.deepcopy(data['attempts'][0]['png'][0]))
        with self.assertRaises(ValueError): r.summarize_arm(data)
        data=arm(); del data['attempts'][0]['png'][0]['bytes']
        self.assertIsNone(r.summarize_arm(data)['attempts'][0]['png_bytes']['value'])

    def test_capture_failures_do_not_disappear(self):
        result=r.summarize_capture(trace())
        self.assertEqual(result['capture_outcomes'], {'failed':1,'accepted':1})
        self.assertEqual(result['capture_call_work_seconds']['value'], 5)
        self.assertIsNone(result['base64_characters']['value'])
        self.assertEqual(result['base64_characters']['observed_subtotal'], 12)
        self.assertNotIn('PRIVATE_ERROR', json.dumps(result))

    def test_capture_duplicate_sequence(self):
        data=trace(); data['captures'][1]['sequence']=0
        with self.assertRaises(ValueError): r.summarize_capture(data)

    def test_capture_missing_not_zero(self):
        result=r.summarize_capture(dict(schema='fast-png-pilot-v1'))
        self.assertIsNone(result['recovery_attempts']['value'])
        self.assertIsNone(result['captures']['value'])

    def test_capture_nested_operations_not_added_to_wall(self):
        data=trace(); data['operations'][0]['wall_s']=7; data['operations'][1]['wall_s']=7
        result=r.summarize_capture(data)
        self.assertEqual(result['operations'][0]['work_seconds']['value'], 14)
        self.assertEqual(result['scope_wall_seconds']['value'], 7)

    def test_observer_parallel_work_not_wall(self):
        result=r.summarize_observer(observer())
        self.assertEqual(result['whole_invocation']['observed_union_ns'], 8)
        self.assertEqual(sum(result['whole_invocation']['thread_union_ns'].values()),16)
        self.assertEqual(result['stage_outcomes'], {'raised':1})
        self.assertEqual(result['video_windows'][0]['first_ffmpeg_role'], 'UNKNOWN')

    def test_observer_truncated_unknown(self):
        data=observer(); data['truncated']=True
        self.assertEqual(r.summarize_observer(data)['status'],'UNKNOWN')

    def test_observer_inconsistent_union_rejected(self):
        data=observer(); data['whole_invocation']['observed_union_ns']=11
        with self.assertRaises(ValueError): r.summarize_observer(data)

    def test_observer_synthetic_container(self):
        result=r.summarize_observer(dict(schema='perf-composite-calibration/1',observer_reports=[observer()]))
        self.assertIn('synthetic', result['context'])

    def test_observer_missing_unknown(self):
        result=r.summarize_observer(dict(schema='perf-composite-observer/1'))
        self.assertEqual(result['status'],'UNKNOWN')
        self.assertIsNone(result['whole_invocation']['value'])

    def test_bad_numbers_json_and_schemas(self):
        for value in (True,-1,float('nan'),float('inf'),10**500):
            with self.assertRaises(ValueError): r.number(value,'test')
        for text in ('{"x":1,"x":2}', '{"x":NaN}', '{'):
            with self.assertRaises(ValueError): r.strict_json(text)
        for function in (r.summarize_arm,r.summarize_capture,r.summarize_observer):
            with self.assertRaises(ValueError): function(dict(schema='unknown'))

    def test_identity_allowlist_no_paths(self):
        result=r.safe_identity(dict(source_head='a'*40, source_tree='b'*40, project='PRIVATE_PATH'))
        self.assertEqual(len(result),2)
        self.assertNotIn('PRIVATE_PATH',json.dumps(result))
        with self.assertRaises(ValueError): r.safe_identity([])

    def test_cli_read_only_and_trace_crosscheck(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); a=root/'arm.json'; c=root/'capture.json'; out=root/'new.json'
            a.write_text(json.dumps(arm())); c.write_text(json.dumps(trace())); before=(a.read_bytes(),c.read_bytes())
            self.assertEqual(r.main(['--arm',str(a),'--capture',str(c),'--output',str(out)]),0)
            result=json.loads(out.read_text())
            self.assertEqual(result['observations'][1]['identical_embedded_traces'],[dict(input_id=0,attempt_index=0)])
            self.assertEqual(before,(a.read_bytes(),c.read_bytes()))
            self.assertNotIn('PRIVATE_ERROR',out.read_text()); self.assertNotIn('RECORDED_PATH_NOT_READ',out.read_text())

    def test_cli_existing_file_refused_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'input.json'; source.write_text(json.dumps(arm())); before=source.read_bytes()
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(r.main(['--arm',str(source),'--output',str(source)]),2)
            self.assertEqual(before,source.read_bytes())

    def test_cli_duplicate_content_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'input.json'; source.write_text(json.dumps(arm()))
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(r.main(['--arm',str(source),'--arm',str(source)]),2)

    def test_event_mixed_run_rejected(self):
        rows=[json.loads(x) for x in event_data().splitlines()]
        rows[-1]['run_id']='00000000-0000-0000-0000-000000000001'
        rows[-1]['event_id'][0]=rows[-1]['run_id']
        with self.assertRaises(ValueError): r.summarize_events(('\n'.join(json.dumps(x) for x in rows)+'\n').encode())

    def test_event_mixed_unit_rejected(self):
        rows=[json.loads(x) for x in event_data().splitlines()]
        rows[-1]['timing']['unit']='s'
        with self.assertRaises(ValueError): r.summarize_events(('\n'.join(json.dumps(x) for x in rows)+'\n').encode())

    def test_summary_only_does_not_invent_samples(self):
        data=arm(); data['attempts'][0]['timings_ms']={'nav':{'p50':20,'p95':30}}
        result=r.summarize_arm(data)['attempts'][0]
        self.assertEqual(result['timings_ms']['nav']['p50']['value'],20)
        self.assertIsNone(result['timings_ms']['nav']['sum']['value'])

    def test_cleanup_missing_is_explicit(self):
        result=r.summarize_arm(arm())['attempts'][0]
        self.assertEqual(result['cleanup_checks']['status'],'UNOBSERVED')
        self.assertIsNone(result['cleanup_checks']['value'])

    def test_pair_preflight_failure_and_missing_quality(self):
        result=r.summarize_pair(dict(schema='render-pilot-result/1',status='FAILED',processes=[]))
        self.assertIsNone(result['quality'])
        self.assertEqual(result['cleanup_status'],'UNKNOWN')
        self.assertIsNone(result['runner_complete_wall_seconds']['value'])

    def test_quality_summary_no_pixel_claim(self):
        result=r.summarize_quality(dict(schema='render-pilot-quality/1',equal=True))
        self.assertIsNone(result['frame_records']['value'])
        self.assertEqual(result['independent_decode_by_this_tool'],'UNRUN')
        self.assertEqual(result['full_scene_acceptance'],'UNKNOWN')

    def test_quality_contradiction_rejected(self):
        data=dict(schema='render-pilot-quality/1',equal=True,png_comparison=dict(frames=[dict(entry='e',global_frame=0,equal=False)]))
        data['png_comparison']['frames'][0]['global']=0
        with self.assertRaises(ValueError): r.summarize_quality(data)

    def test_conflicting_timing_summaries_rejected(self):
        data=arm(); data['attempts'][0]['raw_stats']['timings_ms']={'nav':{'sum':200}}
        with self.assertRaises(ValueError): r.summarize_arm(data)

    def test_r2_empty_interval_fields_unknown(self):
        groups=r.summarize_events(b'')['intervals']
        for group in groups.values():
            self.assertIsNone(group['union_seconds']); self.assertIsNone(group['work_seconds'])
            self.assertEqual(group['status'],'UNKNOWN'); self.assertEqual(group['measured_records'],0)

    def test_r2_partial_interval_subtotal_is_not_total(self):
        data=b'\n'.join(event_data().splitlines()[:-1])+b'\n'
        stage=r.summarize_events(data)['intervals']['stage']
        self.assertIsNone(stage['union_seconds']); self.assertIsNone(stage['work_seconds'])
        self.assertEqual(stage['observed_union_seconds'],2)
        self.assertEqual(stage['started_records'],1); self.assertEqual(stage['terminal_records'],1)
        self.assertEqual(stage['status'],'UNKNOWN')

    def test_r2_started_unfinished_interval_unknown(self):
        data=b'\n'.join(event_data().splitlines()[:2])+b'\n'
        stage=r.summarize_events(data)['intervals']['stage']
        self.assertEqual(stage['started_records'],1); self.assertEqual(stage['terminal_records'],0)
        self.assertIsNone(stage['union_seconds']); self.assertEqual(stage['status'],'UNKNOWN')

    def test_r2_png_missing_global_rejected(self):
        data=arm(); del data['attempts'][0]['png'][0]['global']
        with self.assertRaises(ValueError): r.summarize_arm(data)

    def test_r2_png_duplicate_global_rejected(self):
        data=arm(); data['attempts'][0]['png'].append({'local':2,'global':1,'bytes':12})
        with self.assertRaises(ValueError): r.summarize_arm(data)

    def test_r2_png_negative_global_rejected(self):
        data=arm(); data['attempts'][0]['png'][0]['global']=-1
        with self.assertRaises(ValueError): r.summarize_arm(data)

    def test_r2_png_pair_outside_mapping_rejected(self):
        data=arm(); data['attempts'][0]['job']['mapping']=[{'global':1,'local':2}]
        with self.assertRaises(ValueError): r.summarize_arm(data)

    def test_r2_partial_failed_png_mapping_preserved(self):
        data=arm(); data['attempts'][0]['job']['mapping']=[{'global':1,'local':1},{'global':2,'local':2}]
        result=r.summarize_arm(data)['attempts'][0]
        self.assertFalse(result['target_scope']['png_mapping_coverage']['complete'])
        self.assertEqual(result['png_bytes']['value'],13); self.assertEqual(result['outcome'],'failed')

    def test_r2_successful_partial_png_inventory_rejected(self):
        data=arm(); item=data['attempts'][0]; item['status']='RENDERED_UNVERIFIED'; item['exit_code']=0
        item['job']['mapping']=[{'global':1,'local':1},{'global':2,'local':2}]
        with self.assertRaises(ValueError): r.summarize_arm(data)

    def test_r2_missing_observer_whole_window_propagates(self):
        data=observer(); del data['whole_invocation']
        self.assertEqual(r.summarize_observer(data)['status'],'UNKNOWN')

    def test_r2_reindexed_identical_attempt_rejected(self):
        data=arm(); repeat=copy.deepcopy(data['attempts'][0]); repeat['index']=9; data['attempts'].append(repeat)
        with self.assertRaises(ValueError): r.summarize_arm(data)

    def test_r2_reindexed_identical_trace_rejected(self):
        data=arm(); repeat=copy.deepcopy(data['attempts'][0]); repeat['index']=9; repeat['attempt_wall_seconds']=8; data['attempts'].append(repeat)
        with self.assertRaises(ValueError): r.summarize_arm(data)

    def test_r2_distinct_recovery_attempt_kept(self):
        data=arm(); repeat=copy.deepcopy(data['attempts'][0]); repeat['index']=9
        repeat['attempt_wall_seconds']=8; repeat['capture']['scope_wall_s']=8
        repeat['capture']['recovery_attempts']=2; data['attempts'].append(repeat)
        result=r.summarize_arm(data)
        self.assertEqual(result['batches']['continuous']['attempt_records'],2)
        self.assertEqual(result['batches']['continuous']['attempt_work_seconds']['value'],15)
        self.assertEqual(result['attempts'][1]['capture']['recovery_attempts']['value'],2)

    def test_r2_observer_thread_exceeds_wall_rejected(self):
        data=observer(); data['whole_invocation']['thread_union_ns']['1']=11
        with self.assertRaises(ValueError): r.summarize_observer(data)

    def test_r2_observer_thread_exceeds_global_union_rejected(self):
        data=observer(); data['whole_invocation']['thread_union_ns']['1']=9
        with self.assertRaises(ValueError): r.summarize_observer(data)

    def test_r2_missing_observer_maps_unknown(self):
        for key in ('categories','categories_by_thread','thread_union_ns'):
            data=observer(); del data['whole_invocation'][key]
            result=r.summarize_observer(data)['whole_invocation']
            self.assertEqual(result['status'],'UNKNOWN'); self.assertIsNone(result[key])

    def test_r2_missing_observer_category_number_unknown(self):
        data=observer(); del data['whole_invocation']['categories']['outer']['calls']
        self.assertEqual(r.summarize_observer(data)['whole_invocation']['status'],'UNKNOWN')

    def test_r2_observer_category_thread_mismatch_rejected(self):
        data=observer(); data['whole_invocation']['categories']['outer']['calls']=2
        with self.assertRaises(ValueError): r.summarize_observer(data)

    def test_r2_zero_count_positive_sum_rejected(self):
        data=arm(); data['attempts'][0]['timings_ms']['nav']['count']=0
        with self.assertRaises(ValueError): r.summarize_arm(data)

    def test_r2_reversed_percentiles_rejected(self):
        data=arm(); data['attempts'][0]['timings_ms']['nav']['p95']=0
        with self.assertRaises(ValueError): r.summarize_arm(data)

    def test_r2_percentile_exceeds_sum_rejected(self):
        data=arm(); data['attempts'][0]['timings_ms']['nav']['sum']=1
        with self.assertRaises(ValueError): r.summarize_arm(data)

    def test_r2_positive_count_zero_decode_valid(self):
        data=arm(); data['attempts'][0]['timings_ms']['decode']={'count':90,'sum':0,'p50':0,'p95':0}
        result=r.summarize_arm(data)['attempts'][0]['timings_ms']['decode']
        self.assertEqual(result['sum']['value'],0); self.assertEqual(result['count']['value'],90)

    def test_r2_missing_count_not_reconstructed(self):
        data=arm(); del data['attempts'][0]['timings_ms']['nav']['count']
        result=r.summarize_arm(data)['attempts'][0]['timings_ms']['nav']
        self.assertIsNone(result['count']['value']); self.assertEqual(result['sum']['value'],100)

    def test_r2_explicit_count_basis_mismatch_rejected(self):
        data=arm(); data['attempts'][0]['timings_ms']['nav'].update(count=90,count_basis='successful override frames')
        with self.assertRaises(ValueError): r.summarize_arm(data)

    def test_r3_known_90_sample_median_impossible_sum_rejected(self):
        data=arm(); item=data['attempts'][0]; item['raw_stats']['rendered']=90
        row=dict(sum=43,count=90,p50=33.343958499999715,p95=42.24054154999948,
                 count_basis='successful override frames')
        item['timings_ms']['nav']=row
        item['raw_stats']['timings_ms']={'nav':{k:row[k] for k in ('sum','p50','p95')}}
        with self.assertRaises(ValueError): r.summarize_arm(data)

    def test_r3_known_p95_impossible_sum_rejected(self):
        with self.assertRaises(ValueError):
            r.timing_summary(dict(count=90,sum=10,p50=0,p95=10))

    def test_r3_two_sample_median_sum_bound(self):
        with self.assertRaises(ValueError):
            r.timing_summary(dict(count=2,sum=9,p50=5,p95=6))
        result=r.timing_summary(dict(count=2,sum=10,p50=5,p95=9.5))
        self.assertEqual(result['sum']['value'],10)

    def test_r3_linear_interpolation_lower_bound_positive_controls(self):
        # Ordinary arrays covering both minimizers, integer/fractional ranks,
        # zeros and ties. No NumPy dependency or raw-sample reconstruction.
        for values in ([0,10],[0,0,10],[0,5,5,5],[0,0,3,3,3],
                       [0]*18+[1,1], [0]*45+[1]*45, [0]*84+[2]*6):
            with self.subTest(samples=values):
                row=dict(count=len(values),sum=sum(values))
                for name,percent in (('p50',50),('p95',95)):
                    rank,fraction=divmod(percent*(len(values)-1),100)
                    row[name]=values[rank]+(values[min(rank+1,len(values)-1)]-values[rank])*fraction/100
                self.assertEqual(r.timing_summary(row)['sum']['value'],sum(values))

    def test_r3_timing_sum_bound_float_roundoff(self):
        result=r.timing_summary(dict(count=2,sum=1.9999999999999,p50=1,p95=1))
        self.assertEqual(result['sum']['status'],'OBSERVED')

    def test_r3_missing_count_does_not_invent_sum_bound(self):
        result=r.timing_summary(dict(sum=43,p50=33.343958499999715,p95=42.24054154999948))
        self.assertIsNone(result['count']['value'])
        self.assertEqual(result['count']['status'],'UNOBSERVED')
        self.assertEqual(result['sum']['value'],43)

    def test_r3_observer_single_call_over_wall_rejected(self):
        data=window()
        for row in (data['categories']['outer'],data['categories_by_thread']['1']['outer']):
            row['inclusive_ns']=11
        with self.assertRaises(ValueError): r.observer_window(data,'test')

    def test_r3_observer_single_call_over_global_union_rejected(self):
        data=window()
        for row in (data['categories']['outer'],data['categories_by_thread']['1']['outer']):
            row['inclusive_ns']=9
        with self.assertRaises(ValueError): r.observer_window(data,'test')

    def test_r3_observer_single_call_over_thread_union_rejected(self):
        data=window(); data['thread_union_ns']['1']=5
        for row in (data['categories']['outer'],data['categories_by_thread']['1']['outer']):
            row.update(inclusive_ns=6,exclusive_ns=0)
        with self.assertRaises(ValueError): r.observer_window(data,'test')

    def test_r3_observer_repeated_nested_calls_above_wall_valid(self):
        data=window(); data['categories'].pop('inner'); data['categories_by_thread']['1'].pop('inner')
        for row in (data['categories']['outer'],data['categories_by_thread']['1']['outer']):
            row.update(calls=3,inclusive_ns=24,exclusive_ns=8)
        result=r.observer_window(data,'test')
        self.assertEqual(result['status'],'OBSERVED')
        self.assertGreater(result['categories']['outer']['inclusive_ns'],result['wall_ns'])

    def test_r3_observer_multi_call_inclusive_bound_rejected(self):
        data=window()
        for row in (data['categories']['outer'],data['categories_by_thread']['1']['outer']):
            row.update(calls=3,inclusive_ns=25)
        with self.assertRaises(ValueError): r.observer_window(data,'test')

    def test_r3_observer_same_category_parallel_work_valid(self):
        data=dict(status='OBSERVED',wall_ns=10,observed_union_ns=8,outside_observed_functions_ns=2,
                  categories={'work':dict(calls=2,inclusive_ns=16,exclusive_ns=16)},
                  categories_by_thread={str(t):{'work':dict(calls=1,inclusive_ns=8,exclusive_ns=8)} for t in (1,2)},
                  thread_union_ns={'1':8,'2':8})
        self.assertEqual(r.observer_window(data,'test')['status'],'OBSERVED')

    def test_r3_observer_unknown_calls_do_not_invent_bound(self):
        data=window()
        for row in (data['categories']['outer'],data['categories_by_thread']['1']['outer']):
            row.pop('calls'); row['inclusive_ns']=11
        result=r.observer_window(data,'test')
        self.assertEqual(result['status'],'UNKNOWN')
        self.assertIsNone(result['categories']['outer']['calls'])

    def test_readable_contains_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'input.json'; source.write_text(json.dumps(arm())); out=io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(r.main(['--arm',str(source),'--format','text']),0)
            self.assertIn('UNKNOWN',out.getvalue()); self.assertIn('PNG bytes=13',out.getvalue())

    def test_cli_default_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'input.json'; source.write_text(json.dumps(arm())); out=io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(r.main(['--arm',str(source)]),0)
            self.assertEqual(json.loads(out.getvalue())['schema'],r.VERSION)
            self.assertEqual([p.name for p in Path(directory).iterdir()],['input.json'])


if __name__ == '__main__':
    unittest.main()
