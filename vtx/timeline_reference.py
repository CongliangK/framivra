"""Lossless stored references, not rendered frames or a second timeline parser."""
import json
from decimal import Decimal
from pathlib import Path
import re

from .timeline import _load, _error, _coverage, _window
from .timeline_structure import identity_errors

_SCHEMA = "framivra.timeline.reference/v1"
_LIMIT = 8 * 1024 * 1024
_SAFE_INT = 2 ** 53 - 1
# Lexical spans only: grammar, duplicate keys and semantic values come from _load.
_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[{}\[\]:,]|[^\s{}\[\]:,]+')


def _spans(text, document, wanted):
    """Locate validated values using parsed object order; never interpret numbers."""
    tokens = iter(_TOKEN.finditer(text))
    spans = {}

    def visit(value, path):
        first = next(tokens)
        last = first
        if isinstance(value, dict):
            for index, (key, child) in enumerate(value.items()):
                if index:
                    next(tokens)  # comma
                next(tokens)  # original key token (already decoded by _load)
                next(tokens)  # colon
                last = visit(child, path + "/" + key.replace("~", "~0").replace("/", "~1"))
            last = next(tokens)  # closing brace
        elif isinstance(value, list):
            for index, child in enumerate(value):
                if index:
                    next(tokens)
                last = visit(child, path + "/" + str(index))
            last = next(tokens)
        if path in wanted:
            spans[path] = text[first.start():last.end()]
        return last

    visit(document, "")
    return spans


def _finish(result):
    # Same ASCII JSON representation as the CLI, including the final newline.
    if len(json.dumps(result, ensure_ascii=True, allow_nan=False).encode("ascii")) + 1 > _LIMIT:
        result.update(document=None, selection=None, coverage=None)
        result["diagnostics"] = []
        _error(result, "output.limit", "", "reference exceeds JSON wire byte limit", limit_bytes=_LIMIT)
    return result


