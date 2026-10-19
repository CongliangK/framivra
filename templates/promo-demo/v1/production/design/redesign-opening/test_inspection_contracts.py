"""Ordinary media/receipt fixtures only. No browser or security-path negatives."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).absolute().parent))
import inspect_preview as I
import prepare_preview as P
import run_native_preview as R

Q = I.load_native_quality(Path(__file__).absolute().parents[6])


def nominal_probe():
    video = {"codec_type": "video", "index": 0, "width": 1920, "height": 1080, "nb_read_frames": "300",
             "r_frame_rate": "30/1", "avg_frame_rate": "30/1", "time_base": "1/30", "start_pts": 0, "duration_ts": 300}
    audio = {"codec_type": "audio", "index": 1, "sample_rate": "48000", "channels": 2, "codec_name": "aac", "profile": "LC",
             "time_base": "1/48000", "start_pts": 0, "duration_ts": 480000}
    rows = []
    for i in range(300):
        rows += [{"type": "frame", "stream_index": 0, "pts": i, "width": 1920, "height": 1080},
                 {"type": "packet", "stream_index": 0, "pts": i, "duration": 1}]
    rows += [{"type": "packet", "stream_index": 1, "pts": -1024, "duration": 1024, "pos": "prime",
              "side_data_list": [{"side_data_type": "Skip Samples", "skip_samples": 1024, "discard_padding": 0}]}]
    for start in range(0, 480000, 1024):
        rows += [{"type": "frame", "stream_index": 1, "pts": start, "nb_samples": 1024, "channels": 2, "pkt_pos": str(start)},
                 {"type": "packet", "stream_index": 1, "pts": start, "duration": min(1024, 480000-start), "pos": str(start)}]
    return {"streams": [video, audio], "packets_and_frames": rows}


def nominal_backend():
    return {"requested_backend": "cdp", "identity": "", "namespace": "",
            "environment_identity": "vtx.gfx-environment/1|" + "a"*64,
            "environment_evidence": "input-bound; pixel equivalence unproven"}


def nominal_receipt():
    backend = nominal_backend()
    stages = {s: {"ok": True, "gfx_backend": copy.deepcopy(backend)}
              for s in ("validate", "audit", "prepare", "gfx", "video", "audio", "mux", "qc")}
    stages["qc"].update(skipped_probes=[], detail="7/7")
    stages["audio"].update(raw_i=-20., gain_db=6., target=-14.,
        post_loudness={"input_i": -20., "output_i": -14., "gain_db": 6., "target": -14., "tp_dbfs": -2., "sha256": "b"*64})
    receipt = {"gfx_backend": backend, "input_fingerprint": "fixture-only", "stages": stages}
    receipt["quality"] = Q.receipt_quality(receipt)
    return receipt


def nominal_qc():
    # Independent fixed names, not derived from the helper's required set.
    names = ("成片帧数 = 300", "成片时长 ≈ 10.00s", "分辨率 1920x1080",
             "空窗无叠加泄漏 @frame 299", "响度归一 ≈ -14 LUFS",
             "解码域真峰 TP ≤ -1 dBFS", "4 轨并发窗口有能量")
    report = {"checks": [{"name": name, "pass": True, "detail": "SYNTHETIC FIXTURE, NO NATIVE QC"}
                         for name in names], "skipped_probes": []}
    report["quality"] = Q.qc_quality(report)
    return report


class TimingContracts(unittest.TestCase):
    def test_nominal_aac_priming_padding_are_exact_not_tolerance(self):
        r = I.video_contract(nominal_probe())
        self.assertEqual(r["audio_timing"]["priming_samples"], 1024)
        self.assertEqual(r["audio_timing"]["final_padding_samples"], 256)
        self.assertEqual(r["audio_timing"]["decoded_samples"], 480256)
        self.assertEqual(r["audio_timing"]["presented_samples"], 480000)

    def test_rejects_offsets_and_irregular_decoded_pts_even_with_nominal_headers(self):
        for index, bad in ((0, 1), (150, 151), (299, 300)):
            p = nominal_probe(); p["packets_and_frames"][index*2]["pts"] = bad
            with self.assertRaises(ValueError): I.video_contract(p)

    def test_rejects_reordered_decoded_frames(self):
        p = nominal_probe(); rows = p["packets_and_frames"]; rows[2], rows[4] = rows[4], rows[2]
        with self.assertRaises(ValueError): I.video_contract(p)

    def test_accepts_b_frame_packet_order_without_sorting_decoded_frames(self):
        p = nominal_probe(); rows = p["packets_and_frames"]; rows[3], rows[5] = rows[5], rows[3]
        I.video_contract(p)

    def test_rejects_short_audio_despite_claimed_ten_second_header(self):
        p = nominal_probe()
        p["packets_and_frames"] = [r for r in p["packets_and_frames"] if r["stream_index"] == 0 or r["pts"] < 48000]
        with self.assertRaises(ValueError): I.video_contract(p)

    def test_rejects_wrong_actual_end_or_duration(self):
        for stream, key, value in ((0, "duration_ts", 299), (1, "duration_ts", 479999), (1, "start_pts", 1024)):
            p = nominal_probe(); p["streams"][stream][key] = value
            with self.assertRaises(ValueError): I.video_contract(p)
        p = nominal_probe(); p["packets_and_frames"][599]["duration"] = 2
        with self.assertRaises(ValueError): I.video_contract(p)

    def test_rejects_unexplained_priming_and_interior_padding(self):
        for edit in (lambda p: p["packets_and_frames"][600].pop("side_data_list"),
                     lambda p: p["packets_and_frames"][602].update(duration=768),
                     lambda p: p["packets_and_frames"][601].update(nb_samples=768)):
            p = nominal_probe(); edit(p)
            with self.assertRaises(ValueError): I.video_contract(p)

    def test_missing_decoded_payload_and_missing_pts_fail(self):
        p = nominal_probe(); del p["packets_and_frames"][601]
        with self.assertRaises(ValueError): I.video_contract(p)
        p = nominal_probe(); p["packets_and_frames"][0].pop("pts")
        with self.assertRaises(KeyError): I.video_contract(p)

    def test_explicit_final_discard_padding(self):
        p = nominal_probe(); last = p["packets_and_frames"][-1]
        last.update(duration=1024, side_data_list=[{"side_data_type": "Skip Samples", "skip_samples": 0, "discard_padding": 256}])
        self.assertEqual(I.video_contract(p)["audio_timing"]["presented_samples"], 480000)
        last["duration"] = 768  # double subtraction would hide genuine truncation
        with self.assertRaises(ValueError): I.video_contract(p)


class EvidenceContracts(unittest.TestCase):
    def test_receipt_backend_matches_preselected_identity_and_all_stages(self):
        rec = nominal_receipt(); ctx = SimpleNamespace(input_fingerprint="fixture-only", gfx_backend=nominal_backend())
        I.receipt_contract(rec, ctx, I.backend_contract(ctx.gfx_backend))
        for location in (rec, rec["stages"]["gfx"], rec["stages"]["audio"]):
            original = location.pop("gfx_backend")
            with self.assertRaises(ValueError): I.receipt_contract(rec, ctx, I.backend_contract(ctx.gfx_backend))
            location["gfx_backend"] = original
        rec["gfx_backend"]["requested_backend"] = "fixture-fake-no-renderer"
        with self.assertRaises(ValueError): I.receipt_contract(rec, ctx, I.backend_contract(ctx.gfx_backend))

    def test_unknown_missing_or_unpinned_backend_is_unverified(self):
        for key in nominal_backend():
            b = nominal_backend(); b.pop(key)
            with self.assertRaises(ValueError): I.backend_contract(b)
        b = nominal_backend(); b["environment_evidence"] = "unknown; pixel equivalence unproven"
        with self.assertRaises(ValueError): I.backend_contract(b)
        ctx = SimpleNamespace(input_fingerprint="fixture-only", gfx_backend=nominal_backend())
        with self.assertRaises(ValueError): I.receipt_contract(nominal_receipt(), ctx, "f"*64)

    def test_qc_failure_unknown_missing_skipped_or_count_conflict_rejected(self):
        stage = nominal_receipt()["stages"]["qc"]
        I.qc_contract(nominal_qc(), stage)
        mutations = [lambda q: q.update(checks=[]), lambda q: q["checks"][0].update({"pass": False}),
                     lambda q: q["checks"][0].update(status="unknown"), lambda q: q.pop("quality"),
                     lambda q: q.update(skipped_probes=["fixture"]), lambda q: q["checks"].append(q["checks"][0]),
                     lambda q: q["quality"]["evidence"].update(production_gates="failed")]
        for mutate in mutations:
            q = nominal_qc(); mutate(q)
            with self.assertRaises(ValueError): I.qc_contract(q, stage)

    def test_audio_missing_nonfinite_or_contradictory_measurements_rejected(self):
        stage = nominal_receipt()["stages"]["audio"]; audio = {"file_sha256": "b"*64}
        I.audio_measurements(stage, audio)
        for where, key in (("outer", "gain_db"), ("outer", "raw_i"), ("post", "output_i"), ("post", "tp_dbfs"), ("post", "target")):
            for bad in (None, float("nan"), float("inf"), True):
                s = copy.deepcopy(stage); row = s if where == "outer" else s["post_loudness"]; row[key] = bad
                with self.assertRaises(ValueError): I.audio_measurements(s, audio)
        for key, value in (("gain_db", 5.), ("input_i", -21.), ("target", -15.), ("tp_dbfs", 0.)):
            s = copy.deepcopy(stage); s["post_loudness"][key] = value
            with self.assertRaises(ValueError): I.audio_measurements(s, audio)

    def test_coherently_wrong_target_or_gain_does_not_replace_native_contract(self):
        for kind in ("target", "gain"):
            s = nominal_receipt()["stages"]["audio"]
            if kind == "target":
                s["target"] = s["post_loudness"]["target"] = -13.
                s["gain_db"] = s["post_loudness"]["gain_db"] = 7.
            else:
                s["gain_db"] = s["post_loudness"]["gain_db"] = 5.
            with self.assertRaises(ValueError): I.audio_measurements(s, {"file_sha256": "b"*64})


class NativeQualityContracts(unittest.TestCase):
    def test_each_required_check_cannot_disappear_with_synchronized_count(self):
        for index in range(7):
            q = nominal_qc(); del q["checks"][index]
            q["quality"] = Q.qc_quality(q)
            with self.assertRaises(ValueError): I.qc_contract(q, {"skipped_probes": [], "detail": "6/6"})

    def test_duplicate_replacement_cannot_preserve_seven_of_seven(self):
        for index in range(7):
            q = nominal_qc(); q["checks"][index] = copy.deepcopy(q["checks"][(index + 1) % 7])
            with self.assertRaises(ValueError): I.qc_contract(q, nominal_receipt()["stages"]["qc"])

    def test_extra_duplicate_and_unknown_check_profiles_are_unverified(self):
        q = nominal_qc(); q["checks"].append(copy.deepcopy(q["checks"][5]))
        with self.assertRaises(ValueError): I.qc_contract(q, {"skipped_probes": [], "detail": "8/8"})
        q = nominal_qc(); q["checks"][5]["name"] = "unknown replacement"
        with self.assertRaises(ValueError): I.qc_contract(q, nominal_receipt()["stages"]["qc"])

    def test_native_quality_is_exact_complete_annotation_not_art_acceptance(self):
        receipt, report = nominal_receipt(), nominal_qc()
        I.quality_contract(receipt, report, Q)
        self.assertEqual(report["quality"]["evidence"]["visual_content"], "unknown")
        self.assertFalse(report["quality"]["verified_exact"])
        for which in ("receipt", "qc"):
            for key in report["quality"]:
                r, q = nominal_receipt(), nominal_qc()
                doc = r if which == "receipt" else q
                del doc["quality"][key]
                with self.assertRaises(ValueError): I.quality_contract(r, q, Q)
            r, q = nominal_receipt(), nominal_qc()
            (r if which == "receipt" else q).pop("quality")
            with self.assertRaises(ValueError): I.quality_contract(r, q, Q)

    def test_contradictory_failure_visual_and_exact_labels_are_rejected(self):
        for which in ("receipt", "qc"):
            for field, value in (("production_gates", "failed"), ("visual_content", "passed"),
                                 ("content_identity", "passed"), ("strict_exact", "passed")):
                r, q = nominal_receipt(), nominal_qc()
                (r if which == "receipt" else q)["quality"]["evidence"][field] = value
                with self.assertRaises(ValueError): I.quality_contract(r, q, Q)
            for field, value in (("display", "Verified Exact"), ("purpose", "Production"),
                                 ("verified_exact", True), ("reproducible", True),
                                 ("verified_exact", 0), ("annotation_only", 1)):
                r, q = nominal_receipt(), nominal_qc()
                (r if which == "receipt" else q)["quality"][field] = value
                with self.assertRaises(ValueError): I.quality_contract(r, q, Q)


class NativeRecordContracts(unittest.TestCase):
    def exercise(self, run=False, exit_code=0, interrupted=False, mutate=None):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        project = Path(temp.name) / "project"; project.mkdir()
        repo = Path(temp.name) / "engine"; repo.mkdir()
        backend = nominal_backend(); inputs = {"fixture": "not genuine prepared input"}
        infra = SimpleNamespace(Ctx=lambda _: SimpleNamespace(gfx_backend=backend))
        engine = {"commit": "c"*40, "tree": "d"*40, "vtx_status": ""}
        def fake_command(command, **kw):
            kw["stdout"].write(b"fixture native log; no browser or engine executed\n")
            if interrupted: raise KeyboardInterrupt()
            out = project / "out"; out.mkdir(exist_ok=True)
            for name in ("receipt.json", "qc_report.json", "audio.wav", "VERSIONS.json", "final.mp4"):
                (out / name).write_bytes(b"fixture, not real native output")
            return SimpleNamespace(returncode=exit_code)
        with mock.patch.object(I, "check_project", return_value=inputs), mock.patch.object(I, "checked_engine", return_value=infra), \
             mock.patch.object(I, "engine_snapshot", return_value=engine), mock.patch.object(R.subprocess, "run", side_effect=fake_command) as launch:
            if run and (exit_code or interrupted):
                with self.assertRaises((ValueError, KeyboardInterrupt)):
                    R.native_run(project, "e"*64, repo, "c"*40, I.backend_contract(backend), run)
                rec = json.loads((project / "native-run/execution.json").read_text())
                self.assertFalse(rec["completed"]); self.assertEqual(launch.call_count, 1)
                self.assertTrue((project / "native-run/0.log").is_file())
            else:
                result = R.native_run(project, "e"*64, repo, "c"*40, I.backend_contract(backend), run)
                self.assertEqual(launch.call_count, 2 if run else 0)
                if not run:
                    self.assertEqual(list(project.iterdir()), [])
                else:
                    self.assertTrue(result["record"]["completed"])
                    hashes = result["record"]["outputs"]
                    I.run_evidence(project, repo, "c"*40, I.backend_contract(backend), "e"*64, inputs, hashes)
                    if mutate:
                        mutate(result["record"], project / "native-run")
                        (project / "native-run/execution.json").write_bytes(P.json_bytes(result["record"]))
                        with self.assertRaises(ValueError):
                            I.run_evidence(project, repo, "c"*40, I.backend_contract(backend), "e"*64, inputs, hashes)
                        return
                    (project / "native-run/0.log").write_text("changed ordinary log")
                    with self.assertRaises(ValueError): I.run_evidence(project, repo, "c"*40, I.backend_contract(backend), "e"*64, inputs, hashes)

    def test_readonly_plan_launches_nothing_and_writes_nothing(self): self.exercise()
    def test_fixed_all_and_strict_qc_logs_are_bound(self): self.exercise(run=True)
    def test_failure_is_preserved_without_retry(self): self.exercise(run=True, exit_code=7)
    def test_cancellation_is_preserved_without_retry(self): self.exercise(run=True, interrupted=True)


    def test_completed_record_rejects_failure_marker_even_empty(self):
        for key in ("failure", "error"):
            for value in ("KeyboardInterrupt: cancelled", "", None):
                self.exercise(run=True, mutate=lambda r, d: r.update({key: value}))

    def test_zero_exit_rejects_command_error_marker_even_empty(self):
        for index in range(2):
            for key in ("error", "failure"):
                for value in ("KeyboardInterrupt: cancelled", "", None):
                    self.exercise(run=True, mutate=lambda r, d: r["commands"][index].update({key: value}))

    def test_record_backend_digest_must_match_verified_backend(self):
        self.exercise(run=True, mutate=lambda r, d: r.update(backend_sha256="f"*64))
        self.exercise(run=True, mutate=lambda r, d: r.pop("backend_sha256"))

    def test_log_header_must_match_command_and_cwd_despite_correct_hash(self):
        def mutation(kind):
            def apply(record, directory):
                row = record["commands"][0]
                header = {"argv": list(row["argv"]), "cwd": row["cwd"]}
                if kind == "argv": header["argv"][-1] = "draft"
                if kind == "cwd": header["cwd"] += "/other"
                if kind == "missing": header.pop("argv")
                data = (json.dumps(header) + "\nSYNTHETIC FIXTURE\n").encode()
                if kind == "malformed": data = b"not JSON\nSYNTHETIC FIXTURE\n"
                if kind == "unterminated": data = json.dumps(header).encode()
                (directory / "0.log").write_bytes(data)
                row["log_sha256"] = P.sha(data)
            return apply
        for kind in ("argv", "cwd", "missing", "malformed", "unterminated"):
            self.exercise(run=True, mutate=mutation(kind))
