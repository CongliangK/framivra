// serve.cpp — 帧服务模式实现（issue #110 CEF-02a）。
//
// 线程模型：
//   - 主线程：CEF UI（外置泵）+ 请求循环（导航/捕获/槽发布/回执）——
//     OnPaint 与请求处理同线程，捕获装配无跨线程竞态；
//   - 编码线程池（MiniPool）：模式 1 的逆预乘 + PNG 编码 + 原子落盘
//     （工单「本地线程池完成」；等待期间主线程持续泵 CEF）；
//   - --parent-alive-fd 守卫线程：main() 装配（#109 契约，本文件不重置）。
//
// 退出码：0 = 宿主 EOF 优雅收尾；1 = 致命初始化失败；3 = 协议错误
// （magic/版本/路径非法）；4 = 请求中途连接断裂；5 = 空闲超时。
#include "serve.h"

#include <fcntl.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <array>
#include <atomic>
#include <limits>
#include <cerrno>
#include <condition_variable>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <functional>
#include <thread>
#include <vector>

#include "frame_pipeline.h"
#include "ipc_protocol.hpp"
#include "png_writer.h"
#include "shm_ring.hpp"

#include "include/cef_app.h"
#include "include/cef_browser.h"
#include "include/cef_request_context.h"

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

// 最小线程池：Post 提交单任务（顺序契约：上次任务须已完成），主线程以
// pump 等待 Done —— 重活不占 CEF UI 线程，等待期浏览器任务仍被驱动。
class MiniPool {
 public:
  explicit MiniPool(int n, std::function<void()> wake = {})
      : wake_(std::move(wake)) {
    if (n < 1) {
      n = 1;
    }
    for (int i = 0; i < n; ++i) {
      workers_.emplace_back([this] { Loop(); });
    }
  }
  ~MiniPool() {
    {
      std::lock_guard<std::mutex> lock(mu_);
      stop_ = true;
      work_cv_.notify_all();
    }
    for (auto& t : workers_) {
      t.join();
    }
  }
  void Post(std::function<void()> task) {
    std::lock_guard<std::mutex> lock(mu_);
    task_ = std::move(task);
    done_.store(false, std::memory_order_release);
    work_cv_.notify_one();
  }
  bool Done() const { return done_.load(std::memory_order_acquire); }

 private:
  void Loop() {
    for (;;) {
      std::unique_lock<std::mutex> lock(mu_);
      work_cv_.wait(lock, [this] { return stop_ || task_ != nullptr; });
      if (stop_ && task_ == nullptr) {
        return;
      }
      auto task = std::move(task_);
      task_ = nullptr;
      lock.unlock();
      if (task) {
        task();
      }
      done_.store(true, std::memory_order_release);
      if (wake_) wake_();
    }
  }
  std::vector<std::thread> workers_;
  mutable std::mutex mu_;
  std::condition_variable work_cv_;
  std::function<void()> task_;
  std::atomic<bool> done_{true};
  bool stop_ = false;
  std::function<void()> wake_;
};

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
  if (state.closed.load()) { r.status = kStatusTimeout; return r; }
  state.loaded.store(false, std::memory_order_relaxed);
  PrepareJsDocument(state, ExpandUrlTemplate(url_template, frame));
  browser->GetMainFrame()->LoadURL(ExpandUrlTemplate(url_template, frame));
  const uint64_t t0 = NowUs();
  if (!pump.Until([&] { return state.closed.load() ||
                         state.loaded.load(std::memory_order_relaxed); },
                  opt.frame_budget_ms) || state.closed.load()) {
    r.status = kStatusTimeout;
    return r;
  }
  if (!CheckJsCheckpoint(state, pump, browser, t0 + static_cast<uint64_t>(opt.frame_budget_ms) * 1000ull)) {
    r.status = state.js_error.failed ? kStatusJsErr : kStatusTimeout; return r;
  }
  pump.Until([&] { return state.closed.load(); }, opt.ready_settle_ms);  // 字体/布局余量
  if (state.closed.load()) { r.status = kStatusTimeout; return r; }
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
          return state.closed.load() || state.cap_captured || state.cap_size_mismatch;
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
  uint64_t v = 0;
  const uint64_t limit = static_cast<uint64_t>(std::numeric_limits<int64_t>::max()) +
      (neg ? 1u : 0u);
  while (i < js.size() && js[i] >= '0' && js[i] <= '9') {
    const auto digit = static_cast<uint64_t>(js[i] - '0');
    if (v > (limit - digit) / 10u) return false;
    v = v * 10u + digit;
    ++i;
  }
  if (i < js.size() && js[i] != ',' && js[i] != '}' && js[i] != ' ' &&
      js[i] != '\t' && js[i] != '\n' && js[i] != '\r') return false;
  if (neg && v == static_cast<uint64_t>(std::numeric_limits<int64_t>::max()) + 1u)
    *out = std::numeric_limits<int64_t>::min();
  else
    *out = neg ? -static_cast<int64_t>(v) : static_cast<int64_t>(v);
  return true;
}

}  // namespace

