"""Canonical template layout and static consumers; no workflow/media execution."""
import ast
import json
import os
from pathlib import Path
import re
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[2]


class TemplateLayout(unittest.TestCase):
    def test_retired_roots_absent_and_template_peers_present(self):
        for name in ("skill", "skills", "examples"):
            self.assertFalse(os.path.lexists(REPO / name), name)
        for name in ("tech-talk", "skill-demo", "hello-app", "hello-plugin"):
            self.assertTrue((REPO / "templates" / name).is_dir(), name)
        self.assertTrue((REPO / "templates/README.md").is_file())

    def test_tool_skill_routes_and_deeper_dev_links(self):
        skill = REPO / "docs/skill"
        self.assertIn("name: video\n", (skill / "SKILL.md").read_text())
        for name in ("edit", "gfx", "runbook"):
            self.assertTrue((skill / "references" / (name + ".md")).is_file())
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
