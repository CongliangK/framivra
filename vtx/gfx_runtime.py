"""Bounded, opt-in CDP runtime evidence for an isolated experiment.

This probes the browser root session, not a Page session. It does not trace,
change browser settings, infer GPU state from flags, or provide a cache identity.
"""
import http.client
import io
import json
import math
import re
import struct
import os
from pathlib import Path
import time
from urllib.parse import urlsplit
from .gfx_render import WSConn

MAX_HTTP_BYTES = 65536
MAX_SESSION_BYTES = 1024 * 1024
MAX_MESSAGES = 64
PROBE_SECONDS = 5.0


class _BudgetReader(io.RawIOBase):
    def __init__(self, sock):
        self.sock = sock

    def readable(self):
        return True

    def readinto(self, buffer):
        data = self.sock.recv(len(buffer))
        buffer[:len(data)] = data
        return len(data)


class _BudgetSocket:
    def __init__(self, sock, deadline, byte_limit=MAX_SESSION_BYTES):
        self.sock, self.deadline = sock, deadline
        self.remaining = byte_limit

    def _timeout(self):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError('runtime probe deadline')
        self.sock.settimeout(left)

    def recv(self, size):
        self._timeout()
        if self.remaining <= 0:
            raise ValueError('runtime probe byte bound')
        data = self.sock.recv(min(size, self.remaining, 32768))
        self.remaining -= len(data)
        return data

    def sendall(self, data):
        self._timeout()
        size = memoryview(data).nbytes
        if size > self.remaining:
            raise ValueError('runtime probe byte bound')
        # Reserve before sending: even a partial failed send cannot be retried
        # against an unchanged budget. Requests and pong frames share the cap.
        self.remaining -= size
        self.sock.sendall(data)

    def makefile(self, mode, *args, **kwargs):
        if mode != 'rb':
            raise ValueError('runtime probe socket mode')
        return io.BufferedReader(_BudgetReader(self))

    def close(self):
        self.sock.close()


class _ProbeConnection(WSConn):
    def __init__(self, port, path):
        self.deadline = time.monotonic() + PROBE_SECONDS
        super().__init__(port, path, timeout=PROBE_SECONDS)

    def _handshake(self, port, path):
        self.sock = _BudgetSocket(self.sock, self.deadline)
        super()._handshake(port, path)


    def _recv_exact(self, size):
        if size > MAX_SESSION_BYTES:
            raise ValueError('runtime frame byte bound')
        return super()._recv_exact(size)

    def recv_msg(self):
        payload, fragmented = bytearray(), False
        while True:
            header = self._recv_exact(2)
            final, opcode = bool(header[0] & 128), header[0] & 15
            if header[0] & 112 or header[1] & 128:
                raise ValueError('invalid runtime frame flags')
            length = header[1] & 127
            if length == 126:
                length = struct.unpack('>H', self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack('>Q', self._recv_exact(8))[0]
            if opcode >= 8:
                if not final or length > 125 or opcode not in (8, 9, 10):
                    raise ValueError('invalid runtime control frame')
                control = self._recv_exact(length)
                if opcode == 8:
                    raise ConnectionError('runtime session closed')
                if opcode == 9:
                    self._send_frame(10, control)
                continue
            if opcode != (0 if fragmented else 1):
                raise ValueError('invalid runtime fragment sequence')
            if len(payload) + length > MAX_SESSION_BYTES:
                raise ValueError('runtime message byte bound')
            payload.extend(self._recv_exact(length))
            if final:
                return 1, bytes(payload)
            fragmented = True


def _json(data):
    def reject_constant(value):
        raise ValueError('nonfinite runtime JSON')
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('nonfinite runtime JSON')
        return number
    if not isinstance(data, str):
        data = data.decode('utf-8', errors='strict')
    return json.loads(data, parse_constant=reject_constant, parse_float=finite_float)


def _browser_path(port):
    if type(port) is not int or not 0 < port < 65536:
        raise ValueError('invalid runtime probe port')
    deadline = time.monotonic() + PROBE_SECONDS
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=PROBE_SECONDS)
    try:
        connection.connect()
        connection.sock = _BudgetSocket(connection.sock, deadline, 2 * MAX_HTTP_BYTES)
        connection.request('GET', '/json/version')
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError('runtime endpoint unavailable')
        raw = response.read(MAX_HTTP_BYTES + 1)
        if len(raw) > MAX_HTTP_BYTES:
            raise ValueError('runtime endpoint byte bound')
        info = _json(raw)
        address = urlsplit(info['webSocketDebuggerUrl'])
        if (address.scheme != 'ws' or address.hostname not in ('127.0.0.1', 'localhost') or
                address.port != port or address.query or address.fragment or address.username or
                not re.fullmatch(r'/devtools/browser/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', address.path)):
            raise ValueError('invalid browser root endpoint')
        return address.path
    finally:
        connection.close()


