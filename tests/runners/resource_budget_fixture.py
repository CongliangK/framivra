#!/usr/bin/env python3
"""Retain synthetic cross-project budget evidence in a NEW trusted directory.

Linux only, standard library only. No GPU/backend invocation or process signals.
The output parent must exist; symlinks (including ancestors) are rejected.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from vtx.resource_budget import (BudgetCancelled, BudgetExceeded, BudgetTimeout,
                                 ResourceBudget, Resources, ResourceUnavailable,
                                 detect_inventory)

WORKERS = 8
ITERATIONS = 12


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def write_json(path, value):
    with open(path, 'x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')


def new_output(path):
    """Walk existing parents without following symlinks; exclusively create leaf."""
    if ".." in Path(path).parts:
        raise OSError("parent traversal is not accepted")
    path = Path(os.path.abspath(path))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        os.mkdir(path.name, mode=0o700, dir_fd=descriptor)
        child = os.open(path.name, flags, dir_fd=descriptor)
        try:
            os.fchdir(child)
        finally:
            os.close(child)
    finally:
        os.close(descriptor)
    return Path.cwd()


def record(stream, event, **values):
    stream.write(json.dumps(dict(event=event, time_ns=time.monotonic_ns(), **values),
                            sort_keys=True) + '\n')
    stream.flush()


def bounded(snapshot):
    check(all(0 <= snapshot['used'][key] <= snapshot['limits'][key]
              for key in snapshot['used']), 'token limit violated')
    return snapshot


def worker(ledger, request, crash=False):
    budget = ResourceBudget(ledger)
    request = Resources(**json.loads(request))
    if crash:
        budget.acquire(request)
        print(json.dumps({'event': 'crash_acquired', 'snapshot': bounded(budget.snapshot())}),
              flush=True)
        os._exit(17)  # self-exit only: deliberately leave the committed lease
    for iteration in range(ITERATIONS):
        with budget.acquire(request, timeout=20):
            snapshot = bounded(budget.snapshot())
            check(snapshot['active_leases'] >= 2, 'coordinator lease missing')
            record(sys.stdout, 'acquired', project=Path.cwd().name,
                   pid=os.getpid(), iteration=iteration, snapshot=snapshot)
            # Synthetic CPU work only; no memory/disk-sized allocation or GPU work.
            checksum = sum(value * value for value in range(1000))
            time.sleep(.005)
        record(sys.stdout, 'released', iteration=iteration, checksum=checksum)


def expect(stream, budget, exception, event, request, **kwargs):
    before = budget.snapshot()
    try:
        budget.acquire(request, **kwargs)
    except exception:
        after = bounded(budget.snapshot())
        check(after['used'] == before['used'] and
              after['active_leases'] == before['active_leases'], 'failed request leaked')
        record(stream, event, snapshot=after)
    else:
        raise RuntimeError(f'{event}: expected {exception.__name__}')


def run(out, events):
    inventory = detect_inventory(out)
    write_json("inventory.json", inventory.as_dict())
    measured = inventory.limits
    check(min(measured.cpu_millis, measured.ram_bytes, measured.disk_bytes) >= 4,
          'insufficient measured inventory; unavailable resources are not invented')
    limits = Resources(measured.cpu_millis // 2, measured.ram_bytes // 2,
                       12, measured.disk_bytes // 2)
    request = Resources(*(value // 2 for value in limits.as_dict().values()))
    write_json('config.json', dict(schema=1, inventory=inventory.as_dict(),
        limits=limits.as_dict(), request=request.as_dict(), workers=WORKERS,
        iterations_per_worker=ITERATIONS, physical_peak_claim=False,
        vram='SIMULATED abstract byte tokens; no GPU probe or workload'))
    ledger = out / 'pool.json'
    budget = ResourceBudget.create(ledger, limits)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    command = [sys.executable, '-B', str(Path(__file__).resolve())]
    children = []
    streams = []
    try:
        # Keep half the pool occupied while independent project processes compete.
        with budget.acquire(request):
            record(events, 'coordinator_acquired', snapshot=bounded(budget.snapshot()))
            for number in range(WORKERS):
                project = out / f'project-{number}'
                project.mkdir()
                stdout = open(project / 'events.jsonl', 'x', encoding='utf-8')
                stderr = open(project / 'stderr.txt', 'x', encoding='utf-8')
                streams.extend([stdout, stderr])
                children.append(subprocess.Popen(command + ['--worker', str(ledger),
                    json.dumps(request.as_dict())], cwd=project, env=env,
                    stdout=stdout, stderr=stderr))
            # Workers have finite acquisition deadlines and no external workloads.
            # Do not signal even on failure: retain files and reap all owned workers.
            codes = [child.wait() for child in children]
            check(all(code == 0 for code in codes), f'worker exit codes: {codes}')
    finally:
        for child in children:
            child.wait()
        for stream in streams:
            stream.close()
    acquisitions = 0
    for number in range(WORKERS):
        with open(out / f'project-{number}' / 'events.jsonl', encoding='utf-8') as source:
            rows = [json.loads(line) for line in source]
        check(len(rows) == ITERATIONS * 2, 'incomplete worker evidence')
        acquisitions += sum(row['event'] == 'acquired' for row in rows)
    check(budget.snapshot()['active_leases'] == 0, 'competition leaked')
    record(events, 'competition_complete', acquisitions=acquisitions,
           projects=WORKERS, snapshot=bounded(budget.snapshot()))
    # One dimension oversize while the others fit: failure must be all-or-none.
    for dimension in limits.as_dict():
        values = request.as_dict()
        values[dimension] = limits.as_dict()[dimension] + 1
        expect(events, budget, BudgetExceeded, f'oversize_{dimension}', Resources(**values))
    with budget.acquire(limits):
        expect(events, budget, BudgetExceeded, 'full_rejected', request)
        expect(events, budget, BudgetTimeout, 'timeout', request, timeout=.03)
        cancel = threading.Event()
        timer = threading.Timer(.03, cancel.set)
        timer.start()
        try:
            expect(events, budget, BudgetCancelled, 'cancel_wait', request,
                   timeout=2, cancel=cancel)
        finally:
            timer.join()
    with open('crash-events.jsonl', 'x', encoding='utf-8') as stdout, \
            open('crash-stderr.txt', 'x', encoding='utf-8') as stderr:
        child = subprocess.Popen(command + ['--crash', str(ledger),
            json.dumps(limits.as_dict())], cwd=out / 'project-0', env=env,
            stdout=stdout, stderr=stderr)
        check(child.wait() == 17, 'crash fixture did not self-exit as expected')
    with open(ledger, encoding='utf-8') as source:
        write_json('crash-ledger-before-reclaim.json', json.load(source))
    reopened = ResourceBudget(ledger)
    snapshot = bounded(reopened.snapshot())
    check(snapshot['reclaimed_leases'] == 1 and snapshot['active_leases'] == 0,
          'reopen did not reclaim exactly one crashed lease')
    record(events, 'reopen_reclaim', snapshot=snapshot)
    with reopened.acquire(limits):
        record(events, 'reacquire_all', snapshot=bounded(reopened.snapshot()))
    snapshot = bounded(reopened.snapshot())
    check(snapshot['active_leases'] == 0 and not any(snapshot['used'].values()), 'final leak')
    record(events, 'final_empty', snapshot=snapshot)
    missing = ResourceBudget.create(out / 'unavailable.json', Resources(cpu_millis=1))
    expect(events, missing, ResourceUnavailable, 'unknown_vram_unavailable', Resources(vram_bytes=1))
    return dict(result='PASS', acquisitions=acquisitions, projects=WORKERS,
                physical_peak_claim=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', help='new evidence directory under an existing trusted parent')
    parser.add_argument('--worker', nargs=2, help=argparse.SUPPRESS)
    parser.add_argument('--crash', nargs=2, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker or args.crash:
        worker(*(args.worker or args.crash), crash=bool(args.crash))
        return 0
    if not args.output:
        parser.error('--output is required (use --help for a read-only preview)')
    try:
        out = new_output(args.output)
    except OSError as error:
        parser.error(f'output refused: {error}')
    with open('events.jsonl', 'x', encoding='utf-8') as events:
        try:
            result = run(out, events)
        except BaseException as error:
            record(events, 'failure', error_type=type(error).__name__, message=str(error))
            write_json('result.json', dict(result='FAIL', error_type=type(error).__name__,
                                           message=str(error), physical_peak_claim=False))
            traceback.print_exc()
            return 1
        write_json('result.json', result)
        record(events, 'complete', **result)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
