"""Private bridge and sealed-package correlation tests; no media/backend claims."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from vtx import acceptance as a
from vtx import execution_links as links
from tests.acceptance import test_acceptance


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.timeline = self.root / 'timeline.json'
        self.timeline.write_text('{}')
        self.export = str(uuid.uuid4())

    def request(self):
        links.initialize(self.root)
        return links.prepare_request(self.root, self.export, 0)

    def test_absent_and_dry_are_no_write(self):
        before = list(self.root.iterdir())
        self.assertEqual(links.open_response(self.timeline, links.ROLES[0]), (None, None))
        self.assertEqual(before, list(self.root.iterdir()))
        self.request()
        self.assertEqual(links.open_response(self.timeline, links.ROLES[0], dry=True), (None, None))
        self.assertFalse(list((self.root / links.DIRECTORY / 'responses').iterdir()))

    def test_exclusive_response_and_project_relative_resolution(self):
        request = self.request()
        stream, observed = links.open_response(self.timeline, links.ROLES[0])
        self.assertEqual(observed, request)
        stream.write('unit evidence\n')
        stream.close()
        with contextlib.redirect_stderr(io.StringIO()) as warning:
            self.assertEqual(links.open_response(self.timeline, links.ROLES[0]), (None, None))
        self.assertIn('incomplete', warning.getvalue())
        self.assertEqual(links.response_bytes(self.root, request), b'unit evidence\n')

    def test_reserved_directory_cannot_be_reused(self):
        self.request()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(links.initialize(self.root))

    def test_role_malformed_oversized_and_nonprivate_requests(self):
        request = self.request()
        path = self.root / links.DIRECTORY / 'request.json'
        for data in ('{}', '{"x":1,"x":2}', ' ' * 5000,
                     json.dumps({**request, 'nonce': '../elsewhere'}),
                     json.dumps({**request, 'command_index': True}),
                     json.dumps({**request, 'schema_version': 'vtx.observation-request/2'})):
            path.write_text(data)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(links.open_response(self.timeline, links.ROLES[0]), (None, None))
        path.write_text(json.dumps(request))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(links.open_response(self.timeline, links.ROLES[1]), (None, None))
            path.chmod(0o644)
            self.assertEqual(links.open_response(self.timeline, links.ROLES[0]), (None, None))
        self.assertFalse(list((self.root / links.DIRECTORY / 'responses').iterdir()))

    def test_symlink_and_hardlink_requests_never_activate(self):
        request = self.request()
        path = self.root / links.DIRECTORY / 'request.json'
        target = self.root / 'target'
        path.rename(target)
        for link in ('symlink', 'hardlink'):
            if link == 'symlink':
                path.symlink_to(target)
            else:
                os.link(target, path)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(links.open_response(self.timeline, links.ROLES[0]), (None, None))
            path.unlink()
        self.assertEqual(json.loads(target.read_text()), request)

    def test_symlink_response_directory_and_file(self):
        request = self.request()
        responses = self.root / links.DIRECTORY / 'responses'
        outside = self.root / 'outside'
        outside.mkdir(mode=0o700)
        responses.rmdir()
        responses.symlink_to(outside, target_is_directory=True)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(links.open_response(self.timeline, links.ROLES[0]), (None, None))
        self.assertFalse(list(outside.iterdir()))
        responses.unlink()
        responses.mkdir(mode=0o700)
        response = responses / (request['nonce'] + '.jsonl')
        response.symlink_to(outside / 'stolen')
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(links.open_response(self.timeline, links.ROLES[0]), (None, None))
        self.assertFalse((outside / 'stolen').exists())


class PackageLinkTests(test_acceptance.AcceptanceTests):
    def observed_render(self, cmd, **kwargs):
        from vtx.events import ExecutionContext
        result = self.render(cmd, **kwargs)
        role = links.ROLES[0 if cmd[5] == 'all' else 1]
        stream, request = links.open_response(cmd[4], role)
        execution = ExecutionContext(stream=stream, request=request)
        execution.finish(result.returncode)
        return result

    def sidecar(self):
        return a.read_json(self.package / links.SIDECAR)

    def rehash(self, name):
        manifest = a.read_json(self.package / 'manifest.json')
        manifest['files'][name] = a.describe(self.package / name)
        self.rewrite('manifest.json', manifest)
        self.reseal()

    def test_unobserved_children_are_incomplete(self):
        self.build()
        self.assertEqual(self.verify()['execution_linkage'], 'incomplete')
        rows = self.sidecar()['commands']
        self.assertEqual([r['run_id'] for r in rows], [None, None])
        self.assertEqual([r['reason'] for r in rows], ['start_unobserved'] * 2)

    def test_spawn_failure_has_zero_observed_ids(self):
        def fail(*args, **kwargs):
            raise FileNotFoundError('test spawn error')
        self.build(fail)
        rows = self.sidecar()['commands']
        self.assertEqual(rows[0]['launch_status'], 'launch_error')
        self.assertEqual(rows[0]['exit_code'], -1)
        self.assertEqual(rows[1], links.not_attempted(1))
        self.assertEqual(self.verify()['execution_linkage'], 'incomplete')

    def test_old_package_linkage_unknown(self):
        self.build()
        manifest = a.read_json(self.package / 'manifest.json')
        for name in list(manifest['files']):
            if name == links.SIDECAR or name.startswith('artifacts/execution-'):
                (self.package / name).unlink()
                del manifest['files'][name]
        self.rewrite('manifest.json', manifest)
        self.reseal()
        self.assertEqual(self.verify()['execution_linkage'], 'unknown')

    def test_two_children_have_distinct_execution_export_ids(self):
        self.build(self.observed_render)
        self.assertEqual(self.verify()['execution_linkage'], 'complete')
        sidecar = self.sidecar()
        self.assertEqual(sidecar['export_id'], self.result['run_id'])
        self.assertEqual(len({sidecar['export_id'], *(row['run_id'] for row in sidecar['commands'])}), 3)
        self.assertEqual(self.verify()['run_id'], self.result['run_id'])

    def test_second_spawn_error_has_one_child(self):
        def render(cmd, **kwargs):
            if cmd[5] == 'qc':
                raise OSError('second spawn error')
            return self.observed_render(cmd, **kwargs)
        self.build(render)
        rows = self.sidecar()['commands']
        self.assertIsNotNone(rows[0]['run_id'])
        self.assertIsNone(rows[1]['run_id'])
        self.assertEqual(rows[1]['launch_status'], 'launch_error')
        self.assertEqual(self.verify()['execution_linkage'], 'incomplete')

    def test_unavailable_safe_filesystem_preserves_build(self):
        with patch.object(links, '_directory', side_effect=OSError('unsupported')), contextlib.redirect_stderr(io.StringIO()):
            self.build()
        self.assertEqual(self.verify()['execution_linkage'], 'incomplete')
        self.assertEqual(self.verify()['gates']['process']['status'], 'passed')

    def test_request_write_failure_preserves_build(self):
        with patch.object(links, '_bridge', side_effect=OSError('unavailable')), contextlib.redirect_stderr(io.StringIO()):
            self.build()
        self.assertEqual(self.verify()['execution_linkage'], 'incomplete')
        self.assertEqual(self.verify()['gates']['process']['status'], 'passed')

    def test_reanchored_nonce_mismatch(self):
        self.build(self.observed_render)
        row = self.sidecar()['commands'][0]
        path = self.package / row['response']
        data = [json.loads(line) for line in path.read_text().splitlines()]
        data[0]['extensions']['observation']['nonce'] = str(uuid.uuid4())
        path.write_text(''.join(json.dumps(event) + '\n' for event in data))
        self.rehash(row['response'])
        with self.assertRaisesRegex(ValueError, 'issued request'):
            self.verify()

    def test_reanchored_export_and_completeness_lies_reject(self):
        self.build()
        original = self.sidecar()
        for altered in ({**original, 'export_id': str(uuid.uuid4())},
                        {**original, 'commands': [{**original['commands'][0], 'evidence_status': 'complete'}, original['commands'][1]]}):
            self.rewrite(links.SIDECAR, altered)
            self.rehash(links.SIDECAR)
            with self.assertRaises(ValueError):
                self.verify()

    def test_swapped_responses_reject(self):
        self.build(self.observed_render)
        sidecar = self.sidecar()
        sidecar['commands'][0]['response'], sidecar['commands'][1]['response'] = (
            sidecar['commands'][1]['response'], sidecar['commands'][0]['response'])
        self.rewrite(links.SIDECAR, sidecar)
        self.rehash(links.SIDECAR)
        with self.assertRaises(ValueError):
            self.verify()

    def test_receipt_commit_reference_and_tamper(self):
        from vtx.events import ExecutionContext
        def render(cmd, **kwargs):
            result = self.render(cmd, **kwargs)
            role = links.ROLES[0 if cmd[5] == 'all' else 1]
            stream, request = links.open_response(cmd[4], role)
            execution = ExecutionContext(stream=stream, request=request)
            receipt_path = Path(cmd[4]).parent / 'out/receipt.json'
            receipt = a.read_json(receipt_path)
            with execution.task(stage='qc').scope():
                def commit(ref):
                    receipt['stages']['qc']['observed_by'] = ref
                    receipt_path.write_text(json.dumps(receipt))
                execution.receipt('qc', True, commit)
            execution.finish(0)
            return result
        self.build(render)
        self.assertEqual(self.verify()['execution_linkage'], 'complete')
        receipt = a.read_json(self.package / 'artifacts/receipt.json')
        receipt['stages']['qc']['observed_by']['task_id'] = str(uuid.uuid4())
        self.rewrite('artifacts/receipt.json', receipt)
        self.rehash('artifacts/receipt.json')
        with self.assertRaisesRegex(ValueError, 'receipt commit'):
            self.verify()

    def test_dangling_receipt_commit_is_incomplete(self):
        self.build(self.observed_render)
        row = self.sidecar()['commands'][0]
        receipt = a.read_json(self.package / 'artifacts/receipt.json')
        receipt['stages']['qc']['observed_by'] = {'run_id': row['run_id'],
            'task_id': str(uuid.uuid4()), 'event_id': [row['run_id'], 999]}
        self.rewrite('artifacts/receipt.json', receipt)
        self.rehash('artifacts/receipt.json')
        self.assertEqual(self.verify()['execution_linkage'], 'incomplete')

    def test_reserved_input_rejected(self):
        timeline = a.read_json(self.timeline)
        timeline['project']['cache_dependencies'] = [links.DIRECTORY + '/request.json']
        self.rewrite_source(timeline)
        with self.assertRaises(ValueError):
            self.build()

    def rewrite_source(self, data):
        self.timeline.write_text(json.dumps(data))


if __name__ == '__main__':
    unittest.main()
