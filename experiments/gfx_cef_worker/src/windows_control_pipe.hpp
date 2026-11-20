#ifndef GFX_CEF_WORKER_WINDOWS_CONTROL_PIPE_H_
#define GFX_CEF_WORKER_WINDOWS_CONTROL_PIPE_H_

#include "native_runtime.hpp"

#include <algorithm>
#include <thread>

namespace vtx_cef {

// Anonymous byte pipes, inherited by handle list. Their child ends must not
// leak to Chromium helpers. The host Job owns the whole tree independently of
// EOF; the byte protocol has no listening socket and no shared memory handle.
class WindowsControlPipe {
 public:
  WindowsControlPipe() = default;
  WindowsControlPipe(const WindowsControlPipe&) = delete;
  WindowsControlPipe& operator=(const WindowsControlPipe&) = delete;
  ~WindowsControlPipe() { Close(); }

  bool Adopt(uintptr_t read_handle, uintptr_t write_handle) {
    BOOL in_job = FALSE;
    if (!::IsProcessInJob(::GetCurrentProcess(), nullptr, &in_job) || !in_job ||
        !read_handle || !write_handle || read_handle == write_handle) return false;
    HANDLE read = reinterpret_cast<HANDLE>(read_handle);
    HANDLE write = reinterpret_cast<HANDLE>(write_handle);
    if (::GetFileType(read) != FILE_TYPE_PIPE || ::GetFileType(write) != FILE_TYPE_PIPE)
      return false;
    read_ = read;
    write_ = write;
    DWORD available = 0;
    return ::SetHandleInformation(read_, HANDLE_FLAG_INHERIT, 0) &&
           ::SetHandleInformation(write_, HANDLE_FLAG_INHERIT, 0) &&
           ::PeekNamedPipe(read_, nullptr, 0, nullptr, &available, nullptr);
  }

  int Poll(int timeout_ms) const {
    DWORD available = 0;
    return WaitAvailable(timeout_ms, &available);
  }

  int Receive(void* buf, size_t n, int timeout_ms) const {
    auto* bytes = static_cast<uint8_t*>(buf);
    const auto deadline = Clock::now() + std::chrono::milliseconds(timeout_ms);
    size_t got = 0;
    while (got < n) {
      const auto remaining = std::chrono::ceil<std::chrono::milliseconds>(deadline - Clock::now());
      DWORD available = 0;
      const int ready = WaitAvailable(static_cast<int>(std::max<int64_t>(0, remaining.count())),
                                      &available);
      if (ready < 0) return -2;
      if (!ready) return got ? -2 : -1;
      if (!available) return got ? -2 : 0;
      DWORD count = 0;
      if (!::ReadFile(read_, bytes + got, static_cast<DWORD>(std::min<size_t>(available, n - got)),
                      &count, nullptr)) return -2;
      if (!count) return got ? -2 : 0;
      got += count;
    }
    return 1;
  }

  bool Send(const void* buf, size_t n) const {
    // All replies are fixed size (<= 128 bytes); host loss closes the pipe,
    // and host cancellation/timeout terminates the owned Job, even in I/O.
    DWORD count = 0;
    return ::WriteFile(write_, buf, static_cast<DWORD>(n), &count, nullptr) && count == n;
  }

  void Close() {
    if (read_) ::CloseHandle(read_);
    if (write_) ::CloseHandle(write_);
    read_ = write_ = nullptr;
  }

 private:
  using Clock = std::chrono::steady_clock;

  int WaitAvailable(int timeout_ms, DWORD* available) const {
    const auto deadline = Clock::now() + std::chrono::milliseconds(timeout_ms);
    for (;;) {
      *available = 0;
      if (!::PeekNamedPipe(read_, nullptr, 0, nullptr, available, nullptr)) {
        const DWORD err = ::GetLastError();
        return err == ERROR_BROKEN_PIPE || err == ERROR_PIPE_NOT_CONNECTED ? 1 : -1;
      }
      if (*available) return 1;
      const auto now = Clock::now();
      if (now >= deadline) return 0;
      std::this_thread::sleep_until(std::min(deadline, now + std::chrono::milliseconds(2)));
    }
  }

  HANDLE read_ = nullptr;
  HANDLE write_ = nullptr;
};

}  // namespace vtx_cef
#endif
