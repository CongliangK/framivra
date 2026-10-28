"""Asset-independent stored-document checks shared by the store and validate.

No Ctx, asset probing, subtitle expansion, plugin execution or migration.
The JSON schema remains the field/type authority; identities are protocol checks.
"""
import json
from decimal import Decimal, InvalidOperation
import re
from pathlib import Path

from .timeline import _pairs, _constant, _float, _check_shape

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


def strict_loads(payload):
    def number(token):
        value = _float(token)
        try:
            exact = Decimal(token)
        except InvalidOperation as error:
            # JSON's exponent range exceeds Decimal's. Normalize this known
            # input boundary here, not by swallowing arbitrary store failures.
            raise ValueError("numeric precision outside writable range: " + token) from error
        if exact != Decimal(str(value)):
            raise ValueError("numeric precision would be lost on write: " + token)
        return value
    return json.loads(payload.decode("utf-8"), object_pairs_hook=_pairs,
                      parse_constant=_constant, parse_float=number)


def managed_objects(doc):
    """Yield (JSON pointer, object, kind) for stored (not referenced) objects."""
    for key, kind, child in (("sources", "source", None),
                             ("video_tracks", "video_track", "clips"),
                             ("audio_tracks", "audio_track", "events"),
                             ("subtitles", "subtitle", None)):
        values = doc.get(key, [])
        if not isinstance(values, list):
            continue
        for i, obj in enumerate(values):
            if not isinstance(obj, dict):
                continue
            path = f"/{key}/{i}"
            yield path, obj, kind
            if child and isinstance(obj.get(child), list):
                for j, block in enumerate(obj[child]):
                    if isinstance(block, dict):
                        yield f"{path}/{child}/{j}", block, "clip" if child == "clips" else "event"


def identity_errors(doc, *, require_managed=False, require_source_id=True):
    errors = []
    if not isinstance(doc, dict):
        return ["/: expected object"]
    meta = doc.get("_vtx")
    managed = "_vtx" in doc
    if require_managed and not managed:
        errors.append("/_vtx: explicit adopt required")
    if managed:
        if (not isinstance(meta, dict) or set(meta) != {"protocol", "project_id", "head"}
                or type(meta.get("protocol")) is not int or meta["protocol"] != 1
                or not valid_id(meta.get("project_id")) or not valid_id(meta.get("head"))):
            errors.append("/_vtx: expected protocol 1 and valid project_id/head; unknown protocol/keys rejected")
    seen = set()
    for path, obj, kind in managed_objects(doc):
        if "id" not in obj:
            if managed or require_managed or (kind == "source" and require_source_id):
                errors.append(path + "/id: missing stable identity")
            continue
        value = obj["id"]
        if not valid_id(value):
            errors.append(path + "/id: invalid stable identity")
        elif value in seen:
            errors.append(path + "/id: duplicate stable identity " + value)
        else:
            seen.add(value)
    return errors


def valid_id(value):
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _schema_errors(value, schema, path=""):
    """Evaluate the vocabulary used by timeline.schema.json (no dependencies)."""
    errors = []
    def add(message):
        errors.append((path or "/") + ": " + message)
    types = {"object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list),
             "string": lambda v: isinstance(v, str), "integer": lambda v: type(v) is int,
             "number": lambda v: type(v) in (int, float), "boolean": lambda v: type(v) is bool}
    if "type" in schema and not types[schema["type"]](value):
        add("expected " + schema["type"])
        return errors
    def equal(a, b):
        return a == b and (type(a) is bool) == (type(b) is bool)
    if "const" in schema and not equal(value, schema["const"]):
        add("expected constant " + repr(schema["const"]))
    if "enum" in schema and not any(equal(value, v) for v in schema["enum"]):
        add("not in enum")
    for subschema in schema.get("allOf", []):
        errors.extend(_schema_errors(value, subschema, path))
    if "not" in schema and not _schema_errors(value, schema["not"], path):
        add("forbidden shape")
    if "if" in schema:
        branch = "then" if not _schema_errors(value, schema["if"], path) else "else"
        errors.extend(_schema_errors(value, schema.get(branch, {}), path))
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                add("missing " + key)
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                errors.extend(_schema_errors(value[key], sub, path + "/" + key))
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            add("too few items")
        for i, item in enumerate(value):
            errors.extend(_schema_errors(item, schema.get("items", {}), path + "/" + str(i)))
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            add("string too short")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            add("invalid string pattern")
    if type(value) in (int, float):
        for key, test in (("minimum", lambda a, b: a < b), ("maximum", lambda a, b: a > b),
                          ("exclusiveMinimum", lambda a, b: a <= b)):
            if key in schema and test(value, schema[key]):
                add("outside " + key)
    return errors


def structure_errors(doc, *, require_managed=False):
    """Schema + stored bounds/EDL/references/IDs; NOT renderability validation."""
    schema = json.loads(Path(__file__).with_name("timeline.schema.json").read_text(encoding="utf-8"))
    errors = _schema_errors(doc, schema)
    errors.extend(identity_errors(doc, require_managed=require_managed))
    result = {"document": doc, "ok": True, "diagnostics": []}
    _check_shape(result)
    errors.extend(d["address"] + ": " + d["message"] for d in result["diagnostics"])
    if errors:
        return errors
    sources = {s["id"] for s in doc["sources"]}
    for key, child in (("video_tracks", "clips"), ("audio_tracks", "events")):
        tracks = doc[key]
        names = [t["name"] for t in tracks]
        if len(names) != len(set(names)):
            errors.append("/" + key + ": duplicate track name (reference ambiguity)")
        for ti, track in enumerate(tracks):
            windows = []
            for bi, block in enumerate(track[child]):
                path = f"/{key}/{ti}/{child}/{bi}"
                if child == "clips":
                    start, end = block["dst_in"], block["dst_in"] + block["dur"]
                    if track["kind"] != "gfx":
                        if (type(block.get("src_in")) is not int or type(block.get("src_out")) is not int
                                or block["src_out"] - block["src_in"] != block["dur"]):
                            errors.append(path + ": src_out-src_in must equal dur")
                else:
                    start, end = block["in"], block["out"]
                windows.append((start, end))
                if child == "events" or track.get("kind") != "gfx":
                    if block["src"] not in sources:
                        errors.append(path + "/src: unknown source identity")
                if block.get("fade_in_frames", 0) + block.get("fade_out_frames", 0) > end - start:
                    errors.append(path + ": fades exceed duration")
            windows.sort()
            if any(a[1] > b[0] for a, b in zip(windows, windows[1:])):
                errors.append(f"/{key}/{ti}: overlapping blocks")
            if child == "events":
                refs = track.get("duck_by", ["voice"]) if track.get("duck") else track.get("duck_by", [])
                strategy = track.get("duck_strategy")
                if strategy is not None:
                    if not isinstance(strategy, dict) or not isinstance(strategy.get("voice_track", "voice"), str):
                        errors.append(f"/{key}/{ti}/duck_strategy: invalid voice reference")
                    else:
                        refs = refs + [strategy.get("voice_track", "voice")]
                if any(ref not in names or ref == track["name"] for ref in refs):
                    errors.append(f"/{key}/{ti}: invalid duck/voice track reference")
    return errors
