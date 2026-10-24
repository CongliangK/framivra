"""CEF benchmark-tool contracts, not a Mac/CEF or 900-frame performance result.

Fake engines exercise the real CLI/subprocess boundary. Tiny FFmpeg fixtures only
verify full-stream hashing. Every signal experiment has an isolated session;
these tests never select or alter repository gates or product thresholds.
"""
import copy
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from fractions import Fraction
import textwrap
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts/gfx/bench_cef.py"


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")


def _snapshot(root):
    """Original entries, bytes, mtimes, and modes must survive the experiment."""
    result = {}
    for path in [root] + sorted(root.rglob("*")):
        info = path.lstat()
        value = path.read_bytes() if stat.S_ISREG(info.st_mode) else None
        if stat.S_ISLNK(info.st_mode):
            value = os.readlink(path)
        result[path.relative_to(root).as_posix()] = (info.st_mode, info.st_mtime_ns, value)
    return result


def _executable(path, source):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!" + sys.executable + "\n" + textwrap.dedent(source), encoding="utf-8")
    path.chmod(0o755)
    return path


def _safe_env(root):
    return {"PATH": os.defpath, "HOME": str(root), "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}


class _TemporaryCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="vtx-cef-bench-test-")
        self.addCleanup(temporary.cleanup)
        # macOS /tmp is a system symlink; project safety requires a physical root.
        self.root = Path(temporary.name).resolve()
        self.env = _safe_env(self.root)

    def python(self, program, *args, timeout=30, env=None):
        """The harness itself, not just the process being tested, is isolated."""
        return subprocess.run([sys.executable, "-I", "-B", "-c", program,
                               str(ROOT), *map(str, args)], cwd=self.root,
                              env=self.env if env is None else env,
                              capture_output=True, text=True, timeout=timeout,
                              start_new_session=True)


_RUNTIME_HARNESS = r'''
import json, os, signal, subprocess, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from scripts.gfx import _bench_runtime as runtime
spec = json.loads(Path(sys.argv[2]).read_text())
try:
    result = runtime.run_command(**spec)
    owned = Path(sys.argv[3])
    if owned.exists():
        ids = json.loads(owned.read_text())
        probe = subprocess.run(['ps', '-o', 'stat=', '-p',
            str(ids['pid']) + ',' + str(ids['child'])], capture_output=True, text=True, timeout=5)
        import time
        deadline = time.monotonic() + 2
        while any(not line.strip().startswith('Z') for line in probe.stdout.splitlines()) and time.monotonic() < deadline:
            time.sleep(.02)
            probe = subprocess.run(['ps', '-o', 'stat=', '-p',
                str(ids['pid']) + ',' + str(ids['child'])], capture_output=True, text=True, timeout=5)
        result['_owned_states_before_guard'] = probe.stdout.splitlines()
    print(json.dumps(result, allow_nan=False))
finally:
    # Only this fixture's PID/PGID, recorded by its direct spawned command.
    # This guard prevents a failing regression from leaking the owned process.
    owned = Path(sys.argv[3])
    if owned.exists():
        identity = json.loads(owned.read_text())
        pid, pgid = identity['pid'], identity['pgid']
        if pid == pgid and pgid != os.getpgrp():
            try:
                member = pid
                try:
                    current = os.getpgid(member)
                except ProcessLookupError:
                    member = identity['child']
                    current = os.getpgid(member)
                if current == pgid:
                    os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
'''


class RuntimeContracts(_TemporaryCase):
    def command(self, source, *, timeout=5, env=None):
        owned = self.root / "owned.json"
        spec = self.root / "command.json"
        _json(spec, {"argv": [sys.executable, "-I", "-B", "-c", source],
                     "cwd": str(self.root), "env": self.env if env is None else env,
                     "log_path": str(self.root / "command.log"),
                     "timeout": timeout, "sample_interval": 0.02})
        child = self.python(_RUNTIME_HARNESS, spec, owned)
        self.assertEqual(child.returncode, 0, child.stderr)
        return json.loads(child.stdout)

    def test_real_child_success_private_log_and_no_environment_dump(self):
        sentinel = "cef-benchmark-secret-must-not-be-serialized"
        result = self.command("import time; print('fixture output', flush=True); time.sleep(.15)",
                              env=dict(self.env, PRIVATE_API_TOKEN=sentinel))
        self.assertEqual(result["exit_code"], 0)
        self.assertFalse(result["timed_out"])
        self.assertGreater(result["elapsed_seconds"], 0)
        log = (self.root / "command.log").read_text()
        self.assertTrue(log.rstrip().endswith("fixture output"))
        self.assertNotIn(sentinel, log)
        self.assertEqual(stat.S_IMODE((self.root / "command.log").stat().st_mode), 0o600)
        self.assertNotIn(sentinel, json.dumps(result))
        resources = result["resources"]
        self.assertIn(resources["status"], ("sampled", "partial", "unavailable"))
        if resources["status"] == "sampled":
            self.assertGreater(resources["samples"], 0)
            self.assertGreater(resources["peak_tree_rss_bytes"], 0)

    def test_failed_child_is_not_retried(self):
        marker = self.root / "attempts.txt"
        result = self.command("from pathlib import Path; p=Path(" + repr(str(marker)) +
                              "); p.write_text(p.read_text()+'x' if p.exists() else 'x'); "
                              "raise SystemExit(23)")
        self.assertEqual(result["exit_code"], 23)
        self.assertFalse(result["timed_out"])
        self.assertEqual(marker.read_text(), "x")

    @unittest.skipUnless(os.name == "posix", "POSIX process-group contract")
    def test_timeout_reaps_owned_process_group_in_isolated_harness(self):
        owned = self.root / "owned.json"
        source = """
import json, os, signal, subprocess, sys, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, '-c',
    'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'])
Path(%r).write_text(json.dumps({'pid': os.getpid(), 'pgid': os.getpgrp(), 'child': child.pid}))
time.sleep(60)
""" % str(owned)
        result = self.command(source, timeout=0.25)
        self.assertTrue(result["timed_out"])
        self.assertNotEqual(result["exit_code"], 0)
        self.assertTrue(result["cleanup"]["term_sent"])
        self.assertTrue(result["cleanup"]["kill_sent"])
        identity = json.loads(owned.read_text())
        self.assertEqual(identity["pid"], identity["pgid"])
        states = result['_owned_states_before_guard']
        self.assertTrue(all(line.strip().startswith("Z") for line in states),
                        "Owned timed-out process remained live: " + repr(states))

    def test_invalid_timing_rejected_before_spawn(self):
        from scripts.gfx import _bench_runtime as runtime
        for timeout, interval in ((0, 1), (-1, 1), (math.nan, 1), (math.inf, 1),
                                  (1, 0), (1, math.nan), (1, 61)):
            with self.subTest(timeout=timeout, interval=interval), \
                    mock.patch.object(runtime.subprocess, "Popen") as spawn:
                with self.assertRaises(ValueError):
                    runtime.run_command([sys.executable, "-c", "pass"], cwd=self.root,
                                        env=self.env, log_path=self.root / "invalid.log",
                                        timeout=timeout, sample_interval=interval)
                spawn.assert_not_called()



    def test_cleanup_never_signals_reaped_leader_and_reap_wait_is_bounded(self):
        program = r'''
import json, subprocess, sys
from unittest import mock
sys.path.insert(0, sys.argv[1])
from scripts.gfx import _bench_runtime as runtime
results = []
for reaped in (True, False):
    process = mock.Mock(pid=12345, returncode=0 if reaped else None)
    process.wait.side_effect = subprocess.TimeoutExpired('owned fixture', 5)
    cleanup = {'term_sent':False, 'kill_sent':False, 'errors':[]}
    with mock.patch.object(runtime.os, 'killpg') as signal_group, mock.patch.object(runtime.time, 'sleep'):
        code = runtime._terminate_owned_group(process, cleanup)
    results.append({'code':code, 'cleanup':cleanup,
        'signals':[list(call.args) for call in signal_group.call_args_list],
        'waits':[call.kwargs for call in process.wait.call_args_list]})
print(json.dumps(results))
'''
        child = self.python(program)
        self.assertEqual(child.returncode, 0, child.stderr)
        reaped, unreapable = json.loads(child.stdout)
        self.assertEqual(reaped['signals'], [])
        self.assertEqual(reaped['waits'], [])
        self.assertTrue(reaped['cleanup']['errors'])
        self.assertEqual([row[0] for row in unreapable['signals']], [12345,12345])
        self.assertEqual(unreapable['waits'], [{'timeout':5.0}])
        self.assertIsNone(unreapable['code'])
        self.assertFalse(unreapable['cleanup']['leader_reaped'])
        self.assertTrue(unreapable['cleanup']['errors'])


