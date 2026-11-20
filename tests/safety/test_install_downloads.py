"""Real loopback transport faults; production remains HTTPS-only."""
import contextlib
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
import urllib.parse
import urllib.request

from scripts import install_downloads as downloads


class Downloads(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name) / 'cache'
        self.body = b'fixed scientific wheel and native package\0' * 40_000
        self.sha = hashlib.sha256(self.body).hexdigest()
        self.state = dict(requests=[], online=True, drop=False, ignore=False,
                          malformed=False, etag='"stable"')
        state, body = self.state, self.body

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                state['requests'].append((self.path, self.headers.get('Range'), self.headers.get('If-Range')))
                if self.path == '/missing':
                    self.send_error(404)
                    return
                if not state['online']:
                    self.send_error(503)
                    return
                payload = b'wrong identity' if self.path == '/wrong' else body
                offset = int(self.headers['Range'].split('=')[1].split('-')[0]) if self.headers.get('Range') else 0
                if offset == len(payload) and not state['ignore']:
                    self.send_response(416)
                    self.send_header('Content-Range', 'bytes */%d' % len(payload))
                    self.end_headers()
                    return
                if state['ignore']:
                    offset = 0
                self.send_response(206 if offset else 200)
                self.send_header('Content-Length', str(len(payload) - offset))
                self.send_header('ETag', state['etag'])
                if offset:
                    self.send_header('Content-Range', 'bytes %d-%d/%d' % (
                        0 if state['malformed'] else offset, len(payload) - 1, len(payload)))
                self.end_headers()
                if state['drop']:
                    self.wfile.write(payload[offset:offset + 1_100_000])
                    self.wfile.flush()
                    state['drop'], state['online'] = False, False
                    self.close_connection = True
                else:
                    try:
                        self.wfile.write(payload[offset:])
                    except (BrokenPipeError, ConnectionResetError):
                        pass  # Client intentionally rejects bad range/validator headers.

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.url = 'http://127.0.0.1:%s' % self.server.server_port
        original = downloads.validate_url

        def test_local_url(url):
            if url.startswith(self.url + '/'):
                return urllib.parse.urlsplit(url)
            return original(url)

        patch = mock.patch.object(downloads, 'validate_url', side_effect=test_local_url)
        patch.start()
        self.addCleanup(patch.stop)
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def get(self, paths=('/payload',), size=True):
        return downloads.acquire(self.cache, [self.url + p for p in paths], self.sha,
                                 filename='fixture.whl', size=len(self.body) if size else None)

    def seed(self, length, validator='"stable"'):
        directory = self.cache / self.sha
        directory.mkdir(parents=True)
        part = directory / 'fixture.whl.part'
        part.write_bytes(self.body[:length])
        (directory / 'fixture.whl.json').write_text(json.dumps(dict(
            url=self.url + '/payload', validator=validator)), encoding='utf-8')
        return part

    def test_interrupted_invocation_retains_bytes_and_next_invocation_ranges_then_offline_cache(self):
        self.state['drop'] = True
        with self.assertRaises(downloads.DownloadError):
            self.get()
        part = self.cache / self.sha / 'fixture.whl.part'
        retained = part.stat().st_size
        self.assertGreater(retained, 1_000_000)
        self.assertLess(retained, len(self.body))
        self.state['online'] = True
        complete = self.get()
        self.assertEqual(complete.read_bytes(), self.body)
        self.assertIn(('/payload', 'bytes=%d-' % retained, '"stable"'), self.state['requests'])
        requests = list(self.state['requests'])
        self.state['online'] = False
        self.assertEqual(self.get(), complete)
        self.assertEqual(self.state['requests'], requests)

    def test_ignored_range_restarts_only_artifact_and_verifies(self):
        self.seed(500_000)
        self.state['ignore'] = True
        self.assertEqual(self.get().read_bytes(), self.body)
        self.assertIn('range-reset', (self.cache / 'downloads.jsonl').read_text())

    def test_interrupt_publishing_full_response_validator_cannot_relabel_old_bytes(self):
        part = self.seed(500_000)
        part.write_bytes(b'old representation')
        self.state.update(ignore=True, etag='"replacement"')
        publish = downloads.atomic_json

        def interrupt_after_publish(path, value):
            publish(path, value)
            raise KeyboardInterrupt

        with mock.patch.object(downloads, 'atomic_json', side_effect=interrupt_after_publish):
            with self.assertRaises(KeyboardInterrupt):
                self.get()
        self.assertEqual(part.read_bytes(), b'')
        self.state['ignore'] = False
        self.assertEqual(self.get().read_bytes(), self.body)
        self.assertEqual(self.state['requests'], [('/payload', 'bytes=18-', '"stable"'),
                                                  ('/payload', None, None)])

    def test_missing_mirror_falls_back_same_hash(self):
        self.assertEqual(self.get(('/missing', '/payload')).read_bytes(), self.body)
        self.assertEqual([x[0] for x in self.state['requests']], ['/missing', '/payload'])

    def test_unknown_size_wrong_hash_after_missing_candidate_stops_before_third_source(self):
        with self.assertRaisesRegex(downloads.IntegrityError, 'SHA256 mismatch'):
            self.get(('/missing', '/wrong', '/payload'), size=False)
        self.assertEqual([x[0] for x in self.state['requests']], ['/missing', '/wrong'])
        self.assertFalse((self.cache / self.sha / 'fixture.whl').exists())

    def test_malformed_range_or_changed_validator_cannot_append(self):
        for options in ({'malformed': True}, {'etag': '"different"'}):
            with self.subTest(options=options):
                part = self.seed(500_000) if not self.cache.exists() else self.cache / self.sha / 'fixture.whl.part'
                part.write_bytes(self.body[:500_000])
                self.state.update(options)
                with self.assertRaises(downloads.IntegrityError):
                    self.get()
                self.assertEqual(part.stat().st_size, 500_000)
                self.state.update(malformed=False, etag='"stable"')

    def test_416_can_verify_already_complete_unknown_length_partial(self):
        self.seed(len(self.body))
        self.assertEqual(self.get(size=False).read_bytes(), self.body)
        self.assertEqual(self.state['requests'][0][1], 'bytes=%d-' % len(self.body))

    def test_corrupt_complete_cache_is_not_executed_or_silently_replaced(self):
        complete = self.get()
        complete.write_bytes(b'corrupt')
        count = len(self.state['requests'])
        with self.assertRaisesRegex(downloads.IntegrityError, 'Corrupt completed'):
            self.get()
        self.assertEqual(len(self.state['requests']), count)

    def test_http_and_insecure_redirect_fail_before_any_cache_write(self):
        with self.assertRaises(downloads.IntegrityError):
            downloads.acquire(self.cache, ['http://example.invalid/a'], self.sha)
        self.assertFalse(self.cache.exists())
        with self.assertRaises(downloads.IntegrityError):
            downloads.HTTPSRedirects().redirect_request(
                urllib.request.Request('https://example.invalid/a'), None, 302, 'redirect', {},
                'http://example.invalid/a')

    def test_process_death_does_not_leave_a_stale_installation_lock(self):
        lock = Path(self.temp.name) / 'installation.lock'
        code = ('import os; from pathlib import Path; from scripts.install_downloads import advisory_lock; '
                'ctx=advisory_lock(Path(%r)); ctx.__enter__(); os._exit(0)' % str(lock))
        subprocess.run([sys.executable, '-c', code], check=True)
        self.assertTrue(lock.exists())
        with downloads.advisory_lock(lock):
            with self.assertRaisesRegex(downloads.DownloadError, 'Another installation'):
                with downloads.advisory_lock(lock):
                    pass


if __name__ == '__main__':
    unittest.main()
