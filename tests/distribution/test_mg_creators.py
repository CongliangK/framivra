"""MG creator publication regressions; renderer adapters do NOT accept media quality.

Run with a normally installed matching vtx.api. Optional source templates are
loaded by path; no kernel import-path injection or replacement publisher.
"""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from vtx import api, timeline_store as store

ROOT = Path(__file__).resolve().parents[2]
MG = ROOT / 'templates' / 'mg-styles'


def load(name):
    spec = importlib.util.spec_from_file_location('mg_test_' + name, MG / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MGCreators(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='mg-creators-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.build = load('build')
        self.media = load('build_media')

    def evidence(self, output):
        return sorted(p.relative_to(output).as_posix() for p in output.rglob('*')
                      if p.is_file() and (p.name == 'timeline.json' or '.vtx-' in str(p)))

    def intents(self, output):
        return [json.loads(p.read_bytes()) for p in output.rglob('intent.json')]

    def cli(self, script, *args):
        return subprocess.run([sys.executable, '-I', '-B', str(MG / script), *map(str, args)],
                              env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                              capture_output=True, text=True, timeout=30)

    @contextmanager
    def media_assets(self, action=None, receipt_error=False):
        """Only renderer/tool adapters. Captured build and real store always execute."""
        frozen = self.media.frozen_module
        seen = []
        def capture(name, source, *args):
            seen.append((Path(name).name, source))
            if Path(name).name == 'build.py':
                return frozen(name, source, *args)
            def render(data, frames, output, font):
                self.assertFalse((output.parent / 'timeline.json').exists())
                if action:
                    action(output.parent)
                output.mkdir()
                return {'adapter': 'asset-orchestration-only; no real pixels'}
            return types.SimpleNamespace(validate_pixel_content=lambda *a: {'adapter': True},
                                         render_pixel_frames=render)
        def encode(sequence, pattern, output, root, *args):
            if action is None:
                self.assertFalse((root / 'timeline.json').exists())
            output.write_bytes(b'asset adapter, not encoded video')
        with ExitStack() as stack:
            stack.enter_context(patch.object(self.media, 'frozen_module', capture))
            stack.enter_context(patch.object(self.media, 'encode', encode))
            stack.enter_context(patch.object(self.media.shutil, 'which', return_value='/adapter/ffmpeg'))
            stack.enter_context(patch.object(self.media.subprocess, 'check_output',
                                             side_effect=OSError('receipt version fault') if receipt_error else None,
                                             return_value='adapter ffmpeg\n'))
            yield seen

    # 1: real build/create, whole document, stable unmanaged schema, durable history.
    def test_build_real_create_whole_document_and_request(self):
        output = self.root / 'new'
        compose = self.build._compose
        def extension(*args):
            data, doc = compose(*args)
            doc['x-editor'] = {'keep': [1, '未裁剪']}
            return data, doc
        original_plan = api.timeline_write_plan
        planned = []
        def plan(path, request):
            before = self.evidence(output)
            result = original_plan(path, request)
            self.assertEqual(before, self.evidence(output))
            planned.append(deepcopy(request))
            return result
        with patch.object(self.build, '_compose', extension), patch.object(api, 'timeline_write_plan', plan):
            result = self.build.build('pixel', output, frames=30)
        raw = (output / 'timeline.json').read_bytes()
        self.assertEqual(json.loads(raw), result)
        self.assertEqual(result['x-editor'], {'keep': [1, '未裁剪']})
        self.assertNotIn('_vtx', result)
        self.assertNotIn('id', result['video_tracks'][0])
        self.assertNotIn('id', result['video_tracks'][0]['clips'][0])
        self.assertEqual((output / 'timeline.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(raw, (json.dumps(result, ensure_ascii=False, sort_keys=True,
                                         separators=(',', ':'), allow_nan=False)).encode())
        intents = self.intents(output)
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0]['request'], planned[0])
        req = planned[0]
        self.assertEqual({k: v for k, v in req.items() if k not in ('op_id', 'document')},
                         dict(protocol=2, operation='create', base_revision=None,
                              identity_mode='preserve-unmanaged', payload_mode='document', purpose='create'))
        self.assertEqual(len(list(output.rglob('commit.json'))), 1)

    # 2: both CLI raw byte boundaries, including dry, before any directory write.
    def test_strict_raw_cli_rejects_duplicates_nonfinite_and_precision_loss(self):
        tokens = [b'{"seed":1,"seed":2}', b'{"pace":NaN}',
                  b'{"pace":1.00000000000000001}', b'{"items":[{"label":"x","value":Infinity}]}']
        for script in ('build.py', 'build_media.py'):
            for dry in (False, True):
                for index, raw in enumerate(tokens):
                    with self.subTest(script=script, dry=dry, index=index):
                        content = self.root / 'content.json'
                        content.write_bytes(raw)
                        output = self.root / 'strict'
                        proc = self.cli(script, '--style', 'pixel', '--content', content,
                                        '--output', output, *(['--dry'] if dry else []))
                        self.assertNotEqual(proc.returncode, 0, proc.stdout)
                        self.assertFalse(output.exists())
                        with self.assertRaises(ValueError) as invalid:
                            api.parse_write_json(raw)
                        self.assertIn(str(invalid.exception), proc.stderr)
                        self.assertNotIn('Traceback', proc.stderr)

    # 3: dry/invalid/preexisting/API failure zero writes, exact diagnostics.
    def test_dry_invalid_and_existing_zero_writes(self):
        output = self.root / 'dry'
        with patch.object(self.build, '_public_api', side_effect=AssertionError('dry imported store')):
            self.assertEqual(self.build.build('pixel', output, frames=30, dry=True)['version'], 2)
        self.assertFalse(output.exists())
        for options in ({'frames': 29}, {'content': {'bad': 1}}):
            with self.assertRaises(ValueError):
                self.build.build('pixel', output, **options)
            self.assertFalse(output.exists())
        for kind in ('dir', 'file', 'symlink', 'dangling'):
            existing = self.root / kind
            if kind == 'dir':
                existing.mkdir(); (existing / 'marker').write_bytes(b'keep')
            elif kind == 'file':
                existing.write_bytes(b'keep')
            else:
                existing.symlink_to(self.root / ('file' if kind == 'symlink' else 'missing'))
            for dry in (False, True):
                with self.assertRaises(ValueError):
                    self.build.build('pixel', existing, frames=30, dry=dry)
            self.assertTrue(existing.exists() or existing.is_symlink())
        self.assertEqual((self.root / 'dir' / 'marker').read_bytes(), b'keep')
        self.assertEqual((self.root / 'file').read_bytes(), b'keep')

    def test_missing_old_and_broken_api_diagnostics_before_mkdir(self):
        import builtins
        importer = builtins.__import__
        for absent in ('vtx', 'vtx.api', 'unrelated_dependency'):
            def broken(name, *args, absent=absent, **kw):
                if name == 'vtx.api':
                    raise ModuleNotFoundError('missing ' + absent, name=absent)
                return importer(name, *args, **kw)
            output = self.root / absent
            with patch('builtins.__import__', broken):
                with self.assertRaises(RuntimeError if absent != 'unrelated_dependency' else ModuleNotFoundError) as caught:
                    self.build.build('pixel', output, frames=30)
            self.assertFalse(output.exists())
            if absent != 'unrelated_dependency':
                self.assertIn(sys.executable, str(caught.exception))
                self.assertIsInstance(caught.exception.__cause__, ModuleNotFoundError)
        with patch.object(api, 'timeline_transaction', None):
            with self.assertRaisesRegex(RuntimeError, 'missing exports: timeline_transaction'):
                self.build.build('pixel', self.root / 'old', frames=30)
        self.assertFalse((self.root / 'old').exists())

    # 4: real late conflicts and physical parent addressing; no rebase/retry.
    def test_foreign_target_during_assets_and_after_real_plan_survives(self):
        foreign = b'{ "external": "do not touch" }\n'
        for phase in ('assets', 'plan'):
            with self.subTest(phase=phase):
                output = self.root / phase
                assets, plan = self.build._materialize_assets, api.timeline_write_plan
                def materialize(*args, **kw):
                    assets(*args, **kw)
                    if phase == 'assets':
                        (output / 'timeline.json').write_bytes(foreign)
                def planned(path, request):
                    result = plan(path, request)
                    if phase == 'plan':
                        self.assertTrue(result['ok'], result)
                        (output / 'timeline.json').write_bytes(foreign)
                    return result
                with patch.object(self.build, '_materialize_assets', materialize), patch.object(api, 'timeline_write_plan', planned):
                    with self.assertRaises(RuntimeError) as caught:
                        self.build.build('pixel', output, frames=30)
                self.assertEqual((output / 'timeline.json').read_bytes(), foreign)
                self.assertEqual(self.intents(output), [])
                self.assertIn('original_request=', str(caught.exception))
                self.assertIsNone(caught.exception.request['base_revision'])

    def test_media_foreign_target_during_renderer_and_after_plan_survives(self):
        foreign = b'{ "external": "media race" }\n'
        for phase in ('renderer', 'plan'):
            with self.subTest(phase=phase):
                output = self.root / phase
                original_plan = api.timeline_write_plan
                def render(root):
                    if phase == 'renderer':
                        (root / 'timeline.json').write_bytes(foreign)
                def plan(path, request):
                    result = original_plan(path, request)
                    if phase == 'plan':
                        self.assertTrue(result['ok'], result)
                        (output / 'timeline.json').write_bytes(foreign)
                    return result
                with self.media_assets(action=render), patch.object(api, 'timeline_write_plan', plan):
                    with self.assertRaises(RuntimeError) as caught:
                        self.media.assemble('pixel', output, {}, frames=30)
                self.assertEqual((output / 'timeline.json').read_bytes(), foreign)
                self.assertEqual(self.intents(output), [])
                self.assertIsNone(caught.exception.request['base_revision'])
                self.assertEqual(caught.exception.request['document']['sources'][-1]['id'], 'composition')

    def test_claim_exclusivity_and_symlink_dotdot_parent(self):
        target = self.root / 'claimed'
        target.mkdir()
        with self.assertRaises(FileExistsError):
            self.build._claim_creation(target)
        self.assertEqual(list(target.iterdir()), [])
        (self.root / 'physical' / 'sub').mkdir(parents=True)
        (self.root / 'lexical').mkdir()
        (self.root / 'lexical' / 'link').symlink_to(self.root / 'physical' / 'sub')
        via = self.root / 'lexical' / 'link' / '..' / 'new'
        self.build.build('pixel', via, frames=30)
        self.assertTrue((self.root / 'physical' / 'new' / 'timeline.json').is_file())
        self.assertFalse((self.root / 'lexical' / 'new').exists())

    # 5: captured asset-only media path, final single publication and failure boundary.
    def test_media_asset_adapter_single_final_publication_and_frozen_sources(self):
        output = self.root / 'media'
        with self.media_assets() as seen:
            doc = self.media.assemble('pixel', output, {}, frames=30)
        self.assertEqual(json.loads((output / 'timeline.json').read_bytes()), doc)
        self.assertEqual([s['id'] for s in doc['sources']], ['cue', 'composition'])
        self.assertEqual(doc['video_tracks'][0]['clips'][0]['src'], 'composition')
        self.assertEqual(len(self.intents(output)), 1)
        receipt = json.loads((output / 'source-receipt.json').read_bytes())
        for name, raw in seen:
            self.assertEqual((output / name).read_bytes(), raw)
            self.assertEqual(receipt['renderer_sources'][name], hashlib.sha256(raw).hexdigest())
        self.assertIn('artistic acceptance pending', receipt['state'])
        dry = self.root / 'media-dry'
        with self.media_assets(), patch.object(api, 'read_timeline_for_write', side_effect=AssertionError('dry snapshot')):
            self.assertEqual(self.media.assemble('pixel', dry, {}, frames=30, dry=True)['style'], 'pixel')
        self.assertFalse(dry.exists())

    def test_asset_failure_no_timeline_and_receipt_failure_is_committed(self):
        output = self.root / 'asset-fault'
        def fail(_):
            raise OSError('renderer asset fault')
        with self.media_assets(action=fail):
            with self.assertRaisesRegex(OSError, 'renderer asset fault'):
                self.media.assemble('pixel', output, {}, frames=30)
        self.assertTrue((output / 'cue.wav').is_file())
        self.assertEqual(self.evidence(output), [])
        committed = self.root / 'receipt-fault'
        with self.media_assets(receipt_error=True):
            with self.assertRaisesRegex(RuntimeError, 'timeline committed; source receipt incomplete') as caught:
                self.media.assemble('pixel', committed, {}, frames=30)
        self.assertEqual(len(list(committed.rglob('commit.json'))), 1)
        self.assertTrue((committed / 'timeline.json').is_file())
        self.assertIsInstance(caught.exception.__cause__, OSError)
        self.assertEqual(caught.exception.operation_id, self.intents(committed)[0]['op_id'])
        self.assertEqual(caught.exception.request, self.intents(committed)[0]['request'])

    # 6: real store checkpoint fault injection, not a mocked plan/publisher/CAS.
    def test_real_final_cas_and_outcome_unknown_preserve_request(self):
        for phase, code in (('staged', 'recovery_required'), ('replaced', 'outcome_unknown')):
            with self.subTest(phase=phase):
                output = self.root / phase
                foreign = b'{"external":"late"}\n'
                def fault(name):
                    if name == phase:
                        if phase == 'staged':
                            (output / 'timeline.json').write_bytes(foreign)
                        else:
                            raise OSError('postreplace fault')
                with patch.object(store, '_checkpoint', fault):
                    with self.assertRaises(RuntimeError) as caught:
                        self.build.build('pixel', output, frames=30)
                error = caught.exception
                self.assertEqual(error.code, code)
                self.assertEqual(len(self.intents(output)), 1)
                self.assertEqual(error.request, self.intents(output)[0]['request'])
                self.assertEqual(error.operation_id, error.request['op_id'])
                if phase == 'staged':
                    self.assertEqual((output / 'timeline.json').read_bytes(), foreign)
                else:
                    raw = (output / 'timeline.json').read_bytes()
                    replay = api.timeline_transaction(output / 'timeline.json', error.request)
                    self.assertTrue(replay['ok'], replay)
                    self.assertTrue(replay['replayed'])
                    self.assertEqual((output / 'timeline.json').read_bytes(), raw)

    # 7: ordinary installed CLI interoperates with creator's genuine v2 history.
    def test_cli_replay_replace_undo_preserves_unmanaged_create(self):
        output = self.root / 'replay'
        original = self.build.build('pixel', output, frames=30)
        target = output / 'timeline.json'
        raw = target.read_bytes()
        request = self.intents(output)[0]['request']
        def command(req):
            request_file = self.root / 'request.json'
            request_file.write_text(json.dumps(req), encoding='utf-8')
            proc = subprocess.run([sys.executable, '-I', '-B', '-m', 'vtx.cli.commands',
                                   '-t', str(target), 'store', '--request', str(request_file)],
                                  env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                                  capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            return json.loads(proc.stdout)
        self.assertTrue(command(request)['replayed'])
        self.assertEqual(target.read_bytes(), raw)
        document = deepcopy(original)
        document['project']['x-note'] = 'edited'
        change = dict(request, operation='replace', op_id='mg-test-replace',
                      base_revision=api.read_timeline_for_write(target)['revision'],
                      purpose='legacy-write', document=document)
        self.assertTrue(command(change)['ok'])
        undo = {k: v for k, v in change.items() if k != 'document'}
        undo.update(operation='undo', op_id='mg-test-undo', undo_of=change['op_id'],
                    base_revision=api.read_timeline_for_write(target)['revision'])
        self.assertTrue(command(undo)['ok'])
        self.assertEqual(target.read_bytes(), raw)
        self.assertNotIn('_vtx', json.loads(target.read_bytes()))
        self.assertEqual(len(self.intents(output)), 3)


if __name__ == '__main__':
    unittest.main()
