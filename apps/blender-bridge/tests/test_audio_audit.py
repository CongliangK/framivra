"""Real FFmpeg audit regressions; set VTX_AUDIT_SCRIPT to compare an older script."""
import ast
import io
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

# v2 P2a 随迁 apps/blender-bridge/tests/：app 根注入 sys.path 后普通 import
# audit_bundle（清除 spec_from_file_location，§1.4；app 目录注入不涉内核双模块）
APP = Path(__file__).resolve().parent.parent
REPO = APP.parent.parent
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
import audit_bundle as audit_module   # noqa: E402
import bundle_session

SCRIPT = Path(os.environ.get('VTX_AUDIT_SCRIPT', APP / 'audit_bundle.py'))


class ValidationInterpreter(unittest.TestCase):
    def test_windows_uses_real_interpreter_without_python3_alias_lookup(self):
        with patch.object(audit_module.sys, "platform", "win32"), \
             patch.object(audit_module.sys, "executable", sys.executable), \
             patch.object(audit_module.shutil, "which", side_effect=AssertionError("Store alias")):
            self.assertEqual(audit_module.validation_python(), sys.executable)

    def test_appimage_uses_host_python(self):
        with patch.object(audit_module.sys, "platform", "linux"), \
             patch.dict(os.environ, {"APPIMAGE": "/opt/app.AppImage"}), \
             patch.object(audit_module.shutil, "which", return_value="/usr/bin/python3"):
            self.assertEqual(audit_module.validation_python(), "/usr/bin/python3")

    def test_blender_requires_minimum_version_and_both_modern_properties(self):
        for version, properties, ok in (
                ((4, 3, 2), ("strips", "strips_all"), False),
                ((4, 4, 0), ("strips", "strips_all"), True),
                ((5, 2, 1), ("strips", "strips_all"), True),
                ((5, 2, 1), ("strips",), False),
                ((5, 2, 1), ("strips_all",), False)):
            bpy = SimpleNamespace(app=SimpleNamespace(version=version, version_string=str(version)),
                types=SimpleNamespace(SequenceEditor=SimpleNamespace(
                    bl_rna=SimpleNamespace(properties=properties))))
            with self.subTest(version=version, properties=properties), \
                 patch.object(sys, "stderr", io.StringIO()) as err:
                if ok:
                    self.assertIsNone(bundle_session.require_modern_vse(bpy))
                    self.assertEqual(err.getvalue(), "")
                else:
                    with self.assertRaises(SystemExit) as raised:
                        bundle_session.require_modern_vse(bpy)
                    self.assertEqual(raised.exception.code, 2)
                    self.assertIn("blender-bridge", err.getvalue())


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class AudioAuditIntegration(unittest.TestCase):
    def setUp(self):
        parent = os.environ.get('VTX_AUDIT_TEST_OUTPUT')
        if parent:
            Path(parent).mkdir(parents=True, exist_ok=True)
            self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName+'-', dir=parent))
        else:
            temp = tempfile.TemporaryDirectory(prefix='vtx-audit-')
            self.addCleanup(temp.cleanup)
            self.root = Path(temp.name)
        self.project = self.root / '工程 空格'
        self.project.mkdir()
        self.tone('a.wav', 440, silent=True)
        self.tone('b.wav', 880)
        self.tl = {'version': 2, 'project': {'fps':30,'width':1920,'height':1080,'duration_frames':180},
                   'sources':[{'id':s,'path':s+'.wav'} for s in ['a','b']],
                   'video_tracks':[], 'subtitles':[], 'audio_tracks':[{'name':'bed', 'events':[
                       {'src':'a','src_in':30,'in':90,'out':120,'volume':0.2},
                       {'src':'b','src_in':0,'in':120,'out':150,'volume':0.8}]}]}
        self.audit = self.root / '审计 输出'

    def tone(self, name, hz, silent=False):
        with wave.open(str(self.project / name), 'wb') as w:
            w.setparams((1,2,48000,0,'NONE','not compressed'))
            w.writeframes(b''.join(struct.pack('<h', round(8000*math.sin(2*math.pi*hz*i/48000))
                if not silent or 48000 <= i < 96000 else 0) for i in range(144000)))

    def invoke(self, *args, check=True):
        path = self.project / 'timeline.json'
        path.write_text(json.dumps(self.tl,ensure_ascii=False),encoding='utf-8')
        env = os.environ.copy()
        env.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', VTX_VTX_PY=str(REPO/'vtx/vtx.py'))
        r = subprocess.run([sys.executable,'-B','-X','utf8',str(SCRIPT),'-t',str(path),
             '--prefix','bed','--audit-dir',str(self.audit),'--skip-blend',*args],env=env,capture_output=True,text=True,encoding='utf-8')
        (self.root / ('run-'+str(len(list(self.root.glob('run-*.txt'))))+'.txt')).write_text(r.stdout+r.stderr,encoding='utf-8')
        if check:self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        return r

    def result(self):
        return json.loads((self.audit/'timeline.json').read_text(encoding='utf-8'))

    def test_whole_preserves_events_and_multiple_sources(self):
        events = self.tl['audio_tracks'][0]['events']
        events[0].update(pan=-0.3,fade_in_frames=5,fade_out_frames=7)
        events[1].update(loop=True,out=180)
        self.invoke('--whole')
        result = self.result()
        self.assertEqual(result['audio_tracks'][0]['events'], events)
        self.assertEqual({s['id'] for s in result['sources']},{'a','b'})

    def test_split_maps_source_time_to_delayed_event(self):
        self.tl['audio_tracks'][0]['events'] = [{'src':'a','src_in':15,'in':90,'out':165,'volume':0.2,'pan':-0.3}]
        self.invoke('--pad','0','--min-silence','0.1')
        event = self.result()['audio_tracks'][0]['events'][0]
        self.assertEqual(event, {'src':'a','src_in':30,'in':105,'out':135,'volume':0.2,'pan':-0.3})

    def test_relocated_audio_audit_ignores_visual_dependencies(self):
        (self.project/'gfx').mkdir()
        (self.project/'gfx/film.js').write_text('/* visual-only dependency */',encoding='utf-8')
        self.tl['project']['cache_dependencies']=['gfx/film.js']
        self.invoke('--whole')
        self.assertNotIn('cache_dependencies',self.result()['project'])

    def test_same_timestamp_source_replacement_refreshes_audio(self):
        self.invoke('--whole')
        def media():
            return [(self.audit/s['path']).read_bytes() for s in self.result()['sources']]
        before = media()
        src = self.project/'a.wav'; stamp = src.stat()
        self.tone('a.wav',1200,silent=True)
        os.utime(src,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
        self.invoke('--whole')
        self.assertNotEqual(before, media())

    def test_silent_source_retains_valid_event(self):
        with wave.open(str(self.project/'a.wav'),'wb') as w:
            w.setparams((1,2,48000,0,'NONE','not compressed')); w.writeframes(bytes(288000))
        self.tl['audio_tracks'][0]['events']=[self.tl['audio_tracks'][0]['events'][0]]
        self.invoke('--pad','0','--min-silence','0.1')
        self.assertEqual(self.result()['audio_tracks'][0]['events'],self.tl['audio_tracks'][0]['events'])

    def test_default_retains_loop_and_envelopes(self):
        events = self.tl['audio_tracks'][0]['events']
        events[0].update(fade_in_frames=8, pan=0.5)
        events[1].update(loop=True, out=180)
        self.invoke()
        result = self.result()
        self.assertEqual(result['audio_tracks'][0]['events'],events)
        self.assertEqual(len(result['_meta']['whole_fallbacks']),2)

    def test_corrupt_cached_mp3_is_repaired(self):
        self.invoke('--whole')
        path = self.audit/self.result()['sources'][0]['path']
        before = path.read_bytes()
        self.invoke('--whole')
        self.assertEqual(path.read_bytes(),before)
        path.write_bytes(b'corrupt cached file')
        self.invoke('--whole')
        self.assertEqual(path.read_bytes(),before)

    def test_cannot_overwrite_production_timeline(self):
        self.audit = self.project
        r = self.invoke('--whole',check=False)
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(json.loads((self.project/'timeline.json').read_text(encoding='utf-8')),self.tl)

    def test_failed_forced_transcode_preserves_cached_bytes(self):
        self.invoke('--whole')
        path=self.audit/self.result()['sources'][0]['path']
        receipt=path.with_suffix('.json')
        before=(path.read_bytes(),receipt.read_bytes())
        profile=json.loads(receipt.read_text(encoding='utf-8'))['identity']['profile']
        with patch.object(audit_module.subprocess,'run',side_effect=subprocess.CalledProcessError(1,['ffmpeg'],stderr='encoder failed')):
            with self.assertRaises(subprocess.CalledProcessError):
                audit_module.export_mp3(self.project/'a.wav',path.parent,profile,force=True)
        self.assertEqual((path.read_bytes(),receipt.read_bytes()),before)
        self.assertEqual(list(path.parent.glob('.audit-*')),[])


class AudioAuditMapping(unittest.TestCase):
    def test_blender_resolved_audio_and_original_video_use_separate_paths(self):
        # Extract only these functions: importing the entry point would run bpy.
        tree = ast.parse((APP / 'json2blend.py').read_text(encoding='utf-8'))
        namespace = {'Path': Path, 'subprocess': subprocess}
        exec(compile(ast.Module(body=[node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in ('audio_src', 'probe')],
            type_ignores=[]), 'json2blend.py', 'exec'), namespace)
        rendered = Path('/owned/cache/处理 audio.wav')
        missing = Path('/owned/missing.wav')
        for suffix in ('.wav', '.MP4', '.json', '.JSON'):
            original = Path('/owned/source' + suffix)
            namespace['SRC'] = {'s': original}
            for mapping, expected in (({}, None if suffix.lower() == '.json' else original),
                    ({'s': str(rendered)}, rendered), ({'s': str(missing)}, missing),
                    *[({'s': value}, None) for value in (None, '', False, 0, [], {})]):
                with self.subTest(suffix=suffix, mapping=mapping):
                    namespace['RESOLVED'] = mapping
                    self.assertEqual(namespace['audio_src']('s'), expected)
            for value in (True, 1, ['bad'], {'bad': 'path'}):
                namespace['RESOLVED'] = {'s': value}
                with self.subTest(suffix=suffix, value=value), self.assertRaises(TypeError):
                    namespace['audio_src']('s')
        for mapping in (None, [], 42, 'bad'):
            namespace['RESOLVED'] = mapping
            with self.subTest(mapping=mapping), self.assertRaises(AttributeError):
                namespace['audio_src']('s')
        namespace['RESOLVED'] = {'unknown': str(rendered)}
        with self.assertRaises(KeyError):
            namespace['audio_src']('unknown')
        video = Path('/owned/source.MP4')
        namespace.update(SRC={'s': video}, RESOLVED={'s': str(rendered)})
        audio = namespace['audio_src']('s')
        for fps in (24, 30.0):
            for order in ((audio, video), (video, audio)):
                namespace.update(FPS=fps, _probe={})
                def result(cmd, **kwargs):
                    text = ('1920,1080,90' if cmd[-1] == str(video) else
                            '2.5' if 'format=duration' in cmd else '')
                    return subprocess.CompletedProcess(cmd, 0, text, '')
                with self.subTest(fps=fps, order=order), patch.object(
                        subprocess, 'run', side_effect=result) as run:
                    for path in order + order:
                        self.assertEqual(namespace['probe'](path),
                            (1920, 1080, 90) if path == video else (0, 0, round(2.5 * fps)))
                    self.assertEqual(run.call_count, 3)
                    self.assertEqual(set(namespace['_probe']), {audio, video})
        # Both callers probe the same path they pass to new_sound/new_movie.
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == 'probe']
        self.assertEqual([ast.unparse(call) for call in calls], ['probe(path)', 'probe(path)'])

    def test_source_intersection_has_independent_time_origin(self):
        e={'src':'a','src_in':40,'in':300,'out':360,'volume':0.4,'pan':-0.5}
        parts, reason=audit_module.split_event(e,[(0,45),(55,65),(90,120)])
        self.assertIsNone(reason)
        self.assertEqual([(p['src_in'],p['in'],p['out']) for p in parts],
                         [(40,300,305),(55,315,325),(90,350,360)])
        self.assertTrue(all(p['volume']==0.4 and p['pan']==-0.5 for p in parts))

    def test_detector_failure_is_not_treated_as_content(self):
        with patch.object(audit_module.subprocess,'run',side_effect=subprocess.CalledProcessError(1,['ffmpeg'],stderr='decode failed')):
            with self.assertRaises(subprocess.CalledProcessError):
                audit_module.content_spans(Path('bad.wav'),3,30,'-50dB',0.1,0)

    def test_frame_boundaries_expand_instead_of_truncating(self):
        result=subprocess.CompletedProcess([],0,stdout='',stderr='silence_start: 0\nsilence_end: 0.011\nsilence_start: 0.044\nsilence_end: 1.0\n')
        with patch.object(audit_module.subprocess,'run',return_value=result):
            self.assertEqual(audit_module.content_spans(Path('a'),1,30,'-50dB',0.1,0),[(0,2)])

    def test_cross_drive_path_falls_back_to_absolute(self):
        path=Path('external.mp3').resolve()
        with patch.object(audit_module.os.path,'relpath',side_effect=ValueError('different drives')):
            self.assertEqual(audit_module.media_path(path,Path('audit')),str(path))

    def test_quantized_spans_do_not_overlap(self):
        result=subprocess.CompletedProcess([],0,stdout='',stderr='silence_start: 0.501\nsilence_end: 0.509\n')
        with patch.object(audit_module.subprocess,'run',return_value=result):
            self.assertEqual(audit_module.content_spans(Path('a'),1,30,'-50dB',0.001,0),[(0,30)])


if __name__ == '__main__':
    unittest.main(verbosity=2)
