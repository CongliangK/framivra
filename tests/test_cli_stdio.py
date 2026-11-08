"""CLI-owned standard streams must survive inherited O_NONBLOCK transports."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from vtx.runtime.stdio import ensure_blocking_stdio


class _FakeStream:
    """Minimal stream stand-in: only fileno() matters to ensure_blocking_stdio."""

    def __init__(self, fileno):
        self._fileno = fileno

    def fileno(self):
        if isinstance(self._fileno, Exception):
            raise self._fileno
        return self._fileno


class _NoFilenoStream:
    """Stream without any fileno method (pure Python replacement)."""


@unittest.skipUnless(os.name == "posix" and hasattr(os, "set_blocking"), "POSIX descriptor semantics")
class EnsureBlockingStdioUnitTests(unittest.TestCase):
    """Unit cases for ensure_blocking_stdio() against vtx/runtime/stdio.py."""

    def setUp(self):
        self._saved_streams = (sys.stdin, sys.stdout, sys.stderr)
        self._saved_flags = {fd: os.get_blocking(fd) for fd in (0, 1, 2) if self._fd_ok(fd)}
        self._fds_to_close = []

    @staticmethod
    def _fd_ok(fd):
        try:
            os.get_blocking(fd)
            return True
        except OSError:
            return False

    def tearDown(self):
        sys.stdin, sys.stdout, sys.stderr = self._saved_streams
        for fd, blocking in self._saved_flags.items():
            try:
                os.set_blocking(fd, blocking)
            except OSError:
                pass
        for fd in self._fds_to_close:
            try:
                os.close(fd)
            except OSError:
                pass

    def _pipe_nonblocking(self):
        r, w = os.pipe()
        self._fds_to_close.extend((r, w))
        os.set_blocking(w, False)
        return r, w

    def _patch_streams(self, stdin, stdout, stderr):
        sys.stdin, sys.stdout, sys.stderr = stdin, stdout, stderr

    def test_standard_fds_nonblocking_cleared(self):
        # Real POSIX fd 0/1/2 carrying O_NONBLOCK must be switched to blocking.
        pipes = [os.pipe() for _ in range(3)]
        for r, w in pipes:
            self._fds_to_close.extend((r, w))
        saved = [os.dup(fd) for fd in (0, 1, 2)]
        self._fds_to_close.extend(saved)
        try:
            for (r, _w), fd in zip(pipes, (0, 1, 2)):
                os.dup2(r, fd)
                os.set_blocking(fd, False)
            self._patch_streams(
                _FakeStream(0), _FakeStream(1), _FakeStream(2))
            ensure_blocking_stdio()
            self.assertEqual(
                [os.get_blocking(fd) for fd in (0, 1, 2)], [True, True, True])
        finally:
            for old, fd in zip(saved, (0, 1, 2)):
                os.dup2(old, fd)
                os.set_blocking(fd, self._saved_flags[fd])

    def test_nonstandard_fd_untouched(self):
        # A descriptor outside 0/1/2 keeps its O_NONBLOCK even as sys.stdout.
        _r, w = self._pipe_nonblocking()
        self._patch_streams(
            _FakeStream(0), _FakeStream(w), _FakeStream(2))
        ensure_blocking_stdio()
        self.assertFalse(os.get_blocking(w))

    def test_already_blocking_unchanged(self):
        # Blocking descriptors stay blocking; the call is a no-op, not an error.
        pipes = [os.pipe() for _ in range(3)]
        for r, w in pipes:
            self._fds_to_close.extend((r, w))
        saved = [os.dup(fd) for fd in (0, 1, 2)]
        self._fds_to_close.extend(saved)
        try:
            for (r, _w), fd in zip(pipes, (0, 1, 2)):
                os.dup2(r, fd)
            self._patch_streams(
                _FakeStream(0), _FakeStream(1), _FakeStream(2))
            ensure_blocking_stdio()
            self.assertEqual(
                [os.get_blocking(fd) for fd in (0, 1, 2)], [True, True, True])
        finally:
            for old, fd in zip(saved, (0, 1, 2)):
                os.dup2(old, fd)
                os.set_blocking(fd, self._saved_flags[fd])

    def test_replacement_streams_skipped(self):
        # io.UnsupportedOperation (StringIO), missing fileno(), ValueError,
        # and a non-int fileno() are all skipped without touching descriptors.
        _r, w = self._pipe_nonblocking()
        self._patch_streams(
            _NoFilenoStream(), io.StringIO(), _FakeStream(ValueError("closed")))
        ensure_blocking_stdio()
        self.assertFalse(os.get_blocking(w))
        # bool is an int subclass but type(True) is not int: must be skipped too.
        self._patch_streams(
            _FakeStream(True), _FakeStream(1), _FakeStream(2))
        ensure_blocking_stdio()
        self.assertFalse(os.get_blocking(w))

    def test_get_blocking_error_propagates(self):
        # Real descriptor inspection failure must surface, not be swallowed.
        self._patch_streams(
            _FakeStream(0), _FakeStream(1), _FakeStream(2))
        with mock.patch("os.get_blocking", side_effect=OSError(9, "Bad fd")):
            with self.assertRaises(OSError):
                ensure_blocking_stdio()

    def test_set_blocking_error_propagates(self):
        # Real descriptor update failure must surface, not be swallowed.
        # Only standard fds reach os.set_blocking; make fd 1 nonblocking first.
        os.set_blocking(1, False)
        self._patch_streams(
            _FakeStream(0), _FakeStream(1), _FakeStream(2))
        with mock.patch("os.set_blocking", side_effect=OSError(9, "Bad fd")):
            with self.assertRaises(OSError):
                ensure_blocking_stdio()


@unittest.skipUnless(os.name == "posix" and hasattr(os, "set_blocking"), "POSIX descriptor semantics")
class CliStdioUnitTests(unittest.TestCase):
    def test_library_import_does_not_mutate_flags(self):
        # Importing the package must not touch descriptor flags; only main()
        # normalizes them (vtx/__init__.py invokes ensure_blocking_stdio inside
        # main(), before CLI imports, not at module import time).
        def nonblocking():
            for fd in (0, 1, 2):
                os.set_blocking(fd, False)
        script = (
            "import json, os\n"
            "import vtx\n"
            "print(json.dumps([os.get_blocking(fd) for fd in (0, 1, 2)]))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            preexec_fn=nonblocking, timeout=15, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2048:].decode(errors="replace"))
        self.assertEqual(json.loads(result.stdout), [False, False, False])

    def test_unnormalized_nonblocking_breaks_or_times_out(self):
        # Drain/timeout semantics of subprocess.run against a child that writes
        # 600KB through inherited nonblocking pipes WITHOUT calling main():
        # communicate() must either observe the child's EAGAIN failure
        # (nonzero exit, BlockingIOError traceback) or fire the timeout and
        # kill the stuck writer. Both outcomes prove the transport is unsafe
        # until ensure_blocking_stdio() normalizes it.
        script = r'''
import os, sys
assert sys.stdout.fileno() == 1
assert not os.get_blocking(1)
sys.stdout.write('z' * 600000 + '\n')
sys.stdout.flush()
'''
        def nonblocking():
            for fd in (0, 1, 2):
                os.set_blocking(fd, False)
        try:
            result = subprocess.run(
                [sys.executable, "-c", script],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                preexec_fn=nonblocking, timeout=5, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"BlockingIOError", result.stderr + result.stdout)
        except subprocess.TimeoutExpired:
            pass  # writer wedged on the full nonblocking pipe: timeout-owned kill


@unittest.skipUnless(os.name == "posix" and hasattr(os, "set_blocking"), "POSIX descriptor semantics")
class CliStdioIntegrationTests(unittest.TestCase):
    def test_main_normalizes_inherited_streams_and_drains_large_output(self):
        # Force all three inherited stream descriptions nonblocking before exec.
        # communicate() joins process AND pipe EOF, with timeout-owned cleanup.
        script = r'''
import json, os, sys
from vtx.cli import commands
from vtx import main

def probe(execution):
    flags = [os.get_blocking(fd) for fd in (0, 1, 2)]
    sys.stdout.write(json.dumps(flags) + '\n')
    sys.stdout.write('x' * 600000 + '\nSTDOUT-DONE\n')
    sys.stdout.flush()
    sys.stderr.write('y' * 600000 + '\nSTDERR-DONE\n')
    sys.stderr.flush()
    assert sys.stdin.read() == 'SENTINEL-42'
    return 0
commands._main = probe
raise SystemExit(main())
'''
        def nonblocking():
            for fd in (0, 1, 2):
                os.set_blocking(fd, False)
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            input=b"SENTINEL-42", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            preexec_fn=nonblocking, timeout=15, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2048:].decode(errors="replace"))
        header, payload = result.stdout.split(b"\n", 1)
        self.assertEqual(json.loads(header), [True, True, True])
        self.assertEqual(len(payload), 600013)
        self.assertTrue(payload.endswith(b"\nSTDOUT-DONE\n"))
        self.assertEqual(payload.count(b"x"), 600000)
        self.assertEqual(len(result.stderr), 600013)
        self.assertTrue(result.stderr.endswith(b"\nSTDERR-DONE\n"))
        self.assertEqual(result.stderr.count(b"y"), 600000)


if __name__ == "__main__":
    unittest.main()
