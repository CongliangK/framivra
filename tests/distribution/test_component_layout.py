"""Canonical component/template layout, entrypoints and isolated skill mounts.

Structural checks and real help/import checks share one ownership boundary.
Doctor health/dependency subprocesses and HTTP remain mocked; no ASR/IME probes.
"""
import argparse
import ast
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(REPO))

import vt


TARGETS = {
 'bed_static_duck.py': 'plugins/duck-static/bed_static_duck.py',
 'bilibili.py': 'apps/research/bilibili.py',
 'check_4k.sh': 'apps/publish/check_4k.sh',
 'compare_4k_quality.py': 'apps/publish/compare_4k_quality.py',
 'content_gates.py': 'apps/gates/content_gates.py',
 'denoise_init.sh': 'plugins/denoise/denoise_init.sh',
 'denoise_voice.py': 'plugins/denoise/denoise_voice.py',
 'dump-computed-styles.mjs': 'apps/research/dump-computed-styles.mjs',
 'export_4k.py': 'apps/publish/export_4k.py',
 'fetch-site-assets.py': 'apps/research/fetch-site-assets.py',
 'github_repos.py': 'apps/research/github_repos.py',
 'make_cover.py': 'apps/cover/make_cover.py',
 'repack_voice.py': 'apps/sync/repack_voice.py',
 'score_topic.py': 'apps/research/score_topic.py',
 'scroll-shoot.mjs': 'apps/research/scroll-shoot.mjs',
 'sync_audio.py': 'apps/sync/sync_audio.py',
}


class ComponentEntrypoints(unittest.TestCase):
    def test_canonical_help_is_cwd_independent(self):
        bash = shutil.which('bash')
        if os.name == 'nt':
            git = shutil.which('git')
            candidate = Path(git).resolve().parent.parent / 'bin/bash.exe' if git else None
            if candidate and candidate.is_file(): bash = str(candidate)
        for name, destination in TARGETS.items():
            with self.subTest(entry=name):
                target = REPO / destination
                self.assertTrue(target.is_file(), destination)
                prefix = [sys.executable] if target.suffix == '.py' else [bash if target.suffix == '.sh' else shutil.which('node')]
                self.assertTrue(prefix[0], 'required script interpreter missing')
                env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
                owner = subprocess.run(prefix + [target, '--help'], cwd=REPO, env=env, capture_output=True, timeout=30)
                # These two positional-only CLIs treat --help as a missing input;
                # retain their actual error contracts instead of accepting any failure.
                expected_errors = {'check_4k.sh': (2, b'FATAL: --help'),
                                   'score_topic.py': (1, b'--help/supplement.json')}
                expected_code, marker = expected_errors.get(name, (0, None))
                self.assertEqual(owner.returncode, expected_code, owner.stderr.decode('utf-8', 'replace'))
                self.assertNotIn(b'Traceback (most recent call last)', owner.stdout + owner.stderr)
                if marker:
                    self.assertIn(marker, owner.stdout + owner.stderr)

                with tempfile.TemporaryDirectory() as cwd:
                    independent = subprocess.run(prefix + [target, '--help'], cwd=cwd, env=env, capture_output=True, timeout=30)
                self.assertEqual(independent.returncode, owner.returncode, independent.stderr.decode('utf-8','replace'))
                self.assertEqual(independent.stdout, owner.stdout)
                self.assertEqual(independent.stderr, owner.stderr)

    def test_canonical_import_owns_fingerprint_implementation(self):
        module = importlib.import_module('apps.gates.content_gates')
        owner_path = REPO / 'apps/gates/content_gates.py'
        owner = runpy.run_path(str(owner_path), run_name='owner_fixture')
        self.assertEqual(Path(module.__file__).resolve(), owner_path.resolve())
        self.assertEqual(Path(module.fingerprint.__code__.co_filename).resolve(), owner_path.resolve())
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'input'; source.write_bytes(b'fingerprint input')
            self.assertEqual(module.fingerprint(source), owner['fingerprint'](source))


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
            # Keep this case ID while enforcing the retired-alias contract.
            self.assertFalse(os.path.lexists(REPO / "scripts" / name))
            entry = REPO / target
            self.assertTrue(entry.is_file())
            self.assertFalse(entry.is_symlink())
            source = entry.read_text(encoding='utf-8')
            self.assertTrue(source.strip())
            compile(source, str(entry), 'exec')


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


