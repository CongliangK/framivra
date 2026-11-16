"""Cheap mocked observability contracts; no renderer, host query or native calls."""
import ctypes
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from scripts.gfx import _bench_runtime as runtime


class DiskIOContracts(unittest.TestCase):
    def test_verified_v2_layout_and_exact_flavor(self):
        self.assertEqual(ctypes.sizeof(runtime._RusageInfoV2), 160)
        self.assertEqual(runtime._RusageInfoV2.ri_diskio_bytesread.offset, 144)
        self.assertEqual(runtime._RusageInfoV2.ri_diskio_byteswritten.offset, 152)

        def call(pid, flavor, buffer):
            self.assertEqual((pid, flavor), (123, 2))
            info = ctypes.cast(buffer, ctypes.POINTER(runtime._RusageInfoV2)).contents
            info.ri_diskio_bytesread = 17
            info.ri_diskio_byteswritten = 29
            return 0

        with mock.patch.object(runtime.sys, 'platform', 'darwin'), \
                mock.patch.object(runtime, '_libproc_rusage', return_value=call):
            self.assertEqual(runtime._process_disk_io(123), (17, 29))
        with mock.patch.object(runtime.sys, 'platform', 'darwin'), \
                mock.patch.object(runtime, '_libproc_rusage', return_value=lambda *args: -1):
            with self.assertRaises(OSError):
                runtime._process_disk_io(123)

    def test_only_owned_descendants_and_counter_maxima(self):
        sampler = runtime._ResourceSampler(10, 1)
        rows = {10: (1, 1024, 1.5), 11: (10, 2048, .5), 99: (1, 9999, 999)}
        with mock.patch.object(runtime.sys, 'platform', 'darwin'), \
                mock.patch.object(runtime, '_ps_snapshot', return_value=rows), \
                mock.patch.object(runtime, '_process_disk_io', side_effect=[(100, 20), (5, 0), (90, 40), (7, 0)]) as io:
            sampler._sample()
            sampler._sample()
            result = sampler.result()
        self.assertEqual([call.args[0] for call in io.call_args_list], [10, 11, 10, 11])
        self.assertEqual(result['peak_tree_rss_bytes'], 3072)
        self.assertEqual(result['cpu_seconds_lower_bound'], 2)
        disk = result['disk_io']
        self.assertEqual(disk['status'], 'sampled')
        self.assertEqual(disk['read_bytes_lower_bound'], 107)
        self.assertEqual(disk['written_bytes_lower_bound'], 40)
        self.assertEqual(disk['successful_process_observations'], 4)
        self.assertEqual(disk['observed_processes'], 2)

    def test_partial_unavailable_and_genuine_zero(self):
        for values, status, expected in (
                ([(0, 0), OSError()], 'partial', 0),
                ([OSError(), OSError()], 'unavailable', None)):
            sampler = runtime._ResourceSampler(10, 1)
            with mock.patch.object(runtime.sys, 'platform', 'darwin'), \
                    mock.patch.object(runtime, '_ps_snapshot', return_value={10: (1, 1, 0), 11: (10, 1, 0)}), \
                    mock.patch.object(runtime, '_process_disk_io', side_effect=values):
                sampler._sample()
                result = sampler.result()
            self.assertEqual(result['status'], 'sampled')  # Preserve RSS/CPU semantics.
            self.assertEqual(result['disk_io']['status'], status)
            self.assertEqual(result['disk_io']['read_bytes_lower_bound'], expected)
            self.assertEqual(result['disk_io']['attempted_process_observations'], 2)
            self.assertTrue(result['disk_io']['errors'])

    def test_bounded_coverage_and_unsupported(self):
        rows = {10: (1, 1, 0), **{pid: (10, 1, 0) for pid in range(11, 311)}}
        sampler = runtime._ResourceSampler(10, 1)
        with mock.patch.object(runtime.sys, 'platform', 'darwin'), \
                mock.patch.object(runtime.time, 'monotonic', return_value=1), \
                mock.patch.object(runtime, '_ps_snapshot', return_value=rows), \
                mock.patch.object(runtime, '_process_disk_io', return_value=(1, 2)) as io:
            sampler._sample()
            disk = sampler.result()['disk_io']
        self.assertEqual(io.call_count, 256)
        self.assertEqual(disk['selected_process_observations'], 301)
        self.assertEqual(disk['status'], 'partial')
        with mock.patch.object(runtime.sys, 'platform', 'linux'), \
                mock.patch.object(runtime, '_process_disk_io') as io, \
                mock.patch.object(runtime, '_ps_snapshot', return_value=rows):
            sampler = runtime._ResourceSampler(10, 1)
            sampler._sample()
            disk = sampler.result()['disk_io']
        io.assert_not_called()
        self.assertEqual(disk['status'], 'unsupported')
        self.assertIsNone(disk['read_bytes_lower_bound'])


