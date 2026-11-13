"""Fresh checkout integration: no old checkout, personal runtime or media."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from bgm_gen import paths
from bgm_gen.engines import EngineError, run_engine
from bgm_gen.engines.sf2 import _runtime_fluidsynth_lib

pytestmark = pytest.mark.fast
APP = Path(__file__).resolve().parents[2]


@pytest.fixture
def fresh_env(monkeypatch, tmp_path):
    for name in ("MUSIC_RUNTIME", "MUSIC_ASSETS_ROOT", "MUSIC_SAMPLES_ROOT", "PYTHONPATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return dict(os.environ)


def test_runtime_defaults_and_explicit_missing(fresh_env, tmp_path):
    root = tmp_path / "data" / "framivra" / "music"
    assert paths.data_root() == str(root)
    assert paths.runtime_path() == str(root / "runtime.json")
    assert paths.local_runtime() == {}
    assert paths.effective_runtime() == {"assets_root": str(root / "assets"),
                                        "samples_root": str(root / "assets" / "samples")}
    env = {**fresh_env, "MUSIC_RUNTIME": str(tmp_path / "missing.json")}
    with pytest.raises(paths.RuntimeConfigError, match="MUSIC_RUNTIME"):
        paths.local_runtime(environ=env)
    assert paths.local_runtime() == {}  # Snapshot resolution must not mutate globals.


def test_runtime_relative_paths_shared_and_environment_snapshot(fresh_env, tmp_path):
    config = tmp_path / "config" / "runtime.json"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "assets_root": "../assets", "samples_root": "oneshots",
        "python_system": "./venv/bin/python", "python_torch": "python3",
        "tools": {"fluidsynth_lib": "./lib/custom.so", "ffmpeg": "ffmpeg"},
        "sf2": {"test": "fonts/example.sf2"},
        "demucs": {"python": "./venv/bin/python", "repo": "weights",
                    "env": {"HF_HOME": "$MY_CACHE/hf"}},
        "comfyui": {"base_url": "http://127.0.0.1:8188"}
    }), encoding="utf-8")
    env = {**fresh_env, "MUSIC_RUNTIME": str(config), "MY_CACHE": str(tmp_path / "cache")}
    rt = paths.effective_runtime(environ=env)
    assert rt["assets_root"] == str(tmp_path / "assets")
    assert rt["samples_root"] == str(config.parent / "oneshots")
    assert rt["python_system"] == str(config.parent / "venv/bin/python")
    assert rt["python_torch"] == "python3"
    assert rt["tools"]["ffmpeg"] == "ffmpeg"
    assert rt["sf2"]["test"] == str(config.parent / "fonts/example.sf2")
    assert rt["demucs"]["env"]["HF_HOME"] == str(tmp_path / "cache/hf")
    assert rt["demucs"]["repo"] == str(config.parent / "weights")
    assert _runtime_fluidsynth_lib(rt) == str(config.parent / "lib/custom.so")
    assert paths.effective_runtime(environ={**env, "MUSIC_ASSETS_ROOT": "~/user-assets", "HOME": str(tmp_path)})["assets_root"] == str(tmp_path / "user-assets")
    assert paths.local_runtime() == {}


@pytest.mark.parametrize("value", ["[]", "{bad", '{"tools": []}'])
def test_bad_explicit_runtime_fails_without_traceback(fresh_env, tmp_path, value):
    config = tmp_path / "runtime.json"
    config.write_text(value)
    env = {**fresh_env, "MUSIC_RUNTIME": str(config)}
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"payload": {"program": "kick", "notes": [[36, 0, .25, 100]]}}))
    result = subprocess.run(["python3", str(APP / "bgm.py"), "render-engine", "--engine", "synth", "--spec", str(spec)], env=env, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 1
    assert "MUSIC_RUNTIME" in result.stderr and "Traceback" not in result.stderr


def test_fresh_synth_default_output_and_explicit_empty_runtime(fresh_env, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MUSIC_RUNTIME", str(tmp_path / "missing.json"))
    result = run_engine({"engine": "synth", "payload": {"program": "kick", "notes": [[36, 0, .25, 100]]}}, runtime={})
    assert result["path"] == str(tmp_path / "out/smoke/synth/render.wav")
    assert Path(result["path"]).stat().st_size > 100
    assert result["sr"] == 44100 and result["rms_db"] > -50


def test_health_and_import_do_not_load_gpu_libraries(fresh_env, tmp_path):
    probe = subprocess.run(["python3", str(APP / "health.py")], cwd=tmp_path, env=fresh_env, capture_output=True, text=True)
    assert probe.returncode == 0, probe.stderr
    assert json.loads(probe.stdout)["ok"] is True
    env = {**fresh_env, "PYTHONPATH": str(APP)}
    probe = subprocess.run(["python3", "-c", "import bgm_gen.engines, sys; assert not {'torch', 'demucs', 'matchering'} & set(sys.modules)"], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert probe.returncode == 0, probe.stderr


def test_fresh_synth_project_end_to_end(fresh_env, tmp_path):
    assert shutil.which("ffmpeg"), "FFmpeg is the documented base render dependency"
    out = tmp_path / "render"
    command = ["python3", str(APP / "bgm.py"), "render", str(APP / "examples/synth.json"), "-o", str(out), "-q"]
    result = subprocess.run(command, cwd=tmp_path, env=fresh_env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["duration_s"] == 4
    assert report["qc_ok"] is True
    for name in ("mix.flac", "mix_master.wav", "mix_premaster.wav", "qc.json", "project.json", "stems/keys.synth.wav", "stems/keys.bus.wav"):
        assert (out / name).stat().st_size > 0
    assert json.loads((out / "qc.json").read_text())["ok"] is True
    # A second independent render must reproduce the audio, not rely on old out/.
    again = tmp_path / "again"
    command[-2] = str(again)
    other = subprocess.run(command, cwd=tmp_path, env=fresh_env, capture_output=True, text=True, timeout=90)
    assert other.returncode == 0, other.stderr
    assert (out / "mix.flac").read_bytes() == (again / "mix.flac").read_bytes()


def test_project_passes_explicit_runtime_to_sample_and_master(fresh_env, tmp_path):
    from bgm_gen.core.project import render_project
    from bgm_gen.engines import synth
    sample_dir = tmp_path / "samples"
    sample_dir.mkdir()
    run_engine({"engine": "synth", "out": str(sample_dir / "hit.wav"), "payload": {"program": "kick", "notes": [[36, 0, .25, 100]]}}, runtime={})
    project = {"name": "sample-test", "meta": {"spec_version": "1", "bpm": 120},
               "tracks": [{"name": "click", "engine": "sample", "instrument": "hit.wav", "notes": [[60, 0, .5, 100]], "clip": {"start_beat": 0, "end_beat": 8}}]}
    source = tmp_path / "project.json"
    source.write_text(json.dumps(project))
    result = render_project(str(source), str(tmp_path / "render"), quiet=True, runtime={"samples_root": str(sample_dir)})
    assert Path(result["master"]).is_file()
    assert result["stems"]["click"]["rms_db"] > -60


def test_project_qc_uses_explicit_runtime_without_reloading_defaults(fresh_env, tmp_path, monkeypatch):
    from bgm_gen.core import qc
    from bgm_gen.core.project import render_project

    config = tmp_path / "global-runtime.json"
    # An existing but wrong executable makes a leaked default observable without
    # requiring a second installed Python or replacing the real QC subprocesses.
    config.write_text(json.dumps({"python_system": shutil.which("ffmpeg")}))
    monkeypatch.setenv("MUSIC_RUNTIME", str(config))
    default_reads, script_calls = [], []
    load_runtime, run = qc.load_runtime, subprocess.run

    def read_default():
        default_reads.append(True)
        return load_runtime()

    def observe_run(command, *args, **kwargs):
        if len(command) > 1 and Path(command[1]).name in ("key_check.py", "bpm_check.py"):
            script_calls.append(command[:2])
        return run(command, *args, **kwargs)

    monkeypatch.setattr(qc, "load_runtime", read_default)
    monkeypatch.setattr(qc.subprocess, "run", observe_run)
    for name, runtime in (("selected", {"python_system": sys.executable}), ("empty", {})):
        if name == "empty":
            config.write_text("invalid default JSON")
        before = len(script_calls)
        result = render_project(str(APP / "examples/synth.json"),
                                str(tmp_path / name), quiet=True, runtime=runtime)
        report = json.loads(Path(result["qc"]).read_text())
        assert result["qc_ok"] is True
        assert report["measured"]["key_top3"]
        assert report["measured"]["bpm_top3"]
        assert len(script_calls) == before + 2
        assert all(call[0] == sys.executable for call in script_calls[before:])
        assert default_reads == []

    # Direct QC with None still reads the selected user config, and bad config
    # fails before the optional-analysis catches can turn it into an info item.
    config.write_text(json.dumps({"python_system": sys.executable}))
    assert qc.qc_report(result["master"])["measured"]["key_top3"]
    assert len(default_reads) == 1
    config.write_text("invalid default JSON")
    with pytest.raises(EngineError, match="MUSIC_RUNTIME"):
        qc.qc_report(result["master"])
    assert len(default_reads) == 2


def test_cli_output_overrides_and_catalog_never_write_app(fresh_env, tmp_path):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"out": str(tmp_path / "not-selected.wav"), "payload": {"program": "kick", "notes": [[36, 0, .25, 100]]}}))
    selected = tmp_path / "selected.wav"
    result = subprocess.run(["python3", str(APP / "bgm.py"), "render-engine", "--engine", "synth", "--spec", str(spec), "-o", str(selected)], env=fresh_env, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert selected.is_file() and not (tmp_path / "not-selected.wav").exists()
    result = subprocess.run(["python3", str(APP / "bgm.py"), "catalog", "--write"], env=fresh_env, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "catalog/timbres.json").is_file()


def test_demucs_configured_weights_force_local_repo_and_offline(fresh_env, tmp_path, monkeypatch):
    import bgm_gen.engines.demucs as demucs
    source = tmp_path / "source.wav"
    run_engine({"engine": "synth", "out": str(source), "payload": {"program": "kick", "notes": [[36, 0, .25, 100]]}}, runtime={})
    repo = tmp_path / "weights"
    repo.mkdir()
    (repo / "htdemucs.yaml").write_text("models: ['test']")
    (repo / "test.th").write_bytes(b"test fixture only; no actual model")
    class CommandVerified(Exception):
        pass
    def verify(cmd, **kwargs):
        assert cmd[:3] == [shutil.which("python3"), "-m", "demucs"]
        assert cmd[cmd.index("--repo") + 1] == str(repo)
        assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
        raise CommandVerified
    monkeypatch.setattr(demucs.subprocess, "run", verify)
    with pytest.raises(CommandVerified):
        run_engine({"engine": "demucs", "payload": {"input": str(source), "device": "cpu", "out_dir": str(tmp_path / "out")}}, runtime={"demucs": {"python": shutil.which("python3"), "repo": str(repo)}})


def test_demucs_missing_local_weights_never_starts_process(fresh_env, tmp_path, monkeypatch):
    import bgm_gen.engines.demucs as demucs
    source = tmp_path / "source.wav"
    source.write_bytes(b"not opened before local weight validation")
    repo = tmp_path / "weights"
    repo.mkdir()
    def forbidden(*args, **kwargs):
        pytest.fail("Demucs must fail before subprocess/network/GPU access")
    monkeypatch.setattr(demucs.subprocess, "run", forbidden)
    with pytest.raises(EngineError, match="本地权重"):
        run_engine({"engine": "demucs", "payload": {"input": str(source), "device": "cpu"}}, runtime={"demucs": {"python": shutil.which("python3"), "repo": str(repo)}})
