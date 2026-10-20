// serve.cpp — 帧服务模式实现（issue #110 CEF-02a）。
//
// 线程模型：
//   - 主线程：CEF UI（外置泵）+ 请求循环（导航/捕获/槽发布/回执）——
//     OnPaint 与请求处理同线程，捕获装配无跨线程竞态；
//   - 编码线程池（SerialEncodeJob）：模式 1 的逆预乘 + PNG 编码 + 原子落盘
//     （工单「本地线程池完成」；等待期间主线程持续泵 CEF）；
//   - --parent-alive-fd 守卫线程：main() 装配（#109 契约，本文件不重置）。
//
// 退出码：0 = 宿主 EOF 优雅收尾；1 = 致命初始化失败；3 = 协议错误
// （magic/版本/路径非法）；4 = 请求中途连接断裂/编码超时；5 = 空闲超时。
#include "serve.h"

#include <fcntl.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <cerrno>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <memory>
#include <vector>

#include "frame_pipeline.h"
#include "ipc_protocol.hpp"
#include "png_writer.h"
#include "serial_encode_job.h"
#include "shm_ring.hpp"

#include "include/cef_app.h"
#include "include/cef_browser.h"

#if defined(__APPLE__)
#define VTX_SEND_FLAGS 0  // RunServe ignores SIGPIPE before opening sockets.
#else
#define VTX_SEND_FLAGS MSG_NOSIGNAL
#endif

