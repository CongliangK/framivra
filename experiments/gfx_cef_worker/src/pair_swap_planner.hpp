// Pure bookkeeping for optional signed/unsigned browser-pair navigation rings.
// At BIND of N, navigate an already-created retired pair to N+depth-1.
// Never create browsers or inject readiness/paint bridges speculatively. Pair
// rotation follows accepted request ordinals, not frame numbers. CEF lifetime,
// navigation and the unchanged single active verifier remain owned by serve.cpp.
#ifndef VTX_CEF_PAIR_SWAP_PLANNER_HPP_
#define VTX_CEF_PAIR_SWAP_PLANNER_HPP_

#include <cstdint>
#include <string>

namespace vtx_cef {
namespace pairswap {
enum class Outcome { kOff, kHit, kMiss };
inline const char* OutcomeName(Outcome o) {
  switch (o) {
    case Outcome::kHit: return "hit";
    case Outcome::kMiss: return "miss";
    default: return "off";
  }
}
struct PairBookkeeping {
  bool prefetch_active = false;
  int32_t prefetched_frame = -1;
  std::string prefetched_url;
  uint64_t load_floor = 0;
  bool Matches(int32_t frame, const std::string& url) const {
    return prefetch_active && prefetched_frame == frame && prefetched_url == url;
  }
  void Consume() { prefetch_active = false; }
  void Record(int32_t frame, const std::string& url, uint64_t floor) {
    prefetch_active = true;
    prefetched_frame = frame;
    prefetched_url = url;
    load_floor = floor;
  }
};
enum class SignedNav { kNavigateNow, kAdoptPrefetch, kNavigateByCreation };
struct Binding {
  int pair = 0;
  SignedNav signed_nav = SignedNav::kNavigateNow;
  Outcome signed_prefetch = Outcome::kOff;
  uint64_t signed_load_floor = 0;
  bool speculation_invalidated = false;
};
class Planner {
 public:
  explicit Planner(bool enabled = false, int depth = 2)
      : enabled_(enabled), depth_(depth == 3 ? 3 : 2) {}
  bool enabled() const { return enabled_; }
  int active_pair() const { return enabled_ ? active_pair_ : 0; }
  int effective_depth() const { return enabled_ ? depth_ : 1; }
  void InvalidateAll() { for (auto& pair : pairs_) pair.Consume(); }
  void Disable() { InvalidateAll(); enabled_ = false; active_pair_ = 0; }
  Binding BindNextRequest(int32_t frame, const std::string& url) {
    Binding b;
    b.pair = active_pair();
    // A discontinuity invalidates the whole horizon, including another pair's
    // N+1 token; replay must not revive a previous speculative document.
    if (have_last_frame_ && (last_frame_ == INT32_MAX || frame != last_frame_ + 1)) {
      InvalidateAll();
      b.speculation_invalidated = true;
    }
    have_last_frame_ = true;
    last_frame_ = frame;
    auto& rec = pairs_[b.pair];
    const bool hit = enabled_ && rec.Matches(frame, url);
    b.signed_prefetch = enabled_ ? (hit ? Outcome::kHit : Outcome::kMiss) : Outcome::kOff;
    if (hit) {
      b.signed_nav = SignedNav::kAdoptPrefetch;
      b.signed_load_floor = rec.load_floor;
    }
    rec.Consume();
    return b;
  }
  void RecordSignedPrefetch(int pair, int32_t frame, const std::string& url,
                            uint64_t load_floor) {
    if (enabled_ && pair >= 0 && pair < depth_)
      pairs_[pair].Record(frame, url, load_floor);
  }
  int PrefetchTargetAtBind(int32_t frame) const {
    if (!enabled_ || PrefetchFrame(frame) < 0) return -1;
    return (active_pair_ + depth_ - 1) % depth_;
  }
  int32_t PrefetchFrame(int32_t frame) const {
    const int delta = depth_ - 1;
    return frame < 0 || frame > INT32_MAX - delta ? -1 : frame + delta;
  }
  void NotifyRequestFinished(bool accepted) {
    if (enabled_ && accepted) active_pair_ = (active_pair_ + 1) % depth_;
  }
 private:
  bool enabled_ = false;
  int depth_ = 2;
  int active_pair_ = 0;
  bool have_last_frame_ = false;
  int32_t last_frame_ = 0;
  PairBookkeeping pairs_[3]{};
};
}  // namespace pairswap
}  // namespace vtx_cef
#endif  // VTX_CEF_PAIR_SWAP_PLANNER_HPP_
