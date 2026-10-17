"""Unit edges plus a real opt-in CPU fixture/control matrix, without browser work."""
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import tempfile
import unittest

from scripts import cpu_visual_fixture as f

try:
    import numpy as np
    import PIL
except ImportError:
    np = None


@unittest.skipUnless(np is not None, 'fixture test dependencies numpy/Pillow required')
class OracleUnits(unittest.TestCase):
    def probe(self):
        return {'streams': [{'width': 640, 'height': 360, 'avg_frame_rate': '30/1',
            'time_base': '1/15360', 'duration_ts': 92160}],
            'frames': [{'pts': n * 512} for n in range(180)]}

    def test_frozen_threshold_edges_and_nonfinite(self):
        self.assertTrue(f.metrics_pass(2, 12, math.nextafter(1, 0)))
        for values in ((math.nextafter(2, math.inf), 12, 0),
                       (2, math.nextafter(12, math.inf), 0), (2, 12, 1),
                       (math.nan, 0, 0), (0, math.inf, 0), (0, 0, math.nan)):
            with self.subTest(values=values):
                self.assertFalse(f.metrics_pass(*values))

    def test_sliding_block_checks_unaligned_damage_and_edges(self):
        ref = np.zeros((360, 640, 3), np.uint8)
        actual = ref.copy()
        actual[101:121, 301:321] = 12
        mae, local = f.pixel_metrics(ref, actual)
        self.assertLess(mae, 2)
        self.assertEqual(local, 12)
        self.assertTrue(f.metrics_pass(mae, local, 0))
        actual[110, 310, 0] = 13
        mae, local = f.pixel_metrics(ref, actual)
        self.assertGreater(local, 12)
        self.assertFalse(f.metrics_pass(mae, local, 0))
        actual[:] = 2
        self.assertEqual(f.pixel_metrics(ref, actual), (2, 2))

    def test_rational_pts_and_count_not_rounded_timestamp(self):
        report = f.timing_report(self.probe(), 180)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['pts_seconds_rational'][1], '1/30')
        for change in ('one_tick', 'repeat', 'missing', 'extra', 'rate', 'dimensions', 'duration'):
            probe = self.probe()
            if change == 'one_tick':
                probe['frames'][89]['pts'] += 1
            elif change == 'repeat':
                probe['frames'][90]['pts'] = probe['frames'][89]['pts']
            elif change == 'missing':
                del probe['frames'][90]['pts']
            elif change == 'extra':
                probe['frames'].append({'pts': 180 * 512})
            elif change == 'rate':
                probe['streams'][0]['avg_frame_rate'] = '30000/1001'
            elif change == 'dimensions':
                probe['streams'][0]['width'] = 360
            else:
                probe['streams'][0]['duration_ts'] -= 1
            with self.subTest(change=change):
                self.assertEqual(f.timing_report(probe, 180)['status'], 'failed')
        self.assertEqual(f.timing_report(self.probe(), 179)['status'], 'failed')
        self.assertEqual(f.timing_report(self.probe(), 181)['status'], 'failed')

    def test_analytical_geometry_not_generator_self_comparison(self):
        # Independently assembled pixels, without scene() or Pillow drawing.
        frame = np.full((360, 640, 3), (28, 44, 60), np.uint8)
        frame[130:181, 60:111] = (255, 150, 60)
        yy, xx = np.mgrid[:360, :640]
        frame[(xx - 540)**2 + (yy - 275)**2 <= 25**2] = (50, 210, 220)
        self.assertTrue(f.frame_report(frame, frame, 0)['passed'])
        shifted = frame.copy()
        shifted[120:190] = np.roll(shifted[120:190], 1, axis=1)
        row = f.frame_report(frame, shifted, 0)
        self.assertEqual(row['centroid_error_px'], 1)
        self.assertFalse(row['passed'])
        empty = np.full_like(frame, 28)
        self.assertFalse(f.frame_report(empty, empty, 0)['passed'])
        wrong_background = frame.copy()
        wrong_background[100:320, :20] = (90, 90, 90)
        self.assertFalse(f.frame_report(wrong_background, wrong_background, 0)['passed'])

    def test_counter_order_repeat_and_static_background(self):
        a, b = f.scene(89), f.scene(90)
        self.assertTrue(f.frame_report(a, a, 89)['passed'])
        self.assertFalse(f.frame_report(b, a, 90)['passed'])
        unchanged = b.copy()
        unchanged[100:320, :20] = a[100:320, :20]
        self.assertTrue(f.frame_report(b, unchanged, 90)['passed'])
        wrong_counter = b.copy()
        wrong_counter[82:92, 24:120] = a[82:92, 24:120]
        self.assertFalse(f.frame_report(b, wrong_counter, 90)['passed'])

    def test_output_exclusive_and_outside_source(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(FileExistsError):
                f.run_fixture(temp)
        with self.assertRaisesRegex(ValueError, 'outside'):
            f.run_fixture(f.ROOT / 'unwanted-fixture-output')


@unittest.skipUnless(os.environ.get('VTX_TEST_FFMPEG'), 'set VTX_TEST_FFMPEG for real CPU matrix')
class RealCPUFixture(unittest.TestCase):
    def test_actual_positive_and_encoded_corruption_matrix(self):
        # mkdtemp is intentionally retained on failure and success. CI can keep
        # the root as an artifact; cleanup never touches another test's files.
        parent = Path(os.environ.get('VTX_CPU_FIXTURE_OUTPUT', tempfile.gettempdir()))
        retained = Path(tempfile.mkdtemp(prefix='vtx-cpu-visual-', dir=parent))
        print('CPU visual evidence retained at %s' % retained, flush=True)
        report = f.run_fixture(retained / 'run')
        self.assertEqual(report['status'], 'passed', str(retained) + ': ' + report.get('error', ''))
        self.assertEqual(len(report['cases']), 7)
        self.assertTrue(all(row['passed'] for row in report['cases']))
        self.assertEqual(report['cases'][0]['case'], 'static_background_repeat')
        self.assertTrue(report['cases'][0]['unchanged_positive_decode'])
        self.assertTrue(all(row['lossless_roundtrip'] for row in report['cases']))
        self.assertEqual(report['generic_visual_content'], 'unknown')
        positive = json.loads((retained / 'run/positive-oracle.json').read_text())
        self.assertEqual(len(positive['per_frame']), 180)
        self.assertEqual(positive['timing']['pts_seconds_rational'][-1], str(Fraction(179, 30)))
        package = json.loads((retained / 'run/package-verification.json').read_text())
        self.assertFalse(package['accepted'])
        self.assertEqual(package['gates']['visual_content']['status'], 'unknown')
        for name in ('permutation', 'repeat_moving', 'drop', 'inversion', 'black_insert', 'local_corruption'):
            control = json.loads((retained / ('run/' + name + '-oracle.json')).read_text())
            self.assertEqual(control['status'], 'failed')
        local = json.loads((retained / 'run/local_corruption-oracle.json').read_text())['per_frame'][90]
        self.assertLessEqual(local['rgb_mae_255'], 2)
        self.assertGreater(local['max_sliding_20x20_mae_255'], 12)


if __name__ == '__main__':
    unittest.main()
