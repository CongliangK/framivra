"""Pure v2 policy for the single timeline store; no locks or publisher here.

Protocol 1 is intentionally not normalized through this module. Exact imports
are create-only artifacts, never an overwrite bypass for live projects.
"""
import copy
from typing import Literal, Optional, TypedDict

from .timeline_structure import json_equal, managed_objects, strict_loads, structure_errors, valid_id
from .timeline_edit import edit_summary as _v1_edit_summary, reference_edges, transform_timeline, window

SCHEMA = "framivra.timeline.store/v2"


class TimelineSnapshot(TypedDict):
    raw_bytes: Optional[bytes]
    revision: Optional[str]
    document: Optional[dict]
    identity_mode: Literal["managed", "preserve-unmanaged"]


def _kernel():
    # Runtime import avoids a second authority or import-time cycle.
    from . import timeline_store
    return timeline_store


def identity_mode(doc):
    return "managed" if isinstance(doc, dict) and "_vtx" in doc else "preserve-unmanaged"


def check_document(doc):
    errors = structure_errors(doc, preserve_unmanaged=True)
    if errors:
        _kernel()._fail("input.structure", "; ".join(errors[:20]))


def read_timeline_for_write(path) -> TimelineSnapshot:
    """One raw read, strict writable document, no history/lock/bootstrap/backup.

    Missing is a legitimate create snapshot. Existing JSON null is not missing.
    Raises StoreError/OSError on invalid input; never repairs or adopts it.
    """
    s = _kernel()
    raw = s._read(s._canonical(path), missing=True)
    doc = s._parse(raw) if raw is not None else None
    if raw is not None:
        check_document(doc)
    return TimelineSnapshot(raw_bytes=raw, revision=s._sha(raw), document=doc,
                            identity_mode=identity_mode(doc))


def freeze_request(request, *, evidence=False):
    """Freeze a v2 request. Bytes become strict UTF-8 text in the signed request.

    CLI resolves payload_file ONCE before calling the kernel. Journal contains
    text plus after.bin, never a mutable pathname. No alternate JSON parser.
    """
    s = _kernel()
    if not isinstance(request, dict) or type(request.get("protocol")) is not int or request["protocol"] != 2:
        s._fail("input.request", "expected request protocol 2")
    request = dict(request)
    common = {"protocol", "operation", "op_id", "base_revision", "identity_mode", "payload_mode", "purpose"}
    op, mode, payload, purpose = (request.get(k) for k in ("operation", "identity_mode", "payload_mode", "purpose"))
    if not valid_id(request.get("op_id")) or mode not in ("managed", "preserve-unmanaged"):
        s._fail("input.request", "invalid op_id or identity_mode")
    if payload == "exact-import":
        if op != "create" or purpose not in ("create", "frozen-copy", "detached-copy"):
            s._fail("input.policy", "exact-import is create-only: create/frozen-copy/detached-copy; no live overwrite or snapshot target")
        key = "raw_utf8" if evidence else "raw_bytes"
        if set(request) != common | {key}:
            s._fail("input.request", "exact-import requires exactly raw_bytes (CLI: payload_file)")
        raw = request.pop(key)
        if evidence:
            if not isinstance(raw, str):
                s._fail("input.request", "invalid frozen raw_utf8")
        else:
            if not isinstance(raw, bytes):
                s._fail("input.request", "raw_bytes must be frozen bytes")
            raw = raw.decode("utf-8")
        request["raw_utf8"] = raw
    elif payload == "document":
        extra = {"document"} if op in ("create", "replace") else {"changes"} if op == "apply" else {"operations"} if op == "edit" else {"owner", "owned_ids", "new_ids", "operations"} if op == "reconcile" else {"head"} if op == "undo" and mode == "managed" else {"undo_of"} if op == "undo" else None
        if extra is None or set(request) != common | extra:
            s._fail("input.request", "missing/unknown v2 operation fields")
        expected_purpose = "create" if op == "create" else "edit" if mode == "managed" else "legacy-write"
        if purpose != expected_purpose or (mode == "preserve-unmanaged" and op not in ("create", "replace", "undo")):
            s._fail("input.policy", "unsupported purpose/identity/operation combination")
    else:
        s._fail("input.request", "unknown payload_mode")
    encoded = s._encode(request)
    return strict_loads(encoded), s._sha(encoded)


