"""Portable GPU locking regressions; no GPU or model required."""
import errno
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.bgm.bgm_gen.engines import gpu_guard as guard


@pytest.mark.fast
def test_windows_engine_discovery_without_fcntl():
    # A fresh process exercises the actual eager discovery, not cached modules.
    code = '''
import builtins, os, sys, types
real_import = builtins.__import__
def importing(name, *args, **kwargs):
    if name == "fcntl":
        raise AssertionError("POSIX import on Windows")
    return real_import(name, *args, **kwargs)
# Import pathlib before selecting the platform to avoid simulating Windows paths.
import pathlib, tempfile, subprocess, ctypes, ctypes.util
sys.modules["msvcrt"] = types.ModuleType("msvcrt")
builtins.__import__ = importing
os.name = "nt"
import apps.bgm.bgm_gen.engines
'''
    result = subprocess.run([sys.executable, "-c", code],
                            cwd=Path(__file__).resolve().parents[4],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.fast
def test_lock_contention_releases_timeout_handle(tmp_path):
    path = str(tmp_path / "gpu.lock")
    first = guard.VramGuard(1, 1, "raise", lock_path=path)
    second = guard.VramGuard(1, 1, "raise", lock_path=path)
    assert first._acquire_lock(time.time() + 2)
    try:
        assert not second._acquire_lock(time.time() + 0.05)
        assert second._fd is None
    finally:
        first._release_lock()
    assert second._acquire_lock(time.time() + 2)
    second._release_lock()


@pytest.mark.fast
def test_windows_byte_lock_and_unlock(monkeypatch, tmp_path):
    lock = Mock()
    monkeypatch.setattr(guard, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(guard, "msvcrt", SimpleNamespace(
        locking=lock, LK_NBLCK=2, LK_UNLCK=0), raising=False)
    item = guard.VramGuard(1, 1, "raise", lock_path=str(tmp_path / "gpu.lock"))
    assert item._acquire_lock(time.time() + 2)
    fd = item._fd.fileno()
    item._release_lock()
    assert [call.args for call in lock.call_args_list] == [(fd, 2, 1), (fd, 0, 1)]
    assert item._fd is None


@pytest.mark.fast
def test_unexpected_lock_error_closes_handle(monkeypatch, tmp_path):
    monkeypatch.setattr(guard, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(guard, "msvcrt", SimpleNamespace(
        locking=Mock(side_effect=OSError(errno.EBADF, "bad descriptor")),
        LK_NBLCK=2), raising=False)
    item = guard.VramGuard(1, 1, "raise", lock_path=str(tmp_path / "gpu.lock"))
    with pytest.raises(OSError, match="bad descriptor"):
        item._acquire_lock(time.time() + 2)
    assert item._fd is None
