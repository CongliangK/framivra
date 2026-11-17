"""Core-only preview selection drift must fail before plugin imports/providers."""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from vtx.api import frame_timeline
from vtx.pluginlib import hooks


class CorePreviewPlugins(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.path = self.root / 'timeline.json'
        self.document = {'version': 2, 'project': {'fps': 30, 'width': 1920, 'height': 1080,
            'duration_frames': 10}, 'sources': [{'id': 'base', 'path': 'missing.mp4'}],
            'video_tracks': [], 'audio_tracks': [], 'subtitles': []}
        self.path.write_text(json.dumps(self.document))
        self.active = False
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(hooks._registry, '_external_by_name', return_value={}))
        stack.enter_context(patch.object(hooks._registry, 'declared_plugins', return_value={'danger': {'version': '1.0.0'}}))
        stack.enter_context(patch.object(hooks._registry, 'resolve_plugin', return_value={'name': 'danger'}))
        stack.enter_context(patch.object(hooks._registry, 'plugin_state', side_effect=lambda *a, **k: 'active' if self.active else 'disabled'))
        stack.enter_context(patch.object(hooks, '_plugin_hooks_declared', return_value=['probe_source']))
        stack.enter_context(patch.object(hooks, '_timeline_declares', return_value=True))
        self.load = stack.enter_context(patch.object(hooks, '_load_plugin_module', side_effect=AssertionError('plugin import must not run')))

    def test_guard_is_installed_before_context_constructor_fingerprint(self):
        def activate_between_initial_guard_and_context():
            self.active = True
            return {}
        version = 'sha256:' + hashlib.sha256(self.path.read_bytes()).hexdigest()
        with patch('vtx.timeline_frame._environment', side_effect=activate_between_initial_guard_and_context):
            result = frame_timeline(self.path, frame=2, render=True, prepare_gfx=True,
                                    include_png=True, expected_content_version=version)
        self.assertFalse(result['ok'])
        self.assertEqual(result['diagnostics'][0]['code'], 'plugins.changed', result['diagnostics'])
        self.load.assert_not_called()
        self.assertFalse((self.root / 'cache').exists())

    def test_drift_before_fingerprint_and_probe_fails_without_loading(self):
        context = SimpleNamespace(tl=self.document, _core_only_preview=True)
        self.assertEqual(hooks.active_plugins_digest(context), '')
        self.active = True
        for consume in (lambda: hooks.active_plugins_digest(context),
                        lambda: hooks.run_probe_source(context, 'source'),
                        lambda: hooks.run_validate(context),
                        lambda: hooks.contribute_keys(context)):
            with self.subTest(consume=consume):
                with self.assertRaisesRegex(RuntimeError, 'core-only'):
                    consume()
        self.load.assert_not_called()

    def test_normal_context_selection_is_not_changed(self):
        self.active = True
        for context in (None, SimpleNamespace(tl=self.document),
                        SimpleNamespace(tl=self.document, _core_only_preview=False)):
            self.assertEqual([item['name'] for item in hooks.active_plugins(context)], ['danger'])
        self.load.assert_not_called()


if __name__ == '__main__':
    unittest.main()
