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
#include <functional>
#include <mutex>
#include <memory>
#include <map>

#include "include/cef_render_process_handler.h"
#include <string>
#include <vector>

#include "include/cef_app.h"
#include "include/cef_browser.h"
#include "include/cef_client.h"

#include "frame_sync_machine.hpp"
#include "js_error_observer.h"

namespace vtx_cef {

// 启动选项（main.cpp 解析；生命周期须覆盖 CefInitialize→CefShutdown）。
struct Options {
  std::string mode = "a0";             // a0=Software OSR | a1=GPU 合成
  std::string ozone_platform = "headless";  // 仅 a1：headless|x11|wayland
  int width = 1920;
  int height = 1080;
  bool probe = false;                  // 注入 WebGL/WebGPU 探针
  bool probe_gpu_page = false;         // 额外导航 chrome://gpu 提取特性
  // #110 serve/故障注入：视图实际渲染尺寸（GetViewRect 取此值；0=width/
  // height）。捕获卫兵仍按 width/height 期望 → 两者不一致即 SIZE_MISMATCH
  // 故障注入路径（DoD① width=1920,height=1079）。
  int render_width = 0;
  int render_height = 0;
  // #110 serve 模式标记：注入 --disable-crash-reporter（abort 故障注入
  // 演练不落 crashpad 副本/不挂崩溃处理器），一次性渲染路径不受影响。
  bool serve_mode = false;
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
  JsErrorGate js_error;
  bool js_paint_pending = false;
  std::vector<uint8_t> js_paint_candidate;
  int js_paint_w = 0, js_paint_h = 0;
  std::string last_error;                 // 探针/渲染链路错误显形

  // —— #110 serve 逐请求捕获（serve 循环装配，OnPaint 同线程消费）——
  // capture_armed 仅在 serve 模式置位； armed=false 的 OnPaint 走 #109
  // 全帧缓存路径（一次性渲染模式行为不变）。
  bool capture_armed = false;
  uint32_t cap_expected_w = 0;        // 尺寸卫兵期望（不符→SIZE_MISMATCH）
  uint32_t cap_expected_h = 0;
  int32_t cap_target_f = -1;          // 金丝雀目标帧号
  uint8_t* cap_dst = nullptr;         // 紧凑拷贝目标（SHM 槽数据/暂存）
  bool cap_captured = false;          // 金丝雀命中
  bool cap_size_mismatch = false;     // 尺寸不符（记录实际值，不拷贝）
  uint32_t cap_actual_w = 0;
  uint32_t cap_actual_h = 0;
  uint32_t cap_rejected = 0;          // 陈旧 OnPaint 拒绝计数
  uint64_t cap_last_capture_us = 0;   // 最近一次 OnPaint→目标拷贝耗时
  std::vector<uint8_t> cap_staging;   // 模式 1 暂存区（serve 预留容量）

  // —— #114 sync 模式（FrameSyncStateMachine 驱动；OnPaint 与请求循环
  //    同在 UI 线程，装配无跨线程竞态）。sync_machine 非空且
  //    sync_capture_armed 时 OnPaint 走状态机闸门（早期丢弃/拒收/接受+
  //    原像素恢复），不再走 #110 金丝雀路径。——
  fsync::FrameSyncStateMachine* sync_machine = nullptr;
  bool sync_capture_armed = false;
  uint8_t* sync_dst = nullptr;             // 接受帧紧凑拷贝目标
  bool sync_captured = false;              // S5 接受
  bool sync_size_mismatch = false;         // 尺寸不符（记录实际值）
  uint32_t sync_actual_w = 0;
  uint32_t sync_actual_h = 0;
  fsync::PaintVerdict sync_last_verdict;   // 最近一次 FeedPaint 判定
  std::vector<std::string> sync_ready_queue;  // __VTX_READY__ 待消费事件
};

// 探针回传 marker（console.log 通道；JSON 体不得超过控制台行限制，
// gpu 页文本摘要截断到 1500 字符）。
inline constexpr const char kPageProbeMarker[] = "VTXCEFPROBE:";
inline constexpr const char kGpuPageProbeMarker[] = "VTXGPUJSON:";
// #114 注入桥就绪通道 marker（console.debug；工单口径通知通道）。
inline constexpr const char kSyncReadyMarker[] = "__VTX_READY__:";

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

// —— #110 共享装配件（一次性渲染与 serve 两模式共用，防配置漂移）——

// CefSettings 公共面（no_sandbox/单线程消息循环/外置泵/windowless/警告级
// 日志/root_cache_path）。
CefSettings MakeWorkerSettings(const std::string& root_cache_path);
// OSR 建窗（SetAsWindowless(0) + 非 shared texture + external begin frame +
// 透明背景），返回 CreateBrowser 同步结果（浏览器实例经 OnAfterCreated
// 异步送达 RunState::browser）。
bool CreateOsrBrowser(CefRefPtr<CefClient> client, const CefString& url,
                      int width, int height);

// 外置泵驱动器（#109 main.cpp 泵语义原样抽提）：OnScheduleMessagePumpWork
// 唤醒即时干活；空闲保底每 ~8ms 一拍；Until 完成后补一拍。
struct PumpDriver {
  RunState* state = nullptr;
  void Once();
  bool Until(const std::function<bool()>& done, int timeout_ms);
};

class WorkerApp : public CefApp, public CefBrowserProcessHandler {
 public:
  WorkerApp(const Options* options, RunState* state)
      : options_(options), state_(state) {}

  CefRefPtr<CefBrowserProcessHandler> GetBrowserProcessHandler() override {
    return this;
  }

  CefRefPtr<CefRenderProcessHandler> GetRenderProcessHandler() override {
    if (!renderer_) renderer_ = MakeJsErrorObserver();
    return renderer_;
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
  CefRefPtr<CefRenderProcessHandler> renderer_;
};

class WorkerClient : public CefClient,
                     public CefLifeSpanHandler,
                     public CefLoadHandler,
                     public CefRenderHandler,
                     public CefDisplayHandler {
 public:
  WorkerClient(const Options* options, RunState* state)
      : options_(options), state_(state) {}
  // A short-lived unsigned browser may finish closing asynchronously. Keep its
  // callback storage alive independently of the caller's stack.
  WorkerClient(const Options* options, std::shared_ptr<RunState> state)
      : options_(options), state_(state.get()), owned_state_(std::move(state)) {}

  // CefClient
  CefRefPtr<CefLifeSpanHandler> GetLifeSpanHandler() override { return this; }
  CefRefPtr<CefLoadHandler> GetLoadHandler() override { return this; }
  CefRefPtr<CefRenderHandler> GetRenderHandler() override { return this; }
  CefRefPtr<CefDisplayHandler> GetDisplayHandler() override { return this; }

  bool OnProcessMessageReceived(CefRefPtr<CefBrowser> browser,
      CefRefPtr<CefFrame> frame, CefProcessId source,
      CefRefPtr<CefProcessMessage> message) override;

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
  std::shared_ptr<RunState> owned_state_;
};

}  // namespace vtx_cef

#endif  // GFX_CEF_WORKER_APP_HANDLER_H_
