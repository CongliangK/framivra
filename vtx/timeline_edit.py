"""Pure managed timeline operations. No filesystem, media, plugins or publisher.

IDs are supplied by the request; only the final batch must be structurally valid.
Unknown JSON values are deep-copied, never recursively searched/replaced.
"""
import copy

from .timeline_structure import managed_objects, structure_errors, valid_id

EDIT_OPERATIONS = (
    "source.add", "source.remove", "source.update", "track.add", "track.remove",
    "track.copy", "track.reorder", "track.rename", "track.references",
    "block.insert", "block.remove", "block.copy", "block.move", "block.trim",
    "block.split", "block.relink", "parameters.update", "parameters.unset",
    "project.resize", "range.ripple")


class EditError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def fail(message, code="edit.operation"):
    raise EditError(code, message)


def integer(value, label, minimum=None):
    if type(value) is not int or (minimum is not None and value < minimum):
        fail(label + " must be a non-bool integer" + (" >= " + str(minimum) if minimum is not None else ""))
    return value


def fields(op, required, optional=()):
    if not isinstance(op, dict) or not set(required) <= set(op) or set(op) - set(required) - set(optional) - {"op"}:
        fail("missing or unknown fields for operation")


def inventory(doc):
    return {obj["id"]: (path, obj, kind) for path, obj, kind in managed_objects(doc)}


def window(block, kind):
    return (block["dst_in"], block["dst_in"] + block["dur"]) if kind == "clip" else (block["in"], block["out"])


def locate(doc, identity):
    if not valid_id(identity):
        fail("invalid object ID", "edit.identity")
    for path, obj, kind in managed_objects(doc):
        if obj["id"] == identity:
            parts = path.split("/")
            container = doc[parts[1]]
            parent = None
            if len(parts) == 5:
                parent = container[int(parts[2])]
                container = parent[parts[3]]
            return obj, kind, container, parent
    fail("unknown object ID: " + identity, "edit.identity")


def track(doc, identity):
    obj, kind, container, _ = locate(doc, identity)
    if kind not in ("video_track", "audio_track"):
        fail("expected track ID")
    return obj, kind, container


def destination(doc, identity):
    if identity == "subtitles":
        if doc.get("subtitles_src"):
            fail("external subtitles are read-only; explicit materialization is not implemented")
        return doc["subtitles"], "subtitle", None
    obj, kind, _ = track(doc, identity)
    return (obj["clips"], "clip", obj) if kind == "video_track" else (obj["events"], "event", obj)


def block(doc, identity):
    obj, kind, container, parent = locate(doc, identity)
    if kind not in ("clip", "event", "subtitle"):
        fail("expected block ID")
    return obj, kind, container, parent


def compatible(kind, parent, newkind, newparent):
    if kind != newkind or (kind == "clip" and (parent["kind"] == "gfx") != (newparent["kind"] == "gfx")):
        fail("cross-family or ordinary/gfx conversion is not a move/copy")


def place(obj, kind, at):
    integer(at, "at", 0)
    start, end = window(obj, kind)
    if kind == "clip":
        obj["dst_in"] = at
    else:
        obj["in"], obj["out"] = at, at + end - start


def segment(obj, kind, parent, start, end, policy, warnings):
    """Retain timeline [start,end), adjusting the actual source window/phase."""
    if kind == "clip" and parent["kind"] == "gfx":
        fail("gfx continuous trim/split/crossing ripple requires the future source-window model")
    oldstart, oldend = window(obj, kind)
    if not oldstart <= start < end <= oldend:
        fail("segment must be inside the original block")
    if policy not in ("preserve-content", "reapply-edge-fades"):
        fail("fade_policy must be preserve-content or reapply-edge-fades")
    fi, fo = obj.get("fade_in_frames", 0), obj.get("fade_out_frames", 0)
    if policy == "preserve-content":
        if oldstart < start < oldstart + fi or oldend - fo < end < oldend:
            fail("cut intersects a local fade; use explicit reapply-edge-fades or an envelope model")
        if start != oldstart and "fade_in_frames" in obj:
            obj["fade_in_frames"] = 0
        if end != oldend and "fade_out_frames" in obj:
            obj["fade_out_frames"] = 0
    elif fi or fo:
        warnings.append("reapply-edge-fades changes the original local envelope; no fade clamping performed")
    if kind == "clip":
        src = obj["src_in"] + start - oldstart
        obj.update(dst_in=start, src_in=src, src_out=src + end - start, dur=end - start)
    else:
        obj.update({"in": start, "out": end})
        if kind == "event":
            obj["src_in"] = obj.get("src_in", 0) + start - oldstart


