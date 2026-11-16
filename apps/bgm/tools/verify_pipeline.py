#!/usr/bin/env python3
"""Manual, write-producing #374 synth→video acceptance (not CI or a benchmark).

Usage: python apps/bgm/tools/verify_pipeline.py --output /new/external/directory
Requires this interpreter's existing NumPy and PATH ffmpeg/ffprobe. Never installs,
downloads, starts services, or edits source/global configuration. No listening or
optional-engine acceptance is implied. Output directories must not already exist.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import time
import traceback
import struct
import wave

REPO = Path(__file__).resolve().parents[3]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def identity(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    st = path.stat()
    return {"sha256": digest.hexdigest(), "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def reserve_output(path, repo=REPO):
    path = path.expanduser().resolve()
    require(path != repo and repo not in path.parents, "--output must be outside the source checkout")
    path.mkdir(parents=True, exist_ok=False)  # also refuses existing empty directories
    return path


def capture(argv, *, cwd, env, timeout=900):
    """Own a POSIX session so a timeout also stops nested bgm/FFmpeg writers."""
    require(os.name == "posix", "this manual runner requires POSIX process groups")
    child = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, start_new_session=True)
    try:
        stdout, stderr = child.communicate(timeout=timeout)
    except BaseException as exc:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = child.communicate()
        if isinstance(exc, subprocess.TimeoutExpired):
            raise subprocess.TimeoutExpired(argv, timeout, output=stdout, stderr=stderr) from exc
        raise
    return subprocess.CompletedProcess(argv, child.returncode, stdout, stderr)


def loudnorm_measurement(stderr):
    # FFmpeg may append mux statistics after its filter JSON (notably 9.x).
    value, _ = json.JSONDecoder().raw_decode(stderr[stderr.rfind("{"):])
    return value


def verify_selection(selected, reference, derived, source_id):
    require(selected in derived and identity(selected)["sha256"] == identity(reference)["sha256"],
            f"{source_id} did not match its declared master/premaster/bus reference")


def create_sample(root):
    root.mkdir()
    with wave.open(str(root / "原创 one-shot.wav"), "wb") as stream:
        stream.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        stream.writeframes(b"".join(struct.pack("<h", round(12000 * math.exp(-i / 1800)
            * math.sin(2 * math.pi * 880 * i / 48000))) for i in range(12000)))


def create_fixture(project):
    """Original fixed notes, no catalog/private assets; exactly 32 beats / 16 s."""
    project.mkdir()
    keys = {"name": "keys", "engine": "synth", "instrument": "fm_ep",
            "gain_db": -6, "pan": -.6, "clip": {"start_beat": 0, "end_beat": 32},
            "notes": [[p, beat, .9, 88] for beat in range(32)
                      for p in ([(60, 64, 67, 71)[(beat // 8) % 4],
                                 (67, 71, 74, 76)[(beat // 8) % 4]])]}
    dump(project / "keys 音轨.json", keys)
    tracks = [{"$ref": "keys 音轨.json"}]
    for name, instrument, gain, pan, notes in (
        ("bass", "bass", -10, .25, [[(36, 40, 43, 47)[b // 8], b, 1.8, 92] for b in range(0, 32, 2)]),
        ("drums", "kick", -15, .1, [[36, b, .25, 95 if b % 4 == 0 else 72] for b in range(32)])):
        tracks.append({"name": name, "engine": "synth", "instrument": instrument,
                       "gain_db": gain, "pan": pan, "notes": notes,
                       "clip": {"start_beat": 0, "end_beat": 32}})
    tracks.append({"name": "one-shot", "engine": "sample", "instrument": "原创 one-shot.wav",
                   "gain_db": -16, "pan": .4, "clip": {"start_beat": 0, "end_beat": 32},
                   "notes": [[60, b + .5, .5, 85] for b in range(0, 32, 2)]})
    dump(project / "music.json", {"name": "original-374", "meta": {
        "spec_version": "2", "sr": 48000, "channels": 2, "bpm": 120,
        "seed": 374, "duration_hint": 16}, "tracks": tracks,
        "master": {"chain": "loudnorm", "target_lufs": -14, "true_peak_db": -2,
                   "qc": {"duration_min_s": 16, "silent_window_max_s": 1.5}}})
    choices = ("master", "premaster", "stem:keys")
    dump(project / "timeline.json", {"version": 2, "project": {
        "fps": 30, "width": 1920, "height": 1080, "duration_frames": 480},
        "sources": [{"id": "picture", "path": "original-source.mp4"}] + [
            {"id": f"music{i}", "path": "music.json", "bgm": {"output": output}}
            for i, output in enumerate(choices)],
        "video_tracks": [{"name": "original", "kind": "aroll", "clips": [
            {"src": "picture", "src_in": 0, "src_out": 480, "dst_in": 0, "dur": 480}]}],
        "subtitles": [],
        "audio_tracks": [{"name": "adjacent-selections", "events": [
            {"src": f"music{i}", "in": start, "out": end, "src_in": start, "volume": 1,
             "fade_in_frames": 3, "fade_out_frames": 3}
            for i, (start, end) in enumerate(((0, 160), (160, 320), (320, 480)))]}]})
    return project / "music.json"


class Runner:
    def __init__(self, output):
        self.output = output
        self.logs = output / "logs"
        self.logs.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("MUSIC_", "BGM_", "VTX_", "PYTHON"))}
        self.env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
                        MUSIC_RUNTIME=str(output / "runtime.json"),
                        MUSIC_ASSETS_ROOT=str(output / "unused-assets"),
                        VTX_CONFIG=str(output / "vtx-config.json"), BGM_NO_PARALLEL="1")
        create_sample(output / "原创 采样 #374")
        dump(output / "runtime.json", {"samples_root": "原创 采样 #374"})
        # Persistent media reuse is opt-in; core intentionally removes BGM
        # intermediates at session end. Freeze aggressive for cache assertions.
        dump(output / "vtx-config.json", {"cache": {"mode": "aggressive"},
             "bgm": {"python": sys.executable,
                     "runtime": str(output / "runtime.json")}})
        self.summary = {"status": "FAIL", "scope": "original 16-second synth music→video manual acceptance",
                        "engines_exercised_on_success": ["synth", "sample", "master:loudnorm"],
                        "not_tested": ["seven-engine acceptance (SF2/SFZ/AI/Demucs and optional mastering)", "human listening", "cross-platform qualification"],
                        "platform": platform.platform(), "python": sys.version,
                        "interpreter": sys.executable, "steps": []}

    def run(self, label, argv, expected_exit=0):
        argv = [str(x) for x in argv]
        step = {"label": label, "command": argv, "cwd": str(self.output), "expected_exit": expected_exit}
        self.summary["steps"].append(step)
        start = time.monotonic()
        try:
            try:
                result = capture(argv, cwd=self.output, env=self.env, timeout=900)
            except subprocess.TimeoutExpired as exc:
                step["timeout_s"] = 900
                result = subprocess.CompletedProcess(argv, None, exc.stdout or b"", exc.stderr or b"")
            step.update(exit_code=result.returncode, seconds=time.monotonic() - start)
            prefix = self.logs / f"{len(self.summary['steps']):02d}-{label}"
            for suffix, data in (("stdout", result.stdout), ("stderr", result.stderr)):
                path = prefix.with_suffix("." + suffix + ".log")
                path.write_bytes(data)
                step[suffix] = str(path.relative_to(self.output))
            require(result.returncode == expected_exit, f"{label}: exit {result.returncode}, expected {expected_exit}; see {prefix}")
            return result.stdout.decode("utf-8"), result.stderr.decode("utf-8")
        except OSError as exc:
            step.update(exit_code=None, error=str(exc))
            raise

    def bgm(self, label, *args):
        out, _ = self.run(label, [sys.executable, REPO / "apps/bgm/bgm.py", *args])
        return json.loads(out)

    def decode(self, label, path):
        import numpy as np
        target = self.logs / (label + ".f32")
        self.run(label, [self.ffmpeg, "-v", "error", "-i", path, "-map", "0:a:0",
                         "-c:a", "pcm_f32le", "-f", "f32le", target])
        data = np.fromfile(target, dtype="<f4")
        require(data.size > 0 and data.size % 2 == 0 and np.isfinite(data).all(), label + " invalid PCM")
        return data.reshape(-1, 2)

    def bindings(self, label, timeline):
        target = self.logs / (label + ".json")
        self.run(label, [sys.executable, "-c",
            "import sys,json; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
            "from vtx.api import Ctx,resolve_source_wav; c=Ctx(Path(sys.argv[2])); "
            "Path(sys.argv[3]).write_text(json.dumps({s['id']:str(resolve_source_wav(c,s['id'])) "
            "for s in c.tl['sources'] if 'bgm' in s}),encoding='utf-8')",
            REPO, timeline, target])
        return json.loads(target.read_text(encoding="utf-8"))

    def mutation_cases(self, project, vtx, original_bindings, shared):
        music, ref, timeline = [project / name for name in ("music.json", "keys 音轨.json", "timeline.json")]
        originals = {path: path.read_bytes() for path in (music, ref, timeline)}
        cases = self.summary["mutation_cases"] = {}

        def markers():
            return {str(p): identity(p) for p in sorted((project / "cache/bgm").glob("*/_complete.json"))}

        def derived():
            return {str(p): identity(p) for p in sorted((project / "cache/audio").glob("*__bgm_*.wav"))}

        try:
            for label, path in (("project-name", music), ("ref-notes", ref)):
                before = markers()
                value = json.loads(originals[path])
                if path == music:
                    value["name"] += "-cache-mutation"
                else:
                    value["notes"][0][3] -= 1
                dump(path, value)
                self.run(label + "-prepare", [*vtx, "prepare"])
                after = markers()
                added = sorted(set(after) - set(before))
                require(len(added) == 1, label + " must produce a new shared render marker")
                require(json.loads(Path(added[0]).read_text(encoding="utf-8"))["result"].get("qc_ok") is True,
                        label + " mutated render QC failed")
                cases[label] = {"status": "PASS", "new_marker": {added[0]: after[added[0]]}}
                path.write_bytes(originals[path])
                self.run(label + "-restore-prepare", [*vtx, "prepare"])
            before = markers()
            value = json.loads(originals[timeline])
            value["sources"][3]["bgm"]["output"] = "stem:bass"
            dump(timeline, value)
            self.run("output-bass-prepare", [*vtx, "prepare"])
            require(markers() == before, "output-only change rewrote shared render markers")
            prepared = derived()
            bindings = self.bindings("output-bass-bindings", timeline)
            require(derived() == prepared and markers() == before,
                    "binding inspection created or changed outputs missing from prepare")
            require(bindings["music2"] != original_bindings["music2"], "output change reused keys selected path")
            # Verify the selected bass bytes, not merely a changed filename.
            bass_reference = self.logs / "mutation-bass-reference.wav"
            self.run("output-bass-reference", [self.ffmpeg, "-v", "error", "-i", shared / "stems/bass.bus.wav",
                "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", bass_reference])
            verify_selection(Path(bindings["music2"]), bass_reference, [Path(p) for p in prepared], "music2:bass")
            cases["output-selection"] = {"status": "PASS", "shared_markers_unchanged": before, "bindings": bindings}
            prior_derived = derived()
            value["sources"][3]["bgm"]["output"] = "stem:missing374"
            dump(timeline, value)
            stdout, stderr = self.run("missing-stem-prepare", [*vtx, "prepare"], expected_exit=1)
            diagnostic = stdout + stderr
            require("missing374" in diagnostic and all(name in diagnostic for name in ("keys", "bass", "drums", "one-shot")),
                    "missing stem failure must identify request and available track names")
            require(derived() == prior_derived and markers() == before, "missing stem published bogus audio/shared render")
            cases["missing-stem"] = {"status": "PASS", "no_bogus_derived": True}
        finally:
            for path, content in originals.items():
                path.write_bytes(content)
            require(all(path.read_bytes() == content for path, content in originals.items()), "mutation inputs not restored")
        self.run("mutation-recovery-validate", [*vtx, "validate"])
        self.run("mutation-recovery-prepare", [*vtx, "prepare"])
        cases["restoration"] = {"status": "PASS", "input_bytes_restored": True}

    def execute(self):
        import numpy as np
        self.ffmpeg, probe = shutil.which("ffmpeg"), shutil.which("ffprobe")
        require(self.ffmpeg and probe, "existing ffmpeg and ffprobe on PATH are required")
        self.summary["tools"] = {name: {"path": str(Path(path).resolve()), **identity(Path(path))}
                                 for name, path in (("python", sys.executable), ("ffmpeg", self.ffmpeg), ("ffprobe", probe))}
        self.summary["numpy"] = {"version": np.__version__, "module": identity(Path(np.__file__))}
        for name, path in (("ffmpeg", self.ffmpeg), ("ffprobe", probe)):
            self.run(name + "-version", [path, "-version"])
        sources = [p for root in (REPO / "apps/bgm", REPO / "plugins/bgm-render", REPO / "vtx")
                   for p in root.rglob("*") if p.is_file() and p.suffix in (".py", ".json")
                   and "__pycache__" not in p.parts]
        self.summary["source_hashes"] = {str(p.relative_to(REPO)): identity(p) for p in sorted(sources)}
        project = self.output / "原创 音乐 #374"
        music = create_fixture(project)
        before = {p.name: identity(p)["sha256"] for p in (music, project / "keys 音轨.json")}
        show = self.bgm("structure", "show", "structure", "--json", music)
        require(show["duration_s"] == 16 and len(show["tracks"]) == 4, "structure duration/tracks")
        require(show["tracks"][0]["ref_src"], "keys must use $ref")
        self.bgm("edit-gain", "edit", "set", music, "--target", "tracks/keys", "--payload", '{"gain_db":-9}', "--json")
        edited = self.bgm("edited-structure", "show", "structure", "--json", music)
        require(edited["tracks"][0]["gain_db"] == -9, "CLI edit gain not applied")
        self.bgm("undo", "edit", "undo", music, "--json")
        restored = self.bgm("restored-structure", "show", "structure", "--json", music)
        require(restored == show, "undo structure differs")
        require(before == {p.name: identity(p)["sha256"] for p in (music, project / "keys 音轨.json")}, "undo input bytes differ")
        standalone = self.bgm("standalone-render", "render", music, "-o", self.output / "standalone", "--quiet")
        require(standalone.get("name") == "original-374", "render result name differs from project")
        require(standalone.get("qc_ok") is True, "standalone actual qc_ok must be true")
        self.summary["standalone"] = standalone
        measured = self.bgm("standalone-qc", "qc", standalone["mix"], "--project", music, "-o", self.output / "independent-music-qc.json")
        require(measured.get("ok") is True, "independent music QC must pass")
        self.run("original-video-source", [self.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
            "testsrc2=s=1920x1080:r=30:d=16", "-f", "lavfi", "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000", "-t", "16",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", project / "original-source.mp4"])
        self.summary["inputs"] = {str(p.relative_to(self.output)): identity(p) for p in
            [*(p for p in project.iterdir() if p.is_file()), self.output / "runtime.json",
             self.output / "vtx-config.json", self.output / "原创 采样 #374/原创 one-shot.wav"]}
        vtx = [sys.executable, REPO / "vtx/vtx.py", "-t", project / "timeline.json"]
        self.run("cold-all", [*vtx, "all"])
        markers = list((project / "cache/bgm").glob("*/_complete.json"))
        derived = sorted((project / "cache/audio").glob("*__bgm_*.wav"))
        require(len(markers) == 1 and len(derived) == 3, "expected one shared render and three derived WAVs")
        shared = markers[0].parent
        result = json.loads(markers[0].read_text(encoding="utf-8"))["result"]
        require(result.get("qc_ok") is True, "shared render actual qc_ok must be true")
        tracked = markers + derived
        cold = {str(p): identity(p) for p in tracked}
        self.run("warm-all", [*vtx, "all"])
        require(cold == {str(p): identity(p) for p in tracked}, "warm shared marker/derived hashes or mtimes changed")
        require(list((project / "cache/bgm").glob("*/_complete.json")) == markers, "warm created new shared render")
        require(sorted((project / "cache/audio").glob("*__bgm_*.wav")) == derived, "warm created new derived selection")
        self.summary["warm_cache"] = cold
        self.run("independent-strict-qc", [*vtx, "qc", "--strict"])
        qc = json.loads((project / "out/qc_report.json").read_text(encoding="utf-8"))
        require(qc.get("checks") and all(c["pass"] is True for c in qc["checks"]), "strict QC checks failed")
        require(not qc.get("skipped_probes"), "strict QC unexpectedly exempted probes")
        raw = self.decode("raw-keys", shared / "stems/keys.synth.wav")
        bus = self.decode("bus-keys", shared / "stems/keys.bus.wav")
        require(len(bus) == 16 * 48000 and len(raw) >= len(bus), "bus is not project length / raw unexpectedly short")
        expected = raw[:len(bus)] * np.array([1.3, .7]) * round(10 ** (-6 / 20), 6)
        require(np.max(np.abs(bus - expected)) < 2e-6, "bus does not implement keys gain/pan")
        require(np.max(np.abs(raw[:len(bus)] - bus)) > .01, "raw stem accidentally used as bus")
        self.summary["stem_semantics"] = {"gain_db": -6, "pan": -.6,
            "raw_duration_s": len(raw) / 48000, "bus_duration_s": len(bus) / 48000}
        # Resolve each source through the public API in the same isolated child
        # environment. An unordered set of WAVs would miss swapped selections.
        bindings = self.bindings("source-bindings", project / "timeline.json")
        require(set(bindings) == {"music0", "music1", "music2"}, "missing source bindings")
        for i, source in enumerate((Path(result["master"]), Path(result["premaster"]), shared / "stems/keys.bus.wav")):
            reference = self.logs / f"selection-{i}.wav"
            self.run(f"reference-{i}", [self.ffmpeg, "-v", "error", "-i", source, "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", reference])
            selected = Path(bindings[f"music{i}"])
            verify_selection(selected, reference, derived, f"music{i}")
        self.summary["source_bindings"] = bindings
        self.mutation_cases(project, vtx, bindings, shared)
        self.summary["mutation_cases"]["disabled-plugin"] = {"status": "UNRUN",
            "reason": "Builtin enable state is registry-owned; isolated VTX_CONFIG cannot disable it. No source/registry mutation permitted."}
        self.run("restored-final-all", [*vtx, "all"])
        self.run("restored-final-strict-qc", [*vtx, "qc", "--strict"])
        qc = json.loads((project / "out/qc_report.json").read_text(encoding="utf-8"))
        require(qc.get("checks") and all(c["pass"] is True for c in qc["checks"])
                and not qc.get("skipped_probes"), "restored final strict QC failed or exempted probes")
        self.summary["source_bindings"] = self.bindings("restored-source-bindings", project / "timeline.json")
        self.summary["inputs"] = {name: identity(self.output / name) for name in self.summary["inputs"]}
        final = project / "out/final.mp4"
        out, _ = self.run("final-probe", [probe, "-v", "error", "-show_streams", "-show_format", "-of", "json", final])
        facts = json.loads(out)
        audio = next(s for s in facts["streams"] if s["codec_type"] == "audio")
        require(int(audio["sample_rate"]) == 48000 and audio["channels"] == 2, "final rate/channels")
        pcm = self.decode("final-decoded", final)
        duration, rms = len(pcm) / 48000, float(np.sqrt(np.mean(pcm.astype("float64") ** 2)))
        require(abs(duration - 16) <= 1 / 30 and rms > .001, "final decoded duration or RMS")
        require(all(float(np.sqrt(np.mean(window.astype("float64") ** 2))) > .001
                    for window in np.array_split(pcm[:16 * 48000], 3)), "a selected adjacent window is silent")
        _, err = self.run("decoded-loudness", [self.ffmpeg, "-hide_banner", "-nostats", "-i", final,
            "-map", "0:a:0", "-af", "loudnorm=I=-14:TP=-1:print_format=json", "-f", "null", "-"])
        loud = loudnorm_measurement(err)
        lufs, tp = float(loud["input_i"]), float(loud["input_tp"])
        require(math.isfinite(lufs) and math.isfinite(tp) and abs(lufs + 14) <= 3 and tp <= -1, "final decoded loudness/true peak")
        self.summary["final_audio"] = {"duration_s": duration, "sample_rate": 48000, "channels": 2,
            "rms": rms, "integrated_lufs": lufs, "true_peak_dbtp": tp, "ffprobe": facts}
        self.summary["outputs"] = {str(p.relative_to(self.output)): identity(p)
            for root in (project / "out", shared, self.output / "standalone")
            for p in sorted(root.rglob("*")) if p.is_file()}
        self.summary["status"] = "PASS"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        output = reserve_output(args.output)
    except (OSError, AssertionError) as exc:
        parser.error(str(exc))  # never overwrite an existing run, even its summary
    try:
        runner = Runner(output)
    except Exception as exc:
        dump(output / "summary.json", {"status": "FAIL", "failure": {"phase": "setup", "message": str(exc)}})
        return 1
    try:
        runner.execute()
    except Exception as exc:
        runner.summary["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        (runner.logs / "failure.log").write_text(traceback.format_exc(), encoding="utf-8")
    finally:
        dump(output / "summary.json", runner.summary)
    print(f"{runner.summary['status']}: {output / 'summary.json'}")
    return 0 if runner.summary["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
