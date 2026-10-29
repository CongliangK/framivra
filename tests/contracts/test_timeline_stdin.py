"""Official request transport: actual OS pipes/socketpairs, no renderer or DSH dependency."""
import copy
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from vtx.api import (timeline_transaction, timeline_write_plan,
                     timeline_store_capabilities, timeline_store_status)
from vtx import timeline_store as store
from vtx.cli import timeline_store as adapter
from tests.contracts.test_timeline_store import document, sha
from tests.contracts.test_timeline_write import request as v2_request, tree

ROOT = Path(__file__).resolve().parents[2]
# Source tests explicitly inject only this checkout under isolated Python. Wheel
# consumers can use the installed -I -B -m vtx entry with these same contracts.
CLI = [sys.executable, "-I", "-B", "-c",
       "import runpy,sys;sys.path.insert(0," + repr(str(ROOT)) + ");"
       "runpy.run_module('vtx',run_name='__main__')"]
MAX_BYTES = 8388608


def wire(request):
    return json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf-8")


class TimelineStdinTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="framivra-stdin-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "时间线.json"
        self.path.write_bytes(wire(document()))

    def adopt(self, op_id="adopt"):
        return dict(operation="adopt", op_id=op_id,
                    base_revision=sha(self.path.read_bytes()))

    def args(self, *extra):
        return [*CLI, "-t", str(self.path), *extra]

    def result(self, proc, expected=0):
        self.assertEqual(proc.returncode, expected, (proc.stdout, proc.stderr))
        self.assertEqual(proc.stderr, b"")
        self.assertEqual(len(proc.stdout.splitlines()), 1, proc.stdout[:300])
        return json.loads(proc.stdout)

    def cli(self, payload, *extra, expected=0, transport="pipe"):
        args = self.args("store", *extra, "--request-stdin")
        if transport == "pipe":
            proc = subprocess.run(args, input=payload, cwd=self.root,
                                  capture_output=True, timeout=20)
        else:
            parent, child = socket.socketpair()
            with parent, child:
                process = subprocess.Popen(args, stdin=child, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, cwd=self.root)
                child.close()
                try:
                    parent.sendall(payload)
                    parent.shutdown(socket.SHUT_WR)
                    out, err = process.communicate(timeout=20)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()
                proc = subprocess.CompletedProcess(args, process.returncode, out, err)
        return self.result(proc, expected)

    def test_pipe_and_socket_v1_plan_commit_replay_share_receipt_digest(self):
        for transport in ("pipe", "socket"):
            with self.subTest(transport=transport):
                self.path = self.root / (transport + ".json")
                self.path.write_bytes(wire(document()))
                req = self.adopt(transport)
                # v1 plan is intentionally edit-only. Adopt first, then plan an
                # actual edit through both transports without initializing more state.
                result = self.cli(wire(req), transport=transport)
                self.assertEqual(result["schema"], "framivra.timeline.store/v1")
                self.assertEqual(result["revision"], sha(self.path.read_bytes()))
                self.assertEqual(timeline_transaction(self.path, req)["request_digest"],
                                 result["request_digest"])
                doc = json.loads(self.path.read_bytes())
                edit = dict(operation="edit", op_id="edit-" + transport,
                            base_revision=result["revision"], operations=[{
                                "op": "parameters.update", "id": doc["subtitles"][0]["id"],
                                "path": ["text"], "value": "新字幕😀"}])
                before = tree(self.root)
                plan = self.cli(wire(edit), "plan", transport=transport)
                self.assertTrue(plan["ok"], plan)
                self.assertEqual(plan, timeline_write_plan(self.path, edit))
                self.assertEqual(before, tree(self.root))
                commit = self.cli(wire(edit), transport=transport)
                replay = self.cli(wire(edit), transport=transport)
                self.assertTrue(replay["replayed"])
                self.assertEqual(replay["request_digest"], commit["request_digest"])
                self.assertEqual(replay["revision"], commit["revision"])
                self.assertEqual(json.loads(self.path.read_bytes())["subtitles"][0]["text"], "新字幕😀")
                before = tree(self.root)
                self.assertEqual(self.cli(wire(dict(edit, base_revision="changed")),
                                          transport=transport, expected=1)["code"], "idempotency.conflict")
                self.assertEqual(before, tree(self.root))

    def test_v2_plan_zero_write_commit_cas_history_and_later_replay(self):
        doc = document()
        doc["subtitles"][0]["text"] = "pipe → socket 😀"
        req = v2_request(doc, sha(self.path.read_bytes()), op_id="replace")
        before = tree(self.root)
        plan = self.cli(wire(req), "plan", transport="socket")
        self.assertEqual(plan, timeline_write_plan(self.path, req))
        self.assertEqual(before, tree(self.root))
        commit = self.cli(wire(req))
        self.assertEqual(commit["schema"], "framivra.timeline.store/v2")
        self.assertEqual(commit["revision"], plan["revision"])
        second_doc = copy.deepcopy(doc)
        second_doc["subtitles"][0]["text"] = "later"
        second_req = v2_request(second_doc, commit["revision"], op_id="later")
        later = self.cli(wire(second_req), transport="socket")
        before = tree(self.root)
        replay = self.cli(wire(req), transport="socket")
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["revision"], commit["revision"])
        self.assertEqual(replay["current_revision"], later["revision"])
        self.assertEqual(before, tree(self.root))
        stale = self.cli(wire(dict(second_req, op_id="stale")), expected=1)
        self.assertEqual(stale["code"], "conflict")
        self.assertEqual(before, tree(self.root))
        self.assertTrue(timeline_store_status(self.path)["ok"])

    def test_byte_limit_exact_boundary_and_plus_one_with_multibyte_utf8(self):
        req = self.adopt()
        # Boundary uses whitespace, avoiding a huge output or duplicate payload
        # allocation in the store while exercising the exact raw byte budget.
        raw = wire(req)
        for transport in ("pipe", "socket"):
            before = tree(self.root)
            too_big = raw + b" " * (MAX_BYTES + 1 - len(raw))
            result = self.cli(too_big, expected=1, transport=transport)
            self.assertEqual(result["code"], "input.request")
            self.assertIn("8388608", result["message"])
            self.assertEqual(before, tree(self.root))
            # A valid multibyte JSON request is checked by bytes, not characters.
            doc = document()
            doc["subtitles"][0]["text"] = "汉😀"
            v2 = v2_request(doc, sha(self.path.read_bytes()), op_id="boundary-" + transport)
            encoded = wire(v2)
            self.assertGreater(len(encoded), len(encoded.decode("utf-8")))
            at_limit = encoded + b" " * (MAX_BYTES - len(encoded))
            result = self.cli(at_limit, "plan", transport=transport)
            self.assertTrue(result["ok"], result)
            self.assertEqual(before, tree(self.root))

    def test_invalid_utf8_json_numbers_shapes_and_trailing_data_are_zero_write(self):
        payloads = [b"", b" \n", b"{", b'{"operation":', b"\xff", b'"\xe6\xb1"',
                    b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}',
                    b'{"x":-Infinity}', b'{"x":1e99999}',
                    b'{"x":0.12345678901234567890123456789}',
                    b'{"x":1e-99999999999999999999999999999}',
                    b"[]", b"null", b"true", b"123", b'"scalar"',
                    wire(self.adopt()) + b"\n{}"]
        before = tree(self.root)
        for transport in ("pipe", "socket"):
            for payload in payloads:
                with self.subTest(transport=transport, payload=payload[:70]):
                    result = self.cli(payload, expected=1, transport=transport)
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["code"], "input.request")
                    self.assertEqual(before, tree(self.root))

    def test_wrong_commands_conflicts_never_read_open_stdin_or_write(self):
        cases = [["store", name, "--request-stdin"]
                 for name in ("status", "recover", "capabilities", "bogus")]
        cases += [[name, "--request-stdin"] for name in
                  ("inspect", "query", "reference", "capabilities", "validate", "all", "video", "clean", "unknown")]
        cases += [["timeline", "frame", "--render", "--frame", "0", "--request-stdin"],
                  ["store", "--request", "nonexistent.json", "--request-stdin"],
                  ["store", "plan", "--request-stdin", "--dry-run"],
                  ["store", "--request-stdin", "--events-jsonl", str(self.root / "events.jsonl")]]
        before = tree(self.root)
        for args in cases:
            with self.subTest(args=args):
                proc = subprocess.Popen(self.args(*args), stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.root)
                try:
                    # Keep writer open and empty. Any attempted read would block;
                    # communicate() here would hide the bug by sending EOF.
                    proc.wait(timeout=5)
                    out, err = proc.communicate(timeout=5)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.communicate()
                self.assertEqual(proc.returncode, 2, (out, err))
                if args[0] in ("store", "inspect", "query", "reference", "capabilities", "timeline"):
                    result = json.loads(out)
                    self.assertFalse(result["ok"])
                    code = result.get("code") or result["diagnostics"][0]["code"]
                    self.assertEqual(code, "cli.arguments")
                    self.assertEqual(err, b"")
                else:
                    self.assertEqual(out, b"")
                    self.assertIn(b"--request-stdin", err)
                self.assertEqual(before, tree(self.root))

    def test_file_request_literal_dash_is_not_stdin(self):
        req = self.adopt()
        (self.root / "-").write_bytes(wire(req))
        before = tree(self.root)
        proc = subprocess.run(self.args("store", "--request", "-"), input=b"NOT JSON",
                              cwd=self.root, capture_output=True, timeout=20)
        committed = self.result(proc)
        self.assertTrue(committed["ok"])
        self.assertEqual(committed["request_digest"], timeline_transaction(self.path, req)["request_digest"])
        self.assertEqual((self.root / "-").read_bytes(), wire(req))
        self.assertNotEqual(before, tree(self.root))
        (self.root / "-").unlink()
        before = tree(self.root)
        proc = subprocess.run(self.args("store", "--request", "-"), input=wire(req),
                              cwd=self.root, capture_output=True, timeout=20)
        self.assertEqual(self.result(proc, 1)["code"], "input.request")
        self.assertEqual(before, tree(self.root))

    def exact_request(self, payload_file):
        return dict(protocol=2, operation="create", op_id="exact", base_revision=None,
                    identity_mode="preserve-unmanaged", payload_mode="exact-import",
                    purpose="frozen-copy", payload_file=str(payload_file))

    def test_exact_import_explicit_file_and_alias_restrictions_unchanged(self):
        payload = self.path
        raw = b" \n" + payload.read_bytes() + b"\n "
        payload.write_bytes(raw)
        self.path = self.root / "exact.json"
        req = self.exact_request(payload)
        before = tree(self.root)
        plan = self.cli(wire(req), "plan", transport="socket")
        self.assertEqual(before, tree(self.root))
        result = self.cli(wire(req), transport="socket")
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(result["revision"], sha(raw))
        self.assertEqual(result["request_digest"], plan["request_digest"])
        self.assertTrue(self.cli(wire(req))["replayed"])
        payload.write_bytes(raw + b" ")
        self.assertEqual(self.cli(wire(req), expected=1)["code"], "idempotency.conflict")
        symlink = self.root / "alias.json"
        symlink.symlink_to(payload)
        hardlink = self.root / "hardlink.json"
        os.link(payload, hardlink)
        for source in (symlink, hardlink, self.root, "https://example.invalid/payload", "/dev/stdin"):
            before = tree(self.root)
            result = self.cli(wire(self.exact_request(source)), expected=1)
            self.assertFalse(result["ok"])
            self.assertEqual(before, tree(self.root))
        for extra in ({"raw_bytes": "escape"}, {"raw_utf8": raw.decode()}, {"payload_file": ""}):
            before = tree(self.root)
            result = self.cli(wire(dict(req, **extra)), expected=1)
            self.assertEqual(result["code"], "input.request")
            self.assertEqual(before, tree(self.root))
        without_file = dict(req)
        without_file.pop("payload_file")
        self.assertEqual(self.cli(wire(without_file), expected=1)["code"], "input.request")

    def test_exact_file_and_request_freeze_once_before_publisher(self):
        source = self.path
        raw = source.read_bytes()
        self.path = self.root / "frozen.json"
        req = self.exact_request(source)
        read_fd, write_fd = os.pipe()
        os.write(write_fd, wire(req))
        os.close(write_fd)
        original_read = store._read
        source_reads = []
        def read(path, *args, **kwargs):
            if path == source:
                source_reads.append(path)
            return original_read(path, *args, **kwargs)
        def publish(path, frozen):
            self.assertEqual(frozen["raw_bytes"], raw)
            self.assertNotIn("payload_file", frozen)
            source.write_bytes(b"changed after freeze")
            return timeline_transaction(path, frozen)
        from vtx.cli.commands import _main
        with os.fdopen(read_fd, "r", encoding="utf-8") as stdin, \
             patch.object(sys, "stdin", stdin), patch.object(sys, "stdout", new_callable=io.StringIO) as stdout, \
             patch.object(sys, "argv", ["vtx", "-t", str(self.path), "store", "--request-stdin"]), \
             patch.object(store, "_read", side_effect=read), \
             patch.object(adapter, "strict_loads", wraps=adapter.strict_loads) as parse, \
             patch.object(adapter, "timeline_transaction", side_effect=publish), \
             patch("vtx.pipeline.infra.Ctx", side_effect=AssertionError("no Ctx")):
            self.assertEqual(_main(), 0)
            result = json.loads(stdout.getvalue())
        self.assertTrue(result["ok"])
        self.assertEqual(parse.call_count, 1)
        self.assertEqual(source_reads, [source])
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(source.read_bytes(), b"changed after freeze")

    def test_eof_required_premature_eof_and_cancellation_leave_no_writes(self):
        for cancellation in (None, signal.SIGINT, signal.SIGTERM):
            parent, child = socket.socketpair()
            with parent, child:
                proc = subprocess.Popen(self.args("store", "--request-stdin"), stdin=child,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.root)
                child.close()
                before = tree(self.root)
                try:
                    parent.sendall(wire(self.adopt()) if cancellation else b'{"operation":')
                    # A full object is not permission to transact before EOF.
                    with self.assertRaises(subprocess.TimeoutExpired):
                        proc.wait(timeout=0.3)
                    self.assertEqual(before, tree(self.root))
                    if cancellation:
                        proc.send_signal(cancellation)
                    else:
                        parent.shutdown(socket.SHUT_WR)
                    out, err = proc.communicate(timeout=10)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.communicate()
                if cancellation is None:
                    self.assertEqual(proc.returncode, 1, err)
                    self.assertEqual(json.loads(out)["code"], "input.request")
                    self.assertEqual(err, b"")
                else:
                    self.assertNotEqual(proc.returncode, 0)
                    self.assertEqual(out, b"")
                self.assertEqual(before, tree(self.root))
                # Reaped child and its closed stdin cannot retain a writer/lock.
                self.assertIsNotNone(proc.poll())

    def test_capability_transport_is_cli_only_additive_and_zero_write(self):
        before = tree(self.root)
        api = timeline_store_capabilities()
        self.assertNotIn("request_transport", api)
        proc = subprocess.run(self.args("store", "capabilities"), input=b"unused",
                              cwd=self.root, capture_output=True, timeout=20)
        cli = self.result(proc)
        self.assertEqual(cli.pop("request_transport"), {
            "stdin": {"flag": "--request-stdin", "max_bytes": MAX_BYTES}})
        self.assertEqual(cli, api)
        self.assertEqual(timeline_store_capabilities(), api)
        self.assertEqual(before, tree(self.root))


if __name__ == "__main__":
    unittest.main()
