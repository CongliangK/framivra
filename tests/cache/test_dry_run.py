"""A01: every CLI dry spelling is a read-only plan, including cleanup/snapshot.

Run: PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.cache.test_dry_run
All writable fixtures are isolated temporary projects; no Chrome/FFmpeg required.
"""
import contextlib
import io
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from tests._support import cleanup_timeline, tree_sha256
from unittest import mock

from vtx import cli, hygiene
from tests.gfx._environment import bind_environment, environment_cli
from vtx.infra import Ctx

REPO = Path(__file__).resolve().parents[2]
VTX = REPO / "vtx" / "vtx.py"
DRY_FLAGS = (("--dry-run",), ("--dry",), ("--dry-run", "--dry"),
             ("--dry", "--dry-run"), ("--dry-run", "--dry-run"),
             ("--dry", "--dry"))


class DryRunRegression(unittest.TestCase):
    def setUp(self):
        bind_environment(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.timeline = cleanup_timeline(
            self.root, subtitles=[{"in": 0, "out": 30, "text": "字幕"}])
        files = {
            "cache/gfx/stale/f0000.png": b"old png",
            "cache/seg/stale.mp4": b"old segment",
            "cache/audio/stale.wav": b"old extracted audio",
            "cache/audio/loudness_receipts.json": b'{"entries":{}}',
            "cache/reports/evidence.json": b'{"proof":true}',
            "cache/manifest.json": b'{"entries":[]}',
            "cache/qc_pass_segs.json": b'{"version":1}',
            "cache/tmp/orphan/work.bin": b"orphan",
            "cache/vseg/list.txt": b"old concat list",
            "cache/subs.ass": b"old subtitles",
            "out/final.mp4": b"current final",
            "out/final_4k.mp4": b"4k deliverable",
            "out/FREEZE.json": b'{"frozen":true}',
            "out/VERSIONS.json": b'{"versions":[]}',
            "out/history/final_old.mp4": b"historical final",
            "out/receipt.json": b'{"stages":{}}',
            "out/reports/evidence.json": b'{"proof":true}',
            "out/intermediate.bin": b"disposable",
        }
        for number in range(4):
            files[f"cache/snapshots/2000010{number}/timeline.json"] = b"{}"
        for name, content in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        os.utime(self.root / "cache/tmp/orphan", (1, 1))
        (self.root / "cache/empty").mkdir()
        c = Ctx(self.timeline)
        live_key = hygiene.gfx_key(c, c.tl["video_tracks"][0]["clips"][0])
        live = self.root / "cache/gfx" / live_key
        live.mkdir()
        (live / "f0000.png").write_bytes(b"live but incomplete gfx cache")
        (self.root / "cache/audio" / (hygiene.audio_key(c, "voice") + ".wav")) \
            .write_bytes(b"live audio")

    def run_vtx(self, *args, env_extra=None):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", VTX_NO_NVENC="1",
                   VTX_DOCTOR_OFFLINE="1")
        env.update(env_extra or {})
        return subprocess.run([*environment_cli(),
                               "-t", str(self.timeline), *args],
                              cwd=REPO, env=env, capture_output=True, text=True,
                              timeout=30)

    def assert_read_only(self, *args, env_extra=None):
        before = tree_sha256(self.root)
        result = self.run_vtx(*args, env_extra=env_extra)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(tree_sha256(self.root), before, args)
        return result

    def test_cleanup_aliases_positions_and_gc_options(self):
        # Both spellings, both orders, repetitions, before/after/split around command.
        commands = [("clean",), ("snapshot",), ("gc",), ("gc", "--all")]
        commands += [("gc", *deep, "--keep-days", days)
                     for deep, days in itertools.product(((), ("--all",)), ("0", "7"))]
        for command, flags in itertools.product(commands, DRY_FLAGS):
            argv_variants = [(*flags, *command), (*command, *flags)]
            if len(flags) == 2:
                argv_variants.append((flags[0], *command, flags[1]))
            for argv in argv_variants:
                with self.subTest(argv=argv):
                    self.assert_read_only(*argv)

    def test_all_other_commands_and_render_options_are_read_only(self):
        commands = [(name,) for name, _ in cli.STAGES] + [
            ("all",), ("df",), ("subs", "render"), ("subs", "check"),
            ("subs", "preview", "0", "f10"),
            ("diff", str(self.timeline), str(self.timeline)),
            ("video", "--draft"), ("video", "--draft", "--frames", "10"),
            ("video", "--gpu"), ("mux", "--draft"), ("qc", "--strict"),
            ("mux", "--keep-versions", "0"),
            ("video", "--draft", "--range", "0", "0.5"),
            ("video", "--draft", "--clip", "title"),
            ("video", "--draft", "--track", "title"),
        ]
        commands += [("all", "--until", stage) for stage in cli._UNTIL_STAGES]
        # Tiny budget plus auto GC deliberately makes the legacy budget path dangerous.
        env = {"VTX_CACHE_BUDGET_GB": "0.000000001", "VTX_GC_AUTO": "1",
               "VTX_SNAPSHOT_KEEP": "0"}
        for command, flags in itertools.product(commands, DRY_FLAGS):
            with self.subTest(command=command, flags=flags):
                self.assert_read_only(*command, *flags, env_extra=env)
        for flags in DRY_FLAGS:
            self.assert_read_only("all", "--until", "video", *flags,
                                  env_extra={**env, "VTX_PIPELINE": "0"})

    def test_missing_cache_out_are_not_created(self):
        empty = self.root / "new_project"
        empty.mkdir()
        self.timeline = empty / "timeline.json"
        self.timeline.write_text(json.dumps({
            "version": 2, "project": {"fps": 30, "width": 1920, "height": 1080,
                                      "duration_frames": 30},
            "sources": [], "video_tracks": [], "audio_tracks": [], "subtitles": []
        }))
        for command, flags in itertools.product(
                [("gc",), ("gc", "--all"), ("clean",), ("snapshot",), ("df",),
                 ("all",), ("subs", "preview", "0")], DRY_FLAGS):
            with self.subTest(command=command, flags=flags):
                self.assert_read_only(*flags, *command)
        self.assertFalse((empty / "cache").exists())
        self.assertFalse((empty / "out").exists())

    def test_invalid_combinations_exit_two_without_changes(self):
        bad = [
            ("gc", "--draft"), ("clean", "--all"), ("snapshot", "--keep-days", "0"),
            ("gc", "--keep-days", "-1"), ("gc", "--keep-days", "bad"),
            ("gc", "extra"), ("clean", "--json", str(self.root / "new.json")),
            ("clean", "--until", "video"), ("all", "--until", "audio"),
            ("video", "--frames", "10"), ("video", "--draft", "--frames", "0"),
            ("audio", "--gpu"), ("video", "--strict"),
            ("audio", "--report"), ("audio", "--report-only"),
            ("audio", "--report", "--report-only"), ("video", "--report"),
            ("video", "--draft", "--range", "0", "1", "--clip", "title"),
            ("video", "--draft", "--range", "0", "1", "--frames", "2"),
            ("video", "--draft", "--range", "0", "1", "--gpu"),
            ("subs", "preview"), ("subs", "check", "extra"), ("subs", "unknown"),
            ("diff", str(self.timeline), str(self.timeline), "--json",
             str(self.root / "dry_diff.json")),
            ("gc", "--keep-versions", "1"), ("unknown",),
        ]
        for argv, flags in itertools.product(bad, DRY_FLAGS):
            with self.subTest(argv=argv, flags=flags):
                before = tree_sha256(self.root)
                result = self.run_vtx(*flags, *argv)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(tree_sha256(self.root), before)

    def test_plan_dispatch_never_calls_mutators(self):
        # Hash equality also needs this guard: create-then-delete would otherwise pass.
        for command in ("all", "snapshot", "clean", "gc", "df"):
            with self.subTest(command=command), contextlib.ExitStack() as stack:
                for owner, name in ((Path, "mkdir"), (Path, "write_text"),
                                    (Path, "write_bytes"), (Path, "unlink"),
                                    (cli.shutil, "rmtree"), (cli, "snapshot"),
                                    (cli, "df"), (cli, "_run_all_pipeline")):
                    stack.enter_context(mock.patch.object(
                        owner, name, side_effect=AssertionError(f"unexpected {name}")))
                stack.enter_context(mock.patch.object(
                    sys, "argv", ["vtx", "-t", str(self.timeline), command, "--dry"]))
                stack.enter_context(mock.patch.dict(os.environ, {"VTX_NO_NVENC": "1"}))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                cli.main()

    def test_cleanup_plan_precedes_execution_and_keeps_existing_rules(self):
        c = Ctx(self.timeline)
        c.dry = True
        before = tree_sha256(self.root)
        ordinary = cli._cleanup_plan(c, "gc")
        deep = cli._cleanup_plan(c, "gc", deep=True)
        self.assertEqual(tree_sha256(self.root), before)
        ordinary_paths = {p.relative_to(self.root).as_posix()
                          for items in ordinary.values() for p, _, _ in items}
        self.assertIn("cache/audio/stale.wav", ordinary_paths)
        self.assertIn("out/intermediate.bin", ordinary_paths)
        self.assertNotIn("cache/audio/loudness_receipts.json", ordinary_paths)
        self.assertNotIn("cache/reports/evidence.json", ordinary_paths)
        self.assertNotIn("out/final.mp4", ordinary_paths)
        self.assertNotIn("out/history", ordinary_paths)
        self.assertNotIn("out/VERSIONS.json", ordinary_paths)
        cli.ExecutionPolicy.EXECUTE.execute_cleanup(deep)
        self.assertFalse((self.root / "cache/gfx").exists())
        self.assertFalse((self.root / "out/intermediate.bin").exists())
        for path in ("cache/reports/evidence.json", "cache/snapshots", "cache/tmp",
                     "cache/manifest.json", "cache/qc_pass_segs.json", "out/final.mp4",
                     "out/history/final_old.mp4", "out/VERSIONS.json", "out/FREEZE.json",
                     "out/final_4k.mp4"):
            self.assertTrue((self.root / path).exists(), path)

    def test_non_dry_cleanup_still_executes_in_temporary_project(self):
        result = self.run_vtx("gc")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / "cache/audio/stale.wav").exists())
        self.assertTrue((self.root / "out/final.mp4").exists())
        result = self.run_vtx("clean")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / "cache").exists())
        # A02（issue #21 / F-01）：普通 clean 默认保留不可再生交付件——out 根
        # 因 final.mp4/VERSIONS.json/history/FREEZE* 存活而保留；全删唯一入口
        # 是显式危险旗标（本用例语义随工单同步，非 dry 执行面不变）
        self.assertTrue((self.root / "out/final.mp4").exists())
        self.assertTrue((self.root / "out/VERSIONS.json").exists())
        self.assertTrue((self.root / "out/history/final_old.mp4").exists())
        result = self.run_vtx("clean", "--i-know-this-nukes-everything")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / "out").exists())
        self.assertTrue(self.timeline.exists())

    def test_cleanup_never_deletes_through_symlinked_roots(self):
        # 红队对抗复审（A03×A01）：cache/out 根被换成指向外部的符号链接后，
        # gc/clean（dry 与 EXECUTE）都不得经链接删除目标内容——统一清理路径
        # 曾跟随根级链接把链接目标当 legacy 垃圾整树删除
        store = self.root / "linked_store"
        store.mkdir()
        (store / "keep.txt").write_bytes(b"precious")
        (store / "sub").mkdir()
        (store / "sub" / "deep.bin").write_bytes(b"deep")
        for victim in (self.root / "cache", self.root / "out"):
            shutil.rmtree(victim)
            victim.symlink_to(store)
        for argv in (("gc", "--dry-run"), ("clean", "--dry-run"), ("gc", "--all", "--dry"),
                     ("gc",), ("gc", "--all"), ("clean",)):
            with self.subTest(argv=argv):
                before = tree_sha256(self.root)
                result = self.run_vtx(*argv)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("拒绝清理符号链接根", result.stdout)
                self.assertEqual(tree_sha256(self.root), before, argv)
        self.assertEqual((store / "keep.txt").read_bytes(), b"precious")
        self.assertEqual((store / "sub" / "deep.bin").read_bytes(), b"deep")
        self.assertTrue((self.root / "cache").is_symlink())

    def test_cleanup_with_one_symlinked_root_still_cleans_the_real_root(self):
        # 只链接 cache：out 仍按既有规则清理（拒绝不扩大到健康根）
        store = self.root / "linked_store"
        store.mkdir()
        (store / "keep.txt").write_bytes(b"precious")
        shutil.rmtree(self.root / "cache")
        (self.root / "cache").symlink_to(store)
        result = self.run_vtx("gc")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((store / "keep.txt").read_bytes(), b"precious")
        self.assertFalse((self.root / "out/intermediate.bin").exists())
        self.assertTrue((self.root / "out/final.mp4").exists())

    def test_snapshot_refuses_symlinked_cache_root(self):
        # 快照 EXECUTE 会 mkdir/写 cache/snapshots：根为符号链接时须拒绝（防
        # 越界写入链接目标）；dry 计划仍可用
        store = self.root / "linked_store"
        store.mkdir()
        (store / "keep.txt").write_bytes(b"precious")
        shutil.rmtree(self.root / "cache")
        (self.root / "cache").symlink_to(store)
        result = self.run_vtx("snapshot")
        self.assertEqual(result.returncode, 1)
        self.assertIn("拒绝快照", result.stdout)
        self.assertEqual((store / "keep.txt").read_bytes(), b"precious")
        self.assertFalse((store / "snapshots").exists())
        self.assertTrue((self.root / "cache").is_symlink())
        self.assert_read_only("snapshot", "--dry-run")


if __name__ == "__main__":
    unittest.main()