namespace vtx_cef {

// ---------------------------------------------------------------- Socket 基元
//（ipc_protocol.hpp 声明；此处为全仓唯一定义）

int RecvAllOrShort(int fd, void* buf, size_t n, int timeout_ms) {
  auto* p = static_cast<uint8_t*>(buf);
  size_t got = 0;
  while (got < n) {
    struct pollfd pf { fd, POLLIN, 0 };
    const int pr = ::poll(&pf, 1, timeout_ms);
    if (pr == 0) {
      return -1;  // 超时
    }
    if (pr < 0) {
      if (errno == EINTR) {
        continue;
      }
      return -1;
    }
    const ssize_t r = ::recv(fd, p + got, n - got, 0);
    if (r == 0) {
      return got == 0 ? 0 : -2;  // 边界 EOF / 中途断裂
    }
    if (r < 0) {
      if (errno == EINTR) {
        continue;
      }
      return -1;
    }
    got += static_cast<size_t>(r);
  }
  return 1;
}

bool SendAll(int fd, const void* buf, size_t n) {
  const auto* p = static_cast<const uint8_t*>(buf);
  size_t sent = 0;
  while (sent < n) {
    const ssize_t s = ::send(fd, p + sent, n - sent, VTX_SEND_FLAGS);
    if (s < 0) {
      if (errno == EINTR) {
        continue;
      }
      return false;
    }
    sent += static_cast<size_t>(s);
  }
  return true;
}

// Hello + SCM_RIGHTS(SHM fd) 单消息交付：消费端 recvmsg 一次收全。
bool SendHelloWithFd(int conn, const HelloInfo& hello, int shm_fd) {
  struct msghdr msg;
  std::memset(&msg, 0, sizeof(msg));
  struct iovec iov;
  iov.iov_base = const_cast<HelloInfo*>(&hello);
  iov.iov_len = sizeof(hello);
  msg.msg_iov = &iov;
  msg.msg_iovlen = 1;
  char cmsgbuf[CMSG_SPACE(sizeof(int))];
  std::memset(cmsgbuf, 0, sizeof(cmsgbuf));
  msg.msg_control = cmsgbuf;
  msg.msg_controllen = sizeof(cmsgbuf);
  struct cmsghdr* cmsg = CMSG_FIRSTHDR(&msg);
  cmsg->cmsg_level = SOL_SOCKET;
  cmsg->cmsg_type = SCM_RIGHTS;
  cmsg->cmsg_len = CMSG_LEN(sizeof(int));
  std::memcpy(CMSG_DATA(cmsg), &shm_fd, sizeof(int));
  msg.msg_controllen = cmsg->cmsg_len;
  return ::sendmsg(conn, &msg, VTX_SEND_FLAGS) ==
         static_cast<ssize_t>(sizeof(hello));
}

// ---------------------------------------------------------------- 编码线程池

// The job owns everything an encoder can access, including the moved capture
// storage. No per-request stack variables or RunState are shared with a worker.
struct EncodePayload {
  std::vector<uint8_t> bgra;
  std::vector<uint8_t> rgba;
  uint32_t width = 0;
  uint32_t height = 0;
  PngEncodeOptions png;
  std::string out_path;
};

struct EncodeResult {
  bool delivered = false;
  uint64_t t_alpha = 0;
  uint64_t t_png = 0;
  uint64_t t_disk = 0;
};

EncodeResult EncodeFrame(EncodePayload& payload) {
  EncodeResult result;
  const uint64_t t2 = NowUs();
  UnpremultiplyToRgba(payload.bgra.data(),
                      static_cast<size_t>(payload.width) * payload.height * 4u,
                      payload.rgba.data());
  result.t_alpha = NowUs() - t2;
  std::vector<uint8_t> png;
  const uint64_t t3 = NowUs();
  const bool enc = EncodePngRgba(payload.rgba.data(),
                                static_cast<int>(payload.width),
                                static_cast<int>(payload.height),
                                payload.png, &png);
  result.t_png = NowUs() - t3;
  const uint64_t t4 = NowUs();
  result.delivered =
      enc && AtomicWriteBytes(payload.out_path, png.data(), png.size());
  result.t_disk = NowUs() - t4;
  return result;
}

// ---------------------------------------------------------------- 帧生产

struct ProduceResult {
  uint16_t status = kStatusOk;
  uint32_t width = 0;
  uint32_t height = 0;
  uint32_t rejected_paints = 0;
  uint32_t document_gen = 0;
  int32_t canary_frame = -1;
  uint64_t t_ready_wait_us = 0;
  uint64_t t_capture_us = 0;
};

std::string ExpandUrlTemplate(const std::string& tpl, int32_t frame) {
  std::string url = tpl;
  const std::string tag = "{f}";
  const std::string val = std::to_string(frame);
  for (size_t pos = url.find(tag); pos != std::string::npos;
       pos = url.find(tag, pos + val.size())) {
    url.replace(pos, tag.size(), val);
  }
  return url;
}

// 导航 → 等加载落定 → external begin frame 驱动至金丝雀命中/尺寸不符。
// cap_dst / cap_expected_* 由调用方装配（模式 1=暂存区，模式 2=槽数据）。
ProduceResult ProduceFrame(RunState& state, PumpDriver& pump,
                           CefRefPtr<CefBrowser> browser,
                           const std::string& url_template, int32_t frame,
                           const ServeOptions& opt, uint32_t document_gen) {
  ProduceResult r;
  r.document_gen = document_gen;
  state.loaded.store(false, std::memory_order_relaxed);
  PrepareJsDocument(state, ExpandUrlTemplate(url_template, frame));
  browser->GetMainFrame()->LoadURL(ExpandUrlTemplate(url_template, frame));
  const uint64_t t0 = NowUs();
  if (!pump.Until([&] { return state.loaded.load(std::memory_order_relaxed); },
                  opt.frame_budget_ms)) {
    r.status = kStatusTimeout;
    return r;
  }
  if (!CheckJsCheckpoint(state, pump, browser, t0 + static_cast<uint64_t>(opt.frame_budget_ms) * 1000ull)) {
    r.status = state.js_error.failed ? kStatusJsErr : kStatusTimeout; return r;
  }
  pump.Until([] { return false; }, opt.ready_settle_ms);  // 字体/布局余量
  r.t_ready_wait_us = NowUs() - t0;

  {
    std::lock_guard<std::mutex> lock(state.mu);
    state.cap_captured = false;
    state.cap_size_mismatch = false;
    state.cap_actual_w = 0;
    state.cap_actual_h = 0;
    state.cap_rejected = 0;
    state.cap_last_capture_us = 0;
    state.cap_target_f = frame;
    state.capture_armed = true;
  }

  const uint64_t t1 = NowUs();
  const uint64_t budget_us =
      static_cast<uint64_t>(opt.frame_budget_ms) * 1000ull;
  while (NowUs() - t1 < budget_us) {
    browser->GetHost()->SendExternalBeginFrame();
    pump.Until(
        [&] {
          std::lock_guard<std::mutex> lock(state.mu);
          return state.cap_captured || state.cap_size_mismatch;
        },
        200);
    if (!CheckJsCheckpoint(state, pump, browser, t1 + budget_us)) {
      state.capture_armed = false; r.status = state.js_error.failed ? kStatusJsErr : kStatusTimeout; return r;
    }
    std::lock_guard<std::mutex> lock(state.mu);
    if (state.cap_size_mismatch) {
      state.capture_armed = false;
      r.status = kStatusSizeMismatch;
      r.width = state.cap_actual_w;
      r.height = state.cap_actual_h;
      r.rejected_paints = state.cap_rejected;
      return r;
    }
    if (state.cap_captured) {
      state.capture_armed = false;
      r.status = kStatusOk;
      r.width = state.cap_expected_w;
      r.height = state.cap_expected_h;
      r.rejected_paints = state.cap_rejected;
      r.canary_frame = frame;
      r.t_capture_us = state.cap_last_capture_us;
      return r;
    }
  }
  {
    std::lock_guard<std::mutex> lock(state.mu);
    state.capture_armed = false;
    r.status = kStatusTimeout;
    r.rejected_paints = state.cap_rejected;
  }
  return r;
}

// ---------------------------------------------------------------- #114 sync 帧生产

// #114 状态机驱动的帧生产结果（对 ProduceResult 的超集：canary 来自真实
// 解码、含早期丢弃/S3→S5 时长/恢复源等 JSONL 同口径字段）。
struct SyncProduceResult {
  uint16_t status = kStatusOk;
  uint32_t width = 0;
  uint32_t height = 0;
  uint32_t document_gen = 0;
  uint32_t request_id = 0;
  int32_t target_f = -1;
  int32_t canary_frame = -1;   // 解码结果（非请求值回显，#136 红队缺口闭合）
  uint32_t rejected_paints = 0;
  uint32_t early_paint_dropped = 0;
  uint64_t t_ready_wait_us = 0;               // S1→S3
  uint64_t accepted_elapsed_us_since_ready = 0;  // S3 就绪信号→S5 接受
  uint64_t t_capture_us = 0;
  bool restored = false;
  bool restore_from_reported = false;
};

// —— __VTX_READY__ json 最小解析（字段面固定：phase/gen/req/f/orig）——
namespace {

bool ParseJsonInt(const std::string& js, const char* key, int64_t* out) {
  const std::string tag = std::string("\"") + key + "\":";
  const size_t pos = js.find(tag);
  if (pos == std::string::npos) {
    return false;
  }
  size_t i = pos + tag.size();
  while (i < js.size() && js[i] == ' ') {
    ++i;
  }
  bool neg = false;
  if (i < js.size() && js[i] == '-') {
    neg = true;
    ++i;
  }
  if (i >= js.size() || js[i] < '0' || js[i] > '9') {
    return false;
  }
  int64_t v = 0;
  while (i < js.size() && js[i] >= '0' && js[i] <= '9') {
    v = v * 10 + (js[i] - '0');
    ++i;
  }
  *out = neg ? -v : v;
  return true;
}

}  // namespace

bool ParseSyncReadyJson(const std::string& js, fsync::PageEvent* ev,
                        bool baseline_only = false) {
  int64_t gen = 0, req = 0, f = 0;
  if (!ParseJsonInt(js, "gen", &gen) || !ParseJsonInt(js, "req", &req) ||
      !ParseJsonInt(js, "f", &f)) {
    return false;
  }
  ev->document_gen = static_cast<uint32_t>(gen);
  ev->request_id = static_cast<uint32_t>(req);
  ev->target_frame_index = static_cast<int32_t>(f);
  const size_t phase_pos = js.find("\"phase\":\"");
  if (phase_pos == std::string::npos) {
    return false;
  }
  const size_t phase_val = phase_pos + 9;
  const size_t phase_end = js.find('"', phase_val);
  if (phase_end == std::string::npos) {
    return false;
  }
  const std::string phase = js.substr(phase_val, phase_end - phase_val);
  // Baseline readiness is a separate channel, never a canary_set candidate.
  // In particular, a late baseline callback must not advance signed S2 -> S3.
  if (baseline_only ? phase != "baseline_ready"
                    : (phase != "assets_ready" && phase != "canary_set")) {
    return false;
  }
  ev->phase = phase == "assets_ready" ? fsync::PageEvent::Phase::kAssetsReady
                                      : fsync::PageEvent::Phase::kCanarySet;
  // orig：[[r,g,b,a],[r,g,b,a]] 或 null（无上报 → 邻域兜底显形）。
  ev->has_reported_originals = false;
  const size_t orig_pos = js.find("\"orig\":");
  if (orig_pos != std::string::npos) {
    size_t i = orig_pos + 7;
    while (i < js.size() && js[i] == ' ') {
      ++i;
    }
    if (i < js.size() && js[i] == '[') {
      uint8_t* dst = &ev->original_rgba[0][0];
      int got = 0;
      while (i < js.size() && got < 8) {
        if (js[i] >= '0' && js[i] <= '9') {
          int64_t v = 0;
          while (i < js.size() && js[i] >= '0' && js[i] <= '9') {
            v = v * 10 + (js[i] - '0');
            ++i;
          }
          if (v < 0 || v > 255) {
            return false;  // 越域：整事件拒收（宁拒不猜）
          }
          dst[got] = static_cast<uint8_t>(v);
          ++got;
        } else {
          ++i;
        }
      }
      ev->has_reported_originals = got == 8;
    }
  }
  return true;
}

// 就绪事件出队 → 状态机（UI 线程内完成解析与推进）。
void DrainSyncReadyQueue(RunState& state, fsync::FrameSyncStateMachine& m) {
  std::vector<std::string> queued;
  {
    std::lock_guard<std::mutex> lock(state.mu);
    queued.swap(state.sync_ready_queue);
  }
  for (const auto& js : queued) {
    fsync::PageEvent ev;
    if (ParseSyncReadyJson(js, &ev)) {
      m.NotifyPageEvent(ev, NowUs());
    }
  }
}

// —— #114 注入桥 JS：S2 就绪收集 → S3 帧状态施加 + rAF→rAF→MessageChannel
//    task → v2 双像素 Canary 绘制 → console.debug("__VTX_READY__:"+json)。
//    三元组（gen/req/f）随装配注入 —— 晚到/异世代回调在宿主侧被拒收。
std::string BuildSyncBridgeJs(uint32_t gen, uint32_t request_id, int32_t f,
                              bool mode_n, bool clean, bool exact,
                              const std::string& fault,
                              bool baseline_only = false) {
  char header[240];
  std::snprintf(header, sizeof(header),
                "(function(){\"use strict\";var GEN=%u,REQ=%u,F=%d,"
                "MODE_N=%s,CLEAN=%s,EXACT=%s,BASELINE=%s,FAULT=\"%s\";",
                gen, request_id, f, mode_n ? "true" : "false",
                clean ? "true" : "false", exact ? "true" : "false",
                 baseline_only ? "true" : "false", fault.c_str());
  // 注：FAULT 名单由 main.cpp 参数面锁定（none|stale_canary|no_canary|
  // no_ready|early_burst），此处字符串只来自受信配置面。
  return std::string(header) + R"VTXSYNC(
if (window.__VTX_SYNC_TAG === GEN + ":" + REQ) { return; }
window.__VTX_SYNC_TAG = GEN + ":" + REQ;
function send(o) {
  o.gen = GEN; o.req = REQ; o.f = F;
  try { console.debug("__VTX_READY__:" + JSON.stringify(o)); } catch (e) {}
}
if (FAULT === "no_ready") { return; }   // 故障注入：不发任何就绪信号
// —— S2：readyState complete + fonts.ready + 全部 <img> decode() ——
function imgsReady() {
  var ps = [];
  var ims = [];
  try { ims = document.images || []; } catch (e) {}
  for (var i = 0; i < ims.length; i++) {
    (function (im) {
      ps.push(im.decode ? im.decode().catch(function () {}) : 0);
    })(ims[i]);
  }
  return Promise.all(ps);
}
var fontsP = (document.fonts && document.fonts.ready)
    ? document.fonts.ready : { then: function (cb) { cb(); } };
var loadP = (document.readyState === "complete") ? { then: function (cb) { cb(); } }
    : new Promise(function (res) {
        if (document.readyState === "complete") { res(); return; }
        window.addEventListener("load", function () { res(); }, { once: true });
      });
// —— 帧状态施加：Mode P 走页面 __vtx_render(N)（S1 动作在常驻页的重放）；
//    Mode N 的帧状态已由 ?f= 导航期间的页面自身脚本施加。——
function applyState() {
  if (!MODE_N) {
    if (typeof window.__vtx_render !== "function") {
      return false;  // protocol violation, not a page JS exception
    }
    var result = window.__vtx_render(F);
    if (result && typeof result.then === "function") {
      return false;  // protocol violation, not a page JS exception
    }
  } else if (Number(new URLSearchParams(location.search).get("f")) !== F) {
    return false;  // wrong URL/frame must time out without manufacturing JS_ERR
  }
  return true;
}
// —— v2 双像素 Canary（vtx/frame_canary.py 冻结公式镜像）——
var KEY1 = CLEAN ? 0x5A : 0xA5, KEY2 = CLEAN ? 0x3C : 0xC3;
function cP1(fr) {
  var hi = (fr >> 8) & 255, lo = fr & 255;
  var rgb = hi + "," + lo + "," + ((hi ^ lo ^ KEY1) & 255);
  return CLEAN ? "rgba(" + rgb + ",0.996078431372549)" : "rgb(" + rgb + ")";
}
function cP2(fr) {
  var hi = (fr >> 8) & 255, lo = fr & 255;
  var b = (16 * lo + 22 * hi + KEY2) & 255;
  var rgb = lo + "," + hi + "," + b;
  return CLEAN ? "rgba(" + rgb + ",0.996078431372549)" : "rgb(" + rgb + ")";
}
function makeEl(pos, size, color) {
  // Isolate probes from generic page selectors (e.g. div{top:60px}).
  var d = document.createElement("vtx-frame-canary");
  d.setAttribute("data-vtx-sync-canary", "1");
  d.style.cssText = "all:initial;";
  var styles = {position:"fixed",display:"block",left:"auto",right:"auto",
    top:"auto",bottom:"auto",width:size+"px",height:size+"px",margin:"0",
    padding:"0",border:"0",outline:"0",opacity:"1",filter:"none",transform:"none",
    "box-shadow":"none","mix-blend-mode":"normal","box-sizing":"content-box",
    "z-index":"2147483647","pointer-events":"none",visibility:"visible",
    background:color};
  pos.split(";").forEach(function(s){
    var p=s.split(":"); if(p.length===2)styles[p[0]]=p[1];
  });
  Object.keys(styles).forEach(function(k){d.style.setProperty(k,styles[k],"important");});
  return d;
}
function clearCanary() {
  try {
    var ns = document.querySelectorAll("[data-vtx-sync-canary]");
    for (var i = 0; i < ns.length; i++) {
      if (ns[i].parentNode) { ns[i].parentNode.removeChild(ns[i]); }
    }
  } catch (e) {}
}
function drawCanary(fr) {
  if (FAULT === "no_canary") { return; }        // 故障注入：不画 Canary
  if (FAULT === "stale_canary") { fr = (fr - 1 + 65536) & 0xFFFF; }
  var root = document.documentElement;
  if (!root || !root.appendChild) { return; }
  clearCanary();
  if (CLEAN) {
    root.appendChild(makeEl("left:0;top:0;", 1, cP1(fr)));    // P1 @ (0,0)
    root.appendChild(makeEl("left:1px;top:0;", 1, cP2(fr)));  // P2 @ (1,0)
  } else {
    root.appendChild(makeEl("left:0;top:0;", 8, cP1(fr)));    // 左上 8x8
    root.appendChild(makeEl("right:0;bottom:0;", 8, cP2(fr))); // 右下 8x8
  }
}
// Only the native first-paint verifier may trigger relocation. A new token
// rejects queued old paints. Keep the same document, viewport and page frame.
window.__VTX_RELOCATE = function () {
  if (!EXACT || window.__VTX_SYNC_TAG !== GEN + ":" + REQ) { return; }
  clearCanary();
  var fr = F ^ 65535;
  if (FAULT === "stale_canary") { fr = (fr - 1 + 65536) & 65535; }
  if (FAULT === "no_canary") { return; }
  var root = document.documentElement;
  root.appendChild(makeEl("right:0;top:0;", 8, cP1(fr)));
  root.appendChild(makeEl("left:0;bottom:0;", 8, cP2(fr)));
};
function originals() {
  try {
    if (typeof window.__vtx_originals === "function") {
      return window.__vtx_originals();
    }
  } catch (e) {}
  return null;
}
var done = false;
Promise.all([fontsP, imgsReady(), loadP]).then(function () {
  if (!BASELINE) send({ phase: "assets_ready" }); // —— S2 就绪信号 ——
  if (!applyState()) return;  // never publish readiness for invalid protocol
  requestAnimationFrame(function () {          // 双 rAF（只作就绪候选）
    requestAnimationFrame(function () {
      var step = function () {                 // MessageChannel task（非微任务）
        if (done) { return; }
        done = true;
        if (BASELINE) {
          // This fresh document NEVER receives witness DOM (not even hidden).
          // The native capture is a rejection reference only, not frame proof.
          send({ phase: "baseline_ready" });
        } else {
          drawCanary(F);
          send({ phase: "canary_set", orig: originals() });
        }  // —— S3 通知 ——
      };
      var ch = null;
      try { ch = new MessageChannel(); } catch (e) {}
      if (ch) {
        ch.port1.onmessage = function () { step(); };
        ch.port2.postMessage(0);
      } else {
        setTimeout(step, 0);                   // 环境无 MessageChannel 的兜底
      }
    });
  });
});
})();)VTXSYNC";
}

