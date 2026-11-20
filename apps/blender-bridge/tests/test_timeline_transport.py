"""Test timeline text transport through the Blender scripts, not Blender itself."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

APP = Path(__file__).resolve().parents[1]

# Supply only the scene API these entry points consume. The document read,
# strip reconciliation and Unicode write are the actual script implementations.
_SCENE = '''
import runpy, sys
from types import SimpleNamespace as NS

script, timeline, blend, output = sys.argv[1:]
verifying = script.endswith('verify_blend.py')
start = 0 if verifying else 1
strip = NS(name='旁白|0|声😀', channel=1, frame_final_start=start,
           frame_start=start, frame_final_end=start+6, volume=.5, pan=0.)
class Scene(dict):
    sequence_editor = NS(strips_all=[strip])
scene = Scene(vtx_timeline=timeline)
sys.modules['bpy'] = NS(app=NS(version=(4, 4, 0)),
    types=NS(SequenceEditor=NS(bl_rna=NS(properties=['strips', 'strips_all']))),
    context=NS(scene=scene), ops=NS(wm=NS(open_mainfile=lambda **kw: None)))
sys.argv = [script, '--', timeline, blend] if verifying else [script, '--', blend, output]
runpy.run_path(script, run_name='__main__')
'''


class TimelineTransport(unittest.TestCase):
    def test_verify_and_blendback_keep_utf8_timeline(self):
        with tempfile.TemporaryDirectory(prefix='blender-bridge-') as temp:
            root = Path(temp)
            timeline, blend, output = (root / name for name in
                ('时间线😀.json', '预览😀.blend', '回写😀.json'))
            expected = {'project': {'duration_frames': 30},
                'sources': [{'id': '声😀', 'path': '配音😀.wav'}], 'video_tracks': [],
                'subtitles': [], 'audio_tracks': [{'name': '旁白', 'events': [
                    {'src': '声😀', 'in': 0, 'out': 6, 'volume': .5}]}]}
            original = json.dumps(expected, ensure_ascii=False).encode('utf-8')
            timeline.write_bytes(original)
            env = dict(os.environ, PYTHONIOENCODING='utf-8')
            if os.name == 'nt':
                env['PYTHONUTF8'] = '0'
            for script, marker in (('verify_blend.py', '失败 0 项'), ('blend2json.py', '回写 1 项')):
                with self.subTest(script=script):
                    result = subprocess.run([sys.executable, '-B', '-c', _SCENE,
                        str(APP / script), str(timeline), str(blend), str(output)], cwd=root,
                        env=env, capture_output=True, encoding='utf-8', timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                    self.assertIn(marker, result.stdout)
                    self.assertEqual(timeline.read_bytes(), original)
                    if script == 'blend2json.py':
                        expected['audio_tracks'][0]['events'][0].update({'in': 1, 'out': 7})
                        self.assertEqual(json.loads(output.read_text(encoding='utf-8')), expected)
                        self.assertIn('声😀'.encode('utf-8'), output.read_bytes())
                    else:
                        self.assertFalse(output.exists())