def _call(connection, identifier, method):
    connection.send_text(json.dumps({'id': identifier, 'method': method, 'params': {}}))
    for _ in range(MAX_MESSAGES):
        opcode, data = connection.recv_msg()
        if opcode != 1:
            raise ValueError('invalid runtime protocol message')
        message = _json(data)
        if not isinstance(message, dict):
            raise ValueError('invalid runtime protocol object')
        if type(message.get('id')) is int and message['id'] == identifier:
            if 'error' in message or not isinstance(message.get('result'), dict):
                raise ValueError('runtime protocol failure')
            return message['result']
    raise ValueError('runtime protocol message bound')


def selected_evidence(version, system):
    """Retain measured fields needed by #62; omit URLs/command lines/devices."""
    product = version.get('product')
    gpu = system.get('gpu')
    if (not isinstance(product, str) or len(product) > 128 or
            not re.fullmatch(r'(?:Chrome|HeadlessChrome|Chromium)/[0-9]+(?:\.[0-9]+){3}', product) or
            not isinstance(gpu, dict)):
        raise ValueError('missing substantive runtime evidence')
    features = gpu.get('featureStatus')
    status = features.get('gpu_compositing') if isinstance(features, dict) else None
    if not isinstance(status, str) or not 0 < len(status) <= 128:
        raise ValueError('missing actual compositor status')
    selected_gpu = {'featureStatus': {'gpu_compositing': status}}
    aux = gpu.get('auxAttributes')
    if isinstance(aux, dict) and 'glRenderer' in aux:
        renderer = aux['glRenderer']
        if not isinstance(renderer, str) or len(renderer) > 1024:
            raise ValueError('invalid actual renderer evidence')
        selected_gpu['auxAttributes'] = {'glRenderer': renderer}
    return {'browser_version': {'product': product}, 'system_info': {'gpu': selected_gpu}}


def probe(port):
    connection = _ProbeConnection(port, _browser_path(port))
    try:
        version = _call(connection, 1, 'Browser.getVersion')
        system = _call(connection, 2, 'SystemInfo.getInfo')
        return selected_evidence(version, system)
    finally:
        connection.close()


def classify(evidence):
    """Conservative #62 classification from actual compositor/renderer fields."""
    gpu = evidence['system_info']['gpu']
    status = gpu['featureStatus']['gpu_compositing'].lower()
    renderer = gpu.get('auxAttributes', {}).get('glRenderer', '').lower()
    software = status in ('disabled_software', 'software_only') or 'software only' in status
    hardware = status in ('enabled', 'enabled_on', 'hardware accelerated')
    if not software and not hardware:
        raise ValueError('unverifiable actual compositor')
    if 'swiftshader' in renderer:
        return 'swiftshader'
    if any(word in renderer for word in ('llvmpipe', 'softpipe', 'software')):
        if software:
            return 'software_skia'
        raise ValueError('contradictory software renderer')
    if 'vulkan' in renderer and hardware:
        return 'vulkan'
    if any(word in renderer for word in ('nvidia', 'geforce', 'quadro', 'tesla')) and hardware:
        return 'nvidia_egl'
    if software and not renderer:
        return 'software_skia'
    raise ValueError('unverifiable actual renderer')


def _browser_pid(connection, identifier):
    result = _call(connection, identifier, 'SystemInfo.getProcessInfo')
    candidates = result.get('processInfo')
    browsers = [p for p in candidates if isinstance(p, dict) and p.get('type') == 'browser'] if isinstance(candidates, list) else []
    if len(browsers) != 1 or type(browsers[0].get('id')) is not int or not 0 < browsers[0]['id'] <= 2 ** 31 - 1:
        raise ValueError('ambiguous actual browser process')
    return browsers[0]['id']


def _argv(pid):
    with (Path('/proc') / str(pid) / 'cmdline').open('rb') as stream:
        raw = stream.read(65537)
    if not raw or len(raw) > 65536 or not raw.endswith(b'\0'):
        raise ValueError('invalid actual process arguments')
    argv = [arg.decode('utf-8') for arg in raw[:-1].split(b'\0')]
    if not argv[0] or len(argv) > 4096:
        raise ValueError('invalid actual process arguments')
    return argv


