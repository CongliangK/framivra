// CEF-independent external message-pump scheduling and wakeup policy.
#ifndef VTX_CEF_EXTERNAL_PUMP_SCHEDULE_HPP_
#define VTX_CEF_EXTERNAL_PUMP_SCHEDULE_HPP_

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <limits>
#include <mutex>

namespace vtx_cef {

// OnScheduleMessagePumpWork can run on ANY thread. A positive delay replaces
// an earlier scheduled call (CEF's documented cancellation semantics), rather
// than asking the UI thread to poll at a fixed cadence. A zero/negative delay
// wakes a sleeping UI thread immediately. No CEF call is made under this lock.
class ExternalPumpSchedule {
 public:
  static constexpr uint64_t kNoDeadline = std::numeric_limits<uint64_t>::max();

  static uint64_t NowUs() {
    return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count());
  }

  // Timestamp input makes overflow/cancellation/race policy independently
  // testable, without CEF, sleeps, a fake browser, or wall-clock assumptions.
  static uint64_t DeadlineUs(uint64_t now_us, int64_t delay_ms) {
    if (delay_ms <= 0) return std::min(now_us, kNoDeadline - 1);
    const uint64_t room = kNoDeadline - 1 - std::min(now_us, kNoDeadline - 1);
    const uint64_t ms = static_cast<uint64_t>(delay_ms);
    return ms > room / 1000 ? kNoDeadline - 1 : now_us + ms * 1000;
  }

  void Schedule(int64_t delay_ms, uint64_t now_us) {
    {
      std::lock_guard<std::mutex> lock(mu_);
      deadline_us_ = DeadlineUs(now_us, delay_ms);
    }
    cv_.notify_one();
  }

  // Native encoder completion is not a CEF timer. Keep a separate sticky
  // wake so a completion between checking its predicate and sleeping is not
  // lost, and never cancel/postpone the browser's scheduled work.
  void NotifyWork() {
    {
      std::lock_guard<std::mutex> lock(mu_);
      external_work_ = true;
    }
    cv_.notify_one();
  }

  uint64_t deadline_us() const {
    std::lock_guard<std::mutex> lock(mu_);
    return deadline_us_;
  }

  // Consume only due work BEFORE CefDoMessageLoopWork. Callbacks scheduled
  // during the CEF call then remain pending. An early explicit/fallback pump
  // must not accidentally cancel a still-future timer that CEF may not reissue.
  void ConsumeDue(uint64_t now_us) {
    std::lock_guard<std::mutex> lock(mu_);
    if (deadline_us_ <= now_us) deadline_us_ = kNoDeadline;
  }

  // Sleep only until the caller's bounded fallback/deadline or CEF's next
  // scheduled work, whichever is earlier. Scheduling and sleeping share a
  // mutex: a callback between checking and waiting cannot lose its wakeup.
  void WaitUntil(uint64_t limit_us) {
    std::unique_lock<std::mutex> lock(mu_);
    for (;;) {
      if (external_work_) {
        external_work_ = false;
        return;
      }
      const uint64_t now = NowUs();
      const uint64_t next = std::min(limit_us, deadline_us_);
      if (now >= next) return;
      const uint64_t remaining = std::min<uint64_t>(next - now,
          static_cast<uint64_t>(std::numeric_limits<int64_t>::max()));
      cv_.wait_for(lock, std::chrono::microseconds(remaining));
    }
  }

 private:
  mutable std::mutex mu_;
  std::condition_variable cv_;
  uint64_t deadline_us_ = kNoDeadline;
  bool external_work_ = false;
};

}  // namespace vtx_cef
#endif
