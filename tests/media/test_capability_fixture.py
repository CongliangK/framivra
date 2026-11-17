"""Capability inputs must survive the compositor's RGB domain, not bypass QC."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tests.runners._capability_fixture import create
from vtx.inspection.qc import (QC_LEAK_COVER_MAX, QC_LEAK_MEAN_MAX,
                               QC_PIXEL_DIFF, _QC_GRAY_FILTER)


FFMPEG = os.environ.get('VTX_TEST_FFMPEG') or shutil.which('ffmpeg')


class CapabilityFixtureCommands(unittest.TestCase):
    def test_normalizes_after_hue_before_encoding_without_changing_events(self):
        with tempfile.TemporaryDirectory() as temporary:
            commands = []
            timeline = json.loads(create(Path(temporary) / 'project', 'ffmpeg',
                                         commands.append).read_text())
            self.assertEqual(len(commands), 2)
            for command, hue in zip(commands, (25, 155)):
                video_input = command[command.index('-i') + 1]
                self.assertEqual(video_input,
                    f'testsrc2=s=1920x1080:r=30:d=3,hue=h={hue},format=rgba,format=yuv420p')
                self.assertEqual(command[command.index('-pix_fmt') + 1], 'yuv420p')
            self.assertEqual(timeline['project']['duration_frames'], 180)
            self.assertEqual([clip['dst_in'] for clip in timeline['video_tracks'][0]['clips']], [0, 90])
            self.assertEqual([(clip['dst_in'], clip['dur'])
                for clip in timeline['video_tracks'][1]['clips']], [(15, 60), (105, 60)])
            self.assertEqual(timeline['subtitles'][-1]['out'], 175)


@unittest.skipUnless(FFMPEG, 'requires CPU FFmpeg (VTX_TEST_FFMPEG or PATH)')
class CapabilityFixtureMedia(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='vtx_capability_color_')
        cls.addClassCleanup(temporary.cleanup)
        cls.project = Path(temporary.name) / 'project'
        create(cls.project, FFMPEG, cls.command)

    @staticmethod
    def command(args):
        result = subprocess.run(list(map(str, args)), capture_output=True, timeout=180)
        if result.returncode:
            raise AssertionError(result.stderr.decode('utf-8', errors='replace')[-4000:])
        return result

    def sample(self, source, prefix=''):
        result = self.command([FFMPEG, '-v', 'error', '-i', source, '-an', '-vf',
            prefix + _QC_GRAY_FILTER, '-f', 'rawvideo', '-'])
        size = 48 * 27
        self.assertEqual(len(result.stdout), 90 * size)
        return [result.stdout[n * size:(n + 1) * size] for n in range(90)]

    @staticmethod
    def difference(a, b):
        deltas = [abs(x - y) for x, y in zip(a, b)]
        return sum(deltas) / len(deltas), sum(d > QC_PIXEL_DIFF for d in deltas) / len(deltas)

    def test_both_encoded_sources_are_stable_across_rgba_roundtrip(self):
        # hue can rotate legal YUV into colors with negative / >255 RGB.
        # Comparing its original Y to the compositor's clipped RGB->Y falsely
        # reports an overlay. Check the actual generated/encoded sources, not
        # a hand-built gray surrogate or a weakened leak threshold.
        originals = []
        for name in ('left', 'right'):
            source = self.project / f'source-{name}.mp4'
            before = self.sample(source)
            after = self.sample(source, 'format=rgba,format=yuv420p,')
            for frame in range(90):
                with self.subTest(source=name, frame=frame):
                    mean, cover = self.difference(before[frame], after[frame])
                    self.assertLess(mean, QC_LEAK_MEAN_MAX)
                    self.assertLess(cover, QC_LEAK_COVER_MAX)
            originals.append(before[44])
        self.assertGreater(self.difference(*originals)[0], QC_LEAK_MEAN_MAX,
                           'the two ordinary-media inputs must remain distinct')

    def test_real_overlay_remains_detectable_with_normalized_sources(self):
        source = self.project / 'source-right.mp4'
        before = self.sample(source)
        # Independent known opaque rectangle, present even on the last frame.
        after = self.sample(source, 'format=rgba,drawbox=x=420:y=240:w=1000:h=480:'
                                    'color=white:t=fill,format=yuv420p,')
        for frame in (0, 44, 89):
            with self.subTest(frame=frame):
                mean, cover = self.difference(before[frame], after[frame])
                self.assertGreater(mean, QC_LEAK_MEAN_MAX)
                self.assertGreater(cover, QC_LEAK_COVER_MAX)


if __name__ == '__main__':
    unittest.main()
