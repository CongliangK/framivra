"""Read-only classification of caller declarations; never a deletion capability."""
import ast
from collections import Counter, defaultdict
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import stat

TABLE_NAMES = ("OUT_PROTECTED_NAMES", "OUT_PROTECTED_DIRS", "OUT_PROTECTED_PATTERNS")
PROTECTED_ROLES = {"input", "music", "sdk", "download", "final", "evidence", "history", "shared"}
HELD_PURPOSES = {"BASELINE", "EXPERIMENT", "PERFORMANCE_COMPARISON", "FAILURE_DIAGNOSTIC", "PENDING_INDEPENDENT_REVIEW"}
RESERVE_KINDS = ("LOGS", "DIAGNOSTICS", "SUMMARY", "SAFETY_MARGIN")
DECLARED = "SUPPLIED_NOT_REVERIFIED"


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def parse_protection_source(text):
    """Inspect a narrow AST contract, never evaluate or load production code."""
    result = {"relative_path": "vtx/hygiene.py", "sha256": hashlib.sha256(text.encode()).hexdigest(),
              "adapter_contract_version": "out-protected-ast/1", "parse_status": "UNKNOWN",
              "tables": None, "reason": None}
    try:
        module = ast.parse(text)
        tables = {}
        for name in TABLE_NAMES:
            definitions = [node for node in module.body if isinstance(node, ast.Assign)
                           and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                           and node.targets[0].id == name]
            stores = [node for node in ast.walk(module) if isinstance(node, ast.Name)
                      and node.id == name and isinstance(node.ctx, (ast.Store, ast.Del))]
            if len(definitions) != 1 or len(stores) != 1:
                raise ValueError("missing, duplicate or rebound protection table: " + name)
            rhs = definitions[0].value
            if not isinstance(rhs, ast.Tuple) or not all(isinstance(x, ast.Constant) and type(x.value) is str for x in rhs.elts):
                raise ValueError("protection table must be a literal string tuple: " + name)
            tables[name] = [x.value for x in rhs.elts]
        functions = [node for node in ast.walk(module) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and node.name == "out_protected"]
        if len(functions) != 1 or functions[0] not in module.body or not isinstance(functions[0], ast.FunctionDef):
            raise ValueError("unexpected out_protected definition")
        function = functions[0]
        body = function.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body = body[1:]
        expected = ast.parse('def out_protected(name: str) -> bool:\n'
                             '    return (name in OUT_PROTECTED_NAMES or name in OUT_PROTECTED_DIRS\n'
                             '            or any(fnmatch.fnmatch(name, p) for p in OUT_PROTECTED_PATTERNS))').body[0]
        if (function.decorator_list or getattr(function, "type_params", []) or
                ast.dump(function.args) != ast.dump(expected.args) or
                ast.dump(function.returns) != ast.dump(expected.returns) or
                len(body) != 1 or ast.dump(body[0]) != ast.dump(expected.body[0])):
            raise ValueError("out_protected predicate/signature changed")
        guarded = set(TABLE_NAMES) | {"out_protected", "fnmatch", "any"}
        imports = []
        # Match nodes are absent on the repository's Python 3.9 minimum.
        match_captures = tuple(getattr(ast, name) for name in ("MatchAs", "MatchStar")
                               if hasattr(ast, name))
        for node in ast.walk(module):
            # These bindings are string attributes, not ast.Name(Store).
            if isinstance(node, ast.ExceptHandler) and node.name in guarded:
                raise ValueError("guarded exception binding")
            if isinstance(node, match_captures) and node.name in guarded:
                raise ValueError("guarded match capture binding")
            if isinstance(node, getattr(ast, "MatchMapping", ())) and node.rest in guarded:
                raise ValueError("guarded match mapping binding")
            if isinstance(node, getattr(ast, "type_param", ())) and node.name in guarded:
                raise ValueError("guarded type parameter binding")
            if isinstance(node, ast.Name) and node.id in {"out_protected", "fnmatch", "any"} and isinstance(node.ctx, (ast.Store, ast.Del)):
                raise ValueError("predicate dependency rebound")
            if isinstance(node, ast.arg) and node.arg in guarded:
                raise ValueError("guarded name shadowed")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in guarded and node is not function:
                raise ValueError("guarded name redefined")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.name == "*":
                        raise ValueError("wildcard import has unknown protection bindings")
                    binding = alias.asname or alias.name.split(".")[0]
                    if binding in guarded:
                        if not isinstance(node, ast.Import) or alias.name != "fnmatch" or alias.asname is not None:
                            raise ValueError("unexpected protection dependency import")
                        imports.append(node)
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)) and isinstance(node.value, ast.Name) and node.value.id in guarded:
                raise ValueError("protection dependency mutated")
        if len(imports) != 1 or imports[0] not in module.body:
            raise ValueError("fnmatch must be a single standard-library import")
        result.update(parse_status="PARSED", tables=tables)
    except (SyntaxError, ValueError, TypeError, RecursionError) as error:
        result["reason"] = "PROTECTION_SOURCE_UNKNOWN: " + str(error)
    return result