def reference_edges(doc):
    edges = []
    for _, obj, kind in managed_objects(doc):
        if kind in ("clip", "event"):
            _, _, _, parent = locate(doc, obj["id"])
            if kind == "event" or parent["kind"] != "gfx":
                edges.append({"id": obj["id"], "field": "src", "value": obj["src"]})
        if kind == "source":
            edges.append({"id": obj["id"], "field": "path", "value": obj["path"]})
        if kind == "clip":
            _, _, _, parent = locate(doc, obj["id"])
            if parent["kind"] == "gfx":
                edges.append({"id": obj["id"], "field": "gfx.src", "value": obj["src"]})
        if kind == "audio_track":
            if obj.get("duck") or "duck_by" in obj:
                edges.append({"id": obj["id"], "field": "duck_by", "value": obj.get("duck_by", ["voice"])})
            if isinstance(obj.get("duck_strategy"), dict):
                edges.append({"id": obj["id"], "field": "duck_strategy.voice_track", "value": obj["duck_strategy"].get("voice_track", "voice")})
    return edges


def edit_summary(before, after, warnings=()):
    old, new = inventory(before), inventory(after)
    changed = [identity for identity in old.keys() & new.keys() if old[identity][1] != new[identity][1] or old[identity][0] != new[identity][0]]
    affected = []
    for identity in sorted(set(changed) | (old.keys() ^ new.keys())):
        item = {"id": identity}
        for label, objects in (("before", old), ("after", new)):
            if identity in objects and objects[identity][2] in ("clip", "event", "subtitle"):
                item[label] = list(window(objects[identity][1], objects[identity][2]))
        if len(item) > 1:
            affected.append(item)
    previous, current = reference_edges(before), reference_edges(after)
    changed_sources = {i for i in changed if old[i][2] == "source"}
    impacted = {e["id"] for e in previous + current if e["field"] == "src" and e["value"] in changed_sources}
    for identity in changed:
        for objects in (old, new):
            _, obj, kind = objects[identity]
            child = "clips" if kind == "video_track" else "events" if kind == "audio_track" else None
            if child:
                impacted.update(b["id"] for b in obj[child])
    if before["project"] != after["project"]:
        impacted.update(i for objects in (old, new) for i, (_, _, kind) in objects.items() if kind in ("clip", "event", "subtitle"))
    for identity in sorted(impacted - {item["id"] for item in affected}):
        item = {"id": identity, "reason": "shared source, parent track or project changed"}
        for label, objects in (("before", old), ("after", new)):
            if identity in objects:
                item[label] = list(window(objects[identity][1], objects[identity][2]))
        affected.append(item)
    return {"id_locations": {i: new[i][0] for i in sorted(new)},
            "created_ids": sorted(new.keys() - old.keys()), "deleted_ids": sorted(old.keys() - new.keys()),
            "changed_ids": sorted(changed),
            "source_users": {i: sorted(e["id"] for e in current if e["field"] == "src" and e["value"] == i) for i in sorted(new) if new[i][2] == "source"},
            "changed_references": {"removed": [e for e in previous if e not in current], "added": [e for e in current if e not in previous]},
            "affected_ranges": affected,
            "warnings": sorted(set(warnings) | {"Unknown extensions are preserved, not reference-rewritten or validated; hidden object/file references may be stale (including copied metadata)."}),
            "validation": {"structure": "passed", "source_bounds": "unprobed", "assets": "unchecked", "plugins": "unchecked", "render": "required"},
            "requires_render_validation": True}


