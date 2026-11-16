// CEF-independent throttling of redundant active-producer Invalidate calls.
#ifndef VTX_CEF_INVALIDATION_POLICY_HPP_
#define VTX_CEF_INVALIDATION_POLICY_HPP_

#include <algorithm>
#include <cstdint>

namespace vtx_cef {

// This policy controls ONLY forced invalidations, never BeginFrame, message
// pumping, proof samples or deadlines. A callback (including a stale one) cannot
// satisfy demand: the producer retries for as long as its existing loop asks.
class ActiveInvalidationPolicy {
 public:
  void Reset(int capture_fps) {
    // The caller already validates 1..240. Keep this pure helper finite and
    // bounded even for invalid input, without floating-point clock arithmetic.
    const auto fps = static_cast<uint64_t>(std::clamp(capture_fps, 1, 240));
    // Never delay a retry beyond the existing 8 ms pump fallback: low FPS
    // requests can have short readiness/proof budgets and stale callbacks.
    interval_us_ = std::min<uint64_t>(8000u, (1000000u + fps - 1u) / fps);
    issued_ = false;
  }

  // The caller changes phase whenever a new readiness/probe/sample demand
  // starts. Such a transition forces an immediate invalidation, even if the
  // preceding phase requested one at the same timestamp.
  bool ShouldInvalidate(uint64_t now_us, uint64_t phase) {
    if (issued_ && phase == phase_ && now_us >= last_us_ &&
        now_us - last_us_ < interval_us_) return false;
    issued_ = true;
    phase_ = phase;
    last_us_ = now_us;
    return true;
  }

  uint64_t interval_us() const { return interval_us_; }

 private:
  uint64_t interval_us_ = 8000;  // existing pump fallback caps default 120 Hz
  uint64_t last_us_ = 0;
  uint64_t phase_ = 0;
  bool issued_ = false;
};

}  // namespace vtx_cef
#endif
