"""Asset-independent, read-only timeline inspection. Public exports: vtx.api.

This is a query-shape check, NOT render validation or a second schema authority.
No Ctx, plugins, referenced files, migrations, locks or project writes are used.
"""
import hashlib
import json
import math
from pathlib import Path

from .pipeline.constraints import ALLOWED_FPS, fps_error

_SCHEMA = "framivra.timeline.read/v1"
_FAMILIES = ("video", "audio", "subtitles")


def _result(operation):
    return {"schema": _SCHEMA, "operation": operation, "ok": True,
            "content_version": None, "diagnostics": []}


def _error(result, code, address, message, **details):
    result["ok"] = False
    result["diagnostics"].append(dict(code=code, address=address,
                                      severity="error", message=message, **details))


def timeline_capabilities():
    """Return the static read protocol, without opening any project or asset."""
    result = _result("capabilities")
    result["capabilities"] = {
        "operations": ["inspect", "query", "capabilities"], "read_only": True,
        "cli_exit_codes": {"0": "success", "1": "input/query error", "2": "CLI usage error"},
        "validation_scope": "query-shape (not render validation)",
        "families": list(_FAMILIES), "fps": list(ALLOWED_FPS),
        "time_unit": "integer frames (bool and float rejected)",
        "interval": "[start,end)", "interval_match": "overlap",
        "query_bounds": "0 <= frame < duration; 0 <= start < end <= duration; no clipping",
        "addressing": "JSON Pointer array positions, transient within content_version; not stable IDs",
        "content_version": "sha256:<hex> of the exact timeline file bytes, not assets",
        "preserves_extensions": True, "resolves_assets": False,
        "expands_subtitles_src": False, "executes_plugins": False,
        "write_operations": [],
    }
    return result


class _StrictJSONError(ValueError):
    pass


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _StrictJSONError("duplicate object key: " + repr(key))
        result[key] = value
    return result


def _constant(value):
    raise _StrictJSONError("non-JSON numeric constant: " + value)


def _float(value):
    number = float(value)
    if not math.isfinite(number):
        raise _StrictJSONError("number outside finite float range: " + value)
    return number


def _load(path, operation):
    result = _result(operation)
    result["document"] = None
    try:
        payload = Path(path).expanduser().read_bytes()
    except (OSError, ValueError, TypeError) as error:
        _error(result, "input.read", "", str(error))
        return result
    result["content_version"] = "sha256:" + hashlib.sha256(payload).hexdigest()
    try:
        result["document"] = json.loads(payload.decode("utf-8"), object_pairs_hook=_pairs,
                                        parse_constant=_constant, parse_float=_float)
    except (ValueError, UnicodeError, RecursionError) as error:
        _error(result, "input.json", "", str(error),
               line=getattr(error, "lineno", None), column=getattr(error, "colno", None))
        return result
    _check_shape(result)
    return result


def _check_shape(result):
    """Only check fields needed to identify/query stored blocks safely.

    Keep even malformed/unsupported documents intact for inspection. Rendering
    restrictions (source facts, geometry, EDL, fades, plugins etc.) stay validate's.
    """
    doc = result["document"]
    def expect(value, cls, address):
        if not isinstance(value, cls):
            _error(result, "shape.type", address, "expected " + cls.__name__)
            return False
        return True
    def integer(value, address, minimum):
        if type(value) is not int or value < minimum:
            _error(result, "shape.frame", address, "expected integer >= " + str(minimum))
            return False
        return True
    if not expect(doc, dict, ""):
        return
    if type(doc.get("version")) not in (int, float) or doc["version"] != 2:
        _error(result, "timeline.version", "/version", "only version 2 is queryable; no migration")
    project = doc.get("project")
    duration = None
    if expect(project, dict, "/project"):
        problem = fps_error(project.get("fps"))
        if problem:
            _error(result, "timeline.fps", "/project/fps", problem)
        if integer(project.get("duration_frames"), "/project/duration_frames", 1):
            duration = project["duration_frames"]
    sources = doc.get("sources")
    if expect(sources, list, "/sources"):
        for index, source in enumerate(sources):
            address = f"/sources/{index}"
            if expect(source, dict, address):
                for key in ("id", "path"):
                    expect(source.get(key), str, address + "/" + key)
    for family, key, blocks_key in (("video", "video_tracks", "clips"),
                                    ("audio", "audio_tracks", "events"),
                                    ("subtitles", "subtitles", None)):
        # External subtitles remain a reference; never materialize them here.
        values = doc.get(key, [] if key == "subtitles" and doc.get("subtitles_src") else None)
        if not expect(values, list, "/" + key):
            continue
        tracks = [(values, "/subtitles")] if blocks_key is None else []
        if blocks_key:
            for ti, track in enumerate(values):
                address = f"/{key}/{ti}"
                if not expect(track, dict, address):
                    continue
                expect(track.get("name"), str, address + "/name")
                if family == "video":
                    expect(track.get("kind"), str, address + "/kind")
                blocks = track.get(blocks_key)
                if expect(blocks, list, address + "/" + blocks_key):
                    tracks.append((blocks, address + "/" + blocks_key))
        for blocks, address in tracks:
            for bi, block in enumerate(blocks):
                bp = f"{address}/{bi}"
                if not expect(block, dict, bp):
                    continue
                for field in (("text",) if family == "subtitles" else ("src",)):
                    expect(block.get(field), str, bp + "/" + field)
                start_key, end_key = ("dst_in", "dur") if family == "video" else ("in", "out")
                start_ok = integer(block.get(start_key), bp + "/" + start_key, 0)
                end_ok = integer(block.get(end_key), bp + "/" + end_key, 1)
                for field in ("src_in", "src_out", "fade_in_frames", "fade_out_frames"):
                    if field in block:
                        integer(block[field], bp + "/" + field, 0 if field != "src_out" else 1)
                if start_ok and end_ok:
                    start, end = _window(family, block)
                    if end <= start or duration is not None and end > duration:
                        _error(result, "timeline.bounds", bp, "block must satisfy 0 <= start < end <= duration")
    if "subtitles_src" in doc:
        expect(doc["subtitles_src"], str, "/subtitles_src")
        if doc["subtitles_src"] and doc.get("subtitles"):
            _error(result, "timeline.subtitles_conflict", "/subtitles_src", "inline and external subtitles are mutually exclusive")


