"""Finite, explicit-root, read-only filesystem observations.

This module measures names and stat metadata, never file contents.  It has no
production imports and creates no files.  Limits are checked between filesystem
calls; a blocked filesystem call is not subject to a hard wall-clock timeout.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
import stat
import time


DEFAULT_MAX_ENTRIES = 100_000
DEFAULT_MAX_SCAN_SECONDS = 30.0
PROTECTED_ROLES = frozenset(
    {"input", "music", "sdk", "download", "final", "evidence", "history", "shared"}
)
_ROLE_ORDER = (
    "input", "music", "sdk", "download", "final", "evidence", "history",
    "shared", "unknown", "audio", "encode", "gfx",
)
_ALLOCATION_CAVEATS = [
    "ST_BLOCKS_IS_NOT_EXCLUSIVE_PHYSICAL_EXTENTS",
    "APFS_COW_SNAPSHOTS_COMPRESSION_OPEN_HANDLES_AND_METADATA_MAY_DIFFER",
    "UNOBSERVED_LINK_NAMES_MAY_EXIST",
    "NOT_RECLAIMABLE_PHYSICAL_BYTES",
]
_STAT_FIELDS = (
    "st_dev", "st_ino", "st_nlink", "size", "blocks", "mtime_ns", "ctime_ns",
    "stat_file_type",
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _integer(value, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return None if nonnegative and value < 0 else value


def _snapshot(value):
    mode = _integer(getattr(value, "st_mode", None), True)
    return {
        "st_dev": _integer(getattr(value, "st_dev", None), True),
        "st_ino": _integer(getattr(value, "st_ino", None), True),
        "st_nlink": _integer(getattr(value, "st_nlink", None), True),
        "size": _integer(getattr(value, "st_size", None), True),
        "blocks": _integer(getattr(value, "st_blocks", None), True),
        "mtime_ns": _integer(getattr(value, "st_mtime_ns", None)),
        "ctime_ns": _integer(getattr(value, "st_ctime_ns", None)),
        "stat_file_type": stat.S_IFMT(mode) if mode is not None else None,
    }


def _type(mode):
    return {
        stat.S_IFREG: "FILE", stat.S_IFDIR: "DIRECTORY",
        stat.S_IFLNK: "SYMLINK", stat.S_IFIFO: "FIFO",
        stat.S_IFSOCK: "SOCKET", stat.S_IFCHR: "CHARACTER_DEVICE",
        stat.S_IFBLK: "BLOCK_DEVICE",
    }.get(mode, "UNKNOWN")


def _volume_id(dev):
    return None if dev is None else "dev:" + str(dev)


def _inode_id(snapshot):
    dev, ino = snapshot["st_dev"], snapshot["st_ino"]
    if dev is None or ino is None:
        return None
    return "dev:" + str(dev) + ":ino:" + str(ino)


def _within(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


class _Scan:
    def __init__(self, request):
        self.request = request
        self.started_at = _now()
        self.started = time.monotonic()
        self.max_entries = request.get("max_entries", DEFAULT_MAX_ENTRIES)
        self.max_seconds = request.get("max_scan_seconds", DEFAULT_MAX_SCAN_SECONDS)
        self.roots = [dict(root, path=os.path.normpath(root["path"]))
                      for root in request["roots"]]
        self.entries = {}
        self.volumes = {}
        self.errors = []
        self.limit_reasons = []
        self.enumeration_complete = True
        self.enumeration_stopped = False
        self.directory_entries_enumerated = 0

    def error(self, reason, path=None, error=None):
        item = {"reason": reason, "path": path}
        if error is not None:
            item["error_type"] = type(error).__name__
            item["errno"] = getattr(error, "errno", None)
        self.errors.append(item)

    def time_available(self):
        if time.monotonic() - self.started < self.max_seconds:
            return True
        self.stop("MAX_SCAN_SECONDS_REACHED")
        return False

    def stop(self, reason):
        self.enumeration_complete = False
        self.enumeration_stopped = True
        if reason not in self.limit_reasons:
            self.limit_reasons.append(reason)
            self.error(reason)

    def may_enumerate(self):
        if self.enumeration_stopped or not self.time_available():
            return False
        if len(self.entries) >= self.max_entries:
            self.stop("MAX_ENTRIES_REACHED")
            return False
        return True

    def mark_unknown(self, entry, reason):
        if reason not in entry["reasons"]:
            entry["reasons"].append(reason)
        if entry["status"] != "CHANGED_DURING_SCAN":
            entry["status"] = "UNKNOWN"
        entry["completeness"] = "PARTIAL"

    def mark_changed(self, entry, after=None):
        self.mark_unknown(entry, "CHANGED_DURING_SCAN")
        entry["status"] = "CHANGED_DURING_SCAN"
        entry["metadata_stable"] = False
        entry["logical_bytes"] = None
        entry["allocated_bytes"] = None
        if after is not None:
            entry["observed_after"] = after
        self.error("CHANGED_DURING_SCAN", entry["path"])

    def new_entry(self, path, before=None):
        before = before or {key: None for key in _STAT_FIELDS}
        kind = _type(before["stat_file_type"])
        entry = {
            "observation_id": "sha256:" + hashlib.sha256(os.fsencode(path)).hexdigest(),
            "path": path,
            "root_id": None, "relative_path": None, "objects": [],
            "roles": [], "category": "unknown",
            "entry_type": kind, "file_type": kind,
            "stat_file_type": before["stat_file_type"],
            "observed_at": _now(), "rechecked_at": None,
            "status": "PENDING_RECHECK", "completeness": "PARTIAL",
            "metadata_stable": False, "reasons": [],
            "logical_bytes": None, "allocated_bytes": None,
            "allocation_method": ("ST_BLOCKS_TIMES_512" if before["blocks"] is not None
                                  else "UNAVAILABLE"),
            "allocation_caveats": list(_ALLOCATION_CAVEATS),
            "st_dev": before["st_dev"], "st_ino": before["st_ino"],
            "st_nlink": before["st_nlink"],
            "mtime_ns": before["mtime_ns"], "ctime_ns": before["ctime_ns"],
            "identity_basis": {
                "method": "OBSERVED_DEV_INODE_STAT_TYPE",
                "st_dev": before["st_dev"], "st_ino": before["st_ino"],
                "stat_file_type": before["stat_file_type"],
            },
            "inode_group": _inode_id(before),
            "volume_id": _volume_id(before["st_dev"]),
            "observed_before": before, "observed_after": None,
            "unknown_fields": {key: "STAT_FIELD_UNAVAILABLE" for key, val in before.items()
                               if val is None},
        }
        self.entries[path] = entry
        vid = entry["volume_id"]
        if vid is not None and vid not in self.volumes:
            self.volumes[vid] = {
                "volume_id": vid, "st_dev": before["st_dev"],
                "volume_free_bytes": None, "free_bytes_observed_at": None,
                "volume_free_observed_at": None,
                "free_bytes_method": "F_BAVAIL_TIMES_F_FRSIZE",
                "free_bytes_reason": "NOT_OBSERVED",
                "capacity_independence": "NOT_ESTABLISHED_APFS_CONTAINERS_MAY_BE_SHARED",
            }
        return entry

    def finish_metadata(self, entry, value):
        after = _snapshot(value)
        entry["observed_after"] = after
        entry["rechecked_at"] = _now()
        if entry["observed_before"] != after:
            self.mark_changed(entry, after)
            return
        if entry["status"] == "CHANGED_DURING_SCAN":
            return
        entry["metadata_stable"] = True
        entry["logical_bytes"] = after["size"]
        entry["allocated_bytes"] = after["blocks"] * 512 if after["blocks"] is not None else None
        if entry["unknown_fields"]:
            self.mark_unknown(entry, "STAT_METADATA_INCOMPLETE")
        if not entry["reasons"]:
            entry["status"] = "STABLE"
            entry["completeness"] = "COMPLETE"

    def measure_free(self, fd, entry):
        volume = self.volumes.get(entry["volume_id"])
        if volume is None or volume["free_bytes_reason"] != "NOT_OBSERVED":
            return
        if not self.time_available():
            volume["free_bytes_reason"] = "TIME_LIMIT_REACHED"
            return
        try:
            value = os.statvfs(fd)
            available = _integer(getattr(value, "f_bavail", None), True)
            fragment = _integer(getattr(value, "f_frsize", None), True)
            if available is None or fragment is None or fragment == 0:
                volume["free_bytes_reason"] = "STATVFS_FIELDS_UNAVAILABLE"
                self.error("VOLUME_FREE_UNKNOWN", entry["path"])
            else:
                volume["volume_free_bytes"] = available * fragment
                volume["free_bytes_observed_at"] = _now()
                volume["volume_free_observed_at"] = volume["free_bytes_observed_at"]
                volume["free_bytes_reason"] = None
        except (OSError, AttributeError, NotImplementedError) as exc:
            volume["free_bytes_reason"] = "STATVFS_UNAVAILABLE"
            self.error("VOLUME_FREE_UNKNOWN", entry["path"], exc)

    def open_directory(self, path, entry, parent_fd=None, name=None):
        """Open one already observed ordinary directory, without following it."""
        if not self.time_available():
            self.mark_unknown(entry, "TIME_LIMIT_BEFORE_DIRECTORY_OPEN")
            return None
        fd = None
        try:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            fd = os.open(path if parent_fd is None else name, flags, dir_fd=parent_fd)
            if not self.time_available():
                self.mark_unknown(entry, "TIME_LIMIT_BEFORE_DIRECTORY_STAT")
                os.close(fd)
                return None
            current = os.fstat(fd)
            if _snapshot(current) != entry["observed_before"]:
                self.mark_changed(entry, _snapshot(current))
                os.close(fd)
                return None
            self.measure_free(fd, entry)
            if not self.time_available():
                self.mark_unknown(entry, "TIME_LIMIT_BEFORE_DIRECTORY_ENUMERATION")
                os.close(fd)
                return None
            iterator = os.scandir(fd)
            return {"path": path, "entry": entry, "fd": fd, "iterator": iterator,
                    "parent_fd": parent_fd, "name": name, "enumerated": False}
        except (OSError, AttributeError, NotImplementedError) as exc:
            if fd is not None:
                os.close(fd)
            self.mark_unknown(entry, "DIRECTORY_OPEN_OR_ENUMERATION_FAILED")
            self.enumeration_complete = False
            self.error("DIRECTORY_OPEN_OR_ENUMERATION_FAILED", path, exc)
            return None

    def close_directory(self, frame):
        entry = frame["entry"]
        try:
            frame["iterator"].close()
            if not frame["enumerated"]:
                self.mark_unknown(entry, "DIRECTORY_ENUMERATION_INCOMPLETE")
            if not self.time_available():
                self.mark_unknown(entry, "TIME_LIMIT_BEFORE_METADATA_RECHECK")
                return
            # fstat checks the opened object; the non-following name lookup also
            # detects a known ordinary rename/replacement while it was open.
            current = os.fstat(frame["fd"])
            self.finish_metadata(entry, current)
            if not self.time_available():
                self.mark_unknown(entry, "TIME_LIMIT_BEFORE_NAME_RECHECK")
                return
            named = os.stat(frame["path"] if frame["parent_fd"] is None else frame["name"],
                            dir_fd=frame["parent_fd"], follow_symlinks=False)
            if _snapshot(named) != _snapshot(current):
                self.mark_changed(entry, _snapshot(named))
        except OSError as exc:
            self.mark_unknown(entry, "DIRECTORY_METADATA_RECHECK_FAILED")
            entry["metadata_stable"] = False
            entry["logical_bytes"] = entry["allocated_bytes"] = None
            self.error("DIRECTORY_METADATA_RECHECK_FAILED", frame["path"], exc)
        finally:
            os.close(frame["fd"])

    def walk_root(self, root):
        path = root["path"]
        if path in self.entries:
            existing = self.entries[path]
            if existing["entry_type"] != "DIRECTORY":
                self.mark_unknown(existing, "ROOT_NOT_ORDINARY_DIRECTORY")
                self.error("ROOT_NOT_ORDINARY_DIRECTORY", path)
                self.enumeration_complete = False
            return
        if not self.may_enumerate():
            return
        if not self.time_available():
            return
        try:
            value = os.stat(path, follow_symlinks=False)
        except OSError as exc:
            entry = self.new_entry(path)
            self.mark_unknown(entry, "ROOT_STAT_FAILED")
            self.error("ROOT_STAT_FAILED", path, exc)
            self.enumeration_complete = False
            return
        entry = self.new_entry(path, _snapshot(value))
        if entry["entry_type"] != "DIRECTORY":
            self.mark_unknown(entry, "ROOT_NOT_ORDINARY_DIRECTORY")
            self.error("ROOT_NOT_ORDINARY_DIRECTORY", path)
            self.enumeration_complete = False
            return
        frame = self.open_directory(path, entry)
        if frame is None:
            self.enumeration_complete = False
            return
        stack = [frame]
        try:
            while stack and not self.enumeration_stopped:
                frame = stack[-1]
                if not self.may_enumerate():
                    break
                try:
                    child = next(frame["iterator"])
                    self.directory_entries_enumerated += 1
                except StopIteration:
                    frame["enumerated"] = True
                    self.close_directory(stack.pop())
                    continue
                except OSError as exc:
                    self.mark_unknown(frame["entry"], "DIRECTORY_ENUMERATION_FAILED")
                    self.enumeration_complete = False
                    self.error("DIRECTORY_ENUMERATION_FAILED", frame["path"], exc)
                    self.close_directory(stack.pop())
                    continue
                child_path = os.path.join(frame["path"], child.name)
                if not self.time_available():
                    break
                try:
                    before = os.stat(child.name, dir_fd=frame["fd"], follow_symlinks=False)
                except OSError as exc:
                    item = self.entries.get(child_path) or self.new_entry(child_path)
                    self.mark_unknown(item, "ENTRY_STAT_FAILED")
                    self.error("ENTRY_STAT_FAILED", child_path, exc)
                    self.enumeration_complete = False
                    continue
                if child_path in self.entries:
                    item = self.entries[child_path]
                    if item["observed_before"] != _snapshot(before):
                        self.mark_changed(item, _snapshot(before))
                    continue
                item = self.new_entry(child_path, _snapshot(before))
                if item["entry_type"] == "DIRECTORY":
                    nested = self.open_directory(child_path, item, frame["fd"], child.name)
                    if nested is not None:
                        stack.append(nested)
                    else:
                        self.enumeration_complete = False
                elif item["entry_type"] == "FILE":
                    if not self.time_available():
                        self.mark_unknown(item, "TIME_LIMIT_BEFORE_METADATA_RECHECK")
                        break
                    try:
                        after = os.stat(child.name, dir_fd=frame["fd"], follow_symlinks=False)
                        self.finish_metadata(item, after)
                    except OSError as exc:
                        self.mark_unknown(item, "ENTRY_METADATA_RECHECK_FAILED")
                        self.error("ENTRY_METADATA_RECHECK_FAILED", child_path, exc)
                else:
                    self.mark_unknown(item, "UNSUPPORTED_NON_ORDINARY_OBJECT")
                    self.error("UNSUPPORTED_NON_ORDINARY_OBJECT", child_path)
                    self.enumeration_complete = False
        finally:
            while stack:
                self.close_directory(stack.pop())

    def mappings_and_groups(self):
        groups = {}
        for path, item in self.entries.items():
            objects = [{"root_id": root["id"],
                        "relative_path": os.path.relpath(path, root["path"]).replace(os.sep, "/"),
                        "role": root["role"]}
                       for root in self.roots if _within(path, root["path"])]
            objects.sort(key=lambda obj: (obj["root_id"], obj["relative_path"], obj["role"]))
            item["objects"] = objects
            if objects:
                item["root_id"] = objects[0]["root_id"]
                item["relative_path"] = objects[0]["relative_path"]
            item["roles"] = sorted({obj["role"] for obj in objects})
            item["category"] = next((role for role in _ROLE_ORDER if role in item["roles"]), "unknown")
            if item["inode_group"] is not None:
                groups.setdefault(item["inode_group"], []).append(item)
        output = []
        for gid, members in sorted(groups.items()):
            roles = sorted({role for item in members for role in item["roles"]})
            signatures = {tuple(item["observed_before"][key] for key in _STAT_FIELDS)
                          for item in members}
            conflict = len(signatures) > 1
            if conflict:
                for item in members:
                    self.mark_changed(item)
                    self.mark_unknown(item, "INODE_GROUP_METADATA_CONFLICT")
            links = [item["st_nlink"] for item in members]
            nlink = links[0] if links and links[0] is not None and len(set(links)) == 1 else None
            is_file = all(item["entry_type"] == "FILE" for item in members)
            external = ("POSSIBLE" if is_file and nlink is not None and nlink > len(members)
                        else "NONE_OBSERVED" if is_file and nlink == len(members)
                        else "UNKNOWN")
            group = {"inode_group": gid, "st_dev": members[0]["st_dev"],
                     "st_ino": members[0]["st_ino"], "roles": roles,
                     "protected": bool(PROTECTED_ROLES.intersection(roles)),
                     "observation_ids": sorted(item["observation_id"] for item in members),
                     "visible_link_names": len(members), "st_nlink": nlink,
                     "external_links": external, "metadata_conflict": conflict,
                     "estimated_reclaimable_physical_bytes": None,
                     "reclaimable_reason": "NOT_MEASURED_AND_NO_DELETION"}
            output.append(group)
            for item in members:
                item["group_roles"] = roles
                item["group_protected"] = group["protected"]
                item["visible_link_names"] = len(members)
                item["external_links"] = external
        for item in self.entries.values():
            item.setdefault("group_roles", item["roles"])
            item.setdefault("group_protected", bool(PROTECTED_ROLES.intersection(item["roles"])))
            item.setdefault("visible_link_names", None)
            item.setdefault("external_links", "UNKNOWN")
        return output

    def totals(self, entries, complete):
        stable = [item for item in entries if item["metadata_stable"]]
        files = [item for item in stable if item["entry_type"] == "FILE"]
        directories = [item for item in stable if item["entry_type"] == "DIRECTORY"]
        unique_files = {item["inode_group"]: item for item in files if item["inode_group"] is not None}
        unique_dirs = {item["inode_group"]: item for item in directories if item["inode_group"] is not None}
        def known_sum(items, field):
            return sum(item[field] for item in items if item[field] is not None)
        subtotal = {
            "logical_path_bytes": known_sum(files, "logical_bytes"),
            "logical_unique_inode_bytes": known_sum(unique_files.values(), "logical_bytes"),
            "allocated_unique_inode_bytes": known_sum(unique_files.values(), "allocated_bytes"),
            "directory_logical_path_bytes": known_sum(directories, "logical_bytes"),
            "directory_logical_unique_inode_bytes": known_sum(unique_dirs.values(), "logical_bytes"),
            "directory_allocated_unique_inode_bytes": known_sum(unique_dirs.values(), "allocated_bytes"),
            "files": len(files), "directories": len(directories),
            "unique_inodes": len(unique_files), "unique_directory_inodes": len(unique_dirs),
        }
        result = {key: value if complete else None for key, value in subtotal.items()}
        result.update({
            "complete": complete, "observed_entry_count": len(entries),
            "unknown_count": sum(item["status"] != "STABLE" for item in entries),
            "omitted_count": 0 if self.enumeration_complete else None,
            "partial_known_subtotal": subtotal,
            "logical_basis": "REGULAR_FILES_ONLY_DIRECTORY_METADATA_REPORTED_SEPARATELY",
            "unique_inodes_basis": "REGULAR_FILES_ONLY",
            "allocation_caveats": list(_ALLOCATION_CAVEATS),
            "estimated_reclaimable_physical_bytes": None,
            "reclaimable_reason": "NOT_MEASURED_AND_NO_DELETION",
        })
        return result

    def run(self):
        for root in self.roots:
            if self.enumeration_stopped:
                break
            self.walk_root(root)
        groups = self.mappings_and_groups()
        # A known changed directory makes descendant name mappings uncertain.
        changed_dirs = [item["path"] for item in self.entries.values()
                        if item["entry_type"] == "DIRECTORY"
                        and item["status"] == "CHANGED_DURING_SCAN"]
        for item in self.entries.values():
            if any(item["path"] != path and _within(item["path"], path) for path in changed_dirs):
                self.mark_unknown(item, "ANCESTOR_CHANGED_DURING_SCAN")
                item["metadata_stable"] = False
                item["logical_bytes"] = item["allocated_bytes"] = None
        entries = sorted(self.entries.values(), key=lambda item: item["path"])
        complete = (self.enumeration_complete
                    and all(item["status"] == "STABLE" for item in entries)
                    and all(volume["volume_free_bytes"] is not None for volume in self.volumes.values()))
        root_observations = {}
        for root in sorted(self.roots, key=lambda value: value["id"]):
            item = self.entries.get(root["path"])
            root_observations[root["id"]] = {
                "observation_id": item["observation_id"] if item else None,
                "volume_id": item["volume_id"] if item and item["metadata_stable"] else None,
                "status": item["status"] if item else "UNKNOWN",
                "reason": None if item and item["status"] == "STABLE" else "ROOT_OBSERVATION_INCOMPLETE",
            }
        ended_at = _now()
        volumes = []
        for vid, volume in sorted(self.volumes.items()):
            volume_entries = [item for item in entries if item["volume_id"] == vid]
            volume["complete"] = complete
            volume["root_ids"] = sorted(root_id for root_id, item in root_observations.items()
                                         if item["volume_id"] == vid)
            volume["totals"] = self.totals(volume_entries, complete)
            volume["observation_span"] = {"started_at": self.started_at, "ended_at": ended_at}
            volumes.append(volume)
        code_ref = self.request.get("code_ref", {})
        return {
            "schema": "framivra.disk-inventory/1",
            "mode": "READ_ONLY", "execution_eligible": False,
            "permission_status": "NOT_REQUESTED_OR_GRANTED_BY_THIS_REPORT",
            "planned_operations": [],
            "scope": {"project_id": self.request["project_id"], "run_id": self.request["run_id"],
                      "identity_status": "CALLER_SUPPLIED", "roots": self.roots,
                      "scope_policy": "EXPLICIT_ROOTS_ONLY_ORDINARY_OBJECTS_NO_FOLLOW"},
            "code_ref": {"value": self.request.get("code_ref"),
                         "status": "CALLER_SUPPLIED" if "code_ref" in self.request else "UNKNOWN",
                         "commit": code_ref.get("commit"), "tree": code_ref.get("tree"),
                         "field_status": {key: "CALLER_SUPPLIED" if key in code_ref else "UNKNOWN"
                                          for key in ("commit", "tree")},
                         "unknown_fields": {key: "NOT_SUPPLIED" for key in ("commit", "tree")
                                            if key not in code_ref},
                         "identity_verification": "NOT_REVERIFIED_NOT_A_SCAN_COMPLETENESS_CONDITION"},
            "started_at": self.started_at, "ended_at": ended_at,
            "duration": time.monotonic() - self.started,
            "limits": {"max_entries": self.max_entries, "max_scan_seconds": self.max_seconds,
                       "entries_observed": len(entries),
                       "directory_entries_enumerated": self.directory_entries_enumerated,
                       "reached": self.limit_reasons,
                       "time_limit_semantics": "BEST_EFFORT_AT_FILESYSTEM_CALL_BOUNDARIES_NOT_HARD_TIMEOUT",
                       "entry_limit_semantics": "UNIQUE_OBSERVED_NAMES_INCLUDING_ROOT_DIRECTORIES"},
            "complete": complete, "enumeration_complete": self.enumeration_complete,
            "consistency": "BEST_EFFORT_NON_ATOMIC",
            "consistency_caveat": "UNOBSERVED_CREATION_OR_MODIFICATION_CAN_OCCUR_BETWEEN_OBSERVATIONS",
            "errors": self.errors, "entries": entries,
            "root_observations": root_observations, "inode_groups": groups,
            "totals": self.totals(entries, complete), "volumes": volumes,
            "estimated_reclaimable_physical_bytes": None,
            "reclaimable_reason": "NOT_MEASURED_AND_NO_DELETION",
        }


def scan(request: dict) -> dict:
    """Observe a prevalidated request; return a JSON-safe, non-atomic report."""
    return _Scan(request).run()
