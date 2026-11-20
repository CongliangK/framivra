#ifndef GFX_CEF_WORKER_NATIVE_RUNTIME_H_
#define GFX_CEF_WORKER_NATIVE_RUNTIME_H_

#include <chrono>
#include <cstdio>
#include <cstdint>
#include <filesystem>
#include <string>

#if defined(_WIN32)
#include <windows.h>
#else
#include <unistd.h>
#endif

namespace vtx_cef {

inline uint64_t NowUs() {
  return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::microseconds>(
      std::chrono::steady_clock::now().time_since_epoch()).count());
}

inline uint32_t NativePid() {
#if defined(_WIN32)
  return ::GetCurrentProcessId();
#else
  return static_cast<uint32_t>(::getpid());
#endif
}

// Wire and command-line strings are UTF-8. Never pass them through the
// Windows ANSI code page (project/profile/log paths can contain Unicode).
inline std::filesystem::path NativePath(const std::string& path) {
#if defined(_WIN32)
  return std::filesystem::path(std::u8string(path.begin(), path.end()));
#else
  return std::filesystem::path(path);
#endif
}

inline std::string PathUtf8(const std::filesystem::path& path) {
#if defined(_WIN32)
  const auto bytes = path.u8string();
  return std::string(bytes.begin(), bytes.end());
#else
  return path.string();
#endif
}

#if defined(_WIN32)
// Relative Win32 paths always have the old MAX_PATH bound. The owned cwd
// itself can fit that bound while a child PNG temporary filename does not.
// Resolve using the native path and prefix only when needed; do not depend
// on the machine-wide LongPathsEnabled policy.
inline std::filesystem::path WindowsIoPath(const std::string& path) {
  std::error_code ec;
  const auto absolute = std::filesystem::absolute(NativePath(path), ec);
  if (ec) return {};
  const auto value = absolute.lexically_normal().native();
  if (value.size() < MAX_PATH || value.starts_with(L"\\\\?\\") || value.starts_with(L"\\\\.\\"))
    return std::filesystem::path(value);
  if (value.starts_with(L"\\\\")) return std::filesystem::path(L"\\\\?\\UNC\\" + value.substr(2));
  return std::filesystem::path(L"\\\\?\\" + value);
}
#endif

inline std::FILE* OpenWriteFile(const std::string& path) {
#if defined(_WIN32)
  return ::_wfopen(WindowsIoPath(path).c_str(), L"wb");
#else
  return std::fopen(path.c_str(), "wb");
#endif
}

#if defined(_WIN32)
// The caller treats an empty path as a failure, not as the current directory.
inline std::filesystem::path ExecutableDirectory() {
  std::wstring buf(32768, L'\0');
  const DWORD len = ::GetModuleFileNameW(nullptr, buf.data(), static_cast<DWORD>(buf.size()));
  if (!len || len >= buf.size()) return {};
  buf.resize(len);
  return std::filesystem::path(buf).parent_path();
}
#endif

}  // namespace vtx_cef
#endif
