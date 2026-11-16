"""Owned CDP schema negotiation; all I/O is mocked, no browser is launched."""
import base64
import io
import json
import socket
import unittest
from unittest import mock

from vtx.render import gfx_render as gfx
from vtx.render.gfx_dependencies import DependencyError


def protocol(parameters):
    return {'domains': [{'domain': 'Page', 'commands': [
        {'name': 'captureScreenshot', 'parameters': parameters}]}]}


SPEED = {'name': 'optimizeForSpeed', 'type': 'boolean', 'optional': True}
FORMAT = {'name': 'format', 'type': 'string', 'optional': True}


class ScreenshotSchemaTests(unittest.TestCase):
    def test_enabled_and_explicit_unsupported(self):
        self.assertEqual(gfx._screenshot_speed_schema(protocol([FORMAT, SPEED])), 'enabled')
        self.assertEqual(gfx._screenshot_speed_schema(protocol([FORMAT])), 'unsupported')

    def test_unknown_schema_is_not_unsupported(self):
        cases = [None, {}, {'domains': []}, protocol(None), protocol([None]),
                 protocol([FORMAT, FORMAT]), protocol([{'name': 'format'}]),
                 protocol([dict(SPEED, type='string')]),
                 protocol([dict(SPEED, optional=False)]),
                 protocol([dict(SPEED, optional=1)])]
        duplicate = protocol([SPEED]); duplicate['domains'] *= 2; cases.append(duplicate)
        duplicate = protocol([SPEED]); duplicate['domains'][0]['commands'] *= 2; cases.append(duplicate)
        for value in cases:
            with self.subTest(value=value):
                self.assertEqual(gfx._screenshot_speed_schema(value), 'unknown-schema')


