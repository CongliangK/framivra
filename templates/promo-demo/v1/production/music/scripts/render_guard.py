#!/usr/bin/env python3
"""Finite Linux supervisor for this task's own Python/FFmpeg process tree.

Samples aggregate RSS (not RLIMIT_AS) and host MemAvailable every 50 ms.
Tracks the newly created session and observed descendants, including a child
that later changes session. Not a sandbox against deliberate daemon escape.
Only pidfds whose /proc identity was checked are signalled. No global kills,
system configuration, persistent process, or product-source modification.
"""
import argparse
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

GIB = 1024 ** 3
PAGE = os.sysconf("SC_PAGE_SIZE")


def memory():
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        if key in ("MemTotal", "MemAvailable"):
            values[key] = int(value.split()[0]) * 1024
    return values["MemTotal"], values["MemAvailable"]


def proc(pid):
    # comm may contain spaces and parentheses; fields begin after its last ')'.
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return {"pid": pid, "state": fields[0], "ppid": int(fields[1]),
            "sid": int(fields[3]), "start": int(fields[19]),
            "rss": max(0, int(fields[21])) * PAGE}


def scan():
    result = {}
    for item in Path("/proc").iterdir():
        if item.name.isdigit():
            try:
                result[int(item.name)] = proc(int(item.name))
            except (FileNotFoundError, ProcessLookupError):
                pass  # Process exited during the scan.
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--cap-gb", type=float, default=6)
    parser.add_argument("--floor-gb", type=float, default=6)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or any(not math.isfinite(x) or x <= 0 for x in
                          (args.timeout, args.cap_gb, args.floor_gb)):
        parser.error("command and positive finite limits are required")
    report = Path(args.report)
    # Never overwrite an existing attempt, including on an eventual failure.
    with report.open("x") as handle:
        handle.write('{"status":"STARTING"}\n')
    start = time.monotonic()
    child = None
    owned = {}  # pid -> (start ticks, pidfd); descriptors pin the PID identity.
    result = {"command": command, "status": "GUARD_ERROR", "returncode": 125,
              "cap_bytes": int(args.cap_gb * GIB), "timeout_s": args.timeout,
              "interval_s": 0.05, "peak_tree_rss_bytes": 0, "samples": 0,
              "scope": "own session plus observed descendants; sampled, not a sandbox"}

    def collect():
        table = scan()
        wanted = {pid for pid, row in table.items() if row["sid"] == child.pid}
        wanted.update(pid for pid, (birth, _) in owned.items()
                      if pid in table and table[pid]["start"] == birth)
        while True:
            expanded = wanted | {pid for pid, row in table.items()
                                 if row["ppid"] in wanted}
            if expanded == wanted:
                break
            wanted = expanded
        live = []
        for pid in wanted:
            row = table[pid]
            if pid not in owned:
                try:
                    fd = os.pidfd_open(pid)
                    current = proc(pid)
                    if current["start"] != row["start"]:
                        os.close(fd)
                        continue
                    owned[pid] = (row["start"], fd)
                except (FileNotFoundError, ProcessLookupError):
                    continue
            if owned[pid][0] == row["start"] and row["state"] not in ("Z", "X"):
                live.append(row)
        return live

    def stop():
        # Re-scan while killing so ordinary children still created during cleanup
        # are included. All targets use identity-pinned pidfds owned by this run.
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            live = collect()
            if not live:
                return []
            for row in live:
                try:
                    signal.pidfd_send_signal(owned[row["pid"]][1], signal.SIGKILL)
                except ProcessLookupError:
                    pass
            time.sleep(0.05)
        return [row["pid"] for row in collect()]

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt("guard received SIGTERM")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        total, available = memory()
        floor = max(int(args.floor_gb * GIB), int(total * 0.20))
        result.update(floor_bytes=floor, initial_mem_available_bytes=available,
                      min_mem_available_bytes=available)
        if available < floor:
            result.update(status="LOW_MEMORY_BEFORE_START", returncode=125)
        else:
            # Check required safe signalling support before launching anything.
            if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
                raise RuntimeError("Linux pidfd support is required")
            child = subprocess.Popen(command, start_new_session=True)
            root_fd = os.pidfd_open(child.pid)
            owned[child.pid] = (proc(child.pid)["start"], root_fd)
            result["root_pid"] = child.pid
            while True:
                live = collect()
                _, available = memory()
                rss = sum(row["rss"] for row in live)
                result["samples"] += 1
                result["peak_tree_rss_bytes"] = max(result["peak_tree_rss_bytes"], rss)
                result["min_mem_available_bytes"] = min(result["min_mem_available_bytes"], available)
                if rss > result["cap_bytes"]:
                    result.update(status="TREE_RSS_LIMIT", returncode=125)
                    break
                if available < floor:
                    result.update(status="LOW_MEMORY", returncode=125)
                    break
                if time.monotonic() - start > args.timeout:
                    result.update(status="TIMEOUT", returncode=124)
                    break
                code = child.poll()
                if code is not None:
                    if collect():
                        result.update(status="DESCENDANTS_AFTER_EXIT", returncode=125)
                    else:
                        result.update(status="COMPLETED", returncode=code if code >= 0 else 128 - code)
                    result["child_returncode"] = code
                    break
                time.sleep(0.05)
    except (Exception, KeyboardInterrupt) as error:
        result["error"] = f"{type(error).__name__}: {error}"
    finally:
        if child is not None:
            try:
                remaining = stop()
                result["remaining_owned_pids"] = remaining
                if remaining:
                    result.update(status="CLEANUP_INCOMPLETE", returncode=125)
                child.wait(timeout=3)
            except Exception as error:
                # A /proc inspection error must not leave known children alive.
                for _, fd in owned.values():
                    try:
                        signal.pidfd_send_signal(fd, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                result.update(status="CLEANUP_ERROR", returncode=125,
                              cleanup_error=f"{type(error).__name__}: {error}")
        for _, fd in owned.values():
            os.close(fd)
        result["elapsed_s"] = time.monotonic() - start
        report.write_text(json.dumps(result, indent=2) + "\n")
        print("RENDER_GUARD " + json.dumps(result, sort_keys=True), file=sys.stderr)
    return result["returncode"]


if __name__ == "__main__":
    raise SystemExit(main())
