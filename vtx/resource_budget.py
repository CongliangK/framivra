"""Cooperative, process-shared resource accounting (issue #44).

This is an opt-in Python API, not OS isolation, a worker pool, or a GPU probe.
All participants in a pool must use the same persistent ledger. Linux /proc,
local filesystem flock, and fstxn's fd-anchored writes are required.
"""
from __future__ import annotations

import contextlib
import copy
import dataclasses
import json
import math
import os
import shutil
import stat
import time
import uuid
from pathlib import Path
from typing import Optional

from . import fstxn, proc_supervisor

__all__ = ["Resources", "Inventory", "detect_inventory", "ResourceBudget",
           "Lease", "BudgetError", "BudgetExceeded", "BudgetTimeout",
           "BudgetCancelled", "ResourceUnavailable", "BudgetStateError"]
_FIELDS = ("cpu_millis", "ram_bytes", "vram_bytes", "disk_bytes")
_SCHEMA = 1
_POLL_SECONDS = 0.02
_IMPORT_PID = os.getpid()


class BudgetError(RuntimeError):
    """Base exception for budget failures."""


class BudgetExceeded(BudgetError):
    """A request exceeds configured capacity or nonblocking free capacity."""


class BudgetTimeout(BudgetError):
    """The acquisition deadline elapsed, including time waiting for a lock."""


class BudgetCancelled(BudgetError):
    """The caller's cancellation event was set before acquisition completed."""


class ResourceUnavailable(BudgetError):
    """Required platform support or a requested resource is unavailable."""


class BudgetStateError(BudgetError):
    """Missing, corrupt, incompatible, or externally modified state; fail closed."""


@dataclasses.dataclass(frozen=True)
class Resources:
    """Nonnegative integer tokens; 1000 cpu_millis represents one logical CPU."""

    cpu_millis: int = 0
    ram_bytes: int = 0
    vram_bytes: int = 0
    disk_bytes: int = 0

    def __post_init__(self):
        for name in _FIELDS:
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")

    def as_dict(self):
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or set(data) != set(_FIELDS):
            raise ValueError("resource vector must contain exactly the four dimensions")
        return cls(**data)


def _sum(vectors):
    result = dict.fromkeys(_FIELDS, 0)
    for vector in vectors:
        for name in _FIELDS:
            result[name] += getattr(vector, name)
    return Resources(**result)


@dataclasses.dataclass(frozen=True)
class Inventory:
    """A current-machine measurement, including explicit unavailable reasons."""

    limits: Resources
    sources: dict

    def as_dict(self):
        return {"limits": self.limits.as_dict(), "sources": dict(self.sources)}


def _cgroup_dirs(root=None, membership=None):
    """Visible cgroup-v2 ancestors only; absence is reported, never guessed."""
    root = Path("/sys/fs/cgroup") if root is None else Path(root)
    membership = Path("/proc/self/cgroup") if membership is None else Path(membership)
    if not (root / "cgroup.controllers").is_file():
        return []
    try:
        line = next(line for line in membership.read_text().splitlines()
                    if line.startswith("0::"))
        relative = line[3:].lstrip("/")
        if ".." in Path(relative).parts:
            return []
        current = root / relative
        if not current.is_dir():
            # A container may mount its cgroup subtree as the visible root.
            # Even without a reliable leaf mapping, visible root caps still apply.
            return [root]
        return [current] + [p for p in current.parents if p == root or root in p.parents]
    except (OSError, StopIteration):
        return [root]


