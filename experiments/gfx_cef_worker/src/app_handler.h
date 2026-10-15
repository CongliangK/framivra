// app_handler.h — CEF App/Client/OSR 渲染与后端探针（issue #109 CEF-01b）。
//
// 职责（工单口径）：
//   - WorkerApp：OnBeforeCommandLineProcessing 按 A0/A1 双模式注入开关
//     （A0=软件 OSR；A1=GPU 合成→CPU Buffer，支持 --ozone-platform 切换）；
//     OnScheduleMessagePumpWork 配合 external_message_pump 外置泵。
//   - WorkerClient：OSR（SetAsWindowless(0)+external_begin_frame+
//     shared_texture=false）OnPaint 全帧 BGRA 缓存 → 直通 alpha RGBA PNG；
//     真实渲染后端探针（页面内 WebGL WEBGL_debug_renderer_info / WebGPU
//     adapter info + chrome://gpu 特性文本），经 OnConsoleMessage marker
//     通道回传浏览器进程（WebUI 页无 cefQuery，console 通道全页通用）。
#ifndef GFX_CEF_WORKER_APP_HANDLER_H_
#define GFX_CEF_WORKER_APP_HANDLER_H_

#include <atomic>
#include <cstdint>
#include <mutex>
#include <string>
#include <vector>

#include "include/cef_app.h"
#include "include/cef_browser.h"
#include "include/cef_client.h"

namespace vtx_cef {

// 启动选项（main.cpp 解析；生命周期须覆盖 CefInitialize→CefShutdown）。
struct Options {
  std::string mode = "a0";             // a0=Software OSR | a1=GPU 合成
  std::string ozone_platform = "headless";  // 仅 a1：headless|x11|wayland
  int width = 1920;
  int height = 1080;
  bool probe = false;                  // 注入 WebGL/WebGPU 探针
  bool probe_gpu_page = false;         // 额外导航 chrome://gpu 提取特性
};

// 单次运行状态。CEF 回调与主循环同在 UI/主线程（external_message_pump +
// multi_threaded_message_loop=false）；原子量只为 hold 轮询可见性。
struct RunState {
  std::atomic<bool> loaded{false};        // OnLoadingStateChange(!isLoading)
  std::atomic<bool> closed{false};        // OnBeforeClose
  std::atomic<int> paints{0};             // OnPaint（PET_VIEW）次数
  std::atomic<bool> pump_scheduled{false};
  std::atomic<int64_t> pump_delay_ms{0};

  std::mutex mu;                          // 以下四组跨回调/主循环共享
  std::vector<uint8_t> buffer;            // 最近一次全帧 BGRA（上行）
  int width = 0;
  int height = 0;
  CefRefPtr<CefBrowser> browser;          // OnAfterCreated 捕获（异步建窗）
  std::string page_probe;                 // WebGL/WebGPU 探针 JSON（首个为准）
  std::string gpu_page_probe;             // chrome://gpu 提取 JSON（首个为准）
  std::string last_error;                 // 探针/渲染链路错误显形
};

// 探针回传 marker（console.log 通道；JSON 体不得超过控制台行限制，
// gpu 页文本摘要截断到 1500 字符）。
inline constexpr const char kPageProbeMarker[] = "VTXCEFPROBE:";
inline constexpr const char kGpuPageProbeMarker[] = "VTXGPUJSON:";

// 工单 A0 注入（Software OSR）：禁 GPU 全链，纯 CPU 光栅。
void AppendA0Switches(CefRefPtr<CefCommandLine> command_line);
// 工单 A1 注入（GPU Composition → CPU Buffer OSR）：保留 GPU，启用 GPU
// 光栅 + 忽略 blocklist；ozone 平台按参数切换。
void AppendA1Switches(CefRefPtr<CefCommandLine> command_line,
                      const std::string& ozone_platform);

// 页面探针 JS（WebGL debug_renderer_info + WebGPU adapter info；
// 双通道回传：console.log marker；不依赖 cefQuery —— WebUI 页也可用）。
std::string BuildPageProbeJs();
// chrome://gpu 特性提取 JS（穿 shadow DOM 收集文本 + 特性行正则）。
std::string BuildGpuPageProbeJs();

class WorkerApp : public CefApp, public CefBrowserProcessHandler {
 public:
  WorkerApp(const Options* options, RunState* state)
      : options_(options), state_(state) {}

  CefRefPtr<CefBrowserProcessHandler> GetBrowserProcessHandler() override {
    return this;
  }

  void OnBeforeCommandLineProcessing(
      const CefString& process_type,
      CefRefPtr<CefCommandLine> command_line) override;

  void OnScheduleMessagePumpWork(int64_t delay_ms) override;

 private:
  IMPLEMENT_REFCOUNTING(WorkerApp);
  DISALLOW_COPY_AND_ASSIGN(WorkerApp);

  const Options* options_;
  RunState* state_;
};

class WorkerClient : public CefClient,
                     public CefLifeSpanHandler,
                     public CefLoadHandler,
                     public CefRenderHandler,
                     public CefDisplayHandler {
 public:
  WorkerClient(const Options* options, RunState* state)
      : options_(options), state_(state) {}

  // CefClient
  CefRefPtr<CefLifeSpanHandler> GetLifeSpanHandler() override { return this; }
  CefRefPtr<CefLoadHandler> GetLoadHandler() override { return this; }
  CefRefPtr<CefRenderHandler> GetRenderHandler() override { return this; }
  CefRefPtr<CefDisplayHandler> GetDisplayHandler() override { return this; }

  // CefLifeSpanHandler
  void OnAfterCreated(CefRefPtr<CefBrowser> browser) override;
  void OnBeforeClose(CefRefPtr<CefBrowser> browser) override;

  // CefLoadHandler
  void OnLoadingStateChange(CefRefPtr<CefBrowser> browser, bool isLoading,
                            bool canGoBack, bool canGoForward) override;
  void OnLoadError(CefRefPtr<CefBrowser> browser, CefRefPtr<CefFrame> frame,
                   ErrorCode errorCode, const CefString& failedUrl,
                   const CefString& text) override;

  // CefRenderHandler
  void GetViewRect(CefRefPtr<CefBrowser> browser, CefRect& rect) override;
  void OnPaint(CefRefPtr<CefBrowser> browser, PaintElementType type,
               const RectList& dirtyRects, const void* buffer, int width,
               int height) override;

  // CefDisplayHandler：console.log marker 通道（探针回传）。
  bool OnConsoleMessage(CefRefPtr<CefBrowser> browser,
                        cef_log_severity_t level, const CefString& message,
                        const CefString& source, int line) override;

 private:
  IMPLEMENT_REFCOUNTING(WorkerClient);
  DISALLOW_COPY_AND_ASSIGN(WorkerClient);

  const Options* options_;
  RunState* state_;
};

}  // namespace vtx_cef

#endif  // GFX_CEF_WORKER_APP_HANDLER_H_