def _stat_signature(value):
    return (value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode), value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def load_protection_source():
    """Only the fixed source in this checkout is eligible; no caller override."""
    path = Path(__file__).absolute().parents[2] / "vtx" / "hygiene.py"
    result = {"relative_path": "vtx/hygiene.py", "sha256": None,
              "adapter_contract_version": "out-protected-ast/1", "parse_status": "UNKNOWN",
              "tables": None, "reason": None}
    try:
        before = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("protection source is not an observed ordinary file")
        with open(path, "rb") as stream:
            first = stream.read()
            stream.seek(0)
            second = stream.read()
            opened = os.fstat(stream.fileno())
        after = os.stat(path, follow_symlinks=False)
        result["sha256"] = hashlib.sha256(first).hexdigest()
        if first != second or _stat_signature(before) != _stat_signature(opened) or _stat_signature(before) != _stat_signature(after):
            raise ValueError("protection source changed during observation")
        result = parse_protection_source(first.decode("utf-8"))
        result["sha256"] = hashlib.sha256(first).hexdigest()
    except (OSError, UnicodeError, ValueError) as error:
        result["reason"] = "PROTECTION_SOURCE_UNKNOWN: " + str(error)
    return result


def protected_name(name, source):
    if source["parse_status"] != "PARSED":
        return False
    tables = source["tables"]
    return (name in tables["OUT_PROTECTED_NAMES"] or name in tables["OUT_PROTECTED_DIRS"] or
            any(fnmatch.fnmatch(name, pattern) for pattern in tables["OUT_PROTECTED_PATTERNS"]))


def _ref_key(value):
    return (value["root_id"], value["relative_path"])


def _stable_file(entry):
    return entry is not None and entry["entry_type"] == "FILE" and entry["status"] == "STABLE"


