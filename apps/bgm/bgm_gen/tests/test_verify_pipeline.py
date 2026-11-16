"""Fast helper contracts for the manual runner; never execute media acceptance."""
import json
import os
import subprocess
import sys
import time
import wave

import pytest

from tools import verify_pipeline as runner

pytestmark = pytest.mark.fast


def test_output_refuses_checkout_and_existing_directory(tmp_path):
    with pytest.raises(AssertionError, match="outside"):
        runner.reserve_output(runner.REPO / "never-create")
    with pytest.raises(FileExistsError):
        runner.reserve_output(tmp_path)
    new = tmp_path / "new"
    assert runner.reserve_output(new) == new


def test_original_fixture_is_reproducible_and_has_adjacent_selections(tmp_path):
    first = runner.create_fixture(tmp_path / "first")
    second = runner.create_fixture(tmp_path / "second")
    assert first.read_bytes() == second.read_bytes()
    project = json.loads(first.read_text(encoding="utf-8"))
    assert project["meta"]["spec_version"] == "2"
    assert project["meta"]["duration_hint"] == 16
    assert project["tracks"][0] == {"$ref": "keys 音轨.json"}
    assert {t["engine"] for t in project["tracks"][1:]} == {"synth", "sample"}
    timeline = json.loads((first.parent / "timeline.json").read_text(encoding="utf-8"))
    assert [s["bgm"]["output"] for s in timeline["sources"][1:]] == ["master", "premaster", "stem:keys"]
    events = timeline["audio_tracks"][0]["events"]
    assert [(e["in"], e["out"], e["src_in"]) for e in events] == [(0, 160, 0), (160, 320, 160), (320, 480, 320)]
    assert timeline["project"]["duration_frames"] == 480
    assert timeline["subtitles"] == []
    assert all(e["fade_in_frames"] == e["fade_out_frames"] == 3 for e in events)


def test_sample_is_original_pcm_and_deterministic(tmp_path):
    for name in ("a", "b"):
        runner.create_sample(tmp_path / name)
    a, b = [tmp_path / name / "原创 one-shot.wav" for name in ("a", "b")]
    assert runner.identity(a)["sha256"] == runner.identity(b)["sha256"]
    with wave.open(str(a)) as stream:
        assert (stream.getframerate(), stream.getnchannels(), stream.getsampwidth(), stream.getnframes()) == (48000, 1, 2, 12000)
    assert runner.identity(a)["size"] > 24000


def test_command_timeout_is_recorded_without_running_media(tmp_path, monkeypatch):
    instance = runner.Runner(tmp_path)
    assert json.loads((tmp_path / "vtx-config.json").read_text())["cache"] == {"mode": "aggressive"}

    def timeout(argv, **kwargs):
        assert kwargs["timeout"] == 900
        raise subprocess.TimeoutExpired(argv, 900, output=b"partial", stderr=b"diagnostic")

    monkeypatch.setattr(runner, "capture", timeout)
    with pytest.raises(AssertionError, match="exit None"):
        instance.run("timeout", ["never-executed"])
    step = instance.summary["steps"][0]
    assert step["exit_code"] is None and step["timeout_s"] == 900
    assert (tmp_path / step["stdout"]).read_bytes() == b"partial"
    assert (tmp_path / step["stderr"]).read_bytes() == b"diagnostic"


def test_expected_failure_preserves_actual_code_and_rejects_other_codes(tmp_path, monkeypatch):
    instance = runner.Runner(tmp_path)
    monkeypatch.setattr(runner, "capture", lambda argv, **kwargs:
                        subprocess.CompletedProcess(argv, 1, b"expected diagnostic", b""))
    assert instance.run("negative", ["never-executed"], expected_exit=1)[0] == "expected diagnostic"
    assert instance.summary["steps"][-1]["exit_code"] == 1
    assert instance.summary["steps"][-1]["expected_exit"] == 1
    with pytest.raises(AssertionError, match="expected 0"):
        instance.run("unexpected", ["never-executed"])
    monkeypatch.setattr(runner, "capture", lambda argv, **kwargs:
                        subprocess.CompletedProcess(argv, 2, b"", b"wrong failure"))
    with pytest.raises(AssertionError, match="expected 1"):
        instance.run("wrong-negative", ["never-executed"], expected_exit=1)