def detect_inventory(disk_path, *, vram_bytes: Optional[int] = None):
    """Measure affinity, available RAM, and free bytes on disk_path without writes.

    Visible cgroup-v2 quotas/headroom further cap CPU/RAM. VRAM is unavailable
    unless the caller supplies a trusted *abstract* inventory. No GPU program
    is launched. Measurements are a point-in-time starting configuration, not
    reservations against unrelated applications or hidden container limits.
    """
    sources = {}
    try:
        cpu = len(os.sched_getaffinity(0)) * 1000
        sources["cpu_millis"] = "sched_getaffinity"
    except (AttributeError, OSError):
        count = os.cpu_count()
        cpu = count * 1000 if count else 0
        sources["cpu_millis"] = "os.cpu_count" if count else "unavailable"
    ram = 0
    try:
        fields = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        ram = int(fields["MemAvailable"].split()[0]) * 1024
        sources["ram_bytes"] = "/proc/meminfo MemAvailable"
    except (OSError, ValueError, KeyError):
        sources["ram_bytes"] = "unavailable: MemAvailable could not be measured"
    cgroups = _cgroup_dirs()
    sources["cgroup"] = ("visible cgroup-v2 paths: " + ", ".join(map(str, cgroups))
                         if cgroups else "unavailable: no visible cgroup-v2 limits")
    for directory in cgroups:
        try:
            quota, period = (directory / "cpu.max").read_text().split()
            if quota != "max":
                cpu = min(cpu, int(quota) * 1000 // int(period))
        except (OSError, ValueError, ZeroDivisionError):
            pass
        try:
            limit = (directory / "memory.max").read_text().strip()
            if limit != "max":
                headroom = max(0, int(limit) - int((directory / "memory.current").read_text()))
                ram = min(ram, headroom)
        except (OSError, ValueError):
            pass
    try:
        disk = shutil.disk_usage(disk_path).free
        sources["disk_bytes"] = f"disk_usage({Path(disk_path).absolute()}).free"
    except OSError:
        disk = 0
        sources["disk_bytes"] = "unavailable: disk path could not be measured"
    if vram_bytes is None:
        vram = 0
        sources["vram_bytes"] = "unavailable: no trusted VRAM inventory supplied"
    else:
        Resources(vram_bytes=vram_bytes)  # strict integer validation, including bool
        vram = vram_bytes
        sources["vram_bytes"] = "caller-supplied abstract inventory; no GPU probe"
    return Inventory(Resources(cpu, ram, vram, disk), sources)


def _scope():
    if os.getpid() != _IMPORT_PID:
        raise ResourceUnavailable("fork-inherited budget interpreter is unsupported; use spawn/subprocess/exec")
    boot = proc_supervisor.boot_id()
    try:
        namespace = os.stat("/proc/self/ns/pid").st_ino
    except OSError as error:
        raise ResourceUnavailable("Linux PID namespace identity is unavailable") from error
    if not boot or fstxn.fcntl is None or fstxn.write_capability() != "fd-anchored":
        raise ResourceUnavailable("resource accounting requires Linux /proc, flock and fd-anchored writes")
    return {"boot_id": boot, "pid_namespace": namespace}


def _owner():
    scope = _scope()
    pid = os.getpid()
    ticks = proc_supervisor.proc_starttime(pid)
    if ticks is None:
        raise ResourceUnavailable("current process start time is unavailable")
    return dict(scope, pid=pid, start_ticks=ticks)


def _dead(owner):
    # PID reuse must never retain the old allocation or signal the new process.
    ticks = proc_supervisor.proc_starttime(owner["pid"])
    if ticks is not None:
        if ticks != owner["start_ticks"]:
            return True
        tail = proc_supervisor._proc_stat_tail(owner["pid"])
        return bool(tail and tail[0] in ("Z", "X"))
    try:
        os.stat(f'/proc/{owner["pid"]}')
    except FileNotFoundError:
        return True
    except OSError:
        pass
    return False  # permission/I/O uncertainty is occupied, never free capacity


def _strict_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


class _Wait:
    def __init__(self, timeout=None, cancel=None):
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                                    or not math.isfinite(timeout) or timeout < 0):
            raise ValueError("timeout must be None or a finite nonnegative number")
        if cancel is not None and not callable(getattr(cancel, "is_set", None)):
            raise TypeError("cancel must provide is_set(), like threading.Event")
        self.nonblocking = timeout == 0
        self.deadline = None if timeout is None or timeout == 0 else time.monotonic() + timeout
        self.cancel = cancel

    def check(self):
        if self.cancel is not None and self.cancel.is_set():
            raise BudgetCancelled("resource acquisition cancelled")
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise BudgetTimeout("resource acquisition timed out")

    def pause(self):
        self.check()
        if self.nonblocking:
            raise BudgetExceeded("resource budget or ledger lock is busy")
        delay = _POLL_SECONDS
        if self.deadline is not None:
            delay = min(delay, max(0, self.deadline - time.monotonic()))
        time.sleep(delay)
        self.check()