class TemplateLayout(unittest.TestCase):
    def test_retired_roots_absent_and_template_peers_present(self):
        for name in ("skill", "skills", "examples"):
            self.assertFalse(os.path.lexists(REPO / name), name)
        for name in ("tech-talk", "skill-demo", "hello-app", "hello-plugin"):
            self.assertTrue((REPO / "templates" / name).is_dir(), name)
        self.assertTrue((REPO / "templates/README.md").is_file())

    def test_tool_skill_routes_and_deeper_dev_links(self):
        skill = REPO / "docs/skill"
        skill_text = (skill / "SKILL.md").read_text()
        self.assertIn("name: video\n", skill_text)
        for name in ("edit", "gfx"):
            self.assertTrue((skill / "references" / (name + ".md")).is_file())
        self.assertFalse(os.path.lexists(skill / "references/runbook.md"))
        self.assertIn("(references/edit.md)", skill_text)
        self.assertIn("(references/gfx.md#html-创作组织)", skill_text)
        self.assertNotIn("references/runbook.md", skill_text)
        for name in ("edit", "gfx"):
            path = skill / "references" / (name + ".md")
            self.assertIn("../../DEV.md", path.read_text())
            self.assertEqual((path.parent / "../../DEV.md").resolve(), REPO / "docs/DEV.md")
        for name in ("title-card", "lower-third"):
            self.assertTrue((skill / "assets" / (name + ".html")).is_file())

    def test_tool_skill_documents_canonical_mounts(self):
        text = (REPO / "docs/skill/SKILL.md").read_text()
        self.assertIn("~/.agents/skills/video/", text)
        self.assertIn("`SKILL.md`、`references`、`assets`", text)
        self.assertIn("`vtx`、`scripts`、`templates`", text)
        self.assertNotIn("历史路径继续可用", text)
        self.assertNotIn("vtx/references/assets/scripts/skills", text)

    def test_workflow_manifests_and_tech_talk_internal_templates(self):
        for name in ("tech-talk", "skill-demo"):
            root = REPO / "templates" / name
            manifest = json.loads((root / "skill.json").read_text())
            self.assertEqual(manifest["kind"], "skill")
            self.assertEqual(manifest["name"], name)
            self.assertTrue((root / manifest["docs"]).is_file())
            if "assets" in manifest:
                self.assertTrue((root / manifest["assets"]).is_dir())
        root = REPO / "templates/tech-talk"
        for name in ("00-structure", "02-script", "03-assets-plan", "06-teleprompter"):
            self.assertTrue((root / "templates" / (name + ".md")).is_file())
            self.assertFalse((REPO / "templates" / (name + ".md")).exists())
        for name in ("placeholder_av.mp4", "placeholder_audio.wav", "placeholder_gfx.html"):
            self.assertTrue((root / "assets/presets/placeholders" / name).is_file())

    def test_canary_and_experiment_fixture_paths_are_canonical(self):
        page = REPO / "templates/skill-demo/gfxA.html"
        self.assertTrue(page.is_file())
        for rel in ("tests/gfx/test_frame_canary.py",
                    "experiments/gfx_electron_worker/drill_sigkill.py",
                    "experiments/gfx_electron_worker/run_gfx_electron_worker.py"):
            tree = ast.parse((REPO / rel).read_text())
            strings = {node.value for node in ast.walk(tree)
                       if isinstance(node, ast.Constant) and isinstance(node.value, str)}
            self.assertIn("templates", strings, rel)
            self.assertNotIn("examples", strings, rel)

    def test_electron_default_page_is_canonical(self):
        source = (REPO / "experiments/gfx_electron_worker/main.mjs").read_text()
        self.assertIn("path.join(REPO_ROOT, 'templates', 'skill-demo', 'gfxA.html')", source)
        self.assertNotIn("'examples'", source)

    def test_test_runner_collects_both_developer_templates(self):
        runner = (REPO / "scripts/run_all_tests.sh").read_text()
        self.assertIn("apps/*/tests plugins/*/tests templates/*/tests", runner)
        self.assertEqual({p.parent.name for p in (REPO / "templates").glob("*/tests")},
                         {"hello-app", "hello-plugin"})


class DocsSmokeTemplateCoverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shell = (REPO / "scripts/docs/docs_smoke.sh").read_text()
        cls.source = shell.split("<<'PYEOF'\n", 1)[1].rsplit("\nPYEOF", 1)[0]

    def test_collection_includes_all_template_and_tool_skill_documents_once(self):
        # Execute only the collection prefix: no script import/probe or smoke subprocess.
        namespace = {}
        env = {"DOCS_SMOKE_ROOT": str(REPO), "DOCS_SMOKE_VERBOSE": "", "DOCS_SMOKE_GOLDEN": ""}
        with mock.patch.dict(os.environ, env):
            exec(compile(self.source.split("FENCE = re.compile", 1)[0],
                         "docs_smoke_collection", "exec"), namespace)
        targets = namespace["targets"]
        self.assertEqual(len(targets), len(set(targets)))
        for root in (REPO / "templates", REPO / "docs/skill"):
            self.assertTrue(set(root.rglob("*.md")).issubset(targets))
        self.assertFalse(any(p.relative_to(REPO).parts[1].startswith("plans")
                             for p in targets if p.is_relative_to(REPO / "docs")))

    def test_inline_selfcheck_uses_current_surface_not_historical_cap(self):
        self.assertIn("if n_channel > n_cand:", self.source)
        self.assertNotIn("if n_channel > INLINE_CAND_ANCHOR:", self.source)
        self.assertIn("INLINE_CAND_ANCHOR = 69", self.source)

    def test_normalize_recognizes_canonical_template_scripts(self):
        nodes = []
        for node in ast.parse(self.source).body:
            if (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name)
                    and t.id in {"TOKEN", "CMD_WORDS"} for t in node.targets)):
                nodes.append(node)
            elif isinstance(node, ast.FunctionDef) and node.name in {"normalize", "resolve"}:
                nodes.append(node)
        namespace = {"ROOT": REPO, "re": re, "Path": Path}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "docs_smoke_paths", "exec"), namespace)
        for command, rel, method in (
            ("bash templates/tech-talk/scripts/new_project.sh", "templates/tech-talk/scripts/new_project.sh", "sh"),
            ("env python3 $VIDEO_TOOLS/templates/tech-talk/scripts/freeze.py --help", "templates/tech-talk/scripts/freeze.py", "py"),
            ("env python3 templates/hello-app/health_check.py", "templates/hello-app/health_check.py", "py"),
        ):
            self.assertEqual(namespace["normalize"](command, REPO), (REPO / rel, method))


if __name__ == "__main__":
    unittest.main()
