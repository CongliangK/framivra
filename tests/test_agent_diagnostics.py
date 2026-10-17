"""Source-level diagnostic coverage, original indices, and legacy parity."""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from vtx.diagnostics import DiagnosticMessage, emit
from vtx.infra import Ctx
from vtx import stages


def timeline():
    return {'version': 2, 'project': {'fps': 30, 'width': 160, 'height': 90,
            'duration_frames': 90}, 'sources': [], 'video_tracks': [],
            'audio_tracks': [], 'subtitles': []}


class Diagnostics(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def context(self, tl):
        p = self.root / 'timeline.json'
        p.write_text(json.dumps(tl))
        c = Ctx(p, diagnostics=[])
        c.dry = True
        return c

    def validate(self, tl, probes=None):
        c = self.context(tl)
        if probes:
            c.probe = lambda p: probes[Path(p).name]
        with contextlib.redirect_stdout(io.StringIO()), patch('vtx.stages._plugin_hooks.run_probe_source', return_value=None):
            try:
                stages.validate(c)
                code = 0
            except SystemExit as e:
                code = e.code
        return c.diagnostics, code

    def test_every_validate_rule(self):
        video = {'name': 'v', 'kind': 'aroll', 'clips': [
            {'src': 's', 'src_in': 0, 'src_out': 30, 'dst_in': 0, 'dur': 30}]}
        audio = {'name': 'voice', 'events': [{'src': 's', 'in': 0, 'out': 30, 'volume': 1}]}
        base = timeline(); base['sources'] = [{'id': 's', 'path': 'source.mp4'}]
        base['video_tracks'] = [video]; base['audio_tracks'] = [audio]
        (self.root / 'source.mp4').touch()
        facts = {'fps': 30, 'nb_frames': 90, 'has_audio': True, 'has_video': True}
        cases = [
          ('validate.source_structure', lambda t: t['sources'].append({'path':'source.mp4'})),
          ('validate.source_exists', lambda t: t['sources'].append({'id':'missing','path':'missing.mp4'})),
          ('validate.track_empty', lambda t: t['video_tracks'].append({'name':'empty','clips':[]})),
          ('validate.clip_overlap', lambda t: t['video_tracks'][0]['clips'].append(dict(video['clips'][0], dst_in=5))),
          ('validate.clip_bounds', lambda t: t['video_tracks'][0]['clips'][0].update(dst_in=-1)),
          ('validate.gfx_exists', lambda t: t['video_tracks'].append({'name':'g','kind':'gfx','clips':[{'src':'missing.html','dst_in':0,'dur':10}]})),
          ('validate.clip_source', lambda t: t['video_tracks'][0]['clips'][0].update(src='unknown')),
          ('validate.clip_source_range', lambda t: t['video_tracks'][0]['clips'][0].pop('src_in')),
          ('validate.clip_edl', lambda t: t['video_tracks'][0]['clips'][0].update(src_out=31)),
          ('validate.clip_source_bounds', lambda t: t['video_tracks'][0]['clips'][0].update(src_out=100)),
          ('validate.clip_fade', lambda t: t['video_tracks'][0]['clips'][0].update(fade_in_frames=31)),
          ('validate.subtitle_bounds', lambda t: t['subtitles'].append({'in':-1,'out':10,'text':'x'})),
          ('validate.audio_track_structure', lambda t: t['audio_tracks'].append('bad')),
          ('validate.audio_events_structure', lambda t: t['audio_tracks'].append({'name':'bad','events':None})),
          ('validate.audio_event_required', lambda t: t['audio_tracks'][0]['events'][0].pop('volume')),
          ('validate.audio_event_bounds', lambda t: t['audio_tracks'][0]['events'][0].update(out=100)),
          ('validate.audio_source', lambda t: t['audio_tracks'][0]['events'][0].update(src='unknown')),
          ('validate.audio_source_bounds', lambda t: t['audio_tracks'][0]['events'][0].update(src_in=80)),
          ('validate.audio_overlap', lambda t: t['audio_tracks'][0]['events'].append(dict(audio['events'][0], **{'in': 5}))),
        ]
        for rule, mutate in cases:
            with self.subTest(rule=rule):
                t = copy.deepcopy(base); mutate(t)
                records, code = self.validate(t, {'source.mp4': facts})
                self.assertEqual(code, 1)
                found = [r for r in records if r['rule_id'] == rule]
                self.assertTrue(found, records)
                self.assertIsNotNone(found[0]['measured'])
                self.assertIsNotNone(found[0]['threshold'])
                self.assertTrue(found[0]['suggested_actions'])
        for rule, changes in [('validate.source_fps', {'fps':24}),
                              ('validate.source_audio', {'has_audio':False}),
                              ('validate.clip_video', {'has_video':False})]:
            with self.subTest(rule=rule):
                records, code = self.validate(base, {'source.mp4': dict(facts, **changes)})
                self.assertEqual(code, 1)
                self.assertIn(rule, [r['rule_id'] for r in records])

    def test_indices_and_escaped_unknown_keys(self):
        t = timeline(); t['video_tracks'] = [{'name':'g','kind':'gfx','clips':[
            {'src':'late.html','dst_in':30,'dur':10}, {'src':'early.html','dst_in':0,'dur':40}]}]
        t['audio_tracks'] = [{'name':'a','events':[]},{'name':'b','events':[{'src':'x','in':50,'out':60,'volume':1,'a.b"c':True}]}]
        records, code = self.validate(t)
        overlap = next(x for x in records if x['rule_id']=='validate.clip_overlap')
        self.assertEqual(overlap['json_path'], '$.video_tracks[0].clips[0].dst_in')
        unknown = next(x for x in records if x['rule_id']=='validate.unknown_key')
        self.assertEqual(unknown['json_path'], '$.audio_tracks[1].events[0]["a.b\\"c"]')
        self.assertEqual(unknown['severity'], 'warning')

    def test_unknown_is_warning_runtime_small_canvas_is_valid(self):
        t = timeline(); t['unrecognized'] = True
        records, code = self.validate(t)
        self.assertEqual(code, 0)
        self.assertEqual(records[0]['severity'], 'warning')

    def test_declared_plugin_states_preserve_error_and_warning(self):
        t=timeline(); t['project']['extension']=True
        for state in ('not_installed','disabled','broken'):
            with self.subTest(state=state):
                c=self.context(t)
                with patch('vtx.stages._plugin_registry.lookup_key',return_value=('fixture',{'path':'project'})), \
                     patch('vtx.stages._plugin_registry.plugin_state',return_value=state):
                    errors=[];warnings=stages._scan_plugin_keys(c,errors)
                self.assertEqual(len(errors),1)
                self.assertEqual(warnings,[])
                self.assertEqual(c.diagnostics[0]['rule_id'],'validate.plugin_state')
                self.assertEqual(c.diagnostics[0]['json_path'],'$.project["extension"]')
                self.assertEqual(c.diagnostics[0]['measured']['state'],state)
        c=self.context(t)
        with patch('vtx.stages._plugin_registry.lookup_key',return_value=('fixture',{'path':'sources[]'})):
            errors=[];warnings=stages._scan_plugin_keys(c,errors)
        self.assertEqual(errors,[])
        self.assertEqual(len(warnings),1)
        self.assertEqual(c.diagnostics[0]['severity'],'warning')

    def test_external_subtitles_map_to_file(self):
        (self.root/'captions.json').write_text(json.dumps({'subtitles':[{'in':-1,'out':10,'text':'hi'}]}))
        t = timeline(); t['subtitles_src']='captions.json'
        records, code = self.validate(t)
        r = next(x for x in records if x['rule_id']=='validate.subtitle_bounds')
        self.assertEqual(r['json_path'], '$.subtitles_src')
        self.assertEqual(r['source'], {'file':'captions.json','format':'.json','json_path':'$.subtitles','index':0})
        self.assertEqual(code,1)

    def test_subtitle_source_error_branches(self):
        for value, inline, rule in [(True, [], 'subtitle.source_type'),
            ('x.srt', [{'in':0,'out':1,'text':'x'}], 'subtitle.source_exclusive'),
            ('missing.srt', [], 'subtitle.source_exists'), ('bad.srt', [], 'subtitle.source_parse')]:
            with self.subTest(rule=rule):
                (self.root/'bad.srt').write_text('bad line')
                t=timeline();t['subtitles_src']=value;t['subtitles']=inline
                records, code=self.validate(t)
                self.assertEqual(code,1)
                self.assertIn(rule,[r['rule_id'] for r in records])

    def test_audit_is_advisory_and_individual(self):
        t=timeline(); t['audio_tracks']=[{'name':'voice','events':[{'src':'x','in':0,'out':30,'volume':1}]}]
        t['subtitles']=[{'in':10,'out':20,'text':'a'*20},{'in':0,'out':15,'text':'b'*10}]
        c=self.context(t)
        with contextlib.redirect_stdout(io.StringIO()): stages.audit(c)
        r=next(x for x in c.diagnostics if x['rule_id']=='audit.subtitle_overlap')
        self.assertEqual(r['json_path'],'$.subtitles[0]')
        self.assertEqual(r['measured']['previous_index'],1)
        self.assertEqual(len([x for x in c.diagnostics if x['rule_id']=='audit.subtitle_cps']),2)
        self.assertTrue({'audit.voice_coverage','audit.voice_rate','audit.video_gap','audit.audio_gap'} <= {r['rule_id'] for r in c.diagnostics})
        self.assertFalse(any(x['severity']=='error' for x in c.diagnostics))

    def test_str_compatible_metadata_and_finite_json(self):
        c=SimpleNamespace(diagnostics=[])
        msg=DiagnosticMessage('original',json_path='$.x',rule_id='plugin.test',measured={'value':float('nan')})
        emit(c,msg)
        self.assertEqual(str(msg),'original')
        self.assertIsNone(c.diagnostics[0]['measured']['value'])
        json.dumps(c.diagnostics,allow_nan=False)

    def test_opaque_plugin_error_has_no_invented_precision(self):
        with patch('vtx.stages._plugin_hooks.run_validate',return_value=['opaque third-party failure']):
            records,code=self.validate(timeline())
        self.assertEqual(code,1)
        self.assertEqual(records[0]['rule_id'],'unknown')
        self.assertIsNone(records[0]['measured'])


if __name__=='__main__': unittest.main()
