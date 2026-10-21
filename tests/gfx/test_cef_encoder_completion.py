"""CEF-free execution of the actual serve encoder task/wait/receipt source.

The fake pump expires the first deadline while a controlled writer is blocked.
It releases the writer only when production code enters the required drain.
No real deadline, browser, CEF SDK or filesystem output is needed.
"""
import pathlib
import shutil
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'experiments/gfx_cef_worker/src/serve.cpp'


class EncoderCompletion(unittest.TestCase):
    def test_owned_completion_and_failure_receipts(self):
        compiler = shutil.which('c++')
        if compiler is None:
            self.skipTest('C++ compiler unavailable')
        source = SOURCE.read_text()
        pool = source[source.index('class MiniPool {'):
                      source.index('// ---------------------------------------------------------------- 帧生产')]
        start = source.index('      uint64_t t_alpha = 0, t_png = 0, t_disk = 0;')
        end = source.index('    } else {\n      // ================= 模式 2', start)
        delivery = source[start:end]
        with tempfile.TemporaryDirectory() as directory:
            cpp = pathlib.Path(directory) / 'completion.cpp'
            exe = pathlib.Path(directory) / 'completion'
            cpp.write_text(PREFIX + pool + MAIN_START + delivery + MAIN_END)
            subprocess.run([compiler, '-std=c++17', '-pthread', '-O0',
                            str(cpp), '-o', str(exe)], check=True, timeout=30,
                           capture_output=True, text=True)
            for case in ('ok', 'encode_false', 'write_false', 'encode_throw',
                         'write_throw', 'timeout', 'timeout_write_false'):
                with self.subTest(case=case):
                    subprocess.run([str(exe), case], check=True, timeout=5,
                                   capture_output=True, text=True)


PREFIX = r'''
#include <atomic>
#include <cassert>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <functional>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
static std::string test_case;
static std::mutex gate_mu;
static std::condition_variable gate_cv;
static bool writer_entered = false, writer_released = false;
static std::atomic<bool> committed{false};
static std::atomic<int> receipts{0}, requests{0};
static bool IsTimeout() { return test_case.rfind("timeout", 0) == 0; }
static uint64_t NowUs() { return 1; }
static void UnpremultiplyToRgba(const uint8_t*, size_t, uint8_t*) {}
static bool EncodePngRgba(const uint8_t*, int, int, int, std::vector<uint8_t>* png) {
  if (test_case == "encode_throw") throw std::runtime_error("encoder");
  png->assign(1, 42);
  return test_case != "encode_false";
}
static bool AtomicWriteBytes(const std::string&, const uint8_t*, size_t) {
  if (IsTimeout()) {
    std::unique_lock<std::mutex> lock(gate_mu);
    writer_entered = true;
    gate_cv.notify_all();
    gate_cv.wait(lock, [] { return writer_released; });
  }
  if (test_case == "write_throw") throw std::runtime_error("writer");
  if (test_case == "write_false" || test_case == "timeout_write_false") return false;
  committed.store(true);
  return true;
}
static constexpr int kStatusOk = 0, kStatusTimeout = 1, kStatusJsErr = 3;
struct ProduceResult { int status = kStatusOk; unsigned width = 1, height = 1; };
struct FrameSlotHeader { int status_code; };
static void FillReceipt(FrameSlotHeader* h, const ProduceResult& r, uint64_t,
                        unsigned, int, uint64_t, uint64_t, uint64_t) {
  h->status_code = r.status;
}
static bool SendAll(int, const void* data, size_t) {
  auto* receipt = static_cast<const FrameSlotHeader*>(data);
  if (IsTimeout()) {
    assert(writer_released);  // No receipt while worker still owns captures.
    assert(requests.load() == 1);
    assert(receipt->status_code == kStatusTimeout);
    assert(committed.load() == (test_case != "timeout_write_false"));
  } else {
    assert(receipt->status_code == (test_case == "ok" ? kStatusOk : kStatusJsErr));
    assert(committed.load() == (test_case == "ok"));
  }
  ++receipts;
  return true;
}
struct FakePump {
  unsigned calls = 0;
  bool Until(const std::function<bool()>& done, int timeout_ms) {
    assert(timeout_ms == 60000);
    ++calls;
    if (IsTimeout() && calls == 1) {
      std::unique_lock<std::mutex> lock(gate_mu);
      gate_cv.wait(lock, [] { return writer_entered; });
      assert(!done());
      return false;
    }
    if (IsTimeout()) {
      // The drain must happen before any error receipt, reuse or scope exit.
      assert(receipts.load() == 0 && requests.load() == 1);
      assert(!committed.load());
      { std::lock_guard<std::mutex> lock(gate_mu); writer_released = true; }
      gate_cv.notify_all();
    }
    while (!done()) std::this_thread::yield();
    return true;
  }
};
'''
MAIN_START = r'''
int main(int argc, char** argv) {
  assert(argc == 2); test_case = argv[1];
  MiniPool pool(2); FakePump pump;
  struct { std::vector<uint8_t> cap_staging{1, 2, 3, 4}; } state;
  struct { int png = 0; } opt;
  struct { unsigned request_id = 1; int frame_index = 0; } req;
  std::vector<uint8_t> rgba(4);
  uint64_t run_id = 1; unsigned ok_replies = 0; int conn = 0, exit_code = 0;
  std::string out_path = "same-output.png";
  for (int index = 0; index < 2; ++index) {
    ++requests; ProduceResult r;
'''
MAIN_END = r'''
  }
  assert(pool.Done());
  assert(requests.load() == (IsTimeout() ? 1 : 2));
  assert(receipts.load() == requests.load());
  assert(ok_replies == (test_case == "ok" ? 2u : 0u));
  assert(exit_code == (IsTimeout() ? 4 : 0));
}
'''


if __name__ == '__main__':
    unittest.main()