_QC_NAMES = ('成片帧数 = 900', '成片时长 ≈ 30.00s', '分辨率 1920x1080',
             '响度归一 ≈ -14 LUFS', '解码域真峰 TP ≤ -1 dBFS', '1 轨并发窗口有能量')


def _qc_document():
    return {'checks':[{'name':name, 'pass':True,
                       'detail':'解码计数' if index == 0 else 'owned fixture'}
                      for index, name in enumerate(_QC_NAMES)], 'skipped_probes':[]}


def _receipt_document():
    stages = {name:{'ok':True} for name in
              ('validate', 'audit', 'prepare', 'gfx', 'video', 'audio', 'mux', 'qc')}
    stages['qc']['detail'] = '6/6'
    return {'stages':stages, 'gfx_backend':{'requested_backend':'cef',
            'identity':'cef-alpha-exact-v2|owned-fixture', 'namespace':'cef-alpha',
            'environment_identity':'a'*64, 'environment_evidence':'input-bound; pixel equivalence unproven'},
            'timeline':'f'*64,
            'input_fingerprint':'e'*64}


_FAKE_ENGINE = r"""
import hashlib, io, json, os, sys, uuid
from pathlib import Path
sys.path.insert(0, {real_root!r})
from vtx.runtime.events import ExecutionContext
trace = Path({trace!r})
project = Path(sys.argv[sys.argv.index('-t') + 1]).parent
phase = 'strict' if '--strict' in sys.argv else 'all'
inputs = {{p: hashlib.sha256((project / p).read_bytes()).hexdigest()
           for p in ('timeline.json', 'gfx.html', 'audio.wav')}}
row = {{'tool': 'engine', 'phase': phase, 'role': {role!r}, 'project': str(project),
       'inputs': inputs, 'cache_existed': (project / 'cache/owned').exists(),
       'secret_forwarded': 'PRIVATE_API_TOKEN' in os.environ,
       'env': {{k:v for k,v in os.environ.items() if k.startswith('VTX_')}}}}
with trace.open('a') as out:
    out.write(json.dumps(row) + '\n')
if {failure!r} == phase:
    print('owned fake engine failure', flush=True)
    raise SystemExit(19)
output = project / 'out'
output.mkdir(exist_ok=True)
(output / 'final.mp4').write_bytes(b'not-real-media: CLI contract fixture only')
(output / 'receipt.json').write_text(json.dumps({receipt!r}))
(output / 'qc_report.json').write_text(json.dumps({qc!r}))
cache = project / 'cache'
cache.mkdir(exist_ok=True)
if phase == 'all' and not (cache / 'owned').exists():
    native = cache / 'reports/cef'
    native.mkdir(parents=True, exist_ok=True)
    (native / 'fixture.pipeline.json').write_text(json.dumps({{
        'schema':'vtx.cef-host-pipeline/1', 'completed':True,
        'request_wall_to_reply_us':1000, 'capture_wall_us':500}}))
(cache / 'owned').write_text('warm cache marker')
events = Path(sys.argv[sys.argv.index('--events-jsonl') + 1])
events.parent.mkdir(parents=True, exist_ok=True)
with events.open('w') as stream:
    execution = ExecutionContext(stream)
    if phase == 'all' and not row['cache_existed']:
        native_path = cache / 'reports/cef/fixture.pipeline.json'
        native_data = json.loads(native_path.read_text())
        native_data['execution'] = {{'run_id':execution.run_id}}
        native_path.write_text(json.dumps(native_data))
    for stage in (('qc',) if phase == 'strict' else
                  ('validate','audit','prepare','gfx','video','audio','mux','qc')):
        with execution.task(stage, kind='stage').scope():
            pass
    execution.finish(0)
"""


