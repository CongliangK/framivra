"""The public demo derives both gfx routes from one editable timeline."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.demo_workbench import graphics_spec, main, timeline
from scripts.demo_workbench_acceptance import exercise_edit, run


class DemoWorkbenchTests(unittest.TestCase):
    def test_soundtrack_defaults_to_score_with_explicit_portable_mode(self):
        args = ['prepare', '--output', 'project', '--fonts-dir', 'fonts']
        with patch('scripts.demo_workbench.prepare') as prepare:
            self.assertEqual(main(args), 0)
            self.assertEqual(prepare.call_args.args[0].soundtrack, 'carulli')
            self.assertEqual(main(args + ['--soundtrack', 'portable']), 0)
            self.assertEqual(prepare.call_args.args[0].soundtrack, 'portable')

    def test_only_gfx_sources_differ_between_routes(self):
        artifacts = {kind: timeline(kind, []) for kind in ('html', 'svg')}
        spec = graphics_spec()
        for kind, document in artifacts.items():
            for track in document['video_tracks']:
                if track['kind'] != 'gfx':
                    continue
                for clip in track['clips']:
                    suffix = '.html' if kind == 'html' else '.frames.json'
                    key = clip.pop('src').split('/')[-1].removesuffix(suffix)
                    self.assertEqual(clip.pop('format'), 'html' if kind == 'html' else 'svg-sequence')
                    self.assertEqual(clip['dur'], spec[key]['frames'])
                    clip['src'] = key
        self.assertEqual(artifacts['html'], artifacts['svg'])

    def test_existing_evidence_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'prepare.log'
            log.write_bytes(b'first run')
            with self.assertRaises(FileExistsError):
                run([], log, {})
            self.assertEqual(log.read_bytes(), b'first run')

    def test_edit_uses_store_and_preserves_other_content(self):
        original = timeline('html', [])
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp).resolve()
            path = project / 'timeline.json'
            path.write_text(json.dumps(original), encoding='utf-8')
            result = exercise_edit(path)
            self.assertTrue(result['plan']['ok'])
            self.assertTrue(result['commit']['ok'])
            edited = json.loads(path.read_text(encoding='utf-8'))
            cue = edited['video_tracks'][2]['clips'][0]
            self.assertEqual((cue['dst_in'], cue['dur']), (396, 34))
            sfx = next(track for track in edited['audio_tracks'] if track['name'] == 'sfx')['events'][1]
            self.assertEqual((sfx['in'], sfx['out'], sfx['src'], sfx['src_in']), (396, 430, 'demo_hit', 0))
            self.assertNotEqual(edited['subtitles'][2]['text'], original['subtitles'][2]['text'])
            plate = copy.deepcopy(edited['video_tracks'][1]['clips'])
            for block in plate:
                del block['id']
            self.assertEqual(plate, original['video_tracks'][1]['clips'])
            self.assertTrue((project / 'acceptance/edit/commit.json').is_file())


if __name__ == '__main__':
    unittest.main()