def edit_summary(before, after, warnings=()):
    """v2 JSON-value change audit, without reinterpreting frozen v1 summaries.

    Keep the existing reference/warning diagnostics. Recompute changed IDs and
    their causal ranges because Python deep equality in v1 misses bool/number
    changes, including changed parents, shared sources and project extensions.
    """
    summary = _v1_edit_summary(before, after, warnings)
    old, new = ({obj["id"]: (path, obj, kind) for path, obj, kind in managed_objects(doc)}
                for doc in (before, after))
    changed = {i for i in old.keys() & new.keys()
               if old[i][0] != new[i][0] or not json_equal(old[i][1], new[i][1])}
    affected = []
    def add(identity, reason=None):
        item = {"id": identity}
        if reason:
            item["reason"] = reason
        for label, objects in (("before", old), ("after", new)):
            if identity in objects and objects[identity][2] in ("clip", "event", "subtitle"):
                item[label] = list(window(objects[identity][1], objects[identity][2]))
        if "before" in item or "after" in item:
            affected.append(item)
    for i in sorted(changed | (old.keys() ^ new.keys())):
        add(i)
    changed_sources = {i for i in changed if old[i][2] == "source"}
    impacted = {e["id"] for doc in (before, after) for e in reference_edges(doc)
                if e["field"] == "src" and e["value"] in changed_sources}
    project_changed = not json_equal(before["project"], after["project"])
    for objects in (old, new):
        for i, (_, obj, kind) in objects.items():
            child = "clips" if kind == "video_track" else "events" if kind == "audio_track" else None
            if i in changed and child:
                impacted.update(b["id"] for b in obj[child])
            if kind in ("clip", "event", "subtitle") and project_changed:
                impacted.add(i)
    for i in sorted(impacted - {item["id"] for item in affected}):
        add(i, "shared source, parent track or project changed")
    summary.update(changed_ids=sorted(changed), affected_ranges=affected)
    return summary


def reconciliation(before, after, request):
    """Bounded stable-ID owner audit; no index/path/text matching or generated IDs.

    Track containers are checked separately from their children. Every changed,
    removed or relocated non-owned object is rejected, including implicit refs.
    Root/project data and ordering of non-owned siblings must remain untouched.
    """
    s = _kernel()
    owner, owned, new = (request[k] for k in ("owner", "owned_ids", "new_ids"))
    if not isinstance(owner, str) or not owner.strip():
        s._fail("reconcile.mapping", "explicit nonempty owner required")
    for values in (owned, new):
        if not isinstance(values, list) or any(not valid_id(v) for v in values) or len(values) != len(set(values)):
            s._fail("reconcile.mapping", "owned_ids/new_ids must be unique stable IDs; no positional mapping")
    old = {obj["id"]: (obj, kind) for _, obj, kind in managed_objects(before)}
    current = {obj["id"]: (obj, kind) for _, obj, kind in managed_objects(after)}
    if not set(owned) <= old.keys() or set(new) & old.keys() or set(owned) & set(new):
        s._fail("reconcile.mapping", "unknown owned ID or reused new ID")
    if set(current) - set(old) != set(new) or not set(old) - set(current) <= set(owned):
        s._fail("reconcile.scope", "creates/deletes must be explicitly owned")
    def nonowned_projection(doc):
        def obj_projection(obj, child=None):
            value = copy.deepcopy(obj)
            children = value.pop(child, None) if child else None
            own = value["id"] in owned or value["id"] in new
            shell = None if own else value
            if children is not None:
                parts = [p for p in (obj_projection(c) for c in children) if p is not None]
                # Even an owned container cannot silently relocate non-owned
                # children to another owned container with an identical shell.
                return [value["id"], shell, parts] if shell is not None or parts else None
            return shell
        root = {k: copy.deepcopy(v) for k, v in doc.items()
                if k not in ("sources", "video_tracks", "audio_tracks", "subtitles", "_vtx")}
        for key, child in (("sources", None), ("video_tracks", "clips"), ("audio_tracks", "events"), ("subtitles", None)):
            root[key] = [p for p in (obj_projection(o, child) for o in doc[key]) if p is not None]
        return root
    if not json_equal(nonowned_projection(before), nonowned_projection(after)):
        s._fail("reconcile.scope", "semantic batch changes non-owned data or placement")
    for key in set(old) & set(current):
        if old[key][1] != current[key][1]:
            s._fail("reconcile.mapping", "object kind changed")
        # A reconciliation is not a document replacement: existing unknown deep
        # fields survive through the reviewed semantic transformer.
    summary = edit_summary(before, after)
    return dict(owner=owner, base_revision=request["base_revision"],
                changes=summary["changed_ids"], creates=summary["created_ids"],
                deletes=summary["deleted_ids"], preserved_ids=sorted(set(old) & set(current)),
                warnings=summary["warnings"])


