"""Bounded fixture tests for the explicit native-run binder, not execution proof.

Real temporary Git commits, no-follow reads, Ctx, audio graphs, document checks,
asset copying and rollback run normally. Only media decoding and the external
Node build/preflight processes use fixtures. No native mix or renderer is run.
Run: python -m unittest discover -s tests -p test_bind_native_postmix.py -v
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[3]
SPEC = importlib.util.spec_from_file_location("native_binding", ROOT / "scripts/bind_native_postmix.py")
binder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(binder)
# Import the real core before temporary fixture repositories enter sys.path.
sys.path.insert(0, str(REPO))
from vtx.infra import Ctx
from vtx.stages import _audio_graph
from vtx import gfx_backend, hygiene
from vtx.pluginlib import hooks
sys.path.pop(0)
REAL_RUN = subprocess.run


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")
    return sha(path.read_bytes())


def snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mode & 0o777)
            for p in root.rglob("*") if p.is_file()}


@unittest.skipUnless(shutil.which("git"), "git required for real source-identity fixtures")
class NativeBindingFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A full contract-sized silence oracle, with no audio decoder dependency.
        cls.pcm = bytes(5760000 * 8)
        cls.pcm_hash = sha(cls.pcm)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo, self.projection = self.base / "repo", self.base / "native-run"
        self.project = self.repo / binder.PREFIX
        self.out = self.base / "bound"
        self.repo.mkdir()
        self.projection.mkdir()
        self.addCleanup(setattr, sys, "path", list(sys.path))
        clean_env = {k: v for k, v in os.environ.items()
                     if not k.startswith(("DYLD_", "LD_", "VTX_GFX"))}
        self.use(mock.patch.dict(os.environ, clean_env, clear=True))
        paths = {"timeline.json", "scripts/preflight.py", "scripts/build.js",
                 "scripts/run_native_audio_projection.py", "audio/stems.json",
                 "media/paper-recipe.json", *binder.DERIVED}
        for relative in paths:
            target = self.project / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        for relative in binder.CORE_FILES:
            target = self.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / relative, target)
        self.stems = json.loads((self.project / "audio/stems.json").read_text())
        self.payloads = {}
        for stem in self.stems["stems"]:
            payload = ("fixture media " + stem["id"]).encode()
            self.payloads[stem["path"]] = payload
            stem.update(bytes=len(payload), sha256=sha(payload))
            target = self.projection / stem["path"]
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(payload)
        write_json(self.project / "audio/stems.json", self.stems)
        paper = json.loads((self.project / "media/paper-recipe.json").read_text())
        self.payloads[binder.ASSETS.PAPER] = b"fixture paper media"
        paper["sha256"] = sha(self.payloads[binder.ASSETS.PAPER])
        paper["probe"]["format"]["size"] = str(len(self.payloads[binder.ASSETS.PAPER]))
        write_json(self.project / "media/paper-recipe.json", paper)
        for args in (("init", "-q"), ("add", "."),
                     ("-c", "user.name=Binding Fixture", "-c", "user.email=fixture@example.invalid",
                      "commit", "-qm", "Bounded source fixture")):
            self.git(*args)
        self.commit = self.git("rev-parse", "HEAD").decode().strip()
        # Runtime assets deliberately remain untracked, like the private pack.
        for relative, payload in self.payloads.items():
            (self.project / relative).write_bytes(payload)
        self.full = json.loads((self.project / "timeline.json").read_text())
        projected = copy.deepcopy(self.full)
        projected["project"].pop("cache_dependencies", None)
        projected["sources"] = [s for s in projected["sources"] if s["id"] in binder.ASSETS.STEMS]
        projected["video_tracks"], projected["subtitles"] = [], []
        timeline_hash = write_json(self.projection / "timeline.json", projected)
        self.relevant = {k: projected[k] for k in ("project", "sources", "audio_tracks")}
        context = Ctx(self.projection / "timeline.json")
        self.graph = json.loads(json.dumps(_audio_graph(context)))
        (self.projection / "out").mkdir()
        self.wav = self.projection / "out/audio.wav"
        self.wav.write_bytes(b"fixture WAV; decoder is intentionally mocked")
        self.wav_hash = sha(self.wav.read_bytes())
        self.loudness = dict(input_i=-14.9, output_i=-14.6, gain_db=0.9,
                             target=-14.0, tp_dbfs=-3.9, sha256=self.wav_hash)
        self.receipt = {"timeline": timeline_hash, "input_fingerprint": context.input_fingerprint,
                        "stages": {s: {"ok": True} for s in ("validate", "prepare", "audio")}}
        self.receipt["stages"]["audio"]["post_loudness"] = self.loudness
        self.tools = {}
        self.tool_versions = {t: t + " version 7.1.5-fixture" for t in ("ffmpeg", "ffprobe")}
        for tool in ("ffmpeg", "ffprobe"):
            executable = self.base / tool
            executable.write_bytes(("fixture executable " + tool).encode())
            self.tools[tool] = str(executable)
        # Synthetic private fields must never appear in the portable outputs.
        self.private = "/" + "/".join(("home", "fixture-owner", "private-native-run"))
        self.proof = dict(format="framivra-audio-projection-proof/1",
            status="NATIVE_AUDIO_ONLY_PROJECTION_COMPLETED", full_project_pass=False,
            browser_or_gfx_called=False, user_full_render_postmix_check_required=True,
            core_commit=self.commit,
            core_source_hashes={p: sha((self.repo / p).read_bytes()) for p in binder.CORE_FILES},
            projection_runner_sha256=sha((self.project / "scripts/run_native_audio_projection.py").read_bytes()),
            full_timeline_sha256=sha((self.project / "timeline.json").read_bytes()),
            projection_timeline_sha256=timeline_hash, audio_relevant_inputs=self.relevant,
            audio_inputs_signature=sha(json.dumps(self.relevant, sort_keys=True, separators=(",", ":")).encode()),
            source_sha256={s["id"]: s["sha256"] for s in self.stems["stems"]},
            active_plugins=[], graph_identical=True, target_lufs=-14.0, graph=self.graph,
            graph_sha256=sha(json.dumps(self.graph, sort_keys=True).encode()),
            postmix_sha256=self.wav_hash,
            commands=[dict(argv=["python", "-m", "vtx", "-t", str(self.projection / "timeline.json"), s],
                           cwd=self.private, exit_code=0) for s in ("validate", "prepare", "audio")],
            toolchain={t: dict(path=self.private + "/bin/" + t,
                              version=t + " version 7.1.5-fixture", sha256=sha(Path(p).read_bytes()))
                       for t, p in self.tools.items()})
        self.save_receipt()
        self.args = argparse.Namespace(repo=self.repo, project=self.project, projection=self.projection,
            out=self.out, expected_commit=self.commit, expected_run_commit=self.commit,
            expected_wav_sha256=self.wav_hash, expected_pcm_sha256=self.pcm_hash)
        self.decode = self.use(mock.patch.object(binder.PREFLIGHT, "native_pcm", return_value=(self.pcm, self.pcm_hash)))
        self.use(mock.patch.object(binder.shutil, "which", side_effect=self.tools.get))
        self.asset_decode = self.use(mock.patch.object(binder.ASSETS, "validate_payload"))
        self.build_hook = lambda target: None
        self.preflight_hook = lambda target: None
        self.preflight = dict(source_errors=[], waveform={"supplied_postmix_pcm_verified": True,
                              "supplied_postmix_bins_verified": True}, production_ready=False)
        self.commands = []
        self.use(mock.patch.object(binder.subprocess, "run", side_effect=self.external_stage))

    def use(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def git(self, *args):
        return REAL_RUN(["git", *args], cwd=self.repo, check=True, capture_output=True, timeout=30).stdout

    def save_proof(self):
        write_json(self.projection / "projection-proof.json", self.proof)

    def save_receipt(self):
        self.proof["receipt_sha256"] = write_json(self.projection / "out/receipt.json", self.receipt)
        self.save_proof()

    def external_stage(self, argv, **kwargs):
        if argv[0] == "git":
            return REAL_RUN(argv, **kwargs)
        if argv[-1] == "-version":
            return subprocess.CompletedProcess(argv, 0, self.tool_versions[Path(argv[0]).name] + "\n", "")
        self.assertFalse((self.out / "binding-result.json").exists())
        target = self.out / "project"
        pinned = kwargs["pass_fds"]
        self.assertTrue(pinned)
        identity = target.stat()
        project_fds = [fd for fd in pinned if (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
                       == (identity.st_dev, identity.st_ino)]
        self.assertEqual(len(project_fds), 1)
        self.assertTrue(callable(kwargs["preexec_fn"]))
        with mock.patch.object(os, "fchdir") as change_directory:
            kwargs["preexec_fn"]()
        change_directory.assert_called_once_with(project_fds[0])
        self.commands.append(argv)
        if argv[0] == "node":
            self.assertEqual(argv, ["node", "scripts/build.js", "--draft"])
            self.build_hook(target)
            return subprocess.CompletedProcess(argv, 0, '{"fixture_build":true}', "")
        self.assertEqual(argv, [sys.executable, "scripts/preflight.py", "--source-only",
                                "--postmix", str(self.wav)])
        self.preflight_hook(target)
        return subprocess.CompletedProcess(argv, 0, json.dumps(self.preflight), "")

    def verify(self, **overrides):
        values = dict(run_commit=self.args.expected_run_commit, wav_hash=self.args.expected_wav_sha256,
                      pcm_hash=self.args.expected_pcm_sha256)
        values.update(overrides)
        return binder.verify_run(self.repo, self.project, self.projection, **values)

    def reject_bind(self, pattern=None, preserve_out=False):
        with self.assertRaisesRegex((ValueError, OSError, subprocess.SubprocessError), pattern or ".*"):
            binder.bind(self.args)
        self.assertFalse((self.out / "binding-result.json").exists())
        if not preserve_out:
            self.assertFalse(os.path.lexists(self.out))

    def test_valid_fixture_checks_real_graph_and_explicit_identities(self):
        proof, relevant, loudness, pcm, version = self.verify()
        self.assertEqual((proof, relevant, loudness, pcm, version),
                         (self.proof, self.relevant, self.loudness, self.pcm, "7.1.5"))
        self.decode.assert_called_once_with(self.wav)
        self.assertEqual(self.commands, [])

    def test_malformed_explicit_hashes_and_run_commit_fail_before_decode(self):
        for field, values in (("wav_hash", ("", "a" * 63, "A" * 64, "g" * 64)),
                              ("pcm_hash", ("bad", "0" * 65)),
                              ("run_commit", ("HEAD", "b" * 39, "B" * 40))):
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.verify(**{field: value})
        self.decode.assert_not_called()

    def test_selected_hashes_and_run_commit_must_match(self):
        for field in ("wav_hash", "pcm_hash", "run_commit"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify(**{field: "0" * (40 if field == "run_commit" else 64)})

    def test_proof_identity_and_audio_only_fields_fail_closed(self):
        cases = (("format", "other"), ("status", "PURE_GRAPH_EQUIVALENCE_BEFORE_NATIVE_EXECUTION"),
                 ("full_project_pass", True), ("browser_or_gfx_called", True),
                 ("user_full_render_postmix_check_required", False), ("core_commit", "0" * 40),
                 ("projection_runner_sha256", "0" * 64), ("full_timeline_sha256", "0" * 64),
                 ("projection_timeline_sha256", "0" * 64), ("audio_inputs_signature", "0" * 64),
                 ("audio_relevant_inputs", {}), ("source_sha256", {}), ("active_plugins", ["fixture"]),
                 ("graph_identical", False), ("target_lufs", -16), ("graph_sha256", "0" * 64),
                 ("receipt_sha256", "0" * 64), ("core_source_hashes", {}))
        for key, value in cases:
            original = self.proof[key]
            self.proof[key] = value
            self.save_proof()
            with self.subTest(field=key), self.assertRaises(ValueError):
                self.verify()
            self.proof[key] = original
        self.save_proof()
        self.decode.assert_not_called()

    def test_receipt_identity_stage_and_loudness_fail_even_with_rehashed_proof(self):
        original = copy.deepcopy(self.receipt)
        mutations = [lambda r: r.update(timeline="0" * 64),
                     lambda r: r.update(input_fingerprint="0" * 64)]
        mutations += [lambda r, stage=stage: r["stages"][stage].update(ok=False)
                      for stage in ("validate", "prepare", "audio")]
        mutations += [lambda r, key=key, value=value: r["stages"]["audio"]["post_loudness"].update({key: value})
                      for key, value in (("sha256", "0" * 64), ("target", -16), ("input_i", True),
                                         ("output_i", float("nan")), ("gain_db", float("inf")),
                                         ("tp_dbfs", "-3.9"))]
        for index, mutate in enumerate(mutations):
            self.receipt = copy.deepcopy(original)
            mutate(self.receipt)
            self.save_receipt()
            with self.subTest(case=index), self.assertRaises(ValueError):
                self.verify()
        self.decode.assert_not_called()

    def test_missing_failed_or_reordered_native_commands_rejected(self):
        original = copy.deepcopy(self.proof["commands"])
        bad_exit = copy.deepcopy(original)
        bad_exit[1]["exit_code"] = 1
        wrong_target = copy.deepcopy(original)
        wrong_target[0]["argv"][-2] = str(self.base / "another-timeline.json")
        wrong_module = copy.deepcopy(original)
        wrong_module[0]["argv"][2] = "unrelated-module"
        extra_argument = copy.deepcopy(original)
        extra_argument[0]["argv"].insert(-1, "--unreviewed")
        for commands in (original[:2], original + original[:1], list(reversed(original)),
                         bad_exit, wrong_target, wrong_module, extra_argument):
            self.proof["commands"] = commands
            self.save_proof()
            with self.subTest(commands=commands), self.assertRaises(ValueError):
                self.verify()

    def test_rehashed_forged_graph_and_changed_projection_rejected(self):
        self.proof["graph"][0].append("forged-argument")
        self.proof["graph_sha256"] = sha(json.dumps(self.proof["graph"], sort_keys=True).encode())
        self.save_proof()
        with self.assertRaisesRegex(ValueError, "Native graph differs"):
            self.verify()
        projected = json.loads((self.projection / "timeline.json").read_text())
        projected["audio_tracks"][0]["events"][0]["volume"] = 0.5
        identity = write_json(self.projection / "timeline.json", projected)
        self.proof["projection_timeline_sha256"] = self.receipt["timeline"] = identity
        self.save_receipt()
        with self.assertRaisesRegex(ValueError, "audio-relevant"):
            self.verify()

    def test_native_core_must_match_both_checkout_and_run_commit(self):
        path = self.repo / binder.CORE_FILES[0]
        path.write_bytes(path.read_bytes() + b"\n# fixture change\n")
        with self.assertRaisesRegex(ValueError, "Native core source differs"):
            self.verify()
        self.proof["core_source_hashes"][binder.CORE_FILES[0]] = sha(path.read_bytes())
        self.save_proof()
        with self.assertRaisesRegex(ValueError, "Native core source differs"):
            self.verify()

    def test_stem_bytes_and_manifest_paths_rejected(self):
        stem = self.stems["stems"][0]
        path = self.projection / stem["path"]
        path.write_bytes(b"x" * stem["bytes"])
        with self.assertRaisesRegex(ValueError, "Native projection stem differs"):
            self.verify()
        path.write_bytes(self.payloads[stem["path"]])
        stem["path"] = "../escape.flac"
        write_json(self.project / "audio/stems.json", self.stems)
        with self.assertRaisesRegex(ValueError, "Unsafe native stem path"):
            self.verify()

    def test_toolchain_mismatch_missing_tool_and_bad_version_rejected(self):
        for tool in ("ffmpeg", "ffprobe"):
            original = self.proof["toolchain"][tool]["sha256"]
            self.proof["toolchain"][tool]["sha256"] = "0" * 64
            self.save_proof()
            with self.subTest(tool=tool), self.assertRaisesRegex(ValueError, "same qualified"):
                self.verify()
            self.proof["toolchain"][tool]["sha256"] = original
        self.proof["toolchain"]["ffmpeg"]["version"] = "ffmpeg version 6.0.0-fixture"
        self.save_proof()
        with self.assertRaisesRegex(ValueError, "tool version mismatch"):
            self.verify()
        self.proof["toolchain"]["ffmpeg"]["version"] = "ffmpeg unknown"
        self.tool_versions["ffmpeg"] = "ffmpeg unknown"
        self.save_proof()
        with self.assertRaisesRegex(ValueError, "Unrecognized"):
            self.verify()
        self.proof["toolchain"]["ffmpeg"]["version"] = self.tool_versions["ffmpeg"] = "ffmpeg version 7.1.5-fixture"
        self.save_proof()
        self.tools.pop("ffprobe")
        with self.assertRaisesRegex(ValueError, "ffprobe is required"):
            self.verify()

    def test_nondefault_backend_namespace_or_plugins_rejected(self):
        for module, name, value in ((gfx_backend, "selected", "electron"),
                                    (hygiene, "gfx_cache_ns", "fixture"),
                                    (hooks, "active_plugins", [{"name": "fixture-plugin", "version": "0", "module": None}]),
                                    (hooks, "active_plugins_digest", "fixture-digest")):
            with self.subTest(name=name), mock.patch.object(module, name, return_value=value), self.assertRaises(ValueError):
                self.verify()

    def test_native_wav_change_during_decode_rejected(self):
        def decode(_):
            self.wav.write_bytes(b"changed during decode")
            return self.pcm, self.pcm_hash
        self.decode.side_effect = decode
        with self.assertRaisesRegex(ValueError, "changed during verification"):
            self.verify()

    def test_symlinked_proof_receipt_stem_wav_and_timeline_rejected(self):
        for relative in ("projection-proof.json", "out/receipt.json", "audio/T17.flac",
                         "out/audio.wav", "timeline.json"):
            path = self.projection / relative
            real = path.with_name(path.name + ".real")
            path.rename(real)
            path.symlink_to(real)
            with self.subTest(path=relative), self.assertRaises((OSError, ValueError)):
                self.verify()
            path.unlink()
            real.rename(path)
        self.decode.assert_not_called()

    def test_malformed_source_commit_dirty_checkout_and_detached_source_rejected(self):
        with self.assertRaisesRegex(ValueError, "full source commit"):
            binder.verified_source(self.repo, self.project, "HEAD")
        with self.assertRaisesRegex(ValueError, "explicit source commit"):
            binder.verified_source(self.repo, self.project, "0" * 40)
        path = self.project / "timeline.json"
        path.write_bytes(path.read_bytes() + b"\n")
        self.reject_bind("not clean")
        self.git("checkout", "--", binder.PREFIX + "timeline.json")
        copied = self.base / "detached-project"
        shutil.copytree(self.project, copied)
        (copied / "timeline.json").write_bytes(b"{}")
        with self.assertRaisesRegex(ValueError, "differs from commit"):
            binder.verified_source(self.repo, copied, self.commit)

    def test_source_file_symlink_and_executable_mode_drift_rejected(self):
        copied = self.base / "detached-project"
        shutil.copytree(self.project, copied)
        path = copied / "timeline.json"
        path.unlink()
        path.symlink_to(self.project / "timeline.json")
        with self.assertRaises((ValueError, OSError)):
            binder.verified_source(self.repo, copied, self.commit)
        path.unlink()
        shutil.copy2(self.project / "timeline.json", path)
        path.chmod(path.stat().st_mode ^ 0o111)
        with self.assertRaisesRegex(ValueError, "source mode differs"):
            binder.verified_source(self.repo, copied, self.commit)

    def test_existing_and_symlink_outputs_never_overwritten(self):
        self.out.mkdir()
        (self.out / "keep").write_text("original")
        for symlink in (False, True):
            if symlink:
                self.out.rename(self.base / "existing")
                self.out.symlink_to(self.base / "existing", target_is_directory=True)
            with self.subTest(symlink=symlink), self.assertRaisesRegex(ValueError, "existing outputs"):
                binder.bind(self.args)
            self.assertEqual((self.out / "keep").read_text(), "original")
        self.decode.assert_not_called()

    def test_dangling_output_symlink_and_symlinked_ancestors_rejected(self):
        self.out.symlink_to(self.base / "absent")
        with self.assertRaisesRegex(ValueError, "existing outputs"):
            binder.bind(self.args)
        self.assertTrue(self.out.is_symlink())
        self.out.unlink()
        alias = self.base / "alias"
        alias.symlink_to(self.base, target_is_directory=True)
        for field, value in (("out", alias / "new"), ("repo", alias / "repo"),
                             ("project", alias / "repo" / binder.PREFIX),
                             ("projection", alias / "native-run")):
            original = getattr(self.args, field)
            setattr(self.args, field, value)
            with self.subTest(field=field), self.assertRaises((ValueError, OSError)):
                binder.bind(self.args)
            setattr(self.args, field, original)
        self.assertFalse((self.base / "new").exists())
        self.decode.assert_not_called()

    def test_nested_outputs_rejected_before_any_verification_or_writes(self):
        for parent in (self.repo, self.project, self.projection):
            self.args.out = parent / "new-output"
            with self.subTest(parent=parent.name), self.assertRaisesRegex(ValueError, "outside"):
                binder.bind(self.args)
            self.assertFalse(self.args.out.exists())
        self.decode.assert_not_called()

    def test_loader_overrides_rejected_before_any_writes(self):
        with mock.patch.dict(os.environ, {"LD_PRELOAD": "fixture"}):
            self.reject_bind("dynamic-loader")
        self.decode.assert_not_called()

    def test_success_is_fresh_acyclic_portable_unapproved_and_preserves_inputs(self):
        before_source, before_run = snapshot(self.project), snapshot(self.projection)
        report = binder.bind(self.args)
        target = self.out / "project"
        self.assertEqual(snapshot(self.project), before_source)
        self.assertEqual(snapshot(self.projection), before_run)
        self.assertEqual(report, json.loads((self.out / "binding-result.json").read_text()))
        self.assertEqual(report["status"], "BOUND_DRAFT_REQUIRES_REVIEW")
        for key in ("production_ready", "audio_approved", "execution_attestation"):
            self.assertIs(report[key], False)
        self.assertIs(report["full_render_postmix_check_required"], True)
        self.assertEqual(report["aac_diagnostic"], "NOT_RUN_FOR_THIS_BINDING")
        self.assertEqual(report["selected_wav_sha256"], self.wav_hash)
        self.assertEqual(report["selected_pcm_sha256"], self.pcm_hash)
        self.assertEqual(report["native_run_commit"], self.commit)
        portable_bytes = (target / "audio/native-audio-provenance.json").read_bytes()
        portable = json.loads(portable_bytes)
        binding = json.loads((target / "audio/native-postmix.json").read_text())
        waveform_bytes = (target / "audio/waveform-S5.json").read_bytes()
        waveform = json.loads(waveform_bytes)
        self.assertNotIn("mix_recipe_sha256", portable["postmix_pcm_contract"])
        self.assertEqual(binding["portable_provenance_sha256"], sha(portable_bytes))
        self.assertEqual(binding["postmix"]["mix_recipe_sha256"], sha(portable_bytes))
        self.assertEqual(binding["waveform_sha256"], sha(waveform_bytes))
        self.assertEqual(waveform["provenance"], binding["postmix"])
        self.assertTrue(all((s["min"], s["max"], s["value"]) == (0, 0, 0) for s in waveform["samples"]))
        self.assertNotIn("aac_192k_diagnostic", binding)
        self.assertIs(binding["user_audio_approved"], False)
        self.assertIs(portable["full_execution_proof_included"], False)
        self.assertIs(portable["full_project_pass"], False)
        for relative, expected in report["derived_sha256"].items():
            self.assertEqual(sha((target / relative).read_bytes()), expected)
        for path in self.out.rglob("*.json"):
            self.assertNotIn(self.private, path.read_text())
            self.assertNotIn(str(self.base), path.read_text())
        for relative in ("projection-proof.json", "out/receipt.json", "out/audio.wav"):
            self.assertFalse((target / relative).exists())
        self.assertEqual(self.asset_decode.call_count, 5)
        self.assertEqual(len(self.commands), 2)
        self.assertLessEqual(set(report["changed_payload_paths"]), binder.DERIVED)
        for relative, (data, mode) in before_source.items():
            if relative not in binder.DERIVED:
                self.assertEqual((target / relative).read_bytes(), data)
                self.assertEqual((target / relative).stat().st_mode & 0o777, mode)

    def test_unrelated_build_delta_rejected_and_partial_output_removed(self):
        before = snapshot(self.project)
        self.build_hook = lambda target: (target / "timeline.json").write_text("{}")
        self.reject_bind("unrelated project files")
        self.assertEqual(snapshot(self.project), before)

    def test_new_unrelated_build_file_rejected_and_preserved(self):
        self.build_hook = lambda target: (target / "unexpected-build-output.txt").write_text("fixture")
        self.reject_bind("complete project file inventory", preserve_out=True)
        self.assertEqual((self.out / "project/unexpected-build-output.txt").read_text(), "fixture")

    def test_build_deletion_symlink_fifo_and_mode_changes_rejected(self):
        mutations = (
            (lambda target: (target / "timeline.json").unlink(), "No such file|Owned output file changed|file inventory", False),
            (lambda target: (target / "unexpected-link").symlink_to(self.project), "file inventory", True),
            (lambda target: os.mkfifo(target / "unexpected-fifo"), "file inventory", True),
            (lambda target: (target / "timeline.json").chmod(0o755), "source file modes", False),
        )
        for mutate, message, preserve in mutations:
            self.build_hook = mutate
            with self.subTest(change=message):
                self.reject_bind(message, preserve_out=preserve)
            if self.out.exists():
                shutil.rmtree(self.out)  # Reset only this test's temporary fixture.

    def test_exclusive_creation_preserves_output_created_after_initial_check(self):
        original = binder.binding_documents
        def competing_output(*args):
            documents = original(*args)
            self.out.mkdir()
            (self.out / "keep").write_text("competing output")
            return documents
        with mock.patch.object(binder, "binding_documents", side_effect=competing_output):
            with self.assertRaises(FileExistsError):
                binder.bind(self.args)
        self.assertEqual((self.out / "keep").read_text(), "competing output")
        self.assertFalse((self.out / "project").exists())
        self.assertEqual(self.commands, [])

    def test_failure_cleanup_never_deletes_replacement_directory(self):
        moved = self.base / "moved-owned-output"
        def replace_and_fail(_):
            self.out.rename(moved)
            self.out.mkdir()
            (self.out / "keep").write_text("replacement output")
            raise OSError("fixture competing replacement")
        self.build_hook = replace_and_fail
        with self.assertRaisesRegex(OSError, "fixture competing replacement"):
            binder.bind(self.args)
        self.assertEqual((self.out / "keep").read_text(), "replacement output")
        self.assertFalse((self.out / "binding-result.json").exists())
        self.assertFalse((moved / "binding-result.json").exists())

    def test_owned_writer_preserves_existing_file_and_rejects_replaced_folder(self):
        before_source = snapshot(self.project)
        moved = self.base / "original-owned-folder"
        with binder.OwnedOutput(self.out) as owned:
            owned.write("project/keep.txt", b"owned bytes")
            with self.assertRaises(FileExistsError):
                owned.write("project/keep.txt", b"must not overwrite")
            self.assertEqual((self.out / "project/keep.txt").read_bytes(), b"owned bytes")
            (self.out / "project").rename(moved)
            (self.out / "project").mkdir()
            (self.out / "project/keep.txt").write_bytes(b"existing replacement bytes")
            with self.assertRaisesRegex(ValueError, "directory changed"):
                owned.write("project/later.txt", b"must not be written")
            self.assertEqual((self.out / "project/keep.txt").read_bytes(), b"existing replacement bytes")
            self.assertFalse((self.out / "project/later.txt").exists())
            self.assertEqual((moved / "keep.txt").read_bytes(), b"owned bytes")
        self.assertEqual(snapshot(self.project), before_source)
        self.assertFalse((self.out / "binding-result.json").exists())

    def test_canonical_assets_are_validated_before_output_creation(self):
        def validate_before_output(*_):
            self.assertFalse(os.path.lexists(self.out))
        self.asset_decode.side_effect = validate_before_output
        def inspect_initial_copy(target):
            binding = json.loads((target / "audio/native-postmix.json").read_text())
            self.assertEqual(binding["postmix"]["sha256"], self.wav_hash)
            self.assertEqual(binding["postmix"]["pcm_sha256"], self.pcm_hash)
            self.assertNotIn("aac_192k_diagnostic", binding)
        self.build_hook = inspect_initial_copy
        binder.bind(self.args)
        self.assertEqual(self.asset_decode.call_count, 5)

    def test_replaced_output_is_rejected_before_report_writes(self):
        before_source = snapshot(self.project)
        moved = self.base / "moved-owned-output"
        expected = {"keep.txt": (b"existing replacement bytes", 0o644)}
        def replace_output(_):
            self.out.rename(moved)
            self.out.mkdir()
            path = self.out / "keep.txt"
            path.write_bytes(expected["keep.txt"][0])
            path.chmod(expected["keep.txt"][1])
        self.build_hook = replace_output
        with self.assertRaises((ValueError, OSError)):
            binder.bind(self.args)
        self.assertEqual(snapshot(self.out), expected)
        self.assertEqual(snapshot(self.project), before_source)
        self.assertEqual(len(self.commands), 1)
        self.assertFalse((moved / "binding-result.json").exists())

    def test_replaced_project_is_rejected_before_next_external_stage(self):
        before_source = snapshot(self.project)
        moved = self.base / "moved-owned-project"
        def replace_project(target):
            target.rename(moved)
            target.mkdir()
            (target / "keep.txt").write_text("existing replacement bytes")
        self.build_hook = replace_project
        with self.assertRaises((ValueError, OSError)):
            binder.bind(self.args)
        self.assertEqual(snapshot(self.project), before_source)
        self.assertEqual(len(self.commands), 1)
        self.assertFalse((self.out / "binding-result.json").exists())
        self.assertEqual((self.out / "project/keep.txt").read_text(), "existing replacement bytes")
        self.assertEqual(set(snapshot(self.out / "project")), {"keep.txt"})
        self.assertFalse((moved / "build.json").exists())
        self.assertFalse((moved / "binding-result.json").exists())

    def test_selected_wav_is_rechecked_after_paired_preflight(self):
        before_source = snapshot(self.project)
        original = self.wav.read_bytes()
        self.preflight_hook = lambda _: self.wav.write_bytes(b"x" * len(original))
        self.reject_bind("WAV|postmix")
        self.assertEqual(len(self.commands), 2)
        self.assertEqual(snapshot(self.project), before_source)
        self.assertEqual(self.wav.read_bytes(), b"x" * len(original))

    def test_failed_external_stage_removes_partial_output_and_preserves_inputs(self):
        before_source, before_run = snapshot(self.project), snapshot(self.projection)
        def fail(_):
            raise subprocess.CalledProcessError(1, ["node", "fixture-build"])
        self.build_hook = fail
        self.reject_bind()
        self.assertEqual(snapshot(self.project), before_source)
        self.assertEqual(snapshot(self.projection), before_run)

    def test_failed_asset_decode_creates_no_output(self):
        self.asset_decode.side_effect = ValueError("fixture decode failed")
        self.reject_bind("fixture decode failed")
        self.assertTrue(self.project.exists())
        self.assertTrue(self.projection.exists())
        self.assertEqual(self.commands, [])

    def test_paired_preflight_errors_or_acceptance_claim_rejected(self):
        self.preflight["source_errors"] = ["fixture source failure"]
        self.reject_bind("Paired project/PCM")
        self.preflight["source_errors"] = []
        self.preflight["production_ready"] = True
        self.reject_bind("Paired project/PCM")
        self.preflight["production_ready"] = False
        for key in self.preflight["waveform"]:
            self.preflight["waveform"][key] = False
            self.reject_bind("Paired project/PCM")
            self.preflight["waveform"][key] = True

    def test_source_changes_during_build_rejected_and_output_removed(self):
        self.build_hook = lambda _: (self.project / "timeline.json").write_text("{}")
        self.reject_bind("not clean")
        self.assertEqual((self.project / "timeline.json").read_text(), "{}")

    def test_proof_or_receipt_changes_during_build_rejected_and_output_removed(self):
        for relative in ("projection-proof.json", "out/receipt.json"):
            path = self.projection / relative
            original = path.read_bytes()
            self.build_hook = lambda _, path=path: path.write_bytes(path.read_bytes() + b"\n")
            with self.subTest(path=relative):
                self.reject_bind("proof or receipt changed")
            path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
