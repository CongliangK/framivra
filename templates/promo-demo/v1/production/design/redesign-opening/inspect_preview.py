#!/usr/bin/env python3
"""Read-only evidence for the formal 300-frame output; no rendering or repair.

MIT, 2026 Framivra contributors. Run from the reviewed source package after native
vtx all and qc --strict, with the same process-local renderer environment. Output
JSON goes to stdout; redirect it to a new evidence file, not a frozen source file.
"""
import argparse
import array
from fractions import Fraction
import importlib
import importlib.util
import hashlib
import io
import json
import math
import os
import re
from pathlib import Path
import stat
import subprocess
import sys
import wave

sys.dont_write_bytecode = True
import prepare_preview as P


def wav_identity(data):
    with wave.open(io.BytesIO(data), "rb") as wav:
        values = (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getnframes(), wav.getcomptype())
        P.require(values == (48000, 2, 2, 480000, "NONE"), "Expected exactly 480000 stereo PCM16 sample frames at 48kHz")
        pcm = wav.readframes(wav.getnframes())
    P.require(len(pcm) == 480000 * 4, "Truncated WAV payload")
    samples = array.array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    floats = array.array("f", (n / 32768 for n in samples))
    if sys.byteorder != "little":
        floats.byteswap()
    return {"file_sha256": P.sha(data), "sample_rate": 48000, "channels": 2,
            "sample_frames": 480000, "pcm_s16le_sha256": P.sha(pcm),
            "decoded_f32le_sha256": P.sha(floats.tobytes()),
            "f32_representation": "native-rate interleaved little-endian float32, no resampling or gain",
            "same_as_120_second_postmix_prefix": "NOT_ASSERTED_INDEPENDENT_TEN_SECOND_NORMALIZATION"}


def integer(value, label):
    P.require(type(value) is int or (isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value)),
              "Missing/noninteger " + label)
    return int(value)


