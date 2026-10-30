"""Per-call ASR backup ownership: real adapters, parser and timeline publisher.

Only media/model transcription is substituted for actual ASR main. Load this file
by absolute path for both source and isolated installed-core consumers.
"""
import concurrent.futures
import contextlib
import copy
import errno
import io
import itertools
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import threading
import unittest
from unittest.mock import patch

from vtx.api import parse_write_json, read_timeline_for_write, timeline_transaction

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "apps/asr"))
import tl_merge
import asr_cli


def fixture(text="旧字😀"):
    return {"version": 2, "project": {"fps": 30, "width": 1920, "height": 1080,
            "duration_frames": 180}, "sources": [{"id": "source", "path": "unopened.wav"}],
            "video_tracks": [], "audio_tracks": [],
            "subtitles": [{"in": 90, "out": 110, "text": text,
                           "extra": {"typed": [True, 1, 9007199254740993], "half": 0.5}}],
            "unknown": {"原文": [False, 1, "保留😀"]}}


def subtitles(text):
    # Exercise the public strict parser, including Unicode, unknown fields/numbers.
    return parse_write_json(('[{"in":0,"out":24,"text":' + json.dumps(text) +
                             ',"ext":{"typed":[false,1],"n":9007199254740993,'
                             '"half":0.5,"decimal":0.1,"exp":1e-3}}]').encode())


def tree(root):
    return {str(p.relative_to(root)): (p.lstat().st_mode, p.lstat().st_ino,
            p.lstat().st_size, p.lstat().st_mtime_ns, p.lstat().st_ctime_ns,
            p.read_bytes() if p.is_file() else None)
            for p in [root, *sorted(root.rglob("*"))]}