bool ParseSyncReadyJson(const std::string& js, fsync::PageEvent* ev,
                        bool baseline_only = false) {
  int64_t gen = 0, req = 0, f = 0;
  if (!ParseJsonInt(js, "gen", &gen) || !ParseJsonInt(js, "req", &req) ||
      !ParseJsonInt(js, "f", &f) || gen < 0 || req < 0 || f < 0 ||
      gen > std::numeric_limits<uint32_t>::max() ||
      req > std::numeric_limits<uint32_t>::max() ||
      f > std::numeric_limits<int32_t>::max()) {
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
            if (v > 255) return false;
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
  char header[320];
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
function active() { return window.__VTX_SYNC_TAG === GEN + ":" + REQ; }
function send(o) {
  if (!active()) return;
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
var loadP = (document.readyState === "complete") ? { then: function (cb) { cb(); } }
    : new Promise(function (res) {
        if (document.readyState === "complete") { res(); return; }
        window.addEventListener("load", function () { res(); }, { once: true });
      });
// —— 帧状态施加：Mode P 走页面 __vtx_render(N)（S1 动作在常驻页的重放）；
//    Mode N 的帧状态已由 ?f= 导航期间的页面自身脚本施加。——
function applyState() {
  if (!active()) return false;
  if (!MODE_N) {
    if (typeof window.__vtx_render !== "function") {
      return false;  // protocol violation, not a page JS exception
    }
    var result = window.__vtx_render(F);
    if (result && typeof result.then === "function") {
      // Only production exact P has the bounded async full-frame contract.
      // Rejection remains unhandled here so the native observer reports JS_ERR;
      // a never-fulfilled hook exhausts the ORIGINAL native ready deadline.
      if (!EXACT) return false;
      return Promise.resolve(result).then(function () { return active(); });
    }
  } else if (!new URLSearchParams(location.search).has("f") ||
             Number(new URLSearchParams(location.search).get("f")) !== F) {
    return false;  // wrong URL/frame must time out without manufacturing JS_ERR
  }
  return true;
}
// —— v2 双像素 Canary（vtx/render/frame_canary.py 冻结公式镜像）——
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
// Exact preserves the literal decoded frame AND all 32 request bits.
// Hard stops at integer row boundaries keep the original 8x8 footprint; native
// code validates every BGRA byte in all three bands of BOTH signed probes.
function probeColor(which, fr, relocated) {
  var color = which === 1 ? cP1 : cP2;
  if (!EXACT) return color(fr);
  var low = REQ & 65535, high = (REQ >>> 16) & 65535;
  if (relocated) { low ^= 65535; high ^= 65535; }
  return "linear-gradient(to bottom," + color(fr) + " 0px," + color(fr) +
    " 4px," + color(low) + " 4px," + color(low) + " 6px," +
    color(high) + " 6px," + color(high) + " 8px)";
}
function drawCanary(fr) {
  if (!active() || BASELINE) return;
  if (FAULT === "no_canary") { return; }        // 故障注入：不画 Canary
  if (FAULT === "stale_canary") { fr = (fr - 1 + 65536) & 0xFFFF; }
  var root = document.documentElement;
  if (!root || !root.appendChild) { return; }
  clearCanary();
  if (CLEAN) {
    root.appendChild(makeEl("left:0;top:0;", 1, cP1(fr)));    // P1 @ (0,0)
    root.appendChild(makeEl("left:1px;top:0;", 1, cP2(fr)));  // P2 @ (1,0)
  } else {
    root.appendChild(makeEl("left:0;top:0;", 8, probeColor(1, fr, false)));    // 左上 8x8
    root.appendChild(makeEl("right:0;bottom:0;", 8, probeColor(2, fr, false))); // 右下 8x8
  }
}
// Only the native first-paint verifier may trigger relocation. A new token
// rejects queued old paints. Keep the same document, viewport and page frame.
if (!BASELINE) window.__VTX_RELOCATE = function () {
  if (!EXACT || !active()) { return; }
  clearCanary();
  var fr = F ^ 65535;
  if (FAULT === "stale_canary") { fr = (fr - 1 + 65536) & 65535; }
  if (FAULT === "no_canary") { return; }
  var root = document.documentElement;
  root.appendChild(makeEl("right:0;top:0;", 8, probeColor(1, fr, true)));
  root.appendChild(makeEl("left:0;bottom:0;", 8, probeColor(2, fr, true)));
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
Promise.resolve(loadP).then(function () {
  if (!active()) return;
  // Mode P must remove the old probes BEFORE applying the next complete frame.
  // The reference browser never creates, removes, or hides witness DOM.
  if (!BASELINE) clearCanary();
  return Promise.resolve(applyState()).then(function (applied) {
  if (!applied || !active()) return; // invalid/obsolete hooks cannot publish
  // Applying a frame may replace images or introduce fonts. Resolve these
  // promises AFTER applyState, not from the preceding resident frame.
  if (document.documentElement) document.documentElement.getBoundingClientRect();
  var fontsP = (document.fonts && document.fonts.ready)
      ? document.fonts.ready : Promise.resolve();
  return Promise.all([fontsP, imgsReady()]).then(function () {
  if (!active()) return;
  if (!BASELINE) send({ phase: "assets_ready" }); // —— S2 就绪信号 ——
  function scheduleReadyTask() {
      var step = function () {                 // MessageChannel task（非微任务）
        if (!active()) return;
        if (done) { return; }
        done = true;
        if (BASELINE) {
          // This independent document NEVER receives witness DOM (even hidden).
          // The native capture is a rejection reference only, not frame proof.
          send({ phase: "baseline_ready" });
        } else {
          drawCanary(F);
          // Exact restores only witnessed native pixels, so it must not call
          // an unnecessary page originals hook (which could itself mutate DOM).
          send({ phase: "canary_set", orig: EXACT ? null : originals() });
        }  // —— S3 通知 ——
      };
      var ch = null;
      try { ch = new MessageChannel(); } catch (e) {}
      if (ch) {
        ch.port1.onmessage = function () {
          if (typeof ch.port1.close === "function") ch.port1.close();
          if (typeof ch.port2.close === "function") ch.port2.close();
          step();
        };
        ch.port2.postMessage(0);
      } else {
        setTimeout(step, 0);                   // 环境无 MessageChannel 的兜底
      }
  }
  // Exact P's full-state hook has completed, including awaited work. Layout,
  // fonts and images are settled above; the native paired paint proof, NOT
  // arbitrary rAF delay, binds the frame. Hooks that merely schedule later
  // rendering without awaiting it are outside this explicit resident contract.
  if (EXACT && !MODE_N) scheduleReadyTask();
  else requestAnimationFrame(function () { requestAnimationFrame(scheduleReadyTask); });
  });
  });
});
})();)VTXSYNC";
}

