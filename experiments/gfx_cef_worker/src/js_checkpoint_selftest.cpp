#include "js_checkpoint.hpp"
#include <cstdio>
using namespace vtx_cef;
int main() {
  int failures = 0, checks = 0;
  const auto check = [&](bool ok, const char* name) {
    ++checks;
    std::printf("%s %s\n", ok ? "PASS" : "FAIL", name);
    if (!ok) ++failures;
  };
  JsErrorGate g;
  g.expected_url = "file:///page?f=7";
  g.sent = 9;
  const uint64_t deadline = 1000000;
  check(JsCheckpointRemainingMs(deadline - 1000, deadline) == 1, "one ms remains");
  check(JsCheckpointRemainingMs(deadline - 999, deadline) == 0, "fractional ms cannot extend budget");
  check(JsCheckpointRemainingMs(deadline, deadline) == 0, "exact deadline");
  check(JsCheckpointRemainingMs(deadline + 1, deadline) == 0, "expired no underflow");
  check(JsCheckpointResult(g, deadline - 1, deadline) == JsCheckpointOutcome::kPending, "no reply pending");
  check(JsCheckpointResult(g, deadline, deadline) == JsCheckpointOutcome::kTimeout, "missing reply timeout not JS_ERR");
  check(!g.failed, "timeout does not poison JS state");
  g.installed = true; g.token = "context:7"; g.frame_id = "main"; g.done = 8;
  check(JsCheckpointResult(g, deadline - 1, deadline) == JsCheckpointOutcome::kPending, "stale sequence cannot pass");
  g.done = g.sent;
  check(JsCheckpointResult(g, deadline - 1, deadline) == JsCheckpointOutcome::kPassed, "matching fence before deadline");
  for (const auto pid : {int64_t{0}, int64_t{17}, int64_t{2147483647}, int64_t{-1}}) {
    g.renderer_pid = pid;
    check(JsCheckpointResult(g, deadline - 1, deadline) == JsCheckpointOutcome::kPassed,
          "renderer PID is diagnostic, not proof");
  }
  check(JsCheckpointResult(g, deadline, deadline) == JsCheckpointOutcome::kTimeout, "matching fence exactly at deadline fails closed");
  check(JsCheckpointResult(g, deadline + 1, deadline) == JsCheckpointOutcome::kTimeout, "late reply cannot authorize pixels");
  g.token.clear();
  check(JsCheckpointResult(g, deadline - 1, deadline) != JsCheckpointOutcome::kPassed, "empty context cannot pass");
  g.token = "context:7"; g.frame_id.clear();
  check(JsCheckpointResult(g, deadline - 1, deadline) != JsCheckpointOutcome::kPassed, "empty frame cannot pass");
  g.frame_id = "main"; g.installed = false;
  check(JsCheckpointResult(g, deadline - 1, deadline) != JsCheckpointOutcome::kPassed, "uninstalled observer cannot pass");
  g.failed = true;
  g.renderer_pid = 17;
  for (const auto now : {deadline - 1, deadline, deadline + 1})
    check(JsCheckpointResult(g, now, deadline) == JsCheckpointOutcome::kObserverError, "sticky authenticated error wins deadline race");
  g.failed = false; g.installed = true; g.expected_url.clear();
  check(JsCheckpointResult(g, deadline - 1, deadline) == JsCheckpointOutcome::kTimeout, "missing expected URL fails closed");
  g = JsErrorGate{};
  check(g.renderer_pid == 0, "new document resets renderer PID to unknown");
  std::printf("%d checks, %d failures\n", checks, failures);
  return failures ? 1 : 0;
}
