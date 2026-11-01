// pair_swap_planner.hpp — PairSwap 双对交替预取的纯决策逻辑（无 CEF 依赖）。
//
// 机制（报告 /tmp/cef-veto-research/REPORT.md §3b）：worker 持两对浏览器
// （每对 = 1 个 signed + 1 个 unsigned 基线）。帧 N 在活跃对 A 上 veto
// 通过（S5 accepted）的那一刻，立即对备用对 B 的两个浏览器 LoadURL
// 帧 N+1 的 URL（纯文档导航，不注入桥）。帧 N+1 请求到达时若投机命中
// （帧号 + URL 均匹配），跳过导航直接进入加载等待门——导航成本已藏进
// 上帧周期；未命中（乱序/跳帧/重放等非严格 N+1 请求）则就地作废陈旧
// 预取，回退 navigate-now 现路径。任一时刻仍只有 1 个导航 + 1 个验证
// 在推进（预取只发生在帧 N 已验收之后）。
//
// 本头文件只承载"可与浏览器编排分离"的簿记与判定（供 serve.cpp 与
// frame_sync_selftest.cpp 共用一处真相）；LoadURL 的实际发出、浏览器
// 生命周期由 serve.cpp 负责，本类型不做任何 I/O。
#ifndef VTX_CEF_PAIR_SWAP_PLANNER_HPP_
#define VTX_CEF_PAIR_SWAP_PLANNER_HPP_

#include <cstdint>
#include <string>

namespace vtx_cef {
namespace pairswap {

// 预取结局（进 adapter_timing 的 per-frame 证据字段）。
enum class Outcome { kOff, kHit, kMiss };

inline const char* OutcomeName(Outcome o) {
  switch (o) {
    case Outcome::kHit: return "hit";
    case Outcome::kMiss: return "miss";
    default: return "off";
  }
}

// 单侧（signed 或 unsigned）浏览器的预取簿记。
struct PairBookkeeping {
  bool prefetch_active = false;   // 已对备用浏览器发出下一帧 LoadURL
  int32_t prefetched_frame = -1;  // 预取目标帧号
  std::string prefetched_url;     // 预取目标 URL（模板展开后）
  uint64_t load_floor = 0;        // 预取导航发出前的 load_starts 基线

  // 命中判定：帧号与 URL 都匹配的预取才可被采纳（非严格 N+1 一律 miss）。
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

// 帧请求开始时的绑定决策（serve.cpp 据此路由 state/browser/捕获器）。
enum class SignedNav {
  kNavigateNow,         // 现路径：本请求自行 LoadURL（预取未启用/未命中）
  kAdoptPrefetch,       // 命中：沿用预取导航，load_floor 取预取发出前基线
  kNavigateByCreation,  // 首航：以目标 URL 创建浏览器（创建即导航）
};

struct Binding {
  int pair = 0;                    // 本请求服务的对（0=A，1=B）
  SignedNav signed_nav = SignedNav::kNavigateNow;
  Outcome signed_prefetch = Outcome::kOff;
  uint64_t signed_load_floor = 0;  // kAdoptPrefetch/kNavigateByCreation 的基线
};

// 双对交替的帧级编排决策。所有方法纯函数式（无时钟/无 I/O）。
class Planner {
 public:
  explicit Planner(bool enabled = false) : enabled_((enabled)) {}
  bool enabled() const { return enabled_; }
  int active_pair() const { return active_pair_; }

  // 请求 (frame, url) 到达：选出服务对并判定预取命中。预取一次性消费：
  // 命中即采纳；未命中（含乱序/跳帧/重放/从未预取）就地作废，回退
  // navigate-now。开关关闭时恒用 A 对且结局记 off。
  Binding BindNextRequest(int32_t frame, const std::string& url) {
    Binding b;
    b.pair = enabled_ ? active_pair_ : 0;
    PairBookkeeping& rec = pairs_[b.pair];
    const bool hit = enabled_ && rec.Matches(frame, url);
    b.signed_prefetch =
        enabled_ ? (hit ? Outcome::kHit : Outcome::kMiss) : Outcome::kOff;
    if (hit) {
      b.signed_nav = SignedNav::kAdoptPrefetch;
      b.signed_load_floor = rec.load_floor;
    }
    rec.Consume();
    return b;
  }

  // 预取发出登记（serve.cpp 对备用对 signed 浏览器 LoadURL 之后调用）。
  void RecordSignedPrefetch(int pair, int32_t frame, const std::string& url,
                            uint64_t load_floor) {
    if (pair < 0 || pair > 1) return;
    pairs_[pair].Record(frame, url, load_floor);
  }

  // 帧 N veto 通过后的预取目标对；返回 -1 = 不预取（开关关闭/帧号非法）。
  int PrefetchTargetAfterAccept(int32_t accepted_frame) const {
    if (!enabled_ || accepted_frame < 0 ||
        accepted_frame == INT32_MAX)  // frame+1 不得回绕
      return -1;
    return active_pair_ ^ 1;
  }

  // 预取目标帧：严格 N+1（投机帧序；猜错由 BindNextRequest 判 miss 兜底）。
  static int32_t PrefetchFrame(int32_t accepted_frame) {
    return accepted_frame + 1;
  }

  // 请求成功结束后交替活跃对；失败请求不交替（宿主必然中止会话）。
  void NotifyRequestFinished(bool accepted) {
    if (enabled_ && accepted) active_pair_ ^= 1;
  }

 private:
  bool enabled_ = false;
  int active_pair_ = 0;
  PairBookkeeping pairs_[2]{};
};

}  // namespace pairswap
}  // namespace vtx_cef

#endif  // VTX_CEF_PAIR_SWAP_PLANNER_HPP_
