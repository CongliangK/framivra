"""Reached native operations: precise origins, unchanged exceptions and controls."""
import contextlib
import copy
import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.diagnostics._support import StageFixture
from vtx.runtime.diagnostics import operation, required_field
from vtx.infra import Ctx


class NativeOperations(unittest.TestCase):
    def test_once_same_exception_and_no_nested_duplicate(self):
        c = SimpleNamespace(diagnostics=[])
        error = TypeError('actual native failure')
        calls = []
        def fail():
            calls.append(1)
            raise error
        with self.assertRaises(TypeError) as raised:
            operation(c, fail, path='$.subtitles[2]', rule='validate.operation')
        self.assertIs(raised.exception, error)
        self.assertEqual(calls, [1])
        self.assertEqual(c.diagnostics[0]['json_path'], '$.subtitles[2]')
        c.diagnostics.clear()
        with self.assertRaises(KeyError):
            operation(c, lambda: required_field(c, {}, 'dur', '$.video_tracks[0].clips[1]'),
                      path='$.video_tracks', rule='validate.operation')
        self.assertEqual(len(c.diagnostics), 1)
        self.assertEqual(c.diagnostics[0]['rule_id'], 'load.required_field')

    def test_unrelated_runtime_and_io_are_not_claimed(self):
        for error in (RuntimeError('callback'), PermissionError('resource')):
            c = SimpleNamespace(diagnostics=[])
            def fail():
                raise error
            with self.assertRaises(type(error)) as caught:
                operation(c, fail, path='$.sources[0]', rule='validate.operation')
            self.assertIs(caught.exception, error)
            self.assertEqual(c.diagnostics, [])
            self.assertFalse(getattr(error, '_vtx_diagnostic_handled', False))


class IdentityExit(unittest.TestCase):
    def test_only_actual_handled_identity_failure_transfers_to_exit(self):
        from vtx.cache.cache_state import InputIdentityError
        from vtx.runtime.diagnostics import mark_handled
        for handled in (False, True):
            error = InputIdentityError('identity changed')
            if handled:
                mark_handled(error)
            c = SimpleNamespace(receipt=lambda *args: None)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                Ctx.reject_input_identity(c, None, error)
            self.assertEqual(raised.exception.code, 1)
            self.assertEqual(bool(getattr(raised.exception, '_vtx_diagnostic_handled', False)), handled)
        def receipt(*args):
            raise PermissionError('receipt failure')
        c.receipt = receipt
        with self.assertRaises(PermissionError) as raised:
            Ctx.reject_input_identity(c, 'qc', error)
        self.assertFalse(getattr(raised.exception, '_vtx_diagnostic_handled', False))


class StageOperations(StageFixture, unittest.TestCase):
    def test_validate_native_failures_and_accepted_control(self):
        cases=[
          ('missing_name',lambda t:t.update(video_tracks=[{'kind':'gfx','clips':[]}]), '$.video_tracks[0]',KeyError),
          ('clip_duration',lambda t:t['video_tracks'][0]['clips'][0].update(dur='bad'),'$.video_tracks[0].clips[0]',TypeError),
          ('clip_start',lambda t:t['video_tracks'][0]['clips'][0].update(dst_in=[]),'$.video_tracks[0].clips[0]',TypeError),
          ('clip_source',lambda t:t['video_tracks'][0]['clips'][0].update(src=[]),'$.video_tracks[0].clips[0]',TypeError),
          ('source_range',lambda t:t['video_tracks'][0]['clips'][0].update(src_out='bad'),'$.video_tracks[0].clips[0]',TypeError),
          ('fade',lambda t:t['video_tracks'][0]['clips'][0].update(fade_in_frames='bad'),'$.video_tracks[0].clips[0]',TypeError),
          ('clip_sort',lambda t:t['video_tracks'][0]['clips'].append({'dst_in':'bad'}),'$.video_tracks[0].clips',TypeError),
          ('sub_start',lambda t:t['subtitles'][0].update(**{'in':'bad'}),'$.subtitles[0]',TypeError),
          ('sub_text',lambda t:t['subtitles'][0].update(**{'in':-1,'text':5}),'$.subtitles[0]',TypeError),
          ('event_start',lambda t:t['audio_tracks'][0]['events'][0].update(**{'in':'bad'}),'$.audio_tracks[0].events[0]',TypeError),
          ('event_source',lambda t:t['audio_tracks'][0]['events'][0].update(src=[]),'$.audio_tracks[0].events[0]',TypeError),
          ('event_offset',lambda t:t['audio_tracks'][0]['events'][0].update(src_in='bad'),'$.audio_tracks[0].events[0]',TypeError),
          ('event_sort',lambda t:t['audio_tracks'][0]['events'].append({'src':'s','in':'bad','out':40,'volume':1}),'$.audio_tracks[0].events',TypeError),
        ]
        for name, mutate, path, typ in cases:
            with self.subTest(name=name):
                tl=copy.deepcopy(self.tl);mutate(tl)
                c=self.context(tl); plain=self.context(tl,False)
                out,error=self.run_stage(c,'validate');plainout,plainerror=self.run_stage(plain,'validate')
                self.assertIsInstance(error,typ)
                self.assertEqual((out,type(error),str(error)),(plainout,type(plainerror),str(plainerror)))
                self.assertTrue(c.diagnostics, name)
                self.assertTrue(c.diagnostics[-1]['json_path'].startswith(path),c.diagnostics)
                self.assertNotEqual(c.diagnostics[-1]['rule_id'],'runtime.unknown')
                self.assertTrue(getattr(error,'_vtx_diagnostic_handled',False))
        c=self.context(self.tl)
        self.assertIsNone(self.run_stage(c,'validate')[1])

    def test_audit_preparation_and_zero_duration(self):
        for name,mutate,path,typ in [
            ('mixed_kinds',lambda t:(t['video_tracks'][0].update(kind=1), t['video_tracks'].append({'name':'g','kind':'gfx','clips':[]})), '$.video_tracks',TypeError),
            ('clip',lambda t:t['video_tracks'][0]['clips'][0].update(dur='bad'),'$.video_tracks[0].clips[0]',TypeError),
            ('name',lambda t:t['audio_tracks'][0].pop('name'),'$.audio_tracks[0]',KeyError),
            ('audio',lambda t:t['audio_tracks'][0]['events'][0].update(out='bad'),'$.audio_tracks[0].events[0]',TypeError),
            ('text',lambda t:t['subtitles'][0].update(text=2),'$.subtitles[0]',TypeError),
            ('zero',lambda t:t['subtitles'][0].update(out=0),'$.subtitles[0]',ZeroDivisionError),
        ]:
            with self.subTest(name=name):
                tl=copy.deepcopy(self.tl);mutate(tl);c=self.context(tl)
                _,error=self.run_stage(c,'audit')
                self.assertIsInstance(error,typ)
                self.assertTrue(c.diagnostics[-1]['json_path'].startswith(path),c.diagnostics)
                self.assertTrue(getattr(error,'_vtx_diagnostic_handled',False))


if __name__=='__main__': unittest.main()
