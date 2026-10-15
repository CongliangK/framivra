// app_handler.cpp — CEF App/Client/OSR 渲染与后端探针实现（issue #109，
// #110 增 serve 捕获路径与共享装配件）。
#include "app_handler.h"

#include <chrono>
#include <cstring>
#include <thread>

#include "frame_pipeline.h"
#include "shm_ring.hpp"

#include "include/cef_command_line.h"

// -Wall -Wextra -Werror：虚函数覆写的未用形参一律匿名化。

namespace vtx_cef {

// ---------------------------------------------------------------- 模式开关注入

void AppendA0Switches(CefRefPtr<CefCommandLine> command_line) {
  // 工单 A0（Software OSR）：禁 GPU 全链 → SwiftShader/软件光栅路径。
  command_line->AppendSwitch("disable-gpu");
  command_line->AppendSwitch("disable-gpu-compositing");
  command_line->AppendSwitch("disable-gpu-vsync");
  command_line->AppendSwitch("hide-scrollbars");
  command_line->AppendSwitchWithValue("force-color-profile", "srgb");
  // 偏差说明（工单外追加）：Chromium 131+ 默认禁软件 WebGL（无 GPU 时
  // getContext 返回 null），探针将无后端证据可采。本开关只解锁软件 WebGL
  // 上下文创建（渲染路径仍由 --disable-gpu 钉死在软件光栅），
  // WEBGL_debug_renderer_info 才能实测记录 SwiftShader 回退事实。
  command_line->AppendSwitch("enable-unsafe-swiftshader");
}

void AppendA1Switches(CefRefPtr<CefCommandLine> command_line,
                      const std::string& ozone_platform) {
  // 工单 A1（GPU Composition → CPU Buffer OSR）：不禁 GPU，走
  // CEF Issue #4166 验证的内部 GPU 合成→CPU buffer 拷出路径。
  command_line->AppendSwitch("enable-gpu-rasterization");
  command_line->AppendSwitch("ignore-gpu-blocklist");
  command_line->AppendSwitch("disable-gpu-vsync");
  command_line->AppendSwitch("hide-scrollbars");
  command_line->AppendSwitchWithValue("force-color-profile", "srgb");
  // 同 A0 偏差说明：硬件 WebGL 可用时本开关无影响；硬件不可用时解锁
  // SwiftShader 软件回退 —— 矩阵才能实测记录「退化为 SwiftShader」这一
  // 真相（工单矩阵项的核心观测值），而非无证据。
  command_line->AppendSwitch("enable-unsafe-swiftshader");
  if (!ozone_platform.empty()) {
    command_line->AppendSwitchWithValue("ozone-platform", ozone_platform);
  }
}

void WorkerApp::OnBeforeCommandLineProcessing(
    const CefString& process_type, CefRefPtr<CefCommandLine> command_line) {
  // 子进程再入路径（CefExecuteProcess 的占位 app，options_=null）不做注入；
  // 仅浏览器进程（process_type 为空）注入，子进程开关由 Chromium 自行传播
  // ——对子进程改参是未定义行为（cef_app.h 文档）。
  if (options_ == nullptr || !process_type.empty()) {
    return;
  }
  if (options_->serve_mode) {
    // serve 模式：DoD③ abort() 演练需原生 SIGABRT 语义（无崩溃副本/无
    // 崩溃处理器拦截），一次性渲染路径不注入。
    command_line->AppendSwitch("disable-crash-reporter");
  }
  if (options_->mode == "a0") {
    AppendA0Switches(command_line);
  } else if (options_->mode == "a1") {
    AppendA1Switches(command_line, options_->ozone_platform);
  }
}

void WorkerApp::OnScheduleMessagePumpWork(int64_t delay_ms) {
  // external_message_pump 外置泵契约：CEF 有活要干时叫醒主循环。
  state_->pump_delay_ms.store(delay_ms, std::memory_order_relaxed);
  state_->pump_scheduled.store(true, std::memory_order_relaxed);
}

// ---------------------------------------------------------------- 探针 JS

std::string BuildPageProbeJs() {
  // WebGL WEBGL_debug_renderer_info（UNMASKED_VENDOR/RENDERER）+ WebGPU
  // adapter info。双通道回传：console.log marker（全页通用，WebUI 亦然）。
  return R"CEFPS(
(function () {
  var MARK = 'VTXCEFPROBE:';
  var out = { href: location.href, ua: navigator.userAgent };
  try {
    var c = document.createElement('canvas');
    var gl = c.getContext('webgl2') || c.getContext('webgl');
    if (!gl) {
      out.webgl = { error: 'no-webgl-context' };
    } else {
      var ext = gl.getExtension('WEBGL_debug_renderer_info');
      out.webgl = ext ? {
        unmasked: true,
        vendor: gl.getParameter(ext.UNMASKED_VENDOR_WEBGL),
        renderer: gl.getParameter(ext.UNMASKED_RENDERER_WEBGL),
        version: gl.getParameter(gl.VERSION),
        glsl_version: gl.getParameter(gl.SHADING_LANGUAGE_VERSION)
      } : {
        unmasked: false,
        vendor: gl.getParameter(gl.VENDOR),
        renderer: gl.getParameter(gl.RENDERER),
        version: gl.getParameter(gl.VERSION),
        glsl_version: gl.getParameter(gl.SHADING_LANGUAGE_VERSION)
      };
    }
  } catch (e) { out.webgl = { error: String(e) }; }
  var send = function () {
    try { console.log(MARK + JSON.stringify(out)); } catch (e) {}
  };
  if (navigator.gpu) {
    try {
      navigator.gpu.requestAdapter().then(function (a) {
        if (!a) { out.webgpu = { adapter: null }; send(); return; }
        var finish = function (i) {
          out.webgpu = i ? { vendor: i.vendor, architecture: i.architecture,
                             device: i.device, description: i.description }
                         : { info: 'unavailable' };
          send();
        };
        if (a.info) { finish(a.info); }
        else if (a.requestAdapterInfo) {
          a.requestAdapterInfo().then(finish, function () { finish(null); });
        } else { finish(null); }
      }, function (err) { out.webgpu = { error: String(err) }; send(); });
      return;
    } catch (e) { out.webgpu = { error: String(e) }; }
  } else {
    out.webgpu = { unsupported: true };
  }
  send();
})();
)CEFPS";
}