def video_contract(probe):
    """Native MP4 contract, using decoded frame order and exact rational timestamps.

    AAC-LC padding is not a duration tolerance: demuxed packet presentation spans
    must end at sample 480000 and each span must have real decoded samples behind
    it. Only the final AAC block may extend beyond that end (encoder padding).
    """
    streams = probe["streams"]
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    P.require(len(video) == len(audio) == 1 and len(streams) == 2, "Expected one video and one audio stream")
    v, a = video[0], audio[0]
    P.require((v["width"], v["height"], integer(v["nb_read_frames"], "decoded video count"),
               Fraction(v["r_frame_rate"]), Fraction(v["avg_frame_rate"])) ==
              (1920, 1080, 300, Fraction(30), Fraction(30)), "Full 1080p30 and 300 decoded frames required")
    P.require((integer(a["sample_rate"], "audio sample rate"), a["channels"], a["codec_name"], a["profile"]) ==
              (48000, 2, "aac", "LC"), "Expected native stereo 48kHz AAC-LC final audio")
    for stream in (v, a):
        tb = Fraction(stream["time_base"])
        P.require(tb > 0 and integer(stream["start_pts"], "stream start") == 0 and
                  integer(stream["duration_ts"], "stream duration") * tb == 10,
                  "Each stream must present exactly [0,10) seconds")
    rows = probe["packets_and_frames"]  # ffprobe -show_frames really decodes, no input seek
    def selected(kind, stream):
        return [r for r in rows if r.get("type") == kind and r.get("stream_index") == stream["index"]]
    vf, vp = selected("frame", v), selected("packet", v)
    vtb = Fraction(v["time_base"])
    P.require(len(vf) == len(vp) == 300, "Need 300 decoded video frames and their packets")
    frame_pts = []
    for i, frame in enumerate(vf):  # never sort; repeated/reordered decoded PTS fail
        pts = integer(frame["pts"], "video frame PTS")
        P.require(pts * vtb == Fraction(i, 30), "Decoded video PTS differs at frame " + str(i))
        P.require((frame["width"], frame["height"]) == (1920, 1080), "Decoded video dimensions changed")
        frame_pts.append(pts)
    packets_by_pts = {}
    for packet in vp:
        pts = integer(packet["pts"], "video packet PTS")
        P.require(pts not in packets_by_pts and integer(packet["duration"], "video packet duration") * vtb == Fraction(1, 30),
                  "Repeated video packet PTS or non-1/30 duration")
        packets_by_pts[pts] = packet
    P.require(set(packets_by_pts) == set(frame_pts), "Video packet/frame PTS differ")
    # Last frame's real packet duration closes [299/30,10), not nominal fps metadata.
    P.require((frame_pts[-1] + integer(packets_by_pts[frame_pts[-1]]["duration"], "last video duration")) * vtb == 10,
              "Video decoded presentation does not end at ten seconds")

    af, ap = selected("frame", a), selected("packet", a)
    atb = Fraction(a["time_base"])
    def sample_time(value, label):
        n = integer(value, label) * atb * 48000
        P.require(n.denominator == 1, "Audio timestamp is off sample grid: " + label)
        return n.numerator
    P.require(af and ap, "No actual decoded audio")
    # Bind decoded AAC frames to their demuxed packets; packet offsets are emitted
    # by ffprobe, not inferred from a stream-duration header.
    by_pos = {}
    decoded_end = 0
    for frame in af:
        start = sample_time(frame["pts"], "audio decoded PTS")
        count = integer(frame["nb_samples"], "decoded audio samples")
        P.require(start == decoded_end and 0 < count <= 1024 and frame["channels"] == 2,
                  "Decoded AAC samples are missing, overlapping, or malformed")
        pos = str(frame["pkt_pos"])
        P.require(pos not in by_pos, "Ambiguous AAC packet/frame mapping")
        by_pos[pos] = (start, count)
        decoded_end += count
    end = 0; priming = 0; padding = 0; used = set(); spans = []
    for i, packet in enumerate(ap):
        start = sample_time(packet["pts"], "audio packet PTS")
        duration = sample_time(packet["duration"], "audio packet duration")
        side = [d for d in packet.get("side_data_list", []) if d.get("side_data_type") == "Skip Samples"]
        P.require(len(side) <= 1, "Ambiguous AAC skip metadata")
        skip = integer(side[0].get("skip_samples", 0), "AAC priming") if side else 0
        discard = integer(side[0].get("discard_padding", 0), "AAC discard padding") if side else 0
        P.require(0 <= skip <= 1024 and 0 <= discard < 1024 and 0 < duration <= 1024, "Invalid AAC skip/duration")
        pos = str(packet["pos"])
        if start < 0:
            P.require(i == 0 and start == -skip and duration == skip and discard == 0 and pos not in by_pos,
                      "Unexplained AAC priming; no generic offset tolerance")
            priming = skip
            continue
        P.require(skip == 0 and start == end and pos in by_pos, "AAC packet gap/offset or missing decoded payload")
        actual_start, count = by_pos[pos]; used.add(pos)
        P.require(actual_start == start, "AAC packet/frame PTS mismatch")
        # Native MP4 expresses end padding by shortened final packet duration.
        # Explicit discard is supported only if duration still includes it.
        valid = duration - discard
        P.require(0 < valid <= count, "AAC valid duration exceeds real decoded samples")
        if i != len(ap) - 1:
            P.require(valid == count == 1024 and discard == 0, "Interior AAC truncation/padding")
        else:
            padding = count - valid
            P.require(discard == 0 or (duration == count and padding == discard), "Contradictory AAC padding metadata")
        end += valid
        spans.append([start, count, valid])
    P.require(used == set(by_pos) and end == 480000 and decoded_end == 480000 + padding and 0 <= padding < 1024,
              "Final AAC does not actually cover exactly 480000 presented samples")
    return {"video": v, "audio": a, "frame_count_method": "ffprobe sequential decoded frames, integer PTS/time_base and packet durations",
            "video_timing": {"frame_pts": frame_pts, "time_base": v["time_base"], "start_seconds": 0, "end_seconds": 10,
                             "grid": "frame i at i/30, i=0..299; exact rational equality, no tolerance"},
            "audio_timing": {"presented_samples": end, "decoded_samples": decoded_end,
                             "priming_samples": priming, "final_padding_samples": padding,
                             "decoded_spans_start_count_valid": spans,
                             "rule": "packet-valid spans [0,480000), each backed by decoded AAC samples"}}


