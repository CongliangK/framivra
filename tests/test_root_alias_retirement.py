"""#98: root aliases absent; independent scripts and skill mounts remain usable.

Doctor health/dependency subprocesses and HTTP are mocked: never probe ASR/IME.
"""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import vt

REPO = Path(__file__).resolve().parents[1]
ALIASES = {"aroll": "aroll", "asr": "asr", "blender_bridge": "blender-bridge",
           "imgen": "imgen", "matting": "matting", "tts": "tts"}


class RootLayout(unittest.TestCase):
    def test_no_root_alias_even_as_file_or_materialized_directory(self):
        for name, app in ALIASES.items():
            with self.subTest(name=name):
                self.assertFalse(os.path.lexists(REPO / name), name)
                self.assertTrue((REPO / "apps" / app / "app.json").is_file())

    def test_independent_script_links_remain_readable(self):
        for name, target in (("bed_static_duck.py", "plugins/duck-static/bed_static_duck.py"),
                             ("content_gates.py", "apps/gates/content_gates.py")):
            entry = REPO / "scripts" / name
            self.assertTrue(entry.is_file())
            self.assertFalse(entry.is_symlink())
            self.assertIn("../" + target, entry.read_text(encoding='utf-8'))
            self.assertIn('runpy.run_path', entry.read_text(encoding='utf-8'))


class IsolatedSkillMounts(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="alias-retirement-")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.mount = self.home / "video"
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.dict(os.environ, {
            "HOME": str(self.home), "VT_MOUNT_ROOT": str(self.mount),
            "VTX_CONFIG": str(self.home / "config.json"), "VTX_DOCTOR_OFFLINE": "1"}))
        # A call is a test failure, not a real localhost/health attempt.
        stack.enter_context(mock.patch.object(vt.subprocess, "run", side_effect=AssertionError("subprocess forbidden")))
        stack.enter_context(mock.patch.object(vt.urllib.request, "urlopen", side_effect=AssertionError("HTTP forbidden")))
        stack.enter_context(mock.patch.object(vt, "_doctor_version", return_value={"state": "fixture"}))
        stack.enter_context(mock.patch.object(vt, "_doctor_dependencies", return_value={"errors": []}))
        stack.enter_context(mock.patch.object(vt, "_doctor_components", return_value={"fail": False}))
        # Skills discovery and mount/registry repair stay real (static filesystem only).

    def doctor(self, json_out=True):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = vt.cmd_doctor(argparse.Namespace(json=json_out, fix=True))
        self.assertEqual(rc, 0, output.getvalue())
        return json.loads(output.getvalue()) if json_out else output.getvalue()

    def test_dry_relative_links_skills_readable_and_idempotent(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(vt.cmd_fix_links(True), 0)
        self.assertFalse(self.mount.exists())
        self.assertEqual(len(vt._mount_link_state()), 6)
        data = self.doctor()
        self.assertEqual(data["fix"]["mount"]["status"], "fixed")
        for name, target in vt.MOUNT_LINKS:
            link = self.mount / name
            self.assertTrue(link.is_symlink())
            self.assertFalse(Path(os.readlink(link)).is_absolute())
            self.assertEqual(link.resolve(), (REPO / target).resolve())
        self.assertTrue((self.mount / "templates/tech-talk/SKILL.md").read_text())
        self.assertEqual(vt._mount_link_state(), [])
        self.assertEqual(self.doctor()["fix"]["mount"]["status"], "ok")
        self.assertIn("无可自动修复项", self.doctor(False))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(vt.cmd_fix_links(False), 0)
        self.assertIn("0 处变更", output.getvalue())

    def test_real_file_is_never_overwritten(self):
        self.mount.mkdir()
        occupied = self.mount / "SKILL.md"
        occupied.write_text("user-owned\n")
        report = self.doctor()["fix"]
        self.assertTrue(any("真实文件占用" in note for note in report["manual"]))
        with contextlib.redirect_stdout(io.StringIO()):
            vt.cmd_fix_links(False)
        self.assertEqual(occupied.read_text(), "user-owned\n")

    def test_foreign_live_mount_is_manual_and_unchanged(self):
        self.mount.mkdir()
        foreign = self.home / "other-checkout"
        foreign.mkdir()
        (self.mount / "vtx").symlink_to(foreign)
        with mock.patch.object(vt, "mount_root", return_value=self.mount), \
                mock.patch.dict(os.environ, {"VT_MOUNT_ROOT": ""}):
            report = self.doctor()["fix"]
        self.assertEqual(report["mount"]["status"], "manual")
        self.assertEqual((self.mount / "vtx").resolve(), foreign)
        self.assertFalse(os.path.lexists(self.mount / "SKILL.md"))


if __name__ == "__main__":
    unittest.main()
