"""tests/media/test_post_loudness.py —— v2 响度终链（§1.2 limiter 并入 vtx audio）单元测试。

v1 时代本文件经 spec_from_file_location 加载 scripts/post_loudness.py（独立进程
语义：测 I→增益+限幅→复测→回执摘除-回写手术）。v2（P1-1，2026-09-13）响度终链
并入内核：alimiter 下沉进 stages.mix_audio_wav 输出链、build_audio 尾部做终态
校验（复测 ±1.5 LU + 时长 ±1 采样 + 回执 post_loudness 字段）、
旧 scripts/post_loudness.py 入口现已退役。本文件直测仍保留的
内核函数（包导入，无 spec 加载——§1.4 / 验收门 1）：
  ① mix_audio_wav 输出链构造（mock c.run）：第二遍命令必含
     volume={gain}dB,aformat=sample_fmts=s16,alimiter=…（s16 量化中间步复刻 v1 域，P5 门 8）——win=None /
     gain_override / dry 三路径同样生效；第一遍测量链含 ebur128 且不含 alimiter；
  ② build_audio 终态校验（合成小工程真实 ffmpeg + 定点 mock）：正常路径回执
     stages.audio 附 post_loudness 字段（input_i=raw_i/output_i/gain_db/target/
     sha256）；复测超 ±1.5 LU → exit 1；时长漂移 >1 采样 → exit 1；--dry-run
     跳过校验与 sha256（无回执）；
  ③ normalize_project_audio（真实 ffmpeg）：两遍幂等（第二遍 gain≈0、output_i 稳定）、
     编码失败保留原文件、不写回执手术（receipt.json 字节不动）。
运行：env python3 -m unittest tests.media.test_post_loudness -v（本机 ZCode 环境必须
env python3 前缀：AppImage 劫持 argv[0]，裸 python3 的 sys.executable 指向错误
解释器，见全局 AGENTS.md）
"""
import hashlib, json, math, os, shutil, struct, sys, tempfile, unittest, wave
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from contextlib import contextmanager, redirect_stdout
import io

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))   # repo 根注入（v2 §1.4 包导入，不再注入 repo/vtx）

from vtx.pipeline import stages
from vtx.pipeline.stages import (TARGET_LUFS, mix_audio_wav, normalize_project_audio)

FFMPEG = os.environ.get('VTX_TEST_FFMPEG') or shutil.which('ffmpeg')
OUTPUT = None

ALIMITER = "alimiter=limit=0.599:level=false:latency=true"


def _mk_source(root: Path, frames_s: int = 3):
    """合成正弦 wav（48k mono s16，默认 3s）——audio_key 只 stat + 采样，内容随意。"""
    src = root / "a.wav"
    with wave.open(str(src), 'wb') as s:
        s.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
        s.writeframes(b''.join(
            struct.pack('<h', round(9000 * math.sin(2 * math.pi * 440 * i / 48000)))
            for i in range(48000 * frames_s)))
    return src


def _mk_timeline(root: Path, dur=90) -> Path:
    tl = {"version": 2,
          "project": {"fps": 30, "width": 640, "height": 360, "duration_frames": dur},
          "sources": [{"id": "a", "path": "a.wav"}],
          "video_tracks": [], "subtitles": [],
          "audio_tracks": [{"name": "voice",
                            "events": [{"src": "a", "in": 0, "out": dur, "volume": 0.9}]}]}
    p = root / "timeline.json"
    p.write_text(json.dumps(tl), encoding="utf-8")
    return p


class _FakeCtx:
    """mix_audio_wav 链构造测试的最小上下文：run 记录命令并按脚本回放 stderr。
    c.run 只会被 mix_audio_wav 调（测量一遍 + 输出一遍）。"""
    dry = False

    def __init__(self, root: Path, stderr_script=("-20.5 LUFS",)):
        self.root = root
        self.cache = root / "cache"
        self.out = root / "out"
        self.fps, self.spf, self.spf_samples, self.dur = 30, 1 / 30, 1600, 90
        self.tl = {"sources": [{"id": "a", "path": "a.wav"}],
                   "audio_tracks": [{"name": "voice",
                                     "events": [{"src": "a", "in": 0, "out": 90, "volume": 1.0}]}]}
        self.calls = []
        self._script = list(stderr_script)

    def src_path(self, sid):
        return self.root / next(s for s in self.tl["sources"] if s["id"] == sid)["path"]

    def run(self, cmd, need=True):
        self.calls.append(list(map(str, cmd)))
        stderr = self._script.pop(0) if self._script else ""
        return SimpleNamespace(returncode=0, stderr=stderr)