// One dedicated unsigned OSR browser is retained for the worker lifetime. In
// Mode N it navigates freshly for EVERY request, concurrently with the signed
// browser. Mode P is an explicit full-frame-hook contract: both documents stay
// resident, but only the signed document can ever receive witness DOM.
//
// Baseline samples are collected while signed navigation/readiness/two-phase
// proof are in flight. They never enter FeedPaint and never supply output bytes.
// S5 still requires TWO distinct post-readiness native paints that match the
// frozen signed reconstruction byte-for-byte, including all four probe corners.
// The sole exception is rasterization jitter tolerance: frozen cross-renderer
// divergence (>=kJitterStreakTarget identical signatures) within the
// kJitterMaxPixels/kJitterMaxChannelDelta budgets and exact probe corners
// accepts the signed candidate with a via=rasterization_jitter_tolerance
// audit event; anything beyond those budgets still fails closed.
class ExactBaselineCapture {
 public:
  bool Begin(const ServeOptions& opt, uint32_t gen, uint32_t req,
             int32_t frame, uint64_t load_deadline, uint64_t request_begin_us) {
    options_ = &opt;
    mode_n_ = opt.sync_mode != "p";
    if (!mode_n_ && resident_poisoned_)
      return Fail("resident_request_previously_failed");
    identity_ = fsync::PageEvent{};
    identity_.document_gen = gen;
    identity_.request_id = req;
    identity_.target_frame_index = frame;
    injected_ = false;
    ready_ = false;
    failed_ = false;
    failure_reason_ = "baseline_timeout";
    started_us_ = request_begin_us;
    loaded_us_ = ready_us_ = samples_us_ = veto_us_ = signed_proof_us_ = 0;
    load_deadline_ = load_deadline;
    ready_deadline_ = 0;
    samples_count_ = 0;
    // Release logical samples but preserve their allocation for the next frame.
    for (auto& sample : samples_) sample.sequence = 0;
    const bool first_browser = !state_;
    if (!first_browser && state_->closed.load())
      return Fail("unsigned_browser_closed");
    if (!first_browser && !browser_ && !AdoptCreatedBrowser())
      return Fail("unsigned_browser_creation_still_pending");
    if (first_browser) state_ = std::make_shared<RunState>();
    {
      std::lock_guard<std::mutex> lock(state_->mu);
      state_->sync_ready_queue.clear();
      last_paint_ = SetPassiveSnapshotDemandLocked(*state_, false);
    }
    checkpoint_floor_ = state_->js_error.sent;
    paint_floor_ = static_cast<uint64_t>(state_->paints.load());
    sample_count_ = 0;
    copied_paints_floor_ = state_->passive_snapshot_copied_paints;
    copied_bytes_floor_ = state_->passive_snapshot_copied_bytes;
    dropped_paints_floor_ = state_->passive_snapshot_dropped_paints;
    const auto url = ExpandUrlTemplate(opt.url_template, frame);
    navigated_ = mode_n_ || first_browser;
    if (navigated_) {
      state_->loaded.store(false, std::memory_order_relaxed);
      PrepareJsDocument(*state_, url);
      load_floor_ = state_->load_starts.load();
    } else if (state_->js_error.expected_url != url) {
      return Fail("resident_unsigned_url_changed");
    }
    if (first_browser) {
      if (opt.reference_context == "isolated") {
        // Empty cache_path is an independent in-memory/incognito context. Do
        // not copy shared storage, alter sandbox/GPU flags, or silently fall
        // back if initialization fails. Pure pages must render independently.
        CefRequestContextSettings context_settings;
        request_context_ = CefRequestContext::CreateContext(context_settings, nullptr);
        if (!request_context_) return Fail("unsigned_request_context_create_failed");
      }
      CefRefPtr<WorkerClient> client = new WorkerClient(&opt.cef, state_);
      CefWindowInfo window;
      window.SetAsWindowless(0);
      window.shared_texture_enabled = false;
      window.external_begin_frame_enabled = true;
      CefBrowserSettings settings;
      settings.background_color = 0;
      settings.windowless_frame_rate = opt.cef.capture_fps;
      // A new request context initializes asynchronously. CreateBrowserSync
      // cannot safely stand in for that initialization. Issue creation with the
      // actual target URL, then adopt OnAfterCreated inside the SAME existing
      // load deadline while the signed navigation progresses independently.
      creating_ = CefBrowserHost::CreateBrowser(window, client, url, settings,
                                               nullptr, request_context_);
      if (!creating_) return Fail("unsigned_browser_create_failed");
    } else if (navigated_) {
      browser_->GetMainFrame()->LoadURL(url);
    }
    return true;
  }

