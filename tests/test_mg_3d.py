"""Pure semantic and frame-state tests; these do not claim rendered acceptance."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'templates' / 'mg-styles'
SPEC = importlib.util.spec_from_file_location('mg_3d_template', ROOT / 'render_3d.py')
MG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MG)


class ThreeDimensionalStory(unittest.TestCase):
    def setUp(self):
        self.content = json.loads((ROOT / 'examples' / 'three-d-story.json').read_text(encoding='utf-8'))

    def normalize(self, content=None, **kwargs):
        return MG.validate_3d_content(self.content if content is None else content, frames=240, **kwargs)

    def test_valid_examples_do_not_mutate_callers(self):
        for name in ('three-d-story.json', 'three-d-materials.json'):
            content = json.loads((ROOT / 'examples' / name).read_text(encoding='utf-8'))
            original = deepcopy(content)
            normalized = self.normalize(content)
            self.assertEqual(content, original)
            self.assertEqual(len(normalized['objects']), len(content['objects']))

    def test_invalid_story_and_links_fail_before_blender_is_imported(self):
        patches = [
            {'scene_story': [{'frame': 0, 'title': 'a', 'layout': 'scatter'}]},
            {'scene_story': [{'frame': 0, 'title': 'a', 'layout': 'scatter'}, {'frame': 1, 'title': 'b', 'layout': 'result'}]},
            {'scene_story': [{'frame': 0, 'title': 'a', 'layout': 'bad'}, {'frame': 120, 'title': 'b', 'layout': 'result'}]},
            {'scene_story': [{'frame': 1, 'title': 'a', 'layout': 'scatter'}, {'frame': 120, 'title': 'b', 'layout': 'result'}]},
            {'links': [[0, 0]]}, {'links': [[0, 3]]}, {'links': [[0, 1], [1, 0]]},
            {'links': [[True, 1]]}, {'transition_frames': 1.5},
            {'beats': {'reveal': 0, 'settle': 150, 'resolve': 120}},
            {'objects': [{'label': 'Bad', 'value': float('nan')}]},
            {'objects': [{'label': 'Bad', 'value': True}]},
            {'objects': [{'label': 'Bad', 'value': 50, 'shape': 'invalid'}]},
            {'objects': [{'label': 'Bad', 'value': 50, 'roughness': float('inf')}]},
            {'pace': float('nan')},
        ]
        for patch in patches:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.normalize({**self.content, **patch})

    def test_quality_and_viewport_integer_validation(self):
        for options in ({'samples': True}, {'samples': 0}, {'samples': 257}, {'width': 1920.5}, {'height': 20}, {'engine': 'unknown'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.normalize(**options)

    def test_chapters_change_geometry_and_build_relations(self):
        data = self.normalize()
        intro = MG.story_state(data, 0)
        connected = MG.story_state(data, 120)
        result = MG.story_state(data, 239)
        self.assertEqual((intro['chapter'], connected['chapter'], result['chapter']), (0, 1, 2))
        self.assertNotEqual(intro['positions'], connected['positions'])
        self.assertNotEqual(connected['positions'], result['positions'])
        self.assertEqual(intro['connections'], 0)
        self.assertEqual(connected['connections'], 1)
        self.assertEqual(result['connections'], 1)

    def test_state_is_seekable_and_pace_delays_transition(self):
        data = self.normalize()
        forward = {frame: MG.story_state(data, frame) for frame in (0, 83, 120, 239)}
        for frame in (239, 0, 120, 83):
            self.assertEqual(forward[frame], MG.story_state(data, frame))
        slower = self.normalize({**self.content, 'pace': 1.8})
        self.assertLess(MG.story_state(slower, 83)['progress'], MG.story_state(data, 83)['progress'])

    def test_values_and_scene_layout_affect_geometry(self):
        data = self.normalize()
        changed = deepcopy(self.content)
        changed['objects'][0]['value'] = 100
        self.assertNotEqual(MG.story_state(data, 120)['positions'], MG.story_state(self.normalize(changed), 120)['positions'])
        changed = {**self.content, 'scene': {'layout': 'arc'}}
        self.assertNotEqual(MG.story_state(data, 120)['positions'], MG.story_state(self.normalize(changed), 120)['positions'])

    def test_partial_frame_selection_is_explicit(self):
        self.assertEqual(MG.requested_frames('180,0,80', 240), [180, 0, 80])
        self.assertEqual(MG.requested_frames(None, 3), [0, 1, 2])
        for invalid in ('0,0', '-1', '240', '0.5', ''):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                MG.requested_frames(invalid, 240)


COMPOSER_SPEC = importlib.util.spec_from_file_location('mg_story_compositor', ROOT / 'compose_3d_story.py')
COMPOSER = importlib.util.module_from_spec(COMPOSER_SPEC)
COMPOSER_SPEC.loader.exec_module(COMPOSER)


@unittest.skipUnless(importlib.util.find_spec('PIL'), 'optional Pillow unavailable')
class StoryboardPlateComposition(unittest.TestCase):
    def fixture(self, root):
        from PIL import Image
        content = json.loads((ROOT / 'examples' / 'three-d-story.json').read_text(encoding='utf-8'))
        content_path = root / 'creative.json'
        content_path.write_text(json.dumps(content), encoding='utf-8')
        plates = root / 'plates'
        (plates / 'frames').mkdir(parents=True)
        scene = plates / 'scene.blend'
        scene.write_bytes(b'Unit-test fixture, not an actual Blender rendering')
        indices = COMPOSER.story_plate_indices(content, 240)
        hashes = []
        for frame, color in zip(indices, ((230,30,20), (20,210,40), (20,40,220))):
            path = plates / 'frames' / ('f%06d.png' % frame)
            Image.new('RGB', (320,180), color).save(path)
            hashes.append(COMPOSER.digest(path))
        data = MG.validate_3d_content(content, frames=240)
        evidence = {
            'engine':'CYCLES', 'samples':128, 'width':320, 'height':180,
            'fps':30, 'frames':240, 'rendered_frames':indices, 'frame_sha256':hashes,
            'scene_story':data['scene_story'], 'links':data['links'],
            'scene_sha256':COMPOSER.digest(scene), 'content_sha256':COMPOSER.digest(content_path),
        }
        (plates / 'evidence.json').write_text(json.dumps(evidence), encoding='utf-8')
        return content, plates

    def test_frozen_renderer_injection_does_not_read_a_live_dependency(self):
        import hashlib
        from types import ModuleType
        from unittest.mock import patch
        source=(ROOT/'compose_3d_story.py').read_bytes()
        module=ModuleType('captured_storyboard')
        module.__file__='/nonexistent/captured/compose_3d_story.py'
        module._RENDERER=MG
        module._RENDERER_SHA256=hashlib.sha256((ROOT/'render_3d.py').read_bytes()).hexdigest()
        with patch.object(Path,'read_bytes',side_effect=AssertionError('live source read')):
            exec(compile(source,module.__file__,'exec'),module.__dict__)
            content={'title':'Frozen','items':[{'label':'A','value':20}],
                     'beats':{'reveal':0,'settle':72,'resolve':180}}
            self.assertEqual(module.story_plate_indices(content,240),[24,96,204])
        self.assertIs(module._RENDERER,MG)

    def test_settled_indices_match_actual_transition_duration(self):
        for name, expected in (('three-d-story.json', [24,96,180]), ('three-d-materials.json', [30,90,180])):
            data = json.loads((ROOT / 'examples' / name).read_text(encoding='utf-8'))
            self.assertEqual(COMPOSER.story_plate_indices(data,240), expected)
            normalized = MG.validate_3d_content(data,frames=240)
            for frame in expected:
                self.assertEqual(MG.story_state(normalized,frame)['progress'], 1)

    def test_native_size_exact_holds_and_explicit_2d_wipe(self):
        import tempfile
        from PIL import Image
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data, plates = self.fixture(root)
            output = root / 'composite'
            evidence = COMPOSER.compose_3d_story(data,240,plates,output)
            self.assertEqual(len(list((output / 'frames').glob('*.png'))),240)
            self.assertEqual(evidence['mode'],'storyboard_2d_plate_composite')
            self.assertTrue(evidence['complete_composite_sequence'])
            self.assertFalse(evidence['complete_blender_sequence'])
            self.assertFalse(evidence['spatial_resampling'])
            self.assertEqual(evidence['source_samples'],128)
            for frame, source in ((0,24),(71,24),(96,96),(239,180)):
                self.assertEqual(COMPOSER.digest(output/'frames'/('f%06d.png'%frame)), COMPOSER.digest(plates/'frames'/('f%06d.png'%source)))
            with Image.open(output/'frames/f000083.png') as image:
                self.assertEqual(image.size,(320,180))
                self.assertEqual(image.getpixel((0,90)),(20,210,40))
                self.assertEqual(image.getpixel((319,90)),(230,30,20))
            for frame, sha in enumerate(evidence['frame_sha256']):
                self.assertEqual(COMPOSER.digest(output/'frames'/('f%06d.png'%frame)),sha)

    def test_corrupt_or_mismatched_plates_fail_before_output(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data, plates = self.fixture(root)
            output = root/'output'
            (plates/'frames/f000096.png').write_bytes(b'corrupt image')
            with self.assertRaisesRegex(ValueError,'hash mismatch'):
                COMPOSER.compose_3d_story(data,240,plates,output)
            self.assertFalse(output.exists())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data, plates = self.fixture(root)
            data['objects'][0]['value'] = 99
            with self.assertRaisesRegex(ValueError,'differs'):
                COMPOSER.compose_3d_story(data,240,plates,root/'output')
            self.assertFalse((root/'output').exists())

    def test_wrong_native_size_and_existing_output_are_rejected(self):
        import tempfile
        from PIL import Image
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data, plates = self.fixture(root)
            path = plates/'frames/f000024.png'
            Image.new('RGB',(640,360)).save(path)
            evidence = json.loads((plates/'evidence.json').read_text())
            evidence['frame_sha256'][0] = COMPOSER.digest(path)
            (plates/'evidence.json').write_text(json.dumps(evidence))
            with self.assertRaisesRegex(ValueError,'dimensions'):
                COMPOSER.compose_3d_story(data,240,plates,root/'output')
            self.assertFalse((root/'output').exists())
            (root/'exists').mkdir()
            with self.assertRaisesRegex(ValueError,'new directory'):
                COMPOSER.compose_3d_story(data,240,plates,root/'exists')


if __name__ == '__main__':
    unittest.main()