class TestMixAudioChain(unittest.TestCase):
    """① mix_audio_wav 输出链：volume 后必挂 alimiter（§1.2 施加位置）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='vtx-post-chain-')
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _mk_source(self.root)

    def _out_cmd(self, c):
        self.assertEqual(len(c.calls), 2, "两遍法：先测量后输出")
        meas, out = c.calls
        self.assertIn("ebur128", " ".join(meas))
        self.assertNotIn("alimiter", " ".join(meas))   # 测量链不限幅（测原始混音）
        return " ".join(out)

    def test_output_chain_has_alimiter_after_volume(self):
        c = _FakeCtx(self.root, stderr_script=("  I:          -20.5 LUFS",))
        gain, raw_i, n_ev, duck = mix_audio_wav(c, c.out / "audio.wav")
        self.assertEqual((raw_i, n_ev, duck), (-20.5, 1, False))
        self.assertAlmostEqual(gain, TARGET_LUFS - (-20.5))
        out = self._out_cmd(c)
        self.assertIn(f",volume={gain:+.2f}dB,aformat=sample_fmts=s16,{ALIMITER}[aout]", out)

    def test_gain_override_path_also_limited(self):
        c = _FakeCtx(self.root)
        gain, raw_i, _n, _d = mix_audio_wav(c, c.out / "audio.wav", gain_override=-3.25)
        self.assertEqual(len(c.calls), 1, "gain_override 跳过测量遍")
        self.assertIsNone(raw_i)
        self.assertEqual(gain, -3.25)
        self.assertIn(f",volume={-3.25:+.2f}dB,aformat=sample_fmts=s16,{ALIMITER}[aout]", " ".join(c.calls[0]))

    def test_dry_run_path_also_limited(self):
        c = _FakeCtx(self.root)
        c.dry = True
        mix_audio_wav(c, c.out / "audio.wav")
        self.assertEqual(len(c.calls), 1, "dry 跳过测量遍，只打印输出命令")
        self.assertIn(f",volume={0.0:+.2f}dB,aformat=sample_fmts=s16,{ALIMITER}[aout]", " ".join(c.calls[0]))


class TestAudioDigestStreaming(unittest.TestCase):
    """Real file/hash/transaction checks; mixing and measurements are stubbed."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='vtx-audio-digest-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.out = self.root / 'out'
        self.out.mkdir()
        self.source = self.out / 'audio.wav'
        self.frames, self.samples_per_frame = 180, 1600
        with wave.open(str(self.source), 'wb') as stream:
            stream.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
            stream.writeframes(b'\0' * self.frames * self.samples_per_frame * 4)
        self.expected = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.c = SimpleNamespace(
            out=self.out, dry=False, dur=self.frames, spf_samples=self.samples_per_frame,
            tl={'audio_tracks': []}, receipt=mock.Mock(),
            run=mock.Mock(return_value=self._measurement()))

    @staticmethod
    def _measurement():
        return SimpleNamespace(returncode=0, stderr='I: -14.0 LUFS\nPeak: -3.0 dBFS\n')

    def _run_normalization(self, command, **kwargs):
        if '-c:a' in command:
            shutil.copyfile(self.source, command[-1])
        return self._measurement()

    @contextmanager
    def _bounded_reads(self, fail_when=None):
        original_open = Path.open
        test, reads = self, []

        def open_file(path, mode='r', *args, **kwargs):
            stream = original_open(path, mode, *args, **kwargs)
            if mode != 'rb':
                return stream

            class Reader:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return stream.__exit__(*args)

                def read(self, size=-1):
                    test.assertGreater(size, 0, 'whole-file reads are not bounded')
                    test.assertLessEqual(size, 1024 * 1024)
                    reads.append((Path(path), size))
                    if fail_when is not None and fail_when(Path(path), reads):
                        raise OSError('injected read failure')
                    return stream.read(size)

            return Reader()

        with mock.patch.object(Path, 'open', open_file):
            yield reads

    def test_shared_digest_exact_bytes_and_chunk_boundaries(self):
        path = self.root / 'hash-input.bin'
        for size in (0, 1024 * 1024, 1024 * 1024 + 1):
            with self.subTest(size=size):
                content = b'x' * size
                path.write_bytes(content)
                with self._bounded_reads() as reads:
                    actual = stages.recovery._digest(path)
                self.assertEqual(actual, hashlib.sha256(content).hexdigest())
                self.assertEqual(len(reads), size // (1024 * 1024) + 1 + bool(size % (1024 * 1024)))

    def test_build_audio_hashes_in_chunks_and_dry_never_reads(self):
        with mock.patch.object(stages, 'mix_audio_wav', return_value=(0.0, -14.0, 0, False)), \
                self._bounded_reads() as reads, redirect_stdout(io.StringIO()):
            stages.build_audio(self.c)
        self.assertGreater(len(reads), 1)
        self.assertEqual(self.c.receipt.call_args.kwargs['extra']['post_loudness']['sha256'],
                         self.expected)
        self.c.dry = True
        with mock.patch.object(stages, 'mix_audio_wav', return_value=(0.0, None, 0, False)), \
                mock.patch.object(stages.recovery, '_digest', side_effect=AssertionError('dry hash')), \
                redirect_stdout(io.StringIO()):
            stages.build_audio(self.c)

    def test_terminal_receipt_hit_and_later_same_size_change(self):
        previous = {'sha256': self.expected, 'gain_db': 0.0, 'target': TARGET_LUFS}
        receipt = self.out / 'receipt.json'
        receipt.write_text(json.dumps({'stages': {'audio': {'post_loudness': previous}}}))
        before = receipt.read_bytes()
        with self._bounded_reads(), \
                mock.patch.object(stages.subprocess, 'run', side_effect=AssertionError('warm measurement')), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(normalize_project_audio(self.root), previous)
        with self.source.open('r+b') as stream:
            stream.seek(1024 * 1024 + 3)
            stream.write(b'changed')
        with self._bounded_reads(), \
                mock.patch.object(stages.subprocess, 'run', side_effect=RuntimeError('new measurement')), \
                redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'new measurement'):
            normalize_project_audio(self.root)
        self.assertEqual(receipt.read_bytes(), before)

    def test_terminal_receipt_requires_same_target_before_skipping(self):
        receipt = self.out / 'receipt.json'
        for name, saved_target, target, skip in (
                ('same', {'target': -14}, -14.0, True),
                ('different', {'target': -14}, -16.0, False),
                ('missing', {}, -14.0, False)):
            with self.subTest(case=name):
                previous = {'sha256': self.expected, 'gain_db': 0.0, **saved_target}
                receipt.write_text(json.dumps({'stages': {'audio': {'post_loudness': previous},
                                                        'mux': {'ok': True}, 'qc': {'ok': True}}}))
                before = self.source.read_bytes(), receipt.read_bytes()
                calls = []

                def run(command, **kwargs):
                    calls.append(command)
                    if '-c:a' in command:
                        shutil.copyfile(self.source, command[-1])
                    measured = -14 if len(calls) == 1 else target
                    return SimpleNamespace(returncode=0, stderr=f'I: {measured} LUFS\nPeak: -3.0 dBFS\n')

                with self._bounded_reads(), mock.patch.object(stages.subprocess, 'run', side_effect=run), \
                        redirect_stdout(io.StringIO()):
                    result = normalize_project_audio(self.root, target=target)
                self.assertEqual(len(calls), 0 if skip else 3)
                self.assertEqual(result['target'], target)
                if skip:
                    self.assertEqual(result, previous)
                else:
                    self.assertEqual(result['gain_db'], target + 14)
                    self.assertIn(f'volume={target + 14:.3f}dB,{ALIMITER}', calls[1])
                    self.assertEqual(result['sha256'], self.expected)
                self.assertEqual((self.source.read_bytes(), receipt.read_bytes()), before)
                self.assertEqual(list(self.out.glob('.audio.wav.*.tmp')), [])

    def test_normalize_hashes_source_and_candidate_without_receipt_writes(self):
        with self._bounded_reads() as reads, mock.patch.object(stages.subprocess, 'run', self._run_normalization), \
                redirect_stdout(io.StringIO()):
            result = normalize_project_audio(self.root)
        self.assertEqual(result['sha256'], self.expected)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.expected)
        paths = {path for path, _size in reads}
        self.assertIn(self.source, paths)
        self.assertEqual(len(paths), 2, 'source and staged output must each be hashed')
        self.assertFalse((self.out / 'receipt.json').exists())

    def test_read_failure_prevents_success_receipt_and_candidate_publication(self):
        with mock.patch.object(stages, 'mix_audio_wav', return_value=(0.0, -14.0, 0, False)), \
                self._bounded_reads(lambda path, reads: len(reads) == 2), \
                redirect_stdout(io.StringIO()), self.assertRaisesRegex(OSError, 'injected read failure'):
            stages.build_audio(self.c)
        self.c.receipt.assert_not_called()
        original = self.source.read_bytes()

        with self._bounded_reads(lambda path, reads: path != self.source), \
                mock.patch.object(stages.subprocess, 'run', self._run_normalization), redirect_stdout(io.StringIO()), \
                self.assertRaisesRegex(OSError, 'injected read failure'):
            normalize_project_audio(self.root)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertFalse((self.out / 'receipt.json').exists())
        self.assertEqual(list(self.out.glob('.audio.wav.*.tmp')), [])


