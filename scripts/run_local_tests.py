#!/usr/bin/env python3
"""Run a local Linux test command with a hard, verified process-tree memory limit."""

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys


UNIT = "framivra-dev-tests.scope"
CGROUP = Path("/sys/fs/cgroup")
MIB = 1024 * 1024
RESERVE_MIB = 2048
REFUSED = 75
OOM_EXIT = 137


class Refused(RuntimeError):
    pass


def scope_status():
    result = subprocess.run(
        ["systemctl", "--user", "show", UNIT, "-p", "LoadState", "-p", "ActiveState", "-p", "Result", "-p", "ControlGroup"],
        capture_output=True, text=True, timeout=10, check=True, start_new_session=True,
    )
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines())
    if fields.get("LoadState") not in {"loaded", "not-found"} or not fields.get("Result"):
        raise Refused("无法确认测试组状态；未启动测试。")
    return fields["ActiveState"], fields["Result"], fields["ControlGroup"]


def scope_action(action):
    subprocess.run(["systemctl", "--user", action, UNIT], timeout=10, check=True, start_new_session=True)


def checked_exec(limit_mib, command):
    # This process was forked inside the transient scope. Inspect what the
    # kernel actually enforces instead of trusting systemd property acceptance.
    membership = next(line[3:] for line in Path("/proc/self/cgroup").read_text().splitlines() if line.startswith("0::/"))
    path = CGROUP / membership.lstrip("/")
    limit = limit_mib * MIB
    properties = {"memory.max": str(limit), "memory.swap.max": "0", "memory.oom.group": "1"}
    if path.name != UNIT or any((path / name).read_text().strip() != value for name, value in properties.items()):
        raise Refused("测试组的硬上限、禁用交换或整组 OOM 清理未生效；拒绝裸跑。")
    available = next(int(line.split()[1]) * 1024 for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemAvailable:"))
    if available < limit + RESERVE_MIB * MIB:
        raise Refused(f"主机可用内存仅 {available // MIB} MiB；需留出测试上限 {limit_mib} MiB 和宿主余量 {RESERVE_MIB} MiB。")
    for parent in path.parents:
        if parent == CGROUP:
            break
        ceiling = (parent / "memory.max").read_text().strip()
        if ceiling != "max" and int(ceiling) - int((parent / "memory.current").read_text()) < limit:
            raise Refused("父 cgroup 剩余内存不足以容纳测试上限；未启动测试。")
    print(f"本机测试：硬上限 {limit_mib} MiB，交换 0 MiB，触顶终止整组。", file=sys.stderr, flush=True)
    os.execvp(command[0], command)


def run(limit_mib, command, acknowledge):
    import fcntl

    # One scope and one lock per user, shared by all checkouts and sessions.
    runtime = Path(f"/run/user/{os.getuid()}")
    with os.fdopen(os.open(runtime / "framivra-dev-tests.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)) as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused("另一个本机测试正在运行；未启动第二组。") from None
        state, result, group = scope_status()
        if acknowledge:
            if (state, result, group) != ("failed", "oom-kill", ""):
                raise Refused("没有已停止且待确认的测试组 OOM；未启动测试。")
            scope_action("reset-failed")
            print("已确认测试组 OOM，未运行测试。请修复后另行启动。", file=sys.stderr)
            return 0
        if result == "oom-kill":
            raise Refused("上次测试组触发 OOM，拒绝重跑；排查后单独运行本入口的 --ack-oom。")
        if state != "inactive" or group:
            raise Refused(f"测试组未清理（{state}/{result}）；请先检查 {UNIT}。")
        return run_scope(limit_mib, command)


def run_scope(limit_mib, command):
    cancelled = 0

    def defer_signal(signum, _frame):
        nonlocal cancelled
        cancelled = cancelled or signum

    # Signals request cancellation without interrupting stop/verification and
    # releasing the lock while descendants are still live in the named scope.
    previous = {signum: signal.signal(signum, defer_signal) for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        try:
            with subprocess.Popen([
                "systemd-run", "--user", "--scope", "--quiet", f"--unit={UNIT}",
                f"--property=MemoryMax={limit_mib * MIB}", "--property=MemorySwapMax=0",
                "--property=OOMPolicy=kill", "--property=TimeoutStopSec=5s", "--",
                sys.executable, "-I", str(Path(__file__).resolve()), "_in_scope", str(limit_mib), *command,
            ]) as process:
                while True:
                    if cancelled:
                        process.kill()
                    try:
                        rc = process.wait(timeout=0.25)
                        break
                    except subprocess.TimeoutExpired:
                        pass
        finally:
            state, result, group = scope_status()
            if state not in {"inactive", "failed"} or group:
                # Also reap inherited children when a test runner exits first.
                scope_action("stop")
                state, result, group = scope_status()
                if state not in {"inactive", "failed"} or group:
                    raise Refused(f"无法确认测试组已停止，请查看 {UNIT}。")
            if result == "oom-kill":
                print("测试组发生 OOM，已停止；再次启动前须人工排查并确认。", file=sys.stderr)
            elif state == "failed":
                # SIGTERM-ignoring descendants can cause a stop timeout even
                # though the manager killed them and the cgroup is now empty.
                scope_action("reset-failed")
        if result == "oom-kill":
            return OOM_EXIT
        if cancelled:
            return 128 + cancelled
        if result != "success":
            raise Refused(f"测试组异常停止（{state}/{result}）；请查看 {UNIT}。")
        return rc if rc >= 0 else 128 - rc
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-mib", type=int, default=8192, help="hard group limit in MiB (default: %(default)s)")
    parser.add_argument("--ack-oom", action="store_true", help="acknowledge a stopped test-group OOM; never run tests")
    parser.add_argument("command", nargs=argparse.REMAINDER, metavar="-- COMMAND [ARG ...]")
    try:
        if sys.platform != "linux":
            raise Refused("此入口需要 Linux cgroup v2 和 systemd 用户管理器；不会裸跑。")
        if argv[:1] == ["_in_scope"]:
            checked_exec(int(argv[1]), argv[2:])
        args = parser.parse_args(argv)
        command = args.command[1:] if args.command[:1] == ["--"] else []
        if args.memory_mib < 64 or args.ack_oom == bool(command) or (args.ack_oom and args.command):
            parser.error("use --ack-oom alone or [--memory-mib >=64] -- COMMAND [ARG ...]")
        # Turn host cancellation into normal unwinding so the scope is reaped.
        def interrupted(signum, _frame):
            raise KeyboardInterrupt(signum)
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGHUP, interrupted)
        return run(args.memory_mib, command, args.ack_oom)
    except KeyboardInterrupt as error:
        return 128 + (error.args[0] if error.args else signal.SIGINT)
    except (Refused, OSError, StopIteration, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"本机测试未运行或已停止：{error}", file=sys.stderr)
        return REFUSED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
