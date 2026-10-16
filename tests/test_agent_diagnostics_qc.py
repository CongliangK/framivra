"""QC diagnostics preserve legacy results and report source-level evidence.

Fast mocked fixtures cover exact boundaries, missing evidence and exemptions.
The CPU integration fixture runs the real stages to establish receipts before
checking generated media and deliberately damaged final outputs. No Chrome,
GPU, external assets, network, or manufactured successful receipts are used.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from vtx import qc
from vtx.infra import Ctx

REPO = Path(__file__).resolve().parents[1]


class _FakeContext:
    def __init__(self, root, *, enabled=False, loud=-14, tp=-2, energy=-25,
                 covered=False, dry=False, strict=False, metadata=None):
        self.root = root
        self.out = root / 'out'; self.out.mkdir()
        self.cache = root / 'cache'; self.cache.mkdir()
        (self.out / 'final.mp4').write_bytes(b'mocked movie')
        (self.cache / 'vseg').mkdir()
        (self.cache / 'vseg/seg0_000000_aaaaaaaaaaaa.mp4').write_bytes(b'mocked segment')
        (self.cache / 'vseg/list.txt').write_text("file 'seg0_000000_aaaaaaaaaaaa.mp4'\n")
        self.fps = 30; self.spf = 1 / 30; self.dur = 24
        self.w = 1920; self.h = 1080
        self.strict = strict; self.dry = dry
        self.energy = energy; self.loud = loud; self.tp = tp
        self.metadata = metadata or {}
        self.tl = {
            'video_tracks': [
                {'name': 'bottom', 'kind': 'aroll', 'clips': [
                    {'src': 'b', 'src_in': 10, 'dst_in': 10, 'dur': 14},
                    {'src': 'a', 'src_in': 0, 'dst_in': 0, 'dur': 10}]},
                {'name': 'over', 'kind': 'aroll', 'clips': [
                    {'src': 'a', 'src_in': 0, 'dst_in': 2, 'dur': 2},
                    {'src': 'a', 'src_in': 0, 'dst_in': 5, 'dur': 2, 'scale': 0.5}]},
                {'name': 'gfx', 'kind': 'gfx', 'clips': [
                    {'src': 'gfx/page.html', 'dst_in': 10, 'dur': 4}]}],
            'subtitles': [{'in': 0, 'out': 24}] if covered else [],
            'audio_tracks': [{'name': 'voice', 'events': [{'src': 'a', 'in': 0, 'out': 24}]}]}
        self.receipts = []; self.commands = []
        if enabled:
            self.diagnostics = []

    def receipt(self, *args, **kwargs):
        self.receipts.append((args, kwargs))

    def src_path(self, src):
        return self.root / (src + '.mp4')

    def crop169(self, src):
        return 'crop=1920:1080'

    def probe(self, src):
        return {'w': 1920, 'h': 1080}

    def run(self, cmd, **kwargs):
        self.commands.append(tuple(cmd))
        if cmd[0] == 'ffprobe':
            data = {'streams': [{'nb_frames': str(self.dur), 'width': self.w, 'height': self.h}],
                    'format': {'duration': self.dur * self.spf}}
            if '-count_frames' in cmd:
                data = {'streams': [{'nb_read_frames': str(self.dur)}]}
            data.update(self.metadata)
            return types.SimpleNamespace(stdout=json.dumps(data), stderr='')
        if 'volumedetect' in cmd:
            return types.SimpleNamespace(stdout='', stderr=(
                f'mean_volume: {self.energy} dB' if self.energy is not None else ''))
        if 'rawvideo' in cmd:
            frame = round(float(cmd[cmd.index('-ss') + 1]) * self.fps) if '-ss' in cmd else 0
            source = cmd[cmd.index('-i') + 1]
            overlay = 2 <= frame < 4 or 5 <= frame < 7 or 10 <= frame <= 13
            intensity = 200 if source.endswith('final.mp4') and overlay else 100
            Path(cmd[-1]).write_bytes(bytes([intensity]) * 1296)
            return types.SimpleNamespace(stdout='', stderr='')
        raise AssertionError(cmd)


def _run_qc(context):
    output = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(output):
        try:
            qc.qc(context)
        except SystemExit as error:
            code = error.code
    report_path = context.out / 'qc_report.json'
    report = json.loads(report_path.read_text()) if report_path.exists() else None
    return code, output.getvalue(), report


class TestQCDiagnostics(unittest.TestCase):
    def context(self, **params):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return _FakeContext(Path(tmp.name), **params)

    def run_mocked(self, context):
        with mock.patch.object(qc.loudness_cache, 'lookup', return_value={
                'i_lufs': context.loud, 'tp_dbfs': context.tp}):
            return _run_qc(context)

    def test_opt_in_preserves_outputs_receipts_and_exit(self):
        cases = [({}, 0), ({'loud': -12}, 0), ({'loud': -10}, 1), ({'tp': 0}, 1),
                 ({'loud': None, 'tp': None, 'energy': None}, 1),
                 ({'covered': True}, 0), ({'dry': True}, 0), ({'strict': True}, 0),
                 ({'metadata': {'streams': [{'nb_frames': '23', 'width': 1280, 'height': 720}],
                                'format': {'duration': 0.9}}}, 1)]
        for params, expected_code in cases:
            with self.subTest(params=params):
                enabled = self.context(enabled=True, **params)
                disabled = self.context(**params)
                on = self.run_mocked(enabled); off = self.run_mocked(disabled)
                self.assertEqual(on, off)
                self.assertEqual(enabled.receipts, disabled.receipts)
                self.assertEqual(on[0], expected_code)
                self.assertTrue(enabled.diagnostics)
                for diagnostic in enabled.diagnostics:
                    self.assertNotEqual(diagnostic['rule_id'], 'unknown')
                    self.assertTrue(diagnostic['suggested_actions'])
                    self.assertIn('params', diagnostic['suggested_actions'][0])
                json.dumps(enabled.diagnostics, allow_nan=False)
                if params.get('dry'):
                    self.assertEqual(enabled.diagnostics[0]['status'], 'skipped')
                    self.assertEqual(enabled.commands, [])
                    self.assertIsNone(on[2])
                else:
                    self.assertEqual(len(on[2]['checks']), 11 if params.get('covered') else 12)
                    self.assertEqual(set(on[2]), {'checks', 'skipped_probes', 'quality'})
                    from vtx.quality import qc_quality
                    self.assertEqual(on[2]['quality'], qc_quality(on[2]))
                    self.assertEqual(enabled.receipts[-1][1],
                                     {'extra': {'skipped_probes': on[2]['skipped_probes']}})
                    self.assertTrue(all(set(check) == {'name', 'pass', 'detail'}
                                        for check in on[2]['checks']))

    def test_original_indices_values_and_thresholds(self):
        context = self.context(enabled=True)
        self.run_mocked(context)
        cuts = [d for d in context.diagnostics if d['rule_id'] == 'qc.cut_frame']
        self.assertEqual([d['json_path'] for d in cuts],
                         ['$.video_tracks[0].clips[1]', '$.video_tracks[0].clips[0]'])
        self.assertEqual(cuts[0]['measured']['source_frame'], {'value': 2, 'unit': 'frames'})
        self.assertEqual(cuts[0]['threshold']['mean_pixel_difference']['lt'], qc.QC_CUT_MEAN_MAX)
        window = next(d for d in context.diagnostics if d['rule_id'] == 'qc.clip_window')
        self.assertEqual(window['json_path'], '$.video_tracks[1].clips[1]')
        self.assertEqual(window['measured']['coverage']['value'], 1.0)
        self.assertEqual(window['threshold']['coverage']['gt'], qc.QC_WINDOW_COVER_MIN)

    def test_exemptions_are_skipped_and_do_not_probe(self):
        context = self.context(enabled=True)
        self.run_mocked(context)
        context.commands.clear(); context.diagnostics.clear()
        code, output, report = self.run_mocked(context)
        self.assertEqual(code, 0)
        skipped = [d for d in context.diagnostics if d['status'] == 'skipped']
        self.assertEqual(len(skipped), 6)
        self.assertEqual(len(report['skipped_probes']), 6)
        self.assertFalse(any('rawvideo' in cmd for cmd in context.commands))
        self.assertTrue(all(d['suggested_actions'][0] == {
            'op': 'run_qc', 'params': {'strict': True}} for d in skipped))

    def test_loudness_boundaries_keep_existing_warning_band(self):
        for loudness, expected in [(-14, 'pass'), (-12.5, 'pass'), (-12.49, 'warn'),
                                   (-11, 'warn'), (-10.99, 'fail')]:
            with self.subTest(loudness=loudness):
                context = self.context(enabled=True, loud=loudness)
                self.run_mocked(context)
                result = next(d for d in context.diagnostics if d['rule_id'] == 'qc.integrated_loudness')
                self.assertEqual(result['status'], expected)
                self.assertEqual(result['measured']['value'], loudness)

    def test_unavailable_evidence_is_unknown(self):
        context = self.context(enabled=True, loud=None, tp=None, energy=None, covered=True)
        self.run_mocked(context)
        unknown = {d['rule_id'] for d in context.diagnostics if d['status'] == 'unknown'}
        self.assertEqual(unknown, {'qc.integrated_loudness', 'qc.true_peak',
                                   'qc.stacked_audio_energy', 'qc.overlay_leak'})
        energy = next(d for d in context.diagnostics if d['rule_id'] == 'qc.stacked_audio_energy')
        self.assertIsNone(energy['measured']['mean_volume']['value'])


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'requires CPU FFmpeg tools')
class TestQCDiagnosticsCPU(unittest.TestCase):
    def command(self, args, **kwargs):
        result = subprocess.run(list(map(str, args)), capture_output=True, text=True,
                                timeout=180, **kwargs)
        self.assertEqual(result.returncode, 0, result.stdout[-5000:] + result.stderr[-5000:])
        return result

    def test_real_stages_then_frame_loudness_and_peak_failures(self):
        with tempfile.TemporaryDirectory(prefix='vtx_qc_diagnostics_') as tmp:
            root = Path(tmp)
            source = root / 'source.mp4'
            ffmpeg = shutil.which('ffmpeg')
            self.command([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                          'color=c=0x708090:s=320x180:r=30:d=2', '-f', 'lavfi', '-i',
                          'sine=frequency=1000:sample_rate=48000:duration=2',
                          '-c:v', 'libx264', '-threads', '1', '-preset', 'ultrafast',
                          '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-ac', '2', '-shortest', source])
            timeline = root / 'timeline.json'
            timeline.write_text(json.dumps({
                'version': 2,
                'project': {'fps': 30, 'width': 320, 'height': 180, 'duration_frames': 60},
                'sources': [{'id': 'source', 'path': 'source.mp4'}],
                'video_tracks': [{'name': 'base', 'kind': 'aroll', 'clips': [
                    {'src': 'source', 'src_in': 0, 'src_out': 60, 'dst_in': 0, 'dur': 60}]}],
                'subtitles': [],
                'audio_tracks': [{'name': 'tone', 'events': [
                    {'src': 'source', 'in': 0, 'out': 60, 'volume': 1.0}]}]}))
            env = dict(os.environ, VTX_PIPELINE='0', VTX_VIDEO_SEGMENTS='1', VTX_SEG_NVENC='0',
                       PYTHONDONTWRITEBYTECODE='1')
            self.command([sys.executable, REPO / 'vtx/vtx.py', '-t', timeline, 'all'], env=env, cwd=REPO)
            receipt = json.loads((root / 'out/receipt.json').read_text())
            for stage in ('validate', 'prepare', 'gfx', 'video', 'audio', 'mux', 'qc'):
                self.assertTrue(receipt['stages'][stage]['ok'], (stage, receipt))
            context = Ctx(timeline, diagnostics=[])
            context.strict = True
            code, _, report = _run_qc(context)
            self.assertEqual(code, 0)
            self.assertTrue(all(check['pass'] for check in report['checks']))
            by_rule = {d['rule_id']: d for d in context.diagnostics}
            self.assertEqual(by_rule['qc.frame_count']['measured']['value'], 60)
            self.assertEqual(by_rule['qc.frame_count']['measured']['method'], 'decoded_frame_count')
            self.assertEqual(by_rule['qc.integrated_loudness']['status'], 'pass')
            self.assertEqual(by_rule['qc.true_peak']['status'], 'pass')
            final = root / 'out/final.mp4'
            good = root / 'out/good.mp4'
            shutil.copyfile(final, good)
            # Keep later sample frames available while adding actual excess frames.
            # The source video ends at 2 s, so tpad duplicates its final picture.
            damaged = root / 'out/damaged.mp4'
            self.command([ffmpeg, '-v', 'error', '-y', '-i', good, '-vf',
                          'tpad=stop_mode=clone:stop_duration=0.2', '-c:v', 'libx264',
                          '-threads', '1', '-preset', 'ultrafast', '-c:a', 'copy', damaged])
            os.replace(damaged, final)
            context = Ctx(timeline, diagnostics=[]); context.strict = True
            code, _, _ = _run_qc(context)
            self.assertEqual(code, 1)
            count = next(d for d in context.diagnostics if d['rule_id'] == 'qc.frame_count')
            self.assertEqual(count['status'], 'fail')
            self.assertGreater(count['measured']['value'], 60)
            # Replace only the output audio with a near-full-scale tone. Actual
            # ebur128 measurements must fail both loudness and true peak gates.
            self.command([ffmpeg, '-v', 'error', '-y', '-i', good, '-f', 'lavfi', '-i',
                          'aevalsrc=0.99*sin(2*PI*1000*t)|0.99*sin(2*PI*1000*t):s=48000:d=2',
                          '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy',
                          '-c:a', 'aac', '-b:a', '192k', '-ac', '2', '-shortest', damaged])
            os.replace(damaged, final)
            context = Ctx(timeline, diagnostics=[]); context.strict = True
            code, _, report = _run_qc(context)
            self.assertEqual(code, 1)
            by_rule = {d['rule_id']: d for d in context.diagnostics}
            self.assertEqual(by_rule['qc.frame_count']['status'], 'pass')
            self.assertEqual(by_rule['qc.integrated_loudness']['status'], 'fail')
            self.assertEqual(by_rule['qc.true_peak']['status'], 'fail')
            self.assertGreater(by_rule['qc.true_peak']['measured']['value'], qc.QC_TP_MAX)
            self.assertFalse(json.loads((root / 'out/receipt.json').read_text())['stages']['qc']['ok'])


if __name__ == '__main__':
    unittest.main()