def _coverage(document):
    external = document.get("subtitles_src") if isinstance(document, dict) else None
    return {"complete": not bool(external), "scope": "stored timeline blocks only",
            "unexpanded": ([{"address": "/subtitles_src", "reference": external,
                              "reason": "external subtitle file is not read"}] if external else [])}


def inspect_timeline(path):
    """Return full stored JSON data + byte content version + structured diagnostics.

    No normalization or reference expansion. ``ok`` means queryable shape only,
    not schema conformance, asset validity, renderability or content approval.
    """
    result = _load(path, "inspect")
    result["coverage"] = _coverage(result["document"])
    return result


def _window(family, block):
    if family == "video":
        return block["dst_in"], block["dst_in"] + block["dur"]
    return block["in"], block["out"]


def query_timeline(path, *, family=None, track_index=None, block_index=None,
                   frame=None, interval=None):
    """Query by array positions and/or global frame/overlapping [start,end).

    All filters intersect. Order is video, audio, subtitles then original array
    order (never time-sorted). Subtitle entries use virtual track index 0.
    Returned data is complete, not a rendering projection. Addresses are only
    meaningful in the returned content_version; there are no stable block IDs.
    """
    result = _load(path, "query")
    doc = result.pop("document")
    result.update(tracks=[], blocks=[], coverage=_coverage(doc))
    if not result["ok"]:
        return result
    if family is not None and (not isinstance(family, str) or family not in _FAMILIES):
        _error(result, "query.family", "/query/family", "expected video, audio or subtitles")
    for key, value in (("track_index", track_index), ("block_index", block_index), ("frame", frame)):
        if value is not None and (type(value) is not int or value < 0):
            _error(result, "query.integer", "/query/" + key, "expected nonnegative integer")
    if track_index is not None and family is None:
        _error(result, "query.selector", "/query/track_index", "track_index requires family")
    if block_index is not None and (family is None or track_index is None):
        _error(result, "query.selector", "/query/block_index", "block_index requires family and track_index")
    duration = doc["project"]["duration_frames"]
    if frame is not None and type(frame) is int and not 0 <= frame < duration:
        _error(result, "query.bounds", "/query/frame", "frame must be in [0,duration)")
    if frame is not None and interval is not None:
        _error(result, "query.selector", "/query/interval", "frame and interval are mutually exclusive")
    if interval is not None:
        if (not isinstance(interval, (tuple, list)) or len(interval) != 2
                or any(type(v) is not int for v in interval)):
            _error(result, "query.interval", "/query/interval", "expected two integer frames")
        elif not 0 <= interval[0] < interval[1] <= duration:
            _error(result, "query.bounds", "/query/interval", "require 0 <= start < end <= duration; no clipping")
    if not result["ok"]:
        return result
    for current, key, blocks_key in (("video", "video_tracks", "clips"),
                                     ("audio", "audio_tracks", "events"),
                                     ("subtitles", "subtitles", None)):
        if family is not None and current != family:
            continue
        tracks = doc.get(key, []) if blocks_key else [doc.get(key, [])]
        if track_index is not None and track_index >= len(tracks):
            _error(result, "query.not_found", "/query/track_index", "track position does not exist")
            return result
        for ti, track in enumerate(tracks):
            if track_index is not None and ti != track_index:
                continue
            address = f"/{key}/{ti}" if blocks_key else "/subtitles"
            blocks = track[blocks_key] if blocks_key else track
            if block_index is not None and block_index >= len(blocks):
                _error(result, "query.not_found", "/query/block_index", "block position does not exist")
                return result
            matches = []
            for bi, block in enumerate(blocks):
                if block_index is not None and bi != block_index:
                    continue
                start, end = _window(current, block)
                if frame is not None and not start <= frame < end:
                    continue
                if interval is not None and not (start < interval[1] and interval[0] < end):
                    continue
                bp = f"{address}/{blocks_key}/{bi}" if blocks_key else f"/subtitles/{bi}"
                matches.append({"family": current, "track_index": ti, "block_index": bi,
                                "address": bp, "range": [start, end], "data": block})
            if matches or frame is None and interval is None and block_index is None:
                result["tracks"].append({"family": current, "track_index": ti,
                                         "address": address, "data": track})
            result["blocks"].extend(matches)
    return result
