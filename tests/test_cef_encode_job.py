"""Explicit CEF-free native ownership/completion test; never launches a worker.

Run only this ID with VTX_TEST_CEF_ENCODE_JOB=1:
  tests.test_cef_encode_job.PureSerialEncodeJobSelfTest.test_completion_and_timeout_contract
VTX_TEST_CEF_ENCODE_JOB_ASAN=1 adds AddressSanitizer. Address-only execution
(ASAN_OPTIONS=detect_leaks=0) does not establish leak or thread sanitizer passes.
VTX_TEST_CEF_ENCODE_JOB_NO_EXCEPTIONS=1 adds -fno-exceptions, as in the CEF
worker build. Both modes check explicit Result failure; only the default mode
can exercise captured exceptions. Assertion failure aborts the no-exception
helper process rather than recovering.
The selftest includes the same serial_encode_job.h used by serve.cpp. Its pass
does not validate serve integration, real CEF capture/IPC, or performance.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CASES = {
    "completion_visibility",
    "deadline_latches_failure",
    "completed_after_deadline_is_rejected",
    "serial_requests_do_not_overlap",
    "destructor_drains_owned_payload",
    "idle_shutdown",
}


@unittest.skipUnless(os.environ.get("VTX_TEST_CEF_ENCODE_JOB") == "1",
                     "requires explicit VTX_TEST_CEF_ENCODE_JOB=1; no CEF")
class PureSerialEncodeJobSelfTest(unittest.TestCase):
    def test_completion_and_timeout_contract(self):
        compiler = shutil.which("g++")
        self.assertIsNotNone(compiler, "explicit native gate requires g++")
        source = ROOT / "experiments/gfx_cef_worker/src"
        sanitize = (["-fsanitize=address", "-fno-omit-frame-pointer"]
                    if os.environ.get("VTX_TEST_CEF_ENCODE_JOB_ASAN") == "1"
                    else [])
        no_exceptions = os.environ.get("VTX_TEST_CEF_ENCODE_JOB_NO_EXCEPTIONS") == "1"
        exception_flags = ["-fno-exceptions"] if no_exceptions else []
        cases = CASES | {"result_is_failure" if no_exceptions
                         else "exception_is_failure"}
        with tempfile.TemporaryDirectory(prefix="vtx_serial_encode_job_") as tmp:
            executable = Path(tmp) / "serial_encode_job_selftest"
            argv = [compiler, "-std=c++20", "-O1", "-g", "-Wall", "-Wextra",
                    "-Werror", "-pthread", *sanitize, *exception_flags,
                    "-I" + str(source),
                    str(source / "serial_encode_job_selftest.cpp"),
                    "-o", str(executable)]
            print("SERIAL_ENCODE_JOB_BUILD " + " ".join(argv), flush=True)
            build = subprocess.run(argv, capture_output=True, text=True,
                                   timeout=180)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run([str(executable)], capture_output=True,
                                 text=True, timeout=60)
            print(run.stdout, end="", flush=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertEqual({line[5:] for line in run.stdout.splitlines()
                              if line.startswith("PASS ")}, cases)
            self.assertIn("SERIAL_ENCODE_JOB EXCEPTIONS " +
                          ("disabled" if no_exceptions else "enabled"),
                          run.stdout)
            self.assertIn("SERIAL_ENCODE_JOB ALL PASS", run.stdout)