std::string BuildGpuPageProbeJs() {
  // chrome://gpu 特性提取：WebUI 页为 shadow DOM 组合，innerText 不穿透
  // ——递归收集所有 shadowRoot 文本；特性行（"Canvas: Hardware accelerated"
  // 风格）正则归一为 dict。文本头 1500 字符随证据落档供复核。
  return R"CEFGPS(
(function () {
  var MARK = 'VTXGPUJSON:';
  var parts = [];
  function deepText(root, depth) {
    if (!root || depth > 12) return;
    try {
      // ShadowRoot 非 Element：无 innerText —— 统一 textContent（含隐藏
      // 文本，对特性提取无碍），否则 shadow 内容静默丢失。
      parts.push(root.textContent || '');
    } catch (e) {}
    var all = root.querySelectorAll('*');
    for (var i = 0; i < all.length; i++) {
      if (all[i].shadowRoot) deepText(all[i].shadowRoot, depth + 1);
    }
  }
  deepText(document.body, 0);
  var text = parts.join('\n');
  var features = {};
  var re = /([A-Za-z][A-Za-z0-9_\/ ()+-]{2,60}):\s*(Hardware accelerated|Software only\. GPU acceleration disabled|Software only|Disabled|Force enabled|Force disabled)/g;
  var m;
  while ((m = re.exec(text))) { features[m[1].trim()] = m[2]; }
  var payload = { url: location.href, features: features,
                  text_length: text.length, text_head: text.slice(0, 1500) };
  try { console.log(MARK + JSON.stringify(payload)); } catch (e) {}
})();
)CEFGPS";
}

// ---------------------------------------------------------------- WorkerClient

void WorkerClient::OnAfterCreated(CefRefPtr<CefBrowser> browser) {
  std::lock_guard<std::mutex> lock(state_->mu);
  state_->browser = browser;  // CreateBrowser 异步送达（工单：外置泵架构）
}

void WorkerClient::OnBeforeClose(CefRefPtr<CefBrowser> /*browser*/) {
  state_->closed.store(true, std::memory_order_relaxed);
}

void WorkerClient::OnLoadingStateChange(CefRefPtr<CefBrowser> /*browser*/,
                                        bool isLoading,
                                        bool /*canGoBack*/,
                                        bool /*canGoForward*/) {
  if (!isLoading) {
    state_->loaded.store(true, std::memory_order_relaxed);
  }
}

void WorkerClient::OnLoadError(CefRefPtr<CefBrowser> /*browser*/,
                               CefRefPtr<CefFrame> frame, ErrorCode errorCode,
                               const CefString& errorText,
                               const CefString& failedUrl) {
  if (frame != nullptr && frame->IsMain()) {
    std::lock_guard<std::mutex> lock(state_->mu);
    state_->last_error = "load error " + std::to_string(errorCode) + " " +
                         errorText.ToString() + " @ " + failedUrl.ToString();
  }
}

void WorkerClient::GetViewRect(CefRefPtr<CefBrowser> /*browser*/,
                               CefRect& rect) {
  // 视图实际尺寸：故障注入可用 render_* 偏离 width/height（DoD① 尺寸
  // 不符帧路径）；缺省与期望一致。
  const int w = options_->render_width > 0 ? options_->render_width
                                           : options_->width;
  const int h = options_->render_height > 0 ? options_->render_height
                                            : options_->height;
  rect = CefRect(0, 0, w, h);
}