class _BenchmarkFixture:
    """All subprocesses and writes live in one owned tree, never a real project."""
    def __init__(self, root, *, failure=None):
        self.root = root
        root.mkdir()
        self.project = root / 'source'
        self.project.mkdir()
        self.output = root / 'result'
        self.trace = root / 'trace.jsonl'
        self.env = _safe_env(root)
        self.env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        self.timeline = {'project': {'width':1920, 'height':1080, 'fps':30,
                                    'duration_frames':900},
                         'sources':[{'id':'audio', 'path':'audio.wav'}],
                         'video_tracks':[{'kind':'gfx', 'clips':[
                             {'src':'gfx.html', 'dst_in':0, 'dur':900}]}],
                         'audio_tracks':[], 'subtitles':[]}
        _json(self.project / 'timeline.json', self.timeline)
        (self.project / 'gfx.html').write_text('<html><body>owned fixture</body></html>')
        (self.project / 'audio.wav').write_bytes(b'fixture-only-audio')
        for name in ('cache', 'out', '.git'):
            (self.project / name).mkdir()
            (self.project / name / 'original').write_bytes(b'original preserved')
        self.repos = {}
        self.workers = {}
        for role in ('baseline', 'candidate'):
            repo = root / role
            repo.mkdir()
            entry = repo / 'vtx/vtx.py'
            entry.parent.mkdir()
            entry.write_text(_FAKE_ENGINE.format(real_root=str(ROOT), trace=str(self.trace),
                                                 role=role, failure=failure,
                                                 receipt=_receipt_document(), qc=_qc_document()), encoding='utf-8')
            self.git(repo, 'init', '--quiet')
            self.git(repo, 'add', '.')
            self.git(repo, '-c', 'user.name=CEF Test', '-c', 'user.email=cef-test@example.invalid',
                     'commit', '--quiet', '-m', 'Owned benchmark contract fixture')
            self.repos[role] = repo
            self.workers[role] = _executable(root / (role + '-worker'),
                                           "print('owned fake CEF worker')\n")
        self.tools = root / 'tools'
        self.tools.mkdir()
        self.ffmpeg = _executable(self.tools / 'ffmpeg', self.fake_media('ffmpeg', failure))
        self.ffprobe = _executable(self.tools / 'ffprobe', self.fake_media('ffprobe', failure))

    def git(self, repo, *args):
        result = subprocess.run(['git', '-C', str(repo), *args], env=self.env,
                                capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout.strip()

    def fake_media(self, tool, failure):
        return r"""
import json, sys
from pathlib import Path
if '-version' in sys.argv or '--version' in sys.argv:
    print({tool!r} + ' version owned contract fixture')
    raise SystemExit(0)
with Path({trace!r}).open('a') as trace:
    trace.write(json.dumps({{'tool':{tool!r}, 'argv':sys.argv[1:]}}) + '\n')
if {failure!r} == {tool!r}:
    raise SystemExit(21)
if {tool!r} == 'ffprobe':
    result = {{'streams':[
        {{'index':0,'codec_type':'video','codec_name':'h264','width':1920,'height':1080,
          'r_frame_rate':'30/1','avg_frame_rate':'30/1','nb_frames':'900',
          'nb_read_frames':'900','duration':'30.000000','pix_fmt':'yuv420p','start_pts':0}},
        {{'index':1,'codec_type':'audio','codec_name':'aac','sample_rate':'48000',
          'channels':2,'channel_layout':'stereo','duration':'30.000000','start_pts':0,
          'time_base':'1/48000','duration_ts':1440000}}],
        'format':{{'duration':'30.000000'}}}}
    Path(sys.argv[sys.argv.index('-o')+1]).write_text(json.dumps(result))
elif '0:v:0' in sys.argv:
    Path(sys.argv[-1]).write_text('#tb 0: 1/30\n#dimensions 0: 1920x1080\n' +
        ''.join('0, %d, %d, 1, 3110400, %s\n' % (i,i,'a'*32) for i in range(900)))
else:
    Path(sys.argv[sys.argv.index('-hash')+2]).write_text('SHA256=' + 'b'*64 + '\n')
    Path(sys.argv[-1]).write_text('#tb 0: 1/48000\n0, 0, 0, 1440000, 5760000, ' + 'c'*32 + '\n')
""".format(tool=tool, trace=str(self.trace), failure=failure)

    def args(self, command, *extra):
        if command == 'report':
            return [str(self.output), *extra]
        return ['--project', str(self.project),
                '--baseline-repo', str(self.repos['baseline']),
                '--baseline-worker', str(self.workers['baseline']),
                '--candidate-repo', str(self.repos['candidate']),
                '--candidate-worker', str(self.workers['candidate']),
                '--ffmpeg', str(self.ffmpeg), '--ffprobe', str(self.ffprobe),
                '--rounds', '2', '--sample-interval', '0.05', '--timeout', '10',
                *(['--output', str(self.output)] if command == 'run' else []), *extra]

    def cli(self, command, *extra, env=None):
        return subprocess.run([sys.executable, '-B', str(CLI), command,
                               *self.args(command, *extra)], cwd=self.root,
                              env=self.env if env is None else env,
                              capture_output=True, text=True, timeout=60,
                              start_new_session=True)

    def rows(self):
        if not self.trace.exists():
            return []
        return [json.loads(line) for line in self.trace.read_text().splitlines()]


@unittest.skipUnless(shutil.which('git') and os.name == 'posix', 'owned Git/POSIX fixtures')
class CliContracts(_TemporaryCase):
    def fixture(self, name='case', **kwargs):
        return _BenchmarkFixture(self.root / name, **kwargs)

    def test_plan_is_read_only_and_identifies_clean_sources(self):
        fixture = self.fixture()
        original = _snapshot(fixture.root)
        result = fixture.cli('plan')
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(_snapshot(fixture.root), original)
        self.assertFalse(fixture.output.exists())
        self.assertIn(fixture.git(fixture.repos['baseline'], 'rev-parse', 'HEAD'),
                      json.dumps(plan))
        self.assertTrue(plan['plan']['baseline']['clean'])
        self.assertTrue(plan['plan']['candidate']['clean'])
        self.assertEqual(plan['plan']['workload']['duration_frames'], 900)
        self.assertRegex(plan['plan']['input_digest'], r'^[0-9a-f]{64}$')

    def test_refuses_existing_output_and_unqualified_workloads(self):
        for kind in ('existing-output', 'svg', 'twelve-seconds', 'no-html', 'dirty-repo'):
            with self.subTest(kind=kind):
                fixture = self.fixture(kind)
                if kind == 'existing-output':
                    fixture.output.mkdir()
                    (fixture.output / 'keep').write_text('never overwrite')
                elif kind == 'dirty-repo':
                    (fixture.repos['candidate'] / 'unexpected').write_text('dirty')
                else:
                    if kind == 'svg':
                        (fixture.project / 'gfx.svg').write_text('<svg/>')
                        fixture.timeline['video_tracks'][0]['clips'][0]['src'] = 'gfx.svg'
                    elif kind == 'twelve-seconds':
                        fixture.timeline['project']['duration_frames'] = 360
                        fixture.timeline['video_tracks'][0]['clips'][0]['dur'] = 360
                    else:
                        fixture.timeline['video_tracks'] = []
                    _json(fixture.project / 'timeline.json', fixture.timeline)
                before = _snapshot(fixture.project)
                result = fixture.cli('run')
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(_snapshot(fixture.project), before)
                self.assertEqual(fixture.rows(), [])
                if kind == 'existing-output':
                    self.assertEqual((fixture.output / 'keep').read_text(), 'never overwrite')
                else:
                    self.assertFalse(fixture.output.exists())

    def test_rejects_symlink_special_file_and_output_overlap(self):
        for kind in ('symlink-file', 'symlink-dir', 'fifo', 'output-in-project',
                     'output-contains-project', 'output-in-repo', 'external-reference'):
            with self.subTest(kind=kind):
                fixture = self.fixture(kind)
                if kind == 'symlink-file':
                    (fixture.project / 'link').symlink_to(fixture.project / 'gfx.html')
                elif kind == 'symlink-dir':
                    (fixture.project / 'linked').symlink_to(fixture.repos['baseline'],
                                                           target_is_directory=True)
                elif kind == 'fifo':
                    os.mkfifo(fixture.project / 'pipe')
                elif kind == 'output-in-project':
                    fixture.output = fixture.project / 'new-output'
                elif kind == 'output-contains-project':
                    fixture.output = fixture.root
                elif kind == 'output-in-repo':
                    fixture.output = fixture.repos['candidate'] / 'new-output'
                else:
                    fixture.timeline['sources'][0]['path'] = '../outside.wav'
                    _json(fixture.project / 'timeline.json', fixture.timeline)
                before = _snapshot(fixture.project)
                result = fixture.cli('run')
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(_snapshot(fixture.project), before)
                self.assertEqual(fixture.rows(), [])

    def test_fake_engine_end_to_end_order_copy_phase_isolation_and_report(self):
        fixture = self.fixture()
        original = _snapshot(fixture.project)
        sentinel = 'do-not-copy-ambient-secret-to-benchmark'
        result = fixture.cli('run', env=dict(fixture.env, PRIVATE_API_TOKEN=sentinel,
                                             VTX_GFX='cdp', PYTHONPATH='/not/a/real/path'))
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(_snapshot(fixture.project), original)
        record = json.loads((fixture.output / 'run.json').read_text())
        self.assertEqual(record['status'], 'passed', record.get('errors'))
        expected = [(1, 'baseline', 'cold'), (1, 'baseline', 'warm'),
                    (1, 'candidate', 'cold'), (1, 'candidate', 'warm'),
                    (2, 'candidate', 'cold'), (2, 'candidate', 'warm'),
                    (2, 'baseline', 'cold'), (2, 'baseline', 'warm')]
        self.assertEqual([(r['round'], r['role'], r['cache_state']) for r in record['runs']],
                         expected)
        rows = fixture.rows()
        engines = [r for r in rows if r['tool'] == 'engine']
        self.assertEqual(len(engines), 16)
        inputs = {p:hashlib.sha256((fixture.project / p).read_bytes()).hexdigest()
                  for p in ('timeline.json', 'gfx.html', 'audio.wav')}
        for index, row in enumerate(record['runs']):
            with self.subTest(round=row['round'], role=row['role'], cache=row['cache_state']):
                all_row, strict_row = engines[index * 2:index * 2 + 2]
                self.assertEqual((all_row['phase'], strict_row['phase']), ('all', 'strict'))
                self.assertEqual(all_row['role'], row['role'])
                self.assertEqual(all_row['inputs'], inputs)
                self.assertEqual(strict_row['inputs'], inputs)
                self.assertEqual(all_row['cache_existed'], row['cache_state'] == 'warm')
                self.assertFalse(all_row['secret_forwarded'])
                self.assertEqual(all_row['env']['VTX_GFX'], 'cef')
                self.assertEqual(row['command']['argv'][-1], 'all')
                self.assertIn('--strict', row['strict_command']['argv'])
                self.assertGreater(row['qa_seconds'], 0)
                self.assertTrue(row['source_identity_valid'])
                self.assertTrue(row['tools_identity_valid'])
                self.assertEqual(row['media']['status'], 'passed')
                if row['cache_state'] == 'warm':
                    self.assertEqual(row['native_paths'], [], 'Old cold native logs are not new warm work')
                else:
                    self.assertEqual(len(row['native_paths']), 1)
                project = Path(all_row['project'])
                self.assertFalse((project / '.git').exists())
                self.assertFalse((project / 'out/original').exists())
                self.assertFalse((project / 'cache/original').exists())
        # No verification is inside/replacing the measured all process: it starts
        # only after all + strict, and finishes before the next measured command.
        starts = [i for i, r in enumerate(rows) if r.get('phase') == 'all'] + [len(rows)]
        for first, end in zip(starts, starts[1:]):
            phase = rows[first:end]
            self.assertEqual([r.get('phase') for r in phase[:2]], ['all', 'strict'])
            self.assertIn('ffprobe', [r['tool'] for r in phase[2:]])
            self.assertIn('ffmpeg', [r['tool'] for r in phase[2:]])
        for path in fixture.output.rglob('*'):
            if path.is_file():
                self.assertNotIn(sentinel.encode(), path.read_bytes(), str(path))
        manifest = json.loads((fixture.output / 'input_manifest.json').read_text())
        self.assertIn(inputs['gfx.html'], json.dumps(manifest))
        before = _snapshot(fixture.output)
        trace = fixture.trace.read_bytes()
        report = fixture.cli('report')
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertIn('CEF', report.stdout)
        structured = fixture.cli('report', '--json')
        self.assertEqual(structured.returncode, 0, structured.stderr)
        self.assertFalse(json.loads(structured.stdout)['goal_met'])  # only two paired rounds
        self.assertEqual(_snapshot(fixture.output), before)
        self.assertEqual(fixture.trace.read_bytes(), trace, 'report must not replay any command')

    def test_failure_stops_dependencies_preserves_report_and_never_retries(self):
        for failure in ('all', 'strict', 'ffprobe', 'ffmpeg'):
            with self.subTest(failure=failure):
                fixture = self.fixture(failure, failure=failure)
                original = _snapshot(fixture.project)
                result = fixture.cli('run')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(_snapshot(fixture.project), original)
                record = json.loads((fixture.output / 'run.json').read_text())
                self.assertEqual(record['status'], 'failed')
                self.assertEqual(len(record['runs']), 1)
                self.assertEqual(record['runs'][0]['status'], 'failed')
                self.assertTrue((fixture.output / 'report.md').is_file())
                summary = json.loads((fixture.output / 'summary.json').read_text())
                self.assertFalse(summary['goal_met'])
                rows = fixture.rows()
                phases = [r['phase'] for r in rows if r['tool'] == 'engine']
                self.assertEqual(phases, ['all'] if failure == 'all' else ['all', 'strict'])
                if failure in ('all', 'strict'):
                    self.assertEqual([r['tool'] for r in rows], ['engine'] * len(phases))
                else:
                    self.assertEqual(sum(r['tool'] == failure for r in rows), 1)
                if failure == 'all':
                    self.assertIsNone(record['runs'][0]['strict_command'])
                    self.assertIsNone(record['runs'][0]['media'])
                elif failure == 'strict':
                    self.assertIsNone(record['runs'][0]['media'])

    def test_explicit_ablation_only_and_dangerous_environment_options_rejected(self):
        fixture = self.fixture()
        for args in (('--common-set', 'VTX_GFX=cdp'),
                     ('--common-set', 'LD_PRELOAD=/tmp/not-allowed'),
                     ('--candidate-set', 'VTX_CEF_PNG_LEVEL=2'),
                     ('--candidate-set', 'VTX_CEF_PNG_LEVEL=2', '--ablation', '   '),
                     ('--common-set', 'VTX_CEF_ENCODE_THREADS=0')):
            with self.subTest(args=args):
                self.assertNotEqual(fixture.cli('plan', *args).returncode, 0)
        result = fixture.cli('plan', '--candidate-set', 'VTX_CEF_PNG_LEVEL=2',
                             '--ablation', 'png-level-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)['plan']
        self.assertEqual(plan['ablation'], 'png-level-only')
        self.assertEqual(plan['candidate_env'], {'VTX_CEF_PNG_LEVEL':'2'})
        self.assertEqual(fixture.rows(), [])


def _overlapping_events():
    """Canonical events with overlapping independent waves and stage intervals."""
    from vtx.runtime.events import ExecutionContext
    stream, now = io.StringIO(), [0.0]
    execution = ExecutionContext(stream, clock=lambda:now[0])
    first = execution.task(kind='wave_job')
    second = execution.task(kind='wave_job')
    video = execution.task('video', parent=first, kind='stage')
    audio = execution.task('audio', parent=second, kind='stage')
    scopes = [first.scope(), second.scope(), video.scope(), audio.scope()]
    for stamp, scope in zip((0, 1, 1.5, 2), scopes):
        now[0] = stamp
        scope.__enter__()
    for stamp, scope in zip((3, 4, 4.5, 5), reversed(scopes)):
        now[0] = stamp
        scope.__exit__(None, None, None)
    now[0] = 6
    with mock.patch.object(stream, 'close'):
        execution.finish(0)
    return stream.getvalue()



def _event_document(stages):
    from vtx.runtime.events import ExecutionContext
    stream = io.StringIO()
    execution = ExecutionContext(stream)
    for stage in stages:
        with execution.task(stage, kind='stage').scope():
            pass
    with mock.patch.object(stream, 'close'):
        execution.finish(0)
    return stream.getvalue()



def _synthetic_media(root):
    from scripts.gfx._bench_runtime import _normalize_framemd5, _normalize_audio_framemd5
    video = {'index':0, 'codec_type':'video', 'width':1920, 'height':1080, 'pix_fmt':'yuv420p',
             'avg_frame_rate':'30/1', 'r_frame_rate':'30/1', 'nb_read_frames':'900', 'start_pts':0}
    audio = {'index':1, 'codec_type':'audio', 'sample_rate':'48000', 'channels':2,
             'start_pts':0, 'time_base':'1/48000', 'duration_ts':1440000}
    _json(root / 'ffprobe.json', {'streams':[video,audio]})
    (root / 'video.framemd5').write_text('#tb 0: 1/30\n#dimensions 0: 1920x1080\n' +
        ''.join('0, %d, %d, 1, 3110400, %s\n' % (i,i,'a'*32) for i in range(900)))
    (root / 'audio.framemd5').write_text('#tb 0: 1/48000\n0, 0, 0, 1440000, 5760000, ' + 'c'*32 + '\n')
    (root / 'audio.sha256').write_text('SHA256=' + 'b'*64 + '\n')
    video_normalized, video_digest = _normalize_framemd5(root / 'video.framemd5', video, 900, Fraction(30))
    audio_normalized, timeline_digest = _normalize_audio_framemd5(root / 'audio.framemd5', audio, 900, Fraction(30))
    _json(root / 'video.normalized.json', video_normalized)
    _json(root / 'audio.normalized.json', audio_normalized)
    evidence = {'ffprobe':'ffprobe.json', 'video_framemd5':'video.framemd5',
                'video_normalized':'video.normalized.json', 'audio_sha256':'audio.sha256',
                'audio_framemd5':'audio.framemd5', 'audio_normalized':'audio.normalized.json'}
    commands = {}
    for name in ('ffprobe','video','audio'):
        (root / (name + '.log')).write_text('synthetic evidence, not an actual media run\n')
        evidence[name + '_log'] = name + '.log'
        commands[name] = {'exit_code':0, 'timed_out':False, 'error':None, 'elapsed_seconds':1}
    checks = ('final_exists','ffprobe_full_stream','video_stream','audio_stream',
              'video_geometry','video_frame_count','video_frame_rate','video_start_time',
              'audio_format','audio_start_time','video_full_decode','video_timestamps',
              'video_decoded_frame_count','audio_full_decode','audio_nonempty','audio_timestamps',
              'audio_duration','source_unchanged')
    return {'status':'passed', 'video_digest':video_digest, 'audio_digest':'b'*64,
            'audio_timeline_digest':timeline_digest, 'checks':dict.fromkeys(checks, True),
            'video':video, 'audio':audio, 'audio_timing':audio_normalized,
            'evidence':evidence, 'commands':commands}


def _report_fixture(root):
    """Synthetic complete three-pair evidence, reusable by independent review."""
    from scripts.gfx.bench_cef import FIXED_ENV
    root.mkdir(parents=True, exist_ok=True)
    _json(root / 'receipt.json', _receipt_document())
    _json(root / 'qc.json', _qc_document())
    media = _synthetic_media(root)
    files = {p.name:{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(), 'size':p.stat().st_size}
             for p in root.iterdir() if p.is_file()}
    plan = {'rounds':3, 'input_digest':'a'*64, 'common_env':dict(FIXED_ENV),
            'candidate_env':{}, 'ablation':None,
            'environment':{'PATH':'/fixture', 'HOME':'/fixture', 'LANG':'C', 'LC_ALL':'C',
                           'TZ':'UTC', 'PYTHONUTF8':'1', 'PYTHONDONTWRITEBYTECODE':'1'},
            'workload':{'duration_frames':900, 'fps':30, 'width':1920, 'height':1080,
                        'seconds':30, 'html_clips':1, 'timeline':'timeline.json'},
            'platform':{'system':'Synthetic', 'release':'fixture', 'machine':'fixture', 'python':'3.12'},
            'tools':{name:{'path':'/fixture/' + name, 'sha256':'c'*64, 'size':10}
                     for name in ('python','ffmpeg','ffprobe')}}
    for role, char in (('baseline','b'), ('candidate','d')):
        plan[role] = {'repo':'/fixture/' + role, 'head':char*40, 'tree':char*40, 'clean':True,
                      'worker':{'path':'/fixture/' + role + '-worker', 'sha256':char*64, 'size':10}}
    record = {'schema':'framivra.cef-benchmark/1', 'status':'passed',
              'original_input_valid':True, 'errors':[], 'plan':plan, 'runs':[]}
    for number in range(1, 4):
        for role in ('baseline', 'candidate'):
            for state in ('cold', 'warm'):
                elapsed = {'baseline':(10.,20.,100.), 'candidate':(6.,8.,50.)}[role][number-1]
                command = {'argv':['vtx','all'], 'exit_code':0, 'timed_out':False, 'error':None,
                           'elapsed_seconds':elapsed, 'cleanup':{'errors':[]},
                           'resources':{'status':'unavailable', 'peak_tree_rss_bytes':None,
                                        'cpu_seconds_lower_bound':None, 'peak_sampled_processes':None}}
                events = {}
                evidence = copy.deepcopy(files)
                for phase, stages in (('all', ('validate','audit','prepare','gfx','video','audio','mux','qc')),
                                      ('strict', ('qc',))):
                    path = root / ('%s-%s-%s-%s.events.jsonl' % (number,role,state,phase))
                    path.write_text(_event_document(stages), encoding='utf-8')
                    events[phase] = path.name
                    evidence[path.name] = {'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                                           'size':path.stat().st_size}
                prefix = [plan['tools']['python']['path'], '-B', plan[role]['repo'] + '/vtx/vtx.py',
                          '-t', str(root / 'projects' / ('round-%03d' % number) / role / 'timeline.json')]
                command['argv'] = prefix + ['--events-jsonl', str(root / events['all']), 'all']
                strict = dict(command, argv=prefix + ['--events-jsonl', str(root / events['strict']),
                                                      'qc', '--strict'], elapsed_seconds=1.)
                row = {'round':number, 'role':role, 'cache_state':state, 'status':'passed',
                       'input_digest':plan['input_digest'], 'source_identity_valid':True,
                       'tools_identity_valid':True, 'identity_before':copy.deepcopy(plan[role]),
                       'identity_after':copy.deepcopy(plan[role]), 'tools_before':copy.deepcopy(plan['tools']),
                       'tools_after':copy.deepcopy(plan['tools']), 'command':command,
                       'strict_command':strict, 'media':copy.deepcopy(media),
                       'receipt_path':'receipt.json', 'all_receipt_path':'receipt.json',
                       'qc_path':'qc.json', 'all_qc_path':'qc.json',
                       'events_path':events['all'], 'strict_events_path':events['strict'],
                       'native_paths':[], 'evidence_files':evidence}
                record['runs'].append(row)
    return record


class ReportContracts(_TemporaryCase):
    def setUp(self):
        super().setUp()
        self.record = _report_fixture(self.root)

    def report(self, record=None):
        from scripts.gfx._bench_report import build_report
        return build_report(self.record if record is None else record, self.root)

    def test_complete_pairs_use_all_paired_ratios_and_separate_cold_warm(self):
        summary = self.report()
        self.assertTrue(summary['qualified'], summary['issues'])
        self.assertTrue(summary['goal_met'], summary['issues'])
        for state in ('cold','warm'):
            result = summary['cache_states'][state]
            self.assertEqual(result['complete_pairs'], 3)
            self.assertEqual(result['paired_ratio'], {'count':3,'median':.5,'min':.4,'max':.6})
            self.assertEqual(result['elapsed_reduction_percent'], 50)
            self.assertEqual(result['throughput_gain_percent'], 100)
            self.assertEqual(len(result['pairs']), 3)
        self.assertIsNone(summary['resources']['cold']['candidate']['peak_tree_rss_bytes']['median'])
        self.assertEqual(summary['runs'][0]['native']['status'], 'unknown')

    def test_overlapping_stages_and_waves_are_unions_not_sums(self):
        path = self.root / 'overlap.events.jsonl'
        path.write_text(_overlapping_events(), encoding='utf-8')
        self.record['runs'][0]['events_path'] = path.name
        self.record['runs'][0]['command']['argv'][-2] = str(path)
        self.record['runs'][0]['evidence_files'][path.name] = {
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest(), 'size':path.stat().st_size}
        summary = self.report()
        events = summary['runs'][0]['events']
        self.assertEqual(events['stages']['video']['union_seconds'], 2.5)
        self.assertEqual(events['stages']['audio']['union_seconds'], 1)
        self.assertEqual(events['all_stage_union_seconds'], 2.5)
        self.assertEqual(events['wave_union_seconds'], 5)
        self.assertGreater(sum(row['seconds'] for row in events['wave_jobs']), 5)
        from scripts.gfx._bench_report import render_markdown
        markdown = render_markdown(summary)
        self.assertIn('not additive', markdown)
        self.assertIn('UNRUN', markdown)
        self.assertNotIn('visual perfection: passed', markdown)

    def test_failed_unknown_nonfinite_partial_or_drifted_evidence_cannot_claim_goal(self):
        changes = {
            'video-mismatch': lambda r:r['runs'][1]['media'].update(video_digest='3'*64),
            'audio-mismatch': lambda r:r['runs'][1]['media'].update(audio_digest='4'*64),
            'failed-run': lambda r:r['runs'][1].update(status='failed'),
            'strict-failed': lambda r:r['runs'][1]['strict_command'].update(exit_code=1),
            'strict-missing': lambda r:r['runs'][1].update(strict_command=None),
            'media-check-missing': lambda r:r['runs'][1]['media'].update(checks={'video_full_decode':True}),
            'missing-wall': lambda r:r['runs'][1]['command'].pop('elapsed_seconds'),
            'zero-wall': lambda r:r['runs'][1]['command'].update(elapsed_seconds=0),
            'nan-wall': lambda r:r['runs'][1]['command'].update(elapsed_seconds=math.nan),
            'infinite-wall': lambda r:r['runs'][1]['command'].update(elapsed_seconds=math.inf),
            'partial-rounds': lambda r:r['runs'].pop(),
            'duplicate-run': lambda r:r['runs'].append(copy.deepcopy(r['runs'][0])),
            'input-drift': lambda r:r['runs'][1].update(input_digest='0'*64),
            'worker-drift': lambda r:r['runs'][1]['identity_after']['worker'].update(sha256='0'*64),
            'tool-drift': lambda r:r['runs'][1]['tools_after']['ffmpeg'].update(sha256='0'*64),
            'events-missing': lambda r:r['runs'][1].update(events_path=None),
            'strict-events-missing': lambda r:r['runs'][1].update(strict_events_path=None),
            'all-receipt-missing': lambda r:r['runs'][1].update(all_receipt_path=None),
            'original-unknown': lambda r:r.update(original_input_valid=None),
            'runner-error': lambda r:r['errors'].append('owned failure'),
            'wrong-workload': lambda r:r['plan']['workload'].update(duration_frames=360),
        }
        for name, change in changes.items():
            with self.subTest(name=name):
                record = copy.deepcopy(self.record)
                change(record)
                summary = self.report(record)
                self.assertFalse(summary['qualified'], name)
                self.assertFalse(summary['goal_met'], name)
                self.assertNotIn('NaN', json.dumps(summary, allow_nan=False))

    def test_evidence_bytes_or_partial_qc_cannot_silently_qualify(self):
        _json(self.root / 'qc.json', {'checks':[{'name':'arbitrary','pass':True}], 'skipped_probes':[]})
        summary = self.report()
        self.assertFalse(summary['goal_met'])
        self.assertTrue(any('SHA256' in issue for issue in summary['issues']), summary['issues'])
        # Rebinding the hash does not make an incomplete QC report complete.
        path = self.root / 'qc.json'
        for row in self.record['runs']:
            row['evidence_files'][path.name] = {'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                                               'size':path.stat().st_size}
        self.assertFalse(self.report()['goal_met'])

    def test_primary_cold_goal_keeps_warm_diagnostics_and_requires_complete_pairs(self):
        for row in self.record['runs']:
            if row['role'] == 'candidate' and row['cache_state'] == 'warm':
                row['command']['elapsed_seconds'] *= 3
        summary = self.report()
        self.assertTrue(summary['qualified'], summary['issues'])
        self.assertTrue(summary['cache_states']['cold']['goal_met'])
        self.assertFalse(summary['cache_states']['warm']['goal_met'])
        self.assertEqual(summary['cache_states']['warm']['paired_ratio']['median'], 1.5)
        self.assertTrue(summary['goal_met'])  # primary cold goal, not a warm speed claim
        for incomplete in ('two-rounds', 'missing-warm'):
            with self.subTest(incomplete=incomplete):
                record = copy.deepcopy(self.record)
                if incomplete == 'two-rounds':
                    record['plan']['rounds'] = 2
                    record['runs'] = [r for r in record['runs'] if r['round'] <= 2]
                else:
                    record['runs'].pop()  # one planned warm observation is missing
                summary = self.report(record)
                self.assertFalse(summary['qualified'])
                self.assertFalse(summary['goal_met'])


    def test_actual_receipt_producer_and_preinstrumentation_native_remain_readable(self):
        from vtx.pipeline.infra import Ctx
        from vtx.inspection import qc
        from tests.gfx._environment import collect
        project = self.root / 'receipt-producer'
        project.mkdir()
        _json(project / 'timeline.json', {
            'project':{'fps':30,'duration_frames':900,'width':1920,'height':1080},
            'sources':[], 'video_tracks':[{'kind':'gfx','clips':[
                {'src':'gfx.html','dst_in':0,'dur':900}]}], 'audio_tracks':[], 'subtitles':[]})
        (project / 'gfx.html').write_text('<html>receipt serialization only</html>')
        worker = _executable(self.root / 'receipt-worker', 'raise SystemExit(99)')
        # Only environment discovery is stubbed. Ctx and receipt serialization are
        # production code; no renderer, pipeline stage, or QC execution is claimed.
        with mock.patch.dict(os.environ, dict(self.env, VTX_GFX='cef', VTX_CEF_WORKER=str(worker)), clear=True), \
                mock.patch('vtx.render.gfx_environment.collect', side_effect=collect):
            context = Ctx(project / 'timeline.json')
            context.out.mkdir()
            for stage in ('validate','audit','prepare','gfx','video','audio','mux','qc'):
                context.receipt(stage, True, '6/6' if stage == 'qc' else 'owned fixture')
        produced = json.loads((context.out / 'receipt.json').read_text())
        self.assertIsInstance(produced['gfx_backend'], dict)
        self.assertEqual(produced['gfx_backend']['namespace'], 'cef-alpha')
        self.assertIn('响度归一 ≈ %g LUFS' % qc.TARGET_LUFS, _QC_NAMES)
        self.assertIn('解码域真峰 TP ≤ %g dBFS' % qc.QC_TP_MAX, _QC_NAMES)
        # 7c76ab's actual producer used the same receipt dictionary but a v1
        # identity and only FSM sync/receipts logs: no host/adapter timings.
        legacy = self.root / 'legacy.sync.jsonl'
        _json(self.root / 'legacy.receipts.json', [])
        legacy.write_text(json.dumps({'seq':1,'ts_us':100,'event':'begin',
            'drive_mode':'n','document_gen':1,'request_id':1,'target_frame_index':0,
            'state':'S0_IDLE','from':'','to':''}) + '\n')
        for revision in ('candidate', '7c76ab'):
            with self.subTest(revision=revision):
                receipt = copy.deepcopy(produced)
                if revision == '7c76ab':
                    receipt['gfx_backend']['identity'] = 'cef-alpha-exact-v1|a0|mode-n|' + 'a'*64
                    for stage in receipt['stages'].values():
                        stage['gfx_backend'] = copy.deepcopy(receipt['gfx_backend'])
                _json(self.root / 'receipt.json', receipt)
                for row in self.record['runs']:
                    for path in (self.root / 'receipt.json', legacy, self.root / 'legacy.receipts.json'):
                        row['evidence_files'][path.name] = {
                            'sha256':hashlib.sha256(path.read_bytes()).hexdigest(), 'size':path.stat().st_size}
                    row['native_paths'] = [legacy.name, 'legacy.receipts.json']
                summary = self.report()
                self.assertTrue(summary['qualified'], summary['issues'])
                self.assertTrue(summary['goal_met'])
                native = summary['runs'][0]['native']
                self.assertEqual(native['status'], 'unknown')
                self.assertEqual(native['adapter_samples'], 0)
                self.assertIsNone(native['adapter_us']['capture_wall_us']['median'])


_MEDIA_HARNESS = r'''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from scripts.gfx._bench_runtime import _verify_media
spec = json.loads(Path(sys.argv[2]).read_text())
print(json.dumps(_verify_media(**spec), allow_nan=False))
'''


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe') and os.name == 'posix',
                     'tiny mechanism test needs FFmpeg/ffprobe, not CEF')
class FullStreamMediaContracts(_TemporaryCase):
    def make_media(self, name, *, late_video=False, frequency=440, gap=False):
        project = self.root / name
        (project / 'out').mkdir(parents=True)
        command = [str(Path(shutil.which('ffmpeg')).resolve()), '-v', 'error', '-nostdin',
                   '-f', 'lavfi', '-i', 'testsrc2=size=64x48:rate=30:duration=1',
                   '-f', 'lavfi', '-i', 'sine=frequency=%d:sample_rate=48000:duration=1' % frequency]
        if late_video:
            command += ['-vf', "drawbox=x=0:y=0:w=64:h=48:color=red:t=fill:enable='gte(t,0.7)'"]
        if gap:
            command += ['-af', r'asetpts=PTS+if(between(T\,0.2\,0.4)\,0.005/TB\,0)']
        command += ['-c:v', 'mpeg4', '-q:v', '2', '-threads', '1', '-filter_threads', '1',
                    '-c:a', 'aac', '-ar', '48000', '-ac', '2', '-t', '1',
                    str(project / 'out/final.mp4')]
        result = subprocess.run(command, capture_output=True, text=True, env=self.env,
                                timeout=30, start_new_session=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return project

    def verify(self, project, suffix='evidence'):
        spec = self.root / 'media-spec.json'
        _json(spec, {'project':str(project), 'evidence_dir':str(project / suffix),
                     'ffmpeg':str(Path(shutil.which('ffmpeg')).resolve()),
                     'ffprobe':str(Path(shutil.which('ffprobe')).resolve()),
                     'timeout':10, 'env':self.env, 'expected_width':64,
                     'expected_height':48, 'expected_frames':30})
        result = self.python(_MEDIA_HARNESS, spec, timeout=40)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_real_full_stream_hash_detects_late_video_and_audio_changes(self):
        baseline = self.make_media('baseline')
        original = (baseline / 'out/final.mp4').read_bytes()
        first = self.verify(baseline)
        repeated = self.verify(baseline, 'repeat')
        video = self.verify(self.make_media('late-video', late_video=True))
        audio = self.verify(self.make_media('changed-audio', frequency=880))
        for result in (first, repeated, video, audio):
            self.assertEqual(result['status'], 'passed', result['error'])
            self.assertTrue(all(value is True for value in result['checks'].values()))
            frames = json.loads(Path(result['evidence']['video_normalized']).read_text())['frames']
            self.assertEqual(len(frames), 30)
        for key in ('video_digest','audio_digest','audio_timeline_digest'):
            self.assertEqual(first[key], repeated[key])
        self.assertNotEqual(first['video_digest'], video['video_digest'])
        self.assertEqual(first['audio_digest'], video['audio_digest'])
        self.assertEqual(first['video_digest'], audio['video_digest'])
        self.assertNotEqual(first['audio_digest'], audio['audio_digest'])
        original_frames = json.loads(Path(first['evidence']['video_normalized']).read_text())['frames']
        changed_frames = json.loads(Path(video['evidence']['video_normalized']).read_text())['frames']
        self.assertEqual(original_frames[:20], changed_frames[:20])
        self.assertNotEqual(original_frames[-1], changed_frames[-1])
        self.assertEqual((baseline / 'out/final.mp4').read_bytes(), original)

    def test_internal_audio_pts_gap_fails_even_with_same_pcm_hash(self):
        normal = self.verify(self.make_media('normal'))
        gapped = self.verify(self.make_media('internal-gap', gap=True))
        self.assertEqual(normal['status'], 'passed', normal['error'])
        self.assertEqual(normal['audio_digest'], gapped['audio_digest'])
        self.assertEqual(gapped['status'], 'failed', gapped)
        self.assertFalse(gapped['checks']['audio_timestamps'])
        self.assertIsNone(gapped['audio_timeline_digest'])


if __name__ == '__main__':
    unittest.main()