# Known fields may only be edited through their semantic operations. Unknown
# extension roots are allowed, but cannot replace a known identity/container/ref.
KNOWN = {
    "project": {"fps", "width", "height", "duration_frames", "gfx_dependency_policy", "cache_dependencies"},
    "source": {"id", "path", "transform"},
    "video_track": {"id", "name", "kind", "clips"},
    "audio_track": {"id", "name", "events", "duck", "duck_by", "duck_params", "duck_strategy"},
    "clip": {"id", "src", "src_in", "src_out", "dst_in", "dur", "format", "cache_dependencies", "scale", "x", "y", "opacity", "fade_in_frames", "fade_out_frames"},
    "event": {"id", "src", "src_in", "in", "out", "volume", "pan", "loop", "fade_in_frames", "fade_out_frames"},
    "subtitle": {"id", "in", "out", "text"}}
PARAMETERS = {
    "project": {"gfx_dependency_policy", "cache_dependencies"}, "source": {"transform"},
    "video_track": set(), "audio_track": {"duck", "duck_params"},
    "clip": {"scale", "x", "y", "opacity", "fade_in_frames", "fade_out_frames", "cache_dependencies"},
    "event": {"volume", "pan", "loop", "fade_in_frames", "fade_out_frames"}, "subtitle": {"text"}}
REQUIRED = {"event": {"volume"}, "subtitle": {"text"}}