def budget_report(request, inventory):
    """Never mix caller logical reserves with observed allocated blocks."""
    grouped = defaultdict(list)
    for record in request.get("reserves", []):
        grouped[record["reserve_id"]].append(record)
    mapped = []
    for reserve_id, records in sorted(grouped.items()):
        volumes = {inventory["root_observations"].get(r["scope_root_id"], {}).get("volume_id") for r in records}
        signatures = {(r["kind"], r.get("bytes"), r.get("rationale")) for r in records}
        reasons = []
        if None in volumes or len(volumes) != 1:
            reasons.append("RESERVE_VOLUME_UNKNOWN_OR_CONFLICT")
        if len(signatures) != 1:
            reasons.append("RESERVE_ID_CONFLICT")
        if any("bytes" not in r or "rationale" not in r for r in records):
            reasons.append("RESERVE_VALUE_UNKNOWN")
        first = records[0]
        mapped.append({"reserve_id": reserve_id, "volume_id": next(iter(volumes)) if len(volumes) == 1 and None not in volumes else None,
                       "kind": first["kind"], "logical_bytes": first.get("bytes") if not reasons else None,
                       "rationale": first.get("rationale"), "sources": records,
                       "status": "UNKNOWN" if reasons else "DECLARED", "reasons": reasons,
                       "declaration_status": DECLARED})
    by_kind = defaultdict(list)
    for record in mapped:
        if record["volume_id"] is not None:
            by_kind[(record["volume_id"], record["kind"])].append(record)
    for records in by_kind.values():
        if len(records) > 1:
            for record in records:
                record["status"] = "UNKNOWN"
                record["logical_bytes"] = None
                record["reasons"].append("MULTIPLE_RESERVE_IDS_FOR_VOLUME_KIND")
    unmapped = [r for r in mapped if r["volume_id"] is None]
    reports = []
    for volume in inventory["volumes"]:
        vid = volume["volume_id"]
        records = [r for r in mapped if r["volume_id"] == vid]
        known = [r for r in records if r["status"] == "DECLARED"]
        kinds = {r["kind"] for r in known}
        # Unmapped declarations may concern this same volume; do not invent a total.
        reserve_complete = set(RESERVE_KINDS) == kinds and len(known) == len(records) and not unmapped
        subtotal = sum(r["logical_bytes"] for r in known)
        required = subtotal if reserve_complete else None
        totals = volume["totals"]
        logical = totals.get("logical_unique_inode_bytes")
        allocated = totals.get("allocated_unique_inode_bytes")
        free = volume.get("volume_free_bytes")
        numeric = "UNKNOWN" if required is None or free is None else "LT" if required < free else "GT" if required > free else "EQ"
        reports.append({"volume_id": vid, "complete": bool(reserve_complete and inventory["complete"] and logical is not None and free is not None),
                        "observed_logical_unique_bytes": logical, "observed_allocated_unique_bytes": allocated,
                        "observation_span": {"started_at": inventory["started_at"], "ended_at": inventory["ended_at"]},
                        "free_bytes_observed_at": volume.get("volume_free_observed_at", volume.get("observed_at")),
                        "volume_free_bytes": free,
                        "reserve_basis": "CALLER_DECLARED_INCREMENTAL_LOGICAL_BYTES",
                        "required_reserve_logical_total": required, "known_reserve_logical_subtotal": subtotal,
                        "missing_kinds": sorted(set(RESERVE_KINDS) - kinds), "reserves": records,
                        "observed_plus_reserve_logical_bytes": logical + required if logical is not None and required is not None and inventory["complete"] else None,
                        "observed_plus_reserve_allocated_bytes": None,
                        "allocated_reserve_reason": "RESERVE_ALLOCATION_UNKNOWN",
                        "reserve_vs_free_numeric_comparison": {"relation": numeric, "reserve_logical_bytes": required,
                            "instant_free_bytes": free, "free_observed_at": volume.get("volume_free_observed_at", volume.get("observed_at")),
                            "basis": "LOGICAL_BUDGET_VS_INSTANT_FREE_NUMERIC_ONLY"}})
    return {"complete": bool(inventory["complete"] and "reserves" in request and not unmapped and all(v["complete"] for v in reports)),
            "volumes": reports, "unmapped_or_conflicting_reserves": unmapped,
            "reserve_status": "DECLARED" if "reserves" in request else "UNKNOWN_NOT_SUPPLIED",
            "concurrent_work": "observed_within_explicit_roots", "unobserved_or_uncooperative_work": "UNKNOWN",
            "physical_volume_independence": "UNKNOWN_APFS_VOLUMES_MAY_SHARE_CONTAINER",
            "future_growth_bytes": None, "peak_disk_bytes": None, "hard_limit_enforced": False,
            "peak_status": "UNKNOWN_SINGLE_SCAN", "estimated_reclaimable_physical_bytes": None}


