// vtx_cef_worker — CEF Native OSR Worker 骨架（issue #108 构建系统范围）
//
// 本工单只交付构建系统与版本锁：此处是可编译/可链接/可运行的最小 worker
// 骨架，不初始化 CEF 消息循环与 OSR 渲染（#109+ 的范围）。
//
// --sanity 自检（版本锁运行时自证）：
//   1) libcef.so 导出的 cef_version_info() 八个版本分量逐项比对编译期
//      锁定的 cef_version.h 宏 —— 库与头漂移即失败；
//   2) cef_api_hash() 运行时 API hash 比对编译期 CEF_API_HASH_PLATFORM ——
//      wrapper 头文件与 libcef.so 二进制不兼容即失败；
//   3) 实例化 CefApp 子类 —— libcef_dll_wrapper 静态库真实参与链接。
#include <cstdio>
#include <cstring>
#include <string>

#include "include/cef_api_hash.h"
#include "include/cef_app.h"
#include "include/cef_version.h"
#include "include/cef_version_info.h"

// cef_version.h 只提供 Chromium 分量宏；组合成完整版本串（锁定值）。
#define VTX_CHROME_VERSION_STRING    \
  MAKE_STRING(CHROME_VERSION_MAJOR)  \
  "." MAKE_STRING(CHROME_VERSION_MINOR) "." MAKE_STRING(           \
      CHROME_VERSION_BUILD) "." MAKE_STRING(CHROME_VERSION_PATCH)

namespace {

// #109+ 在此挂 OSR 生命周期钩子；当前仅证明 wrapper 链接与 vtable 可用。
class WorkerApp : public CefApp, public CefBrowserProcessHandler {
 public:
  WorkerApp() = default;

  CefRefPtr<CefBrowserProcessHandler> GetBrowserProcessHandler() override {
    return this;
  }

 private:
  IMPLEMENT_REFCOUNTING(WorkerApp);
  DISALLOW_COPY_AND_ASSIGN(WorkerApp);
};

struct VersionCheck {
  const char* name;
  int runtime;  // libcef.so 运行时导出值
  int locked;   // 编译期锁定宏
};

bool Check(const VersionCheck& check) {
  const bool ok = check.runtime == check.locked;
  std::printf("%-22s runtime=%-8d locked=%-8d %s\n", check.name,
              check.runtime, check.locked, ok ? "ok" : "MISMATCH");
  return ok;
}

int Sanity() {
  const VersionCheck checks[] = {
      {"CEF_VERSION_MAJOR", cef_version_info(0), CEF_VERSION_MAJOR},
      {"CEF_VERSION_MINOR", cef_version_info(1), CEF_VERSION_MINOR},
      {"CEF_VERSION_PATCH", cef_version_info(2), CEF_VERSION_PATCH},
      {"CHROME_VERSION_MAJOR", cef_version_info(4), CHROME_VERSION_MAJOR},
      {"CHROME_VERSION_MINOR", cef_version_info(5), CHROME_VERSION_MINOR},
      {"CHROME_VERSION_BUILD", cef_version_info(6), CHROME_VERSION_BUILD},
      {"CHROME_VERSION_PATCH", cef_version_info(7), CHROME_VERSION_PATCH},
  };
  bool ok = true;
  for (const VersionCheck& check : checks) {
    ok = Check(check) && ok;
  }

  const char* runtime_hash = cef_api_hash(CEF_API_VERSION, 0);
  const bool hash_ok = runtime_hash != nullptr &&
                       std::strcmp(runtime_hash, CEF_API_HASH_PLATFORM) == 0;
  std::printf("%-22s runtime=%s\n%-22s locked=%s %s\n", "CEF_API_HASH_PLATFORM",
              runtime_hash != nullptr ? runtime_hash : "(null)",
              "", CEF_API_HASH_PLATFORM, hash_ok ? "ok" : "MISMATCH");
  ok = hash_ok && ok;

  CefRefPtr<WorkerApp> app = new WorkerApp();
  const bool wrapper_ok = app->GetBrowserProcessHandler().get() == app.get();
  std::printf("%-22s %s\n", "libcef_dll_wrapper",
              wrapper_ok ? "ok" : "MISMATCH");
  ok = wrapper_ok && ok;

  if (ok) {
    std::printf("SANITY OK: cef=%s chromium=%s (libcef.so 与版本锁一致)\n",
                CEF_VERSION, VTX_CHROME_VERSION_STRING);
    return 0;
  }
  std::fprintf(stderr, "SANITY FAIL: libcef.so 与编译期版本锁不一致\n");
  return 1;
}

}  // namespace

int main(int argc, char* argv[]) {
  if (argc == 2 && std::strcmp(argv[1], "--sanity") == 0) {
    return Sanity();
  }
  // 默认面：打印身份信息。CEF 主循环/OSR 渲染接线属 #109+ 工单。
  std::printf("vtx_cef_worker (issue #108 build skeleton)\n"
              "CEF:      %s\n"
              "Chromium: %s\n"
              "usage:    %s --sanity\n",
              CEF_VERSION, VTX_CHROME_VERSION_STRING, argv[0]);
  return 0;
}
