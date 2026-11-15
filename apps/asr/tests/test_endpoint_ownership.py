#!/usr/bin/env python3
"""#140 owned endpoint regressions: self-created loopback fixtures, zero models."""
import ast
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ASR_DIR = Path(__file__).resolve().parent.parent
ROOT = ASR_DIR.parent.parent
sys.path.insert(0, str(ASR_DIR))
import client
import independence

# An exact-script fixture substitutes for sidecar_server, never imports it or
# any ASR runtime dependency. Its no-listen mode retains valid process identity.
FIXTURE = r'''
import argparse, json, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
p = argparse.ArgumentParser()
p.add_argument('--port', type=int)
p.add_argument('--log')
p.add_argument('--redirect')
p.add_argument('--no-listen', action='store_true')
a = p.parse_args()
if a.no_listen:
    print('ready', flush=True)
    while True: time.sleep(60)
class H(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def reply(self):
        n = int(self.headers.get('Content-Length', 0))
        payload = self.rfile.read(n).decode() if n else None
        with open(a.log, 'a') as log:
            log.write(json.dumps([self.command, self.path, payload]) + '\n')
        body = b'{"status":"ok","text":"fixture","model":"fixture"}'
        self.send_response(302 if a.redirect else 200)
        if a.redirect: self.send_header('Location', a.redirect)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    do_GET = reply
    do_POST = reply
s = ThreadingHTTPServer(('127.0.0.1', a.port), H)
print('ready', flush=True)
s.serve_forever()
'''


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='asr-endpoint-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {'HOME': str(self.root), 'PATH': os.defpath,
                    'VTX_CONFIG': str(self.root / 'absent.json')}
        source = mock.patch.object(independence, 'ASR_DIR', self.root)
        source.start()
        self.addCleanup(source.stop)
        state = mock.patch.object(independence, 'project_state_root', return_value=self.root / 'state')
        state.start()
        self.addCleanup(state.stop)
        environment = mock.patch.dict(os.environ, self.env, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.script = self.root / 'sidecar_server.py'
        self.script.write_text(FIXTURE, encoding='utf-8')
        self.procs = []
        self.addCleanup(self.reap)

    def reap(self):
        for proc in self.procs:
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=5)
            proc.stdout.close()
            proc.stderr.close()

    def spawn(self, *, port=None, own=True, no_listen=False, redirect=None):
        port = port or free_port()
        log = self.root / ('requests-%d-%d.jsonl' % (port, len(self.procs)))
        script = self.script
        if not own:
            script = self.root / 'foreign.py'
            script.write_text(FIXTURE, encoding='utf-8')
        cmd = [sys.executable, '-I', str(script), '--port', str(port), '--log', str(log)]
        if no_listen:
            cmd.append('--no-listen')
        if redirect:
            cmd += ['--redirect', redirect]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=self.env, start_new_session=True)
        self.procs.append(proc)
        self.assertEqual(proc.stdout.readline().strip(), 'ready', 'fixture failed to become ready')
        return SimpleNamespace(proc=proc, port=port, log=log, url='http://127.0.0.1:%d' % port)

    def requests(self, peer):
        return [json.loads(line) for line in peer.log.read_text().splitlines()] if peer.log.exists() else []

    def own(self, peer):
        independence.write_pid_record(peer.port, peer.proc.pid)
        return {'asr': {'enabled': True, 'base_url': peer.url}}

    def assert_rejected(self, url):
        with self.assertRaises(independence.AsrIndependenceError):
            independence.attribute_endpoint(url)
        self.assertIsNone(client.health(url))
        for call in (lambda: client.warm(url, cfg={'asr': {'enabled': True}}),
                     lambda: client.transcribe(url, '/not-read.wav', cfg={'asr': {'enabled': True}})):
            with self.assertRaises(independence.AsrIndependenceError):
                call()

    def test_foreign_listener_direct_callers_ensure_stop_zero_http(self):
        peer = self.spawn(own=False)
        self.assert_rejected(peer.url)
        with mock.patch.object(client, 'probe_python', side_effect=AssertionError('no probe')), \
                mock.patch('os.kill', side_effect=AssertionError('no signal')):
            self.assertFalse(client.ensure_sidecar({'asr': {'enabled': True}}, peer.url, verbose=False))
            self.assertFalse(client.stop_sidecar(peer.url, cfg={}))
        self.assertEqual(self.requests(peer), [])

    def test_valid_local_pid_but_wrong_listener_zero_http(self):
        peer = self.spawn(own=False)
        attributed = self.spawn(port=peer.port, no_listen=True)
        self.own(attributed)
        self.assertTrue(independence.pid_owns_sidecar(attributed.proc.pid, peer.port,
                         independence.pid_start_time(attributed.proc.pid)))
        self.assert_rejected(peer.url)
        self.assertFalse(client.ensure_sidecar({'asr': {'enabled': True}}, peer.url, verbose=False))
        self.assertEqual(self.requests(peer), [])

    def test_localhost_offline_owned_listener_and_direct_payloads(self):
        peer = self.spawn()
        cfg = self.own(peer)
        url = 'http://localhost:%d/' % peer.port
        with mock.patch('socket.getaddrinfo', side_effect=AssertionError('DNS forbidden')):
            # HTTPConnection receives the normalized numeric address; CPython's
            # create_connection still calls getaddrinfo for numeric addresses.
            self.assertEqual(independence.attribute_endpoint(url)['base_url'], peer.url)
        self.assertTrue(client.health(url))
        self.assertEqual(client.warm(url, cfg=cfg)['text'], 'fixture')
        self.assertEqual(client.transcribe(url, '/not-read.wav', timestamps=True,
                                          language='Chinese', cfg=cfg)['text'], 'fixture')
        with mock.patch.object(client, 'probe_python', side_effect=AssertionError('no spawn')):
            self.assertTrue(client.ensure_sidecar(cfg, url, verbose=False))
        requests = self.requests(peer)
        self.assertEqual([r[:2] for r in requests], [['GET', '/health'], ['POST', '/warm'],
                         ['POST', '/transcribe'], ['GET', '/health']])
        self.assertEqual(json.loads(requests[2][2]),
                         {'audio': '/not-read.wav', 'timestamps': True, 'language': 'Chinese'})

    def test_remote_same_port_and_invalid_urls_rejected_offline(self):
        peer = self.spawn()
        self.own(peer)
        urls = ['http://192.0.2.1:%d' % peer.port, 'http://example.test:%d' % peer.port,
                'http://127.0.0.1:%d/prefix' % peer.port,
                'http://user:pass@127.0.0.1:%d' % peer.port,
                'http://127.0.0.1:%d/?x=1' % peer.port,
                'http://127.0.0.1:%d/#x' % peer.port,
                'http://127.0.0.1:18081', 'http://localhost:18081/',
                'http://127.0.0.1:0', 'http://127.0.0.1:',
                'http://127.0.0.1:65536', 'http://127.0.0.1:\n80']
        with mock.patch('socket.socket', side_effect=AssertionError('no connection')), \
                mock.patch('socket.getaddrinfo', side_effect=AssertionError('no DNS')):
            for url in urls:
                with self.subTest(url=url):
                    for call in (lambda: client.health(url),
                                 lambda: client.ensure_sidecar({'asr': {'enabled': True}}, url),
                                 lambda: client.stop_sidecar(url, cfg={}),
                                 lambda: client.warm(url, cfg={'asr': {'enabled': True}}),
                                 lambda: client.transcribe(url, '/not-read.wav', cfg={'asr': {'enabled': True}})):
                        with self.assertRaises(independence.AsrIndependenceError):
                            call()
        self.assertEqual(self.requests(peer), [])

    def test_proxy_environment_ignored_and_redirect_not_followed(self):
        foreign = self.spawn(own=False)
        peer = self.spawn(redirect=foreign.url + '/stolen')
        cfg = self.own(peer)
        env = {key: foreign.url for key in ('http_proxy', 'https_proxy', 'all_proxy',
                                            'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY')}
        with mock.patch.dict(os.environ, env):
            self.assertIsNone(client.health(peer.url))
            with self.assertRaises(client.ClientError) as error:
                client.warm(peer.url, cfg=cfg)
            self.assertEqual(error.exception.status, 302)
        self.assertEqual(len(self.requests(peer)), 2)
        self.assertEqual(self.requests(foreign), [])

    def test_startup_wrong_peer_never_health_probed(self):
        foreign = self.spawn(own=False)
        child = self.spawn(port=foreign.port, no_listen=True)
        model = self.root / 'model'
        model.mkdir()
        (model / 'model.safetensors').write_bytes(b'fixture-only')
        real_listeners = independence.endpoint_listeners
        calls = []
        def initially_free(url):
            calls.append(url)
            return set() if len(calls) == 1 else real_listeners(url)
        # Simulate a foreign listener winning the bind after the initial free
        # port check; Popen returns our exact-script, non-listening child.
        with mock.patch.object(independence, 'endpoint_listeners', side_effect=initially_free), \
                mock.patch.object(client, 'probe_python', return_value=sys.executable), \
                mock.patch.object(client, 'pick_model', return_value=str(model)), \
                mock.patch.object(client, 'probe_aligner', return_value=None), \
                mock.patch.object(client, 'SIDECAR_LOG', self.root / 'sidecar.log'), \
                mock.patch.object(client.subprocess, 'Popen', return_value=child.proc), \
                mock.patch.object(client.time, 'sleep'):
            self.assertFalse(client.ensure_sidecar({'asr': {'enabled': True, 'device_map': 'cpu'}},
                                                  foreign.url, start_timeout=0.03, verbose=False))
        self.assertEqual(self.requests(foreign), [])

    def test_expected_spawn_identity_cannot_be_replaced_by_other_owned_peer(self):
        peer = self.spawn()
        self.own(peer)
        record = independence.read_pid_record(peer.port)
        different = dict(record, pid=record['pid'] + 1)
        self.assertIsNone(client.health(peer.url, expected_record=different))
        self.assertEqual(self.requests(peer), [])

    def test_cli_flag_positions_remote_same_port_and_foreign_listener(self):
        peer = self.spawn()
        cfg = self.own(peer)
        config = self.root / 'config.json'
        config.write_text(json.dumps(cfg))
        env = dict(self.env, VTX_CONFIG=str(config))
        code = ("import sys; from pathlib import Path; sys.path.insert(0, %r); "
                "import independence; root=Path(sys.argv[1]); independence.ASR_DIR=root; "
                "independence.project_state_root=lambda: root/'state'; "
                "import asr_cli; sys.argv=['asr_cli']+sys.argv[2:]; "
                "raise SystemExit(asr_cli.main())") % str(ASR_DIR)
        remote = 'http://192.0.2.1:%d' % peer.port
        for args in (['--base-url', remote, 'sidecar', 'health'],
                     ['sidecar', 'health', '--base-url', remote]):
            result = subprocess.run([sys.executable, '-I', '-c', code, str(self.root)] + args,
                                    capture_output=True, text=True, timeout=10, env=env)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn('192.0.2.1', result.stdout + result.stderr)
        self.assertEqual(self.requests(peer), [])
        foreign = self.spawn(own=False)
        for args in (['--base-url', foreign.url, 'sidecar', 'health'],
                     ['sidecar', 'status', '--base-url', foreign.url]):
            result = subprocess.run([sys.executable, '-I', '-c', code, str(self.root)] + args,
                                    capture_output=True, text=True, timeout=10, env=env)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn(str(foreign.port), result.stdout + result.stderr)
        self.assertEqual(self.requests(foreign), [])

    def test_unreadable_socket_metadata_fails_closed(self):
        peer = self.spawn()
        self.own(peer)
        with mock.patch.object(independence, '_pid_socket_inodes', side_effect=PermissionError('fixture')):
            self.assert_rejected(peer.url)
        self.assertEqual(self.requests(peer), [])

    def test_stop_http_down_isolated_process_proof_only(self):
        # Actual signal experiment runs entirely inside an isolated session.
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                 '--isolated-stop', str(self.root)], env=self.env,
                                capture_output=True, text=True, timeout=15, start_new_session=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def doctor_asr(self, cfg, *, missing=False):
        # Execute the actual ASR doctor AST only: do not run unrelated host
        # probes, load ambient configuration, or inspect configured model dirs.
        sys.path.insert(0, str(ROOT))
        import vt
        tree = ast.parse((ROOT / 'vt.py').read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'cmd_init')
        start = next(i for i, n in enumerate(function.body) if isinstance(n, ast.Expr)
                     and isinstance(n.value, ast.Call) and ast.unparse(n.value.func) == 'sys.path.insert')
        end = next(i for i, n in enumerate(function.body) if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'tts_repo' for t in n.targets))
        records = {}
        namespace = {'sys': sys, 'ROOT': ROOT, 'Path': Path, 'lc': cfg,
                     '_pcfg': SimpleNamespace(cfg_get=client.cfg_get), '_AsrIndShim': vt._AsrIndShim,
                     'rec': lambda key, ok, detail='': records.update({key: (ok, detail)})}
        with mock.patch.object(sys, 'path', sys.path.copy()), \
                mock.patch.dict(sys.modules, {'independence': None} if missing else {}):
            exec(compile(ast.Module(body=function.body[start:end], type_ignores=[]), 'vt-asr-doctor', 'exec'), namespace)
        return records

    def test_doctor_uses_guarded_health_and_disabled_is_zero_http(self):
        peer = self.spawn(own=False)
        cfg = {'asr': {'enabled': True, 'base_url': peer.url}}
        self.assertFalse(self.doctor_asr(cfg)['asr_sidecar'][0])
        self.assertEqual(self.requests(peer), [])
        with mock.patch.object(client, 'health', return_value={'model': 'guarded'}) as health:
            self.assertTrue(self.doctor_asr(cfg)['asr_sidecar'][0])
            health.assert_called_once_with(peer.url, timeout=0.5)
        with mock.patch.object(client, 'health', side_effect=AssertionError('disabled must not probe')):
            cfg['asr']['enabled'] = False
            self.assertFalse(self.doctor_asr(cfg)['asr_sidecar'][0])

    def test_doctor_app_missing_fails_closed_without_arbitrary_http(self):
        peer = self.spawn(own=False)
        with mock.patch.object(client, 'health', side_effect=AssertionError('shim cannot probe')):
            status = self.doctor_asr({'asr': {'enabled': True, 'base_url': peer.url}}, missing=True)
        self.assertFalse(status['asr_sidecar'][0])
        self.assertIn('未发送 HTTP', status['asr_sidecar'][1])
        self.assertEqual(self.requests(peer), [])


def isolated_stop(root):
    root = Path(root)
    independence.ASR_DIR = root
    independence.project_state_root = lambda: root / 'state'
    port = free_port()
    proc = subprocess.Popen([sys.executable, '-I', str(root / 'sidecar_server.py'),
                             '--port', str(port), '--no-listen'], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        assert proc.stdout.readline().strip() == 'ready'
        independence.write_pid_record(port, proc.pid)
        with mock.patch.object(client, 'health', side_effect=AssertionError('stop must not HTTP')), \
                mock.patch.object(independence, 'endpoint_listeners', side_effect=AssertionError('socket down')):
            assert client.stop_sidecar('http://127.0.0.1:%d' % port, cfg={})
        assert not independence.pid_file(port).exists()
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.terminate()
        proc.wait(timeout=5)
        proc.stdout.close()
        proc.stderr.close()


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--isolated-stop':
        isolated_stop(sys.argv[2])
    else:
        unittest.main()