  // Nonblocking except for authenticated renderer fences. Called during BOTH
  // signed load/readiness and signed pumping, so unsigned work is not a serial
  // setup stage. A frame request is issued only while more evidence is needed.
  bool Advance(PumpDriver& pump, fsync::FrameSyncStateMachine& machine,
               uint64_t deadline) {
    if (failed_) return false;
    if (state_->closed.load()) return Fail("unsigned_browser_closed");
    if (!browser_ && !AdoptCreatedBrowser()) {
      if (NowUs() >= load_deadline_) return Fail("unsigned_browser_create_timeout");
      return true;
    }
    if (state_->js_error.failed) return Fail("JS_ERR_unsigned_observer");
    if (!injected_) {
      if (NowUs() >= load_deadline_) return Fail("unsigned_load_timeout");
      if ((!navigated_ || state_->load_starts.load() > load_floor_) &&
          state_->loaded.load(std::memory_order_relaxed) &&
          !browser_->IsLoading() &&
          browser_->GetMainFrame()->GetURL().ToString() ==
              state_->js_error.expected_url) {
        loaded_us_ = NowUs();
        if (!CheckJsCheckpoint(*state_, pump, browser_,
                               std::min(deadline, load_deadline_))) {
          return Fail(state_->js_error.failed ? "JS_ERR_unsigned_load" :
                                               "unsigned_load_fence_timeout");
        }
        browser_->GetMainFrame()->ExecuteJavaScript(BuildSyncBridgeJs(
            identity_.document_gen, identity_.request_id,
            identity_.target_frame_index, mode_n_, false, true,
            options_->sync_fault, true), "vtx_unsigned_readiness", 0);
        injected_ = true;
        ready_deadline_ = NowUs() +
            static_cast<uint64_t>(options_->sync_ready_timeout_ms) * 1000ull;
      }
    }
    {
      std::lock_guard<std::mutex> lock(state_->mu);
      for (const auto& js : state_->sync_ready_queue) {
        fsync::PageEvent ev;
        if (!ready_ && injected_ && ParseSyncReadyJson(js, &ev, true) &&
            ev.document_gen == identity_.document_gen &&
            ev.request_id == identity_.request_id &&
            ev.target_frame_index == identity_.target_frame_index) {
          ready_ = true;
          identity_ = ev;
          // Anything already observed at delivery of readiness is too early.
          // Distinct native paint sequence numbers, not repeated buffer reads,
          // establish the two samples used by the mandatory equality veto.
          last_paint_ = SetPassiveSnapshotDemandLocked(*state_, true);
          ready_us_ = NowUs();
          if (!machine.ArmExactBaseline(identity_, last_paint_, ready_us_))
            return Fail("unsigned_ready_identity_rejected");
          samples_count_ = 0;
        }
      }
      state_->sync_ready_queue.clear();
      const uint64_t paints = static_cast<uint64_t>(state_->paints.load());
      if (ready_ && samples_count_ < samples_.size() && paints > last_paint_ &&
          !state_->buffer.empty()) {
        auto& sample = samples_[samples_count_++];
        ++sample_count_;
        sample.sequence = paints;
        sample.width = state_->width;
        sample.height = state_->height;
        // Freeze native ownership before any renderer checkpoint can pump a
        // newer OnPaint. Swap reuses the preceding frame's backing allocation.
        sample.bytes.swap(state_->buffer);
        last_paint_ = paints;
        if (samples_count_ == samples_.size()) {
          samples_us_ = NowUs();
          SetPassiveSnapshotDemandLocked(*state_, false);
        }
      }
    }
    if (injected_ && !ready_ && NowUs() >= ready_deadline_)
      return Fail("unsigned_ready_timeout");
    if (samples_count_ < samples_.size()) {
      browser_->GetHost()->Invalidate(PET_VIEW);
      browser_->GetHost()->SendExternalBeginFrame();
    }
    return true;
  }

  bool FeedIfReady(RunState& signed_state, PumpDriver& pump,
                   CefRefPtr<CefBrowser> signed_browser,
                   fsync::FrameSyncStateMachine& machine, uint8_t* dst,
                   uint64_t deadline) {
    if (state_->closed.load()) return Fail("unsigned_browser_closed");
    if (signed_state.closed.load()) return Fail("signed_browser_closed");
    if (!machine.needs_baseline_veto() ||
        samples_count_ < samples_.size()) return true;
    // One unsigned fence covers both frozen observations. A signed fence after
    // it also catches errors produced while the unsigned browser was rendering.
    // Neither observer can be skipped merely because the pixels compare equal.
    if (!CheckJsCheckpoint(*state_, pump, browser_, deadline))
      return Fail(state_->js_error.failed ? "JS_ERR_unsigned_capture" :
                                           "unsigned_capture_fence_timeout");
    if (!CheckJsCheckpoint(signed_state, pump, signed_browser, deadline))
      return Fail(signed_state.js_error.failed ? "JS_ERR_signed_capture" :
                                               "signed_capture_fence_timeout");
    // The second fence pumps BOTH browsers. An unsigned error delivered during
    // it invalidates the veto even though the earlier unsigned fence passed.
    if (state_->closed.load()) return Fail("unsigned_browser_closed");
    if (signed_state.closed.load()) return Fail("signed_browser_closed");
    if (state_->js_error.failed) return Fail("JS_ERR_unsigned_during_signed_fence");
    for (const auto& sample : samples_) {
      if (NowUs() >= deadline) return Fail("unsigned_veto_timeout");
      const auto verdict = machine.FeedExactBaselinePaint(identity_,
          sample.bytes.data(), static_cast<size_t>(sample.width) * 4u,
          static_cast<uint32_t>(sample.width),
          static_cast<uint32_t>(sample.height), dst, NowUs(), sample.sequence);
      if (verdict.disposition == fsync::PaintDisposition::kAccepted) {
        veto_us_ = NowUs();
        signed_state.sync_last_verdict = verdict;
        signed_state.sync_captured = true;
        return true;
      }
    }
    // Early stable blanks or late rasterization do not justify another fresh
    // browser/navigation. Continue collecting new unsigned paints inside the
    // ORIGINAL pump deadline; mismatches never relax the equality predicate
    // except through the bounded, audited rasterization-jitter tolerance
    // path in the sync machine (frozen signature + strict budgets).
    // Do not carry an M,E batch's trailing match into the next E,M batch:
    // accepting its first E would ignore the already-frozen latest mismatch.
    machine.DiscardExactBaselinePartialMatch();
    samples_count_ = 0;
    last_paint_ = state_->SetPassiveSnapshotDemand(true);
    return true;
  }