// Capture an independent, never-instrumented document BEFORE the signed
// navigation. No baseline paint enters FeedPaint or supplies output bytes.
// Two equal fresh OnPaint buffers after readiness are required; this is only a
// conservative equality veto, NOT target-frame/generation proof.
static bool CaptureExactBaseline(RunState& state, PumpDriver& pump,
                                 CefRefPtr<CefBrowser> browser,
                                 fsync::FrameSyncStateMachine& machine,
                                 const ServeOptions& opt, uint32_t gen,
                                 uint32_t req, int32_t frame, bool mode_n) {
  {
    std::lock_guard<std::mutex> lock(state.mu);
    state.sync_capture_armed = false;
    state.sync_ready_queue.clear();
    state.buffer.clear();
  }
  state.loaded.store(false, std::memory_order_relaxed);
  const auto url = ExpandUrlTemplate(opt.url_template, frame);
  PrepareJsDocument(state, url);
  const uint64_t load_deadline = NowUs() + static_cast<uint64_t>(opt.frame_budget_ms) * 1000ull;
  browser->GetMainFrame()->LoadURL(url);
  if (!pump.Until([&] { return state.loaded.load(std::memory_order_relaxed); },
                  opt.frame_budget_ms)) {
    return false;
  }
  if (!CheckJsCheckpoint(state, pump, browser, load_deadline)) return false;
  browser->GetMainFrame()->ExecuteJavaScript(
      BuildSyncBridgeJs(gen, req, frame, mode_n, false, true, opt.sync_fault, true),
      "vtx_baseline_readiness", 0);
  const uint64_t deadline = NowUs() +
      static_cast<uint64_t>(opt.sync_ready_timeout_ms) * 1000ull;
  bool ready = false;
  uint64_t last_paint = 0;
  std::vector<uint8_t> previous;
  while (NowUs() < deadline) {
    if (state.js_error.failed) return false;
    browser->GetHost()->Invalidate(PET_VIEW);
    browser->GetHost()->SendExternalBeginFrame();
    pump.Once();
    bool equal = false;
    std::vector<uint8_t> candidate;
    int candidate_w = 0, candidate_h = 0;
    {
      std::lock_guard<std::mutex> lock(state.mu);
      for (const auto& js : state.sync_ready_queue) {
        fsync::PageEvent ev;
        if (ParseSyncReadyJson(js, &ev, true) && ev.document_gen == gen &&
            ev.request_id == req && ev.target_frame_index == frame) {
          ready = true;
          last_paint = state.paints.load(std::memory_order_relaxed);
          previous.clear();
        }
      }
      state.sync_ready_queue.clear();
      const uint64_t paints = state.paints.load(std::memory_order_relaxed);
      if (ready && paints > last_paint && !state.buffer.empty()) {
        last_paint = paints;
        if (previous == state.buffer) {
          equal = true;
          candidate = state.buffer;
          candidate_w = state.width; candidate_h = state.height;
        }
        previous = state.buffer;
      }
    }
    if (equal) {
      if (!CheckJsCheckpoint(state, pump, browser, deadline)) return false;
      return machine.SetExactBaseline(candidate.data(),
          static_cast<size_t>(candidate_w) * 4u, candidate_w, candidate_h, NowUs());
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  return false;
}

// Re-evaluate a veto that may have sampled the initial stable blank before
// rasterization. NEVER reuse the signed browser: even its late queued paints
// must not contaminate this reference. The fresh browser gets readiness JS
// only, no witness DOM, no page originals, and no output/corner reconstruction.
// The existing pump deadline bounds this one attempt (not a timeout extension).
static bool ResampleExactBaseline(RunState& signed_state, PumpDriver& pump,
    fsync::FrameSyncStateMachine& machine, const ServeOptions& opt,
    uint64_t deadline, uint8_t* dst) {
  auto state = std::make_shared<RunState>();
  CefRefPtr<WorkerClient> client = new WorkerClient(&opt.cef, state);
  CefWindowInfo window;
  window.SetAsWindowless(0);
  window.shared_texture_enabled = false;
  window.external_begin_frame_enabled = true;
  CefBrowserSettings settings;
  settings.background_color = 0;
  const auto request = machine.request();
  const std::string url = ExpandUrlTemplate(opt.url_template, request.target_frame_index);
  PrepareJsDocument(*state, url);
  CefRefPtr<CefBrowser> browser = CefBrowserHost::CreateBrowserSync(
      window, client, url, settings, nullptr, nullptr);
  if (!browser) return false;
  state->browser = nullptr;  // no client -> owned state -> browser reference cycle
  bool injected = false, ready = false;
  uint64_t last_paint = 0;
  fsync::PageEvent identity;
  bool accepted = false;
  while (NowUs() < deadline) {
    if (state->js_error.failed || signed_state.js_error.failed) break;
    if (!injected && state->loaded.load(std::memory_order_relaxed) &&
        browser->GetMainFrame()->GetURL().ToString() == url) {
      if (!CheckJsCheckpoint(*state, pump, browser, deadline)) break;
      browser->GetMainFrame()->ExecuteJavaScript(BuildSyncBridgeJs(
          request.document_gen, request.request_id, request.target_frame_index,
          request.drive == fsync::DriveMode::kModeN, false, true,
          opt.sync_fault, true), "vtx_baseline_resample_readiness", 0);
      injected = true;
    }
    browser->GetHost()->Invalidate(PET_VIEW);
    browser->GetHost()->SendExternalBeginFrame();
    pump.Once();
    std::vector<uint8_t> candidate;
    int candidate_w = 0, candidate_h = 0;
    {
      std::lock_guard<std::mutex> lock(state->mu);
      for (const auto& js : state->sync_ready_queue) {
        fsync::PageEvent ev;
        if (ParseSyncReadyJson(js, &ev, true) &&
            ev.document_gen == request.document_gen &&
            ev.request_id == request.request_id &&
            ev.target_frame_index == request.target_frame_index) {
          identity = ev;
          ready = true;
          last_paint = state->paints.load(std::memory_order_relaxed);
        }
      }
      state->sync_ready_queue.clear();
      const uint64_t paints = state->paints.load(std::memory_order_relaxed);
      if (ready && paints > last_paint && !state->buffer.empty()) {
        last_paint = paints;
        candidate = state->buffer;
        candidate_w = state->width; candidate_h = state->height;
      }
    }
    if (!candidate.empty()) {
      // Neither the signed document nor the fresh reference may pass S5 with
      // an unchecked error. Both fences stay inside the original pump budget.
      if (!CheckJsCheckpoint(*state, pump, browser, deadline) ||
          !CheckJsCheckpoint(signed_state, pump, signed_state.browser, deadline)) break;
      const auto verdict = machine.FeedExactBaselinePaint(identity,
          candidate.data(), static_cast<size_t>(candidate_w) * 4u,
          candidate_w, candidate_h, dst, NowUs());
      if (verdict.disposition == fsync::PaintDisposition::kAccepted) {
        signed_state.sync_last_verdict = verdict;
        signed_state.sync_captured = true;
        accepted = true;
        break;
      }
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  if (state->js_error.failed) {
    signed_state.js_error.failed = true;
    signed_state.last_error = state->last_error;
  }
  browser->GetHost()->CloseBrowser(true);
  // Cleanup time is not capture time and cannot turn a failed veto into success.
  const bool closed = pump.Until([&] { return state->closed.load(); }, 5000);
  return accepted && closed;
}

// Only this consumer may feed a signed OnPaint candidate into the verifier.
// Pumping the fence cannot overwrite these bytes or advance the machine to S5.
static bool ConsumeJsPaint(RunState& state, PumpDriver& pump,
    CefRefPtr<CefBrowser> browser, uint64_t deadline) {
  if (!state.js_paint_pending) return !state.js_error.failed;
  auto candidate = std::move(state.js_paint_candidate);
  const int w = state.js_paint_w, h = state.js_paint_h;
  state.js_paint_pending = false;
  if (!CheckJsCheckpoint(state, pump, browser, deadline)) return false;
  const auto verdict = state.sync_machine->FeedPaint(candidate.data(),
      static_cast<size_t>(w) * 4u, w, h, state.sync_dst, NowUs());
  if (verdict.disposition == fsync::PaintDisposition::kSizeMismatch) {
    state.sync_size_mismatch = true;
    state.sync_actual_w = w; state.sync_actual_h = h;
  } else if (verdict.disposition == fsync::PaintDisposition::kAccepted) {
    state.sync_last_verdict = verdict;
    state.sync_captured = true;
  }
  return true;
}

// 状态机驱动的帧生产：S0→S1（导航/注入）→ S2/S3（就绪信号，泵送推进
// rAF —— 期间 paint 由状态机早期丢弃）→ S4（Invalidate+SendExternal-
// BeginFrame+CefDoMessageLoopWork 泵送，1ms 间隔，硬超时 5000ms）→
// S5（真实 OnPaint 双像素验签 + 原地恢复）。dst = 接受帧紧凑拷贝目标。
SyncProduceResult ProduceFrameSync(RunState& state, PumpDriver& pump,
                                   CefRefPtr<CefBrowser> browser,
                                   fsync::FrameSyncStateMachine& machine,
                                   const ServeOptions& opt,
                                   uint32_t document_gen, uint32_t request_id,
                                   int32_t frame, uint32_t expected_w,
                                   uint32_t expected_h, uint8_t* dst) {
  SyncProduceResult r;
  r.document_gen = document_gen;
  r.request_id = request_id;
  r.target_f = frame;
  const bool mode_n = opt.sync_mode != "p";
  const bool clean = opt.sync_canary == "clean";
  const bool exact = opt.sync_canary == "exact";
  fsync::RequestDesc d;
  d.document_gen = document_gen;
  d.request_id = request_id;
  d.target_frame_index = frame;
  d.drive = mode_n ? fsync::DriveMode::kModeN : fsync::DriveMode::kModeP;
  d.canary = exact ? fsync::CanaryMode::kExact
                   : clean ? fsync::CanaryMode::kClean : fsync::CanaryMode::kDiagnostic;
  d.expected_w = expected_w;
  d.expected_h = expected_h;
  const uint64_t dup_before = machine.counters().duplicate_request_rejected;
  if (!machine.BeginRequest(d, NowUs())) {
    // 重复请求（request_id 重放）→ 显式 DUPLICATE 错误码（不静默重渲）；
    // 其余开局拒绝（参数越域等）→ JS_ERR。JSONL 已记录具体原因。
    const bool duplicate =
        machine.counters().duplicate_request_rejected > dup_before;
    r.status = duplicate ? kStatusDuplicate : kStatusJsErr;
    return r;
  }
  {
    std::lock_guard<std::mutex> lock(state.mu);
    state.sync_dst = dst;
    state.sync_captured = false;
    state.sync_size_mismatch = false;
    state.sync_actual_w = 0;
    state.sync_actual_h = 0;
    state.sync_last_verdict = fsync::PaintVerdict{};
    state.sync_capture_armed = true;
    state.js_paint_pending = false;
  }
  const auto disarm = [&]() {
    std::lock_guard<std::mutex> lock(state.mu);
    state.sync_capture_armed = false;
  };

  if (exact) {
    if (!CaptureExactBaseline(state, pump, browser, machine, opt,
                              document_gen, request_id, frame, mode_n)) {
      disarm();
      r.status = state.js_error.failed ? kStatusJsErr : kStatusTimeout;
      machine.FailRequest(state.js_error.failed ? "JS_ERR_baseline" : "uninstrumented_baseline_unavailable", NowUs());
      return r;
    }
    // The signed document must be NEW: no post-removal DOM, mutation side
    // effects or old witness nodes may contaminate the baseline reference.
    std::lock_guard<std::mutex> lock(state.mu);
    state.sync_capture_armed = true;
    state.sync_ready_queue.clear();
  }
  // Mode N navigates per frame. Exact additionally isolates its unsigned
  // reference from its signed document (Mode P re-applies its pure frame hook).
  const uint64_t load_deadline = NowUs() + static_cast<uint64_t>(opt.frame_budget_ms) * 1000ull;
  if (mode_n || exact) {
    state.loaded.store(false, std::memory_order_relaxed);
    PrepareJsDocument(state, ExpandUrlTemplate(opt.url_template, frame));
    browser->GetMainFrame()->LoadURL(
        ExpandUrlTemplate(opt.url_template, frame));
    if (!pump.Until([&] { return state.loaded.load(std::memory_order_relaxed); },
                    opt.frame_budget_ms)) {
      disarm();
      r.status = kStatusTimeout;
      machine.FailRequest("load_timeout", NowUs());
      return r;
    }
  }
  const auto js_fail = [&](const char* timeout_reason) {
    disarm();
    r.status = state.js_error.failed ? kStatusJsErr : kStatusTimeout;
    machine.FailRequest(state.js_error.failed ? "JS_ERR_observer_fence" :
        timeout_reason, NowUs());
    return r;
  };
  if (!CheckJsCheckpoint(state, pump, browser, load_deadline)) return js_fail("load_timeout");
  // 注入桥（Mode N：加载完成后注入新文档；Mode P：常驻页每请求重放）。
  browser->GetMainFrame()->ExecuteJavaScript(
      BuildSyncBridgeJs(document_gen, request_id, frame, mode_n, clean, exact,
                        opt.sync_fault),
      "vtx_sync_bridge", 0);

  // —— S1→S3：就绪信号等待。泵送 BeginFrame 让 rAF/MessageChannel 推进；
  //    期间 OnPaint 走状态机早期丢弃（early_paint_dropped 计数面）。——
  const uint64_t t0 = NowUs();
  if (opt.sync_fault == "early_burst") {
    // 对抗注入：S1 窗口内突发 8 个 BeginFrame，强制旧世代缓冲重放。
    for (int i = 0; i < 8; ++i) {
      browser->GetHost()->SendExternalBeginFrame();
      pump.Once();
    }
  }
  const uint64_t ready_deadline =
      t0 + static_cast<uint64_t>(opt.sync_ready_timeout_ms) * 1000ull;
  while (machine.state() != fsync::State::S3_JS_FRAME_APPLIED_AND_CANARY_SET &&
         NowUs() < ready_deadline) {
    browser->GetHost()->Invalidate(PET_VIEW);
    browser->GetHost()->SendExternalBeginFrame();
    pump.Once();
    DrainSyncReadyQueue(state, machine);
    if (!ConsumeJsPaint(state, pump, browser, ready_deadline)) return js_fail("ready_timeout");
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  DrainSyncReadyQueue(state, machine);
  if (machine.state() != fsync::State::S3_JS_FRAME_APPLIED_AND_CANARY_SET) {
    disarm();
    r.status = kStatusTimeout;
    machine.FailRequest("ready_timeout", NowUs());
    return r;
  }
  if (!CheckJsCheckpoint(state, pump, browser, ready_deadline)) return js_fail("ready_timeout");
  r.t_ready_wait_us = NowUs() - t0;

  // —— S4：外部帧泵（就绪信号只是候选；提交完成只能由 S5 验签确立）——
  machine.EnterPumping(NowUs());
  const uint64_t pump_deadline = NowUs() + static_cast<uint64_t>(
                                              opt.sync_pump_timeout_ms) *
                                              1000ull;
  bool size_mismatch = false;
  uint32_t actual_w = 0;
  uint32_t actual_h = 0;
  bool relocated = false;
  while (NowUs() < pump_deadline) {
    if (exact && machine.needs_baseline_resample()) {
      disarm();
      if (!ResampleExactBaseline(state, pump, machine, opt, pump_deadline, dst)) {
        machine.FailRequest("uninstrumented_baseline_resample_failed", NowUs());
      }
      break;  // at most ONE fresh-browser resample, inside the original budget
    }
    if (exact && machine.needs_relocation() && !relocated) {
      browser->GetMainFrame()->ExecuteJavaScript(
          "window.__VTX_RELOCATE();", "vtx_sync_relocate", 0);
      relocated = true;
    }
    browser->GetHost()->Invalidate(PET_VIEW);
    browser->GetHost()->SendExternalBeginFrame();
    pump.Once();
    DrainSyncReadyQueue(state, machine);
    if (!ConsumeJsPaint(state, pump, browser, pump_deadline)) return js_fail("pump_timeout");
    {
      std::lock_guard<std::mutex> lock(state.mu);
      if (state.sync_size_mismatch) {
        size_mismatch = true;
        actual_w = state.sync_actual_w;
        actual_h = state.sync_actual_h;
      }
    }
    if (size_mismatch) {
      break;
    }
    if (machine.accepted()) {
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  disarm();
  if (state.js_error.failed) return js_fail("pump_timeout");
  const fsync::FrameSyncStateMachine::RequestStats stats =
      machine.request_stats();
  r.early_paint_dropped = static_cast<uint32_t>(stats.early_paint_dropped);
  r.rejected_paints = static_cast<uint32_t>(stats.rejected_stale_paint);
  r.accepted_elapsed_us_since_ready = stats.accepted_elapsed_us_since_ready;
  if (size_mismatch) {
    r.status = kStatusSizeMismatch;
    r.width = actual_w;
    r.height = actual_h;
    machine.FailRequest("size_mismatch", NowUs());
    return r;
  }
  if (!machine.accepted()) {
    r.status = kStatusTimeout;
    machine.FailRequest("pump_timeout", NowUs());
    return r;
  }
  {
    std::lock_guard<std::mutex> lock(state.mu);
    r.width = expected_w;
    r.height = expected_h;
    r.canary_frame = state.sync_last_verdict.decoded_frame;  // 解码结果
    r.t_capture_us = state.sync_last_verdict.capture_us;
    r.restored = state.sync_last_verdict.restored;
    r.restore_from_reported = state.sync_last_verdict.restore_from_reported;
  }
  machine.Reset(NowUs(), "done");  // request_end(accepted) → S0
  return r;
}

void FillReceipt(FrameSlotHeader* h, const ProduceResult& r, uint64_t run_id,
                 uint32_t request_id, int32_t target_f,
                 uint64_t t_alpha = 0, uint64_t t_png = 0,
                 uint64_t t_disk = 0) {
  std::memset(h, 0, sizeof(*h));
  h->magic = kSlotMagic;
  h->protocol_version = kProtocolVersion;
  h->status_code = r.status;
  h->run_id = run_id;
  h->document_gen = r.document_gen;
  h->request_id = request_id;
  h->target_frame_index = target_f;
  h->canary_frame_index = r.canary_frame;
  h->width = r.width;
  h->height = r.height;
  h->stride = r.width * 4u;
  h->pixel_format = kPixBgraPremul;  // 回执描述 OnPaint 源面
  h->color_space = kColorSpaceSrgb;
  h->rejected_paints = static_cast<uint16_t>(r.rejected_paints);
  h->t_ready_wait_us = r.t_ready_wait_us;
  h->t_capture_raw_us = r.t_capture_us;
  h->t_alpha_norm_us = t_alpha;
  h->t_png_encode_us = t_png;
  h->t_disk_commit_us = t_disk;
}

// ---------------------------------------------------------------- RunServe

int RunServe(const ServeOptions& opt, int argc, char* argv[]) {
  ::signal(SIGPIPE, SIG_IGN);  // 宿主暴毙时 send 得错误而非被信号杀死

  // An inherited transport must already be a connected AF_UNIX stream. Close
  // it on exec before CEF launches helpers, so EOF still identifies the host.
  if (opt.ipc_fd >= 0) {
    struct sockaddr_storage peer{};
    socklen_t peer_len = sizeof(peer);
    int type = 0;
    socklen_t type_len = sizeof(type);
    if (::getpeername(opt.ipc_fd, reinterpret_cast<struct sockaddr*>(&peer), &peer_len) != 0 ||
        peer.ss_family != AF_UNIX ||
        ::getsockopt(opt.ipc_fd, SOL_SOCKET, SO_TYPE, &type, &type_len) != 0 ||
        type != SOCK_STREAM || ::fcntl(opt.ipc_fd, F_SETFD, FD_CLOEXEC) < 0) {
      std::fprintf(stderr, "vtx_cef_worker: --ipc-fd must be a connected Unix stream\n");
      return 2;
    }
  }

  // ---- 参数面校验 ----
  const bool sync_mode_p = opt.sync && opt.sync_mode == "p";
  // Mode N（逐帧导航）需要 {f} 占位；Mode P 的 url_template 是常驻页地址。
  if (!sync_mode_p && opt.url_template.find("{f}") == std::string::npos) {
    std::fprintf(stderr, "vtx_cef_worker: --url-template 缺 {f} 占位\n");
    return 2;
  }
  struct SocketDirFd {
    int fd = -1;
    ~SocketDirFd() { if (fd >= 0) ::close(fd); }
  } socket_dir;
  std::string socket_address = opt.ipc_socket;
  struct sockaddr_un sun_probe;
#if defined(__linux__)
  if (socket_address.size() + 1 > sizeof(sun_probe.sun_path)) {
    const std::filesystem::path p(socket_address);
    socket_dir.fd = ::open(p.parent_path().c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
    if (socket_dir.fd < 0) return 2;
    socket_address = "/proc/" + std::to_string(::getpid()) + "/fd/" +
                     std::to_string(socket_dir.fd) + "/" + p.filename().string();
  }
#endif
  if (opt.ipc_fd < 0 && (socket_address.empty() ||
      socket_address.size() + 1 > sizeof(sun_probe.sun_path))) {
    std::fprintf(stderr, "vtx_cef_worker: --ipc-socket 缺失或超长\n");
    return 2;
  }
  if (opt.cef.width <= 0 || opt.cef.height <= 0) {
    std::fprintf(stderr, "vtx_cef_worker: 视口尺寸非法\n");
    return 2;
  }

  // ---- root_cache_path（缺省自建临时目录，退出即删）----
  std::filesystem::path root_cache(opt.root_cache_path);
  const bool owns_cache = opt.root_cache_path.empty();
  const auto cleanup_owned = [&]() {
    if (owns_cache) {
      std::error_code ec;
      std::filesystem::remove_all(root_cache, ec);
    }
  };
  if (owns_cache) {
    std::error_code ec;
    root_cache = std::filesystem::temp_directory_path(ec) /
                 ("vtx_cef_worker-" + std::to_string(::getpid()));
    std::filesystem::create_directories(root_cache, ec);
    if (ec) {
      std::fprintf(stderr, "vtx_cef_worker: root_cache_path 不可创建\n");
      return 1;
    }
  }

  // ---- 监听 socket（路径归宿主目录；残留名先解绑）----
  int listen_fd = -1;
  if (opt.ipc_fd < 0) {
    listen_fd = ::socket(AF_UNIX, SOCK_STREAM, 0);
    if (listen_fd < 0) {
      std::fprintf(stderr, "vtx_cef_worker: socket() 失败: %s\n",
                   std::strerror(errno));
      return 1;
    }
    struct sockaddr_un addr;
    std::memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    std::strncpy(addr.sun_path, socket_address.c_str(),
                 sizeof(addr.sun_path) - 1);
    ::unlink(opt.ipc_socket.c_str());  // 同名残骸（崩溃遗留）先行清扫
    if (::bind(listen_fd, reinterpret_cast<struct sockaddr*>(&addr),
               sizeof(addr)) != 0 ||
        ::listen(listen_fd, 1) != 0) {
      std::fprintf(stderr, "vtx_cef_worker: bind/listen %s 失败: %s\n",
                   opt.ipc_socket.c_str(), std::strerror(errno));
      ::close(listen_fd);
      return 1;
    }

  }

  // ---- CEF 初始化 + 建窗 ----
  Options cef_opt = opt.cef;
  cef_opt.serve_mode = true;
  RunState state;
  CefMainArgs main_args(argc, argv);
  CefSettings settings = MakeWorkerSettings(root_cache.string());
  CefRefPtr<WorkerApp> app = new WorkerApp(&cef_opt, &state);
  if (!CefInitialize(main_args, settings, app, nullptr)) {
    std::fprintf(stderr, "vtx_cef_worker: CefInitialize 失败\n");
    ::close(listen_fd);
    ::unlink(opt.ipc_socket.c_str());
    cleanup_owned();
    return 1;
  }
  PumpDriver pump{&state};
  CefString initial_url;
  initial_url.FromString("about:blank");
  CefRefPtr<WorkerClient> client = new WorkerClient(&cef_opt, &state);
  CreateOsrBrowser(client, initial_url, cef_opt.width, cef_opt.height);
  if (!pump.Until(
          [&] {
            std::lock_guard<std::mutex> lock(state.mu);
            return state.browser != nullptr;
          },
          30000)) {
    std::fprintf(stderr, "vtx_cef_worker: 浏览器创建超时\n");
    CefShutdown();
    ::close(listen_fd);
    ::unlink(opt.ipc_socket.c_str());
    cleanup_owned();
    return 1;
  }
  CefRefPtr<CefBrowser> browser;
  {
    std::lock_guard<std::mutex> lock(state.mu);
    browser = state.browser;
  }

  // ---- #114 sync 装配：状态机 + JSONL 日志 + Mode P 常驻页首航 ----
  vtx_cef::fsync::FrameSyncStateMachine sync_machine;
  std::FILE* sync_log_fh = nullptr;
  if (opt.sync) {
    if (!opt.sync_log.empty()) {
      sync_log_fh = std::fopen(opt.sync_log.c_str(), "wb");
      if (sync_log_fh == nullptr) {
        std::fprintf(stderr, "vtx_cef_worker: --sync-log 打不开: %s\n",
                     opt.sync_log.c_str());
        browser->GetHost()->CloseBrowser(true);
        pump.Until([&] { return state.closed.load(); }, 5000);
        CefShutdown();
        ::close(listen_fd);
        ::unlink(opt.ipc_socket.c_str());
        cleanup_owned();
        return 1;
      }
      std::setvbuf(sync_log_fh, nullptr, _IONBF, 0);  // 崩溃窗口证据保真
    }
    std::FILE* fh = sync_log_fh;
    sync_machine.SetEventSink([fh](const std::string& line) {
      if (fh != nullptr) {
        std::fwrite(line.data(), 1, line.size(), fh);
      }
    });
    {
      std::lock_guard<std::mutex> lock(state.mu);
      state.sync_machine = &sync_machine;
    }
    if (sync_mode_p) {
      // 常驻页唯一一次导航（此后 document_gen 恒 1，请求以 request_id 区分）。
      state.loaded.store(false, std::memory_order_relaxed);
      CefString resident_url;
      resident_url.FromString(opt.url_template);
      PrepareJsDocument(state, opt.url_template);
      browser->GetMainFrame()->LoadURL(resident_url);
      if (!pump.Until(
              [&] { return state.loaded.load(std::memory_order_relaxed); },
              opt.frame_budget_ms)) {
        std::fprintf(stderr, "vtx_cef_worker: sync Mode P 常驻页加载超时\n");
        if (sync_log_fh != nullptr) {
          std::fclose(sync_log_fh);
        }
        browser->GetHost()->CloseBrowser(true);
        pump.Until([&] { return state.closed.load(); }, 5000);
        CefShutdown();
        ::close(listen_fd);
        ::unlink(opt.ipc_socket.c_str());
        cleanup_owned();
        return 1;
      }
    }
  }

  // ---- SHM 环（容量=期望视口；名字即建即解绑 → /dev/shm 零残留）----
  const uint64_t run_id = (static_cast<uint64_t>(::getpid()) << 32) ^
                          (NowUs() & 0xffffffffull);
  std::string ring_err;
  auto ring = ShmRingWriter::Create(
      static_cast<uint32_t>(cef_opt.width), static_cast<uint32_t>(cef_opt.height),
      run_id, &ring_err);
  if (ring == nullptr) {
    std::fprintf(stderr, "vtx_cef_worker: SHM 环创建失败: %s\n",
                 ring_err.c_str());
    browser->GetHost()->CloseBrowser(true);
    pump.Until([&] { return state.closed.load(); }, 5000);
    CefShutdown();
    ::close(listen_fd);
    ::unlink(opt.ipc_socket.c_str());
    cleanup_owned();
    return 1;
  }

  // ---- 就绪公告（宿主在此时点之后 connect）----
  std::printf("VTXCEF_SERVE_READY socket=%s pid=%ld run_id=%llu slots=%u\n",
              opt.ipc_socket.c_str(), static_cast<long>(::getpid()),
              static_cast<unsigned long long>(run_id),
              static_cast<unsigned>(kRingSlots));
  std::fflush(stdout);

  // ---- 接受单宿主连接 ----
  struct pollfd pf { listen_fd, POLLIN, 0 };
  int accept_rc = opt.ipc_fd >= 0 ? 1 : ::poll(&pf, 1, opt.idle_timeout_ms);
  int conn = opt.ipc_fd;
  if (opt.ipc_fd < 0 && accept_rc > 0) {
    conn = ::accept(listen_fd, nullptr, nullptr);
  }
  ::close(listen_fd);
  ::unlink(opt.ipc_socket.c_str());  // 监听名使命完成（单宿主契约）
  if (conn < 0) {
    std::fprintf(stderr, "vtx_cef_worker: 无宿主连接（accept %s）\n",
                 accept_rc == 0 ? "空闲超时" : "错误");
    browser->GetHost()->CloseBrowser(true);
    pump.Until([&] { return state.closed.load(); }, 5000);
    CefShutdown();
    cleanup_owned();
    return accept_rc == 0 ? 5 : 1;
  }

  {
    HelloInfo hello;
    std::memset(&hello, 0, sizeof(hello));
    hello.magic = kHelloMagic;
    hello.protocol_version = kProtocolVersion;
    hello.flags = 1;  // bit0：随消息附 SHM fd
    hello.run_id = run_id;
    hello.pid = static_cast<int32_t>(::getpid());
    hello.ring_slots = kRingSlots;
    hello.max_width = static_cast<uint32_t>(cef_opt.width);
    hello.max_height = static_cast<uint32_t>(cef_opt.height);
    hello.frame_bytes = ring->geometry().frame_bytes;
    if (!SendHelloWithFd(conn, hello, ring->fd())) {
      std::fprintf(stderr, "vtx_cef_worker: Hello/fd 交付失败\n");
      ::close(conn);
      browser->GetHost()->CloseBrowser(true);
      pump.Until([&] { return state.closed.load(); }, 5000);
      CefShutdown();
      cleanup_owned();
      return 4;
    }
  }

  // ---- 请求主循环 ----
  const size_t frame_bytes = ring->geometry().frame_bytes;
  std::vector<uint8_t> rgba(frame_bytes);
  SerialEncodeJob<EncodePayload, EncodeResult> encode_job(opt.encode_threads);
  uint32_t document_gen = 0;
  uint32_t ok_replies = 0;
  int exit_code = 0;

  for (;;) {
    FrameRequest req;
    std::memset(&req, 0, sizeof(req));
    const int rc = RecvAllOrShort(conn, &req, sizeof(req), opt.idle_timeout_ms);
    if (rc == 0) {
      break;  // 宿主 EOF：优雅收尾
    }
    if (rc == -1) {
      exit_code = 5;  // 空闲超时
      break;
    }
    if (rc < 0) {
      exit_code = 4;  // 请求中途断裂（宿主死亡/abort 演练形态）
      break;
    }
    if (req.magic != kRequestMagic || req.protocol_version != kProtocolVersion ||
        !RequestPathValid(req) || req.frame_index < 0 ||
        req.expected_width < 0 || req.expected_height < 0) {
      std::fprintf(stderr,
                   "vtx_cef_worker: 协议错误（magic=0x%x ver=%u path_ok=%d）\n",
                   req.magic, req.protocol_version,
                   static_cast<int>(RequestPathValid(req)));
      exit_code = 3;
      break;
    }
    const std::string out_path(req.out_png_path);
    const uint32_t expected_w =
        req.expected_width > 0 ? static_cast<uint32_t>(req.expected_width)
                               : static_cast<uint32_t>(cef_opt.width);
    const uint32_t expected_h =
        req.expected_height > 0 ? static_cast<uint32_t>(req.expected_height)
                                : static_cast<uint32_t>(cef_opt.height);
    // document_gen 语义：每次导航自增（Mode N 每请求一导航；Mode P 常驻页
    // 恒 1，请求以 request_id 区分 —— 状态机三元组仍唯一）。
    if (!sync_mode_p) {
      ++document_gen;
    } else if (document_gen == 0) {
      document_gen = 1;
    }

    if ((req.flags & kFlagWantSlot) == 0) {
      // ================= 模式 1：Worker 原地落盘（零 IPC 像素传输）=====
      {
        std::lock_guard<std::mutex> lock(state.mu);
        if (state.cap_staging.size() <
            static_cast<size_t>(expected_w) * expected_h * 4u) {
          state.cap_staging.resize(
              static_cast<size_t>(expected_w) * expected_h * 4u);
        }
        state.cap_expected_w = expected_w;
        state.cap_expected_h = expected_h;
        state.cap_dst = state.cap_staging.data();
      }
      ProduceResult r;
      if (opt.sync) {
        // #114 状态机路径：canary/拒收计数/时序取自机器（解码结果口径）。
        SyncProduceResult sr = ProduceFrameSync(
            state, pump, browser, sync_machine, opt, document_gen,
            req.request_id, req.frame_index, expected_w, expected_h,
            state.cap_staging.data());
        r.status = sr.status;
        r.width = sr.width;
        r.height = sr.height;
        r.document_gen = sr.document_gen;
        r.canary_frame = sr.canary_frame;
        r.rejected_paints = sr.rejected_paints;
        r.t_ready_wait_us = sr.t_ready_wait_us;
        r.t_capture_us = sr.t_capture_us;
      } else {
        r = ProduceFrame(state, pump, browser, opt.url_template,
                         req.frame_index, opt, document_gen);
      }
      uint64_t t_alpha = 0, t_png = 0, t_disk = 0;
      bool encode_terminal = false;
      if (r.status == kStatusOk) {
        auto payload = std::make_unique<EncodePayload>();
        payload->width = r.width;
        payload->height = r.height;
        payload->png = opt.png;
        payload->out_path = out_path;
        payload->rgba = std::move(rgba);
        {
          std::lock_guard<std::mutex> lock(state.mu);
          state.capture_armed = false;
          state.sync_capture_armed = false;
          state.cap_dst = nullptr;
          payload->bgra = std::move(state.cap_staging);
        }
        const bool started = encode_job.Start(std::move(payload), EncodeFrame);
        const bool completed =
            started && pump.Until([&] { return encode_job.Done(); }, 60000);
        if (!encode_job.FinishWait(completed)) {
          // Late success must never reverse a deadline. Retain the job's buffers
          // until drain/host termination; never receive another request here.
          r.status = kStatusTimeout;
          encode_terminal = true;
          std::fprintf(stderr, "vtx_cef_worker: PNG 编码超时，关闭连接 %s\n",
                       out_path.c_str());
        } else {
          auto done = encode_job.TakeCompleted();
          if (done) {
            rgba = std::move(done->payload->rgba);
            {
              std::lock_guard<std::mutex> lock(state.mu);
              state.cap_staging = std::move(done->payload->bgra);
            }
          }
          if (!done || done->error || !done->result ||
              !done->result->delivered) {
            r.status = kStatusJsErr;  // 编码/落盘/异常均不可作为 OK 交付。
            std::fprintf(stderr, "vtx_cef_worker: PNG 编码/落盘失败 %s\n",
                         out_path.c_str());
          }
          if (done && done->result) {
            t_alpha = done->result->t_alpha;
            t_png = done->result->t_png;
            t_disk = done->result->t_disk;
          }
        }
      }
      FrameSlotHeader receipt;
      FillReceipt(&receipt, r, run_id, req.request_id, req.frame_index,
                  t_alpha, t_png, t_disk);
      if (!SendAll(conn, &receipt, sizeof(receipt))) {
        exit_code = 4;
        break;
      }
      if (encode_terminal) {
        exit_code = 4;
        break;
      }
      if (r.status == kStatusOk) {
        ++ok_replies;
      }
    } else {
      // ================= 模式 2：SHM 环跨进程零拷贝 ====================
      // 工单背压语义：4 槽全满时不发起下一帧 BeginFrame（produce 未启
      // 动），分片等空槽；等待计时随通知回传并累计入环控制块。
      int slot = -1;
      uint64_t bp_wait = 0;
      const uint64_t deadline =
          NowUs() + static_cast<uint64_t>(opt.slot_wait_timeout_ms) * 1000ull;
      for (;;) {
        uint64_t slice = 0;
        slot = ring->ClaimSlot(50'000, &slice);
        bp_wait += slice;
        if (slot >= 0) {
          break;
        }
        pump.Once();  // 背压期间保 CEF 内部任务
        if (NowUs() >= deadline) {
          break;
        }
      }
      ProduceResult r;
      r.document_gen = document_gen;
      if (slot < 0) {
        r.status = kStatusTimeout;  // 背压超时：明确错误，不死等
      } else if (opt.sync) {
        SyncProduceResult sr = ProduceFrameSync(
            state, pump, browser, sync_machine, opt, document_gen,
            req.request_id, req.frame_index, expected_w, expected_h,
            ring->SlotData(static_cast<uint32_t>(slot)));
        r.status = sr.status;
        r.width = sr.width;
        r.height = sr.height;
        r.canary_frame = sr.canary_frame;
        r.rejected_paints = sr.rejected_paints;
        r.t_ready_wait_us = sr.t_ready_wait_us;
        r.t_capture_us = sr.t_capture_us;
      } else {
        {
          std::lock_guard<std::mutex> lock(state.mu);
          state.cap_expected_w = expected_w;
          state.cap_expected_h = expected_h;
          state.cap_dst = ring->SlotData(static_cast<uint32_t>(slot));
        }
        r = ProduceFrame(state, pump, browser, opt.url_template,
                         req.frame_index, opt, document_gen);
      }
      SlotNotice notice;
      std::memset(&notice, 0, sizeof(notice));
      notice.magic = kNoticeMagic;
      notice.protocol_version = kProtocolVersion;
      notice.request_id = req.request_id;
      notice.frame_index = req.frame_index;
      notice.backpressure_wait_us = bp_wait;
      if (r.status != kStatusOk) {
        if (slot >= 0) {
          ring->AbandonSlot(static_cast<uint32_t>(slot));
        }
        notice.status_code = r.status;
        notice.slot_index = 0xFF;
      } else {
        const auto idx = static_cast<uint32_t>(slot);
        // 槽头 = 工单冻结结构（BGRA 预乘原始面直存；计时计 capture 段）。
        FillReceipt(ring->SlotHeader(idx), r, run_id, req.request_id,
                    req.frame_index);
        ring->PublishSlot(idx);
        notice.status_code = kStatusOk;
        notice.slot_index = static_cast<uint8_t>(idx);
        notice.seq =
            ring->control()->gates[idx].seq.load(std::memory_order_relaxed);
        if ((req.flags & kFlagDumpRaw) != 0) {
          // 诊断直写面：同一 OnPaint 捕获字节经文件 I/O 落地，供端到端
          // 逐字节对照（SHM 槽读出 == 直写路径）。
          AtomicWriteBytes(out_path + ".raw", ring->SlotData(idx),
                           static_cast<size_t>(r.width) * r.height * 4u);
        }
        ++ok_replies;
      }
      if (!SendAll(conn, &notice, sizeof(notice))) {
        exit_code = 4;
        break;
      }
    }

    // DoD③ 故障注入：第 N 帧成功交付后 abort()（模拟 Worker 中途暴毙）。
    if (opt.fault_abort_after_frames >= 0 &&
        ok_replies == static_cast<uint32_t>(opt.fault_abort_after_frames)) {
      std::fflush(stdout);
      std::fflush(stderr);
      std::abort();
    }
  }

  // ---- 收尾：关连接 → 关浏览器 → CefShutdown → 清理 ----
  ::shutdown(conn, SHUT_RDWR);
  ::close(conn);
  // A codec/write call cannot be safely killed as a C++ thread. Stop accepting
  // work and join with its owned buffers/scratch intact. If it never returns,
  // the existing host supervisor terminates the process; no timeout OK is sent.
  encode_job.StopAndDrain();
  {
    std::lock_guard<std::mutex> lock(state.mu);
    state.sync_machine = nullptr;  // 先摘除再析构（防御晚回调）
  }
  browser->GetHost()->CloseBrowser(true);
  pump.Until([&] { return state.closed.load(); }, 5000);
  CefShutdown();
  if (sync_log_fh != nullptr) {
    std::fclose(sync_log_fh);  // JSONL 全事件日志（无缓冲，崩溃窗口亦保真）
  }
  if (owns_cache) {
    std::error_code ec;
    std::filesystem::remove_all(root_cache, ec);
  }
  return exit_code;
}

}  // namespace vtx_cef
