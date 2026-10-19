"""Two explicit, stdout-JSON entry points. Invoke Python with -B."""
import argparse
import json
import math
import os
import sys

from .inventory import scan
from .plan import build_plan

SCHEMA = "framivra.disk-lifecycle-request/1"
ROLES = {"input", "music", "sdk", "download", "gfx", "encode", "audio", "final",
         "evidence", "history", "shared", "unknown"}
PURPOSES = {"NEW_ISOLATED_DERIVED_PNG", "BASELINE", "EXPERIMENT", "PERFORMANCE_COMPARISON",
            "FAILURE_DIAGNOSTIC", "PENDING_INDEPENDENT_REVIEW", "SHARED", "OTHER", "UNKNOWN"}
RESERVE_KINDS = {"LOGS", "DIAGNOSTICS", "SUMMARY", "SAFETY_MARGIN"}


class RequestError(ValueError):
    """Invalid request; no inventory has been started."""


def _object(value, allowed, required=()):
    if not isinstance(value, dict):
        raise RequestError("expected JSON object")
    if set(value) - set(allowed) or set(required) - set(value):
        raise RequestError("unknown or missing object fields")


def _text(value):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise RequestError("expected nonempty text without NUL")


def _list(value):
    if not isinstance(value, list):
        raise RequestError("expected JSON array")


def _enum(value, choices):
    if not isinstance(value, str) or value not in choices:
        raise RequestError("invalid enum value")


def _bool(value):
    if type(value) is not bool:
        raise RequestError("expected boolean")


def _unicode_scalars(value):
    """Reject unpaired JSON surrogate escapes before scanning or UTF-8 output."""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if any(0xD800 <= ord(character) <= 0xDFFF for character in item):
                raise RequestError("request strings must contain Unicode scalar values")
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