  void PauseCapture() {
    if (state_) state_->SetPassiveSnapshotDemand(false);
  }
  void PoisonResident() { resident_poisoned_ = true; }
  void NoteSignedProof() {
    if (signed_proof_us_ == 0) signed_proof_us_ = NowUs();
  }
  std::string TimingFields() const {
    const auto elapsed = [&](uint64_t end) { return end ? end - started_us_ : 0; };
    return ",\"capture_offsets_origin\":\"sync_request_begin\""
        ",\"reference_context\":\"" + options_->reference_context + "\"" +
        ",\"unsigned_navigations\":" + std::to_string(navigated_ ? 1 : 0) +
        ",\"unsigned_load_us\":" + std::to_string(elapsed(loaded_us_)) +
        ",\"unsigned_ready_us\":" + std::to_string(elapsed(ready_us_)) +
        ",\"unsigned_two_samples_us\":" + std::to_string(elapsed(samples_us_)) +
        ",\"unsigned_veto_us\":" + std::to_string(elapsed(veto_us_)) +
        ",\"signed_proof_us\":" + std::to_string(elapsed(signed_proof_us_)) +
        ",\"unsigned_renderer_pid\":" + std::to_string(state_->js_error.renderer_pid) +
        ",\"unsigned_fence_count\":" + std::to_string(state_->js_error.sent - checkpoint_floor_) +
        ",\"unsigned_native_paints\":" + std::to_string(
            static_cast<uint64_t>(state_->paints.load()) - paint_floor_) +
        ",\"unsigned_samples_captured\":" + std::to_string(sample_count_) +
        ",\"unsigned_copied_paints\":" + std::to_string(
            state_->passive_snapshot_copied_paints - copied_paints_floor_) +
        ",\"unsigned_copied_bytes\":" + std::to_string(
            state_->passive_snapshot_copied_bytes - copied_bytes_floor_) +
        ",\"unsigned_discarded_paints\":" + std::to_string(
            state_->passive_snapshot_dropped_paints - dropped_paints_floor_);
  }
  bool js_failed() const { return state_ && state_->js_error.failed; }
  const char* failure_reason() const { return failure_reason_; }

  bool Close(PumpDriver& pump) {
    if (!browser_ && creating_ &&
        !pump.Until([&] {
          // Creation and closure may both complete in one pump batch. The
          // client has already cleared its browser reference at OnBeforeClose;
          // terminal closure, not successful adoption, then ends cleanup.
          return state_->closed.load() || AdoptCreatedBrowser();
        }, 5000)) return false;
    if (!browser_) {
      // Context creation may have succeeded before CreateBrowser rejected the
      // request. No CEF reference may survive a successful Close/CefShutdown.
      creating_ = false;
      request_context_ = nullptr;
      return true;
    }
    if (state_->closed.load()) {
      browser_ = nullptr;
      request_context_ = nullptr;
      return true;  // no browser method is valid after OnBeforeClose
    }
    browser_->GetHost()->CloseBrowser(true);
    const bool closed = pump.Until([&] { return state_->closed.load(); }, 5000);
    if (closed) {
      browser_ = nullptr;
      request_context_ = nullptr;  // context outlives the browser's close callback
    }
    return closed;
  }

 private:
  bool AdoptCreatedBrowser() {
    if (!state_) return false;
    std::lock_guard<std::mutex> lock(state_->mu);
    if (!state_->browser) return false;
    browser_ = state_->browser;
    // WorkerClient owns state until OnBeforeClose. Break its reference cycle
    // only after retaining the browser handle that drives capture and cleanup.
    state_->browser = nullptr;
    creating_ = false;
    return true;
  }
  struct PaintSample {
    std::vector<uint8_t> bytes;
    uint64_t sequence = 0;
    int width = 0;
    int height = 0;
  };
  bool Fail(const char* reason) {
    failed_ = true;
    failure_reason_ = reason;
    return false;
  }
  std::shared_ptr<RunState> state_;
  CefRefPtr<CefBrowser> browser_;
  CefRefPtr<CefRequestContext> request_context_;
  bool creating_ = false;
  const ServeOptions* options_ = nullptr;
  fsync::PageEvent identity_{};
  std::array<PaintSample, 2> samples_;
  size_t samples_count_ = 0;
  uint64_t last_paint_ = 0;
  uint64_t load_floor_ = 0;
  uint64_t paint_floor_ = 0, sample_count_ = 0;
  uint64_t copied_paints_floor_ = 0, copied_bytes_floor_ = 0, dropped_paints_floor_ = 0;
  uint32_t checkpoint_floor_ = 0;
  uint64_t started_us_ = 0, loaded_us_ = 0, ready_us_ = 0;
  uint64_t samples_us_ = 0, veto_us_ = 0, signed_proof_us_ = 0;
  bool navigated_ = false;
  uint64_t load_deadline_ = 0;
  uint64_t ready_deadline_ = 0;
  bool mode_n_ = true;
  bool resident_poisoned_ = false;
  bool injected_ = false;
  bool ready_ = false;
  bool failed_ = false;
  const char* failure_reason_ = "baseline_timeout";
};

