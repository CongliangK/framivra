// CEF-free, single-outstanding-job ownership and completion boundary.
#ifndef GFX_CEF_WORKER_SERIAL_ENCODE_JOB_H_
#define GFX_CEF_WORKER_SERIAL_ENCODE_JOB_H_

#include <atomic>
#include <condition_variable>
#include <exception>
#include <memory>
#include <mutex>
#include <optional>
#include <thread>
#include <utility>
#include <vector>

namespace vtx_cef {

// Public methods belong to one controlling thread. The worker receives only the
// owned payload, never the caller's stack. Keep the requested pool size, but
// accept at most one job until its completed payload has been reclaimed.
template <typename Payload, typename Result>
class SerialEncodeJob {
 public:
  using Work = Result (*)(Payload&);
  struct Completion {
    std::unique_ptr<Payload> payload;
    std::optional<Result> result;
    std::exception_ptr error;
  };

  explicit SerialEncodeJob(int threads) {
    if (threads < 1) {
      threads = 1;
    }
    try {
      for (int i = 0; i < threads; ++i) {
        workers_.emplace_back([this] { Loop(); });
      }
    } catch (...) {
      StopAndDrain();
      throw;
    }
  }
  ~SerialEncodeJob() { StopAndDrain(); }
  SerialEncodeJob(const SerialEncodeJob&) = delete;
  SerialEncodeJob& operator=(const SerialEncodeJob&) = delete;

  bool Start(std::unique_ptr<Payload> payload, Work work) {
    std::lock_guard<std::mutex> lock(mu_);
    if (stop_ || terminal_ || busy_ || !payload || work == nullptr) {
      return false;
    }
    payload_ = std::move(payload);
    result_.reset();
    error_ = nullptr;
    work_ = work;
    busy_ = true;
    accepted_ = false;
    pending_ = true;
    done_.store(false, std::memory_order_release);
    work_cv_.notify_one();
    return true;
  }

  bool Done() const { return done_.load(std::memory_order_acquire); }

  // Pass the actual wait outcome, not a new poll after a deadline. A timeout is
  // irreversible even if the worker finishes between the wait and this call.
  // It denies result access and all later submissions on this connection.
  bool FinishWait(bool completed) {
    std::lock_guard<std::mutex> lock(mu_);
    if (stop_ || terminal_ || !busy_) {
      return false;
    }
    if (!completed || !Done()) {
      terminal_ = true;
      accepted_ = false;
      return false;
    }
    accepted_ = true;
    return true;
  }

  std::optional<Completion> TakeCompleted() {
    std::lock_guard<std::mutex> lock(mu_);
    if (stop_ || terminal_ || !accepted_ || !Done()) {
      return std::nullopt;
    }
    Completion completed{std::move(payload_), std::move(result_), error_};
    busy_ = false;
    accepted_ = false;
    return completed;
  }

  // Never detach, cancel a library call, or release its payload early. This can
  // block indefinitely if a codec/filesystem call never returns. The existing
  // host process supervisor remains responsible for terminating that process.
  // Call before cleaning scratch files or other resources used by the job.
  void StopAndDrain() {
    {
      std::lock_guard<std::mutex> lock(mu_);
      stop_ = true;
      terminal_ = true;
      work_cv_.notify_all();
    }
    for (auto& worker : workers_) {
      if (worker.joinable()) {
        worker.join();
      }
    }
  }

 private:
  void Loop() {
    for (;;) {
      std::unique_lock<std::mutex> lock(mu_);
      work_cv_.wait(lock, [this] { return stop_ || pending_; });
      if (!pending_) {
        return;
      }
      auto* payload = payload_.get();
      const Work work = work_;
      pending_ = false;
      lock.unlock();
      try {
        result_.emplace(work(*payload));
      } catch (...) {
        error_ = std::current_exception();
      }
      // Neither payload nor result is touched by this worker after release.
      done_.store(true, std::memory_order_release);
    }
  }

  std::vector<std::thread> workers_;
  std::mutex mu_;
  std::condition_variable work_cv_;
  std::unique_ptr<Payload> payload_;
  std::optional<Result> result_;
  std::exception_ptr error_;
  Work work_ = nullptr;
  std::atomic<bool> done_{true};
  bool pending_ = false;
  bool busy_ = false;
  bool accepted_ = false;
  bool terminal_ = false;
  bool stop_ = false;
};

}  // namespace vtx_cef

#endif  // GFX_CEF_WORKER_SERIAL_ENCODE_JOB_H_