def validate_request(request):
    """Validate all references and fields before any filesystem enumeration."""
    _unicode_scalars(request)
    _object(request, {"schema", "project_id", "run_id", "roots", "retain_paths", "hold_reasons",
                      "code_ref", "ownership", "delivery_context", "reserves", "max_entries", "max_scan_seconds"},
            {"schema", "project_id", "run_id", "roots", "retain_paths", "hold_reasons"})
    if request["schema"] != SCHEMA:
        raise RequestError("unsupported request schema")
    for name in ("project_id", "run_id"):
        _text(request[name])
    _list(request["roots"])
    if not request["roots"]:
        raise RequestError("at least one explicit root is required")
    roots = set()
    for root in request["roots"]:
        _object(root, {"id", "path", "role"}, {"id", "path", "role"})
        _text(root["id"])
        _text(root["path"])
        _enum(root["role"], ROLES)
        if root["id"] in roots:
            raise RequestError("duplicate root id")
        roots.add(root["id"])
        if not os.path.isabs(root["path"]) or ".." in root["path"].split("/"):
            raise RequestError("root requires an explicit absolute path")

    def ref(value):
        _object(value, {"root_id", "relative_path"}, {"root_id", "relative_path"})
        _text(value["root_id"])
        _text(value["relative_path"])
        if value["root_id"] not in roots:
            raise RequestError("ObjectRef names an undeclared root")
        path = value["relative_path"]
        if path != "." and (path.startswith("/") or "\\" in path or
                            any(part in ("", ".", "..") for part in path.split("/"))):
            raise RequestError("ObjectRef requires an ordinary POSIX relative path")

    def refs(values):
        _list(values)
        for value in values:
            ref(value)

    def status(value, choices):
        _object(value, {"status", "evidence_refs"})
        if "status" in value:
            _enum(value["status"], choices)
        if "evidence_refs" in value:
            refs(value["evidence_refs"])

    refs(request["retain_paths"])
    _list(request["hold_reasons"])
    for reason in request["hold_reasons"]:
        _text(reason)
    if "code_ref" in request:
        _object(request["code_ref"], {"commit", "tree"})
        for value in request["code_ref"].values():
            _text(value)
    if "ownership" in request:
        _list(request["ownership"])
        for record in request["ownership"]:
            _object(record, {"object", "owner_run_id", "purpose", "rebuildable", "activity", "references", "evidence_refs"}, {"object"})
            ref(record["object"])
            if "owner_run_id" in record:
                _text(record["owner_run_id"])
            if "purpose" in record:
                _enum(record["purpose"], PURPOSES)
            if "rebuildable" in record:
                _bool(record["rebuildable"])
            if "activity" in record:
                status(record["activity"], {"DECLARED_INACTIVE", "ACTIVE", "UNKNOWN"})
            if "references" in record:
                status(record["references"], {"DECLARED_NONE", "PRESENT", "UNKNOWN"})
            if "evidence_refs" in record:
                refs(record["evidence_refs"])
    if "delivery_context" in request:
        delivery = request["delivery_context"]
        _object(delivery, {"run_id", "final", "verification", "finish", "notification", "evidence_refs"})
        if "run_id" in delivery:
            _text(delivery["run_id"])
        if "final" in delivery:
            ref(delivery["final"])
        if "evidence_refs" in delivery:
            refs(delivery["evidence_refs"])
        if "verification" in delivery:
            status(delivery["verification"], {"PASS", "FAIL", "CANCELLED", "PENDING", "UNKNOWN"})
        if "finish" in delivery:
            finish = delivery["finish"]
            _object(finish, {"required", "status", "evidence_refs", "reason"})
            if "required" in finish:
                _bool(finish["required"])
            if "status" in finish:
                _enum(finish["status"], {"PASS", "NOT_REQUIRED", "FAIL", "CANCELLED", "PENDING", "UNKNOWN"})
            if "reason" in finish:
                # An empty reason is valid input but cannot satisfy the gate.
                if not isinstance(finish["reason"], str):
                    raise RequestError("finish reason must be text")
            if "evidence_refs" in finish:
                refs(finish["evidence_refs"])
        if "notification" in delivery:
            notice = delivery["notification"]
            _object(notice, {"status", "reference"})
            if "status" in notice:
                _enum(notice["status"], {"SENT_WITH_REFERENCE", "FAILED", "CANCELLED", "PENDING", "UNKNOWN"})
            if "reference" in notice and not isinstance(notice["reference"], str):
                raise RequestError("notification reference must be text")
    if "reserves" in request:
        _list(request["reserves"])
        for reserve in request["reserves"]:
            _object(reserve, {"reserve_id", "scope_root_id", "kind", "bytes", "rationale"},
                    {"reserve_id", "scope_root_id", "kind"})
            _text(reserve["reserve_id"])
            _text(reserve["scope_root_id"])
            if reserve["scope_root_id"] not in roots:
                raise RequestError("reserve names an undeclared root")
            _enum(reserve["kind"], RESERVE_KINDS)
            if "bytes" in reserve and (type(reserve["bytes"]) is not int or reserve["bytes"] < 0):
                raise RequestError("reserve bytes must be a nonnegative integer")
            if "rationale" in reserve:
                _text(reserve["rationale"])
    if "max_entries" in request and (type(request["max_entries"]) is not int or request["max_entries"] <= 0):
        raise RequestError("max_entries must be a positive integer")
    if "max_scan_seconds" in request:
        limit = request["max_scan_seconds"]
        if type(limit) not in (int, float) or limit <= 0:
            raise RequestError("max_scan_seconds must be finite and positive")
        try:
            finite = math.isfinite(limit)
        except OverflowError:
            finite = False
        if not finite:
            raise RequestError("max_scan_seconds must be finite and positive")
    return request


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RequestError("duplicate JSON key")
        result[key] = value
    return result


def _constant(_value):
    raise RequestError("nonfinite JSON value")


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise RequestError(message)


def main(argv=None):
    parser = _Parser(add_help=False, allow_abbrev=False)
    parser.add_argument("command", choices=("inventory", "delivery-cleanup-plan"))
    parser.add_argument("--request", required=True)
    try:
        args = parser.parse_args(argv)
        with open(args.request, "r", encoding="utf-8") as stream:
            request = validate_request(json.load(stream, object_pairs_hook=_pairs, parse_constant=_constant))
    except (RequestError, OSError, UnicodeError, ValueError, RecursionError) as error:
        result = {"schema": "framivra.disk-lifecycle-error/1", "status": "REQUEST_ERROR",
                  "error": str(error), "scan_started": False, "execution_eligible": False,
                  "planned_operations": []}
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        print("disk-lifecycle: invalid request", file=sys.stderr)
        return 2
    observed = scan(request)
    result = observed if args.command == "inventory" else build_plan(request, observed)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0 if result["complete"] else 3


if __name__ == "__main__":
    sys.exit(main())