def prepare(request, before, doc, records):
    """Pure policy/transform after replay and CAS; return exact bytes + result extras."""
    s = _kernel()
    op, mode = request["operation"], request["identity_mode"]
    extras = {}
    if op == "create":
        if before is not None:
            s._fail("create.exists", "create-only refuses an existing target")
        if request["payload_mode"] == "exact-import":
            after = request["raw_utf8"].encode("utf-8")
            after_doc = s._parse(after)
            check_document(after_doc)
            if identity_mode(after_doc) != mode:
                s._fail("input.identity", "exact payload identity_mode mismatch")
            if mode == "managed" and request["purpose"] != "detached-copy":
                s._fail("input.policy", "exact managed bytes require detached-copy; no invented new identity/history")
            if mode != "managed" and request["purpose"] == "detached-copy":
                s._fail("input.policy", "detached-copy requires managed payload")
            return after, after_doc, extras
        after_doc = copy.deepcopy(request["document"])
        if not isinstance(after_doc, dict) or "_vtx" in after_doc:
            s._fail("input.document", "create document must be an object without _vtx")
        if mode == "managed":
            # Stable across plan and pre-intent retries for a fixed request.
            project = "project-" + s.hashlib.sha256((request["op_id"] + ":" + s._sha(s._encode(request))).encode()).hexdigest()[:32]
            after_doc["_vtx"] = {"protocol": 1, "project_id": project, "head": request["op_id"]}
    else:
        if before is None:
            s._fail("input.missing", "target missing")
        if identity_mode(doc) != mode:
            s._fail("input.identity", "request mode must match existing document; no implicit adopt/downgrade")
        after_doc = copy.deepcopy(doc)
        if op == "replace":
            after_doc = copy.deepcopy(request["document"])
            if not isinstance(after_doc, dict) or identity_mode(after_doc) != mode:
                s._fail("input.identity", "replace cannot change identity_mode")
            if mode == "managed":
                if not json_equal(after_doc.get("_vtx"), doc["_vtx"]):
                    s._fail("input.identity", "replace must retain current _vtx")
                s._check(after_doc, managed=True)
                s._same_ids(doc, after_doc)
        elif op == "apply":
            s._apply(after_doc, request["changes"])
        elif op in ("edit", "reconcile"):
            after_doc, original_summary = transform_timeline(doc, request["operations"])
            extras["edit"] = edit_summary(doc, after_doc, original_summary["warnings"])
            if op == "reconcile":
                extras["reconciliation"] = reconciliation(doc, after_doc, request)
        elif op == "undo":
            selected = request["head"] if mode == "managed" else request["undo_of"]
            if mode == "managed" and selected != doc["_vtx"]["head"]:
                s._fail("undo.conflict", "undo accepts only the current managed head")
            previous = records.get(selected) if isinstance(selected, str) else None
            if previous is None or previous["state"] != "committed" or previous["after"] != before:
                s._fail("undo.conflict", "explicit operation must be committed and its after bytes equal current")
            if (previous["before"] is None or previous["intent"]["operation"] == "adopt"
                    or previous["intent"].get("payload_mode") == "exact-import"):
                s._fail("undo.unsupported", "cannot undo create/adopt/exact artifact")
            after = previous["before"]
            after_doc = s._parse(after)
            check_document(after_doc)
            if identity_mode(after_doc) != mode:
                s._fail("undo.unsupported", "cannot undo across identity modes")
            if mode == "preserve-unmanaged":
                return after, after_doc, extras
            extras["edit"] = edit_summary(doc, after_doc)
        if mode == "managed":
            after_doc["_vtx"]["head"] = request["op_id"]
    check_document(after_doc)
    if identity_mode(after_doc) != mode:
        s._fail("input.identity", "output identity_mode mismatch")
    return s._encode(after_doc), after_doc, extras


