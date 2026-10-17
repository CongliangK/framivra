// Browser-side fence state and deadline policy, independent of CEF/wall time.
#ifndef VTX_CEF_JS_CHECKPOINT_HPP_
#define VTX_CEF_JS_CHECKPOINT_HPP_
#include <algorithm>
#include <cstdint>
#include <limits>
#include <string>
namespace vtx_cef {
struct JsErrorGate {
  std::string expected_url, token, frame_id;
  bool installed = false;
  // Sticky observer error (trusted JS exception or invalid observer). A missing
  // reply / exhausted protocol budget must NEVER set this bit.
  bool failed = false;
  uint32_t sent = 0;  // lifetime monotonic, including same-URL navigations
  uint32_t done = 0;
};
enum class JsCheckpointOutcome { kPending, kPassed, kTimeout, kObserverError };
inline int JsCheckpointRemainingMs(uint64_t now, uint64_t deadline) {
  if (now >= deadline) return 0;  // no unsigned underflow / budget extension
  return static_cast<int>(std::min<uint64_t>((deadline - now) / 1000,
      std::numeric_limits<int>::max()));
}
inline JsCheckpointOutcome JsCheckpointResult(const JsErrorGate& g,
                                              uint64_t now, uint64_t deadline) {
  // A previously authenticated error remains an error even at the deadline.
  if (g.failed) return JsCheckpointOutcome::kObserverError;
  if (g.expected_url.empty() || now >= deadline) return JsCheckpointOutcome::kTimeout;
  if (g.installed && !g.token.empty() && !g.frame_id.empty() &&
      g.sent != 0 && g.done == g.sent) return JsCheckpointOutcome::kPassed;
  return JsCheckpointOutcome::kPending;
}
}  // namespace vtx_cef
#endif
