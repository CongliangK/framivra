"""Ordinary creator behavior tests: real script + current-source public kernel.

Only stdlib fixtures; no media, renderer, network, installed package or CI changes.
The test driver supports -I -B. Script/API children deliberately use the normal
source-root stdin route (not -I, which removes that import path); HOME/XDG stay
unchanged and PYTHONDONTWRITEBYTECODE=1 prevents bytecode writes.
"""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import select
import shlex
import shutil
import signal
import stat
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]


def expected(frames=900):
    """Hand oracle, not parsed from creator code or an old generated fixture."""
    return {
        "version": 2,
        "project": {"fps": 30, "width": 1920, "height": 1080, "duration_frames": frames},
        "sources": [{"id": "v01", "path": "assets/v01.mp4", "transform": "crop_center_scale"}],
        "video_tracks": [
            {"name": "aroll", "kind": "aroll", "clips": [
                {"src": "v01", "src_in": 0, "src_out": frames, "dst_in": 0, "dur": frames}]},
            {"name": "gfx", "kind": "gfx", "clips": [
                {"src": "gfx/gfxA.html", "dst_in": 0, "dur": 192}]},
        ],
        "subtitles": [{"in": 12, "out": 88, "text": "示例字幕"}],
        "audio_tracks": [
            {"name": "bed", "events": [
                {"src": "v01", "in": 0, "out": frames, "volume": 0.25, "loop": True}]},
            {"name": "voice", "events": [
                {"src": "v01", "src_in": 0, "in": 12, "out": 200, "volume": 0.9}]},
        ],
    }


