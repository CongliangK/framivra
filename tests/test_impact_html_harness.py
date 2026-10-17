"""Cheap fixture contract checks; these do NOT establish browser acceptance."""
import tempfile
import unittest
from pathlib import Path
from tests.run_impact_html_reconciliation import fixture, middle_edit
from vtx.impact import capture, compare


class HtmlHarnessTests(unittest.TestCase):
    def test_consumed_dependencies_and_effective_restored_mtime_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tl = fixture(root)
            self.assertEqual(tl['project']['fps'], 30.0)
            self.assertEqual(tl['project']['width'], 1920)
            self.assertEqual(tl['project']['height'], 1080)
            self.assertEqual(len(tl['video_tracks'][0]['clips']), 30)
            for i in range(30):
                html = (root / f'page{i}.html').read_text()
                self.assertIn('href="global.css"', html)
                self.assertEqual('href="local.css"' in html, i == 7)
            before = capture(tl, root, nseg=8)
            middle_edit(root / 'local.css', b'#668822', b'#882266')
            after = capture(tl, root, nseg=8)
            local = compare(before, after)
            self.assertEqual(len([p for p in local['new_artifacts'] if p.startswith('cache/gfx/')]), 1)
            middle_edit(root / 'global.css', b'#224466', b'#664422')
            shared = compare(after, capture(tl, root, nseg=8))
            self.assertEqual(len([p for p in shared['new_artifacts'] if p.startswith('cache/gfx/')]), 30)


class ProfileContracts(unittest.TestCase):
    def test_both_profiles_shape_segments_and_exact_mutations(self):
        from tests.run_impact_html_reconciliation import profile
        for frames, total, subtitle in ((48, 1440, [341, 357]), (90, 2700, [640, 670])):
            with self.subTest(frames=frames), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                shape = profile(frames)
                tl = fixture(root, frames)
                self.assertEqual(shape, dict(frames_per_page=frames, pages=30,
                    total_frames=total, static_copied=frames - 1, subtitle=subtitle))
                self.assertEqual(tl['project']['duration_frames'], total)
                clips = tl['video_tracks'][0]['clips']
                self.assertEqual(len(clips), 30)
                self.assertEqual([(c['dst_in'], c['dur']) for c in clips],
                                 [(i * frames, frames) for i in range(30)])
                for i in range(30):
                    self.assertIn('window.__vtx_static_spans=[[0,%d]]' % frames,
                                  (root / ('page%d.html' % i)).read_text())
                self.assertEqual([tl['subtitles'][0][k] for k in ('in', 'out')], subtitle)
                before = capture(tl, root, nseg=8)
                for prefix in ('vseg:', 'qc:vseg:'):
                    ranges = [n['inputs']['range'] for k, n in before['nodes'].items()
                              if k.startswith(prefix)]
                    self.assertEqual(len(ranges), 8)
                    self.assertEqual(ranges[0][0], 0)
                    self.assertEqual(ranges[-1][1], total)
                    self.assertEqual([r[1] for r in ranges[:-1]], [r[0] for r in ranges[1:]])
                    touched = [r for r in ranges if r[0] < 8 * frames and r[1] > 7 * frames]
                    self.assertEqual(len(touched), 2)
                    self.assertLessEqual(subtitle[1], touched[0][1])
                for kind in ('page', 'local', 'global'):
                    if kind == 'page':
                        path = root / 'page7.html'
                        path.write_text(path.read_text().replace('#aa2244', '#22aa44'))
                    elif kind == 'local':
                        middle_edit(root / 'local.css', b'#668822', b'#882266')
                    else:
                        middle_edit(root / 'global.css', b'#224466', b'#664422')
                    after = capture(tl, root, nseg=8)
                    prediction = compare(before, after)
                    paths = prediction['new_artifacts']
                    counts = [sum(p.startswith('cache/' + name + '/') for p in paths)
                              for name in ('gfx', 'vseg', 'vseg_qc', 'vseg_pass')]
                    self.assertEqual(counts, [30, 8, 8, 58] if kind == 'global' else [1, 2, 2, 8])
                    before = after

    def test_partial_inventory_bounded_and_no_symlink_follow(self):
        from tests.run_impact_html_reconciliation import partial_inventory, profile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'cache/vseg').mkdir(parents=True)
            for i in range(5):
                (root / ('cache/vseg/%d.mp4' % i)).write_bytes(b'abc')
            (root / 'cache/vseg/link').symlink_to(root / 'cache/vseg/0.mp4')
            result = partial_inventory(root, max_entries=2)
            self.assertTrue(result['partial'])
            self.assertTrue(result['truncated'])
            self.assertEqual(result['scanned_entries'], 2)
            self.assertLessEqual(len(result['entries']), 2)
            result = partial_inventory(root)
            self.assertEqual(len(result['entries']), 5)
            self.assertTrue(all(item['size'] == 3 for item in result['entries']))
            self.assertEqual(partial_inventory(root, seconds=0)['entries'], [])
        with self.assertRaises(ValueError):
            profile(41)

    def test_timeout_retains_failure_and_partial_inventory(self):
        import json
        import subprocess
        from unittest.mock import Mock, patch
        from tests import run_impact_html_reconciliation as harness
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'run'
            proc = Mock(pid=12345)
            proc.wait.side_effect = [subprocess.TimeoutExpired('fake', 1), 0]
            with patch('sys.argv', ['harness', '--output', str(output), '--frames-per-page', '48']), \
                 patch.object(harness.shutil, 'which', return_value='/fake/chrome'), \
                 patch.object(harness.subprocess, 'Popen', return_value=proc), \
                 patch.object(harness.os, 'killpg') as kill:
                with self.assertRaises(subprocess.TimeoutExpired):
                    harness.main()
            report = json.loads((output / 'evidence/report.json').read_text())
            self.assertFalse(report['passed'])
            self.assertEqual(report['profile']['total_frames'], 1440)
            self.assertTrue(report['commands'][0]['timed_out'])
            self.assertGreaterEqual(report['commands'][0]['seconds'], 0)
            self.assertTrue(report['failure_partial_inventory']['partial'])
            kill.assert_called_once_with(12345, harness.signal.SIGKILL)

    def test_workflow_explicit_profile_and_unchanged_budgets(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / '.github/workflows/impact-html-acceptance.yml').read_text()
        source = (root / 'tests/run_impact_html_reconciliation.py').read_text()
        self.assertIn('--frames-per-page 48', workflow)
        self.assertIn('timeout-minutes: 65', workflow)
        self.assertIn('time.monotonic() + 3000', source)
        self.assertIn('def run(argv, label, limit=900)', source)
        self.assertIn("'--events-jsonl'", source)
        self.assertIn('tests/run_impact_reconciliation.py', workflow)


if __name__ == '__main__':
    unittest.main()