def _exe_stat(pid):
    value = os.stat(Path('/proc') / str(pid) / 'exe')
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _process_observation(connection):
    from .gfx_runtime_executable import observed_executable
    from .process_samples import ProcReader
    deadline = time.monotonic() + PROBE_SECONDS
    pid = _browser_pid(connection, 3)
    reader = ProcReader()
    before, executable_before, argv = reader.identity(pid), _exe_stat(pid), _argv(pid)
    executable = observed_executable(pid, deadline=deadline)
    if (executable.pop('_stat_identity') != executable_before or
            _argv(pid) != argv or reader.identity(pid) != before or
            _browser_pid(connection, 4) != pid or reader.identity(pid) != before or
            _exe_stat(pid) != executable_before or time.monotonic() >= deadline):
        raise ValueError('browser process changed during observation')
    return executable, argv, {'method': 'root-cdp-and-linux-proc-lifetime',
                              'pid': pid, 'start_ticks': before[1]}


def collect(port, *, run_id):
    """Separate raw artifact; never a cache/reuse credential or event metadata.

    Process evidence comes from the live root session and Linux /proc, never a
    caller-provided identity dictionary or a guessed supervisor descendant.
    Page-dependent fields need a separate page witness. Socket budgets apply
    to the entire root session; file deadlines are cooperative, not a hard
    bound on stalled OS I/O. Missing observations stay null with fixed reasons.
    """
    from datetime import datetime, timezone
    from . import events
    if not events._id(run_id) or type(port) is not int or not 0 < port < 65536:
        raise ValueError('invalid runtime observation configuration')
    observation = {
        'schema': 1, 'source': 'live-cdp', 'run_id': run_id,
        'observed_at': datetime.now(timezone.utc).isoformat(),
        'browser_version': None, 'system_info': None,
        'actual_executable': None, 'effective_argv': None, 'process_binding': None,
        'viewport': None, 'color_space': None, 'alpha_path': None,
        'errors': {'viewport': 'page_observation_required',
                   'color_space': 'not_observed', 'alpha_path': 'page_observation_required'},
    }
    connection = None
    try:
        connection = _ProbeConnection(port, _browser_path(port))
        for identifier, method, field in (
                (1, 'Browser.getVersion', 'browser_version'),
                (2, 'SystemInfo.getInfo', 'system_info')):
            try:
                observation[field] = _call(connection, identifier, method)
            except Exception:
                observation['errors'][field] = 'probe_failed'
        try:
            executable, argv, binding = _process_observation(connection)
            observation['actual_executable'] = executable
            observation['effective_argv'] = argv
            observation['process_binding'] = binding
        except Exception:
            for field in ('actual_executable', 'effective_argv', 'process_binding'):
                observation['errors'][field] = 'process_identity_unavailable'
    except Exception:
        for field in ('browser_version', 'system_info', 'actual_executable', 'effective_argv', 'process_binding'):
            observation['errors'][field] = 'root_session_unavailable'
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                observation['errors']['session_close'] = 'close_failed'
    return observation


def identity(raw):
    """Bounded experiment comparison fields, not a general #48 cache identity."""
    if (not isinstance(raw, dict) or raw.get('schema') != 1 or raw.get('source') != 'live-cdp' or
            not isinstance(raw.get('errors'), dict) or
            any(field in raw['errors'] for field in ('browser_version', 'system_info',
                'actual_executable', 'effective_argv', 'process_binding', 'session_close'))):
        raise ValueError('incomplete runtime observation')
    executable, binding, argv = raw.get('actual_executable'), raw.get('process_binding'), raw.get('effective_argv')
    if (not isinstance(executable, dict) or executable.get('observation_method') != 'linux-proc-exe' or
            not isinstance(executable.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', executable['sha256']) or
            not isinstance(binding, dict) or binding.get('method') != 'root-cdp-and-linux-proc-lifetime' or
            type(binding.get('pid')) is not int or binding['pid'] <= 0 or
            type(binding.get('start_ticks')) is not int or binding['start_ticks'] < 0 or
            not isinstance(argv, list) or not argv or len(argv) > 4096 or
            not all(isinstance(arg, str) for arg in argv)):
        raise ValueError('missing process identity')
    evidence = selected_evidence(raw['browser_version'], raw['system_info'])
    # Only known per-instance routing/lifetime parameters are excluded. All
    # other ordered arguments, including unknown flags, remain comparison input.
    stable_argv = [arg for arg in argv[1:] if not arg.startswith((
        '--remote-debugging-port=', '--user-data-dir=', '--parent-alive-fd='))]
    return {'executable_sha256': executable['sha256'], 'argv': stable_argv,
            'chromium_version': evidence['browser_version']['product'].split('/', 1)[1],
            'effective_backend': classify(evidence), 'evidence': evidence}
