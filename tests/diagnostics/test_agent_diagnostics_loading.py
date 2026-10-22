"""Real entry-point failures remain a single strict JSON document."""
from tests._support import temporary_root
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from tests.diagnostics._support import timeline


class Loading(unittest.TestCase):
    def setUp(self):
        self.root = temporary_root(self)
        self.path = self.root / 'timeline.json'

    def invoke(self, command='validate'):
        result = subprocess.run([sys.executable, '-m', 'vtx.agent_diagnostics', '-t', str(self.path), command,
                                 '--format', 'agent-json'], text=True, capture_output=True)
        report = json.loads(result.stdout, parse_constant=lambda x: self.fail(x))
        self.assertEqual(result.stderr, '')
        self.assertEqual(result.returncode, report['exit_code'])
        return result.returncode, report

    def write(self, obj): self.path.write_text(json.dumps(obj))

    def test_loading_branches(self):
        cases = [
            (None, 'load.timeline_file', '$'),
            ('{', 'load.timeline_json', '$'),
            ({}, 'load.required_field', '$["project"]'),
            ({'project':{}}, 'load.required_field', '$.project["fps"]'),
        ]
        for data, rule, path in cases:
            with self.subTest(rule=rule, path=path):
                if data is not None:
                    if isinstance(data,str): self.path.write_text(data)
                    else: self.write(data)
                rc,r=self.invoke();self.assertEqual(rc,1)
                self.assertEqual(r['diagnostics'][0]['rule_id'],rule)
                self.assertEqual(r['diagnostics'][0]['json_path'],path)
        for bad in [0,24,29.97,True,'30',None]:
            with self.subTest(fps=bad):
                t=timeline();t['project']['fps']=bad;self.write(t)
                rc,r=self.invoke();self.assertEqual(rc,1)
                self.assertEqual(r['diagnostics'][0]['rule_id'],'load.project_fps')
                self.assertEqual(r['diagnostics'][0]['measured']['unit'],'frames/s')

    def test_dependency_failures_identify_the_actual_producer_predicate(self):
        (self.root/'exists.txt').write_text('x')
        t=timeline();t['project']['cache_dependencies']=['exists.txt','missing.txt'];self.write(t)
        rc,r=self.invoke();self.assertEqual(rc,1)
        self.assertEqual(len(r['diagnostics']), 1)
        self.assertEqual(r['diagnostics'][0]['json_path'], '$.project.cache_dependencies[1]')
        self.assertEqual(r['diagnostics'][0]['rule_id'], 'load.cache_dependency')
        self.assertEqual(r['diagnostics'][0]['measured'],
                         {'path': 'missing.txt', 'is_file': False})
        self.assertEqual(r['diagnostics'][0]['message'],
                         'cache dependency is not a file: missing.txt')
        t['project']['cache_dependencies']=True;self.write(t)
        rc,r=self.invoke();self.assertEqual(rc,1)
        self.assertEqual(len(r['diagnostics']), 1)
        self.assertEqual(r['diagnostics'][0]['json_path'], '$.project.cache_dependencies')
        self.assertEqual(r['diagnostics'][0]['rule_id'], 'load.cache_dependencies')
        self.assertEqual(r['diagnostics'][0]['measured'], {'type': 'bool'})
        self.assertEqual(r['diagnostics'][0]['message'],
                         'project.cache_dependencies must be a list of nonempty file paths')

    def test_invalid_media_probe_is_structured(self):
        (self.root/'bad.mp4').write_text('not media')
        t=timeline();t['sources']=[{'id':'bad','path':'bad.mp4'}];self.write(t)
        rc,r=self.invoke();self.assertEqual(rc,1)
        error=r['diagnostics'][-1]
        self.assertEqual(error['rule_id'],'runtime.command')
        self.assertEqual(error['json_path'],'$')
        self.assertIn('bad.mp4', error['measured']['command_display'])
        self.assertEqual(error['status'],'unknown')
        self.assertTrue(r['legacy_output']['stderr'])

    def test_prerequisites_and_escape_remain_enforced(self):
        self.write(timeline())
        rc,r=self.invoke('qc');self.assertEqual(rc,1)
        self.assertEqual(r['diagnostics'][0]['rule_id'],'runtime.stage_prerequisite')
        with tempfile.TemporaryDirectory() as outside:
            # Remove only test-owned recoverable empty output artifacts.
            import shutil
            shutil.rmtree(self.root/'out')
            (self.root/'out').symlink_to(outside, target_is_directory=True)
            rc,r=self.invoke();self.assertEqual(rc,1)
            self.assertEqual(r['diagnostics'][0]['rule_id'],'runtime.write_path')
            self.assertEqual(list(Path(outside).iterdir()),[])

    def test_audit_advisory_exit_and_receipt(self):
        self.write(timeline())
        rc,r=self.invoke('audit');self.assertEqual(rc,0)
        self.assertTrue(any(x['severity']=='warning' for x in r['diagnostics']))
        receipt=json.loads((self.root/'out/receipt.json').read_text())
        self.assertTrue(receipt['stages']['audit']['ok'])

    def test_legacy_entrypoint_output_parity(self):
        for bad in [False, True]:
            with self.subTest(bad=bad):
                t=timeline()
                if bad:t['subtitles']=[{'in':-1,'out':20,'text':'bad'}]
                self.write(t)
                legacy=subprocess.run([sys.executable,'-m','vtx','-t',str(self.path),'validate'],capture_output=True,text=True)
                rc,r=self.invoke()
                self.assertEqual(rc,legacy.returncode)
                self.assertEqual(r['legacy_output']['stdout'],legacy.stdout)
                self.assertEqual(r['legacy_output']['stderr'],legacy.stderr)




class ProbeBranches(unittest.TestCase):
    def test_probe_missing_fps_duration_and_malformed_metadata(self):
        from contextlib import redirect_stderr
        from io import StringIO
        from types import SimpleNamespace
        from unittest.mock import patch
        from vtx.pipeline.infra import Ctx
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'timeline.json';t=timeline()
            t['sources']=[{'id':'s','path':'asset.mp4'}];path.write_text(json.dumps(t))
            cases=[
                ({'streams':[{'codec_type':'video','r_frame_rate':'n/a','width':160,'height':90}], 'format':{}}, 'probe.fps'),
                ({'streams':[{'codec_type':'audio'}], 'format':{}}, 'probe.audio_duration'),
                ({'streams':[{'codec_type':'video','r_frame_rate':'30/1','nb_frames':'1'}], 'format':{}}, 'probe.metadata_operation'),
            ]
            for data,rule in cases:
                with self.subTest(rule=rule):
                    c=Ctx(path,diagnostics=[])
                    def response(cmd):
                        return SimpleNamespace(stdout='0' if '-count_packets' in cmd else json.dumps(data))
                    with patch.object(c,'run',side_effect=response), redirect_stderr(StringIO()):
                        with self.assertRaises((SystemExit,KeyError)):
                            c.probe(path.parent/'asset.mp4')
                    self.assertEqual(c.diagnostics[-1]['rule_id'],rule)
                    self.assertEqual(c.diagnostics[-1]['json_path'],'$')
                    self.assertEqual(c.diagnostics[-1]['source']['resource'], str(path.parent/'asset.mp4'))
                    self.assertEqual(c.diagnostics[-1]['status'],'unknown')
                    self.assertEqual(c.diagnostics[-1]['source']['provider'], 'ffprobe')

if __name__=='__main__':unittest.main()
