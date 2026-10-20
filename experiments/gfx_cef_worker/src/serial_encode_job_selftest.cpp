// Real threads and the same ownership/completion helper included by serve.cpp.
// No CEF headers, IPC, filesystem operations, or encoder performance claims.
#include "serial_encode_job.h"

#include <chrono>
#include <cstdio>
#include <future>
#include <stdexcept>
#include <string>

namespace {
using namespace std::chrono_literals;

void Require(bool condition, const char* message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

struct Gate {
  std::mutex mu;
  std::condition_variable cv;
  bool entered = false;
  bool released = false;
  std::atomic<int> destroyed{0};
  std::atomic<int> touched{0};
  std::atomic<int> active{0};
  std::atomic<int> maximum_active{0};

  void EnterAndWait() {
    std::unique_lock<std::mutex> lock(mu);
    entered = true;
    cv.notify_all();
    cv.wait(lock, [this] { return released; });
  }
  void WaitEntered() {
    std::unique_lock<std::mutex> lock(mu);
    Require(cv.wait_for(lock, 5s, [this] { return entered; }),
            "worker did not reach barrier");
  }
  void Release() {
    std::lock_guard<std::mutex> lock(mu);
    released = true;
    cv.notify_all();
  }
};

// Declared after the job so failures unblock its destructor before unwinding.
struct ReleaseOnExit {
  std::shared_ptr<Gate> gate;
  ~ReleaseOnExit() { gate->Release(); }
};

struct Payload {
  explicit Payload(std::shared_ptr<Gate> owner) : gate(std::move(owner)) {}
  ~Payload() { ++gate->destroyed; }
  std::shared_ptr<Gate> gate;
  std::vector<unsigned char> pixels{1, 2, 3, 4};
  std::string path = "owned-frame.png";
  bool block = false;
  bool fail = false;
  bool throw_error = false;
};

struct Result {
  bool delivered = false;
  unsigned checksum = 0;
};

using Job = vtx_cef::SerialEncodeJob<Payload, Result>;

Result Encode(Payload& payload) {
  auto& gate = *payload.gate;
  const int active = ++gate.active;
  struct Leave {
    std::atomic<int>& active;
    ~Leave() { --active; }
  } leave{gate.active};
  int maximum = gate.maximum_active.load();
  while (maximum < active &&
         !gate.maximum_active.compare_exchange_weak(maximum, active)) {}
  if (payload.block) {
    gate.EnterAndWait();
  }
  // Reads and writes happen after the caller has relinquished the payload,
  // including after a deadline or while its owning job is being destroyed.
  Require(payload.path == "owned-frame.png", "owned path corrupted");
  unsigned sum = 0;
  for (auto& value : payload.pixels) {
    sum += value;
    ++value;
  }
  ++gate.touched;
  if (payload.throw_error) {
    throw std::runtime_error("expected encode failure");
  }
  return {!payload.fail, sum};
}

std::unique_ptr<Payload> NewPayload(const std::shared_ptr<Gate>& gate,
                                    bool block = false) {
  auto payload = std::make_unique<Payload>(gate);
  payload->block = block;
  return payload;
}

bool Wait(Job& job, std::chrono::milliseconds budget = 5s) {
  const auto deadline = std::chrono::steady_clock::now() + budget;
  while (!job.Done()) {
    if (std::chrono::steady_clock::now() >= deadline) {
      return false;
    }
    std::this_thread::yield();
  }
  return true;
}

void CompletionVisibility() {
  auto gate = std::make_shared<Gate>();
  Job job(2);
  ReleaseOnExit release{gate};
  auto payload = NewPayload(gate, true);
  const auto* original = payload->pixels.data();
  Require(job.Start(std::move(payload), Encode), "submit rejected");
  gate->WaitEntered();
  Require(!job.Done(), "blocked job reported done");
  Require(!job.TakeCompleted(), "unfinished payload exposed");
  gate->Release();
  Require(Wait(job), "completion wait failed");
  Require(!job.TakeCompleted(), "result exposed before accepted wait");
  Require(job.FinishWait(true), "completion rejected");
  auto done = job.TakeCompleted();
  Require(done && done->result && !done->error, "result unavailable");
  Require(done->result->delivered && done->result->checksum == 10,
          "result was not visible after acquire");
  Require(done->payload->pixels.data() == original &&
              done->payload->pixels == std::vector<unsigned char>({2, 3, 4, 5}),
          "ownership transfer copied or lost payload");
  Require(!job.TakeCompleted(), "completion consumed twice");
}

void DeadlineLatchesFailure() {
  auto gate = std::make_shared<Gate>();
  Job job(2);
  ReleaseOnExit release{gate};
  Require(job.Start(NewPayload(gate, true), Encode), "submit rejected");
  gate->WaitEntered();
  const bool completed = Wait(job, 0ms);  // Deterministic: worker is held.
  Require(!completed && !job.FinishWait(completed), "deadline accepted");
  Require(!job.TakeCompleted(), "timeout exposed incomplete result");
  Require(gate->destroyed == 0, "timeout released live payload");
  Require(!job.Start(NewPayload(gate), Encode), "next request accepted");
  gate->Release();
  Require(Wait(job), "late completion did not finish");
  Require(!job.FinishWait(true) && !job.TakeCompleted(),
          "late completion reversed timeout");
  Require(gate->touched == 1, "rejected request ran");
  job.StopAndDrain();
  Require(!job.Start(NewPayload(gate), Encode), "closed job restarted");
}

void CompletedAfterDeadlineIsRejected() {
  auto gate = std::make_shared<Gate>();
  Job job(1);
  ReleaseOnExit release{gate};
  Require(job.Start(NewPayload(gate, true), Encode), "submit rejected");
  gate->WaitEntered();
  const bool completed = Wait(job, 0ms);
  Require(!completed, "gated work completed");
  gate->Release();
  Require(Wait(job), "late completion did not finish");
  Require(!job.FinishWait(completed), "new poll overrode actual deadline");
  Require(!job.TakeCompleted(), "late result exposed");
}

void ExceptionIsFailure() {
  auto gate = std::make_shared<Gate>();
  Job job(2);
  auto payload = NewPayload(gate);
  payload->throw_error = true;
  Require(job.Start(std::move(payload), Encode), "submit rejected");
  Require(job.FinishWait(Wait(job)), "exception did not complete");
  auto done = job.TakeCompleted();
  Require(done && done->error && !done->result,
          "exception became a successful/default result");
  bool expected_error = false;
  try {
    std::rethrow_exception(done->error);
  } catch (const std::runtime_error& error) {
    expected_error = std::string(error.what()) == "expected encode failure";
  }
  Require(expected_error, "original error was lost");
  payload = NewPayload(gate);
  payload->fail = true;
  Require(job.Start(std::move(payload), Encode), "completed job not reclaimable");
  Require(job.FinishWait(Wait(job)), "failure did not complete");
  done = job.TakeCompleted();
  Require(done && done->result && !done->error && !done->result->delivered,
          "ordinary encoder failure became success");
}

void SerialRequestsDoNotOverlap() {
  auto gate = std::make_shared<Gate>();
  Job job(2);
  ReleaseOnExit release{gate};
  Require(job.Start(NewPayload(gate, true), Encode), "submit rejected");
  gate->WaitEntered();
  Require(!job.Start(NewPayload(gate), Encode), "overlapping request accepted");
  gate->Release();
  Require(job.FinishWait(Wait(job)), "first completion failed");
  Require(!job.Start(NewPayload(gate), Encode), "unreclaimed payload overwritten");
  auto first = job.TakeCompleted();
  Require(first && first->result && first->result->checksum == 10,
          "first result lost");
  Require(job.Start(std::move(first->payload), Encode), "second submit rejected");
  Require(job.FinishWait(Wait(job)), "second completion failed");
  auto second = job.TakeCompleted();
  Require(second && second->result && second->result->checksum == 14,
          "completed payload was not reusable");
  Require(gate->maximum_active == 1 && gate->active == 0 && gate->touched == 2,
          "serial jobs overlapped or rejected job ran");
}

void DestructorDrainsOwnedPayload() {
  auto gate = std::make_shared<Gate>();
  std::promise<void> leaving_scope;
  auto leaving = leaving_scope.get_future();
  auto destroyed = std::async(std::launch::async, [gate, &leaving_scope] {
    Job job(2);
    Require(job.Start(NewPayload(gate, true), Encode), "submit rejected");
    gate->WaitEntered();
    Require(!job.FinishWait(false), "deadline accepted");
    leaving_scope.set_value();
    // Destructor must join before destroying its own payload and result.
  });
  ReleaseOnExit release{gate};
  Require(leaving.wait_for(5s) == std::future_status::ready,
          "destructor test never started");
  Require(destroyed.wait_for(20ms) == std::future_status::timeout,
          "destructor detached unfinished worker");
  Require(gate->destroyed == 0 && gate->touched == 0,
          "blocked payload destroyed before worker access");
  gate->Release();
  Require(destroyed.wait_for(5s) == std::future_status::ready,
          "destructor failed to join completed worker");
  destroyed.get();
  Require(gate->destroyed == 1 && gate->touched == 1 && gate->active == 0,
          "destruction lost work or released payload early");
}

void IdleShutdown() {
  for (const int threads : {0, -1, 1, 2}) {
    Job job(threads);  // Preserve the old pool's minimum-one behavior.
    Require(job.Done() && !job.TakeCompleted(), "idle state invalid");
    job.StopAndDrain();
    job.StopAndDrain();
    auto gate = std::make_shared<Gate>();
    Require(!job.Start(NewPayload(gate), Encode), "stopped pool accepted work");
  }
}
}  // namespace

int main() {
  const std::pair<const char*, void (*)()> cases[] = {
      {"completion_visibility", CompletionVisibility},
      {"deadline_latches_failure", DeadlineLatchesFailure},
      {"completed_after_deadline_is_rejected", CompletedAfterDeadlineIsRejected},
      {"exception_is_failure", ExceptionIsFailure},
      {"serial_requests_do_not_overlap", SerialRequestsDoNotOverlap},
      {"destructor_drains_owned_payload", DestructorDrainsOwnedPayload},
      {"idle_shutdown", IdleShutdown},
  };
  for (const auto& [name, test] : cases) {
    try {
      test();
      std::printf("PASS %s\n", name);
    } catch (const std::exception& error) {
      std::fprintf(stderr, "FAIL %s: %s\n", name, error.what());
      return 1;
    }
  }
  std::puts("SERIAL_ENCODE_JOB ALL PASS (7 cases; helper only, CEF UNRUN)");
  return 0;
}
