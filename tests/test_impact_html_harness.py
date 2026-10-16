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


if __name__ == '__main__':
    unittest.main()