class ScreenshotProbeTests(unittest.TestCase):
    def connection(self, response):
        response.status = 200
        connection = mock.Mock()
        connection.getresponse.return_value = response
        return connection

    def probe(self, data):
        response = io.BytesIO(data)
        connection = self.connection(response)
        with mock.patch.object(gfx, '_ProtocolProbeConnection', return_value=connection):
            result = gfx._probe_screenshot_speed(1234)
        self.assertTrue(response.closed)
        connection.request.assert_called_once_with('GET', '/json/protocol',
                                                   headers={'Connection': 'close'})
        connection.close.assert_called_once()
        return result

    def test_enabled_unsupported_and_malformed_are_distinct(self):
        for params, expected in (([FORMAT, SPEED], 'enabled'), ([FORMAT], 'unsupported')):
            self.assertEqual(self.probe(json.dumps(protocol(params)).encode()), expected)
        for data in (b'not-json', b'\xff', b'{}'):
            self.assertEqual(self.probe(data), 'unknown-schema')

    def test_io_and_timeout_remain_unknown(self):
        cases = [(OSError('private path'), 'unknown-io'),
                 (socket.timeout('private path'), 'unknown-timeout'),
                 (gfx.http.client.BadStatusLine('owned'), 'unknown-io')]
        for error, expected in cases:
            connection = mock.Mock(); connection.request.side_effect = error
            with self.subTest(error=error), mock.patch.object(
                    gfx, '_ProtocolProbeConnection', return_value=connection):
                self.assertEqual(gfx._probe_screenshot_speed(1234), expected)
            connection.close.assert_called_once()

    def test_read_error_closes_response(self):
        for error, expected in ((OSError('owned'), 'unknown-io'),
                                (TimeoutError('owned'), 'unknown-timeout')):
            response = mock.MagicMock()
            response.__enter__.return_value = response
            response.read1.side_effect = error
            with mock.patch.object(gfx, '_ProtocolProbeConnection', return_value=self.connection(response)):
                self.assertEqual(gfx._probe_screenshot_speed(1234), expected)
            response.__exit__.assert_called_once()

    def test_byte_budget_and_read_ahead_are_bounded(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        sizes = []
        def read(n):
            sizes.append(n)
            return b'x' * n
        response.read1.side_effect = read
        with mock.patch.object(gfx, '_ProtocolProbeConnection', return_value=self.connection(response)):
            self.assertEqual(gfx._probe_screenshot_speed(1234), 'unknown-size')
        self.assertEqual(sum(sizes), 4 * 1024 * 1024 + 1)
        self.assertLessEqual(max(sizes), 65536)
        response.__exit__.assert_called_once()

    def test_aggregate_deadline_even_with_successful_short_reads(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read1.return_value = b'x'
        with mock.patch.object(gfx, '_ProtocolProbeConnection', return_value=self.connection(response)), \
                mock.patch.object(gfx.time, 'monotonic', side_effect=[0, .1, .2, .9, 1.01]):
            self.assertEqual(gfx._probe_screenshot_speed(1234), 'unknown-timeout')
        self.assertEqual(response.read1.call_count, 2)
        response.__exit__.assert_called_once()

    def test_exhausted_budget_does_not_open(self):
        with mock.patch.object(gfx, '_ProtocolProbeConnection') as opened:
            self.assertEqual(gfx._probe_screenshot_speed(1234, 0), 'unknown-timeout')
        opened.assert_not_called()

    def test_cancellation_is_not_suppressed(self):
        connection = mock.Mock(); connection.request.side_effect = KeyboardInterrupt
        with mock.patch.object(gfx, '_ProtocolProbeConnection', return_value=connection):
            with self.assertRaises(KeyboardInterrupt): gfx._probe_screenshot_speed(1234)
        connection.close.assert_called_once()

    def test_http_error_or_redirect_is_unknown_and_not_followed(self):
        for status in (302, 404, 500):
            response = mock.MagicMock(); response.__enter__.return_value = response
            connection = self.connection(response); response.status = status
            with mock.patch.object(gfx, '_ProtocolProbeConnection', return_value=connection):
                self.assertEqual(gfx._probe_screenshot_speed(1234), 'unknown-io')
            response.read1.assert_not_called()
            self.assertEqual(connection.request.call_count, 1)
            connection.close.assert_called_once()

    def test_raw_reader_deadline_and_wire_limit_cover_header_trickles(self):
        sock = mock.Mock(); stream = sock.makefile.return_value
        stream.readinto.return_value = 1
        reader = gfx._ProtocolProbeReader(sock, 1)
        with mock.patch.object(gfx.time, 'monotonic', side_effect=[.25, .75, 1.01]):
            self.assertEqual(reader.readinto(bytearray(8)), 1)
            self.assertEqual(reader.readinto(bytearray(8)), 1)
            with self.assertRaises(TimeoutError): reader.readinto(bytearray(8))
        self.assertEqual(sock.settimeout.call_args_list, [mock.call(.75), mock.call(.25)])
        self.assertEqual(stream.readinto.call_count, 2)
        sock.makefile.assert_called_once_with('rb', buffering=0)
        reader.remaining_bytes = 0
        with mock.patch.object(gfx.time, 'monotonic', return_value=0):
            with self.assertRaises(gfx._ProtocolProbeTooLarge): reader.readinto(bytearray(8))

    def test_actual_http_parser_reads_body_after_connection_close_and_chunking(self):
        body = json.dumps(protocol([FORMAT, SPEED])).encode()
        packets = [
            b'HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: '
            + str(len(body)).encode() + b'\r\n\r\n' + body,
            b'HTTP/1.1 200 OK\r\nConnection: close\r\nTransfer-Encoding: chunked\r\n\r\n'
            + hex(len(body))[2:].encode() + b'\r\n' + body + b'\r\n0\r\n\r\n']
        for packet in packets:
            stream = io.BytesIO(packet)
            sock = mock.Mock(); sock.makefile.return_value = stream
            connection = gfx._ProtocolProbeConnection(1234, gfx.time.monotonic() + 1, 1)
            with mock.patch.object(connection, '_create_connection', return_value=sock), \
                    mock.patch.object(gfx, '_ProtocolProbeConnection', return_value=connection):
                self.assertEqual(gfx._probe_screenshot_speed(1234), 'enabled')
            self.assertTrue(stream.closed)
            sock.close.assert_called_once()

    def test_connection_uses_only_owned_loopback_and_deadline_socket(self):
        connection = gfx._ProtocolProbeConnection(1234, 2, 1)
        sock = mock.Mock()
        with mock.patch.object(connection, '_create_connection', return_value=sock) as connect:
            connection.connect()
        self.assertEqual(connect.call_args.args[0], ('127.0.0.1', 1234))
        self.assertIsInstance(connection.sock, gfx._ProtocolProbeSocket)
        with mock.patch.object(gfx.time, 'monotonic', return_value=.5):
            connection.sock.sendall(b'owned')
        sock.settimeout.assert_called_with(1.5)
        with mock.patch.object(gfx.time, 'monotonic', return_value=2):
            with self.assertRaises(TimeoutError): connection.sock.sendall(b'late')
        sock.sendall.assert_called_once_with(b'owned')
        connection.close()
        sock.close.assert_called_once()


class ScreenshotDispatchTests(unittest.TestCase):
    def tab(self, enabled=None):
        tab = gfx.Tab.__new__(gfx.Tab)
        if enabled is not None: tab.optimize_for_speed = enabled
        tab.call = mock.Mock(return_value={'data': base64.b64encode(b'png').decode()})
        return tab

    def test_default_and_flag_kwargs_only(self):
        for enabled in (None, False, True):
            tab = self.tab(enabled)
            self.assertEqual(tab.shot(), b'png')
            expected = dict(format='png', timeout=30)
            if enabled: expected['optimizeForSpeed'] = True
            tab.call.assert_called_once_with('Page.captureScreenshot', **expected)

    def test_actual_errors_propagate_without_retry_or_reclassification(self):
        errors = [RuntimeError("Page.captureScreenshot: {'code': -32602}"),
                  RuntimeError('strict request enforcement failed'),
                  DependencyError('owned policy error'), TimeoutError('owned timeout'),
                  ConnectionError('owned connection'), OSError('owned io'),
                  json.JSONDecodeError('owned json', '', 0), KeyboardInterrupt()]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                tab = self.tab(True); tab.call.side_effect = error
                with self.assertRaises(type(error)) as caught: tab.shot()
                self.assertIs(caught.exception, error)
                self.assertEqual(tab.call.call_count, 1)

    def test_legacy_constructor_default_remains_compatible(self):
        ws = mock.Mock()
        with mock.patch.object(gfx, 'WSConn', return_value=ws), \
                mock.patch.object(gfx.Tab, 'call') as call:
            tab = gfx.Tab(1234, '/owned', 640, 480)
        self.assertFalse(tab.optimize_for_speed)
        self.assertEqual([c.args[0] for c in call.call_args_list],
                         ['Page.enable', 'Emulation.setDeviceMetricsOverride'])

    def test_browser_passes_only_enabled_capability_to_tabs(self):
        browser = gfx.Browser(); browser.port = 1234; browser.tab_ws_urls = ['/one', '/two']
        for status in ('enabled', 'unsupported', 'unknown-schema', 'unknown-io', 'unknown-timeout'):
            browser.screenshot_speed_capability = status
            with mock.patch.object(gfx, 'Tab') as tab:
                browser.open_tabs()
            self.assertEqual(tab.call_count, 2)
            for call in tab.call_args_list:
                self.assertEqual(call.kwargs, {'optimize_for_speed': status == 'enabled'})

    def test_restart_probes_once_per_generation_and_resets_before_start(self):
        browser = gfx.Browser('/owned/chrome', tabs=1)
        states = []
        sock = mock.Mock(); sock.getsockname.return_value = ('127.0.0.1', 1234)
        def response(request, **kwargs):
            return io.BytesIO(json.dumps({'webSocketDebuggerUrl': 'ws://127.0.0.1:1234/owned'}).encode())
        with mock.patch.object(browser, '_find_chrome', side_effect=lambda: states.append(
                browser.screenshot_speed_capability)), \
                mock.patch.object(gfx.socket, 'socket', return_value=sock), \
                mock.patch.object(gfx.tempfile, 'mkdtemp', return_value='/owned/profile'), \
                mock.patch.object(gfx.subprocess, 'Popen'), \
                mock.patch.object(gfx.urllib.request, 'urlopen', side_effect=response), \
                mock.patch.object(gfx, '_probe_screenshot_speed',
                                  side_effect=['enabled', 'unsupported']) as probe, \
                mock.patch.object(browser, 'stop'), \
                mock.patch('builtins.print') as diagnostic:
            browser.start()
            self.assertEqual(browser.screenshot_speed_capability, 'enabled')
            browser.hard_restart()
        self.assertEqual(states, ['unknown-unprobed', 'unknown-unprobed'])
        self.assertEqual(browser.screenshot_speed_capability, 'unsupported')
        self.assertEqual(browser.generation, 1)
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(diagnostic.call_args_list, [
            mock.call('  CDP screenshot optimizeForSpeed: enabled'),
            mock.call('  CDP screenshot optimizeForSpeed: unsupported')])


if __name__ == '__main__':
    unittest.main()