// Only this consumer may feed a signed OnPaint candidate into the verifier.
// Pumping the fence cannot overwrite these bytes or advance the machine to S5.
static bool ConsumeJsPaint(RunState& state, PumpDriver& pump,
    CefRefPtr<CefBrowser> browser, uint64_t deadline) {
  if (!state.js_paint_pending) return !state.closed.load() && !state.js_error.failed;
  auto candidate = std::move(state.js_paint_candidate);
  const int w = state.js_paint_w, h = state.js_paint_h;
  // Keep the pending gate closed while the native fence pumps. No subsequent
  // signed phase has been issued yet, so another same-phase copy would only
  // add bandwidth/backlog, not evidence. The candidate owns immutable bytes.
  if (!CheckJsCheckpoint(state, pump, browser, deadline)) {
    state.js_paint_pending = false;
    state.js_paint_candidate.swap(candidate);
    return false;
  }
  const auto verdict = state.sync_machine->FeedOwnedPaint(candidate,
      static_cast<uint32_t>(w), static_cast<uint32_t>(h), state.sync_dst, NowUs());
  state.js_paint_pending = false;
  state.js_paint_candidate.swap(candidate);
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
                                   ExactBaselineCapture& baseline,
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
  // A failed resident request may still have an unresolved page Promise.
  // Never let its later side effects contaminate another exact request: the
  // caller must restart this worker instead of silently reloading or reusing it.
  struct ResidentGuard {
    ExactBaselineCapture& baseline;
    bool exact;
    bool resident;
    bool completed = false;
    ~ResidentGuard() {
      if (exact) baseline.PauseCapture();
      if (resident && !completed) baseline.PoisonResident();
    }
  } resident_guard{baseline, exact, exact && !mode_n};
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
  const auto js_fail = [&](const char* timeout_reason) {
    disarm();
    r.status = state.js_error.failed ? kStatusJsErr : kStatusTimeout;
    machine.FailRequest(state.js_error.failed ? "JS_ERR_observer_fence" :
        state.closed.load() ? "signed_browser_closed" : timeout_reason, NowUs());
    return r;
  };
  if (state.closed.load()) return js_fail("signed_browser_closed");

  const auto baseline_fail = [&]() {
    disarm();
    r.status = (state.js_error.failed || baseline.js_failed())
        ? kStatusJsErr : kStatusTimeout;
    machine.FailRequest(baseline.failure_reason(), NowUs());
    return r;
  };
  const uint64_t load_deadline = NowUs() +
      static_cast<uint64_t>(opt.frame_budget_ms) * 1000ull;
  if (exact) {
    // Issue the independent unsigned navigation BEFORE the signed navigation.
    // The two documents progress concurrently, never serialize baseline paints
    // in front of signed proof, and neither can supply the other's output bytes.
    if (!baseline.Begin(opt, document_gen, request_id, frame, load_deadline,
                        machine.t_request_begin_us()))
      return baseline_fail();
    std::lock_guard<std::mutex> lock(state.mu);
    state.sync_ready_queue.clear();
  }
  if (mode_n) {
    state.loaded.store(false, std::memory_order_relaxed);
    const auto url = ExpandUrlTemplate(opt.url_template, frame);
    PrepareJsDocument(state, url);
    const auto load_floor = state.load_starts.load();
    browser->GetMainFrame()->LoadURL(url);
    while (!state.closed.load() && !(state.load_starts.load() > load_floor &&
             state.loaded.load(std::memory_order_relaxed) &&
             !browser->IsLoading() &&
             browser->GetMainFrame()->GetURL().ToString() == url)) {
      if (NowUs() >= load_deadline) {
        disarm();
        r.status = kStatusTimeout;
        machine.FailRequest("load_timeout", NowUs());
        return r;
      }
      if (exact && !baseline.Advance(pump, machine, load_deadline))
        return baseline_fail();
      if (state.closed.load()) return js_fail("signed_browser_closed");
      pump.Once();
      pump.WaitForWork(load_deadline);
    }
    if (state.closed.load()) return js_fail("signed_browser_closed");
  }
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
      if (state.closed.load()) return js_fail("signed_browser_closed");
      browser->GetHost()->SendExternalBeginFrame();
      pump.Once();
    }
  }
  const uint64_t ready_deadline =
      t0 + static_cast<uint64_t>(opt.sync_ready_timeout_ms) * 1000ull;
  while (machine.state() != fsync::State::S3_JS_FRAME_APPLIED_AND_CANARY_SET &&
         NowUs() < ready_deadline) {
    if (exact && !baseline.Advance(pump, machine, ready_deadline))
      return baseline_fail();
    // The reference fence pumps the signed browser too; it can close even
    // while the unsigned fence succeeds. Check before any signed browser call.
    if (state.closed.load()) return js_fail("signed_browser_closed");
    browser->GetHost()->Invalidate(PET_VIEW);
    browser->GetHost()->SendExternalBeginFrame();
    pump.Once();
    DrainSyncReadyQueue(state, machine);
    if (!ConsumeJsPaint(state, pump, browser, ready_deadline)) return js_fail("ready_timeout");
    pump.WaitForWork(ready_deadline);
  }
  DrainSyncReadyQueue(state, machine);
  if (machine.state() != fsync::State::S3_JS_FRAME_APPLIED_AND_CANARY_SET) {
    disarm();
    r.status = kStatusTimeout;
    machine.FailRequest("ready_timeout", NowUs());
    return r;
  }
  if (!CheckJsCheckpoint(state, pump, browser, ready_deadline)) return js_fail("ready_timeout");
  r.t_ready_wait_us = machine.t_ready_us() - machine.t_request_begin_us();

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
    if (exact && !baseline.Advance(pump, machine, pump_deadline))
      return baseline_fail();
    if (state.closed.load()) return js_fail("signed_browser_closed");
    if (exact && machine.needs_relocation() && !relocated) {
      browser->GetMainFrame()->ExecuteJavaScript(
          "window.__VTX_RELOCATE();", "vtx_sync_relocate", 0);
      relocated = true;
    }
    if (!exact || !machine.needs_baseline_veto()) {
      browser->GetHost()->Invalidate(PET_VIEW);
      browser->GetHost()->SendExternalBeginFrame();
    }
    pump.Once();
    DrainSyncReadyQueue(state, machine);
    if (!ConsumeJsPaint(state, pump, browser, pump_deadline)) return js_fail("pump_timeout");
    if (exact && machine.needs_baseline_veto()) baseline.NoteSignedProof();
    if (exact && !baseline.FeedIfReady(state, pump, browser, machine, dst,
                                     pump_deadline)) return baseline_fail();
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
    pump.WaitForWork(pump_deadline);
  }
  disarm();
  if (state.closed.load() || state.js_error.failed) return js_fail("pump_timeout");
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
  resident_guard.completed = true;
  // RunServe emits complete capture/encode timing before request_end → S0.
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

// Close a signed browser even if asynchronous creation completes only after
// the startup deadline. A successful CreateBrowser request with no callback is
// still live CEF work: it is not evidence that CefShutdown is safe.
bool CloseServeBrowser(RunState& state, PumpDriver& pump,
                       CefRefPtr<CefBrowser>& browser, bool creation_requested) {
  const auto adopt_or_closed = [&]() {
    std::lock_guard<std::mutex> lock(state.mu);
    if (state.closed.load(std::memory_order_acquire)) return true;
    if (!browser) browser = state.browser;
    return browser != nullptr;
  };
  if (!adopt_or_closed() && creation_requested &&
      !pump.Until(adopt_or_closed, 5000)) return false;
  if (!state.closed.load(std::memory_order_acquire) && browser) {
    browser->GetHost()->CloseBrowser(true);
    if (!pump.Until([&] { return state.closed.load(std::memory_order_acquire); },
                    5000)) return false;
  }
  // OnBeforeClose may already have run (e.g. a failed initialization). Never
  // call GetHost/CloseBrowser after that callback; only release owned handles.
  browser = nullptr;
  {
    std::lock_guard<std::mutex> lock(state.mu);
    state.browser = nullptr;
  }
  return true;
}

