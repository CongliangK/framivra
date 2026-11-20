"""Private operator-run sequencing and checked PCM, without starting media tools."""
from contextlib import ExitStack
from copy import copy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

from vtx import timeline_preview_window as window
from vtx.cache import cache_state, recovery
from vtx.pipeline import _window_seg_relay, composite, infra, stages
from vtx.pipeline import preview_program_audio as program, range as range_pipeline
from vtx.render import frame_environment, gfx_environment
from vtx.render.png_artifact import project_identity
from vtx.runtime import fstxn, preview_window_epoch


def _wav(path, samples):
    with wave.open(str(path), 'wb') as wav:
        wav.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
        wav.writeframes(samples)
    return path.read_bytes()


@unittest.skipUnless(sys.platform == 'linux' and fstxn.directory_write_capability(),
                     'requires the Linux directory-anchored preview worker profile')
class PreviewProgramRunTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.scratch = self.root / 'cache' / 'preview'
        self.scratch.mkdir(parents=True)
        self.path = self.root / 'timeline.json'
        self.audio = self.root / 'original.wav'
        self.off_window = self.root / 'unused.mp4'
        self.off_window.write_bytes(b'untouched')
        self.stored = {'version': 2, 'project': {'fps': 30, 'width': 1920, 'height': 1080, 'duration_frames': 100},
                       'sources': [{'id': 'music', 'path': self.audio.name},
                                   {'id': 'unused', 'path': self.off_window.name}],
                       'video_tracks': [], 'subtitles': [], 'audio_tracks': [{'name': 'music', 'events': [
                           {'src': 'music', 'in': 0, 'out': 1, 'volume': 1}]}]}
        raw = json.dumps(self.stored)
        self.path.write_text(raw, encoding='utf-8')
        self.version = 'sha256:' + hashlib.sha256(raw.encode()).hexdigest()
        self.ref = {'document': {'raw': raw}, 'content_version': self.version}
        data = _wav(self.audio, b'\x00' * 8)
        self.manifest = program.manifest((self.audio,), {str(self.audio): (len(data), hashlib.sha256(data).hexdigest())})
        self.pin = '0' * 64

    def test_pcm_uses_one_exactly_checked_mix_and_independent_absolute_samples(self):
        first, second = (self.scratch / name for name in ('first', 'second'))
        first.mkdir()
        second.mkdir()
        source_sample = struct.pack('<hh', 1, -1)
        samples = [struct.pack('<hh', v, -v) * 48000 for v in (1, 2, 3)]
        # These mocks replace only the native processes. The strict source and
        # whole checks, producer marker match, filesystem and cuts are real.
        extracted = first / 'prepared.wav'
        observed_targets = []

        def extract(argv):
            _wav(Path(argv[-1]), source_sample * 2)

        def mix(_, whole):
            data = _wav(whole, b''.join(samples))
            recovery.completion_marker(whole).write_text(json.dumps(
                {'schema': 1, 'sha256': hashlib.sha256(data).hexdigest(), 'frames': None}))

        c = SimpleNamespace(root=self.root, dur=90, frame_rate=Fraction(30), run=extract,
                            _preview_media_target=observed_targets.append)
        pcm = program._RunPCM()
        cut1, cut2 = first / 'cut.wav', second / 'cut.wav'
        with patch.object(stages, 'source_audio_extraction', return_value=(extracted, ['extract', str(extracted)])) as prepare, \
                patch.object(stages, 'mix_audio_wav', side_effect=mix) as mixed:
            self.assertTrue(pcm.render(c, cut1, 30, 60, {'music': (self.audio, 2)}, lambda: None))
            self.assertFalse((first / 'program-audio.wav').exists())
            # Subsequent windows need neither the first workspace nor its prepared audio.
            extracted.unlink()
            self.assertFalse(pcm.render(c, cut2, 60, 90, {'music': (self.audio, 2)}, lambda: None))
        prepare.assert_called_once_with(c, 'music')
        mixed.assert_called_once()
        self.assertEqual(observed_targets, [cut1, cut2])
        for path, expected in zip((cut1, cut2), samples[1:]):
            with wave.open(str(path), 'rb') as wav:
                self.assertEqual(wav.getnframes(), 48000)
                self.assertEqual(wav.readframes(48000), expected)

    def test_preencode_relay_requires_the_trusted_commit_and_keeps_fresh_targets(self):
        def clip(source, start, frames):
            return {'src': source, 'dst_in': start, 'src_in': 0, 'src_out': frames, 'dur': frames}

        cases = {'reuse': [0], 'marker_and_return': [1], 'oversized': [0, 1, 2],
                 'switch_source': [0, 2], 'eligibility': [0], 'occupied': [0], 'budget': [0]}
        for case, expected in cases.items():
            with self.subTest(case=case), ExitStack() as stack:
                base = self.scratch / case
                base.mkdir(mode=0o700)
                clips = [clip('unused', 0, 90)] if case != 'switch_source' else [
                    clip('unused', 0, 60), clip('another', 60, 30)]
                another = base / 'another.mp4'
                another.write_bytes(b'another source')
                sources = {'unused': self.off_window, 'another': another}
                payload = b'x' * (256 * 1024 + 1) if case == 'oversized' else b'preencoded'
                codec = 'h264'
                encoded, artifacts = [], []
                frames = 0

                def init(ctx, _):
                    ctx.root, ctx.dry, ctx.frame_rate, ctx.fps = self.root, False, Fraction(30), 30
                    ctx.w, ctx.h, ctx.input_fingerprint = 1920, 1080, self.pin
                    ctx.tl = {'video_tracks': [{'kind': 'video', 'clips': clips}]}
                    ctx.src_path = sources.__getitem__
                    ctx.crop169 = lambda _: 'crop=1920:1080'
                    ctx.probe = lambda _: {'codec_name': codec, 'has_alpha': False}

                def process(argv, _check, **_):
                    nonlocal frames
                    if '-frames:v' in argv:
                        encoded.append(index)
                        self.assertIn('libx264', argv)
                        self.assertIn('-format_whitelist', argv)
                        frames = int(argv[argv.index('-frames:v') + 1])
                        Path(argv[-1]).write_bytes(payload)
                    return SimpleNamespace(returncode=0, stderr='', stdout=str(frames))

                def return_only(stage, tasks, _):
                    [(_, argv, _)] = tasks
                    path = Path(argv[-1])
                    fstxn.atomic_write(path, payload, root=stage.root)
                    fstxn.atomic_write(recovery.completion_marker(path), json.dumps(
                        {'schema': 1, 'frames': 90, 'sha256': hashlib.sha256(payload).hexdigest()},
                        sort_keys=True), root=stage.root)
                    return SimpleNamespace(returncode=0)

                stack.enter_context(patch.object(infra.Ctx, '__init__', init))
                stack.enter_context(patch.object(cache_state, 'verify_input_identity', lambda _: None))
                stack.enter_context(patch.object(window, '_process', process))
                stack.enter_context(patch.object(composite, 'seg_encoder_kind', return_value='x264'))
                if case == 'budget':
                    stack.enter_context(patch.object(window, '_WORK_FILE_COUNT', 2))
                c = window._context(self.path, lambda: None, {}, lambda: None,
                                    epoch_sources={str(path) for path in sources.values()})
                relay = c._program_seg_relay = _window_seg_relay._RunSegmentRelay(c, lambda: None)
                for index in range(3):
                    workspace = base / f'render-{index}'
                    workspace.mkdir(mode=0o700)
                    ctx = copy(c)
                    ctx.write_root, ctx.cache = workspace, workspace / 'work'
                    ctx.range_window = (index * 30, (index + 1) * 30)
                    following = (ctx.range_window[1], ctx.range_window[1] + 30) if index < 2 else None
                    relay.begin_window(workspace, fstxn.cleanup_identity(workspace), ctx.range_window, following)
                    runner = return_only if case == 'marker_and_return' and index == 0 else ctx._preview_window_tasks
                    if index == 1 and case == 'eligibility':
                        codec = 'mpeg4'  # same key/argv, but no longer an approved source codec
                    if index == 1 and case == 'occupied':
                        target = ctx.cache / 'seg' / artifacts[0][0].name
                        fstxn.atomic_write(recovery.completion_marker(target), artifacts[0][3], root=ctx.root)
                    if index == 1 and case in ('eligibility', 'occupied') or index == 2 and case == 'budget':
                        with self.assertRaisesRegex(ValueError, {'eligibility': 'eligibility',
                                                                  'occupied': 'already exists',
                                                                  'budget': 'too many'}[case]):
                            composite.prepare_layers(ctx, run_tasks=runner, native_window=ctx.range_window)
                        break
                    [layer] = composite.prepare_layers(ctx, run_tasks=runner, native_window=ctx.range_window)
                    path = layer['seg']
                    marker = recovery.completion_marker(path)
                    artifacts.append((path, path.stat().st_ino, marker.stat().st_ino, marker.read_bytes()))
                    self.assertEqual(path.read_bytes(), payload)
                    relay.settle_window()
                self.assertEqual(encoded, expected)
                inodes = [inode for _, media, marker, _ in artifacts for inode in (media, marker)]
                self.assertEqual(len(set(inodes)), 2 * len(artifacts))
                if case in ('reuse', 'marker_and_return'):
                    self.assertEqual(len({record[3] for record in artifacts}), 1)

    def test_direct_mux_uses_original_graph_and_checked_current_wav_only_for_eligible_private_run(self):
        clip = {'src': 'movie', 'dst_in': 0, 'dur': 180}
        subtitles = [{'in': 29, 'out': 38, 'text': 'retained segment caption'}]
        all_samples = b''.join(struct.pack('<hh', v, -v) * 48000 for v in range(1, 7))
        whole = _wav(self.audio, all_samples)
        # Model the later window of a run. The mix certificate and retention
        # are exercised separately; use the real RunPCM and absolute cut here.
        pcm = program._RunPCM()
        pcm._data = program._checked_wav(self.audio, 288000)

        def render(name, *, run=pcm, suffix='.mp4', cuts=(), with_marker=True, frames=30, part_seconds=None):
            workspace = self.scratch / name
            commands, transitions = [], []
            layer = {'cl': clip, 'gfx': False, 'seg': self.root / ('opaque' + suffix),
                     'x': 0, 'y': 0, 'w2': None, 'h2': None}

            def command(argv):
                if argv[0] == 'ffprobe':
                    return SimpleNamespace(stdout=str(frames))
                commands.append(argv)
                target = Path(argv[-1])
                if '-c:a' in argv:
                    transitions.append('mux')
                    wav_input = next(Path(arg) for arg in argv if str(arg).endswith('.wav'))
                    with wave.open(str(wav_input), 'rb') as wav:
                        samples = frames * 1600
                        self.assertEqual(wav.getnframes(), samples)
                        self.assertEqual(wav.readframes(samples), all_samples[48000 * 4:(48000 + samples) * 4])
                target.write_bytes(b'private-media')
                if with_marker or target.parent != workspace:
                    encoded_frames = int(argv[argv.index('-frames:v') + 1]) if '-frames:v' in argv else frames
                    recovery.completion_marker(target).write_text(json.dumps({
                        'schema': 1, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'frames': encoded_frames}))

            context = SimpleNamespace(root=self.root, cache=workspace / 'work', preview_cache=workspace,
                                      dur=180, fps=30, frame_rate=Fraction(30), spf=1/30,
                                      w=1920, h=1080, dry=False, gpu=False, _program_audio_verified=True,
                                      tl={'video_tracks': [{'clips': [clip]}], 'subtitles': subtitles},
                                      run=command, verify_inputs=lambda _: transitions.append('input-check'),
                                      _preview_media_target=lambda _: transitions.append('wav'))
            with patch.object(range_pipeline, 'prepare_layers', return_value=[layer]), \
                    patch.object(range_pipeline, 'choose_cutpoints', return_value=list(cuts)), \
                    patch.object(range_pipeline, '_native_concat_clock', return_value=[]), \
                    patch.object(program, '_mix', return_value=whole):
                opts = dict(draft=False, staged_gfx={}, run_tasks=lambda *_: None, mux_concat=True,
                            program_sources={'music': (self.audio, 288000)}, program_pcm=run,
                            program_part_seconds=part_seconds)
                if with_marker:
                    range_pipeline._render_range(context, 30, 30 + frames, **opts)
                else:
                    with self.assertRaisesRegex(RuntimeError, 'valid media completion record'):
                        range_pipeline._render_range(context, 30, 30 + frames, **opts)
            return commands, transitions

        direct, transitions = render('direct')
        self.assertEqual(transitions, ['input-check', 'wav', 'mux', 'input-check'])
        self.assertEqual(len(direct), 1)
        self.assertFalse(list((self.scratch / 'direct').rglob('rseg_*')))
        original, _ = render('public-program', run=None)
        video, mux = original
        self.assertTrue(Path(video[-1]).name.startswith('rseg_'))
        self.assertIn('copy', mux)

        def normalize(argv, name):
            # Ignore only owned paths and the single output. ASS, source seek,
            # graph, CFR/cap and original x264 argv must otherwise match.
            return [arg.replace(str(self.scratch / name), '<workspace>') for arg in argv[:-1]]

        cmd = normalize(direct[0], 'direct')
        self.assertEqual(cmd[-8:], ['-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart',
                                    '-movie_timescale', '48000'])
        before_wav = cmd.index('-filter_complex_threads') - 2
        self.assertEqual(cmd[before_wav:before_wav + 2], ['-i', '<workspace>/work/preview/native_range_1.000_2.000.wav'])
        map_audio = cmd.index('2:a:0') - 1
        self.assertEqual(cmd[map_audio], '-map')
        self.assertEqual(cmd[:before_wav] + cmd[before_wav + 2:map_audio] + cmd[map_audio + 2:-8],
                         normalize(video, 'public-program'))
        for seconds in (1, 2, 4):
            with self.subTest(seconds=seconds):
                [command], events = render(f'span-{seconds}', frames=30 * seconds, part_seconds=seconds)
                self.assertEqual(command[command.index('-frames:v') + 1], str(30 * seconds))
                self.assertNotIn('-shortest', command)
                self.assertEqual(events, transitions)
        for name, options, rsegs in (('alpha', {'suffix': '.mov'}, 1), ('long-not-opted-in', {'frames': 60}, 1),
                                     ('multiple', {'cuts': (45,), 'frames': 120, 'part_seconds': 4}, 2)):
            with self.subTest(name=name):
                legacy, _ = render(name, **options)
                self.assertEqual(sum(Path(args[-1]).name.startswith('rseg_') for args in legacy), rsegs)
                self.assertIn('copy', legacy[-1])
        failed, _ = render('no-marker', with_marker=False)
        self.assertEqual(len(failed), 1)  # No retry/fallback on a rejected final product.

    def test_ack_precedes_next_production_and_epoch_or_revocation_vetoes_later_media(self):
        for failure in (None, 'revoked', 'epoch', 'media_limit', 'last_ack_input', 'last_ack_provider'):
            with self.subTest(failure=failure), ExitStack() as stack:
                timeline = []
                allowed = True
                provider_epoch = 'sha256:' + '3' * 64
                candidates = []
                context = SimpleNamespace(input_fingerprint=self.pin, timeline_sha256=self.version[7:],
                                          frame_rate=Fraction(30), fps=30, dur=100, w=1920, h=1080,
                                          src_path=lambda sid: self.audio)

                def produce(c, start, end, *, workspace, program_pcm, program_sources, **_):
                    self.assertIs(c, context)
                    self.assertEqual(program_sources, {'music': (self.audio, 2)})
                    self.assertFalse(any(workspace.iterdir()))
                    candidates.append((workspace, program_pcm))
                    timeline.append(('produce', start))
                    if failure == 'epoch' and start == 35:
                        self.off_window.write_bytes(b'rewritten')  # same length, not a referenced window asset
                    return workspace / 'window.mp4'

                def verify(output, workspace, request, identity, start, end, rate, check, *, limit):
                    self.assertEqual(limit, 30 - 10 * ((start - 5) // 30))
                    if failure == 'media_limit' and start == 35:
                        raise window.WindowError('output.limit', 'synthetic bounded reader refusal')
                    timeline.append(('verified_media', start))
                    return bytes(10), {'start': start, 'end': end}

                def witness(*_):
                    timeline.append(('environment', None))
                    return (provider_epoch, ())

                def emit(media):
                    nonlocal allowed, provider_epoch
                    start = media['start']
                    timeline.append(('emit', start))
                    index = (start - 5) // 30
                    work, _ = candidates[-1]
                    self.assertTrue(work.exists())
                    self.assertFalse((work.parent / f'render-{index + 1}').exists())
                    self.assertEqual(media['data'], bytes(10))
                    self.assertEqual(media['content_version'], self.version)
                    self.assertTrue(media['program_audio'])
                    if failure == 'revoked':
                        allowed = False
                    if index == 2:
                        if failure == 'last_ack_input':
                            self.off_window.write_bytes(b'rewritten')
                        elif failure == 'last_ack_provider':
                            provider_epoch = 'sha256:' + '4' * 64

                def check():
                    if not allowed:
                        raise window.WindowError('render.cancelled', 'test revocation')

                patches = ((window.isolation, 'check_mount', lambda *_: None),
                           (window, '_preflight', lambda _, start, *args, **kw: (start + 30, Fraction(30))),
                           (window, '_plugins', lambda _: None),
                           (window, '_context', lambda *args, **kw: context),
                           (window, '_verified_media', verify),
                           (cache_state, 'verify_input_identity', lambda _: None),
                           (program, 'probe', lambda *_: None),
                           (stages, 'validate_core', lambda _: []),
                           (range_pipeline, '_render_native_window', produce),
                           (frame_environment, 'collect', lambda *args, **kw: ()),
                           (gfx_environment, '_files', lambda _: ()),
                           (gfx_environment, '_libraries', lambda *args, **kw: ()),
                           (window, '_process', lambda *args, **kw: SimpleNamespace(returncode=0, stderr='', stdout='')),
                           (preview_window_epoch.EnvironmentWitness, 'observe', lambda *args: None),
                           (preview_window_epoch.EnvironmentWitness, 'snapshot', witness))
                for module, attr, side_effect in patches:
                    stack.enter_context(patch.object(module, attr, side_effect=side_effect))
                stack.enter_context(patch.object(window, 'MEDIA_LIMIT', 30))

                def run():
                    return window._render(self.path, self.ref, project_identity(self.root), 5, {'diagnostics': []},
                                          check, self.pin, epoch=True, program_manifest=self.manifest,
                                          operator_run=(3, emit))

                if failure is None:
                    self.assertIsNone(run())
                    self.assertEqual(timeline, [('environment', None)] + [event for start in (5, 35, 65)
                                     for event in (('environment', None), ('produce', start),
                                                   ('verified_media', start), ('environment', None), ('emit', start))]
                                     + [('environment', None)])
                    self.assertEqual(len({id(pcm) for _, pcm in candidates}), 1)
                else:
                    with self.assertRaises((window.WindowError, window.isolation.IsolationError)):
                        run()
                    if failure.startswith('last_ack_'):
                        self.assertEqual([start for event, start in timeline if event == 'emit'], [5, 35, 65])
                        self.assertEqual(timeline[-2:], [('emit', 65), ('environment', None)])
                    else:
                        self.assertEqual([start for event, start in timeline if event == 'emit'], [5])
                        self.assertEqual([start for event, start in timeline if event == 'produce'],
                                         [5] if failure == 'revoked' else [5, 35])
                self.assertFalse(any(self.scratch.iterdir()))
                self.off_window.write_bytes(b'untouched')

    def test_private_admission_requires_literal_permission_and_full_exact_windows(self):
        with patch.object(window.isolation, 'check_mount') as mount:
            for count, grant, code in ((4, True, 'render.arguments'), (1, 1, 'render.cancelled')):
                with self.assertRaises(window.WindowError) as caught:
                    window._render_program_run(self.path, start=5, count=count, expected_content_version=self.version,
                                               expected_inputs=self.pin, program_manifest=self.manifest,
                                               check_allowed=lambda: grant, emit=lambda _: None)
                self.assertEqual(caught.exception.code, code)
            mount.assert_not_called()
        for start, rate in ((11, Fraction(30)), (5, Fraction(30000, 1001))):
            with self.assertRaises(window.WindowError):
                window._program_windows(self.stored, start, rate, 3)

    def test_span_callback_binds_batch_and_vetoes_wrong_emission_or_revoked_ack(self):
        opts = dict(start=5, count=1, expected_content_version=self.version, expected_inputs=self.pin,
                    program_manifest=self.manifest)
        for seconds, count in ((True, 1), (2.0, 1), (3, 1), (2, True), (4, 0), (4, 4)):
            with self.subTest(seconds=seconds, count=count), patch.object(window.isolation, 'check_mount') as mount:
                with self.assertRaises(window.WindowError) as caught:
                    window._render_program_span_run(self.path, **dict(opts, count=count), part_seconds=seconds,
                                                    check_allowed=self.fail, emit=self.fail)
                self.assertEqual(caught.exception.code, 'render.arguments')
                mount.assert_not_called()
        for failure in (None, 'legacy-grant', 'truthy', 'wrong-part', 'revoked', 'missing-part'):
            with self.subTest(failure=failure):
                granted, checked, emitted = True, [], []

                def approve(*grant):
                    checked.append(grant)
                    return 1 if failure == 'truthy' else granted and grant == (
                        (5, 35, 1, 1) if failure == 'legacy-grant' else (5, 65, 2, 1))

                def emit(payload):
                    nonlocal granted
                    emitted.append((payload['start'], payload['end']))
                    granted = failure != 'revoked'

                def render(*_, operator_run, operator_part_seconds, **_kwargs):
                    self.assertEqual((operator_run[0], operator_part_seconds), (1, 2))
                    if failure != 'missing-part':
                        operator_run[1]({'start': 5, 'end': 35 if failure == 'wrong-part' else 65})

                with patch.object(window.isolation, 'check_mount'), patch.object(window, '_render', side_effect=render):
                    if failure is None:
                        self.assertIsNone(window._render_program_span_run(self.path, **opts, part_seconds=2,
                                                                          check_allowed=approve, emit=emit))
                    else:
                        with self.assertRaises(window.WindowError) as caught:
                            window._render_program_span_run(self.path, **opts, part_seconds=2,
                                                            check_allowed=approve, emit=emit)
                        self.assertEqual(caught.exception.code, 'media.invalid' if failure in (
                            'wrong-part', 'missing-part') else 'render.cancelled')
                self.assertTrue(checked)
                self.assertEqual(set(checked), {(5, 65, 2, 1)})
                self.assertEqual(emitted, [(5, 65)] if failure in (None, 'revoked') else [])

    def test_span_admits_complete_parts_before_any_staging_and_refuses_incomplete_tail(self):
        clips = [{'src': 'unused', 'dst_in': 110, 'src_in': 0, 'src_out': 12, 'dur': 12}]
        self.stored['video_tracks'] = [{'kind': 'screen', 'name': f'layer-{i}', 'clips': clips} for i in range(3)]
        opts = dict(start=5, count=2, part_seconds=2, expected_inputs=self.pin, program_manifest=self.manifest,
                    check_allowed=lambda *grant: grant == (5, 125, 2, 2), emit=self.fail)
        for duration, code, expected in ((125, 'work.limit', [(5, 65), (65, 125)]),
                                         (124, 'render.range', [])):
            self.stored['project']['duration_frames'] = duration
            raw = json.dumps(self.stored).encode()
            self.path.write_bytes(raw)
            with self.subTest(duration=duration), patch.dict(window.os.environ, {'VTX_VIDEO_SEGMENTS': '8'}), \
                    patch.object(window.isolation, 'check_mount'), patch.object(window, '_safe_environment'), \
                    patch.object(window, '_admit', wraps=window._admit) as admit, \
                    patch.object(program, 'stage') as stage:
                with self.assertRaises(window.WindowError) as caught:
                    window._render_program_span_run(self.path, **opts,
                                                    expected_content_version='sha256:' + hashlib.sha256(raw).hexdigest())
                self.assertEqual(caught.exception.code, code)
                self.assertEqual([call.args[1:] for call in admit.call_args_list], expected)
                stage.assert_not_called()
                self.assertFalse(list(self.scratch.iterdir()))

    def test_span_rejects_segment_setting_changed_by_authorizer_after_admission(self):
        admitted = []

        def admit(c, start, end, **kw):
            real_admit(c, start, end, **kw)
            admitted.append((start, end))

        def approve(*grant):
            self.assertEqual(grant, (5, 65, 2, 1))
            if admitted:
                window.os.environ['VTX_VIDEO_SEGMENTS'] = '8'
            return True

        real_admit = window._admit
        with patch.dict(window.os.environ, {'VTX_VIDEO_SEGMENTS': '1'}), \
                patch.object(window.isolation, 'check_mount'), patch.object(window, '_safe_environment'), \
                patch.object(window, '_admit', side_effect=admit), patch.object(program, 'stage') as stage:
            with self.assertRaises(window.WindowError) as caught:
                window._render_program_span_run(self.path, start=5, count=1, part_seconds=2,
                                                expected_content_version=self.version, expected_inputs=self.pin,
                                                program_manifest=self.manifest, check_allowed=approve, emit=self.fail)
            self.assertEqual(caught.exception.code, 'inputs.changed')
            self.assertEqual(admitted, [(5, 65)])
            stage.assert_not_called()
            self.assertFalse(list(self.scratch.iterdir()))

    def test_span_packet_clock_checks_b_frame_decode_order_and_each_aac_tail(self):
        for seconds, tail in ((1, 896), (2, 768), (4, 512)):
            with self.subTest(seconds=seconds):
                frames, samples = 30 * seconds, 48000 * seconds
                streams = [
                    {'index': 0, 'codec_type': 'video', 'codec_name': 'h264', 'width': 1920, 'height': 1080,
                     'time_base': '1/15360', 'start_pts': 0, 'duration_ts': frames * 512, 'avg_frame_rate': '30/1'},
                    {'index': 1, 'codec_type': 'audio', 'codec_name': 'aac', 'sample_rate': '48000', 'channels': 2,
                     'time_base': '1/48000', 'start_pts': 0, 'duration_ts': samples}]
                video = [{'stream_index': 0, 'pts': p * 512, 'dts': (i - 2) * 512, 'duration': 512}
                         for i, p in enumerate([0, 3, 1, 2, *range(4, frames)])]
                audio = [{'stream_index': 1, 'pts': n, 'dts': n, 'duration': min(1024, samples - n)}
                         for n in range(-1024, samples, 1024)]
                audio[0]['side_data_list'] = [{'side_data_type': 'Skip Samples', 'skip_samples': 1024}]
                self.assertEqual(audio[-1]['duration'], tail)
                info = {'streams': streams, 'packets': sorted(
                    video + audio, key=lambda p: int(p['dts']) * Fraction(streams[p['stream_index']]['time_base']))}
                window._probe_facts(info, Fraction(30), frames, samples, verify_dts=True)
                seam = video[frames // 2]
                for packet, field, value, code in ((seam, 'dts', seam['dts'] + 1, 'media.video_clock'),
                                                   (audio[-1], 'duration', 1024, 'media.audio_clock')):
                    original = packet[field]
                    packet[field] = value
                    with self.assertRaises(window.WindowError) as caught:
                        window._probe_facts(info, Fraction(30), frames, samples, verify_dts=True)
                    self.assertEqual(caught.exception.code, code)
                    packet[field] = original