def probe_output(path):
    command = ["ffprobe", "-v", "error", "-show_streams", "-show_frames", "-show_packets", "-of", "json", str(path)]
    result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=180)
    P.require(not result.stderr.strip(), "ffprobe reported decode/demux errors")
    return video_contract(json.loads(result.stdout)), command, P.sha(result.stdout.encode())

def check_project(project, expected_manifest_sha256, source=P.HERE, production=P.PRODUCTION):
    files = P.load_source(expected_manifest_sha256, source, production)
    P.validate_timeline(files["preview-timeline.json"])
    expected = {"scene/" + n: P.sha(files[n]) for n in ("index.html", "geometry.js", "opening.js")}
    expected.update({"metadata/" + n: P.sha(files[n]) for n in ("cues.json", "stems.json", "source-manifest.json")})
    expected["timeline.json"] = P.sha(files["preview-timeline.json"])
    expected.update({s["path"]: s["sha256"] for s in json.loads(files["stems.json"])["stems"]})
    with P.directory(project) as fd:
        binding_bytes = P.read_file(fd, "source-binding.json", 1024 * 1024)
        binding = json.loads(binding_bytes)
        P.require(binding["format"] == "framivra-isolated-opening-binding/1" and
                  binding["source_manifest_sha256"] == expected_manifest_sha256 and
                  binding["files"] == expected, "Project binding differs from reviewed source/inputs")
        for name, digest in expected.items():
            path = Path(name)
            with P.directory(path.parent, fd) as sub:
                P.require(P.sha(P.read_file(sub, path.name)) == digest, "Changed prepared input: " + name)
    return {"source_manifest_sha256": expected_manifest_sha256, "binding_sha256": P.sha(binding_bytes),
            "input_files_verified": len(expected)}


def check_mux_ledger(ledger, final_md5, final_bytes, timeline_md5, audio_md5):
    row = next((r for r in reversed(ledger["versions"]) if r.get("product") == "final.mp4"), None)
    P.require(row is not None and row.get("cmd") == "mux" and not row.get("superseded"), "Missing current formal mux ledger entry")
    for key, value in (("md5", final_md5), ("bytes", final_bytes),
                       ("timeline_md5", timeline_md5), ("audio_md5", audio_md5)):
        P.require(row.get(key) == value, "Final output differs from formal mux ledger: " + key)
    return row


def checked_engine(repo, expected_commit):
    P.require(bool(re.fullmatch(r"[0-9a-f]{40}", expected_commit or "")), "Exact engine commit required")
    with P.directory(repo):
        pass
    actual = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    P.require(actual == expected_commit, "Engine HEAD differs from reviewed commit")
    status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all", "--", "vtx"],
                            cwd=repo, check=True, capture_output=True, text=True).stdout
    P.require(not status.strip(), "Engine source has local changes")
    P.require("vtx.infra" not in sys.modules, "Use a fresh inspector process")
    sys.path.insert(0, str(repo))
    infra = importlib.import_module("vtx.infra")
    P.require(Path(infra.__file__).resolve() == (repo / "vtx/infra.py").resolve(), "Unexpected imported engine")
    return infra


def backend_contract(value):
    P.require(isinstance(value, dict), "Missing recorded backend identity")
    name = value.get("requested_backend")
    P.require(name in ("cdp", "cli", "cef", "electron"), "Unknown recorded backend")
    P.require(isinstance(value.get("namespace"), str) and isinstance(value.get("identity"), str), "Incomplete backend identity")
    if name in ("cdp", "cli"):
        P.require(value["identity"] == "", "Unexpected legacy backend identity")
    else:
        prefix = "cef-alpha-exact-v1|" if name == "cef" else "electron-alpha/1|"
        P.require(value["identity"].startswith(prefix), "Incomplete native backend identity")
    P.require(re.fullmatch(r"vtx\.gfx-environment/1\|[0-9a-f]{64}", value.get("environment_identity", "")) and
              value.get("environment_evidence") == "input-bound; pixel equivalence unproven",
              "Backend environment missing/unknown: cannot verify output identity")
    return P.sha(P.json_bytes(value))