def reference_timeline(path, *, track_ids=None, block_ids=None, track_addresses=None,
                       block_addresses=None, global_frame=None, interval=None,
                       expected_content_version=None):
    """Reference one exact UTF-8 snapshot. Addresses require its expected version.

    Empty selectors reference only the document. Track selectors include their
    stored blocks; time-only selectors include active/overlapping stored blocks.
    Explicit block/window conflicts fail, rather than silently dropping a target.
    IDs are available only for protocol-1 managed documents. No files are written.
    """
    loaded = _load(path, "reference", raw=True, max_bytes=_LIMIT)
    doc = loaded.pop("document")
    raw = loaded.pop("raw_document", None)
    result = dict(loaded, schema=_SCHEMA, document=None, selection=None,
                  coverage=_coverage(doc), image={"state": "not-generated", "kind": None})
    version = result["content_version"]
    if not result["ok"]:
        return _finish(result)
    managed = "_vtx" in doc
    meta = doc.get("_vtx") if managed else {}
    if managed:
        for problem in identity_errors(doc, require_managed=True):
            _error(result, "reference.identity", "/_vtx", problem)
    if not result["ok"]:
        return _finish(result)
    result["document"] = {
        "state": "stored", "raw": raw, "content_version": version,
        "location": {"path": Path(path).expanduser().name, "relative_to": "timeline parent directory"},
        "project": {"kind": "managed" if managed else "legacy",
                    "project_id": meta.get("project_id"), "head": meta.get("head")},
    }
    duration = doc["project"]["duration_frames"]
    selection = {"global_frame": global_frame, "fps": doc["project"]["fps"],
                 "interval": list(interval) if isinstance(interval, (list, tuple)) else None,
                 "tracks": [], "blocks": []}
    # Do not echo invalid caller values into a purported JSON-safe envelope.
    result["selection"] = None
    if expected_content_version is not None and expected_content_version != version:
        _error(result, "reference.stale", "/selection/expected_content_version", "snapshot version does not match")
    if global_frame is not None:
        if type(global_frame) is not int:
            _error(result, "reference.integer", "/selection/global_frame", "expected integer frame")
        elif not 0 <= global_frame < duration:
            _error(result, "reference.bounds", "/selection/global_frame", "frame must be in [0,duration)")
    if interval is not None:
        if (not isinstance(interval, (list, tuple)) or len(interval) != 2
                or any(type(v) is not int for v in interval)):
            _error(result, "reference.interval", "/selection/interval", "expected two integer frames")
        elif not 0 <= interval[0] < interval[1] <= duration:
            _error(result, "reference.bounds", "/selection/interval", "require 0 <= start < end <= duration")
    if global_frame is not None and interval is not None:
        _error(result, "reference.conflict", "/selection", "frame and interval are mutually exclusive")
    selectors = {}
    for key, values in (("track_ids", track_ids), ("block_ids", block_ids),
                        ("track_addresses", track_addresses), ("block_addresses", block_addresses)):
        if values is None:
            values = []
        if not isinstance(values, (list, tuple)) or any(not isinstance(v, str) for v in values):
            _error(result, "reference.selector", "/selection/" + key, "expected a list of strings")
            values = []
        selectors[key] = values
        if len(values) != len(set(values)):
            _error(result, "reference.duplicate", "/selection/" + key, "duplicate selector")
    if (selectors["track_addresses"] or selectors["block_addresses"]) and expected_content_version is None:
        _error(result, "reference.version_required", "/selection/expected_content_version", "transient addresses require expected_content_version")
    if not managed and (selectors["track_ids"] or selectors["block_ids"]):
        _error(result, "reference.unmanaged", "/selection", "legacy objects have no protocol stable IDs; explicit adopt is separate")
    tracks, blocks = [], []
    source_ids = [s["id"] for s in doc["sources"]]
    if len(source_ids) != len(set(source_ids)):
        _error(result, "reference.ambiguous", "/sources", "duplicate source identity")
    for family, key, child in (("video", "video_tracks", "clips"),
                                ("audio", "audio_tracks", "events"),
                                ("subtitles", "subtitles", None)):
        values = doc.get(key, [])
        if child:
            names = [t["name"] for t in values]
            if len(names) != len(set(names)):
                _error(result, "reference.ambiguous", "/" + key, "duplicate track name")
        for ti, track in enumerate(values if child else [values]):
            address = f"/{key}/{ti}" if child else "/subtitles"
            tr = {"family": family, "stable_id": track.get("id") if managed and child else None,
                  "address": address, "content_version": version}
            tracks.append(tr)
            for bi, block in enumerate(track[child] if child else track):
                bp = address + f"/{child}/{bi}" if child else f"/subtitles/{bi}"
                start, end = _window(family, block)
                for value in (start, end, block.get("src_in", 0), block.get("src_out", 0)):
                    if value > _SAFE_INT:
                        _error(result, "reference.precision", bp, "derived frame exceeds safe integer range")
                item = {"family": family, "stable_id": block.get("id") if managed else None,
                        "address": bp, "content_version": version, "range": [start, end]}
                if child and not (family == "video" and track["kind"] == "gfx"):
                    if block["src"] not in source_ids:
                        _error(result, "reference.source", bp + "/src", "unknown source identity")
                    else:
                        item["source_id"] = block["src"]
                        # No source_frame: without reading media we cannot prove
                        # native fps/CFR, source duration or renderer normalization.
                blocks.append((item, tr))
    if duration > _SAFE_INT:
        _error(result, "reference.precision", "/project/duration_frames", "duration exceeds safe integer range")
    if not result["ok"]:
        return _finish(result)

    def resolve(items, prefix):
        chosen = set()
        for field, lookup in ((prefix + "_ids", "stable_id"), (prefix + "_addresses", "address")):
            for value in selectors[field]:
                matches = [obj for obj in items if obj[lookup] == value]
                if not matches:
                    _error(result, "reference.not_found", "/selection/" + field, "selector does not exist", selector=value)
                elif matches[0]["address"] in chosen:
                    _error(result, "reference.duplicate", "/selection/" + field, "object selected twice", selector=value)
                else:
                    chosen.add(matches[0]["address"])
        return chosen

    chosen_tracks = resolve(tracks, "track")
    chosen_blocks = resolve([b for b, _ in blocks], "block")
    has_explicit = bool(chosen_tracks or chosen_blocks)
    has_time = global_frame is not None or interval is not None
    selected_tracks, selected_blocks = set(), []
    for item, tr in blocks:
        start, end = item["range"]
        hit = ((global_frame is None or start <= global_frame < end)
               and (interval is None or start < interval[1] and interval[0] < end))
        explicit = item["address"] in chosen_blocks
        if explicit and not hit:
            _error(result, "reference.conflict", item["address"], "selected block does not intersect requested window")
        if hit and (explicit or tr["address"] in chosen_tracks or has_time and not has_explicit):
            selected_blocks.append(item)
            selected_tracks.add(tr["address"])
    for tr in tracks:
        if tr["address"] in chosen_tracks:
            if has_time and tr["address"] not in selected_tracks:
                _error(result, "reference.conflict", tr["address"], "selected track has no stored block in requested window")
            selected_tracks.add(tr["address"])
    if not result["ok"]:
        return _finish(result)
    selection["tracks"] = [tr for tr in tracks if tr["address"] in selected_tracks]
    selection["blocks"] = selected_blocks
    wanted = {v["address"] for v in selection["tracks"] + selected_blocks}
    if type(selection["fps"]) is float:
        wanted.add("/project/fps")
    try:
        spans = _spans(raw, doc, wanted) if wanted else {}
    except RecursionError:
        _error(result, "input.limit", "", "reference span nesting exceeds supported depth")
        return _finish(result)
    if "/project/fps" in spans and Decimal(spans["/project/fps"]) != Decimal(str(selection["fps"])):
        _error(result, "reference.precision", "/project/fps", "derived fps would lose stored numeric precision")
        return _finish(result)
    for item in selection["tracks"] + selected_blocks:
        # An external-only subtitle virtual track has no stored array span.
        item["raw"] = spans.get(item["address"])
    result["selection"] = selection
    return _finish(result)
