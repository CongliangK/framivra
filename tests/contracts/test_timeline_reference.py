"""Lossless references via real files, public Python and public CLI; no rendering."""
import copy
import hashlib
import json
import os
import random
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from vtx.api import reference_timeline, inspect_timeline, timeline_transaction
from vtx import timeline

ROOT = Path(__file__).resolve().parents[2]
LIMIT = 8 * 1024 * 1024


def document():
    return {"version": 2, "project": {"fps": 30, "width": 1920, "height": 1080, "duration_frames": 60},
            "sources": [{"id": "source", "path": "missing.mp4"}],
            "video_tracks": [{"name": "picture", "kind": "aroll", "clips": [
                {"src": "source", "src_in": 4, "src_out": 14, "dst_in": 0, "dur": 10},
                {"src": "source", "src_in": 14, "src_out": 24, "dst_in": 10, "dur": 10}]}],
            "audio_tracks": [{"name": "voice", "events": [
                {"src": "source", "in": 0, "out": 30, "loop": True, "volume": 1.0}]}],
            "subtitles": [{"in": 10, "out": 20, "text": "字幕"}],
            "plugins": {"not_installed": {"never_execute": True}}}


class TimelineReferenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="framivra-reference-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "时间线.json"
        self.doc = document()
        self.store()

    def store(self):
        self.path.write_text(json.dumps(self.doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def version(self):
        return "sha256:" + hashlib.sha256(self.path.read_bytes()).hexdigest()

    def ref(self, **kwargs):
        return reference_timeline(self.path, **kwargs)

    def error(self, code, **kwargs):
        result = self.ref(**kwargs)
        self.assertFalse(result["ok"], result)
        self.assertIn(code, [d["code"] for d in result["diagnostics"]], result)
        self.assertIsNone(result["selection"])
        return result

    def cli(self, *args):
        proc = subprocess.run([sys.executable, "-m", "vtx", "-t", str(self.path), "reference", *args],
                              cwd=self.root, env=dict(os.environ, PYTHONPATH=str(ROOT),
                                                     PYTHONDONTWRITEBYTECODE="1"),
                              capture_output=True, text=True)
        self.assertEqual(proc.stderr, "")
        return proc.returncode, json.loads(proc.stdout)

    def test_document_raw_authority_unknown_numbers_escapes_and_block_spans(self):
        block = ('{ "src":"source", "src_in":4,"src_out":14,"dst_in":0,"dur":10, '
                 '"unknown":{"huge":900719925474099312345,"decimal":0.123456789012345678901234567890,'
                 '"nested":[{"a/b~c":"\\u4e2d\\ud83d\\ude00\\\\\\\""},true,null]} }')
        original = json.dumps(self.doc, ensure_ascii=False)
        old_block = json.dumps(self.doc["video_tracks"][0]["clips"][0], ensure_ascii=False)
        text = " \n" + original.replace(old_block, block) + "\t\n"
        self.path.write_bytes(text.encode("utf-8"))
        before = self.path.read_bytes()
        result = self.ref(global_frame=0)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["schema"], "framivra.timeline.reference/v1")
        self.assertEqual(result["document"]["raw"].encode("utf-8"), before)
        self.assertEqual(result["document"]["content_version"], self.version())
        selected = result["selection"]["blocks"][0]
        self.assertEqual(selected["raw"], block)
        self.assertEqual(selected["source_id"], "source")
        self.assertNotIn("source_frame", selected)  # native source clock is unprobed
        self.assertIsNone(selected["stable_id"])
        self.assertEqual(result["image"], {"state": "not-generated", "kind": None})
        self.assertEqual(result["document"]["project"], {"kind": "legacy", "project_id": None, "head": None})
        code, wire = self.cli("--frame", "0")
        self.assertEqual(code, 0)
        self.assertEqual(wire, result)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_empty_selection_references_document_only_and_legacy_has_no_stable_ids(self):
        self.doc["video_tracks"][0]["id"] = "legacy-not-protocol"
        self.store()
        result = self.ref()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["selection"]["blocks"], [])
        self.assertEqual(result["selection"]["tracks"], [])
        self.error("reference.unmanaged", track_ids=["legacy-not-protocol"])
        result = self.ref(track_addresses=["/video_tracks/0"], expected_content_version=self.version())
        self.assertTrue(result["ok"], result)
        self.assertTrue(all(t["stable_id"] is None for t in result["selection"]["tracks"]))
        self.assertEqual(len(result["selection"]["blocks"]), 2)

    def test_managed_real_store_ids_multiple_selectors_and_subtitle_virtual_track(self):
        adopted = timeline_transaction(self.path, {"operation": "adopt", "op_id": "adopt",
                                                  "base_revision": self.version()})
        self.assertTrue(adopted["ok"], adopted)
        doc = json.loads(self.path.read_bytes())
        vt, at = doc["video_tracks"][0], doc["audio_tracks"][0]
        ids = [vt["clips"][1]["id"], doc["subtitles"][0]["id"]]
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result = self.ref(track_ids=[at["id"]], block_ids=ids, global_frame=10)
        self.assertTrue(result["ok"], result)
        self.assertEqual([b["stable_id"] for b in result["selection"]["blocks"]],
                         [ids[0], at["events"][0]["id"], ids[1]])
        self.assertIsNone(result["selection"]["tracks"][-1]["stable_id"])
        self.assertEqual(result["document"]["project"], dict(kind="managed", project_id=doc["_vtx"]["project_id"], head="adopt"))
        code, actual = self.cli("--track-id", at["id"], "--block-id", ids[0], "--block-id", ids[1], "--frame", "10")
        self.assertEqual(code, 0)
        self.assertEqual(actual, result)
        after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(after, before)
        self.error("reference.not_found", block_ids=[vt["id"]])
        self.error("reference.conflict", block_ids=[vt["clips"][0]["id"]], global_frame=10)
        self.error("reference.duplicate", block_ids=[ids[0], ids[0]])
        self.error("reference.duplicate", block_ids=[ids[0]], block_addresses=["/video_tracks/0/clips/1"],
                   expected_content_version=self.version())

    def test_addresses_require_exact_version_stale_never_selects_reordered_block(self):
        version = self.version()
        args = {"block_addresses": ["/video_tracks/0/clips/0"]}
        self.error("reference.version_required", **args)
        result = self.ref(**args, expected_content_version=version)
        self.assertTrue(result["ok"], result)
        self.doc["video_tracks"][0]["clips"].reverse()
        self.store()
        self.error("reference.stale", **args, expected_content_version=version)
        self.error("reference.not_found", block_addresses=["/video_tracks/0/clips/20"], expected_content_version=self.version())
        self.error("reference.not_found", track_addresses=["/sources/0"], expected_content_version=self.version())

    def test_windows_boundaries_fps_and_invalid_selectors(self):
        self.assertEqual(self.ref(global_frame=9)["selection"]["blocks"][0]["range"], [0, 10])
        self.assertEqual(self.ref(global_frame=10)["selection"]["blocks"][0]["range"], [10, 20])
        self.assertEqual(self.ref(global_frame=59)["selection"]["blocks"], [])
        self.assertEqual(len(self.ref(interval=[9, 10])["selection"]["blocks"]), 2)
        for frame in (-1, 60):
            self.error("reference.bounds", global_frame=frame)
        for frame in (True, 1.0, "1", {}):
            self.error("reference.integer", global_frame=frame)
        for interval in ([0, 61], [1, 1], [-1, 1]):
            self.error("reference.bounds", interval=interval)
        for interval in ([0, True], [0, 1.0], [], {}, "01"):
            self.error("reference.interval", interval=interval)
        self.error("reference.conflict", global_frame=1, interval=[0, 2])
        self.error("reference.selector", track_ids="not-a-list")
        self.error("reference.selector", block_addresses=[{}])
        self.doc["project"]["fps"] = 30.0
        self.store()
        self.assertEqual(self.ref()["selection"]["fps"], 30.0)
        for fps in (24, 29.97, True):
            self.doc["project"]["fps"] = fps
            self.store()
            self.error("timeline.fps")

    def test_shared_strict_metadata_global_identity_duplicate_json_and_ambiguity(self):
        for meta in (None, {}, {"protocol": True, "project_id": "p", "head": "h"},
                     {"protocol": 2, "project_id": "p", "head": "h"},
                     {"protocol": 1, "project_id": "p", "head": "h", "extra": 1}):
            self.doc["_vtx"] = meta
            self.store()
            self.error("reference.identity")
        self.doc.pop("_vtx")
        self.doc["sources"].append(copy.deepcopy(self.doc["sources"][0]))
        self.store()
        self.error("reference.ambiguous")
        self.doc = document()
        self.doc["video_tracks"].append(copy.deepcopy(self.doc["video_tracks"][0]))
        self.store()
        self.error("reference.ambiguous")
        self.doc = document()
        self.doc["video_tracks"][0]["clips"][0]["src"] = "unknown"
        self.store()
        self.error("reference.source")
        for raw in (b'{"x":{"a":1,"a":2}}', b'{"x":NaN}', b'\xff', b'{'):
            self.path.write_bytes(raw)
            self.error("input.json")
        self.doc = document()
        self.store()
        adopted = timeline_transaction(self.path, {"operation": "adopt", "op_id": "adopt", "base_revision": self.version()})
        self.assertTrue(adopted["ok"], adopted)
        self.doc = json.loads(self.path.read_bytes())
        self.doc["subtitles"][0]["id"] = self.doc["sources"][0]["id"]
        self.store()
        self.error("reference.identity")

    def test_external_subtitles_coverage_and_conflicting_inline(self):
        self.doc.pop("subtitles")
        self.doc["subtitles_src"] = "missing.srt"
        self.store()
        result = self.ref(global_frame=10)
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["coverage"]["complete"])
        self.assertEqual(result["coverage"]["unexpanded"][0]["reference"], "missing.srt")
        self.assertFalse(any(b["family"] == "subtitles" for b in result["selection"]["blocks"]))
        virtual = self.ref(track_addresses=["/subtitles"], expected_content_version=self.version())
        self.assertTrue(virtual["ok"], virtual)
        self.assertIsNone(virtual["selection"]["tracks"][0]["raw"])
        self.assertIsNone(virtual["selection"]["tracks"][0]["stable_id"])
        self.doc["subtitles"] = [{"in": 0, "out": 1, "text": "conflict"}]
        self.store()
        self.error("timeline.subtitles_conflict")
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_fps_rounding_is_not_reported_as_exact_and_legacy_unicode_sources_stay_legacy(self):
        self.doc["sources"][0]["id"] = "源🎬"
        for track in self.doc["video_tracks"]:
            for block in track["clips"]:
                block["src"] = "源🎬"
        self.doc["audio_tracks"][0]["events"][0]["src"] = "源🎬"
        self.store()
        result = self.ref(global_frame=0)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["selection"]["blocks"][0]["source_id"], "源🎬")
        text = self.path.read_text(encoding="utf-8").replace('"fps": 30', '"fps": 30.000000000000000000001')
        self.path.write_text(text, encoding="utf-8")
        self.error("reference.precision")

    def test_input_output_limits_and_safe_derived_integers_never_truncate(self):
        self.path.write_bytes(b" " * (LIMIT + 1))
        self.error("input.limit")
        self.doc["unknown"] = "中" * (LIMIT // 4)
        self.store()
        self.assertLess(self.path.stat().st_size, LIMIT)
        result = self.error("output.limit")
        self.assertIsNone(result["document"])
        self.assertLess(len(json.dumps(result)), LIMIT)
        code, actual = self.cli()
        self.assertEqual(code, 1)
        self.assertEqual(actual, result)
        self.doc = document()
        self.doc["project"]["duration_frames"] = 2 ** 53
        self.store()
        self.error("reference.precision")
        self.doc = document()
        self.doc["video_tracks"][0]["clips"][0]["src_in"] = 2 ** 53
        self.store()
        self.error("reference.precision", global_frame=0)

    def test_wire_counter_matches_real_encoder_and_saturates(self):
        from vtx.timeline_reference import _wire_size, _RawSpan
        rng = random.Random(1701)
        chars = 'abc"\\\\\b\f\n\r\t\x00\x1f\x7f中🎬\ud800'
        scalars = [None, True, False, 0, -1, 2 ** 53 - 1, 30.0, -0.0, 1e-20]
        for index in range(200):
            text = "".join(rng.choice(chars) for _ in range(index))
            value = {text: [text, {}, [], scalars, {"nested": text}]}
            expected = len(json.dumps(value, ensure_ascii=True, allow_nan=False))
            for budget in (0, expected - 1, expected, expected + 1):
                self.assertEqual(_wire_size(value, budget), min(expected, budget + 1))
            span = _RawSpan("prefix" + text + "suffix", 6, 6 + len(text))
            self.assertEqual(_wire_size(span, LIMIT), len(json.dumps(text)))
        # Iterative counting also handles malformed deeply nested coverage.
        deep = None
        for _ in range(1100):
            deep = [deep]
        self.assertEqual(_wire_size(deep, LIMIT), 2204)

    def test_exact_wire_limit_with_newline_whole_and_selected_raw(self):
        for selected in (False, True):
            with self.subTest(selected=selected):
                self.doc = document()
                target = self.doc["video_tracks"][0]["clips"][0] if selected else self.doc
                target["padding"] = ""
                self.store()
                kwargs = {"global_frame": 0} if selected else {}
                initial = self.ref(**kwargs)
                self.assertTrue(initial["ok"])
                size = len(json.dumps(initial, ensure_ascii=True).encode("ascii")) + 1
                count, remainder = divmod(LIMIT - size, 3 if selected else 1)
                target["padding"] = "x" * count
                self.store()
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(" " * remainder)
                result = self.ref(**kwargs)
                self.assertTrue(result["ok"])
                self.assertEqual(len(json.dumps(result).encode("ascii")) + 1, LIMIT)
                code, actual = self.cli(*(("--frame", "0") if selected else ()))
                self.assertEqual(code, 0)
                self.assertEqual(actual, result)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(" ")
                result = self.error("output.limit", **kwargs)
                self.assertIsNone(result["document"])
                self.assertIsNone(result["coverage"])

    def test_wire_overflow_never_dumps_or_materializes_raw_selections(self):
        import vtx.timeline_reference as tr
        original = json.dumps
        def scalar_only(value, *args, **kwargs):
            self.assertNotIsInstance(value, (str, dict, list, tuple))
            return original(value, *args, **kwargs)
        for payload in ("🎬" * 1_800_000, "x" * 3_000_000):
            self.doc = document()
            self.doc["video_tracks"][0]["clips"][0]["extension"] = payload
            self.store()
            self.assertLess(self.path.stat().st_size, LIMIT)
            with patch.object(tr.json, "dumps", side_effect=scalar_only), patch.object(
                    tr._RawSpan, "materialize", side_effect=AssertionError("premature raw copy")):
                result = self.error("output.limit", global_frame=0)
            self.assertEqual([d["code"] for d in result["diagnostics"]], ["output.limit"])
            self.assertIsNone(result["document"])
            self.assertIsNone(result["coverage"])

    @unittest.skipUnless(sys.platform == "linux", "child RLIMIT_AS probe requires Linux")
    def test_real_cli_wire_overflow_under_child_only_memory_limit(self):
        import resource
        self.doc["video_tracks"][0]["clips"][0]["extension"] = "🎬" * 1_800_000
        self.store()
        before = resource.getrlimit(resource.RLIMIT_AS)
        def child_limit():
            resource.setrlimit(resource.RLIMIT_AS, (72 * 1024 * 1024,) * 2)
        for flags in ([], ["--frame", "0"]):
            proc = subprocess.run([sys.executable, "-m", "vtx", "-t", str(self.path),
                                   "reference", *flags], cwd=self.root,
                                  env=dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1"),
                                  preexec_fn=child_limit, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertEqual(proc.stderr, "")
            result = json.loads(proc.stdout)
            self.assertEqual([d["code"] for d in result["diagnostics"]], ["output.limit"])
            self.assertIsNone(result["document"])
            self.assertIsNone(result["selection"])
            self.assertIsNone(result["coverage"])
        self.assertEqual(resource.getrlimit(resource.RLIMIT_AS), before)

    def test_raw_span_index_is_bounded_and_unknown_large_integers_stay_lexical(self):
        import tracemalloc
        from vtx.timeline_reference import _spans
        self.doc["video_tracks"][0]["clips"][0]["extension"] = "🎬" * 1_800_000
        text = json.dumps(self.doc, ensure_ascii=False)
        tracemalloc.start()
        try:
            spans = _spans(text, self.doc, {"/video_tracks/0", "/video_tracks/0/clips/0"})
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 128 * 1024)
        self.assertEqual(len(spans), 2)
        self.assertGreater(len(spans["/video_tracks/0/clips/0"]), 1_800_000)
        self.doc = document()
        text = json.dumps(self.doc)
        literal = "9" * 100_000
        text = text.replace('"src_out": 14', '"src_out": 14, "unknown": ' + literal, 1)
        self.path.write_text(text, encoding="utf-8")
        result = self.ref(global_frame=0)
        self.assertTrue(result["ok"], result["diagnostics"])
        self.assertIn('"unknown": ' + literal, result["selection"]["blocks"][0]["raw"])
        # Opaque raw integer lexemes must not become valid string shape fields.
        for token in ('"source"', '"picture"', '"aroll"'):
            self.path.write_text(text.replace(token, literal, 1), encoding="utf-8")
            self.error("shape.type")
        self.path.write_text(text.replace('"src_out": 14', '"src_out": ' + literal, 1), encoding="utf-8")
        self.error("reference.precision")

    def numeric_fixture(self, path, literal, *, managed=False):
        from vtx.timeline_structure import managed_objects
        self.doc = document()
        if managed:
            self.doc["_vtx"] = {"protocol": 1, "project_id": "project", "head": "head"}
            for index, (_, obj, _) in enumerate(managed_objects(self.doc)):
                obj.setdefault("id", "object-" + str(index))
        target = self.doc
        for index, part in enumerate(path[:-1]):
            if isinstance(target, list):
                target = target[part]
            else:
                target = target.setdefault(part, [] if isinstance(path[index + 1], int) else {})
        if isinstance(target, list) and path[-1] == len(target):
            target.append("NUMERIC_TOKEN")
        else:
            target[path[-1]] = "NUMERIC_TOKEN"
        text = json.dumps(self.doc).replace('"NUMERIC_TOKEN"', literal)
        self.path.write_text(text, encoding="utf-8")
        return text

    def test_raw_numeric_tokens_are_never_strings_even_for_extensions(self):
        from vtx.timeline_structure import _schema_errors, valid_id
        for literal in ("0", "-0", "9" * 64, "9" * 65, "9" * 128,
                        "9" * 129, "-" + "9" * 128, "9" * 1_000_000):
            with self.subTest(digits=len(literal)):
                token = timeline._raw_integer(literal)
                self.assertNotIsInstance(token, str)
                self.assertFalse(valid_id(token))
                self.assertEqual(_schema_errors(token, {"type": "string"}), ["/: expected string"])
                if len(literal) > 64:
                    self.assertIsInstance(token, timeline._RawInteger)
                    self.assertFalse(hasattr(token, "__dict__"))
                    self.assertEqual(token.negative, literal.startswith("-"))
        # A giant token must not reach int(), nor retain its giant lexeme.
        with patch.object(timeline, "int", side_effect=AssertionError("giant int conversion"), create=True):
            token = timeline._raw_integer("9" * 1_000_000)
        self.assertLess(sys.getsizeof(token), 128)

    def test_red_team_four_numeric_identity_variants_and_all_managed_ids(self):
        from vtx.api import query_timeline
        from vtx.timeline_structure import identity_errors
        paths = [("_vtx", "project_id"), ("_vtx", "head"),
                 ("video_tracks", 0, "id"), ("video_tracks", 0, "clips", 0, "id"),
                 ("audio_tracks", 0, "id"), ("audio_tracks", 0, "events", 0, "id"),
                 ("subtitles", 0, "id"), ("sources", 0, "id")]
        for path in paths:
            for literal in ("0", "-1", "9" * 64, "9" * 65, "9" * 70,
                            "9" * 128, "9" * 129, "-" + "9" * 65):
                with self.subTest(path=path, digits=len(literal)):
                    self.numeric_fixture(path, literal, managed=True)
                    raw = timeline._load(self.path, "reference", raw=True)
                    normal = inspect_timeline(self.path)
                    self.assertEqual(identity_errors(raw["document"]), identity_errors(normal["document"]))
                    self.assertTrue(identity_errors(raw["document"]))
                    result = self.error("shape.type" if path[0] == "sources" else "reference.identity")
                    self.assertEqual(result["content_version"], self.version())
                    for read in (normal, query_timeline(self.path)):
                        # Read/query check shape, not protocol identity; their
                        # existing scope must not be widened to hide this bug.
                        self.assertEqual(raw["diagnostics"], read["diagnostics"])
                        self.assertEqual(result["content_version"], read["content_version"])
                    json.dumps(result, allow_nan=False)  # opaque token never leaks
        self.numeric_fixture(("video_tracks", 0, "id"), "9" * 70, managed=True)
        code, wire = self.cli()
        self.assertEqual(code, 1)
        self.assertEqual(wire, self.ref())

    def test_numeric_string_ids_keep_shared_grammar_and_selection(self):
        from vtx.timeline_structure import identity_errors
        paths = [("_vtx", "project_id"), ("_vtx", "head"), ("sources", 0, "id"),
                 ("video_tracks", 0, "id"), ("video_tracks", 0, "clips", 0, "id"),
                 ("audio_tracks", 0, "id"), ("audio_tracks", 0, "events", 0, "id"),
                 ("subtitles", 0, "id")]
        for path in paths:
            for digits in (1, 64, 65, 128, 129):
                with self.subTest(path=path, digits=digits):
                    value = "9" * digits
                    self.numeric_fixture(path, json.dumps(value), managed=True)
                    loaded = timeline._load(self.path, "reference", raw=True)
                    self.assertEqual(bool(identity_errors(loaded["document"])), digits > 128)
                    # Source references must track a changed source ID.
                    if path == ("sources", 0, "id"):
                        self.doc["sources"][0]["id"] = value
                        self.doc["video_tracks"][0]["clips"][0]["src"] = value
                        self.doc["video_tracks"][0]["clips"][1]["src"] = value
                        self.doc["audio_tracks"][0]["events"][0]["src"] = value
                        self.store()
                    result = self.ref(global_frame=10)
                    self.assertEqual(result["ok"], digits <= 128, result["diagnostics"])
                    if digits <= 128 and path == ("video_tracks", 0, "id"):
                        selected = self.ref(track_ids=[value])
                        self.assertTrue(selected["ok"], selected["diagnostics"])
                        self.assertEqual(selected["selection"]["tracks"][0]["stable_id"], value)

    def test_numeric_known_string_surfaces_match_shared_schema_and_read_shape(self):
        from vtx.api import query_timeline
        from vtx.timeline_structure import _schema_errors, structure_errors
        schema = json.loads((ROOT / "vtx" / "timeline.schema.json").read_text(encoding="utf-8"))
        # All declared string/enum-string fields, plus the duck-strategy voice
        # reference checked by structure_errors rather than the JSON schema.
        paths = [("project", "gfx_dependency_policy"), ("project", "cache_dependencies", 0),
                 ("sources", 0, "path"), ("sources", 0, "transform"),
                 ("video_tracks", 0, "name"), ("video_tracks", 0, "kind"),
                 ("video_tracks", 0, "clips", 0, "src"),
                 ("video_tracks", 0, "clips", 0, "format"),
                 ("video_tracks", 0, "clips", 0, "cache_dependencies", 0),
                 ("audio_tracks", 0, "name"), ("audio_tracks", 0, "events", 0, "src"),
                 ("audio_tracks", 0, "duck_by", 0), ("audio_tracks", 0, "duck_strategy", "voice_track"),
                 ("subtitles", 0, "text"), ("subtitles_src",)]
        for path in paths:
            address = "/" + "/".join(map(str, path))
            for literal in ("0", "-0", "9" * 64, "9" * 65, "9" * 128,
                            "9" * 129, "-" + "9" * 128):
                with self.subTest(path=path, digits=len(literal)):
                    self.numeric_fixture(path, literal)
                    raw = timeline._load(self.path, "reference", raw=True)
                    normal = inspect_timeline(self.path)
                    query = query_timeline(self.path)
                    self.assertEqual(raw["diagnostics"], normal["diagnostics"])
                    self.assertEqual(raw["diagnostics"], query["diagnostics"])
                    self.assertEqual(raw["content_version"], normal["content_version"])
                    self.assertEqual(raw["content_version"], query["content_version"])
                    self.assertEqual(_schema_errors(raw["document"], schema),
                                     _schema_errors(normal["document"], schema))
                    errors = structure_errors(raw["document"])
                    self.assertTrue(any(address in error or "invalid voice reference" in error for error in errors), errors)
                    result = self.ref(global_frame=10)
                    if raw["ok"]:
                        # Optional render-only fields stay outside reference's
                        # query-shape scope, but never become string substitutes.
                        self.assertTrue(result["ok"], result["diagnostics"])
                    else:
                        self.assertFalse(result["ok"])
                        self.assertEqual(result["diagnostics"], raw["diagnostics"])
                        self.assertIsNone(result["coverage"])
                    json.dumps(result, allow_nan=False)

    def test_invalid_known_numeric_never_leaks_in_error_envelopes(self):
        identities = [("_vtx", "project_id"), ("_vtx", "head"),
                      ("video_tracks", 0, "id"), ("video_tracks", 0, "clips", 0, "id"),
                      ("audio_tracks", 0, "id"), ("audio_tracks", 0, "events", 0, "id"),
                      ("subtitles", 0, "id"), ("sources", 0, "id")]
        paths = identities + [("project", "fps"), ("project", "duration_frames"),
                              ("video_tracks", 0, "clips", 0, "dur"), ("subtitles_src",),
                              ("audio_tracks", 0, "name"), ("audio_tracks", 0, "events", 0, "src"),
                              ("subtitles", 0, "text")]
        for path in paths:
            for literal in ("9" * 65, "-" + "9" * 129, "9" * 10_000,
                            "9" * 1_000_000, "-" + "9" * 1_000_000):
                with self.subTest(path=path, digits=len(literal)):
                    self.numeric_fixture(path, literal, managed=path in identities)
                    result = self.ref(global_frame=0)
                    if path in identities:
                        expected = "shape.type" if path[0] == "sources" else "reference.identity"
                        self.assertIn(expected, [d["code"] for d in result["diagnostics"]])
                    self.assertFalse(result["ok"])
                    self.assertIsNone(result["document"])
                    self.assertIsNone(result["selection"])
                    if path not in identities or path[0] == "sources":
                        self.assertIsNone(result["coverage"])
                    wire = json.dumps(result, allow_nan=False)
                    self.assertNotIn(literal, wire)
                    self.assertEqual(result["content_version"], self.version())
        code, result = self.cli()
        self.assertEqual(code, 1)
        self.assertEqual(result, self.ref())

    def test_legacy_unknown_million_digit_integer_real_cli_is_lossless(self):
        for literal in ("9" * 1_000_000, "-" + "9" * 1_000_000):
            text = self.numeric_fixture(("video_tracks", 0, "clips", 0, "extension"), literal)
            result = self.ref(global_frame=0)
            self.assertTrue(result["ok"], result["diagnostics"])
            self.assertEqual(result["document"]["raw"], text)
            self.assertIn('"extension": ' + literal, result["selection"]["blocks"][0]["raw"])
            self.assertEqual(result["content_version"], self.version())
            code, actual = self.cli("--frame", "0")
            self.assertEqual(code, 0)
            self.assertEqual(actual, result)

    def test_raw_decode_chunk_boundaries_preserve_bytes_and_strict_diagnostics(self):
        from vtx.api import query_timeline
        # Each multibyte sequence starts just before a decoder chunk boundary.
        for character in ("中", "🎬"):
            for split in range(1, len(character.encode("utf-8"))):
                self.doc = document()
                self.doc["padding"] = "PREFIX"
                raw = json.dumps(self.doc, ensure_ascii=False).encode("utf-8")
                prefix, suffix = raw.split(b"PREFIX")
                payload = prefix + b"x" * (65536 - split - len(prefix)) + character.encode() + suffix
                self.path.write_bytes(payload)
                result = self.ref(global_frame=0)
                self.assertTrue(result["ok"], result["diagnostics"])
                self.assertEqual(result["document"]["raw"].encode("utf-8"), payload)
                self.assertEqual(result["content_version"], "sha256:" + hashlib.sha256(payload).hexdigest())
        malformed = [b" " * 65535 + b"\xf0\x9f", b" " * 65535 + b"\xf0\x9f!",
                     b" " * 65536 + b"\xff", b"\xef\xbb\xbf{}"]
        for payload in malformed:
            self.path.write_bytes(payload)
            reference = self.error("input.json")
            for read in (inspect_timeline, query_timeline):
                result = read(self.path)
                self.assertEqual(reference["diagnostics"], result["diagnostics"])
                self.assertEqual(reference["content_version"], result["content_version"])

    def test_one_snapshot_even_if_file_changes_after_shared_parse(self):
        original = self.path.read_bytes()
        version = self.version()
        check = timeline._check_shape
        def changing(result):
            check(result)
            self.path.write_bytes(b"{}")
        with patch.object(timeline, "_check_shape", side_effect=changing):
            result = self.ref(global_frame=0, expected_content_version=version)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["document"]["raw"].encode("utf-8"), original)
        self.assertEqual(result["content_version"], version)
        self.assertEqual(result["selection"]["blocks"][0]["range"], [0, 10])

    def test_cli_envelope_exit_codes_and_read_only(self):
        before = self.path.read_bytes()
        for argv, expected in (((), self.ref()), (("--frame", "10"), self.ref(global_frame=10)),
                               (("--interval", "0", "10"), self.ref(interval=[0, 10]))):
            code, actual = self.cli(*argv)
            self.assertEqual(code, 0)
            self.assertEqual(actual, expected)
        for argv in (("--frame", "60"), ("--frame", "true"), ("--block-address", "/subtitles/0")):
            code, actual = self.cli(*argv)
            self.assertEqual(code, 1)
            self.assertFalse(actual["ok"])
        for argv in (("--draft",), ("--json", str(self.root / "report.json")),
                     ("--events-jsonl", str(self.root / "events.jsonl")), ("--family", "video")):
            code, actual = self.cli(*argv)
            self.assertEqual(code, 2)
            self.assertEqual(actual["schema"], "framivra.timeline.reference/v1")
            self.assertEqual(actual["diagnostics"][0]["code"], "cli.arguments")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [self.path])
        self.assertEqual(inspect_timeline(self.path)["content_version"], self.ref()["content_version"])


if __name__ == "__main__":
    unittest.main()
