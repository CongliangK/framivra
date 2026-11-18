"""SF2 library discovery and legacy routing without native libraries/assets."""

import os
from pathlib import Path
import subprocess
from unittest.mock import MagicMock

import pytest

from bgm_gen import doctor
from bgm_gen.engines import base, midi_util, sf2
from bgm_gen.legacy import gm_lofi, midi2wav

pytestmark = pytest.mark.fast


def _fake_cdll(monkeypatch, accepted):
    calls = []
    lib = MagicMock()

    def load(name):
        calls.append(name)
        if name == accepted:
            return lib
        raise OSError("not available")

    monkeypatch.setattr(sf2.ctypes, "CDLL", load)
    return calls, lib


@pytest.mark.parametrize("prefix", ["/opt/homebrew", "/usr/local"])
def test_darwin_homebrew_fallback(monkeypatch, prefix):
    monkeypatch.setattr(sf2.sys, "platform", "darwin")
    monkeypatch.setattr(sf2.ctypes.util, "find_library", lambda name: None)
    expected = prefix + "/opt/fluid-synth/lib/libfluidsynth.dylib"
    calls, lib = _fake_cdll(monkeypatch, expected)
    assert sf2._load_lib({}) is lib
    assert calls[-1] == expected
    assert not any(".so" in name for name in calls)
    assert lib.new_fluid_settings.restype is sf2.ctypes.c_void_p
    assert lib.delete_fluid_settings.argtypes == [sf2.ctypes.c_void_p]


@pytest.mark.parametrize("platform,discovered", [
    ("darwin", "/native/libfluidsynth.3.dylib"),
    ("linux", "libfluidsynth.so.3"),
])
def test_unconfigured_discovery_precedes_fallback(monkeypatch, platform, discovered):
    monkeypatch.setattr(sf2.sys, "platform", platform)
    monkeypatch.setattr(sf2.ctypes.util, "find_library", lambda name: discovered)
    calls, lib = _fake_cdll(monkeypatch, discovered)
    assert sf2._load_lib({}) is lib
    assert calls == [discovered]


def test_explicit_library_first_and_not_reloaded(monkeypatch):
    monkeypatch.setattr(base, "load_runtime", lambda: pytest.fail("runtime reloaded"))
    monkeypatch.setattr(sf2.ctypes.util, "find_library", lambda name: "discovered")
    calls, lib = _fake_cdll(monkeypatch, "configured-library")
    assert sf2._load_lib({"tools": {"fluidsynth_lib": "configured-library"}}) is lib
    assert calls == ["configured-library"]


def test_explicit_invalid_library_does_not_fall_back(monkeypatch):
    monkeypatch.setattr(sf2.ctypes.util, "find_library",
                        lambda name: pytest.fail("explicit value must not discover"))
    calls, _ = _fake_cdll(monkeypatch, "libfluidsynth.so.3")
    with pytest.raises(base.EngineError, match="invalid-explicit"):
        sf2._load_lib({"tools": {"fluidsynth_lib": "invalid-explicit"}})
    assert calls == ["invalid-explicit"]


def test_linux_fallback_unchanged_and_deduplicated(monkeypatch):
    monkeypatch.setattr(sf2.sys, "platform", "linux")
    monkeypatch.setattr(sf2.ctypes.util, "find_library", lambda name: "libfluidsynth.so.3")
    calls, lib = _fake_cdll(monkeypatch, "libfluidsynth.so.2")
    assert sf2._load_lib({}) is lib
    assert calls == ["libfluidsynth.so.3", "libfluidsynth.so.2"]


def test_missing_library_actionable_engine_error(monkeypatch):
    monkeypatch.setattr(sf2.sys, "platform", "darwin")
    monkeypatch.setattr(sf2.ctypes.util, "find_library", lambda name: None)
    _fake_cdll(monkeypatch, None)
    with pytest.raises(base.EngineError, match="tools.fluidsynth_lib") as exc:
        sf2._load_lib({})
    assert "/opt/homebrew/opt/fluid-synth/lib/libfluidsynth.dylib" in str(exc.value)


def test_doctor_unconfigured_uses_shared_fallback_and_actual_load(monkeypatch):
    monkeypatch.setattr(sf2.sys, "platform", "darwin")
    monkeypatch.setattr(sf2.ctypes.util, "find_library", lambda name: "unloadable.dylib")
    expected = "/opt/homebrew/opt/fluid-synth/lib/libfluidsynth.dylib"
    calls, _ = _fake_cdll(monkeypatch, expected)
    ok, detail = doctor._check_fluidsynth_lib(None)
    assert ok and expected in detail
    assert calls == sf2._fluidsynth_candidates()[:calls.index(expected) + 1]
    assert calls[0] == "unloadable.dylib"