def receipt_contract(receipt, context, expected_backend_sha256):
    P.require(receipt.get("input_fingerprint") == context.input_fingerprint, "Stale formal receipt")
    current = context.gfx_backend
    P.require(backend_contract(current) == expected_backend_sha256, "Inspector environment differs from preselected backend")
    P.require(receipt.get("gfx_backend") == current, "Receipt backend differs from inspector environment")
    stages = receipt.get("stages", {})
    required = ("validate", "audit", "prepare", "gfx", "video", "audio", "mux", "qc")
    for name in required:
        row = stages.get(name, {})
        P.require(row.get("ok") is True, "Missing/failed formal stage: " + name)
        P.require(row.get("gfx_backend") == current, "Missing/contradictory stage backend: " + name)
    P.require(stages["qc"].get("skipped_probes") == [], "Run native qc --strict; skipped checks are not accepted")
    return stages, required


# Fixed opening only: one bottom gfx, no overlays/subtitles/fades, four full-window
# audio events. Native vtx/qc.py emits these seven names; its diagnostics' rule_id
# is NOT persisted in qc_report.checks. A different profile must stay unverified.
OPENING_QC_NAMES = frozenset((
    "成片帧数 = 300", "成片时长 ≈ 10.00s", "分辨率 1920x1080",
    "空窗无叠加泄漏 @frame 299", "响度归一 ≈ -14 LUFS",
    "解码域真峰 TP ≤ -1 dBFS", "4 轨并发窗口有能量"))


def qc_contract(report, stage):
    checks = report.get("checks")
    P.require(isinstance(checks, list) and checks, "Missing native QC checks")
    for row in checks:
        P.require(isinstance(row, dict) and row.get("pass") is True and isinstance(row.get("name"), str) and row["name"],
                  "Failed/unknown/malformed native QC check")
        P.require("status" not in row or row["status"] in ("pass", "passed"), "QC check has nonpass status")
    names = [row["name"] for row in checks]
    P.require(len(names) == len(set(names)) and set(names) == OPENING_QC_NAMES,
              "Native QC required opening checks missing, duplicated, or unknown")
    P.require(report.get("skipped_probes") == stage.get("skipped_probes") == [], "QC has missing/skipped probes")
    quality = report.get("quality", {})
    P.require(quality.get("schema") == "vtx.quality/1" and
              quality.get("evidence", {}).get("production_gates") == "passed", "Missing/contradictory native QC quality")
    P.require(stage.get("detail") == f"{len(checks)}/{len(checks)}", "QC report count differs from stage receipt")
    return {"checks_verified": len(checks), "report": report,
            "scope": "native technical QC, not visual_content or listening approval"}