def result(request, digest, before, after, after_doc, extras):
    s = _kernel()
    return dict(schema=SCHEMA, operation=request["operation"], ok=True,
                op_id=request["op_id"], request_digest=digest,
                base_revision=s._sha(before), revision=s._sha(after),
                project_id=after_doc.get("_vtx", {}).get("project_id"),
                identity_mode=request["identity_mode"], payload_mode=request["payload_mode"],
                purpose=request["purpose"], detached_copy=request["purpose"] == "detached-copy", **extras)


def intent(target, request, digest, before, after, doc, result):
    s = _kernel()
    before_doc = s._parse(before) if before is not None else None
    return dict(protocol=2, target=target.name, op_id=request["op_id"], request=request,
                request_digest=digest, operation=request["operation"], before_revision=s._sha(before),
                after_revision=s._sha(after), project_id=doc.get("_vtx", {}).get("project_id"),
                identity_mode=request["identity_mode"], payload_mode=request["payload_mode"], purpose=request["purpose"],
                before_identity_mode=identity_mode(before_doc) if before is not None else None,
                after_identity_mode=identity_mode(doc), result=result)


def record(directory, target, entries, metadata):
    """Validate protocol2 frozen evidence, never replay a transformer in recovery."""
    s = _kernel()
    keys = {"protocol", "target", "op_id", "request", "request_digest", "operation", "before_revision", "after_revision", "project_id", "result", "identity_mode", "payload_mode", "purpose", "before_identity_mode", "after_identity_mode"}
    if set(metadata) != keys or type(metadata["protocol"]) is not int or metadata["protocol"] != 2:
        raise ValueError("invalid v2 intent keys/protocol")
    req, digest = freeze_request(metadata["request"], evidence=True)
    if not isinstance(metadata["result"], dict):
        raise ValueError("invalid v2 result")
    before = s._read(directory / "before.bin") if metadata["before_revision"] is not None else None
    if before is None and "before.bin" in entries:
        raise ValueError("unexpected before bytes")
    after = s._read(directory / "after.bin")
    doc = s._parse(after)
    check_document(doc)
    old = s._parse(before) if before is not None else None
    if before is not None:
        check_document(old)
    if (before is None) != (req["operation"] == "create") or req["base_revision"] != s._sha(before):
        raise ValueError("invalid v2 create/base pairing")
    if identity_mode(doc) != req["identity_mode"] or (before is not None and identity_mode(old) != req["identity_mode"]):
        raise ValueError("v2 identity mode changed")
    if req["payload_mode"] == "exact-import":
        if after != req["raw_utf8"].encode("utf-8") or (identity_mode(doc) == "managed") != (req["purpose"] == "detached-copy"):
            raise ValueError("invalid exact-import bytes/purpose")
    elif req["identity_mode"] == "managed":
        if doc["_vtx"]["head"] != req["op_id"] or (old is not None and old["_vtx"]["project_id"] != doc["_vtx"]["project_id"]):
            raise ValueError("v2 managed head/project mismatch")
        if req["operation"] == "replace":
            s._same_ids(old, doc)
    extras = {}
    if req["operation"] in ("edit", "reconcile") or req["operation"] == "undo" and req["identity_mode"] == "managed":
        summary = metadata["result"].get("edit", {})
        if not isinstance(summary, dict):
            raise ValueError("invalid v2 edit summary")
        warnings = summary.get("warnings")
        if not isinstance(warnings, list) or any(not isinstance(w, str) for w in warnings):
            raise ValueError("invalid v2 edit warnings")
        extras["edit"] = edit_summary(old, doc, warnings)
    if req["operation"] == "reconcile":
        extras["reconciliation"] = reconciliation(old, doc, req)
    expected_result = result(req, digest, before, after, doc, extras)
    if not json_equal(metadata, intent(target, req, digest, before, after, doc, expected_result)):
        raise ValueError("v2 intent/result/request identity mismatch")
    if directory.name != "op-" + s.hashlib.sha256(req["op_id"].encode()).hexdigest():
        raise ValueError("v2 operation directory mismatch")
    committed, aborted = "commit.json" in entries, "abort.json" in entries
    if committed and aborted:
        raise ValueError("both commit and abort")
    if committed and not json_equal(strict_loads(s._read(directory / "commit.json")), expected_result):
        raise ValueError("commit result mismatch")
    if aborted and not json_equal(strict_loads(s._read(directory / "abort.json")), {"op_id": req["op_id"], "state": "aborted"}):
        raise ValueError("abort result mismatch")
    return dict(intent=metadata, before=before, after=after, directory=directory,
                state="committed" if committed else "aborted" if aborted else "pending")
