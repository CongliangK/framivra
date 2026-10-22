"""Regressions from the independent Issue 52 review, without heavy renderers."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

from vtx.inspection.impact import capture, compare


class ImpactRepairRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = Path(__file__).resolve().parents[2]
        for name in ('page.html', 'dep'):
            (self.root / name).write_text(name)
        self.tl = dict(version=2, project=dict(fps=30, width=64, height=64,
            duration_frames=270), sources=[], audio_tracks=[], subtitles=[],
            video_tracks=[dict(kind='gfx', name='pages', clips=[
                dict(src='page.html', dst_in=i * 90, dur=90, cache_dependencies=['dep'])
                for i in range(3)])])
        self.path = self.root / 'timeline.json'

    def run_cli(self, *args):
        self.path.write_text(json.dumps(self.tl))
        return subprocess.run([sys.executable, str(self.repo / 'vtx/vtx.py'), 'diff', *args],
            capture_output=True, text=True, timeout=10,
            env=dict(os.environ, VTX_SEG_NVENC='0'))

    def test_regular_file_hash_preserves_all_binary_bytes(self):
        import hashlib
        from vtx.cache.cache_state import file_digest
        data = b'line\r\n\x1a\0' + bytes(range(256)) * 8193
        path = self.root / 'binary'; path.write_bytes(data)
        self.assertEqual(file_digest(path), hashlib.sha256(data).hexdigest())

    def test_numeric_fps_matches_ctx_and_invalid_fps_rejected(self):
        from vtx.cache.hygiene import gfx_key
        from vtx.infra import Ctx
        before = capture(self.tl, self.root, nseg=2)
        self.tl['project']['fps'] = 30.0
        self.path.write_text(json.dumps(self.tl))
        ctx = Ctx(self.path)
        after = capture(self.tl, self.root, nseg=2)
        self.assertEqual(before, after)
        self.assertEqual(after['nodes']['clip:0:0']['key'],
                         gfx_key(ctx, ctx.tl['video_tracks'][0]['clips'][0]))
        for fps in (True, '30', 29.97, 24, None):
            self.tl['project']['fps'] = fps
            with self.subTest(fps=fps), self.assertRaises(ValueError):
                capture(self.tl, self.root)

    @unittest.skipUnless(hasattr(os, 'symlink'), 'symlink support required')
    def test_symlink_loop_is_conservative_in_api_and_cli(self):
        (self.root / 'loop').symlink_to('loop')
        self.tl['video_tracks'][0]['clips'][0]['cache_dependencies'] = ['loop']
        snapshot = capture(self.tl, self.root, nseg=2)
        result = compare(snapshot, snapshot)
        self.assertIsNone(snapshot['nodes']['clip:0:0']['key'])
        self.assertIn('clip:0:0', result['conservative_nodes'])
        self.assertTrue(any(n.startswith('qc:vseg:') for n in result['conservative_nodes']))
        cli = self.run_cli('--capture-impact', str(self.path))
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertNotIn('Traceback', cli.stderr)
        self.assertIsNone(json.loads(cli.stdout)['nodes']['clip:0:0']['key'])

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'FIFO support required')
    def test_fifo_does_not_block_capture_cli(self):
        os.mkfifo(self.root / 'fifo')
        self.tl['video_tracks'][0]['clips'][0]['cache_dependencies'] = ['fifo']
        cli = self.run_cli('--capture-impact', str(self.path))
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertIsNone(json.loads(cli.stdout)['nodes']['clip:0:0']['key'])
        self.assertIn('not a regular file', cli.stdout)

    @unittest.skipUnless(hasattr(os, 'mkfifo') and hasattr(os, 'O_NONBLOCK'), 'nonblocking FIFO support required')
    def test_regular_to_fifo_swap_cannot_block_hash_open(self):
        script = '''
import os, sys
from pathlib import Path
from unittest.mock import patch
from vtx.cache.cache_state import file_digest
p = Path(sys.argv[1]); original = os.open

def swapped(path, flags):
    p.unlink(); os.mkfifo(p)
    return original(path, flags)

with patch('vtx.cache.cache_state.os.open', side_effect=swapped):
    try:
        file_digest(p)
    except ValueError as error:
        assert 'not a regular file' in str(error)
    else:
        raise AssertionError('FIFO accepted')
'''
        process = subprocess.run([sys.executable, '-c', script, str(self.root / 'dep')],
                                 cwd=self.repo, capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)

    def test_qc_graph_uses_bottom_only_no_subtitles_and_exact_pass_keys(self):
        from vtx.composite import pass_content_key, _graph_code_digest
        self.tl['video_tracks'].append(dict(kind='gfx', name='overlay', clips=[
            dict(src='page.html', dst_in=35, dur=130)]))
        before = capture(self.tl, self.root, nseg=2)
        qc = {k: v for k, v in before['nodes'].items() if k.startswith('qc:')}
        self.assertTrue(qc)
        for key, node in qc.items():
            self.assertTrue(all(parent.startswith('clip:0:') for parent in node['parents']))
            if key.startswith('qc:pass:'):
                inputs = node['inputs']; a, b = inputs['range']
                previous = f'{a}|{b}|64x64|0|0'
                for k, layer in enumerate(inputs['layers']):
                    previous = pass_content_key(previous, k, layer, _graph_code_digest())
                self.assertEqual(node['key'], previous)
            else:
                self.assertTrue(node['artifact'].startswith('cache/vseg_qc/'))
        self.tl['subtitles'] = [dict(**{'in': 10, 'out': 80}, text='changed')]
        self.tl['video_tracks'][1]['clips'][0]['dur'] = 131
        after = capture(self.tl, self.root, nseg=2)
        self.assertEqual(qc, {k: v for k, v in after['nodes'].items() if k.startswith('qc:')})
        self.assertTrue(compare(before, after)['affected_nodes'])

    def test_draft_namespace_and_no_qc_or_unsegmented_cache_claims(self):
        draft = capture(self.tl, self.root, nseg=2, draft=True)
        self.assertFalse(any(k.startswith('qc:') for k in draft['nodes']))
        self.assertTrue(all(v['artifact'].startswith('cache/vseg_draft/')
                            for k, v in draft['nodes'].items() if k.startswith('vseg:')))
        unsegmented = capture(self.tl, self.root, nseg=1)
        self.assertFalse(any(k.startswith(('vseg:', 'pass:', 'qc:')) for k in unsegmented['nodes']))

    def test_incomplete_v1_snapshots_rejected_by_api_and_cli(self):
        snapshot = capture(self.tl, self.root)
        old = copy.deepcopy(snapshot); old['schema'] = 'framivra-impact-snapshot-v1'
        with self.assertRaisesRegex(ValueError, 'unsupported impact snapshot'):
            compare(old, snapshot)
        path = self.root / 'old.json'; path.write_text(json.dumps(old))
        cli = self.run_cli(str(path), str(self.path))
        self.assertEqual(cli.returncode, 2, cli.stderr)
        self.assertIn('unsupported impact snapshot', cli.stderr)
        self.assertFalse((self.root / 'cache').exists())
        self.assertFalse((self.root / 'out').exists())
