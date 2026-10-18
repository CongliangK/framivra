"""Real Darwin kernel probes, separate from the Linux zero-skip PR selection.

Default unittest discovery retains these tests on every platform; execution on
non-Darwin is an explicit platform skip, never a claimed kernel probe pass.
"""
import os
import sys
import unittest
from unittest import mock

from vtx import proc_supervisor
from vtx.proc_supervisor import boot_id, group_live_members, proc_starttime


@unittest.skipUnless(sys.platform == 'darwin', 'Darwin kernel identity')
class DarwinProcessIdentityTests(unittest.TestCase):
    def test_native_identity_and_lineage_rejects_without_signal(self):
        self.assertTrue(proc_supervisor.supervision_available())
        self.assertGreater(proc_starttime(os.getpid()), 0)
        self.assertIn(os.getpid(), group_live_members(os.getpgid(0)))
        self.assertRegex(boot_id(), r'^[0-9A-Fa-f-]{36}$')
        for group in (0, 1, os.getpgid(0), os.getpgid(os.getppid())):
            with self.subTest(group=group), mock.patch.object(os, 'killpg') as send:
                with self.assertRaises(proc_supervisor.LineageKillDenied):
                    proc_supervisor.kill_group(group)
                send.assert_not_called()

    def test_unknown_native_info_is_not_a_synthetic_start_time(self):
        with mock.patch.object(proc_supervisor, '_darwin_info', return_value=None):
            self.assertIsNone(proc_starttime(os.getpid()))
            self.assertFalse(proc_supervisor.supervision_available())


if __name__ == '__main__':
    unittest.main()