def test_doctor_explicit_invalid_library_stays_red(monkeypatch):
    monkeypatch.setattr(sf2.ctypes.util, "find_library",
                        lambda name: pytest.fail("explicit value must not fall back"))
    calls, _ = _fake_cdll(monkeypatch, None)
    ok, detail = doctor._check_fluidsynth_lib("invalid-explicit.dylib")
    assert not ok and "invalid-explicit.dylib" in detail
    assert calls == ["invalid-explicit.dylib"]


def test_doctor_discovery_is_not_proof_of_loadability(monkeypatch):
    monkeypatch.setattr(sf2.ctypes.util, "find_library", lambda name: "unloadable.dylib")
    calls, _ = _fake_cdll(monkeypatch, None)
    ok, detail = doctor._check_fluidsynth_lib(None)
    assert not ok and "tools.fluidsynth_lib" in detail
    assert calls == sf2._fluidsynth_candidates()


@pytest.mark.parametrize("module", [gm_lofi, midi2wav])
@pytest.mark.parametrize("configured", [False, True])
def test_legacy_uses_effective_runtime_and_keeps_argv_cwd(monkeypatch, tmp_path, module, configured):
    font = tmp_path / "user font.sf2"
    font.write_bytes(b"font fixture (not synthesized)")
    runtime = {"tools": {"fluidsynth_lib": "user-library"}}
    if configured:
        runtime["sf2"] = {"FluidR3_GM": str(font)}
    else:
        monkeypatch.setattr(module, "SF2", str(font))
    loads = []

    def load_runtime():
        loads.append(True)
        return runtime

    monkeypatch.setattr(base, "load_runtime", load_runtime)
    render = MagicMock()
    monkeypatch.setattr(sf2, "fluidsynth_render", render)
    write = MagicMock()
    monkeypatch.setattr(midi_util, "write_smf", write)
    monkeypatch.setattr(gm_lofi, "build_tracks", lambda: [])
    if module is gm_lofi:
        module.main()
        expected = (module.OUT_MID, module.OUT_WAV, str(font))
        write.assert_called_once_with(module.OUT_MID, [], bpm=module.BPM)
    else:
        assert module.main(["relative input.mid", "relative output.wav"]) == 0
        expected = ("relative input.mid", "relative output.wav", str(font))
    render.assert_called_once_with(*expected, sr=44100, bank=None, program=None,
                                   channel=0, runtime=runtime)
    assert len(loads) == 1


@pytest.mark.parametrize("module", [gm_lofi, midi2wav])
def test_legacy_broken_explicit_font_does_not_fall_back(monkeypatch, tmp_path, module):
    fallback = tmp_path / "fallback.sf2"
    fallback.write_bytes(b"existing historical font")
    monkeypatch.setattr(module, "SF2", str(fallback))
    monkeypatch.setattr(base, "load_runtime", lambda: {
        "sf2": {"FluidR3_GM": str(tmp_path / "missing.sf2")}})
    render = MagicMock()
    monkeypatch.setattr(sf2, "fluidsynth_render", render)
    with pytest.raises(base.EngineError, match="sf2 表指向不存在"):
        module.main() if module is gm_lofi else module.main(["in.mid", "out.wav"])
    render.assert_not_called()


def test_legacy_import_has_no_render_or_writes(tmp_path):
    app_root = str(Path(__file__).resolve().parents[2])
    env = dict(os.environ, PYTHONPATH=app_root)
    code = (
        "from bgm_gen import synthlib\n"
        "def forbidden(*args, **kwargs):\n"
        "    raise AssertionError('synthesis during import')\n"
        "synthlib.Track = forbidden\n"
        "from bgm_gen.legacy import demo_lofi, gm_lofi\n"
        "assert demo_lofi.BPM == gm_lofi.BPM == 78.0\n"
    )
    result = subprocess.run(["python3", "-c", code], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_midi2wav_usage_stays_dependency_free(monkeypatch):
    monkeypatch.setattr(base, "load_runtime", lambda: pytest.fail("runtime read for usage"))
    assert midi2wav.main([]) == 2