def test_mutation_failure_restores_all_original_bytes(tmp_path, monkeypatch):
    instance = runner.Runner(tmp_path)
    project = tmp_path / "project"
    runner.create_fixture(project)
    files = [project / name for name in ("music.json", "keys 音轨.json", "timeline.json")]
    originals = {path: path.read_bytes() for path in files}

    def fail(*args, **kwargs):
        assert files[0].read_bytes() != originals[files[0]]
        raise AssertionError("simulated prepare failure")

    monkeypatch.setattr(instance, "run", fail)
    with pytest.raises(AssertionError, match="simulated prepare failure"):
        instance.mutation_cases(project, ["never-executed"], {}, tmp_path / "unused-shared")
    assert {path: path.read_bytes() for path in files} == originals


def test_binding_inspection_cannot_self_heal_skipped_prepare(tmp_path, monkeypatch):
    instance = runner.Runner(tmp_path)
    project = tmp_path / "project"
    runner.create_fixture(project)
    counter = []
    def prepare(label, argv, **kwargs):
        if label in ("project-name-prepare", "ref-notes-prepare"):
            folder = project / "cache/bgm" / str(len(counter))
            folder.mkdir(parents=True)
            runner.dump(folder / "_complete.json", {"result": {"qc_ok": True}})
            counter.append(label)
        return "", ""
    def self_heal(label, timeline):
        folder = project / "cache/audio"
        folder.mkdir(parents=True)
        (folder / "new__bgm_fake.wav").write_bytes(b"created by inspection, not prepare")
        return {"music2": str(folder / "new__bgm_fake.wav")}
    monkeypatch.setattr(instance, "run", prepare)
    monkeypatch.setattr(instance, "bindings", self_heal)
    with pytest.raises(AssertionError, match="binding inspection"):
        instance.mutation_cases(project, ["never-executed"], {"music2": "old"}, tmp_path / "shared")


def test_loudnorm_json_allows_ffmpeg_trailing_stats():
    assert runner.loudnorm_measurement('filter output\n{"input_i":"-14.2","input_tp":"-4.1"}\n[out#0/null] stats') == {
        "input_i": "-14.2", "input_tp": "-4.1"}
    with pytest.raises(ValueError):
        runner.loudnorm_measurement("no valid measurement")


def test_selection_is_bound_to_source_not_unordered_set(tmp_path):
    master, stem, reference = [tmp_path / name for name in ("master.wav", "stem.wav", "reference.wav")]
    master.write_bytes(b"master PCM")
    reference.write_bytes(b"master PCM")
    stem.write_bytes(b"stem PCM")
    runner.verify_selection(master, reference, [master, stem], "music0")
    with pytest.raises(AssertionError, match="music0"):
        runner.verify_selection(stem, reference, [master, stem], "music0")


def test_timeout_stops_grandchild_writer(tmp_path):
    if os.name != "posix":
        with pytest.raises(AssertionError, match="POSIX"):
            runner.capture([sys.executable, "-c", "pass"], cwd=tmp_path, env=os.environ)
        return
    sentinel = tmp_path / "late-write"
    code = ("import subprocess,sys; "
            "subprocess.Popen([sys.executable,'-c',"
            "\"import time,pathlib;time.sleep(.8);pathlib.Path('late-write').write_text('orphan')\"]); "
            "print('spawned',flush=True); import time; time.sleep(30)")
    with pytest.raises(subprocess.TimeoutExpired) as exc:
        runner.capture([sys.executable, "-c", code], cwd=tmp_path, env=os.environ, timeout=.4)
    assert b"spawned" in exc.value.output
    time.sleep(.8)  # an unowned grandchild would write by now
    assert not sentinel.exists()