void WorkerClient::OnPaint(CefRefPtr<CefBrowser> /*browser*/,
                           PaintElementType type,
                           const RectList& /*dirtyRects*/,
                           const void* buffer, int width, int height) {
  if (type != PET_VIEW) {
    return;  // 弹出层不入帧
  }
  const auto* src = static_cast<const uint8_t*>(buffer);
  std::lock_guard<std::mutex> lock(state_->mu);

  if (state_->capture_armed) {
    // ===== #110 serve 逐请求捕获：尺寸卫兵 → 紧凑拷贝 → 金丝雀核实 =====
    if (static_cast<uint32_t>(width) != state_->cap_expected_w ||
        static_cast<uint32_t>(height) != state_->cap_expected_h) {
      // 尺寸不符帧：拒收不拷贝，记录实际尺寸（DoD①：回执 SIZE_MISMATCH +
      // 实测 w/h 显形）。
      state_->cap_size_mismatch = true;
      state_->cap_actual_w = static_cast<uint32_t>(width);
      state_->cap_actual_h = static_cast<uint32_t>(height);
      return;
    }
    const uint64_t t0 = NowUs();
    // 逐行紧凑拷贝：源步长任意（CEF OSR 紧凑缓冲步长 = width*4；对齐/
    // 外包框缓冲更宽时同一路径逐行收紧行距，杜绝斜纹）。
    CopyCompactRows(src, static_cast<size_t>(width) * 4u, state_->cap_dst,
                    static_cast<uint32_t>(width),
                    static_cast<uint32_t>(height));
    state_->cap_last_capture_us = NowUs() - t0;
    int32_t canary = -1;
    if (DecodeCanaryFrameIndex(state_->cap_dst, static_cast<uint32_t>(width),
                               static_cast<uint32_t>(height), &canary) &&
        canary == state_->cap_target_f) {
      state_->cap_captured = true;  // 金丝雀核实：确系请求帧
    } else {
      ++state_->cap_rejected;  // 陈旧/异帧 OnPaint 拒绝计数
    }
    state_->paints.store(state_->paints.load(std::memory_order_relaxed) + 1,
                         std::memory_order_relaxed);
    return;
  }

  // ===== #109 一次性渲染路径（行为不变）：全帧缓存 =====
  const size_t bytes = static_cast<size_t>(width) * static_cast<size_t>(height) * 4u;
  // buffer 生命周期仅限回调内 —— 立即整帧拷贝（CEF 给的是全视图尺寸，
  // dirtyRects 只是变化区提示）。
  state_->buffer.assign(src, src + bytes);
  state_->width = width;
  state_->height = height;
  state_->paints.store(state_->paints.load(std::memory_order_relaxed) + 1,
                       std::memory_order_relaxed);
}

bool WorkerClient::OnConsoleMessage(CefRefPtr<CefBrowser> /*browser*/,
                                    cef_log_severity_t /*level*/,
                                    const CefString& message,
                                    const CefString& /*source*/,
                                    int /*line*/) {
  const std::string msg = message.ToString();
  std::lock_guard<std::mutex> lock(state_->mu);
  if (msg.compare(0, strlen(kPageProbeMarker), kPageProbeMarker) == 0) {
    if (state_->page_probe.empty()) {  // 首个为准（双保险去重）
      state_->page_probe = msg.substr(strlen(kPageProbeMarker));
    }
    return true;  // 吞掉，避免污染 stderr 证据
  }
  if (msg.compare(0, strlen(kGpuPageProbeMarker), kGpuPageProbeMarker) == 0) {
    if (state_->gpu_page_probe.empty()) {
      state_->gpu_page_probe = msg.substr(strlen(kGpuPageProbeMarker));
    }
    return true;
  }
  return false;
}

// ---------------------------------------------------------------- #110 共享装配件

CefSettings MakeWorkerSettings(const std::string& root_cache_path) {
  CefSettings settings;
  settings.no_sandbox = true;
  settings.multi_threaded_message_loop = false;
  settings.external_message_pump = true;
  settings.windowless_rendering_enabled = true;
  settings.log_severity = LOGSEVERITY_WARNING;  // 证据可读：保留警告去噪声
  CefString(&settings.root_cache_path).FromString(root_cache_path);
  return settings;
}

bool CreateOsrBrowser(CefRefPtr<CefClient> client, const CefString& url,
                      int width, int height) {
  CefWindowInfo window_info;
  window_info.SetAsWindowless(0);
  window_info.shared_texture_enabled = false;
  window_info.external_begin_frame_enabled = true;
  CefBrowserSettings browser_settings;
  browser_settings.background_color = 0;  // 透明（gfxA 协议要求 alpha 通道）
  (void)width;  // OSR 视口由 GetViewRect 提供（client 持有 Options）
  (void)height;
  return CefBrowserHost::CreateBrowser(window_info, client, url,
                                       browser_settings, nullptr, nullptr);
}

void PumpDriver::Once() {
  state->pump_scheduled.store(false, std::memory_order_relaxed);
  CefDoMessageLoopWork();
}

bool PumpDriver::Until(const std::function<bool()>& done, int timeout_ms) {
  const auto deadline =
      std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
  int idle = 0;
  while (!done()) {
    if (std::chrono::steady_clock::now() >= deadline) {
      return false;
    }
    if (state->pump_scheduled.load(std::memory_order_relaxed) ||
        ++idle >= 4) {
      idle = 0;
      Once();
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }
  Once();
  return true;
}

}  // namespace vtx_cef
