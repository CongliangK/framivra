"""Reviewable first slice of cooperative POSIX timeline transactions.

The project file alone is current truth. Sidecar evidence has no renderer HEAD.
Only local POSIX cooperating writers/process crashes are in the tested model;
non-cooperating writes after final CAS, NFS, Windows and power loss are not.
"""
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import uuid

from .runtime.fstxn import file_lock
from .timeline_structure import (identity_errors, managed_objects, strict_loads,
                                 structure_errors, valid_id)

_SCHEMA = "framivra.timeline.store/v1"
_OPERATIONS = ("create", "adopt", "replace", "apply", "undo")
# Deliberately no identity, track-name, references, extension paths or nested writes.
_FIELDS = {"source": {"path"}, "video_track": set(), "audio_track": set(),
           "clip": {"dst_in", "dur", "src_in", "src_out", "fade_in_frames", "fade_out_frames",
                    "opacity", "scale", "x", "y"},
           "event": {"in", "out", "src_in", "volume", "pan", "fade_in_frames", "fade_out_frames", "loop"},
           "subtitle": {"in", "out", "text"}}


class StoreError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _fail(code, message):
    raise StoreError(code, message)


def _sha(payload):
    return "sha256:" + hashlib.sha256(payload).hexdigest() if payload is not None else None


def _encode(value):
    # Roundtrip catches non-string keys, unsupported objects, NaN and lone surrogates.
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    if strict_loads(payload) != value:
        _fail("input.json", "input is not a lossless JSON value")
    return payload


def _checkpoint(name):
    """Private fault-injection seam. No environment-controlled production behavior."""


def timeline_store_capabilities():
    return {"schema": _SCHEMA, "ok": True, "operations": list(_OPERATIONS) + ["status", "recover"],
            "apply": {kind: sorted(fields) for kind, fields in _FIELDS.items()},
            "replace": "document values/reordering only; retain current _vtx and complete ID/kind inventory; no add/remove/copy/split/ID changes",
            "unsupported": ["move", "copy", "split", "rename identity/track", "arbitrary JSON Pointer", "UI"],
            "base_revision": "exact file bytes sha256:<hex>; create only uses null",
            "validation_scope": "stored schema/IDs/bounds/EDL/references, not renderability/assets/plugins",
            "guarantee": "local POSIX cooperative threads/processes and process crashes only",
            "unmigrated_writers": "all legacy consumers (including scaffold, ASR, Blender, duck)"}


def _result(operation, **kw):
    return dict(schema=_SCHEMA, operation=operation, ok=True, **kw)


def _canonical(path):
    if os.name != "posix":
        _fail("platform.unsupported", "timeline store currently requires POSIX")
    # Resolve parent components in filesystem order: abspath/normpath would
    # collapse symlink/.. lexically and can silently select a different project.
    # Do not resolve the final basename: leaf symlinks remain forbidden below.
    value = Path(os.path.expanduser(os.fspath(path)))
    parent = value.parent.resolve(strict=True)
    value = parent / value.name
    # Names are not appended to a sidecar name: arbitrary Unicode/long filenames work.
    if not value.name or value.name in (".", ".."):
        _fail("path.invalid", "expected a project file")
    _regular(value, missing=True)
    return value