def load_native_quality(repo):
    """Use the pinned engine's existing pure annotation functions, no new labels."""
    spec = importlib.util.spec_from_file_location("opening_pinned_native_quality", repo / "vtx/quality.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def quality_contract(receipt, report, native_quality):
    # A complete native label is required. JSON comparison also distinguishes
    # false from numeric 0; Python dict equality alone does not.
    for document, calculate in ((receipt, native_quality.receipt_quality),
                                (report, native_quality.qc_quality)):
        P.require(P.json_bytes(document.get("quality")) == P.json_bytes(calculate(document)),
                  "Missing/contradictory native quality annotation")


def audio_measurements(stage, audio):
    post = stage.get("post_loudness", {})
    def number(row, key):
        n = row.get(key)
        P.require(type(n) in (int, float) and math.isfinite(n), "Missing/nonfinite audio measurement: " + key)
        return n
    gain = number(stage, "gain_db"); raw = number(stage, "raw_i"); target = number(stage, "target")
    pgain = number(post, "gain_db"); pin = number(post, "input_i"); pout = number(post, "output_i")
    ptarget = number(post, "target"); peak = number(post, "tp_dbfs")
    P.require(post.get("sha256") == audio["file_sha256"], "Actual PCM WAV differs from formal audio receipt")
    P.require(raw == pin and target == ptarget == -14.0 and pgain == target - raw and gain == round(pgain, 2),
              "Audio gain/loudness receipt contradicts the original native -14 LUFS mix contract")
    P.require(abs(pout - target) <= 3 and peak <= -1, "Audio record contradicts native mechanism acceptance")
    return {"raw_i": raw, "gain_db": pgain, "target": target, "output_i": pout, "tp_dbfs": peak,
            "content_loudness_deviation_lu": abs(pout - target),
            "listening": "UNRUN; measurements are the native producer record, not a listening claim"}


def engine_snapshot(repo, expected_commit):
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    result = {"commit": git("rev-parse", "HEAD"), "tree": git("rev-parse", "HEAD^{tree}"),
              "vtx_status": git("status", "--porcelain", "--untracked-files=all", "--", "vtx")}
    P.require(result["commit"] == expected_commit and not result["vtx_status"], "Engine identity changed/dirty")
    return result


def run_evidence(project, engine_repo, expected_engine_commit, expected_backend_sha256,
                 expected_manifest_sha256, inputs, output_hashes):
    """Consume raw fixed-CLI execution records; this is not producer attestation."""
    with P.directory(project / "native-run") as fd:
        data = P.read_file(fd, "execution.json", 4 * 1024 * 1024)
        record = json.loads(data)
        P.require(record.get("format") == "framivra-opening-native-run/1" and record.get("completed") is True,
                  "Missing/incomplete formal execution record")
        P.require("failure" not in record and "error" not in record, "Successful execution carries a failure/error marker")
        P.require(record.get("project") == str(project) and record.get("engine_repo") == str(engine_repo) and
                  record.get("source_manifest_sha256") == expected_manifest_sha256 and
                  record.get("inputs_before") == record.get("inputs_after") == inputs,
                  "Execution inputs/project differ")
        expected_engine = engine_snapshot(engine_repo, expected_engine_commit)
        P.require(record.get("engine_before") == record.get("engine_after") == expected_engine,
                  "Execution engine identity differs from current inspected engine")
        P.require(record.get("backend_before") == record.get("backend_after") and
                  backend_contract(record.get("backend_before")) == record.get("backend_sha256") == expected_backend_sha256,
                  "Execution backend configuration differs")
        P.require(record.get("outputs") == output_hashes, "Output files differ from recorded native run")
        commands = record.get("commands", [])
        P.require(len(commands) == 2 and isinstance(record.get("python"), str), "Missing formal command records")
        prefix = [record["python"], "-m", "vtx", "-t", str(project / "timeline.json")]
        for i, suffix in enumerate((["all"], ["qc", "--strict"])):
            row = commands[i]
            P.require("error" not in row and "failure" not in row, "Successful command carries an error/failure marker")
            P.require(row.get("argv") == prefix + suffix and row.get("cwd") == str(engine_repo) and
                      type(row.get("exit_code")) is int and row["exit_code"] == 0, "Wrong/failed formal command")
            log = P.read_file(fd, str(i) + ".log", 32 * 1024 * 1024)
            P.require(log and row.get("log_sha256") == P.sha(log), "Missing/changed formal command log")
            first, separator, _ = log.partition(b"\n")
            P.require(separator and json.loads(first) == {"argv": row["argv"], "cwd": row["cwd"]},
                      "Fixed command log header contradicts execution record")
    return record, P.sha(data)

def inspect(project, expected_manifest_sha256, engine_repo, expected_engine_commit, expected_backend_sha256):
    project, engine_repo = Path(project).absolute(), Path(engine_repo).absolute()
    inputs = check_project(project, expected_manifest_sha256)
    infra = checked_engine(engine_repo, expected_engine_commit)
    context = infra.Ctx(project / "timeline.json")  # input identity only, no stage/browser invocation
    with P.directory(project / "out") as fd:
        receipt_bytes = P.read_file(fd, "receipt.json", 8 * 1024 * 1024)
        receipt = json.loads(receipt_bytes)
        stages, required = receipt_contract(receipt, context, expected_backend_sha256)
        qc_bytes = P.read_file(fd, "qc_report.json", 8 * 1024 * 1024)
        report = json.loads(qc_bytes)
        qc = qc_contract(report, stages["qc"])
        quality_contract(receipt, report, load_native_quality(engine_repo))
        audio_bytes = P.read_file(fd, "audio.wav", 4 * 1024 * 1024)
        audio = wav_identity(audio_bytes)
        measurements = audio_measurements(stages["audio"], audio)
        raw = os.open("final.mp4", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(raw, "rb") as stream:
            before = os.fstat(stream.fileno())
            P.require(stat.S_ISREG(before.st_mode), "Final output must be a regular file")
            h = hashlib.sha256(); md5 = hashlib.md5()
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(block); md5.update(block)
            after = os.fstat(stream.fileno())
            P.require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns), "Final output changed during inspection")
            final_hash = h.hexdigest()
        versions_bytes = P.read_file(fd, "VERSIONS.json", 8 * 1024 * 1024)
        mux_entry = check_mux_ledger(json.loads(versions_bytes), md5.hexdigest(), after.st_size,
                                     context.timeline_md5, hashlib.md5(audio_bytes).hexdigest())
    video, command, probe_sha = probe_output(project / "out/final.mp4")
    output_hashes = {"receipt.json": P.sha(receipt_bytes), "qc_report.json": P.sha(qc_bytes),
                     "audio.wav": audio["file_sha256"], "VERSIONS.json": P.sha(versions_bytes), "final.mp4": final_hash}
    execution, execution_sha = run_evidence(project, engine_repo, expected_engine_commit, expected_backend_sha256,
                                            expected_manifest_sha256, inputs, output_hashes)
    # Do not report identities for a project/output changed during the probe.
    P.require(check_project(project, expected_manifest_sha256) == inputs, "Prepared inputs changed during inspection")
    with P.directory(project / "out") as fd:
        P.require(P.sha(P.read_file(fd, "qc_report.json", 8 * 1024 * 1024)) == P.sha(qc_bytes), "QC report changed during inspection")
        P.require(P.sha(P.read_file(fd, "receipt.json", 8 * 1024 * 1024)) == P.sha(receipt_bytes), "Receipt changed during inspection")
        P.require(P.sha(P.read_file(fd, "audio.wav", 4 * 1024 * 1024)) == audio["file_sha256"], "Audio changed during inspection")
        P.require(P.sha(P.read_file(fd, "VERSIONS.json", 8 * 1024 * 1024)) == P.sha(versions_bytes), "Mux ledger changed during inspection")
        final_now = os.stat("final.mp4", dir_fd=fd, follow_symlinks=False)
        P.require((final_now.st_dev, final_now.st_ino, final_now.st_size, final_now.st_mtime_ns) ==
                  (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), "Final output identity changed during inspection")
    return {"status": "FORMAL_OUTPUT_CONTRACTS_AND_RECORDED_EVIDENCE_VERIFIED", "inputs": inputs,
            "engine_commit": expected_engine_commit,
            "inspection_backend_configuration": context.gfx_backend,
            "producer_receipt_backend": receipt["gfx_backend"], "producer_gfx_stage": stages["gfx"],
            "runtime_backend_attestation": "NOT_ESTABLISHED; existing receipt/configuration evidence only",
            "native_execution_record": execution, "native_execution_sha256": execution_sha,
            "qc_report_sha256": P.sha(qc_bytes), "native_qc": qc, "audio_measurements": measurements,
            "receipt_sha256": P.sha(receipt_bytes), "receipt_input_fingerprint": context.input_fingerprint,
            "completed_stages": list(required), "audio": audio, "audio_stage": stages["audio"],
            "final_sha256": final_hash, "formal_mux_entry": mux_entry, "mux_ledger_sha256": P.sha(versions_bytes), "final_probe": video, "probe_command": command, "probe_stdout_sha256": probe_sha,
            "tool_versions": {tool: subprocess.run([tool, "-version"], check=True, capture_output=True, text=True).stdout.splitlines()[0]
                              for tool in ("ffmpeg", "ffprobe")},
            "actual_real_time_viewing": "UNRUN_BY_THIS_CHECK", "audio_listening": "UNRUN_BY_THIS_CHECK",
            "user_visual_acceptance": "PENDING", "actual_S2_handoff": "NOT_IMPLEMENTED"}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--expected-source-manifest-sha256", required=True)
    ap.add_argument("--engine-repo", type=Path, required=True)
    ap.add_argument("--expected-engine-commit", required=True)
    ap.add_argument("--expected-backend-sha256", required=True)
    args = ap.parse_args()
    try:
        result = inspect(args.project, args.expected_source_manifest_sha256, args.engine_repo, args.expected_engine_commit, args.expected_backend_sha256)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print("Preview inspection failed: " + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
