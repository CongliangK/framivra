"""Native cross-process admission lock regression; no models or GPU required."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
import independence


class AdmissionLockPortability(unittest.TestCase):
    def test_real_processes_serialize_and_release_after_exception(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            entered = root / "entered"
            script = ("import sys; from pathlib import Path; "
                      "sys.path.insert(0, sys.argv[1]); import independence; "
                      "independence.project_state_root=lambda:Path(sys.argv[2]); "
                      "print('ready', flush=True); "
                      "lease=independence.admission_lease(); lease.__enter__(); "
                      "Path(sys.argv[3]).write_text('entered'); lease.__exit__()")
            with patch.object(independence, "project_state_root", return_value=root):
                child = None
                try:
                    with self.assertRaisesRegex(RuntimeError, "release"):
                        with independence.admission_lease():
                            child = subprocess.Popen(
                                [sys.executable, "-c", script, str(APP), str(root), str(entered)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                            self.assertEqual(child.stdout.readline().strip(), "ready")
                            time.sleep(0.25)
                            self.assertIsNone(child.poll())
                            self.assertFalse(entered.exists())
                            raise RuntimeError("release")
                    stdout, stderr = child.communicate(timeout=10)
                    self.assertEqual(child.returncode, 0, stderr)
                    self.assertTrue(entered.is_file())
                finally:
                    if child is not None:
                        if child.poll() is None:
                            child.kill()
                        child.communicate()

    def test_unwritable_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "not-a-directory"
            root.write_text("occupied")
            with patch.object(independence, "project_state_root", return_value=root):
                with self.assertRaises(independence.AsrIndependenceError):
                    independence.admission_lease()