def build_plan(request, inventory):
    source = load_protection_source()
    entries = inventory["entries"]
    by_ref = {}
    by_id = {e["observation_id"]: e for e in entries}
    roots = {r["id"]: r for r in request["roots"]}
    discrepancies = []
    for entry in entries:
        for ref in entry["objects"]:
            by_ref[_ref_key(ref)] = entry
    retained = set()
    for selector in request["retain_paths"]:
        selected = by_ref.get(_ref_key(selector))
        if selected is None or selected["status"] != "STABLE":
            discrepancies.append({"reason": "RETAIN_OBJECT_UNOBSERVED_OR_CHANGED", "object": selector})
        if selected is None:
            continue
        retained.add(selected["observation_id"])
        if selected["entry_type"] == "DIRECTORY":
            prefix = selector["relative_path"]
            for entry in entries:
                if any(ref["root_id"] == selector["root_id"] and (prefix == "." or ref["relative_path"].startswith(prefix + "/")) for ref in entry["objects"]):
                    retained.add(entry["observation_id"])
    groups = defaultdict(list)
    for entry in entries:
        groups[entry.get("inode_group") or entry["observation_id"]].append(entry)
    # Explicit retains and evidence roles apply to every known name of the inode.
    retained_groups = {group for group, members in groups.items() if any(e["observation_id"] in retained for e in members)}
    for group in retained_groups:
        retained.update(e["observation_id"] for e in groups[group])

    def check_evidence(refs, label):
        if not refs:
            return [label + "_EVIDENCE_MISSING"]
        reasons = []
        for ref in refs:
            entry = by_ref.get(_ref_key(ref))
            valid_role = entry is not None and ("evidence" in entry["roles"] or entry["observation_id"] in retained or
                any("evidence" in e["roles"] for e in groups[entry.get("inode_group") or entry["observation_id"]]))
            if not _stable_file(entry) or not valid_role:
                reasons.append(label + "_EVIDENCE_UNOBSERVED_CHANGED_OR_UNPROTECTED")
                discrepancies.append({"reason": reasons[-1], "object": ref})
        return reasons

    owner_records = defaultdict(list)
    seen_records = set()
    for record in request.get("ownership", []):
        signature = _json(record)
        if signature in seen_records:
            continue
        seen_records.add(signature)
        entry = by_ref.get(_ref_key(record["object"]))
        if not _stable_file(entry):
            discrepancies.append({"reason": "OWNERSHIP_REQUIRES_OBSERVED_STABLE_FILE", "object": record["object"]})
        if entry is None or entry["entry_type"] != "FILE":
            continue
        # A known retention purpose remains binding even if the file changed.
        owner_records[entry.get("inode_group") or entry["observation_id"]].append(record)

    delivery = request.get("delivery_context", {})
    delivery_hold, delivery_unknown = [], []
    if "run_id" not in delivery:
        delivery_unknown.append("DELIVERY_RUN_MISSING")
    elif delivery["run_id"] != request["run_id"]:
        delivery_hold.append("DELIVERY_RUN_MISMATCH")
    final = by_ref.get(_ref_key(delivery["final"])) if "final" in delivery else None
    if final is None:
        delivery_unknown.append("FINAL_UNOBSERVED_OR_MISSING")
        if "final" in delivery:
            discrepancies.append({"reason": "FINAL_UNOBSERVED", "object": delivery["final"]})
    elif not _stable_file(final):
        delivery_hold.append("FINAL_CHANGED_OR_NOT_ORDINARY_FILE")
    elif roots[delivery["final"]["root_id"]]["role"] != "final":
        delivery_unknown.append("FINAL_NOT_IN_EXPLICIT_FINAL_ROOT")
    delivery_unknown += check_evidence(delivery.get("evidence_refs"), "DELIVERY")
    verification = delivery.get("verification", {})
    vstatus = verification.get("status", "UNKNOWN")
    if vstatus in {"FAIL", "CANCELLED", "PENDING"}:
        delivery_hold.append("VERIFICATION_" + vstatus)
    elif vstatus != "PASS":
        delivery_unknown.append("VERIFICATION_UNKNOWN")
    delivery_unknown += check_evidence(verification.get("evidence_refs"), "VERIFICATION")
    finish = delivery.get("finish", {})
    fstatus = finish.get("status", "UNKNOWN")
    if fstatus in {"FAIL", "CANCELLED", "PENDING"}:
        delivery_hold.append("FINISH_" + fstatus)
    elif "required" not in finish or fstatus == "UNKNOWN":
        delivery_unknown.append("FINISH_UNKNOWN")
    elif finish["required"]:
        if fstatus != "PASS":
            delivery_hold.append("FINISH_REQUIRED_STATUS_CONFLICT")
        delivery_unknown += check_evidence(finish.get("evidence_refs"), "FINISH")
    elif fstatus != "NOT_REQUIRED":
        delivery_hold.append("FINISH_NOT_REQUIRED_STATUS_CONFLICT")
    elif not finish.get("reason", "").strip():
        delivery_unknown.append("FINISH_NOT_REQUIRED_REASON_MISSING")
    notice = delivery.get("notification", {})
    nstatus = notice.get("status", "UNKNOWN")
    if nstatus in {"FAILED", "CANCELLED", "PENDING"}:
        delivery_hold.append("NOTIFICATION_" + nstatus)
    elif nstatus != "SENT_WITH_REFERENCE" or not notice.get("reference", "").strip():
        delivery_unknown.append("NOTIFICATION_UNKNOWN_OR_REFERENCE_MISSING")
    # Validate every supplied evidence list even when its status cannot pass.
    for record in request.get("ownership", []):
        check_evidence(record.get("evidence_refs"), "OWNERSHIP")
        for name in ("activity", "references"):
            check_evidence(record.get(name, {}).get("evidence_refs"), name.upper())
    if "evidence_refs" in finish:
        check_evidence(finish["evidence_refs"], "FINISH")

    prelim = {}
    for group, members in groups.items():
        records = owner_records.get(group, [])
        protected, held, unknown = [], [], []
        all_roles = {role for entry in members for role in entry["roles"]}
        if all_roles & PROTECTED_ROLES:
            protected.append("PROTECTED_ROLE:" + ",".join(sorted(all_roles & PROTECTED_ROLES)))
        if any(entry["observation_id"] in retained for entry in members):
            protected.append("EXPLICIT_RETAIN_INODE_GROUP")
        for entry in members:
            for ref in entry["objects"]:
                parts = roots[ref["root_id"]]["path"].split("/") + ref["relative_path"].split("/")
                if any(protected_name(part, source) for part in parts):
                    protected.append("SINGLE_SOURCE_PROTECTED_PATH_COMPONENT")
                # Extra conservative metadata-format retention, not a copied out-name table.
                if any(part.endswith((".lock", ".json", ".jsonl", ".complete")) or part == "complete" for part in parts):
                    protected.append("LOCK_LEDGER_RECEIPT_OR_COMPLETE_METADATA")
        if any(record.get("purpose") == "SHARED" for record in records):
            protected.append("SHARED_PURPOSE")
        if all(entry["entry_type"] == "DIRECTORY" for entry in members):
            protected.append("DIRECTORIES_ARE_NEVER_OPERATION_CANDIDATES")
        if request["hold_reasons"]:
            held.append("CALLER_HOLD:" + ";".join(request["hold_reasons"]))
        signatures = {(r.get("owner_run_id"), r.get("purpose"), r.get("rebuildable"),
                       r.get("activity", {}).get("status"), r.get("references", {}).get("status")) for r in records}
        if len(signatures) > 1:
            held.append("INODE_GROUP_DECLARATION_CONFLICT")
        if not records:
            unknown.append("OWNERSHIP_MISSING")
        for record in records:
            if record.get("owner_run_id") is None:
                unknown.append("OWNER_RUN_MISSING")
            elif record["owner_run_id"] != request["run_id"]:
                held.append("OWNER_RUN_MISMATCH")
            purpose = record.get("purpose", "UNKNOWN")
            if purpose in HELD_PURPOSES:
                held.append("PRESERVED_PURPOSE:" + purpose)
            elif purpose not in {"NEW_ISOLATED_DERIVED_PNG", "SHARED"}:
                unknown.append("PURPOSE_UNKNOWN_OR_NOT_NEW_ISOLATED")
            if record.get("rebuildable") is False:
                held.append("REBUILDABLE_DECLARED_FALSE")
            elif record.get("rebuildable") is not True:
                unknown.append("REBUILDABLE_NOT_DECLARED_TRUE")
            unknown += check_evidence(record.get("evidence_refs"), "OWNERSHIP")
            for name, accepted, blocked in (("activity", "DECLARED_INACTIVE", "ACTIVE"), ("references", "DECLARED_NONE", "PRESENT")):
                declaration = record.get(name, {})
                status = declaration.get("status", "UNKNOWN")
                if status == blocked:
                    held.append(name.upper() + "_" + status)
                elif status != accepted:
                    unknown.append(name.upper() + "_UNKNOWN")
                unknown += check_evidence(declaration.get("evidence_refs"), name.upper())
        for entry in members:
            if entry["status"] != "STABLE":
                held.append("OBJECT_UNSTABLE_OR_INCOMPLETE")
            if entry["entry_type"] != "FILE":
                unknown.append("NOT_ORDINARY_FILE")
            if set(entry["roles"]) != {"gfx"}:
                unknown.append("NOT_UNAMBIGUOUS_EXPLICIT_GFX")
            if not any(ref["relative_path"].lower().endswith(".png") and ref["role"] == "gfx" for ref in entry["objects"]):
                unknown.append("NOT_PNG_NAME_IN_GFX")
            if entry.get("logical_bytes") is None or entry["logical_bytes"] <= 0:
                unknown.append("SIZE_UNKNOWN_OR_EMPTY")
        visible_names = sum(1 for e in members if e["entry_type"] == "FILE")
        if any(e.get("st_nlink") is None for e in members):
            unknown.append("LINK_COUNT_UNKNOWN")
        elif any(e["st_nlink"] > visible_names for e in members if e["entry_type"] == "FILE"):
            held.append("EXTERNAL_LINK_NAMES_POSSIBLE")
        prelim[group] = (protected, held, unknown, records)

    # Every unresolved reference blocks review globally, including otherwise protected rows.
    unique_discrepancies = {_json(item): item for item in discrepancies}
    global_unknown = []
    if not inventory["complete"]:
        global_unknown.append("INVENTORY_INCOMPLETE")
    if source["parse_status"] != "PARSED":
        global_unknown.append("PROTECTION_SOURCE_UNKNOWN")
    if unique_discrepancies:
        global_unknown.append("UNRESOLVED_OBJECT_MAPPING_OR_EVIDENCE")
    items = []
    for group, members in groups.items():
        protected, held, unknown, records = prelim[group]
        held = sorted(set(held + delivery_hold))
        unknown = sorted(set(unknown + delivery_unknown + global_unknown))
        decision = "PROTECTED" if protected else "HELD" if held else "UNKNOWN" if unknown else "REVIEW_ONLY_PNG"
        reasons = sorted(set(protected)) if protected else held if held else unknown if unknown else ["ALL_DECLARED_REVIEW_GATES_SATISFIED"]
        for entry in members:
            items.append({"observation_id": entry["observation_id"], "inode_group": group,
                          "objects": entry["objects"], "logical_bytes": entry.get("logical_bytes"),
                          "allocated_bytes": entry.get("allocated_bytes"), "decision": decision, "reasons": reasons,
                          "additional_hold_reasons": held, "unknown_reasons": unknown,
                          "declaration_status": DECLARED, "ownership_declarations": records,
                          "condition_sources": {"objects": entry["objects"], "inventory_status": entry["status"],
                              "delivery_context": delivery, "protection_source_sha256": source["sha256"],
                              "hold_reasons": request["hold_reasons"], "retain_paths": request["retain_paths"]},
                          "execution_eligible": False})
    items.sort(key=lambda x: x["observation_id"])
    budget = budget_report(request, inventory)
    counts = Counter(item["decision"] for item in items)
    complete = (inventory["complete"] and source["parse_status"] == "PARSED" and not unique_discrepancies and
                not delivery_unknown and counts["UNKNOWN"] == 0 and budget["complete"])
    return {"schema": "framivra.delivery-cleanup-plan/1", "mode": "READ_ONLY", "complete": bool(complete),
            "execution_eligible": False, "planned_operations": [],
            "permission_status": "NOT_REQUESTED_OR_GRANTED_BY_THIS_REPORT",
            "preserved_experiments_notice": "Current b287 baseline and fastPNG/performance/independent-review materials remain held; this report does not release them.",
            "request": {"schema": request["schema"], "project_id": request["project_id"], "run_id": request["run_id"],
                        "identity_status": "caller_supplied", "sha256": _digest(request)},
            "inventory_summary": {"sha256": _digest(inventory), "started_at": inventory["started_at"],
                                  "ended_at": inventory["ended_at"], "complete": inventory["complete"], "totals": inventory["totals"]},
            "inventory": inventory, "protection_source": source,
            "delivery_context": {"declaration_status": DECLARED, "supplied": delivery,
                                 "hold_reasons": delivery_hold, "unknown_reasons": delivery_unknown},
            "discrepancies": list(unique_discrepancies.values()), "items": items,
            "summary": {"counts": {k: counts[k] for k in ("PROTECTED", "HELD", "UNKNOWN", "REVIEW_ONLY_PNG")},
                        "global_unknown_reasons": global_unknown, "estimated_reclaimable_physical_bytes": None,
                        "reason": "NOT_MEASURED_AND_NO_DELETION"}, "budget_report": budget,
            "limitations": ["SUPPLIED_NOT_REVERIFIED", "BEST_EFFORT_NON_ATOMIC", "PNG_TYPE_SUFFIX_AND_PURPOSE_ONLY_NOT_DECODED",
                            "NO_DELIVERY_AUTHENTICATION_NO_EXECUTION_NO_PHYSICAL_RECLAIM_ESTIMATE"]}