// Once CEF creation/closure is unresolved, unwinding RunState would leave live
// callbacks pointing to stack storage. Exit this worker without CEF calls or
// destructor joins; the existing process owner reclaims its entire tree and
// private scratch. This is a failed process, never a successful shutdown.
[[noreturn]] void ExitUnclosedServe(int status) {
  // In particular, do not fflush(nullptr): a blocked encoder may own a FILE
  // lock, so flushing all streams would turn this fail-stop into another wait.
  // Do not print to stderr either: an encoder/CEF thread may own that FILE.
  // The host records the nonzero exit/EOF; READY stdout was already flushed.
  std::_Exit(status);
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
  if (opt.cef.capture_fps < 1 || opt.cef.capture_fps > 240) {
    std::fprintf(stderr, "vtx_cef_worker: capture fps must be 1..240\n");
    return 2;
  }
  if (opt.reference_context != "shared" && opt.reference_context != "isolated") {
    std::fprintf(stderr, "vtx_cef_worker: reference context must be shared or isolated\n");
    return 2;
  }
  if (sync_mode_p && opt.url_template.find("{f}") != std::string::npos) {
    std::fprintf(stderr, "vtx_cef_worker: resident Mode P URL must not contain {f}\n");
    return 2;
  }
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
  CefRefPtr<CefBrowser> browser;
  bool creation_requested = false;
  const auto shutdown_signed = [&]() {
    // Disarm callback consumers before pumping closure, including early Mode P
    // load failure, where sync_machine is already installed.
    state.sync_capture_armed = false;
    state.capture_armed = false;
    state.sync_machine = nullptr;
    if (!CloseServeBrowser(state, pump, browser, creation_requested)) {
      ExitUnclosedServe(1);
    }
    client = nullptr;
    CefShutdown();
  };
  creation_requested = CreateOsrBrowser(client, initial_url,
                                         cef_opt.width, cef_opt.height,
                                         cef_opt.capture_fps);
  if (!creation_requested || !pump.Until(
          [&] {
            std::lock_guard<std::mutex> lock(state.mu);
            return state.closed.load() || state.browser != nullptr;
          },
          30000) || state.closed.load()) {
    std::fprintf(stderr, "vtx_cef_worker: 浏览器创建失败或超时\n");
    shutdown_signed();
    ::close(listen_fd);
    ::unlink(opt.ipc_socket.c_str());
    cleanup_owned();
    return 1;
  }
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
        shutdown_signed();
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
      const auto load_floor = state.load_starts.load();
      browser->GetMainFrame()->LoadURL(resident_url);
      if (!pump.Until(
              [&] { return state.closed.load() ||
                  (state.load_starts.load() > load_floor &&
                  state.loaded.load(std::memory_order_relaxed) &&
                  !browser->IsLoading() &&
                  browser->GetMainFrame()->GetURL().ToString() == opt.url_template); },
              opt.frame_budget_ms) || state.closed.load()) {
        std::fprintf(stderr, "vtx_cef_worker: sync Mode P 常驻页加载超时\n");
        shutdown_signed();
        if (sync_log_fh != nullptr) {
          std::fclose(sync_log_fh);
        }
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
    shutdown_signed();
    if (sync_log_fh != nullptr) std::fclose(sync_log_fh);
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
  // The unsigned browser can launch renderer helpers AFTER connection. Do not
  // let a helper inherit this transport and keep EOF alive after worker death.
  if (conn >= 0 && ::fcntl(conn, F_SETFD, FD_CLOEXEC) < 0) {
    ::close(conn);
    conn = -1;
    accept_rc = -1;
  }
  ::close(listen_fd);
  ::unlink(opt.ipc_socket.c_str());  // 监听名使命完成（单宿主契约）
  if (conn < 0) {
    std::fprintf(stderr, "vtx_cef_worker: 无宿主连接（accept %s）\n",
                 accept_rc == 0 ? "空闲超时" : "错误");
    shutdown_signed();
    if (sync_log_fh != nullptr) std::fclose(sync_log_fh);
    cleanup_owned();
    return accept_rc == 0 ? 5 : 1;
  }

  {
    HelloInfo hello;
    std::memset(&hello, 0, sizeof(hello));
    hello.magic = kHelloMagic;
    hello.protocol_version = kProtocolVersion;
    hello.flags = kHelloHasShmFd;
    if (opt.sync && opt.sync_canary == "exact") hello.flags |= kHelloPairedExact;
    hello.run_id = run_id;
    hello.pid = static_cast<int32_t>(::getpid());
    hello.ring_slots = kRingSlots;
    hello.max_width = static_cast<uint32_t>(cef_opt.width);
    hello.max_height = static_cast<uint32_t>(cef_opt.height);
    hello.frame_bytes = ring->geometry().frame_bytes;
    if (!SendHelloWithFd(conn, hello, ring->fd())) {
      std::fprintf(stderr, "vtx_cef_worker: Hello/fd 交付失败\n");
      ::close(conn);
      shutdown_signed();
      if (sync_log_fh != nullptr) std::fclose(sync_log_fh);
      cleanup_owned();
      return 4;
    }
  }

  // ---- 请求主循环 ----
  ExactBaselineCapture exact_baseline;
  PngEncodeWorkspace encode_workspace;
  MiniPool pool(opt.encode_threads, [&state] { state.pump_schedule.NotifyWork(); });
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
    const uint64_t request_started_us = NowUs();
    const auto signed_checkpoint_floor = state.js_error.sent;
    const auto signed_paint_floor = state.paints.load();
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

    const auto finish_sync = [&](uint64_t capture_us, uint64_t queue_us,
                                 uint64_t alpha_us, uint64_t png_us,
                                 uint64_t disk_us, uint64_t backpressure_us,
                                 uint16_t delivery_status) {
      if (!opt.sync || !sync_machine.accepted()) return;
      if (sync_mode_p && opt.sync_canary == "exact" && delivery_status != kStatusOk)
        exact_baseline.PoisonResident();
      const uint64_t now = NowUs();
      std::string fields = "\"request_wall_to_reply_us\":" +
          std::to_string(now - request_started_us) +
          ",\"capture_fps_requested\":" + std::to_string(cef_opt.capture_fps) +
          ",\"capture_wall_us\":" + std::to_string(capture_us) +
          ",\"signed_ready_us\":" + std::to_string(
              sync_machine.t_ready_us() - sync_machine.t_request_begin_us()) +
          ",\"signed_renderer_pid\":" + std::to_string(state.js_error.renderer_pid) +
          ",\"signed_navigations\":" + std::to_string(sync_mode_p ? 0 : 1) +
          ",\"signed_fence_count\":" + std::to_string(state.js_error.sent - signed_checkpoint_floor) +
          ",\"signed_native_paints\":" + std::to_string(state.paints.load() - signed_paint_floor) +
          ",\"encode_queue_us\":" + std::to_string(queue_us) +
          ",\"alpha_us\":" + std::to_string(alpha_us) +
          ",\"png_us\":" + std::to_string(png_us) +
          ",\"disk_us\":" + std::to_string(disk_us) +
          ",\"backpressure_us\":" + std::to_string(backpressure_us) +
          ",\"delivery_status\":" + std::to_string(delivery_status) +
          ",\"timing_boundary\":\"request_received_to_reply_ready\"" +
          ",\"capture_stages_overlap\":" +
          (opt.sync_canary == "exact" ? "true" : "false");
      if (opt.sync_canary == "exact") fields += exact_baseline.TimingFields();
      sync_machine.RecordAdapterTiming(now, fields);
      sync_machine.Reset(now, "done");
    };

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
      const uint64_t capture_started_us = NowUs();
      if (opt.sync) {
        // #114 状态机路径：canary/拒收计数/时序取自机器（解码结果口径）。
        SyncProduceResult sr = ProduceFrameSync(
            state, pump, browser, sync_machine, exact_baseline, opt, document_gen,
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
      const uint64_t capture_wall_us = NowUs() - capture_started_us;
      uint64_t t_alpha = 0, t_png = 0, t_disk = 0, t_queue = 0;
      bool deliver_ok = r.status == kStatusOk;
      bool encode_timed_out = false;
      if (deliver_ok) {
        const uint64_t queued_us = NowUs();
        pool.Post([&] {
          t_queue = NowUs() - queued_us;
          try {
            PngBuffer png;
            const uint64_t t3 = NowUs();
            const bool enc = EncodePngBgraPremultiplied(state.cap_staging.data(),
                static_cast<int>(r.width), static_cast<int>(r.height),
                static_cast<size_t>(r.width) * 4u, opt.png, &encode_workspace,
                &png, &t_alpha);
            const uint64_t encode_wall_us = NowUs() - t3;
            t_png = encode_wall_us >= t_alpha ? encode_wall_us - t_alpha : 0;
            const uint64_t t4 = NowUs();
            deliver_ok = enc && AtomicWriteBytes(out_path, png.data(), png.size());
            t_disk = NowUs() - t4;
          } catch (...) {
            // Keep task completion observable even if allocation/encoding throws.
            deliver_ok = false;
          }
        });
        if (!pump.Until([&] { return pool.Done(); }, 60000)) {
          encode_timed_out = true;
          // This is a reply deadline, not cancellation of a blocking writer.
          // Keep every capture/output owned until Done's acquire synchronizes
          // with the task. Allow one bounded drain grace; if I/O is still stuck,
          // exit the worker without joining the task, unwinding its references,
          // acknowledging any receipt, or admitting another request. The existing
          // process owner reclaims the tree and its private partial output.
          if (!pump.Until([&] { return pool.Done(); }, 5000)) {
            ExitUnclosedServe(4);
          }
          r.status = kStatusTimeout;
          std::fprintf(stderr, "vtx_cef_worker: PNG 编码/落盘超时，任务已收尾 %s\n",
                       out_path.c_str());
        } else if (!deliver_ok) {
          r.status = kStatusJsErr;  // 落盘失败以非 OK 码显形（复用 3 号档）
          std::fprintf(stderr, "vtx_cef_worker: PNG 编码/落盘失败 %s\n",
                       out_path.c_str());
        }
      }
      finish_sync(capture_wall_us, t_queue, t_alpha, t_png, t_disk, 0, r.status);
      FrameSlotHeader receipt;
      FillReceipt(&receipt, r, run_id, req.request_id, req.frame_index,
                  t_alpha, t_png, t_disk);
      if (!SendAll(conn, &receipt, sizeof(receipt))) {
        exit_code = 4;
        break;
      }
      if (encode_timed_out) {
        exit_code = 4;  // Failed request: drained task, no output/request reuse.
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
      const uint64_t capture_started_us = NowUs();
      r.document_gen = document_gen;
      if (slot < 0) {
        r.status = kStatusTimeout;  // 背压超时：明确错误，不死等
      } else if (opt.sync) {
        SyncProduceResult sr = ProduceFrameSync(
            state, pump, browser, sync_machine, exact_baseline, opt, document_gen,
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
      const uint64_t capture_wall_us = NowUs() - capture_started_us;
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
      finish_sync(capture_wall_us, 0, 0, 0, 0, bp_wait, r.status);
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
  {
    std::lock_guard<std::mutex> lock(state.mu);
    state.sync_machine = nullptr;  // 先摘除再析构（防御晚回调）
  }
  const bool unsigned_closed = exact_baseline.Close(pump);
  const bool signed_closed = CloseServeBrowser(state, pump, browser, creation_requested);
  if (!unsigned_closed || !signed_closed) {
    // Calling CefShutdown with a live browser violates CEF's lifetime contract.
    // Do not log/flush/close streams here: another thread may own a FILE lock.
    // The nonzero process exit and host EOF establish the failed shutdown.
    ExitUnclosedServe(1);
  }
  browser = nullptr;
  state.browser = nullptr;
  client = nullptr;
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
