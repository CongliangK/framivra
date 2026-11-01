"""C1 business slice: real bash, copied source, publisher, media probe and validator.

No render, installed-skill mutation or validator mock. Missing dependencies fail
rather than being reported as a successful business run. Temporary copies obey
TMPDIR; neither HOME nor XDG is redirected.
"""
import contextlib
import json
import os
from pathlib import Path
import select
import shutil
import stat
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]


class TechTalkCreator(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tech-talk-c1-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.repo = self.home / "source copy"
        self.repo.mkdir()
        for name in ("vtx", "plugins", "templates/tech-talk"):
            shutil.copytree(REPO / name, self.repo / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.skill = self.repo / "templates/tech-talk"
        self.skeleton = self.skill / "assets/presets/timeline.skeleton.json"
        self.raw = self.skeleton.read_bytes()
        self.document = json.loads(self.raw)
        self.entry = self.skill / "scripts/new_project.sh"
        self.cwd = self.home / "caller cwd"
        self.cwd.mkdir()
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        self.env.pop("PYTHONPATH", None)
        self.env.pop("PYTHONHOME", None)
        # Ignore machine-specific external plugins, without changing HOME/XDG.
        self.env["VTX_CONFIG"] = str(self.home / "absent-config.json")

    def run_creator(self, directory="project", *args, env=None):
        return subprocess.run(["bash", str(self.entry), directory, *args],
                              cwd=self.cwd, env=env or self.env, text=True,
                              capture_output=True, timeout=40)

    def success(self, run):
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("✓ tech-talk 项目脚手架完成", run.stdout)
        self.assertIn("── vtx validate", run.stdout)

    def failed(self, run):
        self.assertNotEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertNotIn("✓ tech-talk 项目脚手架完成", run.stdout)

    def api(self, target, body, *args):
        run = subprocess.run(["python3", "-B", "-c",
                              "import json,sys; from pathlib import Path; "
                              "from vtx.api import read_timeline_for_write, timeline_store_status, timeline_transaction; "
                              + body, str(target), *map(str, args)],
                             cwd=self.repo, env=self.env, capture_output=True,
                             text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        return json.loads(run.stdout)

    def status(self, target):
        result = self.api(target, "print(json.dumps(timeline_store_status(sys.argv[1])))")
        self.assertTrue(result["ok"], result)
        return result

    def cli_request(self, target, request):
        run = subprocess.run(["python3", "-B", str(self.repo / "vtx/vtx.py"),
                              "-t", str(target), "store", "--request-stdin"],
                             input=json.dumps(request), cwd=self.cwd, env=self.env,
                             text=True, capture_output=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        return json.loads(run.stdout)

    @staticmethod
    def fingerprint(paths):
        result = {}
        for p in sorted(paths):
            s = p.lstat()
            result[str(p)] = (s.st_ino, s.st_mode, s.st_nlink, s.st_size,
                              s.st_mtime_ns, p.read_bytes() if p.is_file() else None)
        return result

    @contextlib.contextmanager
    def slow_copy(self, directory="project"):
        """Real cp, paused at the exact first placeholder copy using OS FIFOs."""
        barrier = self.home / ("barrier-" + directory)
        barrier.mkdir(parents=True)
        for name in ("ready", "release"):
            os.mkfifo(barrier / name)
        adapter = barrier / "bin"
        adapter.mkdir()
        real_cp = shutil.which("cp")
        self.assertIsNotNone(real_cp)
        (adapter / "cp").write_text(
            '#!/bin/bash\nset -eu\n'
            'if [ "$1" = "$C1_EXACT_CP" ]; then\n'
            '  printf ready > "$C1_BARRIER/ready"\n'
            '  read -r token < "$C1_BARRIER/release"\nfi\n'
            'exec "$C1_REAL_CP" "$@"\n', encoding="utf-8")
        (adapter / "cp").chmod(0o700)
        env = dict(self.env, PATH=str(adapter) + os.pathsep + os.environ["PATH"],
                   C1_EXACT_CP=str(self.skill / "assets/presets/placeholders/placeholder_av.mp4"),
                   C1_BARRIER=str(barrier), C1_REAL_CP=real_cp)
        ready = os.open(barrier / "ready", os.O_RDWR | os.O_NONBLOCK)
        release = os.open(barrier / "release", os.O_RDWR | os.O_NONBLOCK)
        proc = subprocess.Popen(["bash", str(self.entry), directory], cwd=self.cwd,
                                env=env, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        try:
            self.assertTrue(select.select([ready], [], [], 20)[0], "cp barrier not reached")
            self.assertEqual(os.read(ready, 5), b"ready")
            yield proc
        finally:
            os.write(release, b"continue\n")
            try:
                proc.c1_stdout, proc.c1_stderr = proc.communicate(timeout=40)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                raise
            finally:
                os.close(ready)
                os.close(release)

    def test_create_semantics_frozen_candidate_and_real_replay_undo(self):
        extension = {"path": "placeholders/custom", "nested": [9007199254740993, 0.125,
                     {"unknown": True}], "title": "{{标题}}/{{id}}"}
        self.document["unknown"] = extension
        self.skeleton.write_text(json.dumps(self.document, ensure_ascii=False), encoding="utf-8")
        with self.slow_copy() as proc:
            target = self.cwd / "project/timeline.json"
            self.assertFalse(os.path.lexists(target))
            self.assertFalse(list(target.parent.glob(".vtx*")))
            # Frozen bytes, not a later reload of even the isolated preset.
            self.skeleton.write_bytes(b'null')
        self.success(subprocess.CompletedProcess([], proc.returncode, proc.c1_stdout, proc.c1_stderr))
        got = json.loads(target.read_bytes())
        expected = json.loads(json.dumps(self.document).replace('"placeholders/', '"assets/placeholders/'))
        expected.setdefault("_meta", {})["scaffolded"] = "由 templates/tech-talk/scripts/new_project.sh 自 presets/timeline.skeleton.json 实例化；实例化步骤见 _meta.instantiate_steps"
        self.assertEqual(got, expected)
        self.assertNotIn("_vtx", got)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertEqual(target.read_bytes(), json.dumps(got, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode())
        status = self.status(target)
        self.assertEqual(len(status["operations"]), 1)
        self.assertEqual(status["operations"][0]["state"], "committed")
        intents = list(Path(status["evidence_path"]).glob("op-*/intent.json"))
        self.assertEqual(len(intents), 1)
        request = json.loads(intents[0].read_bytes())["request"]
        self.assertEqual(request["op_id"], status["operations"][0]["op_id"])
        self.assertEqual({k: request[k] for k in ("protocol", "operation", "base_revision", "identity_mode", "payload_mode", "purpose")},
                         dict(protocol=2, operation="create", base_revision=None,
                              identity_mode="preserve-unmanaged", payload_mode="document", purpose="create"))
        self.assertTrue(self.cli_request(target, request)["replayed"])
        # Create itself is not undoable; a public normal replacement is.
        base = target.read_bytes()
        changed = dict(got, unknown={"changed": True})
        replace = dict(request, operation="replace", op_id="c1-replace", purpose="legacy-write",
                       base_revision=status["current_revision"], document=changed)
        changed_result = self.cli_request(target, replace)
        undo = {k: v for k, v in replace.items() if k != "document"}
        undo.update(operation="undo", op_id="c1-undo", base_revision=changed_result["revision"], undo_of="c1-replace")
        self.cli_request(target, undo)
        self.assertEqual(target.read_bytes(), base)

    def test_existing_managed_and_unmanaged_skip_without_store_writes(self):
        for name in ("unmanaged", "managed", "unmanaged-no-history"):
            with self.subTest(mode=name):
                target = self.cwd / name / "timeline.json"
                if name == "unmanaged-no-history":
                    target.parent.mkdir()
                    target.write_bytes(self.raw.replace(b'"placeholders/', b'"assets/placeholders/'))
                    shutil.copytree(self.skill / "assets/presets/placeholders",
                                    target.parent / "assets/placeholders")
                    original = self.fingerprint([target])
                    # Existing branch must not even strict-read the isolated preset.
                    self.skeleton.write_bytes(b'null')
                self.success(self.run_creator(name))
                if name == "managed":
                    result = self.api(target,
                        "s=read_timeline_for_write(sys.argv[1]); "
                        "print(json.dumps(timeline_transaction(sys.argv[1], dict(operation='adopt',op_id='c1-adopt',base_revision=s['revision']))))")
                    self.assertTrue(result["ok"], result)
                if name == "unmanaged-no-history":
                    self.assertEqual(self.fingerprint([target]), original)
                    self.assertFalse(list(target.parent.glob(".vtx*")))
                    self.assertNotIn("_vtx", json.loads(target.read_bytes()))
                    paths = [target]  # Do NOT call status: even that can create a lock.
                else:
                    status = self.status(target)
                    paths = [target, *Path(status["evidence_path"]).rglob("*")]
                before = self.fingerprint(paths)
                cache = target.parent / "cache"
                self.assertTrue(cache.resolve().is_relative_to(self.home))
                cache.rmdir()  # validate leaves this cache empty; prove skip still creates it.
                receipt = target.parent / "out/receipt.json"
                old_receipt_inode = receipt.stat().st_ino
                rerun = self.run_creator(name)
                self.success(rerun)
                self.assertEqual(self.fingerprint(paths), before)
                self.assertTrue(cache.is_dir())
                self.assertNotEqual(receipt.stat().st_ino, old_receipt_inode)
                self.assertIn(f"⚠ 已存在，不覆盖：{name}/timeline.json", rerun.stdout)
                if name == "unmanaged-no-history":
                    self.assertFalse(list(target.parent.glob(".vtx*")))
                    self.assertNotIn("_vtx", json.loads(target.read_bytes()))

    def test_existing_bad_json_and_aliases_take_original_validator_route(self):
        for kind in ("bad", "symlink", "hardlink", "directory"):
            with self.subTest(kind=kind):
                project = self.cwd / kind
                project.mkdir()
                target = project / "timeline.json"
                foreign = project / "foreign"
                foreign.write_bytes(b'{bad old json')
                if kind == "bad": target.write_bytes(foreign.read_bytes())
                elif kind == "symlink": target.symlink_to(foreign)
                elif kind == "hardlink": os.link(foreign, target)
                else: target.mkdir()
                before = self.fingerprint([target, foreign])
                run = self.run_creator(kind)
                self.failed(run)
                self.assertIn(f"⚠ 已存在，不覆盖：{kind}/timeline.json", run.stdout)
                self.assertIn("── vtx validate", run.stdout)
                self.assertEqual(self.fingerprint([target, foreign]), before)
                self.assertFalse(list(project.glob(".vtx-store-*")))
                self.assertTrue((project / "presets/mixing.json").exists())

    def test_late_foreign_bytes_and_alias_geometry_never_rebase_or_skip(self):
        for kind in ("valid", "bad", "symlink", "dangling", "directory", "hardlink"):
            with self.subTest(kind=kind):
                with self.slow_copy(kind) as proc:
                    target = self.cwd / kind / "timeline.json"
                    self.assertFalse(os.path.lexists(target))
                    foreign = target.parent / "foreign"
                    foreign.write_bytes(self.raw if kind != "bad" else b'{bad foreign json')
                    if kind in ("valid", "bad"): target.write_bytes(foreign.read_bytes())
                    elif kind == "symlink": target.symlink_to(foreign)
                    elif kind == "dangling": target.symlink_to(target.parent / "absent")
                    elif kind == "directory": target.mkdir()
                    else: os.link(foreign, target)
                    before = self.fingerprint([target, foreign])
                run = subprocess.CompletedProcess([], proc.returncode, proc.c1_stdout, proc.c1_stderr)
                self.failed(run)
                self.assertNotIn("── vtx validate", run.stdout)
                self.assertNotIn(f"⚠ 已存在，不覆盖：{kind}/timeline.json", run.stdout)
                self.assertEqual(self.fingerprint([target, foreign]), before)
                self.assertFalse(list(target.parent.rglob("intent.json")))
                self.assertFalse((target.parent / "presets/gates.json").exists())
                self.assertTrue((target.parent / "00-structure.md").exists())
                self.assertIn('"base_revision": null', run.stderr)
                if kind == "valid": self.assertIn('"code": "conflict"', run.stderr)

    def test_raw_rejection_and_initial_dangling_are_not_creates(self):
        variants = (b'{"x":1.0000000000000001}', b'{"x":1,"x":2}',
                    b'{"x":NaN}', b'null', b'{"project": []}')
        for i, raw in enumerate(variants):
            with self.subTest(raw=raw):
                self.skeleton.write_bytes(raw)
                name = f"raw-{i}"
                run = self.run_creator(name)
                self.failed(run)
                project = self.cwd / name
                self.assertFalse(os.path.lexists(project / "timeline.json"))
                self.assertFalse(list(project.rglob("intent.json")))
                self.assertTrue((project / "cache").is_dir())  # non-TL directory residue
        project = self.cwd / "initial-dangling"
        project.mkdir()
        target = project / "timeline.json"
        target.symlink_to(project / "not-created")
        self.failed(self.run_creator("initial-dangling"))
        self.assertTrue(target.is_symlink())
        self.assertFalse(target.exists())
        self.assertFalse((project / "not-created").exists())
        self.assertFalse(list(project.glob(".vtx*")))

    def test_postcommit_real_validator_failure_does_not_claim_rollback(self):
        # Raw/structural publication is legal, but real validation needs media.
        self.document["sources"][0]["path"] = "missing-owned-media.mp4"
        self.skeleton.write_text(json.dumps(self.document), encoding="utf-8")
        run = self.run_creator()
        self.failed(run)
        self.assertIn("── vtx validate", run.stdout)
        self.assertIn("本次 TL 已提交", run.stderr)
        target = self.cwd / "project/timeline.json"
        self.assertTrue(target.is_file())
        self.assertEqual(self.status(target)["operations"][0]["state"], "committed")
        self.assertTrue((target.parent / "presets/gates.json").is_file())

    def test_preflight_defaults_relative_spaces_and_symlink_parent_dotdot(self):
        absent = self.skill / "assets/presets/gates.json"
        saved = absent.read_bytes()
        self.assertTrue(absent.resolve().is_relative_to(self.home))
        absent.unlink()
        run = self.run_creator("must-not-exist")
        self.failed(run)
        self.assertIn("前置检查失败", run.stderr)
        self.assertFalse((self.cwd / "must-not-exist").exists())
        absent.write_bytes(saved)
        self.success(self.run_creator("relative spaces"))
        brief = (self.cwd / "relative spaces/01-brief.md").read_text()
        self.assertIn("未定标题", brief)
        self.assertIn("代号 relative spaces /", self.run_creator("relative spaces").stdout)
        date = subprocess.check_output(["date", "+%F"], text=True).strip()
        self.assertIn(date, brief)
        self.assertIn("{{", brief)
        named = self.run_creator("named", "--title", "标题值", "--id", "custom-id")
        self.success(named)
        self.assertIn(f'标题「标题值」/ 代号 custom-id / {date}', named.stdout)
        self.assertIn("标题值", (self.cwd / "named/01-brief.md").read_text())
        (self.cwd / "physical/inside").mkdir(parents=True)
        (self.cwd / "alias").symlink_to(self.cwd / "physical/inside", target_is_directory=True)
        decoy = self.cwd / "actual project/timeline.json"
        decoy.parent.mkdir()
        decoy.write_bytes(b'{bad lexical decoy')
        before = self.fingerprint([decoy])
        run = self.run_creator("alias/../actual project")
        self.success(run)
        self.assertTrue((self.cwd / "physical/actual project/timeline.json").is_file())
        self.assertEqual(self.fingerprint([decoy]), before)
        self.assertIn("✓ 生成 alias/../actual project/timeline.json", run.stdout)
        # Independent existing CLI evidence: raw relative input selects the decoy.
        legacy = subprocess.run(["python3", str(self.repo / "vtx/vtx.py"), "-t",
                                 "alias/../actual project/timeline.json", "validate"],
                                cwd=self.cwd, env=self.env, text=True,
                                capture_output=True, timeout=30)
        self.assertNotEqual(legacy.returncode, 0, legacy.stdout + legacy.stderr)
        self.assertIn("✗ timeline 无效：Expecting property name", legacy.stdout)
        self.assertNotIn("校验通过", legacy.stdout)
        self.assertEqual(self.fingerprint([decoy]), before)


if __name__ == "__main__":
    unittest.main()
