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

#include <poll.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <atomic>
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
    const ssize_t s = ::send(fd, p + sent, n - sent, MSG_NOSIGNAL);
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
  return ::sendmsg(conn, &msg, MSG_NOSIGNAL) ==
         static_cast<ssize_t>(sizeof(hello));
}

// ---------------------------------------------------------------- 编码线程池

// 最小线程池：Post 提交单任务（顺序契约：上次任务须已完成），主线程以
// pump 等待 Done —— 重活不占 CEF UI 线程，等待期浏览器任务仍被驱动。
class MiniPool {
 public:
  explicit MiniPool(int n) {
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
    }
  }
  std::vector<std::thread> workers_;
  mutable std::mutex mu_;
  std::condition_variable work_cv_;
  std::function<void()> task_;
  std::atomic<bool> done_{true};
  bool stop_ = false;
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
  state.loaded.store(false, std::memory_order_relaxed);
  browser->GetMainFrame()->LoadURL(ExpandUrlTemplate(url_template, frame));
  const uint64_t t0 = NowUs();
  if (!pump.Until([&] { return state.loaded.load(std::memory_order_relaxed); },
                  opt.frame_budget_ms)) {
    r.status = kStatusTimeout;
    return r;
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

  // ---- 参数面校验 ----
  if (opt.url_template.find("{f}") == std::string::npos) {
    std::fprintf(stderr, "vtx_cef_worker: --url-template 缺 {f} 占位\n");
    return 2;
  }
  struct sockaddr_un sun_probe;
  if (opt.ipc_socket.empty() ||
      opt.ipc_socket.size() + 1 > sizeof(sun_probe.sun_path)) {
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
  const int listen_fd = ::socket(AF_UNIX, SOCK_STREAM, 0);
  if (listen_fd < 0) {
    std::fprintf(stderr, "vtx_cef_worker: socket() 失败: %s\n",
                 std::strerror(errno));
    return 1;
  }
  struct sockaddr_un addr;
  std::memset(&addr, 0, sizeof(addr));
  addr.sun_family = AF_UNIX;
  std::strncpy(addr.sun_path, opt.ipc_socket.c_str(),
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
  int accept_rc = ::poll(&pf, 1, opt.idle_timeout_ms);
  int conn = -1;
  if (accept_rc > 0) {
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
  MiniPool pool(opt.encode_threads);
  const size_t frame_bytes = ring->geometry().frame_bytes;
  std::vector<uint8_t> rgba(frame_bytes);
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
    ++document_gen;

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
      ProduceResult r =
          ProduceFrame(state, pump, browser, opt.url_template,
                       req.frame_index, opt, document_gen);
      uint64_t t_alpha = 0, t_png = 0, t_disk = 0;
      bool deliver_ok = r.status == kStatusOk;
      if (deliver_ok) {
        uint64_t t2 = 0, t3 = 0, t4 = 0;
        pool.Post([&] {
          t2 = NowUs();
          UnpremultiplyToRgba(state.cap_staging.data(),
                              static_cast<size_t>(r.width) * r.height * 4u,
                              rgba.data());
          t_alpha = NowUs() - t2;
          std::vector<uint8_t> png;
          t3 = NowUs();
          const bool enc =
              EncodePngRgba(rgba.data(), static_cast<int>(r.width),
                            static_cast<int>(r.height), &png);
          t_png = NowUs() - t3;
          t4 = NowUs();
          deliver_ok = enc && AtomicWriteBytes(out_path, png.data(), png.size());
          t_disk = NowUs() - t4;
        });
        pump.Until([&] { return pool.Done(); }, 60000);  // 泵着等编码完成
        if (!deliver_ok) {
          r.status = kStatusJsErr;  // 落盘失败以非 OK 码显形（复用 3 号档）
          std::fprintf(stderr, "vtx_cef_worker: PNG 编码/落盘失败 %s\n",
                       out_path.c_str());
        }
      }
      FrameSlotHeader receipt;
      FillReceipt(&receipt, r, run_id, req.request_id, req.frame_index,
                  t_alpha, t_png, t_disk);
      if (!SendAll(conn, &receipt, sizeof(receipt))) {
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
  browser->GetHost()->CloseBrowser(true);
  pump.Until([&] { return state.closed.load(); }, 5000);
  CefShutdown();
  if (owns_cache) {
    std::error_code ec;
    std::filesystem::remove_all(root_cache, ec);
  }
  return exit_code;
}

}  // namespace vtx_cef