def revision(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def request(operation, document=None, base=None, op_id="test-foreign", **extra):
    result = dict(protocol=2, operation=operation, op_id=op_id, base_revision=base,
                  identity_mode="preserve-unmanaged", payload_mode="document",
                  purpose="create" if operation == "create" else "legacy-write")
    if document is not None:
        result["document"] = document
    return dict(result, **extra)


class GenericCreator(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="generic-creator-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.repo = self.home / "source copy"
        self.repo.mkdir()
        for name in ("vtx", "plugins"):
            shutil.copytree(REPO / name, self.repo / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (self.repo / "scripts/templates").mkdir(parents=True)
        for name in ("new_project.sh", "templates/gfx-starter.html"):
            shutil.copy2(REPO / "scripts" / name, self.repo / "scripts" / name)
        self.entry = self.repo / "scripts/new_project.sh"
        self.cwd = self.home / "caller cwd"
        self.cwd.mkdir()
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        self.env.pop("PYTHONPATH", None)
        self.env.pop("PYTHONHOME", None)
        self.env["VTX_CONFIG"] = str(self.home / "absent-config.json")

    def run_creator(self, directory="project", *args, env=None):
        return subprocess.run(["bash", str(self.entry), directory, *args], cwd=self.cwd,
                              env=env or self.env, capture_output=True, text=True, timeout=30)

    def success(self, run):
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("timeline 已提交", run.stdout)
        self.assertIn("✓ 项目已创建", run.stdout)

    def failed(self, run):
        self.assertNotEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertNotIn("✓ 项目已创建", run.stdout)

    def api(self, target, body, value=None):
        run = subprocess.run([shutil.which("python3"), "-B", "-", str(target)],
                             input="import json,sys\nfrom pathlib import Path\n"
                             "import vtx.api as api\nvalue=" + repr(value) + "\n" + body,
                             cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        return json.loads(run.stdout)

    def transact(self, target, value):
        return self.api(target, "print(json.dumps(api.timeline_transaction(sys.argv[1], value)))", value)

    def status(self, target, recover=False):
        value = self.api(target, "print(json.dumps(api.timeline_store_status(sys.argv[1], recover=value)))", recover)
        self.assertTrue(value["ok"], value)
        return value

    def intent(self, target):
        status = self.status(target)
        intents = list(Path(status["evidence_path"]).glob("op-*/intent.json"))
        self.assertEqual(len(intents), 1)
        return intents[0], json.loads(intents[0].read_bytes())

    @staticmethod
    def fingerprint(root):
        result = {}
        for p in [root, *sorted(root.rglob("*"))]:
            s = p.lstat()
            result[str(p.relative_to(root))] = (
                s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns,
                os.readlink(p) if p.is_symlink() else p.read_bytes() if p.is_file() else None)
        return result

    def adapter(self, mode):
        """Own process-only public API/OS interposition, never product edits.

        Trace the actual public plan/transaction, or block fsync of the real
        staging fd before final CAS, or raise after a real target os.replace.
        """
        bindir = self.home / ("python-" + mode)
        bindir.mkdir()
        bootstrap = '''import json,os,sys
import vtx.api as api
real_plan,real_tx = api.timeline_write_plan,api.timeline_transaction
def log(kind, req, result=None):
    with open(os.environ["GC_TRACE"], "a") as f:
        f.write(json.dumps(dict(kind=kind, request=req, result=result, source=api.__file__, cwd=os.getcwd()))+"\\n")
def plan(path, req):
    result=real_plan(path,req)
    log("plan",req,result)
    return result
def tx(path, req):
    log("transaction",req)
    return real_tx(path,req)
api.timeline_write_plan,api.timeline_transaction=plan,tx
mode=os.environ["GC_MODE"]
if mode=="final-cas":
    real_fsync=os.fsync
    def fsync(fd):
        result=real_fsync(fd)
        if "/.vtx-timeline-" in os.readlink("/proc/self/fd/"+str(fd)):
            with open(os.environ["GC_BARRIER"]+"/ready", "w") as f: f.write("ready")
            with open(os.environ["GC_BARRIER"]+"/release") as f: f.readline()
        return result
    os.fsync=fsync
elif mode=="unknown":
    real_replace=os.replace
    def replace(src,dst,*a,**kw):
        result=real_replace(src,dst,*a,**kw)
        if str(dst)==os.environ["GC_TARGET"]:
            raise OSError("own test: response lost after real timeline replace")
        return result
    os.replace=replace
sys.argv=sys.argv[1:]
exec(compile(sys.stdin.read(),"<creator-stdin>","exec"))
'''
        wrapper = bindir / "python3"
        # Pass production argv unchanged to a normal source-root Python child.
        wrapper.write_text("#!/bin/bash\nexec " + json.dumps(shutil.which("python3"))
                           + " -c " + shlex.quote(bootstrap) + ' "$@"\n')
        wrapper.chmod(0o700)
        trace = self.home / (mode + ".jsonl")
        env = dict(self.env, PATH=str(bindir) + os.pathsep + self.env["PATH"],
                   GC_MODE=mode, GC_TRACE=str(trace))
        return env, trace

    @contextlib.contextmanager
    def barrier(self, mode, directory="project"):
        barrier = self.home / ("barrier-" + mode)
        barrier.mkdir()
        for name in ("ready", "release"):
            os.mkfifo(barrier / name)
        if mode == "copy":
            bindir = barrier / "bin"
            bindir.mkdir()
            (bindir / "cp").write_text(
                '#!/bin/bash\nset -eu\n'
                'if [ "$1" = "$GC_SOURCE" ]; then\n'
                ' printf ready > "$GC_BARRIER/ready"\n'
                ' read -r token < "$GC_BARRIER/release"\nfi\n'
                'exec "$GC_REAL_CP" "$@"\n')
            (bindir / "cp").chmod(0o700)
            env = dict(self.env, PATH=str(bindir) + os.pathsep + self.env["PATH"],
                       GC_SOURCE=str(self.repo / "vtx/timeline.schema.json"),
                       GC_REAL_CP=shutil.which("cp"))
        else:
            env, _ = self.adapter(mode)
        env["GC_BARRIER"] = str(barrier)
        ready = os.open(barrier / "ready", os.O_RDWR | os.O_NONBLOCK)
        release = os.open(barrier / "release", os.O_RDWR | os.O_NONBLOCK)
        proc = subprocess.Popen(["bash", str(self.entry), directory], cwd=self.cwd,
                                env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
        try:
            self.assertTrue(select.select([ready], [], [], 20)[0], "real barrier not reached")
            self.assertEqual(os.read(ready, 5), b"ready")
            yield proc
        finally:
            os.write(release, b"continue\n")
            try:
                stdout, stderr = proc.communicate(timeout=30)
                proc.result = subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()
                raise
            finally:
                os.close(ready)
                os.close(release)

    def test_defaults_decimal_plan_same_request_mode_and_one_create(self):
        for index, args in enumerate(((), ("",), ("7",), ("0007", "ignored-extra"))):
            with self.subTest(args=args):
                env, trace = self.adapter("trace" + str(index))
                name = "project " + str(index)
                self.success(self.run_creator(name, *args, env=env))
                target = self.cwd / name / "timeline.json"
                got = json.loads(target.read_bytes())
                want = expected(900 if index < 2 else 210)
                self.assertEqual(got, want)
                self.assertNotIn("_vtx", got)
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
                self.assertFalse((target.parent / "assets/v01.mp4").exists())
                self.assertEqual((target.parent / "timeline.schema.json").read_bytes(),
                                 (self.repo / "vtx/timeline.schema.json").read_bytes())
                self.assertEqual((target.parent / "gfx/gfxA.html").read_bytes(),
                                 (self.repo / "scripts/templates/gfx-starter.html").read_bytes())
                events = [json.loads(line) for line in trace.read_text().splitlines()]
                self.assertEqual([e["kind"] for e in events], ["plan", "transaction"])
                self.assertEqual(events[0]["request"], events[1]["request"])
                self.assertEqual(events[0]["result"]["document"], want)
                self.assertEqual(events[0]["source"], str(self.repo / "vtx/api.py"))
                self.assertEqual(events[0]["cwd"], str(self.cwd))
                _, intent = self.intent(target)
                req = intent["request"]
                self.assertEqual(req, events[0]["request"])
                self.assertEqual(req, request("create", want, op_id=req["op_id"]))
                self.assertEqual(len(self.status(target)["operations"]), 1)

    def test_invalid_seconds_rejected_before_any_directory_or_auxiliary(self):
        for secs in ("0", "6", "-7", "+7", "7.0", "1+6", "7e0", " 7", "７", "08x"):
            with self.subTest(seconds=secs):
                run = self.run_creator("invalid", secs)
                self.failed(run)
                self.assertIn("前置失败", run.stderr)
                self.assertFalse((self.cwd / "invalid").exists())

    def test_strict_bad_and_managed_existing_rejected_before_aux(self):
        managed = self.cwd / "managed/timeline.json"
        managed.parent.mkdir()
        managed.write_text(json.dumps(expected()))
        # Explicit public adoption is fixture setup only; the creator must refuse it.
        adopted = self.transact(managed, dict(operation="adopt", op_id="fixture-adopt",
                                            base_revision=revision(managed.read_bytes())))
        self.assertTrue(adopted["ok"], adopted)
        raw_cases = [b"null", b'{"version":2,"version":2}', b'{"x":NaN}',
                     json.dumps(dict(expected(), unknown=0.1)).replace('0.1', '0.10000000000000001').encode(),
                     b'{"version":2,"project":{}}',
                     json.dumps(dict(expected(), _vtx=None)).encode(),
                     json.dumps(dict(expected(), project=dict(expected()["project"], duration_frames=1))).encode()]
        targets = [managed]
        for index, raw in enumerate(raw_cases):
            target = self.cwd / ("bad" + str(index)) / "timeline.json"
            target.parent.mkdir()
            target.write_bytes(raw)
            targets.append(target)
        for target in targets:
            with self.subTest(target=target.parent.name):
                before = self.fingerprint(target.parent)
                self.failed(self.run_creator(target.parent.name))
                self.assertEqual(self.fingerprint(target.parent), before)

    def test_legacy_whole_replace_raw_history_real_head_undo_and_create_no_undo(self):
        target = self.cwd / "legacy/timeline.json"
        target.parent.mkdir()
        old = expected(1200)
        old["extension"] = {"nested": [9007199254740993, None, {"custom": "保留原始历史"}]}
        old["project"]["owner-extension"] = "drop from replacement"
        raw = (json.dumps(old, ensure_ascii=False, indent=3) + "\n \n").encode()
        target.write_bytes(raw)
        target.chmod(0o640)
        (target.parent / "timeline.schema.json").write_bytes(b"old-schema")
        self.success(self.run_creator("legacy", "7"))
        self.assertEqual((target.parent / "timeline.schema.json").read_bytes(),
                         (self.repo / "vtx/timeline.schema.json").read_bytes())
        self.assertEqual(json.loads(target.read_bytes()), expected(210))
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)
        path, intent = self.intent(target)
        self.assertEqual((path.parent / "before.bin").read_bytes(), raw)
        self.assertEqual((path.parent / "after.bin").read_bytes(), target.read_bytes())
        self.assertEqual(intent["request"], request("replace", expected(210),
                         revision(raw), intent["op_id"]))
        undo = request("undo", base=revision(target.read_bytes()),
                       op_id="test-undo", undo_of=intent["op_id"])
        # Exact current-after is mandatory, even with a fresh base revision.
        after = target.read_bytes()
        target.write_bytes(after + b"\n")
        undo["base_revision"] = revision(target.read_bytes())
        self.assertFalse(self.transact(target, undo)["ok"])
        target.write_bytes(after)
        undo["base_revision"] = revision(after)
        self.assertTrue(self.transact(target, undo)["ok"])
        self.assertEqual(target.read_bytes(), raw)
        self.assertEqual(json.loads(target.read_bytes())["extension"], old["extension"])
        self.success(self.run_creator("new"))
        new = self.cwd / "new/timeline.json"
        _, created = self.intent(new)
        result = self.transact(new, request("undo", base=revision(new.read_bytes()),
                               op_id="test-no-create-undo", undo_of=created["op_id"]))
        self.assertFalse(result["ok"], result)
        self.assertEqual(json.loads(new.read_bytes()), expected())

    def test_existing_regular_symlink_and_dangling_gfx_are_unchanged(self):
        for kind in ("regular", "symlink", "dangling"):
            project = self.cwd / kind
            (project / "gfx").mkdir(parents=True)
            gfx = project / "gfx/gfxA.html"
            external = self.cwd / (kind + "-external")
            if kind != "dangling":
                external.write_bytes(b"external-custom")
            if kind == "regular":
                gfx.write_bytes(b"custom-gfx")
            else:
                gfx.symlink_to(external)
            before = (gfx.lstat(), gfx.read_bytes() if kind != "dangling" else None)
            self.success(self.run_creator(kind))
            after = gfx.lstat()
            self.assertEqual((after.st_ino, after.st_mode, after.st_mtime_ns, after.st_ctime_ns),
                             (before[0].st_ino, before[0].st_mode, before[0].st_mtime_ns, before[0].st_ctime_ns))
            if kind != "dangling":
                self.assertEqual(gfx.read_bytes(), before[1])
            else:
                self.assertEqual(os.readlink(gfx), str(external))
                self.assertFalse(external.exists())

    def test_source_copy_failure_keeps_old_timeline_or_new_absent_partial_aux(self):
        # Missing starter makes the second real cp fail after schema was copied.
        (self.repo / "scripts/templates/gfx-starter.html").unlink()
        for existing in (False, True):
            name = "copy-failure-" + str(existing)
            project = self.cwd / name
            project.mkdir()
            target = project / "timeline.json"
            raw = (json.dumps(expected(), indent=2) + "\n").encode()
            if existing:
                target.write_bytes(raw)
            run = self.run_creator(name)
            self.failed(run)
            self.assertIn("辅助文件准备失败", run.stderr)
            self.assertIn("辅助文件可能部分保留", run.stderr)
            self.assertEqual(target.read_bytes() if existing else None, raw if existing else None)
            self.assertEqual(target.exists(), existing)
            self.assertTrue((project / "timeline.schema.json").exists())
            self.assertEqual(sorted(p.name for p in project.iterdir()),
                             sorted(["assets", "gfx", "cache", "out", "timeline.schema.json"]
                                    + (["timeline.json"] if existing else [])))

    def test_physical_parent_alias_dotdot_spaces_no_decoy_writes(self):
        (self.cwd / "real space/child").mkdir(parents=True)
        (self.cwd / "alias").symlink_to(self.cwd / "real space/child", target_is_directory=True)
        self.success(self.run_creator("alias/../new project", "0007"))
        target = self.cwd / "real space/new project/timeline.json"
        self.assertEqual(json.loads(target.read_bytes()), expected(210))
        self.assertFalse((self.cwd / "new project").exists())
        self.assertFalse((self.cwd / "real space/child/new project").exists())
        self.assertFalse((self.repo / "new project").exists())
        _, intent = self.intent(target)
        self.assertEqual(intent["request"]["operation"], "create")

    def test_foreign_public_publisher_during_real_slow_copy_wins(self):
        target = self.cwd / "project/timeline.json"
        with self.barrier("copy") as proc:
            self.assertFalse(target.exists())
            self.assertFalse(list(target.parent.glob(".vtx*")))
            foreign = dict(expected(), foreign="during copy")
            self.assertTrue(self.transact(target, request("create", foreign))["ok"])
            raw = target.read_bytes()
        self.failed(proc.result)
        self.assertEqual(target.read_bytes(), raw)
        self.assertIn('"code": "conflict"', proc.result.stderr)
        self.assertEqual(len(self.status(target)["operations"]), 1)

    def test_after_plan_foreign_write_at_real_staging_final_cas_wins(self):
        target = self.cwd / "project/timeline.json"
        # Lock-respecting publishers serialize at this point; a foreign writer
        # that does not take the store lock is what final CAS must detect.
        with self.barrier("final-cas") as proc:
            self.assertFalse(target.exists())
            foreign = (json.dumps(dict(expected(), foreign="after plan at staged fsync"), indent=2) + "\n").encode()
            target.write_bytes(foreign)
        self.failed(proc.result)
        self.assertEqual(target.read_bytes(), foreign)
        self.assertIn("final CAS conflict", proc.result.stderr)
        status = self.status(target)
        self.assertTrue(status["pending"])
        self.assertEqual([op["state"] for op in status["operations"]], ["pending"])

    def test_public_plan_zero_write_and_original_unsupported_dry_argument(self):
        project = self.cwd / "plan"
        project.mkdir()
        target = project / "timeline.json"
        before = self.fingerprint(project)
        plan = self.api(target, "print(json.dumps(api.timeline_write_plan(sys.argv[1], value)))",
                        request("create", expected(210)))
        self.assertTrue(plan["ok"], plan)
        self.assertEqual(plan["document"], expected(210))
        self.assertEqual(self.fingerprint(project), before)
        # No invented --dry flag: positional SECS rejects it, old extra args
        # remain ignored (not a dry-run promise).
        self.failed(self.run_creator("not-dry", "--dry"))
        self.assertFalse((self.cwd / "not-dry").exists())
        self.success(self.run_creator("extra-dry", "7", "--dry"))
        self.assertEqual(json.loads((self.cwd / "extra-dry/timeline.json").read_bytes()), expected(210))

    def test_false_public_plan_rejects_before_auxiliary_changes(self):
        self.success(self.run_creator())
        target = self.cwd / "project/timeline.json"
        status = self.status(target)
        # Corrupt only our own fixture history, then prove early plan failure
        # does not copy even the schema over an existing custom file.
        manifest = Path(status["evidence_path"]) / "manifest.json"
        self.assertTrue(manifest.is_file())
        manifest.write_bytes(b"null")
        (target.parent / "timeline.schema.json").write_bytes(b"custom-schema")
        before = self.fingerprint(target.parent)
        run = self.run_creator()
        self.failed(run)
        self.assertIn("timeline_write_plan", run.stderr)
        self.assertEqual(self.fingerprint(target.parent), before)

    def test_uncertain_outcome_retains_original_request_and_manual_recovery_proof(self):
        env, trace = self.adapter("unknown")
        target = self.cwd / "project/timeline.json"
        env["GC_TARGET"] = str(target)
        run = self.run_creator(env=env)
        self.failed(run)
        self.assertIn("可能已提交", run.stderr)
        self.assertIn('"code": "outcome_unknown"', run.stderr)
        self.assertNotIn("本次未提交 TL", run.stderr)
        retained = next(json.loads(line) for line in run.stderr.splitlines() if line.startswith('{"request"'))
        events = [json.loads(line) for line in trace.read_text().splitlines()]
        self.assertEqual([e["kind"] for e in events], ["plan", "transaction"])
        self.assertEqual(retained["request"], events[0]["request"])
        self.assertEqual(retained["request"], events[1]["request"])
        self.assertEqual(retained["target"], str(target))
        self.assertEqual(json.loads(target.read_bytes()), expected())
        self.assertTrue(self.status(target)["pending"])
        recovered = self.status(target, recover=True)
        self.assertFalse(recovered["pending"])
        self.assertEqual(recovered["operations"], [{"op_id": retained["request"]["op_id"], "state": "committed"}])
        self.assertEqual(json.loads(target.read_bytes()), expected())


if __name__ == "__main__":
    unittest.main()
