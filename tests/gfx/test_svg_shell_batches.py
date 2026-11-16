"""Bounded SVG scheduling contracts; fake shells, never media qualification."""
import contextlib
import hashlib
import os
import re
import subprocess
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests._support import temporary_root
from tests.gfx._png import write_png
from vtx.render import gfx_svg_stage as stage


class ShellBatches(unittest.TestCase):
    def setUp(self):
        self.root = temporary_root(self)
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop('VTX_SVG_WORKERS', None)

    def inputs(self, count):
        directory = self.root / 'staging'
        directory.mkdir()
        (directory / 'fonts').mkdir()
        (directory / 'fonts/font0.ttf').write_bytes(b'font bytes')
        for i in range(count):
            (directory / f'f{i:04d}.svg').write_bytes(str(i).encode())
        return directory

    def test_exact_order_coverage_threshold_and_bounds(self):
        with mock.patch.object(stage.os, 'cpu_count', return_value=10):
            for count, expected in [(1, 1), (120, 1), (239, 1), (240, 2), (359, 2), (360, 3), (479, 3), (480, 4), (900, 4)]:
                indices = list(range(count))
                batches = stage.shell_batches(indices)
                self.assertEqual(len(batches), expected)
                self.assertEqual(sum(batches, []), indices)
                self.assertLessEqual(max(map(len, batches)) - min(map(len, batches)), 1)
            for value in ['1', '2', '3', '4']:
                os.environ['VTX_SVG_WORKERS'] = value
                self.assertEqual(len(stage.shell_batches(list(range(900)))), int(value))
            for value in ['', '0', '5', '-1', '1.0', ' 2', '02', 'auto']:
                os.environ['VTX_SVG_WORKERS'] = value
                with self.assertRaisesRegex(ValueError, 'VTX_SVG_WORKERS'):
                    stage.shell_batches(list(range(900)))
        os.environ.pop('VTX_SVG_WORKERS')
        for cpus, expected in [(None, 1), (1, 1), (2, 1), (4, 2), (128, 4)]:
            with mock.patch.object(stage.os, 'cpu_count', return_value=cpus):
                self.assertEqual(len(stage.shell_batches(list(range(900)))), expected)
        sparse = [i for i in range(900) if i % 7]
        self.assertEqual(sum(stage.shell_batches(sparse), []), sparse)

    def test_small_and_explicit_serial_use_existing_shell(self):
        for count in [2, 240]:
            with mock.patch.dict(os.environ, {'VTX_SVG_WORKERS': '1'}), \
                 mock.patch.object(stage, 'run_shell') as shell:
                self.assertEqual(stage.run_shell_batches('/inkscape', self.root, count, 64, 64,
                                 root=self.root, indices=list(range(count))), 1)
                shell.assert_called_once_with('/inkscape', self.root, count, 64, 64,
                                              root=self.root, indices=list(range(count)))

    def test_isolated_state_parallel_exact_exports_and_ordered_outputs(self):
        directory = self.inputs(480)
        processes, exported, active = [], [], set()
        mutex = threading.Lock()
        barrier = threading.Barrier(4, timeout=5)
        def launch(argv, *, cwd, env, **kwargs):
            proc = mock.Mock(returncode=0)
            proc.poll.return_value = 0
            def communicate(actions, timeout):
                indices = list(map(int, re.findall(r'file-open:f(\d+)\.svg', actions)))
                with mutex:
                    active.add(str(cwd))
                barrier.wait()
                for i in indices:
                    self.assertEqual((cwd / f'f{i:04d}.svg').read_bytes(), str(i).encode())
                    write_png(cwd / f'f{i:04d}.png', 64, 64, frame=i)
                with mutex:
                    exported.extend(indices)
                return '', ''
            proc.communicate.side_effect = communicate
            processes.append((proc, cwd, env))
            return proc
        with mock.patch.dict(os.environ, {'VTX_SVG_WORKERS': '4'}), \
             mock.patch.object(stage.subprocess, 'Popen', side_effect=launch):
            count = stage.run_shell_batches('/inkscape', directory, 480, 64, 64,
                                           root=self.root, indices=list(range(480)))
        self.assertEqual(count, 4)
        self.assertEqual(len(active), 4)
        self.assertEqual(sorted(exported), list(range(480)))
        self.assertEqual(len(set(exported)), 480)
        self.assertEqual(len(list(directory.glob('f*.png'))), 480)
        for proc, cwd, env in processes:
            for name in ['HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'INKSCAPE_PROFILE_DIR', 'FONTCONFIG_FILE']:
                self.assertTrue(Path(env[name]).is_relative_to(cwd))
            self.assertEqual((cwd / 'fonts/font0.ttf').read_bytes(), b'font bytes')
            self.assertNotEqual((cwd / 'fonts/font0.ttf').stat().st_ino,
                                (directory / 'fonts/font0.ttf').stat().st_ino)
            for stream in [proc.stdin, proc.stdout, proc.stderr]:
                stream.close.assert_called_once()
            proc.terminate.assert_not_called()

    def test_failure_launch_export_timeout_and_cancel_reap_all_before_return(self):
        for failure in ['launch', 'export', 'timeout', 'cancel', 'submit']:
            with self.subTest(failure=failure):
                # A fresh staging root for each failure; no background jobs escape.
                directory = self.root / failure
                directory.mkdir()
                (directory / 'fonts').mkdir()
                for i in range(240):
                    (directory / f'f{i:04d}.svg').write_bytes(b'svg')
                processes = []
                def launch(*args, **kwargs):
                    if failure == 'launch' and processes:
                        raise OSError('launch failed')
                    number = len(processes)
                    stopped = threading.Event()
                    proc = mock.Mock(returncode=None)
                    proc.poll.side_effect = lambda: 0 if stopped.is_set() else None
                    proc.terminate.side_effect = stopped.set
                    proc.wait.return_value = 0
                    def communicate(*args, **kwargs):
                        if number == 0 and failure in ['export', 'timeout']:
                            if failure == 'timeout':
                                raise subprocess.TimeoutExpired('inkscape', 30)
                            proc.returncode = 1
                            return '', 'export failed'
                        if not stopped.wait(5):
                            raise AssertionError('sibling was not stopped')
                        proc.returncode = -15
                        return '', ''
                    proc.communicate.side_effect = communicate
                    processes.append(proc)
                    return proc
                real_executor = stage.ThreadPoolExecutor
                class SubmitFailure(real_executor):
                    def submit(self, *args, **kwargs):
                        if len(processes) == 2:
                            raise RuntimeError('submit failed')
                        return super().submit(*args, **kwargs)
                with mock.patch.dict(os.environ, {'VTX_SVG_WORKERS': '2'}), \
                     mock.patch.object(stage.subprocess, 'Popen', side_effect=launch), \
                     mock.patch.object(stage, 'ThreadPoolExecutor', SubmitFailure if failure == 'submit' else real_executor), \
                     mock.patch.object(stage, 'as_completed', side_effect=KeyboardInterrupt) if failure == 'cancel' else contextlib.nullcontext():
                    with self.assertRaises((OSError, RuntimeError, subprocess.TimeoutExpired, KeyboardInterrupt)):
                        stage.run_shell_batches('/inkscape', directory, 240, 64, 64,
                                                root=self.root, indices=list(range(240)))
                self.assertTrue(processes)
                for proc in processes:
                    proc.terminate.assert_called_once()
                    proc.wait.assert_called_once_with(timeout=5)
                    for stream in [proc.stdin, proc.stdout, proc.stderr]:
                        stream.close.assert_called_once()
                self.assertFalse(list(directory.glob('f*.png')))
                self.assertFalse(any(t.name.startswith('svg-shell') for t in threading.enumerate()))

    def test_stop_escalation_and_exit_races_still_reap(self):
        for race in [False, True]:
            process = mock.Mock()
            process.poll.return_value = None
            process.wait.side_effect = [subprocess.TimeoutExpired('shell', 5), 0]
            if race:
                process.terminate.side_effect = ProcessLookupError
                process.kill.side_effect = ProcessLookupError
            stage._stop_shell(process)
            process.terminate.assert_called_once()
            process.kill.assert_called_once()
            self.assertEqual(process.wait.call_args_list, [mock.call(timeout=5), mock.call(timeout=5)])

    def test_missing_or_corrupt_shard_never_publishes_completion(self):
        for mode in ['missing', 'corrupt']:
            root = self.root / mode
            root.mkdir()
            clip = dict(src='manifest.json', format='svg-sequence', dur=240)
            frames = [f'f{i}.svg' for i in range(240)]
            for name in frames:
                (root / name).write_bytes(b'verified-svg')
            (root / 'font.ttf').write_bytes(b'font')
            record = dict(frames=frames, fonts=['font.ttf'],
                          files={name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                                 for name in frames + ['font.ttf']},
                          engine={'executable': '/inkscape'})
            c = SimpleNamespace(root=root, cache=root / 'cache', fps=30, w=64, h=64,
                                dry=False, tl={'video_tracks': [{'kind': 'gfx', 'clips': [clip]}]},
                                receipt=mock.Mock(), verify_inputs=mock.Mock())
            def launch(*args, cwd, **kwargs):
                process = mock.Mock(returncode=0)
                process.poll.return_value = 0
                def communicate(actions, timeout):
                    for i in map(int, re.findall(r'file-open:f(\d+)\.svg', actions)):
                        if i == 239:
                            if mode == 'corrupt':
                                (cwd / f'f{i:04d}.png').write_bytes(b'not a PNG')
                        else:
                            write_png(cwd / f'f{i:04d}.png', 64, 64)
                    return '', ''
                process.communicate.side_effect = communicate
                return process
            with mock.patch.dict(os.environ, {'VTX_SVG_WORKERS': '2'}), \
                 mock.patch.object(stage.gfx_svg, 'resolve', return_value=record), \
                 mock.patch.object(stage.subprocess, 'Popen', side_effect=launch):
                with self.assertRaises((OSError, ValueError)):
                    stage.render_stage(c)
            self.assertFalse(list(c.cache.rglob(stage.gfx_svg.COMPLETE_FILE)))
            c.receipt.assert_not_called()
            self.assertFalse(any(t.name.startswith('svg-shell') for t in threading.enumerate()))


if __name__ == '__main__':
    unittest.main()
