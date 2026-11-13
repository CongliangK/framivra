"""Built-in producer/receipt identity and source acceptance regressions."""
import contextlib
import io
import json
import os
import sys
from pathlib import Path
from unittest import mock

from tests.test_bgm_render_plugin import _Base, _bgm, _context
from apps.bgm import identity
from vtx.cache.cache_state import receipt_fingerprint, verify_input_identity


class ProducerIdentity(_Base):
    def state(self, c):
        return (_bgm.param_digest(c, "bgm_main"), _bgm.render_digest(c, "bgm_main"),
                receipt_fingerprint(c))

    def test_runtime_and_ref_change_all_identities_same_context(self):
        c = self._ctx(True)
        before = self.state(c)
        self.ref_file.write_text('{"notes": [3, 2, 1]}', encoding="utf-8")
        after = self.state(c)
        self.assertTrue(all(a != b for a, b in zip(before, after)))
        with self.assertRaises(Exception):
            verify_input_identity(c)
        runtime = Path(os.environ["MUSIC_RUNTIME"])
        runtime.write_text('{"secret": "never-publish-plaintext"}', encoding="utf-8")
        newer = self.state(c)
        self.assertTrue(all(a != b for a, b in zip(after, newer)))
        self.assertNotIn("never-publish-plaintext", json.dumps(_bgm.input_identity(c)))

    def test_master_reference_changes_all_identities_same_context(self):
        reference = self.root / "reference.wav"
        reference.write_bytes(b"first reference audio")
        project = json.loads((identity.APP_ROOT / "examples" / "synth.json").read_text())
        # Matchering resolves relative references against caller cwd, not the
        # project directory. Do not introduce a different project-path contract.
        project["master"] = {"chain": "matchering", "ref": os.path.relpath(reference)}
        self.proj.write_text(json.dumps(project), encoding="utf-8")
        c = self._ctx(True)
        before = self.state(c)
        self.assertEqual(identity.asset_dependencies(self.proj, {}), [str(reference)])
        reference.write_bytes(b"different reference audio content")
        after = self.state(c)
        self.assertTrue(all(a != b for a, b in zip(before, after)))
        with self.assertRaises(Exception):
            verify_input_identity(c)

    def test_child_python_and_numpy_change_all_identities_same_context(self):
        venv = self.root / "child-env"
        bindir = venv / ("Scripts" if os.name == "nt" else "bin")
        bindir.mkdir(parents=True)
        child = bindir / ("python.exe" if os.name == "nt" else "python")
        implementation = self.root / "child-implementation"
        implementation.write_bytes(b"original interpreter")
        implementation.chmod(0o755)
        if os.name == "nt":
            child.write_bytes(implementation.read_bytes())
            implementation = child
        else:
            child.symlink_to(implementation)
        dist = venv / "lib" / "python3.13" / "site-packages" / "numpy-1.dist-info"
        dist.mkdir(parents=True)
        record = dist / "RECORD"
        record.write_text("first numpy installation", encoding="utf-8")
        configs = [({"python_system": str(child)}, "qc"),
                   ({"matchering": {"python": str(child)}}, "matchering"),
                   ({"demucs": {"python": str(child)}}, "demucs"),
                   ({"python_torch": str(child)}, "matchering")]
        for config, name in configs:
            with self.subTest(config=config):
                Path(os.environ["MUSIC_RUNTIME"]).write_text(json.dumps(config), encoding="utf-8")
                implementation.write_bytes(b"original interpreter")
                c = self._ctx(True)
                before = self.state(c)
                producer = _bgm._producer_identity(c, "bgm_main")
                selected = producer["child_pythons"][name]
                self.assertEqual(selected["tool"]["selected"], str(child))
                self.assertEqual(selected["tool"]["path"], str(implementation.resolve()))
                self.assertTrue(selected["numpy_distribution"])
                implementation.write_bytes(b"replaced interpreter, different size")
                after = self.state(c)
                self.assertTrue(all(a != b for a, b in zip(before, after)))
                self.assertEqual(producer["tools"], _bgm._producer_identity(c, "bgm_main")["tools"])
                with self.assertRaises(Exception):
                    verify_input_identity(c)
                # Installed NumPy records must also be rebound in this same Ctx.
                record.write_text("updated numpy " + name, encoding="utf-8")
                newer = self.state(c)
                self.assertTrue(all(a != b for a, b in zip(after, newer)))

    def test_app_code_and_descriptor_change_all_identities(self):
        c = self._ctx(True)
        with mock.patch.object(identity, "code_identity", return_value="implementation-a"):
            before = self.state(c)
        with mock.patch.object(identity, "code_identity", return_value="implementation-b"):
            after = self.state(c)
        self.assertTrue(all(a != b for a, b in zip(before, after)))
        root = self.root / "app"
        (root / "bgm_gen" / "descriptors").mkdir(parents=True)
        (root / "workflows").mkdir()
        (root / "bgm.py").write_text("# entry", encoding="utf-8")
        descriptor = root / "bgm_gen" / "descriptors" / "synth.json"
        descriptor.write_text('{"version": 1}', encoding="utf-8")
        before = identity.code_identity(root)
        descriptor.write_text('{"version": 2}', encoding="utf-8")
        self.assertNotEqual(identity.code_identity(root), before)

    def test_other_project_only_invalidates_own_render_and_receipt(self):
        second = self.root / "music" / "other.json"
        second.write_text('{"tracks": []}', encoding="utf-8")
        c = _context(self.root, self._tl_srcs(
            {"id": "bgm_main", "path": "music/ep.json", "bgm": True},
            {"id": "other", "path": "music/other.json", "bgm": True}))
        before = self.state(c)
        other = _bgm.render_digest(c, "other")
        second.write_text('{"tracks": [], "name": "changed"}', encoding="utf-8")
        after = self.state(c)
        self.assertEqual(before[:2], after[:2])
        self.assertNotEqual(before[2], after[2])
        self.assertNotEqual(other, _bgm.render_digest(c, "other"))

    def test_actual_ffmpeg_not_overwritten_by_runtime_tool(self):
        env = dict(os.environ)
        Path(env["MUSIC_RUNTIME"]).write_text(
            json.dumps({"tools": {"ffmpeg": "/configured/ffmpeg"}}), encoding="utf-8")
        def tool(command, env):
            return {"command": command, "stamp": stamps.get(command, 1)}
        stamps = {"ffmpeg": 1}
        with mock.patch.object(identity, "_tool_identity", side_effect=tool):
            before, _ = identity.execution_identity([sys.executable], env)
            stamps["ffmpeg"] = 2
            after, _ = identity.execution_identity([sys.executable], env)
        self.assertNotEqual(before, after)
        self.assertEqual(before["configured_tools"], after["configured_tools"])

    def test_venvs_sharing_python_have_distinct_numpy_identity(self):
        if os.name == "nt":
            self.skipTest("POSIX venv executable symlink layout")
        commands = []
        for name, record in (("venv-a", "numpy-a"), ("venv-b", "numpy-b")):
            root = self.root / name
            (root / "bin").mkdir(parents=True)
            py = root / "bin" / "python"
            py.symlink_to(sys.executable)
            dist = root / "lib" / "python3.13" / "site-packages" / "numpy-1.dist-info"
            dist.mkdir(parents=True)
            (dist / "RECORD").write_text(record, encoding="utf-8")
            commands.append([str(py)])
        first, _ = identity.execution_identity(commands[0], dict(os.environ))
        second, _ = identity.execution_identity(commands[1], dict(os.environ))
        self.assertEqual(first["tools"]["python"]["path"], second["tools"]["python"]["path"])
        self.assertNotEqual(first["tools"]["python"]["selected"], second["tools"]["python"]["selected"])
        self.assertTrue(first["numpy_distribution"])
        self.assertNotEqual(first["numpy_distribution"], second["numpy_distribution"])

    def test_runtime_error_does_not_render_or_publish(self):
        Path(os.environ["MUSIC_RUNTIME"]).write_text("not JSON", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "bgm-render.*input_identity"):
            self._ctx(True)
        self.assertEqual(self._calls(), [])
        self.assertFalse((self.root / "cache").exists())

    def test_source_without_bgm_does_not_import_music_identity(self):
        with mock.patch.object(identity, "execution_identity", side_effect=AssertionError("music loaded")):
            self._ctx()

    def test_mutation_during_render_does_not_publish_complete(self):
        c = self._ctx(True)
        shared = _bgm.render_cache_dir(c, "bgm_main")
        original = _bgm._run_bgm
        def change_after_render(*args):
            result = original(*args)
            self.ref_file.write_text('{"changed": true}', encoding="utf-8")
            return result
        with mock.patch.object(_bgm, "_run_bgm", side_effect=change_after_render):
            with self.assertRaisesRegex(RuntimeError, "输入在渲染期间变化"):
                _bgm._ensure_shared_render(c, "bgm_main")
        self.assertFalse((shared / "_complete.json").exists())

    def test_master_qc_failure_is_rejected_but_stem_remains_mixing_input(self):
        c = self._ctx(True)
        shared, result = _bgm._ensure_shared_render(c, "bgm_main")
        result["qc_ok"] = False
        with mock.patch.object(_bgm, "_ensure_shared_render", return_value=(shared, result)), \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                _bgm.prepare_source(c, "bgm_main")
            stem = self._ctx({"output": "stem:strings"})
            self.assertTrue(_bgm.prepare_source(stem, "bgm_main").is_file())