def transform_timeline(document, operations):
    """Return (deep-copied final document, edit summary), or raise EditError."""
    errors = structure_errors(document, require_managed=True)
    if errors:
        fail("; ".join(errors[:20]), "input.structure")
    if not isinstance(operations, list) or not operations:
        fail("operations must be a nonempty list")
    doc = copy.deepcopy(document)
    seen = set(inventory(doc))
    warnings = []

    def allocate(identity):
        if not valid_id(identity) or identity in seen:
            fail("new IDs must be valid and never reused within the batch: " + str(identity), "edit.identity")
        seen.add(identity)

    def insertion_type(value, kind, parent):
        if not isinstance(value, dict):
            fail("new block must be an object")
        if kind == "clip" and parent.get("kind") == "gfx":
            if "src_in" in value or "src_out" in value or value.get("src") in {s.get("id") for s in doc["sources"]}:
                fail("new gfx blocks require a file reference, not ordinary source IDs/windows")
        if kind == "event" and "src_out" in value:
            fail("audio events have no src_out model")

    def allocate_tree(value, child=None):
        if not isinstance(value, dict):
            fail("new object must be an object")
        allocate(value.get("id"))
        if child:
            if not isinstance(value.get(child), list) or not value[child]:
                fail("new tracks require an explicit nonempty first block collection")
            for entry in value[child]:
                allocate_tree(entry)

    for request in operations:
        if not isinstance(request, dict) or request.get("op") not in EDIT_OPERATIONS:
            fail("unsupported semantic operation")
        op = copy.deepcopy(request)
        name = op["op"]
        if name == "source.add":
            fields(op, {"data"})
            allocate_tree(op["data"])
            doc["sources"].append(op["data"])
        elif name == "source.remove":
            fields(op, {"id"}, {"cascade"})
            obj, kind, container, _ = locate(doc, op["id"])
            if kind != "source":
                fail("expected source ID")
            if "cascade" in op:
                users = {e["id"] for e in reference_edges(doc) if e["field"] == "src" and e["value"] == obj["id"]}
                if not isinstance(op["cascade"], list) or len(set(op["cascade"])) != len(op["cascade"]) or set(op["cascade"]) != users:
                    fail("cascade must explicitly enumerate exactly all current source user block IDs")
                for identity in op["cascade"]:
                    user, _, items, _ = block(doc, identity)
                    items.remove(user)
            container.remove(obj)
        elif name == "source.update":
            fields(op, {"id", "set"})
            obj, kind, _, _ = locate(doc, op["id"])
            if kind != "source" or not isinstance(op["set"], dict) or not op["set"] or set(op["set"]) - {"path", "transform"}:
                fail("source.update supports path/transform only, retaining source ID")
            obj.update(op["set"])
        elif name == "track.add":
            fields(op, {"family", "index", "data"})
            if op["family"] not in ("video", "audio"):
                fail("only real video/audio tracks can be added")
            items = doc[op["family"] + "_tracks"]
            index = integer(op["index"], "index", 0)
            if index > len(items):
                fail("track insertion index outside collection")
            child = "clips" if op["family"] == "video" else "events"
            allocate_tree(op["data"], child)
            for entry in op["data"][child]:
                insertion_type(entry, "clip" if child == "clips" else "event", op["data"])
            items.insert(index, op["data"])
        elif name == "track.remove":
            fields(op, {"id"})
            obj, _, items = track(doc, op["id"])
            items.remove(obj)  # Remaining known references are checked once, after the batch.
        elif name == "track.copy":
            fields(op, {"id", "ids", "name", "index"})
            obj, kind, items = track(doc, op["id"])
            child = "clips" if kind == "video_track" else "events"
            identities = {obj["id"]} | {b["id"] for b in obj[child]}
            if not isinstance(op["ids"], dict) or set(op["ids"]) != identities:
                fail("track copy requires an exact old-ID to new-ID map for track and all descendants")
            for identity in op["ids"].values():
                allocate(identity)
            copied = copy.deepcopy(obj)
            copied["id"], copied["name"] = op["ids"][obj["id"]], op["name"]
            for entry in copied[child]:
                entry["id"] = op["ids"][entry["id"]]
            index = integer(op["index"], "index", 0)
            if index > len(items):
                fail("track insertion index outside collection")
            items.insert(index, copied)
        elif name == "track.reorder":
            fields(op, {"family", "ids"})
            if op["family"] not in ("video", "audio"):
                fail("reorder requires video/audio family")
            items = doc[op["family"] + "_tracks"]
            if not isinstance(op["ids"], list) or len(op["ids"]) != len(items) or set(op["ids"]) != {t["id"] for t in items}:
                fail("reorder requires the complete track ID inventory exactly once")
            mapping = {t["id"]: t for t in items}
            items[:] = [mapping[i] for i in op["ids"]]
        elif name == "track.rename":
            fields(op, {"id", "name"})
            obj, kind, items = track(doc, op["id"])
            if not isinstance(op["name"], str) or not op["name"]:
                fail("track name must be nonempty string")
            oldname = obj["name"]
            if sum(t["name"] == oldname for t in items) != 1:
                fail("ambiguous track-name references")
            if kind == "audio_track":
                for consumer in items:
                    if consumer.get("duck") or "duck_by" in consumer:
                        refs = consumer.get("duck_by", ["voice"])
                        if oldname in refs:
                            consumer["duck_by"] = [op["name"] if r == oldname else r for r in refs]
                    strategy = consumer.get("duck_strategy")
                    if isinstance(strategy, dict) and strategy.get("voice_track", "voice") == oldname:
                        strategy["voice_track"] = op["name"]
            obj["name"] = op["name"]
        elif name == "track.references":
            fields(op, {"id", "set"}, {"unset"})
            obj, kind, _ = track(doc, op["id"])
            values, unset = op["set"], op.get("unset", [])
            if kind != "audio_track" or not isinstance(values, dict) or set(values) - {"duck_by", "duck_strategy"} or not isinstance(unset, list) or set(unset) - {"duck_by", "duck_strategy"} or set(values) & set(unset):
                fail("track.references explicitly sets/unsets duck_by/duck_strategy")
            obj.update(values)
            for key in unset:
                obj.pop(key, None)
        elif name == "block.insert":
            fields(op, {"track_id", "data"})
            items, kind, parent = destination(doc, op["track_id"])
            allocate_tree(op["data"])
            insertion_type(op["data"], kind, parent)
            items.append(op["data"])
        elif name == "block.remove":
            fields(op, {"id"})
            obj, _, items, _ = block(doc, op["id"])
            items.remove(obj)
        elif name in ("block.move", "block.copy"):
            fields(op, {"id", "track_id", "at"} | ({"new_id"} if name == "block.copy" else set()))
            obj, kind, items, parent = block(doc, op["id"])
            newitems, newkind, newparent = destination(doc, op["track_id"])
            compatible(kind, parent, newkind, newparent)
            if name == "block.copy":
                allocate(op["new_id"])
                obj = copy.deepcopy(obj)
                obj["id"] = op["new_id"]
            else:
                items.remove(obj)
            place(obj, kind, op["at"])
            newitems.append(obj)
        elif name == "block.relink":
            fields(op, {"id", "src"})
            obj, kind, _, _ = block(doc, op["id"])
            if kind == "subtitle" or "src" not in obj or not isinstance(op["src"], str):
                fail("only source-bearing blocks can be relinked")
            obj["src"] = op["src"]
        elif name == "block.trim":
            fields(op, {"id", "start", "end"}, {"fade_policy"})
            obj, kind, _, parent = block(doc, op["id"])
            start, end = integer(op["start"], "start", 0), integer(op["end"], "end", 1)
            oldstart, oldend = window(obj, kind)
            if start < oldstart or end > oldend:
                fail("outward trim requires authoritative source bounds, unavailable in this asset-independent slice")
            segment(obj, kind, parent, start, end, op.get("fade_policy", "preserve-content"), warnings)
        elif name == "block.split":
            fields(op, {"id", "at", "new_id"}, {"fade_policy", "text_policy", "texts"})
            obj, kind, items, parent = block(doc, op["id"])
            start, end = window(obj, kind)
            at = integer(op["at"], "at", 0)
            if not start < at < end:
                fail("split must be strictly inside the block")
            allocate(op["new_id"])
            right = copy.deepcopy(obj)
            if kind == "subtitle":
                if op.get("text_policy") == "duplicate-text" and "texts" not in op:
                    pass
                elif op.get("text_policy") == "explicit" and isinstance(op.get("texts"), list) and len(op["texts"]) == 2 and all(isinstance(s, str) for s in op["texts"]):
                    obj["text"], right["text"] = op["texts"]
                else:
                    fail("subtitle split requires duplicate-text or explicit texts")
            segment(obj, kind, parent, start, at, op.get("fade_policy", "preserve-content"), warnings)
            segment(right, kind, parent, at, end, op.get("fade_policy", "preserve-content"), warnings)
            right["id"] = op["new_id"]
            items.insert(items.index(obj) + 1, right)
        elif name in ("parameters.update", "parameters.unset"):
            fields(op, {"path", "value"} if name.endswith("update") else {"path"}, {"id", "target"})
            if ("id" in op) == ("target" in op):
                fail("select exactly one object id or target:project")
            if "target" in op:
                if op["target"] != "project":
                    fail("only target:project is supported")
                obj, kind = doc["project"], "project"
            else:
                obj, kind, _, _ = locate(doc, op["id"])
            path = op["path"]
            if not isinstance(path, list) or not path or any(not isinstance(k, str) or not k for k in path):
                fail("parameter path must be a nonempty list of local object keys")
            key = path[0]
            structural = {"_vtx", "id", "version", "project", "sources", "video_tracks", "audio_tracks", "subtitles", "subtitles_src", "clips", "events", "name", "kind", "src", "path", "src_in", "src_out", "dst_in", "dur", "in", "out", "duration_frames", "fps", "width", "height", "duck_by", "duck_strategy"}
            if key in structural or (key in KNOWN[kind] and key not in PARAMETERS[kind]):
                fail("identity/reference/container/placement fields require semantic operations")
            if key in KNOWN[kind] and len(path) != 1:
                fail("known parameters are replaced as complete typed values")
            if name.endswith("unset") and key in REQUIRED.get(kind, set()):
                fail("cannot unset required parameter")
            for part in path[:-1]:
                if not isinstance(obj.get(part), dict):
                    fail("extension parent must already be an object")
                obj = obj[part]
            if name.endswith("update"):
                obj[path[-1]] = op["value"]
            else:
                if path[-1] not in obj:
                    fail("cannot unset absent parameter")
                del obj[path[-1]]
        elif name == "project.resize":
            fields(op, {"duration_frames"})
            doc["project"]["duration_frames"] = integer(op["duration_frames"], "duration_frames", 1)
        elif name == "range.ripple":
            fields(op, {"scope", "mode", "crossing", "resize"}, {"at", "delta", "start", "end", "split_ids", "fade_policy", "text_policy"})
            scope = op["scope"]
            if not isinstance(scope, dict) or set(scope) != {"track_ids", "subtitles"} or not isinstance(scope["track_ids"], list) or type(scope["subtitles"]) is not bool or type(op["resize"]) is not bool:
                fail("ripple scope must explicitly list track_ids and subtitles bool; resize must be bool")
            if len(set(scope["track_ids"])) != len(scope["track_ids"]) or not (scope["track_ids"] or scope["subtitles"]):
                fail("ripple scope must be nonempty and unique")
            collections = [destination(doc, i) for i in scope["track_ids"]]
            if "subtitles" in scope["track_ids"]:
                fail("virtual subtitles must be selected only by scope.subtitles")
            if scope["subtitles"]:
                collections.append(destination(doc, "subtitles"))
            mode = op["mode"]
            if mode == "shift":
                if set(op) - {"op", "scope", "mode", "crossing", "resize", "at", "delta"} or "at" not in op or "delta" not in op or op["crossing"] != "reject":
                    fail("shift ripple requires at/delta and crossing:reject only")
                start = end = integer(op["at"], "at", 0)
                if start > doc["project"]["duration_frames"]:
                    fail("shift ripple at outside canvas")
                delta = integer(op["delta"], "delta")
                if delta == 0:
                    fail("ripple delta must be nonzero")
            elif mode == "delete":
                if "at" in op or "delta" in op or op["crossing"] not in ("reject", "cut") or "start" not in op or "end" not in op:
                    fail("delete ripple requires start/end and crossing:reject|cut")
                start, end = integer(op["start"], "start", 0), integer(op["end"], "end", 1)
                if not start < end <= doc["project"]["duration_frames"]:
                    fail("delete ripple range outside canvas")
                delta = start - end
            else:
                fail("ripple mode must be shift or delete")
            split_ids = op.get("split_ids", {})
            if not isinstance(split_ids, dict):
                fail("split_ids must be an old-ID to right-ID mapping")
            used_splits = set()
            for items, kind, parent in collections:
                for obj in list(items):
                    a, b = window(obj, kind)
                    if a >= end:
                        place(obj, kind, a + delta)
                    elif b <= start:
                        continue
                    elif mode == "shift" or op["crossing"] == "reject":
                        fail("ripple intersects a block and crossing policy rejects it")
                    elif a >= start and b <= end:
                        items.remove(obj)
                    else:
                        if kind == "subtitle" and op.get("text_policy") != "duplicate-text":
                            fail("crossing subtitle ripple requires explicit duplicate-text policy")
                        policy = op.get("fade_policy", "preserve-content")
                        if a < start and b > end:
                            if obj["id"] not in split_ids:
                                fail("spanning ripple needs a request-held right ID in split_ids")
                            identity = split_ids[obj["id"]]
                            allocate(identity)
                            used_splits.add(obj["id"])
                            right = copy.deepcopy(obj)
                            segment(obj, kind, parent, a, start, policy, warnings)
                            segment(right, kind, parent, end, b, policy, warnings)
                            right["id"] = identity
                            place(right, kind, start)
                            items.insert(items.index(obj) + 1, right)
                        elif a < start:
                            segment(obj, kind, parent, a, start, policy, warnings)
                        else:
                            segment(obj, kind, parent, end, b, policy, warnings)
                            place(obj, kind, start)
            if set(split_ids) != used_splits:
                fail("split_ids must exactly describe the spanning blocks split by this ripple")
            if op["resize"]:
                doc["project"]["duration_frames"] += delta
    errors = structure_errors(doc, require_managed=True)
    if errors:
        fail("; ".join(errors[:20]), "input.structure")
    subtitle_windows = sorted((s["in"], s["out"]) for s in doc["subtitles"])
    if any(a[1] > b[0] for a, b in zip(subtitle_windows, subtitle_windows[1:])):
        fail("the single virtual subtitle track cannot contain overlapping blocks", "input.structure")
    return doc, edit_summary(document, doc, warnings)
