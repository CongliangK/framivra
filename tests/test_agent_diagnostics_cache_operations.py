"""Actual cache producers retain fingerprint bytes, order, and native failures."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from vtx import cache_state
from vtx.agent_diagnostics import main
from vtx.diagnostics import mark_handled


class CacheOperations(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def context(self, **values):
        timeline = {'project': {}, 'sources': [], 'video_tracks': []}
        timeline.update(values)
        return SimpleNamespace(root=self.root, tl=timeline, diagnostics=[],
                               tl_path=SimpleNamespace(read_bytes=Mock(return_value=b'timeline')))

    def fingerprint(self, c, **kwargs):
        with patch('vtx.pluginlib.hooks.active_plugins_digest', return_value=''):
            return cache_state.receipt_fingerprint(c, **kwargs)

    def assert_record(self, c, rule, path, *, status='fail'):
        self.assertEqual(len(c.diagnostics), 1, c.diagnostics)
        record = c.diagnostics[0]
        self.assertEqual(record['rule_id'], rule)
        self.assertEqual(record['json_path'], path)
        self.assertEqual(record['status'], status)
        return record

    def assert_opaque(self, c, error, invoke):
        with self.assertRaises(type(error)) as caught:
            invoke()
        self.assertIs(caught.exception, error)
        self.assertFalse(getattr(error, '_vtx_diagnostic_handled', False))
        self.assertEqual(c.diagnostics, [])

    def test_dependency_predicate_uses_existing_acceptance_and_same_error(self):
        for deps in (None, False, 3, 'file', {}, ['file', None], ['file', 3], [''], [' '], ['a\0b']):
            with self.subTest(deps=deps):
                c = self.context(project={'cache_dependencies': deps})
                marked = []
                def record(error):
                    marked.append(error)
                    mark_handled(error)
                with patch.object(cache_state, 'mark_handled', side_effect=record), \
                        self.assertRaises(ValueError) as caught:
                    cache_state.declared_dependencies(c)
                self.assertEqual(str(caught.exception),
                                 'project.cache_dependencies must be a list of nonempty file paths')
                self.assertIs(marked[0], caught.exception)
                self.assert_record(c, 'load.cache_dependencies', '$.project.cache_dependencies' + (f'[{len(deps) - 1}]' if isinstance(deps, list) else ''))
        for deps in ([], ['file'], ['\ud800']):
            c = self.context(project={'cache_dependencies': deps})
            self.assertIs(cache_state.declared_dependencies(c), deps)
            self.assertEqual(c.diagnostics, [])

    def test_dependency_predicate_does_not_reiterate_rejected_list(self):
        calls = []
        class Dependencies(list):
            def __iter__(self):
                calls.append('iterate')
                return super().__iter__()
        c = self.context(project={'cache_dependencies': Dependencies(['a', None, 'b'])})
        with self.assertRaises(ValueError):
            cache_state.declared_dependencies(c)
        self.assertEqual(calls, ['iterate'])
        self.assert_record(c, 'load.cache_dependencies', '$.project.cache_dependencies[1]')

    def test_missing_dependency_records_only_actual_first_false_result(self):
        (self.root / 'exists').write_bytes(b'x')
        for deps, wanted, calls in ((['missing', 'exists'], 0, ['missing']),
                                   (['exists', 'missing', 'later'], 1, ['exists', 'missing'])):
            with self.subTest(deps=deps):
                c = self.context(project={'cache_dependencies': deps})
                reads = []
                original = Path.is_file
                def is_file(path):
                    reads.append(path.name)
                    return original(path)
                with patch.object(Path, 'is_file', is_file), self.assertRaises(ValueError) as caught:
                    cache_state.validate_cache_dependencies(c)
                self.assertEqual(str(caught.exception), 'cache dependency is not a file: missing')
                self.assertEqual(reads, calls)
                record = self.assert_record(c, 'load.cache_dependency',
                                           f'$.project.cache_dependencies[{wanted}]')
                self.assertEqual(record['measured'], {'path': 'missing', 'is_file': False})
                self.assertEqual(record['source']['resource'], str(self.root / 'missing'))

    def test_dependency_io_failure_never_becomes_missing_or_replays(self):
        for later in ('valid', 'missing'):
            c = self.context(project={'cache_dependencies': ['first', later]})
            error = PermissionError('first dependency cannot be checked')
            with patch.object(Path, 'is_file', side_effect=error) as check:
                self.assert_opaque(c, error, lambda: cache_state.validate_cache_dependencies(c))
            self.assertEqual(check.call_count, 1)

    def test_dependency_mapping_failure_is_at_actual_container(self):
        c = self.context(project=None)
        with self.assertRaises(AttributeError) as caught:
            cache_state.declared_dependencies(c)
        self.assertEqual(str(caught.exception), "'NoneType' object has no attribute 'get'")
        self.assert_record(c, 'load.operation', '$.project')

    def test_source_sort_key_failure_keeps_original_index_and_does_no_reads(self):
        c = self.context(sources=[{'path': 'z'}, {}, None, {'path': 1}],
                         video_tracks=[None])
        with patch.object(Path, 'is_file') as check, self.assertRaises(AttributeError):
            self.fingerprint(c)
        check.assert_not_called()
        record = self.assert_record(c, 'load.operation', '$.sources[2]')
        self.assertEqual(record['measured']['operands']['operation'], 'mapping_get:path')

    def test_source_sort_retains_original_iteration_and_length_hint_callbacks(self):
        events = []
        class Sources(list):
            def __iter__(self):
                events.append('iter')
                return super().__iter__()
            def __len__(self):
                events.append('len')
                return super().__len__()
        c = self.context(sources=Sources([{'path': 'a'}]))
        self.fingerprint(c)
        self.assertEqual(events, ['iter', 'len'])
        events.clear()
        error = ValueError('source length callback')
        class FailingSources(Sources):
            def __len__(self):
                events.append('len')
                raise error
        c = self.context(sources=FailingSources([{'path': 'a'}]))
        self.assert_opaque(c, error, lambda: self.fingerprint(c))
        self.assertEqual(events, ['iter', 'len'])

    def test_sorted_source_access_keeps_original_index_and_native_message(self):
        for source, exception, rule, path in (
                ({}, KeyError, 'load.required_field', '$.sources[2]["path"]'),
                ({'path': None}, TypeError, 'load.operation', '$.sources[2].path'),
                ({'path': 4}, TypeError, 'load.operation', '$.sources[2].path')):
            with self.subTest(source=source):
                c = self.context(sources=[{'path': 'z'}, {'path': 'a'}, source])
                with self.assertRaises(exception) as caught:
                    self.fingerprint(c)
                self.assert_record(c, rule, path)
                try:
                    self.root / source['path']
                except exception as original:
                    self.assertEqual(str(caught.exception), str(original))

    def test_native_collection_failures_and_permissive_empty_controls(self):
        for field, values, path in (
                ('sources', None, '$.sources'),
                ('video_tracks', None, '$.video_tracks'),
                ('video_tracks', [{'kind': 'gfx', 'clips': None}], '$.video_tracks[0].clips')):
            with self.subTest(field=field, values=values):
                c = self.context(**{field: values})
                with self.assertRaises(TypeError):
                    self.fingerprint(c)
                self.assert_record(c, 'load.iterable', path)
        for field in ('sources', 'video_tracks'):
            for empty in ([], {}, ''):
                c = self.context(**{field: empty})
                self.assertEqual(self.fingerprint(c), hashlib.sha256(b'timeline').hexdigest())
                self.assertEqual(c.diagnostics, [])
        # #52 now traverses every track for clip dependency declarations.
        c = self.context(video_tracks=[{'kind': 'ordinary', 'clips': None}])
        with self.assertRaises(TypeError):
            self.fingerprint(c)
        self.assert_record(c, 'load.iterable', '$.video_tracks[0].clips')

    def test_track_and_gfx_access_failures_use_original_indices(self):
        cases = (
            ([{}, None], AttributeError, 'load.operation', '$.video_tracks[1]'),
            ([{}, {'kind': 'gfx', 'clips': [{'src': 'a'}, {}]}], KeyError,
             'load.required_field', '$.video_tracks[1].clips[1]["src"]'),
            ([{}, {'kind': 'gfx', 'clips': [{'src': 'a'}, {'src': 3}]}], TypeError,
             'load.operation', '$.video_tracks[1].clips[1].src'),
        )
        for tracks, exception, rule, path in cases:
            with self.subTest(tracks=tracks):
                c = self.context(video_tracks=tracks)
                with self.assertRaises(exception):
                    self.fingerprint(c)
                self.assert_record(c, rule, path)

    def test_surrogate_encode_precedes_unreachable_unrelated_defects(self):
        for tracks in ([], [None]):
            c = self.context(sources=[{'path': '\ud800'}], video_tracks=tracks)
            with self.assertRaises(UnicodeEncodeError) as caught:
                self.fingerprint(c)
            record = self.assert_record(c, 'load.operation', '$.sources[0].path')
            self.assertEqual(record['message'], str(caught.exception))
            self.assertEqual(record['measured']['operands']['operation'], 'encode')

    def test_gfx_and_dependency_encode_have_direct_field_origin(self):
        c = self.context(video_tracks=[{'kind': 'gfx', 'clips': [{'src': '\ud800'}]}])
        with self.assertRaises(UnicodeEncodeError):
            self.fingerprint(c)
        self.assert_record(c, 'load.operation', '$.video_tracks[0].clips[0].src')
        c = self.context(project={'cache_dependencies': ['\ud800']})
        with patch.object(cache_state, 'file_digest', return_value='digest'), \
                self.assertRaises(UnicodeEncodeError):
            self.fingerprint(c)
        self.assert_record(c, 'load.operation', '$.project.cache_dependencies[0]')

    def test_plugin_result_encode_is_provider_metadata_not_input_defect(self):
        for value in (1, True, ['digest'], '\ud800'):
            with self.subTest(value=value):
                c = self.context(sources=[{'path': 'missing'}])
                with patch('vtx.pluginlib.hooks.active_plugins_digest', return_value=value) as callback, \
                        self.assertRaises((AttributeError, UnicodeEncodeError)) as caught:
                    cache_state.receipt_fingerprint(c)
                callback.assert_called_once_with(c)
                record = self.assert_record(c, 'load.operation', '$', status='unknown')
                self.assertEqual(record['source'], {'domain': 'provider',
                                 'provider': 'active_plugins_digest', 'field': 'return_value'})
                self.assertEqual(record['message'], str(caught.exception))
        for value in ('', None, False, 0, [], {}):
            c = self.context()
            with patch('vtx.pluginlib.hooks.active_plugins_digest', return_value=value):
                self.assertEqual(cache_state.receipt_fingerprint(c),
                                 hashlib.sha256(b'timeline').hexdigest())
            self.assertEqual(c.diagnostics, [])

    def test_opaque_plugin_callbacks_and_custom_encode_keep_same_error(self):
        for error in (ValueError('callback value'), TypeError('callback type'),
                      KeyError('callback key'), ImportError('callback import')):
            c = self.context()
            with patch('vtx.pluginlib.hooks.active_plugins_digest', side_effect=error) as callback:
                self.assert_opaque(c, error, lambda: cache_state.receipt_fingerprint(c))
            callback.assert_called_once_with(c)
        c = self.context()
        error = ValueError('custom encode failed')
        class Digest:
            def encode(self):
                raise error
        with patch('vtx.pluginlib.hooks.active_plugins_digest', return_value=Digest()):
            self.assert_opaque(c, error, lambda: cache_state.receipt_fingerprint(c))

    def test_custom_mapping_string_and_path_callbacks_are_not_native_findings(self):
        for method in ('get', '__getitem__', '__str__', '__fspath__'):
            with self.subTest(method=method):
                # pathlib itself converts a failing __fspath__ TypeError into
                # its unsupported-operand TypeError; ValueError propagates.
                error = (ValueError if method == '__fspath__' else TypeError)('opaque ' + method)
                class Mapping(dict):
                    def get(self, *args):
                        if method == 'get':
                            raise error
                        return super().get(*args)
                    def __getitem__(self, key):
                        if method == '__getitem__':
                            raise error
                        return super().__getitem__(key)
                class Value:
                    def __str__(self):
                        if method == '__str__':
                            raise error
                        return 'value'
                    def __fspath__(self):
                        raise error
                source = Mapping(path='asset') if method in ('get', '__getitem__') else {'path': Value()}
                c = self.context(sources=[source])
                self.assert_opaque(c, error, lambda: self.fingerprint(c))

    def test_io_and_digest_failures_stay_unknown_at_every_resource(self):
        cases = (
            ({'sources': [{'path': 'source'}]}, 'media_stat_digest'),
            ({'video_tracks': [{'kind': 'gfx', 'clips': [{'src': 'graphic'}]}]}, 'file_digest'),
            ({'project': {'cache_dependencies': ['dependency']}}, 'file_digest'),
            ({'subtitles_src': 'subtitle'}, 'file_digest'),
        )
        for values, method in cases:
            for error in (PermissionError('resource read'), ValueError('digest callback')):
                with self.subTest(values=values, error=error):
                    c = self.context(**values)
                    with patch.object(Path, 'is_file', return_value=True), \
                            patch.object(cache_state, method, side_effect=error) as callback:
                        self.assert_opaque(c, error, lambda: self.fingerprint(c))
                    self.assertEqual(callback.call_count, 1)
        c = self.context(sources=[{'path': 'source'}], video_tracks=[None])
        error = PermissionError('source stat')
        with patch.object(Path, 'is_file', side_effect=error) as callback:
            self.assert_opaque(c, error, lambda: self.fingerprint(c))
        self.assertEqual(callback.call_count, 1)

    def test_hash_bytes_order_read_counts_and_stable_source_sort_are_unchanged(self):
        for supplied in (False, True):
            for diagnostics in (False, True):
                with self.subTest(supplied=supplied, diagnostics=diagnostics):
                    events, updates = [], []
                    class Source(dict):
                        def __init__(self, label, path):
                            super().__init__(path=path)
                            self.label = label
                        def get(self, *args):
                            events.append(('get', self.label, args))
                            return super().get(*args)
                        def __getitem__(self, key):
                            events.append(('field', self.label, key))
                            return super().__getitem__(key)
                    sources = [Source('z', 'z'), Source('a1', 'a'), Source('a2', 'a')]
                    c = self.context(sources=sources,
                                     video_tracks=[{'kind': 'ordinary', 'clips': []},
                                                   {'kind': 'gfx', 'clips': [{'src': 'graphic'}]}],
                                     project={'cache_dependencies': ['second', 'first']},
                                     subtitles_src='subtitle')
                    if not diagnostics:
                        del c.diagnostics
                    real_sha256 = hashlib.sha256
                    class Hash:
                        def __init__(self):
                            self.inner = real_sha256()
                        def update(self, value):
                            updates.append(value)
                            self.inner.update(value)
                        def hexdigest(self):
                            return self.inner.hexdigest()
                    def is_file(path):
                        events.append(('is_file', path.name))
                        return True
                    def media(path):
                        events.append(('media', path.name))
                        return 'media:' + path.name
                    def file(path):
                        events.append(('file', path.name))
                        return 'file:' + path.name
                    # #47 adds a real HTML scan after this historical producer.
                    # Isolate that separate phase: these deliberately nonexistent
                    # files/digest strings test only native ordering and callbacks.
                    with patch.object(cache_state, '_append_gfx_dependency_identity') as gfx_identity, \
                            patch.object(cache_state.hashlib, 'sha256', Hash), \
                            patch.object(Path, 'is_file', is_file), \
                            patch.object(cache_state, 'media_stat_digest', side_effect=media), \
                            patch.object(cache_state, 'file_digest', side_effect=file), \
                            patch('vtx.pluginlib.hooks.active_plugins_digest', return_value='plugins') as callback:
                        fingerprint = cache_state.receipt_fingerprint(c, **(
                            {'timeline_bytes': b'timeline'} if supplied else {}))
                    gfx_identity.assert_called_once()
                    self.assertEqual(gfx_identity.call_args.args[2], [
                        (c.tl['video_tracks'][1]['clips'][0], self.root / 'graphic',
                         '$.video_tracks[1].clips[0]', True)])
                    expected = [b'timeline', b'a:media:a\n', b'a:media:a\n', b'z:media:z\n',
                                b'graphic:file:graphic\n', b'second:file:second\n',
                                b'first:file:first\n', b'subtitles_src:file:subtitle\n', b'plugins']
                    self.assertEqual(updates, expected)
                    self.assertEqual(fingerprint, real_sha256(b''.join(expected)).hexdigest())
                    self.assertEqual(c.tl_path.read_bytes.call_count, 0 if supplied else 1)
                    callback.assert_called_once_with(c)
                    self.assertEqual(events, [
                        ('get', 'z', ('path', '')), ('get', 'a1', ('path', '')),
                        ('get', 'a2', ('path', '')),
                        ('field', 'a1', 'path'), ('get', 'a1', ('path', '')),
                        ('is_file', 'a'), ('media', 'a'),
                        ('field', 'a2', 'path'), ('get', 'a2', ('path', '')),
                        ('is_file', 'a'), ('media', 'a'),
                        ('field', 'z', 'path'), ('get', 'z', ('path', '')),
                        ('is_file', 'z'), ('media', 'z'),
                        ('is_file', 'graphic'), ('file', 'graphic'),
                        ('file', 'second'), ('file', 'first'),
                        ('is_file', 'subtitle'), ('file', 'subtitle')])
                    self.assertEqual(getattr(c, 'diagnostics', []), [])

    def test_surrogate_subtitle_path_is_not_eagerly_encoded_or_rejected(self):
        c = self.context(subtitles_src='\ud800')
        expected = hashlib.sha256(b'timeline' + b'subtitles_src:MISSING\n').hexdigest()
        self.assertEqual(self.fingerprint(c), expected)
        self.assertEqual(c.diagnostics, [])

    def test_identity_mismatch_is_only_emitted_after_successful_recheck(self):
        c = self.context()
        c.input_fingerprint = 'pinned'
        with patch.object(cache_state, 'receipt_fingerprint', return_value='observed') as check, \
                self.assertRaises(cache_state.InputIdentityError) as caught:
            cache_state.verify_input_identity(c)
        check.assert_called_once_with(c)
        record = self.assert_record(c, 'runtime.input_identity', '$')
        self.assertEqual(record['measured'], {'input_fingerprint': 'observed'})
        self.assertEqual(record['threshold'], {'input_fingerprint': 'pinned'})
        self.assertEqual(c.input_fingerprint, 'pinned')
        self.assertEqual(record['message'], str(caught.exception))
        self.assertTrue(caught.exception._vtx_diagnostic_handled)
        c.diagnostics.clear()
        with patch.object(cache_state, 'receipt_fingerprint', return_value='pinned') as check:
            cache_state.verify_input_identity(c)
        check.assert_called_once_with(c)
        self.assertEqual(c.diagnostics, [])

    def test_identity_recheck_failure_retains_wrapped_cause_without_inference(self):
        for error in (PermissionError('unreadable'), ValueError('callback'), RuntimeError('callback')):
            c = self.context()
            c.input_fingerprint = 'pinned'
            with patch.object(cache_state, 'receipt_fingerprint', side_effect=error) as check, \
                    self.assertRaises(cache_state.InputIdentityError) as caught:
                cache_state.verify_input_identity(c)
            check.assert_called_once_with(c)
            self.assertIs(caught.exception.__cause__, error)
            self.assertFalse(getattr(caught.exception, '_vtx_diagnostic_handled', False))
            self.assertFalse(getattr(error, '_vtx_diagnostic_handled', False))
            self.assertEqual(c.diagnostics, [])
            self.assertEqual(c.input_fingerprint, 'pinned')

    def test_real_agent_dependency_failure_is_one_precise_json_record(self):
        timeline = {'project': {'fps': 30, 'duration_frames': 30, 'width': 640, 'height': 360,
                                'cache_dependencies': ['exists', 'missing']},
                    'sources': [], 'video_tracks': [], 'audio_tracks': [], 'subtitles': []}
        (self.root / 'exists').write_bytes(b'x')
        path = self.root / 'timeline.json'
        path.write_text(json.dumps(timeline))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(['-t', str(path), 'validate', '--format', 'agent-json']), 1)
        report = json.loads(output.getvalue())
        record = report['diagnostics'][0]
        self.assertEqual(len(report['diagnostics']), 1)
        self.assertEqual(record['rule_id'], 'load.cache_dependency')
        self.assertEqual(record['json_path'], '$.project.cache_dependencies[1]')
        self.assertEqual(record['message'], 'cache dependency is not a file: missing')


if __name__ == '__main__':
    unittest.main()
