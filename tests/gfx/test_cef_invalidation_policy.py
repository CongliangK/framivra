"""Cheap, CEF-free deterministic checks for active invalidation coalescing."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "experiments" / "gfx_cef_worker" / "src"


class ActiveInvalidationPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("c++")
        if not compiler:
            raise unittest.SkipTest("C++ compiler unavailable")
        cls.temp = tempfile.TemporaryDirectory(prefix="vtx-invalidation-policy-")
        cls.addClassCleanup(cls.temp.cleanup)
        base = Path(cls.temp.name)
        source = base / "policy.cpp"
        source.write_text(r'''
#include "invalidation_policy.hpp"
#include <cassert>
#include <cstdint>
#include <limits>
#include <string>
using vtx_cef::ActiveInvalidationPolicy;
int main(int argc, char** argv) {
  assert(argc == 2);
  const std::string test = argv[1];
  ActiveInvalidationPolicy p;
  p.Reset(120);
  if (test == "initial") {
    assert(p.ShouldInvalidate(0, 0));
    assert(!p.ShouldInvalidate(0, 0));
  } else if (test == "phase") {
    assert(p.ShouldInvalidate(100, 0));  // new request/loading
    assert(p.ShouldInvalidate(100, 1));  // bridge injected/readiness
    assert(p.ShouldInvalidate(100, 2));  // ready/new sample zero
    assert(p.ShouldInvalidate(100, 3));  // next distinct sample demand
    assert(p.ShouldInvalidate(100, 2));  // veto mismatch: resample batch
    assert(p.ShouldInvalidate(100, 4));  // signed S4
    assert(p.ShouldInvalidate(100, 20)); // signed relocation
    assert(!p.ShouldInvalidate(100, 20));
  } else if (test == "coalesce") {
    assert(p.ShouldInvalidate(1000, 2));
    for (uint64_t t = 1000; t < 9000; ++t)
      assert(!p.ShouldInvalidate(t, 2));
  } else if (test == "retry") {
    assert(p.ShouldInvalidate(1000, 2));
    assert(!p.ShouldInvalidate(8999, 2));
    assert(p.ShouldInvalidate(9000, 2));
    assert(!p.ShouldInvalidate(16999, 2));
    assert(p.ShouldInvalidate(17000, 2));
  } else if (test == "stale") {
    // Simulate stale callbacks between retries. Callbacks have NO policy API:
    // only the caller's actual readiness/proof/sample progression changes phase.
    int invalidations = 0;
    int begin_frames = 0;
    for (uint64_t t = 0; t <= 50000; ++t) {
      if (p.ShouldInvalidate(t, 2)) ++invalidations;
      ++begin_frames;  // deliberately unconditional, including suppressed calls
    }
    assert(invalidations == 7);
    assert(begin_frames == 50001);
    assert(p.ShouldInvalidate(56000, 2));
  } else if (test == "low_fps_short_budget") {
    for (int fps : {1, 30, 60, 120}) {
      p.Reset(fps);
      assert(p.interval_us() == 8000);
      int invalidations = 0;
      // Stale callbacks keep the same demand throughout a 20 ms budget.
      // Retrying must not wait the full 1-second capture period at FPS=1.
      for (uint64_t t = 0; t < 20000; ++t)
        if (p.ShouldInvalidate(t, 2)) ++invalidations;
      assert(invalidations == 3);  // initial, 8 ms, 16 ms
      assert(p.ShouldInvalidate(20000, 3));  // next sample is immediate
    }
  } else if (test == "reset") {
    assert(p.ShouldInvalidate(1000, 2));
    assert(!p.ShouldInvalidate(1001, 2));
    p.Reset(120);  // same frame/phase timestamp must not suppress a new request
    assert(p.ShouldInvalidate(1001, 2));
  } else if (test == "finite") {
    for (int fps : {-1, 0, 1, 30, 120, 240, 241,
                    std::numeric_limits<int>::min(),
                    std::numeric_limits<int>::max()}) {
      p.Reset(fps);
      assert(p.interval_us() >= 4167 && p.interval_us() <= 8000);
      const uint64_t start = std::numeric_limits<uint64_t>::max() - 1000000;
      assert(p.ShouldInvalidate(start, 0));
      assert(!p.ShouldInvalidate(start + p.interval_us() - 1, 0));
      assert(p.ShouldInvalidate(start + p.interval_us(), 0));
      // Defensive clock regression retries rather than subtracting unsigned
      // timestamps and potentially suppressing demand indefinitely.
      assert(p.ShouldInvalidate(0, 0));
    }
    p.Reset(120); assert(p.interval_us() == 8000);
    p.Reset(240); assert(p.interval_us() == 4167);
    p.Reset(1); assert(p.interval_us() == 8000);
  } else {
    return 2;
  }
}
''', encoding="utf-8")
        cls.binary = base / "policy"
        subprocess.run(
            [compiler, "-std=c++17", "-O0", "-Wall", "-Wextra", "-Werror",
             "-I", str(SOURCE), str(source), "-o", str(cls.binary)],
            check=True, capture_output=True, text=True,
        )

    def check_case(self, case):
        subprocess.run([str(self.binary), case], check=True,
                       capture_output=True, text=True, timeout=5)

    def test_initial_force(self):
        self.check_case("initial")

    def test_phase_transition_force(self):
        self.check_case("phase")

    def test_coalesce_same_phase(self):
        self.check_case("coalesce")

    def test_bounded_retry(self):
        self.check_case("retry")

    def test_stale_callbacks_do_not_satisfy_demand(self):
        self.check_case("stale")

    def test_new_request_reset(self):
        self.check_case("reset")

    def test_low_fps_retries_within_short_budget(self):
        self.check_case("low_fps_short_budget")

    def test_finite_fps_interval_and_clock_edges(self):
        self.check_case("finite")


if __name__ == "__main__":
    unittest.main()
