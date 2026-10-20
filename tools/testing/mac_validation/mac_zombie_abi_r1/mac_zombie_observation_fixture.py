"""SOURCE-ONLY REVIEW FIXTURE. Not approved for execution.

One self-owned direct child exits normally. No child/group signals, group
changes, external target PIDs, production commands, installs or permission
changes. A ps query timeout may terminate only its own ps helper. The 10-second
deadline is cooperative: it cannot preempt a kernel call or guarantee finally
after interpreter/OS termination. Failure to prove reap/absence stays UNKNOWN.
"""
import argparse
import ctypes
import errno
import hashlib
import importlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

OWNER_SHA256 = '6f5c92471f4479b0bf4ba94a75cf079767b142598fc7aec04741a9031fd0ddd6'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--deadline-seconds', type=float, default=10)
    args = parser.parse_args()
    if sys.platform != 'darwin' or not 0 < args.deadline_seconds <= 10:
        raise RuntimeError('Requires Darwin and an explicit at-most-10-second cooperative budget')
    started = time.monotonic()
    deadline = started + args.deadline_seconds
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        raise RuntimeError('Inherited SIGCHLD disposition is not default; no child created')
    repo = Path(args.repo).resolve()
    owner_path = repo / 'vtx' / 'proc_supervisor.py'
    if hashlib.sha256(owner_path.read_bytes()).hexdigest() != OWNER_SHA256:
        raise RuntimeError('Owner source digest differs from the reviewed frozen ABI')
    out = Path(args.out).resolve()
    if out.exists():
        raise RuntimeError('Evidence path must be new; no overwriting prior attempts')
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(repo))
    owner = importlib.import_module('vtx.proc_supervisor')
    if Path(owner.__file__).resolve() != owner_path or owner._DARWIN_PROC is None:
        raise RuntimeError('Frozen owner import or existing libproc unavailable')
    record = dict(status='UNKNOWN', owner_sha256=OWNER_SHA256, kernel=os.uname().release,
                  parent_pid=os.getpid(), inherited_pgid=os.getpgid(0),
                  deadline_seconds=args.deadline_seconds, child_signals_sent=0, child_pid=None,
                  sigchld_disposition='DEFAULT_VERIFIED_READ_ONLY',
                  ps_timeout_policy='subprocess.run may terminate and reap its own ps helper')
    child = None

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError('Fixture cooperative deadline reached')
        return value

    def ps_one(pid):
        argv = ['/bin/ps', '-p', str(pid), '-o', 'pid=,ppid=,pgid=,stat=,rss=']
        before = time.monotonic()
        answer = subprocess.run(argv, capture_output=True, text=True, check=False,
                                timeout=min(2, remaining()))
        result = dict(argv=argv, started_monotonic=before, finished_monotonic=time.monotonic(),
                      returncode=answer.returncode, stdout=answer.stdout, stderr=answer.stderr)
        remaining()
        return result

    def bsd_info(arg):
        remaining()
        info = owner._DarwinBSDInfo()
        expected = ctypes.sizeof(info)
        before = time.monotonic()
        ctypes.set_errno(0)
        count = owner._DARWIN_PROC.proc_pidinfo(child, 3, arg, ctypes.byref(info), expected)
        result = dict(flavor=3, arg=arg, returned_bytes=count, expected_bytes=expected,
                      errno=ctypes.get_errno(), started_monotonic=before,
                      finished_monotonic=time.monotonic())
        if count == expected:
            result['kernel'] = dict(pid=info.pid, ppid=info.ppid, pgid=info.pgid,
                status=info.status, start_sec=info.start_sec, start_usec=info.start_usec)
        remaining()
        return result

    try:
        remaining()
        child = os.fork()
        if child == 0:
            os._exit(0)
        record['child_pid'] = child
        time.sleep(min(.05, remaining()))
        record['zombie_ps'] = ps_one(child)
        record['live_arg_zero'] = bsd_info(0)
        record['terminal_arg_one'] = bsd_info(1)
    except Exception as error:
        record['error'] = type(error).__name__ + ': ' + str(error)
    finally:
        if child is not None:
            try:
                while True:
                    reaped, status = os.waitpid(child, os.WNOHANG)
                    if reaped == child:
                        record['reap'] = dict(pid=reaped, wait_status=status,
                            exited=os.WIFEXITED(status),
                            exitcode=os.WEXITSTATUS(status) if os.WIFEXITED(status) else None)
                        break
                    time.sleep(min(.01, remaining()))
                record['after_reap_ps'] = ps_one(child)
            except Exception as error:
                record['cleanup_error'] = type(error).__name__ + ': ' + str(error)
        record['wall_seconds'] = time.monotonic() - started

    try:
        observed = record['zombie_ps']['stdout'].split()
        proof = record['terminal_arg_one']
        kernel = proof['kernel']
        passed = (
            not record.get('error') and not record.get('cleanup_error')
            and record['wall_seconds'] <= args.deadline_seconds
            and record['zombie_ps']['returncode'] == 0 and len(observed) == 5
            and int(observed[0]) == child and int(observed[1]) == record['parent_pid']
            and int(observed[2]) == record['inherited_pgid'] and observed[3].startswith('Z')
            and record['live_arg_zero']['returned_bytes'] == 0
            and record['live_arg_zero']['errno'] == errno.ESRCH
            and proof['returned_bytes'] == proof['expected_bytes']
            and kernel['pid'] == child and kernel['ppid'] == record['parent_pid']
            and kernel['pgid'] == record['inherited_pgid'] and kernel['status'] == 5
            and kernel['start_sec'] > 0 and 0 <= kernel['start_usec'] < 1_000_000
            and record['reap']['exited'] and record['reap']['exitcode'] == 0
            and record['after_reap_ps']['returncode'] == 1
            and not record['after_reap_ps']['stdout'].strip()
        )
        if passed:
            record['status'] = 'PASS_ORDINARY_ZOMBIE_ABI_ONLY'
    except (KeyError, TypeError, ValueError):
        pass
    with out.open('x') as stream:
        json.dump(record, stream, indent=2, allow_nan=False)
        stream.write('\n')
    return 0 if record['status'] == 'PASS_ORDINARY_ZOMBIE_ABI_ONLY' else 1


if __name__ == '__main__':
    raise SystemExit(main())