class ASRExclusiveBackup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="asr-exclusive-backup-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "时间线😀.json"
        self.raw = self.save(self.path, fixture())

    @staticmethod
    def save(path, doc):
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = ("\n " + json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode()
        path.write_bytes(raw)
        return raw

    def before_files(self, path=None):
        return {json.loads(p.read_bytes())["op_id"]: (p.parent / "before.bin").read_bytes()
                for p in (path or self.root).rglob("intent.json")}

    def assert_backup(self, summary, expected):
        p = Path(summary["backup"])
        self.assertTrue(p.name.startswith("subtitles_backup_"))
        self.assertEqual(json.loads(p.read_bytes()), expected)
        return p, p.read_bytes()

    def test_two_helper_writes_same_pid_second_keep_both_and_before_bin(self):
        with patch.object(time, "strftime", return_value="20000102-030405"):
            a = tl_merge.merge_subtitles(self.path, subtitles("一😀"), op_id="first")
            first, first_bytes = self.assert_backup(a, fixture()["subtitles"])
            raw_second = self.path.read_bytes()
            old_second = read_timeline_for_write(self.path)["document"]["subtitles"]
            b = tl_merge.merge_subtitles(self.path, subtitles("二😀"), op_id="second")
        second, _ = self.assert_backup(b, old_second)
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), first_bytes)
        self.assertEqual(self.before_files(), {"first": self.raw, "second": raw_second})
        after = read_timeline_for_write(self.path)["document"]
        self.assertEqual(after["unknown"], fixture()["unknown"])
        self.assertEqual(after["subtitles"], subtitles("二😀"))

    def asr_main(self, *flags):
        config = self.root / "config.json"
        config.write_text('{"asr":{"enabled":true}}')
        output = io.StringIO()
        service = lambda *args: ({"text": "甲。"}, [{"w": "甲", "s": 0.0, "e": 0.8},
                                                     {"w": "。", "s": 0.8, "e": 0.8}])
        with patch.dict(os.environ, {"VTX_CONFIG": str(config)}), \
                patch.object(asr_cli, "_transcribe_words", service), \
                patch.object(sys, "argv", ["asr_cli.py", "timeline", "unopened.wav", "-t", str(self.path), *flags]), \
                contextlib.redirect_stdout(output):
            result = asr_cli.main()
        self.assertEqual(result, 0, output.getvalue())
        match = re.search(r"备份 (.+?)）", output.getvalue())
        self.assertIsNotNone(match, output.getvalue())
        return {"backup": match.group(1)}

    def main_pair(self):
        old = read_timeline_for_write(self.path)["document"]["subtitles"]
        raw_first = self.path.read_bytes()
        a = self.asr_main()
        first, first_bytes = self.assert_backup(a, old)
        old_second = read_timeline_for_write(self.path)["document"]["subtitles"]
        raw_second = self.path.read_bytes()
        b = self.asr_main("--append", "--offset-frames", "60")
        second, _ = self.assert_backup(b, old_second)
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), first_bytes)
        self.assertEqual(len(read_timeline_for_write(self.path)["document"]["subtitles"]), 2)
        self.assertEqual(sorted(self.before_files(self.path.parent).values()), sorted([raw_first, raw_second]))

    def test_actual_main_replace_append_frozen_second(self):
        with patch.object(time, "strftime", return_value="20000102-030405"):
            self.main_pair()

    def test_actual_main_natural_clock_loop(self):
        # No clock/PID/random/backup/publisher mock. Require one natural same-second pair.
        for attempt in range(8):
            self.path = self.root / str(attempt) / "timeline.json"
            self.save(self.path, fixture())
            started = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
            self.main_pair()
            if started == time.strftime("%Y%m%d-%H%M%S", time.gmtime()):
                return
        self.fail("No natural same-second pair in eight attempts")

    def test_exclusive_retry_regular_and_symlink_never_touch_foreign(self):
        rdir = self.root / "reports"
        rdir.mkdir()
        stamp = "20000102-030405-%d" % os.getpid()
        prefix = "subtitles_backup_" + stamp
        foreign = rdir / (prefix + "-collision.json")
        victim = self.root / "foreign.json"
        victim.write_bytes(b"foreign contents, not JSON\n")
        for symlink in (False, True):
            with self.subTest(symlink=symlink):
                if foreign.exists() or foreign.is_symlink():
                    # Only test-owned fixture; verified under our TemporaryDirectory.
                    foreign.unlink()
                if symlink:
                    foreign.symlink_to(victim)
                else:
                    foreign.write_bytes(b"foreign regular\n")
                # Guard the old deterministic name too: it must never be opened.
                legacy = rdir / (prefix + ".json")
                legacy.write_bytes(b"foreign legacy name\n")
                saved = [(p, p.lstat(), p.read_bytes()) for p in (foreign, victim, legacy)]
                opened = []
                real_open = os.open
                def observed(name, flags, *args, **kwargs):
                    if Path(name).name.startswith("subtitles_backup_"):
                        opened.append((str(name), flags))
                    return real_open(name, flags, *args, **kwargs)
                names = itertools.chain(["collision", "collision"],
                                        ("unique%d" % i for i in itertools.count()))
                with patch.object(time, "strftime", return_value="20000102-030405"), \
                        patch.object(tempfile, "_get_candidate_names", return_value=names), \
                        patch.object(os, "open", observed):
                    snap = read_timeline_for_write(self.path)
                    answer = tl_merge.merge_subtitles(self.path, subtitles(str(symlink)), reports_dir=rdir)
                self.assert_backup(answer, snap["document"]["subtitles"])
                self.assertGreaterEqual(len(opened), 3)
                self.assertTrue(all(flags & os.O_EXCL and flags & os.O_CREAT for _, flags in opened))
                for p, st, raw in saved:
                    self.assertEqual(p.lstat(), st)
                    self.assertEqual(p.read_bytes(), raw)

    def test_exhausted_random_source_fails_closed_without_foreign_mutation(self):
        rdir = self.root / "reports"
        rdir.mkdir()
        target = rdir / ("subtitles_backup_20000102-030405-%d-fixed.json" % os.getpid())
        target.write_bytes(b"foreign collision")
        before = tree(self.root)
        with patch.object(time, "strftime", return_value="20000102-030405"), \
                patch.object(tempfile, "_get_candidate_names", return_value=itertools.repeat("fixed")), \
                patch.object(tempfile, "TMP_MAX", 3):
            with self.assertRaises(FileExistsError):
                tl_merge.merge_subtitles(self.path, subtitles("change"), reports_dir=rdir)
        self.assertEqual(tree(self.root), before)

    def test_same_process_concurrent_calls_shared_directory(self):
        paths = [self.root / str(i) / "timeline.json" for i in range(4)]
        for i, p in enumerate(paths):
            self.save(p, fixture(str(i)))
        with patch.object(time, "strftime", return_value="20000102-030405"), \
                concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            answers = list(pool.map(lambda p: tl_merge.merge_subtitles(p, subtitles("new"),
                                      reports_dir=self.root / "shared"), paths))
        self.assertEqual(len({a["backup"] for a in answers}), len(paths))
        for i, answer in enumerate(answers):
            self.assert_backup(answer, fixture(str(i))["subtitles"])

    def test_same_timeline_concurrency_one_publish_two_independent_backups(self):
        snap = read_timeline_for_write(self.path)
        barrier = threading.Barrier(2)
        real_temp = tempfile.NamedTemporaryFile
        real_replace = os.replace
        publications = []
        def temp(*args, **kwargs):
            stream = real_temp(*args, **kwargs)
            close = stream.close
            def waiting_close():
                close()
                barrier.wait(timeout=10)
            stream.close = waiting_close
            return stream
        def replace(src, dst, *args, **kwargs):
            if Path(dst) == self.path:
                publications.append(str(dst))
            return real_replace(src, dst, *args, **kwargs)
        def run(i):
            try:
                return tl_merge.merge_subtitles(self.path, subtitles(str(i)), snapshot=snap,
                            expected_base=snap["revision"], op_id="same-%d" % i)
            except tl_merge.TimelineWriteError as error:
                self.assertEqual(error.receipt["code"], "conflict")
                return {"backup": error.backup, "failed": True}
        with patch.object(tempfile, "NamedTemporaryFile", temp), patch.object(os, "replace", replace), \
                concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            answers = list(pool.map(run, range(2)))
        self.assertEqual(sum(bool(a.get("failed")) for a in answers), 1)
        self.assertEqual(publications, [str(self.path)])
        self.assertEqual(len({a["backup"] for a in answers}), 2)
        for answer in answers:
            self.assert_backup(answer, fixture()["subtitles"])
        self.assertEqual(list(self.before_files().values()), [self.raw])

    def test_reports_directory_symlink_retains_existing_authority(self):
        actual = self.root / "actual"
        actual.mkdir()
        link = self.root / "reports-link"
        link.symlink_to(actual, target_is_directory=True)
        answer = tl_merge.merge_subtitles(self.path, subtitles("new"), reports_dir=link)
        backup, _ = self.assert_backup(answer, fixture()["subtitles"])
        self.assertEqual(backup.parent, link)
        self.assertEqual(backup.resolve().parent, actual)

    def test_parallel_processes_shared_directory(self):
        code = '''import json,sys
sys.path.append(sys.argv[1])
import tl_merge
print(json.dumps(tl_merge.merge_subtitles(sys.argv[2], [{"in":0,"out":24,"text":"new"}], reports_dir=sys.argv[3])))
'''
        children = []
        try:
            for i in range(3):
                p = self.root / str(i) / "timeline.json"
                self.save(p, fixture(str(i)))
                children.append(subprocess.Popen([sys.executable, "-I", "-B", "-c", code,
                                str(ROOT / "apps/asr"), str(p), str(self.root / "shared")],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE))
            answers = []
            for i, child in enumerate(children):
                out, err = child.communicate(timeout=30)
                self.assertEqual(child.returncode, 0, err)
                answer = json.loads(out)
                self.assert_backup(answer, fixture(str(i))["subtitles"])
                answers.append(answer)
            self.assertEqual(len({a["backup"] for a in answers}), 3)
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.communicate()

    def test_write_flush_close_and_double_failure_do_not_publish(self):
        real_temp = tempfile.NamedTemporaryFile
        for stage in ("create", "write", "flush", "close", "double"):
            with self.subTest(stage=stage):
                path = self.root / stage / "timeline.json"
                raw = self.save(path, fixture())
                primary = OSError(errno.ENOSPC, "own backup " + stage)
                secondary = OSError(errno.EIO, "own close secondary")
                files = []
                class Fault:
                    def __init__(self, stream):
                        self.stream = stream
                        self.name = stream.name
                    def write(self, payload):
                        if stage in ("write", "double"):
                            self.stream.write(payload[:4])
                            raise primary
                        return self.stream.write(payload)
                    def flush(self):
                        if stage == "flush":
                            raise primary
                        return self.stream.flush()
                    def close(self):
                        self.stream.close()
                        if stage == "double":
                            raise secondary
                        if stage == "close":
                            raise primary
                def faulty(*args, **kwargs):
                    if stage == "create":
                        raise primary
                    stream = real_temp(*args, **kwargs)
                    files.append(Path(stream.name))
                    return Fault(stream)
                with patch.object(tempfile, "NamedTemporaryFile", faulty):
                    with self.assertRaises(OSError) as caught:
                        tl_merge.merge_subtitles(path, subtitles("new"))
                self.assertIs(caught.exception, primary)
                if stage == "double":
                    self.assertIs(caught.exception.__cause__, secondary)
                self.assertEqual(path.read_bytes(), raw)
                self.assertFalse(list(path.parent.rglob("intent.json")))
                self.assertEqual(len(files), 0 if stage == "create" else 1)
                # Failed reports may remain; no successful backup was returned.
                self.assertTrue(all(p.exists() for p in files))

    def test_reports_directory_failure_dry_noop_replay_and_relative_spelling(self):
        blocked = self.root / "not-directory"
        blocked.write_bytes(b"foreign")
        before = tree(self.root)
        with self.assertRaises(OSError):
            tl_merge.merge_subtitles(self.path, subtitles("new"), reports_dir=blocked)
        self.assertEqual(tree(self.root), before)
        snap = read_timeline_for_write(self.path)
        for wanted, dry in [(snap["document"]["subtitles"], False), (subtitles("dry"), True)]:
            result = tl_merge.merge_subtitles(self.path, wanted, dry_run=dry, reports_dir=blocked)
            self.assertIsNone(result["backup"])
            self.assertEqual(tree(self.root), before)
        relative = os.path.relpath(self.root / "relative", Path.cwd())
        wanted = subtitles("real")
        result = tl_merge.merge_subtitles(self.path, wanted, reports_dir=relative,
                    snapshot=snap, expected_base=snap["revision"], op_id="one")
        self.assertTrue(result["backup"].startswith(relative + os.sep))
        self.assert_backup(result, snap["document"]["subtitles"])
        before = tree(self.root)
        replay = tl_merge.merge_subtitles(self.path, wanted, reports_dir=blocked,
                    snapshot=snap, expected_base=snap["revision"], op_id="one")
        self.assertIsNone(replay["backup"])
        self.assertEqual(tree(self.root), before)

    def test_after_backup_cas_failure_keeps_complete_backup_and_winner(self):
        snap = read_timeline_for_write(self.path)
        real_temp = tempfile.NamedTemporaryFile
        winner = []
        def foreign_after_backup(*args, **kwargs):
            stream = real_temp(*args, **kwargs)
            real_close = stream.close
            def close():
                real_close()
                result = timeline_transaction(self.path, tl_merge.prepare_subtitle_write(
                    snap, fixture("foreign winner")["subtitles"], "foreign"))
                self.assertTrue(result["ok"], result)
                winner.append(self.path.read_bytes())
            stream.close = close
            return stream
        with patch.object(tempfile, "NamedTemporaryFile", foreign_after_backup):
            with self.assertRaises(tl_merge.TimelineWriteError) as caught:
                tl_merge.merge_subtitles(self.path, subtitles("loser"), snapshot=snap,
                                        expected_base=snap["revision"], op_id="loser")
        self.assertEqual(caught.exception.receipt["code"], "conflict")
        self.assert_backup({"backup": caught.exception.backup}, snap["document"]["subtitles"])
        self.assertEqual(self.path.read_bytes(), winner[0])
        self.assertEqual(set(self.before_files()), {"foreign"})


if __name__ == "__main__":
    unittest.main()
