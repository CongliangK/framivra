"""Bounded protocol fixtures only. These tests launch/connect to no browser."""
import json
import unittest
from unittest.mock import patch
from vtx import gfx_runtime as runtime


class RuntimeTests(unittest.TestCase):
    def evidence(self, status='disabled_software', renderer=''):
        return runtime.selected_evidence({'product': 'Chrome/123.0.0.1', 'private': 'secret'},
            {'gpu': {'featureStatus': {'gpu_compositing': status},
                     'auxAttributes': {'glRenderer': renderer}, 'devices': ['private']},
             'commandLine': 'private'})

    def test_actual_fields_are_selected_without_command_line_or_private_paths(self):
        evidence = self.evidence()
        self.assertNotIn('private', repr(evidence))
        self.assertNotIn('secret', repr(evidence))
        self.assertEqual(runtime.classify(evidence), 'software_skia')
        self.assertEqual(runtime.classify(self.evidence('enabled', 'NVIDIA')), 'nvidia_egl')
        self.assertEqual(runtime.classify(self.evidence('enabled', 'ANGLE SwiftShader')), 'swiftshader')
        with self.assertRaises(ValueError):
            runtime.classify(self.evidence('enabled', 'llvmpipe'))
        with self.assertRaises(ValueError):
            runtime.classify(self.evidence('unknown', 'NVIDIA'))

    def test_request_correlation_and_message_bound(self):
        class Connection:
            def __init__(self):
                self.sent = []
                self.messages = iter([{'id': 8, 'result': {}}, {'method': 'noise'},
                                      {'id': 1, 'result': {'ok': True}}])
            def send_text(self, value):
                self.sent.append(json.loads(value))
            def recv_msg(self):
                return 1, json.dumps(next(self.messages)).encode()
        connection = Connection()
        self.assertEqual(runtime._call(connection, 1, 'Browser.getVersion'), {'ok': True})
        self.assertEqual(connection.sent[0]['method'], 'Browser.getVersion')
        connection.recv_msg = lambda: (1, b'{"method":"noise"}')
        with self.assertRaisesRegex(ValueError, 'message bound'):
            runtime._call(connection, 2, 'SystemInfo.getInfo')
        connection.recv_msg = lambda: (1, b'{"id":2,"error":{"private":"data"}}')
        with self.assertRaisesRegex(ValueError, '^runtime protocol failure$'):
            runtime._call(connection, 2, 'SystemInfo.getInfo')

    def test_socket_byte_budget_and_deadline_apply_to_buffered_http_reads(self):
        class Socket:
            def settimeout(self, timeout):
                pass
            def recv(self, size):
                return b'x' * size
            def close(self):
                pass
        with patch.object(runtime.time, 'monotonic', return_value=0):
            sock = runtime._BudgetSocket(Socket(), 1, byte_limit=4)
            stream = sock.makefile('rb')
            self.assertEqual(stream.read(4), b'xxxx')
            with self.assertRaisesRegex(ValueError, 'byte bound'):
                stream.read(1)
        with patch.object(runtime.time, 'monotonic', return_value=2):
            with self.assertRaises(TimeoutError):
                runtime._BudgetSocket(Socket(), 1).recv(1)

    def test_probe_closes_session_on_bad_runtime_response(self):
        class Connection:
            closed = False
            def send_text(self, value):
                pass
            def recv_msg(self):
                return 1, b'{"id":1,"error":{}}'
            def close(self):
                self.closed = True
        connection = Connection()
        with patch.object(runtime, '_browser_path', return_value='/devtools/browser/test'), \
             patch.object(runtime, '_ProbeConnection', return_value=connection):
            with self.assertRaises(ValueError):
                runtime.probe(1234)
        self.assertTrue(connection.closed)


    def test_raw_collector_uses_process_observer_and_preserves_unknown_page_fields(self):
        import uuid
        from unittest.mock import Mock
        connection = Mock()
        executable = {'path': '/actual/browser', 'sha256': 'a' * 64,
                      'observation_method': 'linux-proc-exe'}
        with patch.object(runtime, '_browser_path', return_value='/unused'), \
             patch.object(runtime, '_ProbeConnection', return_value=connection), \
             patch.object(runtime, '_call', side_effect=[{'product': 'Chrome/123.0.0.1'}, {'gpu': {}}]), \
             patch.object(runtime, '_process_observation', return_value=(executable, ['/actual/browser'], {'pid': 42})) as observer:
            result = runtime.collect(1234, run_id=str(uuid.uuid4()))
        observer.assert_called_once_with(connection)
        connection.close.assert_called_once()
        self.assertEqual(result['actual_executable'], executable)
        for field in ('viewport', 'color_space', 'alpha_path'):
            self.assertIsNone(result[field])
            self.assertIn(field, result['errors'])
        with self.assertRaises(TypeError):
            runtime.collect(1234, run_id=str(uuid.uuid4()), process_identity=lambda pid: {})

    def test_raw_collector_refuses_process_failure_without_exporting_exception_text(self):
        import uuid
        from unittest.mock import Mock
        connection = Mock()
        with patch.object(runtime, '_browser_path', return_value='/unused'), \
             patch.object(runtime, '_ProbeConnection', return_value=connection), \
             patch.object(runtime, '_call', side_effect=[OSError('private credential'), {}]), \
             patch.object(runtime, '_process_observation', side_effect=ValueError('private argv')):
            result = runtime.collect(1234, run_id=str(uuid.uuid4()))
        self.assertIsNone(result['actual_executable'])
        self.assertIsNone(result['browser_version'])
        self.assertEqual(result['errors']['actual_executable'], 'process_identity_unavailable')
        self.assertNotIn('private', repr(result))
        with patch.object(runtime, '_browser_path', side_effect=OSError('private')):
            result = runtime.collect(1234, run_id=str(uuid.uuid4()))
        self.assertIsNone(result['system_info'])
        self.assertEqual(result['errors']['system_info'], 'root_session_unavailable')

    def test_browser_pid_must_be_unique_positive_integer(self):
        for candidates in ([], [{'type': 'browser', 'id': True}],
                [{'type': 'browser', 'id': 1}, {'type': 'browser', 'id': 2}],
                [{'type': 'renderer', 'id': 42}], [{'type': 'browser', 'id': 0}]):
            with patch.object(runtime, '_call', return_value={'processInfo': candidates}):
                with self.assertRaises(ValueError):
                    runtime._browser_pid(None, 3)
        with patch.object(runtime, '_call', return_value={'processInfo': [{'type': 'browser', 'id': 42}]}):
            self.assertEqual(runtime._browser_pid(None, 3), 42)

    def test_lifetime_argv_executable_and_second_cdp_pid_are_rechecked(self):
        from vtx import gfx_runtime_executable, process_samples
        from unittest.mock import Mock
        for change in ('none', 'start', 'inode', 'opened_inode', 'argv', 'cdp'):
            reader = Mock()
            reader.identity.side_effect = [(42, 10), (42, 11) if change == 'start' else (42, 10), (42, 10)]
            with patch.object(process_samples, 'ProcReader', return_value=reader), \
                 patch.object(runtime, '_browser_pid', side_effect=[42, 43 if change == 'cdp' else 42]), \
                 patch.object(runtime, '_exe_stat', side_effect=[(1, 2), (1, 3) if change == 'inode' else (1, 2)]), \
                 patch.object(runtime, '_argv', side_effect=[['browser'], ['changed'] if change == 'argv' else ['browser']]), \
                 patch.object(gfx_runtime_executable, 'observed_executable', return_value={'sha256': 'a' * 64, '_stat_identity': (1, 9) if change == 'opened_inode' else (1, 2)}) as hash_exe:
                if change == 'none':
                    result = runtime._process_observation(None)
                    self.assertEqual(result[2]['pid'], 42)
                    self.assertEqual(result[2]['start_ticks'], 10)
                else:
                    with self.assertRaises(ValueError):
                        runtime._process_observation(None)
                self.assertEqual(hash_exe.call_args.args, (42,))

    def test_strict_bounded_wire_fragmentation_and_control_frames(self):
        def connection(data):
            obj = runtime._ProbeConnection.__new__(runtime._ProbeConnection)
            obj._rbuf = bytearray(data)
            obj.sock = None
            obj._send_frame = lambda *args: None
            return obj
        self.assertEqual(connection(b'\x01\x01a\x89\x00\x80\x01b').recv_msg(), (1, b'ab'))
        for malformed in (b'\x80\x00', b'\x01\x00\x81\x00', b'\x89\x7e\x00\x7e',
                          b'\xc1\x00', b'\x81\x80', b'\x82\x00',
                          b'\x81\x7f' + (runtime.MAX_SESSION_BYTES + 1).to_bytes(8, 'big')):
            with self.assertRaises(ValueError):
                connection(malformed).recv_msg()
        with self.assertRaises(ValueError):
            runtime._json(b'{"x":NaN}')

    def test_bool_id_cannot_match_integer_request(self):
        from unittest.mock import Mock
        connection = Mock()
        connection.recv_msg.side_effect = [(1, b'{"id":true,"result":{"wrong":true}}'),
                                          (1, b'{"id":1,"result":{"right":true}}')]
        self.assertEqual(runtime._call(connection, 1, 'Browser.getVersion'), {'right': True})

    def test_argv_is_bounded_and_requires_nul_terminated_utf8(self):
        import io
        for data in (b'', b'browser', b'\xff\0', b'x' * 65536 + b'\0', b'\0'):
            with patch.object(runtime.Path, 'open', return_value=io.BytesIO(data)):
                with self.assertRaises((ValueError, UnicodeError)):
                    runtime._argv(42)
        with patch.object(runtime.Path, 'open', return_value=io.BytesIO(b'browser\0--flag\0')):
            self.assertEqual(runtime._argv(42), ['browser', '--flag'])

    def test_executable_primitive_hashes_opened_inode_with_size_and_deadline_limits(self):
        from vtx import gfx_runtime_executable as executable
        import hashlib
        from types import SimpleNamespace
        fake_stat = SimpleNamespace(st_mode=0o100644, st_size=3, st_dev=1, st_ino=2,
                                    st_mtime_ns=3, st_ctime_ns=4)
        for mode in ('ok', 'deadline', 'oversize', 'changed'):
            changed_stat = SimpleNamespace(**vars(fake_stat))
            if mode == 'changed':
                changed_stat.st_ino = 9
            with patch.object(executable.os, 'open', return_value=5), \
                 patch.object(executable.os, 'close') as close, \
                 patch.object(executable.os, 'fstat', return_value=fake_stat), \
                 patch.object(executable.os, 'readlink', return_value='/actual/browser'), \
                 patch.object(executable.os, 'read', side_effect=[b'abc', b'']), \
                 patch.object(executable.Path, 'stat', return_value=changed_stat), \
                 patch.object(executable.time, 'monotonic', return_value=2 if mode == 'deadline' else 0), \
                 patch.object(executable, 'MAX_EXECUTABLE_BYTES', 2 if mode == 'oversize' else 10):
                if mode == 'ok':
                    result = executable.observed_executable(42, deadline=1)
                    self.assertEqual(result['sha256'], hashlib.sha256(b'abc').hexdigest())
                    self.assertEqual(result['observation_method'], 'linux-proc-exe')
                else:
                    with self.assertRaises(executable.EnvironmentIdentityError):
                        executable.observed_executable(42, deadline=1)
                close.assert_called_once_with(5)

    def test_browser_root_discovery_accepts_only_local_same_port_endpoint(self):
        from unittest.mock import Mock
        valid = 'ws://127.0.0.1:1234/devtools/browser/12345678-1234-1234-1234-123456789abc'
        for address in (valid, valid.replace('127.0.0.1', 'remote.invalid'),
                        valid.replace(':1234', ':1235'), valid + '?secret',
                        valid.replace('127.0.0.1', 'user@127.0.0.1'),
                        valid.replace('/browser/', '/page/')):
            connection, response = Mock(), Mock()
            connection.getresponse.return_value = response
            response.status = 200
            response.read.return_value = json.dumps({'webSocketDebuggerUrl': address}).encode()
            with patch.object(runtime.http.client, 'HTTPConnection', return_value=connection):
                if address == valid:
                    self.assertEqual(runtime._browser_path(1234), '/devtools/browser/12345678-1234-1234-1234-123456789abc')
                else:
                    with self.assertRaises(ValueError):
                        runtime._browser_path(1234)
            connection.close.assert_called_once()
            response.read.assert_called_once_with(runtime.MAX_HTTP_BYTES + 1)

    def test_ping_stream_is_bounded_by_total_session_bytes(self):
        import io
        from unittest.mock import Mock
        sock = Mock()
        wire = io.BytesIO(b'\x89\x00' * 20)
        sock.recv.side_effect = wire.read
        connection = runtime._ProbeConnection.__new__(runtime._ProbeConnection)
        connection._rbuf = bytearray()
        with patch.object(runtime.time, 'monotonic', return_value=0):
            connection.sock = runtime._BudgetSocket(sock, 1, byte_limit=8)
            with self.assertRaisesRegex(ValueError, 'byte bound'):
                connection.recv_msg()
        self.assertEqual(sock.sendall.call_count, 1)
        sent = sum(len(call.args[0]) for call in sock.sendall.call_args_list)
        self.assertEqual(wire.tell() + sent, 8)

    def test_all_nonfinite_json_numbers_reject_and_finite_artifacts_serialize(self):
        import uuid
        from unittest.mock import Mock
        for number in ('1e400', '-1e400', 'NaN', 'Infinity', '-Infinity',
                       '1e308', '-1e308', '1.5e-30', '0.0'):
            valid = number in ('1e308', '-1e308', '1.5e-30', '0.0')
            wire = ('{"id":2,"result":{"nested":[{"number":' + number + '}]}}').encode()
            connection = Mock()
            connection.recv_msg.side_effect = [(1, b'{"id":1,"result":{"product":"Chrome/123.0.0.1"}}'), (1, wire)]
            with patch.object(runtime, '_browser_path', return_value='/unused'), \
                 patch.object(runtime, '_ProbeConnection', return_value=connection), \
                 patch.object(runtime, '_process_observation', side_effect=ValueError('unavailable')):
                result = runtime.collect(1234, run_id=str(uuid.uuid4()))
            if valid:
                self.assertEqual(result['system_info']['nested'][0]['number'], float(number))
                self.assertNotIn('system_info', result['errors'])
            else:
                self.assertIsNone(result['system_info'])
                self.assertEqual(result['errors']['system_info'], 'probe_failed')
            json.dumps(result, allow_nan=False)

    def test_text_payload_requires_strict_utf8(self):
        from unittest.mock import Mock
        text = '{"id":1,"result":{"text":"中文"}}'
        for encoding in ('utf-16', 'utf-16-le', 'utf-32'):
            connection = Mock()
            connection.recv_msg.return_value = (1, text.encode(encoding))
            with self.assertRaises((ValueError, UnicodeError)):
                runtime._call(connection, 1, 'Browser.getVersion')
        connection.recv_msg.return_value = (1, text.encode('utf-8'))
        self.assertEqual(runtime._call(connection, 1, 'Browser.getVersion'), {'text': '中文'})
        with self.assertRaises(UnicodeError):
            runtime._json(b'{"x":"\xff"}')

    def test_send_and_receive_share_budget_without_extending_deadline(self):
        from unittest.mock import Mock
        sock = Mock()
        sock.recv.return_value = b'ab'
        with patch.object(runtime.time, 'monotonic', return_value=0):
            budget = runtime._BudgetSocket(sock, 1, byte_limit=6)
            budget.sendall(b'abc')
            self.assertEqual(budget.recv(3), b'ab')
            with self.assertRaisesRegex(ValueError, 'byte bound'):
                budget.sendall(b'xx')
            self.assertEqual(budget.remaining, 1)
            budget.sendall(b'x')
            with self.assertRaisesRegex(ValueError, 'byte bound'):
                budget.recv(1)
        self.assertEqual(sock.sendall.call_count, 2)
        with patch.object(runtime.time, 'monotonic', return_value=2):
            with self.assertRaises(TimeoutError):
                budget.sendall(b'')
        self.assertEqual(budget.deadline, 1)