def _regular(path, *, missing=False, directory=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing:
            return None
        raise
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            _fail("path.alias", "evidence path is not a plain directory: " + str(path))
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        _fail("path.alias", "refuse symlink, non-regular or hardlinked file: " + str(path))
    return info


def _read(path, *, missing=False):
    _regular(path, missing=missing)
    try:
        # No following leaf links even if swapped since lstat; cooperating model otherwise.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        if missing:
            return None
        raise
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            _fail("path.alias", "not an unaliased regular file")
        return stream.read()


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_new(path, payload):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _publish_file(path, payload):
    # A random private staging file and replace; no partially published result marker.
    fd, name = tempfile.mkstemp(prefix=".publish-", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        _sync_dir(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def _location(target):
    return target.parent / (".vtx-store-" + hashlib.sha256(target.name.encode("utf-8")).hexdigest())


@contextlib.contextmanager
def _locked(target):
    home = _location(target)
    _regular(home, missing=True, directory=True)
    if not home.exists():
        home.mkdir(mode=0o700, exist_ok=True)
        _sync_dir(target.parent)
    _regular(home, directory=True)
    lock = home / "store.lock"
    _regular(lock, missing=True)
    # file_lock supplies thread lock -> flock, never delete it or reacquire this path.
    with file_lock(home / "store"):
        _regular(lock)
        manifest = home / "manifest.json"
        expected = {"protocol": 1, "target": target.name}
        if not manifest.exists():
            # Do not bless an orphan history whose target cannot be authenticated.
            if any(p.name != "store.lock" for p in home.iterdir()):
                _fail("recovery_required", "history manifest missing with existing evidence")
            _publish_file(manifest, _encode(expected))
        else:
            try:
                actual = strict_loads(_read(manifest))
            except (OSError, ValueError, TypeError, RecursionError) as error:
                _fail("recovery_required", "invalid history manifest: " + str(error))
            if actual != expected or type(actual.get("protocol")) is not int:
                _fail("recovery_required", "history manifest target/protocol mismatch")
        yield home


def _parse(payload):
    try:
        return strict_loads(payload)
    except (ValueError, UnicodeError, RecursionError) as error:
        _fail("input.json", str(error))


def _check(doc, *, managed=False):
    errors = structure_errors(doc, require_managed=managed)
    if errors:
        _fail("input.structure", "; ".join(errors[:20]))


def _record(home, directory, target):
    """Read *all* published evidence strictly; corruption is never a cache miss."""
    try:
        _regular(directory, directory=True)
        entries = {p.name for p in directory.iterdir()}
        if entries - {"intent.json", "before.bin", "after.bin", "commit.json", "abort.json"}:
            # interrupted marker publication is private and harmless
            if any(not n.startswith(".publish-") for n in entries - {
                    "intent.json", "before.bin", "after.bin", "commit.json", "abort.json"}):
                raise ValueError("unknown evidence entry")
        intent = strict_loads(_read(directory / "intent.json"))
        keys = {"protocol", "target", "op_id", "request", "request_digest", "operation", "before_revision",
                "after_revision", "project_id", "result"}
        if (not isinstance(intent, dict) or set(intent) != keys or intent["protocol"] != 1
                or type(intent["protocol"]) is not int or intent["target"] != target.name
                or not valid_id(intent["op_id"]) or intent["operation"] not in _OPERATIONS
                or directory.name != "op-" + hashlib.sha256(intent["op_id"].encode()).hexdigest()
                or not valid_id(intent["project_id"])
                or not isinstance(intent["result"], dict)
                or not isinstance(intent["request_digest"], str)
                or len(intent["request_digest"]) != 71 or not intent["request_digest"].startswith("sha256:")):
            raise ValueError("invalid intent metadata")
        if (not isinstance(intent["request"], dict)
                or _sha(_encode(intent["request"])) != intent["request_digest"]
                or intent["request"].get("op_id") != intent["op_id"]
                or intent["request"].get("operation") != intent["operation"]
                or intent["request"].get("base_revision") != intent["before_revision"]):
            raise ValueError("request digest/identity mismatch")
        before = _read(directory / "before.bin") if intent["before_revision"] is not None else None
        if before is None and "before.bin" in entries:
            raise ValueError("unexpected before bytes")
        after = _read(directory / "after.bin")
        if _sha(before) != intent["before_revision"] or _sha(after) != intent["after_revision"]:
            raise ValueError("evidence SHA mismatch")
        after_doc = strict_loads(after)
        _check(after_doc, managed=True)
        if after_doc["_vtx"] != {"protocol": 1, "project_id": intent["project_id"], "head": intent["op_id"]}:
            raise ValueError("intent/head mismatch")
        before_doc = strict_loads(before) if before is not None else None
        if before_doc is not None:
            _check(before_doc)
            if "_vtx" in before_doc and before_doc["_vtx"]["project_id"] != intent["project_id"]:
                raise ValueError("project identity changed")
        if (before is None) != (intent["operation"] == "create"):
            raise ValueError("invalid create/before pairing")
        if intent["operation"] == "adopt":
            if before_doc is None or "_vtx" in before_doc:
                raise ValueError("invalid adoption predecessor")
            expected_mapping = {path: obj["id"] for path, obj, kind in managed_objects(after_doc) if kind != "source"}
            if intent["result"].get("id_mapping") != expected_mapping:
                raise ValueError("adoption identity mapping mismatch")
        expected = _result(intent["operation"], op_id=intent["op_id"],
                           request_digest=intent["request_digest"], revision=intent["after_revision"],
                           base_revision=intent["before_revision"], project_id=intent["project_id"])
        # The stored mapping is part of the original result, not re-signed on retry.
        if intent["operation"] == "adopt":
            expected["id_mapping"] = intent["result"].get("id_mapping")
            if not isinstance(expected["id_mapping"], dict):
                raise ValueError("adopt mapping missing")
        if intent["result"] != expected:
            raise ValueError("intent result mismatch")
        committed = "commit.json" in entries
        aborted = "abort.json" in entries
        if committed and aborted:
            raise ValueError("both commit and abort")
        if committed and strict_loads(_read(directory / "commit.json")) != intent["result"]:
            raise ValueError("commit result mismatch")
        if aborted and strict_loads(_read(directory / "abort.json")) != {"op_id": intent["op_id"], "state": "aborted"}:
            raise ValueError("abort result mismatch")
        return dict(intent=intent, before=before, after=after, directory=directory,
                    state="committed" if committed else "aborted" if aborted else "pending")
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
        _fail("recovery_required", "invalid history: " + str(error))


def _history(home, target):
    records = {}
    for directory in sorted(home.iterdir()):
        if directory.name in ("manifest.json", "store.lock") or directory.name.startswith(".staging-"):
            continue
        if not directory.name.startswith("op-"):
            _fail("recovery_required", "unknown history entry: " + directory.name)
        record = _record(home, directory, target)
        op_id = record["intent"]["op_id"]
        if op_id in records:
            _fail("recovery_required", "duplicate operation")
        records[op_id] = record
    return records


def _recover(home, target, records):
    pending = [r for r in records.values() if r["state"] == "pending"]
    if len(pending) > 1:
        _fail("recovery_required", "multiple unresolved intents")
    for rec in pending:
        current = _read(target, missing=True)
        if current == rec["after"]:
            try:
                _sync_dir(target.parent)
                _publish_file(rec["directory"] / "commit.json", _encode(rec["intent"]["result"]))
            except OSError as error:
                failure = StoreError("outcome_unknown", "recovery commit sync failed: " + str(error))
                failure.op_id = rec["intent"]["op_id"]
                raise failure
            rec["state"] = "committed"
        elif current == rec["before"]:
            _publish_file(rec["directory"] / "abort.json",
                          _encode({"op_id": rec["intent"]["op_id"], "state": "aborted"}))
            rec["state"] = "aborted"
        else:
            _fail("recovery_required", "current bytes match neither before nor after; never overwrite")


def _publish_intent(home, target, intent, before, after):
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=home))
    if before is not None:
        _write_new(staging / "before.bin", before)
    _write_new(staging / "after.bin", after)
    _write_new(staging / "intent.json", _encode(intent))
    _sync_dir(staging)
    directory = home / ("op-" + hashlib.sha256(intent["op_id"].encode()).hexdigest())
    os.rename(staging, directory)
    _sync_dir(home)
    return directory


def _adopt(doc, op_id):
    if "_vtx" in doc:
        _fail("input.managed", "already managed; adopt does not rewrite identities")
    errors = identity_errors(doc)
    if errors:
        _fail("input.identity", "; ".join(errors))
    mapping = {}
    for address, obj, kind in managed_objects(doc):
        if kind != "source" and "id" in obj:
            _fail("input.identity", "legacy object has an unowned existing id: " + address)
        if "id" not in obj:
            obj["id"] = "vtx-" + uuid.uuid4().hex
            mapping[address] = obj["id"]
    doc["_vtx"] = {"protocol": 1, "project_id": "project-" + uuid.uuid4().hex, "head": op_id}
    return mapping


def _apply(doc, changes):
    if not isinstance(changes, list) or not changes:
        _fail("input.changes", "changes must be a nonempty list")
    objects = {obj["id"]: (obj, kind) for _, obj, kind in managed_objects(doc)}
    for change in changes:
        if not isinstance(change, dict) or set(change) != {"id", "set"}:
            _fail("input.changes", "each change is exactly {id, set}")
        if not valid_id(change["id"]) or change["id"] not in objects:
            _fail("input.identity", "unknown object id")
        obj, kind = objects[change["id"]]
        values = change["set"]
        if not isinstance(values, dict) or not values or not set(values) <= _FIELDS[kind]:
            _fail("input.changes", "field not supported for " + kind)
        obj.update(copy.deepcopy(values))


def _same_ids(before, after):
    old = {o["id"]: kind for _, o, kind in managed_objects(before)}
    new = {o["id"]: kind for _, o, kind in managed_objects(after)}
    if old != new:
        _fail("input.identity", "replace must retain the complete ID/kind inventory; add/remove/copy/split are not supported")


def timeline_transaction(path, request):
    """Execute {operation, op_id, base_revision, document? / changes? / head?}.

    create requires complete explicit IDs (not adoption). replace requires a
    managed document with unchanged metadata and complete ID/kind inventory;
    only values/reordering, no add/remove/copy/split/ID changes. Replacement values
    intentionally include explicit extension values. apply only writes listed fields.
    Returned error codes distinguish conflict, aborted, recovery_required and
    outcome_unknown. Retrying the same op never applies an operation twice.
    """
    operation = request.get("operation") if isinstance(request, dict) else None
    op_id = request.get("op_id") if isinstance(request, dict) else None
    replacing = False
    try:
        if not isinstance(request, dict) or operation not in _OPERATIONS or not valid_id(op_id):
            _fail("input.request", "expected supported operation and valid op_id")
        allowed = {"operation", "op_id", "base_revision"}
        allowed |= {"document"} if operation in ("create", "replace") else {"changes"} if operation == "apply" else {"head"} if operation == "undo" else set()
        if set(request) != allowed:
            _fail("input.request", "missing or unknown request fields")
        request_bytes = _encode(request)
        request = strict_loads(request_bytes)  # freeze caller-owned mutable input before taking the lock
        digest = _sha(request_bytes)
        target = _canonical(path)
        with _locked(target) as home:
            _checkpoint("locked")
            records = _history(home, target)
            _recover(home, target, records)
            _checkpoint("recovered")
            before = _read(target, missing=True)
            if before is not None:
                doc = _parse(before)
                _check(doc)
            else:
                doc = None
            _checkpoint("read")
            if op_id in records:
                record = records[op_id]
                if record["intent"]["request_digest"] != digest:
                    _fail("idempotency.conflict", "same op_id has a different payload")
                if record["state"] == "aborted":
                    _fail("aborted", "intent was not committed; use a new op_id to try again")
                return dict(record["intent"]["result"], current_revision=_sha(before), replayed=True)
            revision = _sha(before)
            if request["base_revision"] != revision:
                _fail("conflict", "base_revision does not match current file bytes")
            mapping = None
            if operation == "create":
                if before is not None:
                    _fail("create.exists", "create-only refuses an existing target")
                after_doc = copy.deepcopy(request["document"])
                if not isinstance(after_doc, dict) or "_vtx" in after_doc:
                    _fail("input.document", "create document must be an object without _vtx")
                after_doc["_vtx"] = {"protocol": 1, "project_id": "project-" + uuid.uuid4().hex, "head": op_id}
            else:
                if doc is None:
                    _fail("input.missing", "target missing")
                after_doc = copy.deepcopy(doc)
                if operation == "adopt":
                    mapping = _adopt(after_doc, op_id)
                else:
                    _check(doc, managed=True)
                    if operation == "replace":
                        after_doc = copy.deepcopy(request["document"])
                        if not isinstance(after_doc, dict) or after_doc.get("_vtx") != doc["_vtx"]:
                            _fail("input.identity", "replace must retain current _vtx")
                        _check(after_doc, managed=True)
                        _same_ids(doc, after_doc)
                    elif operation == "apply":
                        _apply(after_doc, request["changes"])
                    elif operation == "undo":
                        head = request["head"]
                        if head != doc["_vtx"]["head"] or head not in records:
                            _fail("undo.conflict", "undo accepts only the current managed head")
                        previous = records[head]
                        if previous["state"] != "committed" or previous["after"] != before:
                            _fail("undo.conflict", "current file no longer equals committed head bytes")
                        if previous["before"] is None or previous["intent"]["operation"] == "adopt":
                            _fail("undo.unsupported", "cannot undo create/adopt in this first slice")
                        after_doc = _parse(previous["before"])
                        _check(after_doc, managed=True)
                    after_doc["_vtx"]["head"] = op_id
            _check(after_doc, managed=True)
            after = _encode(after_doc)
            _checkpoint("validated")
            result = _result(operation, op_id=op_id, request_digest=digest, revision=_sha(after),
                             base_revision=revision, project_id=after_doc["_vtx"]["project_id"])
            if mapping is not None:
                result["id_mapping"] = mapping
            intent = dict(protocol=1, target=target.name, op_id=op_id, request_digest=digest,
                          operation=operation, before_revision=revision, after_revision=_sha(after),
                          project_id=after_doc["_vtx"]["project_id"], request=request, result=result)
            directory = _publish_intent(home, target, intent, before, after)
            _checkpoint("intent_published")
            fd, name = tempfile.mkstemp(prefix=".vtx-timeline-", suffix=".tmp", dir=target.parent)
            staging = Path(name)
            try:
                with os.fdopen(fd, "wb") as stream:
                    # Retain the old file's basic permissions, never its aliases.
                    if before is not None:
                        os.fchmod(stream.fileno(), stat.S_IMODE(_regular(target).st_mode))
                    stream.write(after)
                    stream.flush()
                    os.fsync(stream.fileno())
                _checkpoint("staged")
                if _read(target, missing=True) != before:
                    _fail("recovery_required", "final CAS conflict; unresolved intent retained")
                _checkpoint("final_cas")
                replacing = True
                os.replace(staging, target)  # linearization point
                _checkpoint("replaced")
                _sync_dir(target.parent)
                _checkpoint("parent_synced")
                _publish_file(directory / "commit.json", _encode(result))
                _checkpoint("committed")
                return dict(result, current_revision=_sha(after), replayed=False)
            finally:
                staging.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
        code = "outcome_unknown" if replacing else getattr(error, "code", "input.error")
        return dict(schema=_SCHEMA, operation=operation, op_id=getattr(error, "op_id", op_id), ok=False, code=code,
                    message=str(error), retry="retry identical op_id/request or recover; never assume uncommitted" if code == "outcome_unknown" else None)


def timeline_store_status(path, *, recover=False):
    """Lock and inspect evidence; recover=True resolves pending intents only.

    status may create the lock/manifest, but never modifies the timeline or
    resolves an intent. recover never re-applies operations or rolls forward.
    """
    operation = "recover" if recover else "status"
    try:
        target = _canonical(path)
        with _locked(target) as home:
            records = _history(home, target)
            if recover:
                _recover(home, target, records)
            current = _read(target, missing=True)
            doc = _parse(current) if current is not None else None
            if doc is not None:
                _check(doc)
            return _result(operation, current_revision=_sha(current),
                           head=doc.get("_vtx", {}).get("head") if doc else None,
                           evidence_path=str(home), pending=any(r["state"] == "pending" for r in records.values()),
                           operations=[dict(op_id=k, state=v["state"]) for k, v in sorted(records.items())])
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
        return dict(schema=_SCHEMA, operation=operation, ok=False, op_id=getattr(error, "op_id", None),
                    code=getattr(error, "code", "input.error"), message=str(error))