@contextlib.contextmanager
def _locked(path, wait, *, create=False):
    """Timed adapter for fstxn's lock protocol; no worker or scheduler thread.

    fstxn's public transaction API waits indefinitely. Reuse its per-path lock,
    pinned directory, sidecar naming, regular-file guard and atomic writer, with
    LOCK_NB solely to support deadline/cancellation while waiting for the lock.
    Never delete the sidecar: unlinking it would split the lock domain.
    """
    thread_lock = fstxn._thread_lock_for(path)
    wait.check()
    while not thread_lock.acquire(blocking=False):
        wait.pause()
    try:
        with fstxn.pinned_parent(path, create=create) as (parent, name, target):
            lockname = name + ".lock"
            fresh = False
            try:
                fd = os.open(lockname, os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent)
            except FileNotFoundError:
                if not create:
                    raise BudgetStateError("budget lock is missing; never automatically recreate")
                try:
                    os.stat(name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise BudgetStateError("existing ledger has no lock; explicit recovery required")
                try:
                    fd = os.open(lockname, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=parent)
                    fresh = True
                except FileExistsError:
                    fd = os.open(lockname, os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise BudgetStateError("budget lock is not a regular file")
                while True:
                    wait.check()
                    try:
                        fstxn.fcntl.flock(fd, fstxn.fcntl.LOCK_EX | fstxn.fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        wait.pause()
                try:
                    wait.check()
                    fstxn.check_regular_at(parent, name, what=target)
                    # A replaced/unlinked lock could create a second lock domain.
                    current = os.stat(lockname, dir_fd=parent, follow_symlinks=False)
                    if fstxn.entry_identity(current) != fstxn.entry_identity(os.fstat(fd)):
                        raise BudgetStateError("budget lock identity changed")
                    yield parent, name, target, fresh, list(fstxn.entry_identity(os.fstat(fd)))
                finally:
                    fstxn.fcntl.flock(fd, fstxn.fcntl.LOCK_UN)
            finally:
                os.close(fd)
    finally:
        thread_lock.release()


@dataclasses.dataclass(frozen=True)
class Lease:
    """Process-owned allocation. Context exit releases; acquisition has no TTL."""

    budget: ResourceBudget
    token: str
    owner: dict
    resources: Resources

    def release(self):
        return self.budget.release(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()


class ResourceBudget:
    """Open a named pool. Use create() explicitly for its first initialization.

    Store the ledger outside cache/out (their garbage collector owns them).
    Do not remove the ledger, sidecar, or parent directory while in use. A pool
    is local to one boot/PID namespace; cross-host/shared-network use is denied.
    """

    def __init__(self, path):
        path = Path(path).absolute()
        self.path = path.parent.resolve() / path.name
        self.scope = _scope()

    @classmethod
    def create(cls, path, limits, *, timeout=None, cancel=None):
        if not isinstance(limits, Resources):
            raise TypeError("limits must be Resources")
        budget = cls(path)
        wait = _Wait(timeout, cancel)
        with _locked(budget.path, wait, create=True) as (parent, name, target, fresh, lock_identity):
            try:
                state = budget._load(parent, name, lock_identity)
            except FileNotFoundError:
                if not fresh:
                    raise BudgetStateError("ledger missing beside an existing lock; explicit recovery required")
                state = {"schema": _SCHEMA, "scope": budget.scope, "lock_identity": lock_identity,
                         "limits": limits.as_dict(), "leases": {}}
                budget._save(target, state)
            else:
                if state["limits"] != limits.as_dict():
                    raise BudgetStateError("pool already has different configured limits")
        return budget

    def _load(self, parent, name, lock_identity):
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("ledger is not a regular file")
                state = json.load(stream, object_pairs_hook=_strict_json)
            if not isinstance(state, dict) or set(state) != {"schema", "scope", "lock_identity", "limits", "leases"}:
                raise ValueError("invalid state shape")
            if type(state["schema"]) is not int or state["schema"] != _SCHEMA:
                raise ValueError("unsupported schema")
            if state["lock_identity"] != lock_identity:
                raise ValueError("budget lock was replaced; explicit recovery required")
            if state["scope"] != self.scope:
                raise ValueError("pool belongs to a different boot or PID namespace")
            limits = Resources.from_dict(state["limits"])
            if not isinstance(state["leases"], dict):
                raise ValueError("invalid lease map")
            vectors = []
            for token, record in state["leases"].items():
                if len(token) != 32 or uuid.UUID(hex=token).hex != token:
                    raise ValueError("invalid lease token")
                if not isinstance(record, dict) or set(record) != {"owner", "resources"}:
                    raise ValueError("invalid lease record")
                owner = record["owner"]
                if not isinstance(owner, dict) or set(owner) != {"boot_id", "pid_namespace", "pid", "start_ticks"}:
                    raise ValueError("invalid process identity")
                if {k: owner[k] for k in self.scope} != self.scope:
                    raise ValueError("lease scope mismatch")
                if any(type(owner[k]) is not int or owner[k] <= 0 for k in ("pid_namespace", "pid", "start_ticks")):
                    raise ValueError("invalid process identity values")
                vector = Resources.from_dict(record["resources"])
                if not any(vector.as_dict().values()):
                    raise ValueError("zero-resource lease")
                vectors.append(vector)
            used = _sum(vectors)
            if any(getattr(used, k) > getattr(limits, k) for k in _FIELDS):
                raise ValueError("recorded usage exceeds configured limits")
            return state
        except FileNotFoundError:
            raise
        except (ValueError, TypeError, KeyError, OSError) as error:
            raise BudgetStateError(f"invalid resource ledger: {error}") from error

    def _save(self, target, state):
        fstxn.atomic_write(target, json.dumps(state, sort_keys=True, allow_nan=False) + "\n")

    def _transaction(self, mutate, wait, *, rollback_cancel=False):
        if _scope() != self.scope:
            raise BudgetStateError("process scope changed")
        try:
            with _locked(self.path, wait) as (parent, name, target, _fresh, lock_identity):
                try:
                    state = self._load(parent, name, lock_identity)
                except FileNotFoundError as error:
                    raise BudgetStateError("budget ledger is missing; never automatically reset") from error
                dead = [token for token, record in state["leases"].items() if _dead(record["owner"])]
                for token in dead:
                    del state["leases"][token]
                original = copy.deepcopy(state) if rollback_cancel else None
                result, changed = mutate(state, len(dead))
                wait.check()
                if dead or changed:
                    self._save(target, state)
                    if rollback_cancel:
                        try:
                            wait.check()
                        except (BudgetCancelled, BudgetTimeout):
                            # Keep the existing lock until the committed allocation
                            # has been rolled back. Never reacquire with an unlimited wait.
                            self._save(target, original)
                            raise
                return result
        except FileNotFoundError as error:
            raise BudgetStateError("budget directory or lock is missing") from error

    def acquire(self, resources, *, timeout=0, cancel=None):
        """Atomically reserve all dimensions. None waits, 0 tries once, >0 bounds wait.

        Cancellation accepts threading.Event/multiprocessing.Event's is_set()
        protocol. It does not terminate a process or revoke a delivered lease.
        """
        if not isinstance(resources, Resources):
            raise TypeError("resources must be Resources")
        if not any(resources.as_dict().values()):
            raise ValueError("request must contain at least one resource token")
        wait = _Wait(timeout, cancel)
        owner = _owner()
        token = uuid.uuid4().hex
        lease = Lease(self, token, owner, resources)

        def reserve(state, _dead_count):
            limits = Resources.from_dict(state["limits"])
            for name in _FIELDS:
                requested, limit = getattr(resources, name), getattr(limits, name)
                if requested and not limit:
                    raise ResourceUnavailable(f"{name} unavailable in this pool")
                if requested > limit:
                    raise BudgetExceeded(f"{name} request exceeds configured limit")
            used = _sum(Resources.from_dict(rec["resources"]) for rec in state["leases"].values())
            if any(getattr(used, k) + getattr(resources, k) > getattr(limits, k) for k in _FIELDS):
                return False, False
            wait.check()
            state["leases"][token] = {"owner": owner, "resources": resources.as_dict()}
            return True, True

        while True:
            wait.check()
            if self._transaction(reserve, wait, rollback_cancel=True):
                # Success linearizes under the transaction lock. Cancellation after
                # that point belongs to the caller's running-work lifecycle.
                return lease
            wait.pause()

    def release(self, lease):
        """Release this process's lease; repeated release returns False."""
        if not isinstance(lease, Lease) or lease.budget.path != self.path:
            raise ValueError("lease belongs to another resource pool")
        if lease.owner != _owner():
            raise BudgetStateError("lease belongs to another process; forked children cannot release it")

        def remove(state, _dead_count):
            record = state["leases"].get(lease.token)
            if record is None:
                return False, False
            if record["owner"] != lease.owner or record["resources"] != lease.resources.as_dict():
                raise BudgetStateError("lease identity does not match the ledger")
            del state["leases"][lease.token]
            return True, True

        return self._transaction(remove, _Wait())

    def snapshot(self, *, timeout=None, cancel=None):
        """Reclaim confirmed dead owners and return an independent usage snapshot."""
        def inspect(state, dead_count):
            limits = Resources.from_dict(state["limits"])
            used = _sum(Resources.from_dict(rec["resources"]) for rec in state["leases"].values())
            available = Resources(**{k: getattr(limits, k) - getattr(used, k) for k in _FIELDS})
            return {"limits": limits.as_dict(), "used": used.as_dict(),
                    "available": available.as_dict(), "active_leases": len(state["leases"]),
                    "reclaimed_leases": dead_count}, False

        return self._transaction(inspect, _Wait(timeout, cancel))

    def reap(self, *, timeout=None, cancel=None):
        """Reclaim exited/reused process owners without signalling any process."""
        return self.snapshot(timeout=timeout, cancel=cancel)["reclaimed_leases"]