class HostMemoryContracts(unittest.TestCase):
    PRESSURE = 'System-wide memory free percentage: 37%\n'
    SWAP = 'total = 1024.00M  used = 1.50M  free = 1022.50M (encrypted)\n'

    def test_valid_partial_unavailable_and_parse_failures(self):
        cases = (([self.PRESSURE, self.SWAP, '1\n'], 'sampled', 3),
                 ([self.PRESSURE, 'invalid', '2\n'], 'partial', 2),
                 ([self.PRESSURE, self.SWAP, '8\n'], 'partial', 2),
                 ([subprocess.TimeoutExpired('query', 2), FileNotFoundError(), ''], 'unavailable', 0))
        for outputs, status, available in cases:
            with mock.patch.object(runtime.sys, 'platform', 'darwin'), \
                    mock.patch.object(runtime, '_host_query', side_effect=outputs) as query:
                snapshot = runtime._host_memory_snapshot()
            self.assertEqual(snapshot['status'], status)
            self.assertEqual(snapshot['available_metrics'], available)
            self.assertEqual(snapshot['expected_metrics'], 3)
            if outputs[2] in ('8\n', ''):
                self.assertIsNone(snapshot['pressure_level'])
                self.assertTrue(any('pressure_level' in reason for reason in snapshot['errors']))
            self.assertEqual(query.call_args_list, [
                mock.call(['/usr/bin/memory_pressure', '-Q']),
                mock.call(['/usr/sbin/sysctl', '-n', 'vm.swapusage']),
                mock.call(['/usr/sbin/sysctl', '-n', 'kern.memorystatus_vm_pressure_level'])])
            if status == 'sampled':
                self.assertEqual(snapshot['memory_pressure']['system_free_percent'], 37)
                self.assertEqual(snapshot['swapusage']['used_bytes'], 1572864)
        for text in ('', 'System-wide memory free percentage: 101%', 'secret'):
            with self.assertRaises(ValueError):
                runtime._parse_pressure(text)
        with self.assertRaises(ValueError):
            runtime._parse_swap('total = 1M used = unknown free = 1M')
        with mock.patch.object(runtime.sys, 'platform', 'linux'), \
                mock.patch.object(runtime, '_host_query') as query:
            self.assertEqual(runtime._host_memory_snapshot()['status'], 'unsupported')
            query.assert_not_called()

    def test_kernel_pressure_level_is_separate_from_free_percentage(self):
        for value, state in ((1, 'normal'), (2, 'warning'), (4, 'critical')):
            self.assertEqual(runtime._parse_pressure_level(str(value) + '\n'),
                             {'value': value, 'state': state})
        for text in ('', '0', '3', '8', '-1', 'normal', '1 trailing'):
            with self.assertRaises(ValueError):
                runtime._parse_pressure_level(text)
        with mock.patch.object(runtime.sys, 'platform', 'darwin'), \
                mock.patch.object(runtime, '_host_query', side_effect=[self.PRESSURE, self.SWAP, FileNotFoundError()]):
            snapshot = runtime._host_memory_snapshot()
        self.assertEqual(snapshot['status'], 'partial')
        self.assertIsNone(snapshot['pressure_level'])
        self.assertEqual(snapshot['memory_pressure']['system_free_percent'], 37)
        self.assertEqual(snapshot['available_metrics'], 2)

    def test_query_timeout_output_bound_and_nonzero(self):
        def run(argv, **kwargs):
            self.assertEqual(kwargs['timeout'], 2)
            self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
            kwargs['stdout'].write(b'x' * 16385)
            return mock.Mock(returncode=0)
        with mock.patch.object(runtime.subprocess, 'run', side_effect=run):
            with self.assertRaises(ValueError):
                runtime._host_query(['/usr/bin/memory_pressure', '-Q'])
        with mock.patch.object(runtime.subprocess, 'run', return_value=mock.Mock(returncode=1)):
            with self.assertRaises(RuntimeError):
                runtime._host_query(['/usr/bin/memory_pressure', '-Q'])

    def test_snapshot_failures_do_not_mask_timeout_or_spawn_error(self):
        for failure in ('timeout', 'spawn'):
            process = mock.Mock(pid=12345)
            process.wait.side_effect = subprocess.TimeoutExpired('owned-command', 5)
            with tempfile.TemporaryDirectory() as root, \
                    mock.patch.object(runtime, '_host_memory_snapshot', side_effect=ValueError('bad metric')), \
                    mock.patch.object(runtime.subprocess, 'Popen',
                                      side_effect=OSError('spawn failed') if failure == 'spawn' else None,
                                      return_value=process), \
                    mock.patch.object(runtime.threading.Thread, 'start'), \
                    mock.patch.object(runtime, '_terminate_owned_group', return_value=-9) as cleanup:
                result = runtime.run_command(['owned-command'], root, {}, Path(root) / 'command.log', 5)
            if failure == 'timeout':
                cleanup.assert_called_once_with(process, result['cleanup'])
                self.assertTrue(result['timed_out'])
                self.assertEqual(result['exit_code'], -9)
                self.assertGreaterEqual(result['elapsed_seconds'], 0)
                self.assertIsNone(result['error'])
            else:
                cleanup.assert_not_called()
                self.assertFalse(result['timed_out'])
                self.assertIsNone(result['elapsed_seconds'])
                self.assertIsNone(result['exit_code'])
                self.assertIn('spawn failed', result['error'])
            self.assertEqual(result['host_memory']['after']['status'], 'unavailable')

    def test_metrics_failure_preserves_command_status_wall_and_cleanup(self):
        for code in (0, 23):
            clock = [0.0]

            def snapshot():
                clock[0] += 100
                raise RuntimeError('unavailable')

            def wait(**kwargs):
                clock[0] += 3
                return code

            process = mock.Mock(pid=12345)
            process.wait.side_effect = wait
            with tempfile.TemporaryDirectory() as root, \
                    mock.patch.object(runtime, '_host_memory_snapshot', side_effect=snapshot) as host, \
                    mock.patch.object(runtime.time, 'perf_counter', side_effect=lambda: clock[0]), \
                    mock.patch.object(runtime.subprocess, 'Popen', return_value=process), \
                    mock.patch.object(runtime.threading.Thread, 'start'):
                result = runtime.run_command(['owned-command'], root, {}, Path(root) / 'command.log', 5)
            self.assertEqual(host.call_count, 2)
            self.assertEqual(result['exit_code'], code)
            self.assertEqual(result['elapsed_seconds'], 3)
            self.assertEqual(result['overhead_seconds']['setup'], 100)
            self.assertEqual(result['overhead_seconds']['after_wait'], 100)
            self.assertIsNone(result['error'])
            self.assertFalse(result['timed_out'])
            self.assertTrue(result['cleanup']['leader_reaped'])
            self.assertEqual(result['host_memory']['after']['status'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
