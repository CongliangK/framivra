"""Issue #44: CPU-only resource accounting, no GPU process or system changes."""
import contextlib
import dataclasses
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from vtx import fstxn, resource_budget as rb

REPO = Path(__file__).resolve().parents[2]
WORKER = r'''
import json, os, sys, time
from pathlib import Path
from vtx import resource_budget as rb
path, mode = Path(sys.argv[1]), sys.argv[2]
budget = rb.ResourceBudget(path)
if mode == "compete":
    for _ in range(12):
        with budget.acquire(rb.Resources(250, 10, 3, 5), timeout=10):
            snap = budget.snapshot()
            assert all(snap["used"][k] <= snap["limits"][k] for k in snap["used"]), snap
            time.sleep(.003)
    print("ok", flush=True)
elif mode == "hold":
    lease = budget.acquire(rb.Resources(1000, 40, 12, 20))
    print("ready", flush=True)
    sys.stdin.readline()
    lease.release()
elif mode in ("crash", "exit"):
    budget.acquire(rb.Resources(1000, 40, 12, 20))
    print("ready", flush=True)
    if mode == "crash":
        os._exit(17)
elif mode == "lock":
    with fstxn.locked_write(path) as tmp:
        tmp.write_text(path.read_text())
        print("ready", flush=True)
        sys.stdin.readline()
'''.replace('from vtx import resource_budget as rb', 'from vtx import fstxn, resource_budget as rb')


@unittest.skipUnless(os.path.exists('/proc/self/stat') and fstxn.fcntl is not None,
                     'requires Linux /proc and flock')
class BudgetCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'pool.json'
        self.limits = rb.Resources(1000, 40, 12, 20)
        self.budget = rb.ResourceBudget.create(self.path, self.limits)
        self.children = []
        self.addCleanup(self.cleanup_children)

    def cleanup_children(self):
        # Only subprocesses explicitly created by this test are eligible.
        for proc in self.children:
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=10)

    def spawn(self, mode):
        proc = subprocess.Popen(['env', 'python3', '-u', '-c', WORKER, str(self.path), mode],
                                cwd=REPO, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        self.children.append(proc)
        return proc

    def assert_empty(self):
        snap = self.budget.snapshot()
        self.assertEqual(snap['used'], rb.Resources().as_dict())
        self.assertEqual(snap['active_leases'], 0)

    def write_state(self, transform):
        state = json.loads(self.path.read_text())
        transform(state)
        self.path.write_text(json.dumps(state))

    def test_create_idempotent_and_limit_conflict(self):
        self.assertEqual(rb.ResourceBudget.create(self.path, self.limits).path, self.path)
        with self.assertRaises(rb.BudgetStateError):
            rb.ResourceBudget.create(self.path, rb.Resources(cpu_millis=2000))
        self.assert_empty()

    def test_all_dimensions_release_and_context_exception(self):
        lease = self.budget.acquire(self.limits)
        self.assertEqual(self.budget.snapshot()['used'], self.limits.as_dict())
        self.assertTrue(lease.release())
        self.assertFalse(lease.release())
        with self.assertRaisesRegex(RuntimeError, 'task failed'):
            with self.budget.acquire(self.limits):
                raise RuntimeError('task failed')
        self.assert_empty()
        self.assertEqual(list(self.path.parent.glob('.*.tmp')), [])
        self.assertTrue(self.path.with_suffix('.json.lock').is_file())

    def test_resources_reject_invalid_and_empty_requests(self):
        for value in (-1, True, 1.1, float('nan'), float('inf'), '1', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rb.Resources(cpu_millis=value)
        with self.assertRaises(ValueError):
            self.budget.acquire(rb.Resources())
        for timeout in (-1, True, float('nan'), float('inf'), '1'):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.budget.acquire(rb.Resources(cpu_millis=1), timeout=timeout)
        with self.assertRaises(TypeError):
            self.budget.acquire(rb.Resources(cpu_millis=1), cancel=object())
        self.assert_empty()

    def test_impossible_request_fails_without_waiting_or_partial_allocation(self):
        for name in self.limits.as_dict():
            vector = self.limits.as_dict()
            vector[name] += 1
            started = time.monotonic()
            with self.subTest(dimension=name), self.assertRaises(rb.BudgetExceeded):
                self.budget.acquire(rb.Resources(**vector), timeout=None)
            self.assertLess(time.monotonic() - started, 1)
            self.assert_empty()

    def test_atomic_multidimensional_rejection(self):
        with self.budget.acquire(rb.Resources(ram_bytes=40)):
            with self.assertRaises(rb.BudgetExceeded):
                self.budget.acquire(rb.Resources(cpu_millis=1000, ram_bytes=1, vram_bytes=12, disk_bytes=20))
            self.assertEqual(self.budget.snapshot()['used'], rb.Resources(ram_bytes=40).as_dict())
            with self.budget.acquire(rb.Resources(cpu_millis=1000, vram_bytes=12, disk_bytes=20)):
                self.assertEqual(self.budget.snapshot()['used'], self.limits.as_dict())
        self.assert_empty()

    def test_no_resource_and_missing_gpu_are_unavailable(self):
        empty = rb.ResourceBudget.create(self.path.parent / 'empty.json', rb.Resources())
        for name in self.limits.as_dict():
            with self.subTest(dimension=name), self.assertRaises(rb.ResourceUnavailable):
                empty.acquire(rb.Resources(**{name: 1}), timeout=None)
        no_gpu = rb.ResourceBudget.create(self.path.parent / 'cpu.json', rb.Resources(cpu_millis=1000))
        with self.assertRaises(rb.ResourceUnavailable):
            no_gpu.acquire(rb.Resources(cpu_millis=1, vram_bytes=1))
        self.assertEqual(no_gpu.snapshot()['active_leases'], 0)

    def test_timeout_and_cancel_waiting_for_capacity(self):
        cancel = threading.Event()
        with self.budget.acquire(self.limits):
            started = time.monotonic()
            with self.assertRaises(rb.BudgetTimeout):
                self.budget.acquire(rb.Resources(cpu_millis=1), timeout=.07)
            self.assertLess(time.monotonic() - started, .8)
            timer = threading.Timer(.05, cancel.set)
            timer.start()
            try:
                with self.assertRaises(rb.BudgetCancelled):
                    self.budget.acquire(rb.Resources(cpu_millis=1), timeout=None, cancel=cancel)
            finally:
                timer.join()
            self.assertEqual(self.budget.snapshot()['active_leases'], 1)
        self.assert_empty()

    def test_pre_cancel_and_post_commit_cancel_leave_no_allocation(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(rb.BudgetCancelled):
            self.budget.acquire(self.limits, cancel=cancel)
        cancel.clear()
        real_save = self.budget._save

        def save_then_cancel(target, state):
            real_save(target, state)
            cancel.set()

        with mock.patch.object(self.budget, '_save', side_effect=save_then_cancel):
            with self.assertRaises(rb.BudgetCancelled):
                self.budget.acquire(self.limits, cancel=cancel)
        self.assert_empty()

    def test_post_commit_cancel_rolls_back_without_reacquiring_lock(self):
        cancel = threading.Event()
        real_save = self.budget._save
        contender_started = threading.Event()
        contenders = []
        calls = 0

        def contender():
            contender_started.set()
            with fstxn._thread_lock_for(self.path):
                time.sleep(.3)

        def save_then_contend(target, state):
            nonlocal calls
            real_save(target, state)
            calls += 1
            if calls == 1:
                thread = threading.Thread(target=contender)
                contenders.append(thread)
                thread.start()
                self.assertTrue(contender_started.wait(1))
                cancel.set()

        started = time.monotonic()
        try:
            with mock.patch.object(self.budget, '_save', side_effect=save_then_contend):
                with mock.patch.object(self.budget, 'release', side_effect=AssertionError('must not reacquire')):
                    with self.assertRaises(rb.BudgetCancelled):
                        self.budget.acquire(self.limits, timeout=.1, cancel=cancel)
            self.assertLess(time.monotonic() - started, .25)
        finally:
            for thread in contenders:
                thread.join(timeout=3)
        self.assert_empty()

    def test_post_commit_timeout_rolls_back_under_same_lock(self):
        real_save = self.budget._save
        calls = 0

        def slow_commit(target, state):
            nonlocal calls
            real_save(target, state)
            calls += 1
            if calls == 1:
                time.sleep(.04)

        with mock.patch.object(self.budget, '_save', side_effect=slow_commit):
            with self.assertRaises(rb.BudgetTimeout):
                self.budget.acquire(self.limits, timeout=.02)
        self.assert_empty()

    def test_failed_cancel_rollback_preserves_charge_and_fails_closed(self):
        cancel = threading.Event()
        real_save = self.budget._save
        calls = 0

        def fail_rollback(target, state):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise OSError('storage failed during rollback')
            real_save(target, state)
            cancel.set()

        with mock.patch.object(self.budget, '_save', side_effect=fail_rollback):
            with self.assertRaisesRegex(OSError, 'rollback'):
                self.budget.acquire(self.limits, cancel=cancel)
        self.assertEqual(self.budget.snapshot()['used'], self.limits.as_dict())
        with self.assertRaises(rb.BudgetExceeded):
            self.budget.acquire(rb.Resources(cpu_millis=1))

    def test_timeout_and_cancel_cover_fstxn_cross_process_lock(self):
        child = self.spawn('lock')
        self.assertEqual(child.stdout.readline().strip(), 'ready')
        cancel = threading.Event()
        with self.assertRaises(rb.BudgetExceeded):
            self.budget.acquire(self.limits)
        started = time.monotonic()
        with self.assertRaises(rb.BudgetTimeout):
            self.budget.acquire(self.limits, timeout=.07)
        self.assertLess(time.monotonic() - started, .8)
        timer = threading.Timer(.05, cancel.set)
        timer.start()
        try:
            with self.assertRaises(rb.BudgetCancelled):
                self.budget.acquire(self.limits, timeout=None, cancel=cancel)
        finally:
            timer.join()
        stdout, stderr = child.communicate('\n', timeout=5)
        self.assertEqual(child.returncode, 0, stderr)
        self.assert_empty()

    def test_timeout_includes_shared_thread_lock(self):
        lock = fstxn._thread_lock_for(self.path)
        lock.acquire()
        try:
            with self.assertRaises(rb.BudgetTimeout):
                self.budget.acquire(self.limits, timeout=.05)
        finally:
            lock.release()
        self.assert_empty()

    def test_waiter_succeeds_after_release(self):
        holder = self.budget.acquire(self.limits)
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(self.budget.acquire, self.limits, timeout=2)
            time.sleep(.05)
            holder.release()
            result.result(timeout=3).release()
        self.assert_empty()

    def test_process_competition_never_overallocates(self):
        workers = [self.spawn('compete') for _ in range(8)]
        for child in workers:
            stdout, stderr = child.communicate(timeout=30)
            self.assertEqual(child.returncode, 0, stderr)
            self.assertEqual(stdout.strip(), 'ok')
        self.assert_empty()

    def test_thread_competition_never_overallocates(self):
        def work(_):
            for _ in range(8):
                with self.budget.acquire(rb.Resources(250, 10, 3, 5), timeout=5):
                    snap = self.budget.snapshot()
                    self.assertTrue(all(snap['used'][k] <= snap['limits'][k] for k in snap['used']))
                    time.sleep(.001)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(work, range(8)))
        self.assert_empty()

    def test_live_process_is_not_reaped_and_exit_is_reclaimed(self):
        child = self.spawn('hold')
        self.assertEqual(child.stdout.readline().strip(), 'ready')
        self.assertEqual(self.budget.reap(), 0)
        with self.assertRaises(rb.BudgetExceeded):
            self.budget.acquire(rb.Resources(cpu_millis=1))
        child.kill()  # only the test-owned child; budget itself never sends signals
        child.wait(timeout=5)
        self.assertEqual(self.budget.reap(), 1)
        self.assertEqual(self.budget.reap(), 0)
        self.assert_empty()

    def test_abnormal_exit_and_zombie_reclaimed(self):
        child = self.spawn('crash')
        self.assertEqual(child.stdout.readline().strip(), 'ready')
        # Don't wait/reap the OS child first: the budget must handle zombies.
        deadline = time.monotonic() + 3
        while self.budget.reap() == 0 and time.monotonic() < deadline:
            time.sleep(.01)
        self.assert_empty()
        child.wait(timeout=5)
        self.assertEqual(child.returncode, 17)

    def test_normal_process_exit_without_release_is_reclaimed(self):
        child = self.spawn('exit')
        stdout, stderr = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 0, stderr)
        self.assertEqual(stdout.strip(), 'ready')
        self.assertEqual(self.budget.reap(), 1)
        self.assert_empty()

    def test_pid_reuse_reclaims_old_identity_without_signal(self):
        lease = self.budget.acquire(self.limits)
        self.write_state(lambda state: state['leases'][lease.token]['owner'].__setitem__('start_ticks', lease.owner['start_ticks'] + 1))
        with mock.patch('os.kill', side_effect=AssertionError('must not signal any PID')):
            self.assertEqual(self.budget.reap(), 1)
        self.assert_empty()

    def test_unreadable_live_process_remains_charged(self):
        with self.budget.acquire(self.limits):
            with mock.patch.object(rb.proc_supervisor, 'proc_starttime', return_value=None):
                self.assertEqual(self.budget.reap(), 0)
                self.assertEqual(self.budget.snapshot()['used'], self.limits.as_dict())
        self.assert_empty()

    def test_forked_or_forged_owner_cannot_release(self):
        lease = self.budget.acquire(self.limits)
        changed = dict(lease.owner, pid=lease.owner['pid'] + 1)
        with mock.patch.object(rb, '_owner', return_value=changed), self.assertRaises(rb.BudgetStateError):
            lease.release()
        forged = dataclasses.replace(lease, resources=rb.Resources(cpu_millis=1))
        with self.assertRaises(rb.BudgetStateError):
            forged.release()
        self.assertEqual(self.budget.snapshot()['active_leases'], 1)
        lease.release()

    def test_corrupt_state_never_resets_or_grants(self):
        lease = self.budget.acquire(self.limits)
        original = self.path.read_text()
        mutations = [
            lambda s: s.__setitem__('schema', True),
            lambda s: s.__setitem__('schema', 999),
            lambda s: s.__setitem__('leases', []),
            lambda s: s['limits'].__setitem__('cpu_millis', -1),
            lambda s: s['limits'].__setitem__('cpu_millis', True),
            lambda s: s['limits'].__setitem__('cpu_millis', 999),
            lambda s: s['leases'][lease.token]['owner'].__setitem__('pid', True),
            lambda s: s['leases'][lease.token]['owner'].__setitem__('start_ticks', 0),
            lambda s: s['scope'].__setitem__('boot_id', 'old-boot'),
            lambda s: s['scope'].__setitem__('pid_namespace', 999),
            lambda s: s['leases'][lease.token]['resources'].__setitem__('ram_bytes', float('nan')),
        ]
        for change in mutations:
            self.path.write_text(original)
            self.write_state(change)
            corrupt = self.path.read_bytes()
            with self.subTest(change=change), self.assertRaises(rb.BudgetStateError):
                self.budget.acquire(rb.Resources(cpu_millis=1))
            self.assertEqual(self.path.read_bytes(), corrupt)
        for corrupt in ('{', '[]', '{"schema":1,"schema":1}', 'null'):
            self.path.write_text(corrupt)
            with self.subTest(raw=corrupt), self.assertRaises(rb.BudgetStateError):
                self.budget.snapshot()
            self.assertEqual(self.path.read_text(), corrupt)
        self.path.write_text(original)
        lease.release()

    def test_missing_state_requires_explicit_recovery(self):
        self.path.unlink()
        with self.assertRaises(rb.BudgetStateError):
            self.budget.acquire(rb.Resources(cpu_millis=1))
        with self.assertRaises(rb.BudgetStateError):
            rb.ResourceBudget.create(self.path, self.limits)
        self.assertFalse(self.path.exists())

    def test_missing_or_replaced_lock_never_creates_second_pool(self):
        lease = self.budget.acquire(self.limits)
        sidecar = self.path.with_suffix('.json.lock')
        old = self.path.parent / 'old-lock'
        sidecar.rename(old)
        for operation in (lambda: self.budget.acquire(rb.Resources(cpu_millis=1)),
                          lambda: rb.ResourceBudget.create(self.path, self.limits)):
            with self.assertRaises(rb.BudgetStateError):
                operation()
            self.assertFalse(sidecar.exists())
        sidecar.write_text('replacement')
        with self.assertRaises(rb.BudgetStateError):
            self.budget.snapshot()
        with self.assertRaises(rb.BudgetStateError):
            rb.ResourceBudget.create(self.path, self.limits)
        sidecar.unlink()
        old.rename(sidecar)
        lease.release()
        self.assert_empty()

    @unittest.skipUnless(hasattr(os, 'fork'), 'fork-specific rejection')
    def test_inherited_fork_is_explicitly_unavailable_instead_of_hanging(self):
        code = """
import os, sys
from pathlib import Path
from vtx import fstxn, resource_budget as rb
path = Path(sys.argv[1])
budget = rb.ResourceBudget(path)
lock = fstxn._thread_lock_for(path)
lock.acquire()
pid = os.fork()
if pid == 0:
    try:
        budget.acquire(rb.Resources(cpu_millis=1), timeout=None)
    except rb.ResourceUnavailable:
        os._exit(42)
    os._exit(1)
lock.release()
_, status = os.waitpid(pid, 0)
assert os.waitstatus_to_exitcode(status) == 42, status
"""
        result = subprocess.run(['env', 'python3', '-c', code, str(self.path)], cwd=REPO,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_empty()

    def test_failed_atomic_write_preserves_state_and_releases_lock(self):
        before = self.path.read_bytes()
        with mock.patch.object(fstxn, 'atomic_write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.budget.acquire(self.limits)
        self.assertEqual(before, self.path.read_bytes())
        with self.budget.acquire(self.limits):
            pass
        self.assert_empty()

    def test_ledger_and_lock_symlinks_do_not_modify_victim(self):
        victim = self.path.parent / 'victim'
        victim.write_text('protected')
        for path in (self.path, self.path.with_suffix('.json.lock')):
            original = path.read_bytes()
            path.unlink()
            path.symlink_to(victim)
            try:
                with self.assertRaises(Exception):
                    self.budget.acquire(rb.Resources(cpu_millis=1))
                self.assertEqual(victim.read_text(), 'protected')
            finally:
                path.unlink()
                path.write_bytes(original)

    def test_platform_degradation_is_explicit_unavailable(self):
        with mock.patch.object(fstxn, 'fcntl', None), self.assertRaises(rb.ResourceUnavailable):
            rb.ResourceBudget(self.path)
        with mock.patch.object(fstxn, 'write_capability', return_value='legacy'), self.assertRaises(rb.ResourceUnavailable):
            rb.ResourceBudget(self.path)


class InventoryTests(unittest.TestCase):
    def test_current_host_measurement_has_explicit_no_gpu(self):
        with tempfile.TemporaryDirectory() as root:
            inventory = rb.detect_inventory(root)
            self.assertGreater(inventory.limits.cpu_millis, 0)
            self.assertGreater(inventory.limits.ram_bytes, 0)
            self.assertGreater(inventory.limits.disk_bytes, 0)
            self.assertEqual(inventory.limits.vram_bytes, 0)
            self.assertIn('unavailable', inventory.sources['vram_bytes'])
            self.assertEqual(list(Path(root).iterdir()), [])
            self.assertEqual(rb.detect_inventory(root, vram_bytes=123).limits.vram_bytes, 123)

    def test_missing_disk_unavailable(self):
        with tempfile.TemporaryDirectory() as root:
            inventory = rb.detect_inventory(Path(root) / 'absent')
            self.assertEqual(inventory.limits.disk_bytes, 0)
            self.assertIn('unavailable', inventory.sources['disk_bytes'])

    def test_cgroup_subtree_mapping_keeps_visible_root_limits(self):
        with tempfile.TemporaryDirectory() as root:
            group = Path(root) / 'cgroup'
            group.mkdir()
            (group / 'cgroup.controllers').write_text('cpu memory')
            (group / 'cpu.max').write_text('50000 100000')
            (group / 'memory.max').write_text('1000')
            (group / 'memory.current').write_text('900')
            membership = Path(root) / 'membership'
            membership.write_text('0::/docker/container-id\n')
            directories = rb._cgroup_dirs(group, membership)
            self.assertEqual(directories, [group])
            with mock.patch.object(rb, '_cgroup_dirs', return_value=directories):
                inventory = rb.detect_inventory(root)
            self.assertEqual(inventory.limits.cpu_millis, 500)
            self.assertEqual(inventory.limits.ram_bytes, 100)

    def test_cgroup_headroom_caps_measured_inventory(self):
        with tempfile.TemporaryDirectory() as root:
            group = Path(root)
            (group / 'cpu.max').write_text('50000 100000')
            (group / 'memory.max').write_text('1000')
            (group / 'memory.current').write_text('900')
            with mock.patch.object(rb, '_cgroup_dirs', return_value=[group]):
                inventory = rb.detect_inventory(root)
            self.assertEqual(inventory.limits.cpu_millis, 500)
            self.assertEqual(inventory.limits.ram_bytes, 100)


if __name__ == '__main__':
    unittest.main()