@unittest.skipUnless(FFMPEG and shutil.which(str(FFMPEG)),
                     'Set VTX_TEST_FFMPEG or install ffmpeg on PATH')
class TestBuildAudioValidation(unittest.TestCase):
    """② build_audio 终态校验：回执 post_loudness 字段 + 两道 FAIL 门 + dry 跳过。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='vtx-post-build-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        if OUTPUT is not None:                       # 证据模式：产物留 OUTPUT
            self.root = OUTPUT / self._testMethodName
            self.root.mkdir(parents=True, exist_ok=True)
        _mk_source(self.root)
        self.tl = _mk_timeline(self.root)
        self._mk_ctx()

    def _mk_ctx(self):
        from vtx.pipeline.infra import Ctx
        self.c = Ctx(self.tl)
        self.c.cache.mkdir(parents=True, exist_ok=True)
        self.c.out.mkdir(parents=True, exist_ok=True)
        stages.prepare(self.c)   # _audio_graph 的输入是 cache/audio/<key>.wav（抽音频）

    def test_receipt_post_loudness_field(self):
        stages.build_audio(self.c)
        wav = self.c.out / "audio.wav"
        self.assertTrue(wav.exists())
        rec = json.loads((self.c.out / "receipt.json").read_text(encoding="utf-8"))
        a = rec["stages"]["audio"]
        self.assertTrue(a["ok"])
        self.assertIn("raw_i", a)
        pl = a["post_loudness"]
        self.assertEqual(pl["input_i"], a["raw_i"])            # input_i = raw_i（§1.2）
        self.assertEqual(pl["target"], TARGET_LUFS)
        self.assertLessEqual(abs(pl["output_i"] - TARGET_LUFS), 1.5)
        self.assertEqual(pl["sha256"], hashlib.sha256(wav.read_bytes()).hexdigest())
        self.assertAlmostEqual(pl["gain_db"], TARGET_LUFS - a["raw_i"], delta=0.05)
        # 同源复跑（完整重建，非 compat 幂等语义）：gain 由源内容决定应稳定，
        # 终态 output_i/sha256 稳定（ffmpeg 混音+限幅对同输入确定性）
        first = dict(pl)
        stages.build_audio(self.c)
        rec2 = json.loads((self.c.out / "receipt.json").read_text(encoding="utf-8"))
        pl2 = rec2["stages"]["audio"]["post_loudness"]
        self.assertLessEqual(abs(pl2["gain_db"] - first["gain_db"]), 0.05,
                             "同源重建增益应稳定")
        self.assertLessEqual(abs(pl2["output_i"] - first["output_i"]), 0.2)
        self.assertEqual(pl2["sha256"], first["sha256"])
        (self.root / "measurements.json").write_text(
            json.dumps({"first": first, "second": pl2}, indent=2), encoding="utf-8")

    @contextmanager
    def _terminal_ebur128(self, stderr):
        """Replace only the completed final-file measurement, with a hit witness."""
        real_run, terminal = self.c.run, []

        def run(cmd, need=True):
            result = real_run(cmd, need)
            argv = list(map(str, cmd))
            if ("ebur128=peak=true:framelog=quiet" in argv and
                    "null" in argv and str(self.c.out / "audio.wav") in argv):
                terminal.append(argv)
                result.stderr = stderr
            return result

        with mock.patch.object(self.c, "run", side_effect=run):
            try:
                yield
            finally:
                self.assertEqual(len(terminal), 1, "必须命中且仅命中终态 ebur128 复测")

    def test_loudness_recheck_failure_exits(self):
        # 保留原有缺失 Peak 拒绝分支，不误称 dev>3（另有独立测试）。
        buf = io.StringIO()
        with self._terminal_ebur128("  I:          -30.0 LUFS\n"), \
                self.assertRaises(SystemExit) as cm, redirect_stdout(buf):
            stages.build_audio(self.c)
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("终态复测未捕获真峰", buf.getvalue())
        self.assertNotIn("增益链断裂", buf.getvalue())
        rec = json.loads((self.c.out / "receipt.json").read_text(encoding="utf-8"))
        self.assertNotIn("audio", rec["stages"], "FAIL 路径不写 audio 回执")

    def test_loudness_deviation_warns_not_fails(self):
        """偏离 ∈ (1.5, 3] 是内容级 WARN，不阻断。"""
        buf = io.StringIO()
        with self._terminal_ebur128("  I:          -16.0 LUFS\n  Peak:        -5.0 dBFS\n"), \
                redirect_stdout(buf):
            stages.build_audio(self.c)
        self.assertIn("限幅响度损耗", buf.getvalue())
        rec = json.loads((self.c.out / "receipt.json").read_text(encoding="utf-8"))
        pl = rec["stages"]["audio"]["post_loudness"]
        self.assertEqual(pl["output_i"], -16.0)
        self.assertEqual(pl["tp_dbfs"], -5.0)

    def test_tp_over_limit_fails(self):
        """TP > -1.0 = 硬 FAIL；有效数字必须先通过 Peak 解析。"""
        buf = io.StringIO()
        with self._terminal_ebur128("  I:          -14.0 LUFS\n  Peak:        0.5 dBFS\n"), \
                self.assertRaises(SystemExit) as cm, redirect_stdout(buf):
            stages.build_audio(self.c)
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("终态真峰 0.5 dBFS > -1.0", buf.getvalue())
        self.assertIn("limiter 失效", buf.getvalue())
        self.assertNotIn("未捕获真峰", buf.getvalue())

    def test_dev_over_3_fails_explicitly(self):
        """dev > 3 的独立路径（有效 TP，不是解析失败）。"""
        buf = io.StringIO()
        with self._terminal_ebur128("  I:          -18.0 LUFS\n  Peak:        -5.0 dBFS\n"), \
                self.assertRaises(SystemExit) as cm, redirect_stdout(buf):
            stages.build_audio(self.c)
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("增益链断裂", buf.getvalue())

    def test_dev_boundaries_warn_and_silent(self):
        """dev 恰 3.0 → WARN 不阻断；dev 恰 1.5 → 静默。"""
        for i_str, want_warn in (("-17.0", True), ("-15.5", False)):
            buf = io.StringIO()
            with self.subTest(output_i=i_str), \
                    self._terminal_ebur128(f"  I:          {i_str} LUFS\n  Peak:        -5.0 dBFS\n"), \
                    redirect_stdout(buf):
                stages.build_audio(self.c)
            if want_warn:
                self.assertIn("限幅响度损耗", buf.getvalue())
            else:
                self.assertNotIn("限幅响度损耗", buf.getvalue())
            rec = json.loads((self.c.out / "receipt.json").read_text(encoding="utf-8"))
            self.assertIn("post_loudness", rec["stages"]["audio"])

    def test_duration_drift_failure_exits(self):
        expect = self.c.dur * self.c.spf_samples

        class _FakeWave:                              # 模拟时长漂移（+500 采样）
            def __init__(self, n): self.n = n
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def getnframes(self): return self.n

        real_open = stages.wave.open
        def drift_only_final(path, *args, **kwargs):
            # Do not replace recovery's independent staging-file integrity check.
            if Path(path) == self.c.out / "audio.wav":
                return _FakeWave(expect + 500)
            return real_open(path, *args, **kwargs)

        with mock.patch.object(stages.wave, "open", drift_only_final):
            with self.assertRaises(SystemExit) as cm:
                stages.build_audio(self.c)
        self.assertEqual(cm.exception.code, 1)
        rec = json.loads((self.c.out / "receipt.json").read_text(encoding="utf-8"))
        self.assertNotIn("audio", rec["stages"])

    def test_dry_run_skips_validation_and_receipt(self):
        self.c.dry = True
        (self.c.out / "receipt.json").unlink(missing_ok=True)   # 清掉 prepare 的回执
        stages.build_audio(self.c)                    # 只打印命令，无产物/无回执
        self.assertFalse((self.c.out / "audio.wav").exists())
        self.assertFalse((self.c.out / "receipt.json").exists())


@unittest.skipUnless(FFMPEG and shutil.which(str(FFMPEG)),
                     'Set VTX_TEST_FFMPEG or install ffmpeg on PATH')
class TestPostLoudnessCompat(unittest.TestCase):
    """③ normalize_project_audio（内核音频回归 API）：幂等 + 保原 +
    不做回执手术。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='vtx-post-compat-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.out = self.root / "out"
        self.out.mkdir(parents=True)
        # 0.95 振幅正弦：一次 compat 即触达限幅（gain 正且峰值超 0.599）
        with wave.open(str(self.out / "audio.wav"), 'wb') as s:
            s.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
            s.writeframes(b''.join(
                struct.pack('<hh', round(31000 * math.sin(2 * math.pi * 440 * i / 48000)),
                            round(31000 * math.sin(2 * math.pi * 440 * i / 48000)))
                for i in range(48000 * 2)))
        self.receipt = self.out / "receipt.json"
        self.receipt.write_text(json.dumps(
            {"input_fingerprint": "fixture",
             "stages": {"audio": {"ok": True, "raw_i": -35, "gain_db": 21},
                        "video": {"ok": True}, "mux": {"ok": True}, "qc": {"ok": True}}},
            ensure_ascii=False), encoding="utf-8")

    def test_idempotent_and_no_receipt_surgery(self):
        self._assert_canonical_cli_contract()
        before_receipt = self.receipt.read_bytes()
        first = normalize_project_audio(self.root, FFMPEG)
        second = normalize_project_audio(self.root, FFMPEG)
        self.assertLess(abs(second["gain_db"]), 0.2, "已限幅文件二遍 gain≈0")
        self.assertLess(abs(second["output_i"] - first["output_i"]), 0.2)
        self.assertEqual(second["target"], -14.0)
        self.assertEqual(self.receipt.read_bytes(), before_receipt,
                         "现存 WAV 维护不做回执摘除-回写手术")
        rec = json.loads(self.receipt.read_text(encoding="utf-8"))
        self.assertEqual(set(rec["stages"]), {"audio", "video", "mux", "qc"})
        self.assertFalse(list(self.out.glob(".post-*")), "临时件已清理")
        (self.root / "measurements.json").write_text(
            json.dumps({"first": first, "second": second}, indent=2), encoding="utf-8")

    def test_encoder_failure_preserves_original(self):
        before = (self.out / "audio.wav").read_bytes(), self.receipt.read_bytes()
        real_run = stages.subprocess.run
        calls = []

        def run3(cmd, **kw):
            calls.append(cmd)
            if len(calls) == 2:                       # 第 1 次=测量（真跑），第 2 次=限幅 → 失败
                return SimpleNamespace(returncode=1, stderr="encoder failure")
            return real_run(cmd, **kw)

        with mock.patch.object(stages.subprocess, "run", run3):
            with self.assertRaisesRegex(RuntimeError, "encoder failure"):
                normalize_project_audio(self.root, FFMPEG)
        self.assertEqual(before, ((self.out / "audio.wav").read_bytes(),
                                   self.receipt.read_bytes()))
        self.assertFalse(list(self.out.glob(".post-*")))

    def test_terminal_receipt_target_change_reprocesses_current_wav(self):
        first = normalize_project_audio(self.root, FFMPEG)
        rec = json.loads(self.receipt.read_text(encoding='utf-8'))
        rec['stages']['audio']['post_loudness'] = first
        self.receipt.write_text(json.dumps(rec), encoding='utf-8')
        before = self.receipt.read_bytes()
        with mock.patch.object(stages.subprocess, 'run', side_effect=AssertionError('same target reprocessed')):
            self.assertEqual(normalize_project_audio(self.root, FFMPEG, target=-14), first)
        changed = normalize_project_audio(self.root, FFMPEG, target=-16)
        self.assertEqual(changed['target'], -16)
        self.assertAlmostEqual(changed['input_i'], first['output_i'], delta=0.2)
        self.assertAlmostEqual(changed['output_i'], -16, delta=1.5)
        self.assertLess(changed['gain_db'], -1.5)
        self.assertNotEqual(changed['sha256'], first['sha256'])
        self.assertEqual(changed['sha256'], hashlib.sha256((self.out / 'audio.wav').read_bytes()).hexdigest())
        with wave.open(str(self.out / 'audio.wav')) as stream:
            self.assertEqual(stream.getnframes(), 48000 * 2)
        self.assertEqual(self.receipt.read_bytes(), before)
        self.assertEqual(list(self.out.glob('.audio.wav.*.tmp')), [])

    def test_missing_audio_raises(self):
        (self.out / "audio.wav").unlink()
        with self.assertRaises(FileNotFoundError):
            normalize_project_audio(self.root, FFMPEG)

    def _assert_canonical_cli_contract(self):
        import contextlib
        import importlib.util
        import io
        path = Path(__file__).resolve().parents[2] / 'scripts/audio/normalize_project_audio.py'
        spec = importlib.util.spec_from_file_location('audio_maintenance_fixture', path)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.object(sys, 'path', list(sys.path)):
            spec.loader.exec_module(module)
        argv = [str(path), 'project', '--target', '-16', '--ffmpeg', 'custom-ffmpeg']
        for failure in (False, True):
            with self.subTest(failure=failure):
                output, errors = io.StringIO(), io.StringIO()
                with mock.patch.object(sys, 'argv', argv), \
                     mock.patch.object(module, 'normalize_project_audio',
                                       return_value={'target': -16, 'project': 'project'},
                                       side_effect=ValueError('invalid audio') if failure else None) as normalize, \
                     contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    code = module.main()
                normalize.assert_called_once_with('project', 'custom-ffmpeg', -16)
                self.assertEqual(code, 1 if failure else 0)
                if failure:
                    self.assertEqual(output.getvalue(), '')
                    self.assertIn('FATAL: invalid audio', errors.getvalue())
                else:
                    self.assertEqual(json.loads(output.getvalue()), {'target': -16, 'project': 'project'})
                    self.assertEqual(errors.getvalue(), '')


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--ffmpeg', default=FFMPEG)
    p.add_argument('--output', type=Path)
    args = p.parse_args()
    FFMPEG, OUTPUT = args.ffmpeg, args.output.resolve() if args.output else None
    unittest.main(argv=[sys.argv[0]], verbosity=2)
