// output_helper.cpp — vtx_elec_output_helper：Electron 输出桥原生编码助手
// （issue #113 ELEC-02 路径 1 bitmap_shared_encoder 的 C++ 侧）。
//
// 职责（复用 #111 已交付源码，只读引用 experiments/gfx_cef_worker/）：
//   1. 附着 Electron 创建的 /dev/shm 4-Slot 环（open → **立即 unlink** 零残留
//      → mmap；布局=shm_ring.hpp RingGeometry 同构）；
//   2. connect Electron 的 Unix socket，定长二进制协议（elec_output_protocol.hpp）；
//   3. 每请求：槽头校验 → v2 双像素 canary 闸门（canary_v2，验证+恢复）
//      → SIMD 逆预乘（pixel_ops.h）→ PNG 编码（png_writer.h：spng/fpng/zlib
//      × level × filter）→ .tmp.<pid>.<tid> + rename 原子落盘 → 回执
//      （计时/体积/crc32/canary 归因，全部取自解码与实测，不转录请求）；
//   4. 线程池（默认 4）并发处理；槽释放即回 credit（socket 信用=权威流控）。
//
// 模式：
//   --selftest [--selftest-json]  canary v2 全域穷举自检（无 SHM/socket）
//   --bench    [--bench-json]     1080p 编码器选型基准（内容类×后端×并发，
//                                 含体积与下游解码成本；#113「编码器选择实测」）
//   --check-header <file>         解析 128B 槽头二进制为 JSON（跨语言偏移锁定）
//   （缺省）serve 模式：--shm= --sock= [--slots=4] [--width=1920] [--height=1080]
//                       [--threads=4] [--idle-timeout-ms=120000]
//
// 退出码：0=正常收尾（socket EOF 后在途排空）；2=参数错；3=SHM/socket 失败；
//         4=协议错误；5=空闲超时。SIGKILL/崩溃由内核回收 SHM（已 unlink）。
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cmath>
#include <condition_variable>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

// ---- CEF #111 复用面（只读引用；构建脚本传 -I）----
#include "pixel_ops.h"
#include "png_writer.h"
#include "shm_ring.hpp"  // RingGeometry / NowUs / FrameSlotHeader（#110 同构环）
#include "spng.h"        // third_party/spng（解码基准用；编码经 png_writer）
#include <zlib.h>        // crc32（回执校验和）

#include "canary_v2.h"
#include "elec_output_protocol.hpp"

namespace {

using namespace vtx_elec;  // elec_output_protocol.hpp 全量导入（本 TU 私有）
using vtx_cef::FrameSlotHeader;
using vtx_cef::NowUs;
using vtx_cef::RingGeometry;

[[noreturn]] void Die(int code, const char* msg) {
  std::fprintf(stderr, "✗ vtx_elec_output_helper: %s\n", msg);
  std::exit(code);
}

// ---------------------------------------------------------------- 小工具
bool RecvAll(int fd, void* buf, size_t n) {
  auto* p = static_cast<uint8_t*>(buf);
  size_t got = 0;
  while (got < n) {
    const ssize_t r = ::recv(fd, p + got, n - got, 0);
    if (r == 0) return false;  // EOF
    if (r < 0) {
      if (errno == EINTR) continue;
      return false;
    }
    got += static_cast<size_t>(r);
  }
  return true;
}

bool SendAll(int fd, const void* buf, size_t n) {
  const auto* p = static_cast<const uint8_t*>(buf);
  size_t sent = 0;
  while (sent < n) {
    const ssize_t r = ::send(fd, p + sent, n - sent, MSG_NOSIGNAL);
    if (r < 0) {
      if (errno == EINTR) continue;
      return false;
    }
    sent += static_cast<size_t>(r);
  }
  return true;
}

std::string HostnameOfBackend(const char* name) {
  // pixel_ops 报告 "avx2"/"neon"/"scalar" → 协议 u8 码。
  if (std::strcmp(name, "avx2") == 0) return "1";
  if (std::strcmp(name, "neon") == 0) return "2";
  return "0";
}

// ---------------------------------------------------------------- SHM 环附着
struct ShmRing {
  int fd = -1;
  void* base = nullptr;
  uint64_t total = 0;
  vtx_cef::RingControl* control = nullptr;
  RingGeometry geo{};

  void Attach(const std::string& path, uint32_t slots, uint32_t width,
              uint32_t height) {
    fd = ::open(path.c_str(), O_RDWR);
    if (fd < 0) Die(3, (std::string("打开 SHM 失败: ") + std::strerror(errno)).c_str());
    // 零残留关键序：open 成功后立即解绑名字——实体此后仅由双方 fd/mmap
    // 维系，任何一方死亡（含 SIGKILL）由内核回收，/dev/shm 零残留。
    ::unlink(path.c_str());
    struct stat st {};
    if (::fstat(fd, &st) != 0 || st.st_size <= 0) Die(3, "SHM fstat 失败");
    total = static_cast<uint64_t>(st.st_size);
    base = ::mmap(nullptr, total, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (base == MAP_FAILED) Die(3, (std::string("SHM mmap 失败: ") + std::strerror(errno)).c_str());
    control = static_cast<vtx_cef::RingControl*>(base);
    // 等创建方发布布局（Electron 在 spawn 前已写；此 poll 是协议防御面）。
    const uint64_t t0 = NowUs();
    while (control->layout_ready.load(std::memory_order_acquire) !=
           vtx_cef::kRingMagic) {
      if (NowUs() - t0 > 5'000'000ull) Die(3, "环布局就绪超时");
      ::usleep(100);
    }
    if (control->magic != vtx_cef::kRingMagic ||
        control->protocol_version != vtx_cef::kProtocolVersion ||
        control->ring_slots != slots ||
        control->max_width != width || control->max_height != height) {
      Die(3, "环控制块 magic/版本/槽数/几何不符");
    }
    geo = RingGeometry::ForSize(width, height);
    if (geo.total_bytes != total) Die(3, "环尺寸与几何不符");
    if (control->gates[0].state.load(std::memory_order_acquire) != 0) {
      Die(3, "槽门初态非 EMPTY");
    }
  }

  // #110 同构访问器（只读视角；释放经 ServeCtx 的 credit 路径）。
  const FrameSlotHeader* SlotHeader(uint32_t index) const {
    return reinterpret_cast<const FrameSlotHeader*>(
        static_cast<const uint8_t*>(base) + geo.SlotOffset(index));
  }
  const uint8_t* SlotData(uint32_t index) const {
    return static_cast<const uint8_t*>(base) + geo.DataOffset(index);
  }
};

// ---------------------------------------------------------------- 帧处理
struct WorkItem {
  ProtoRequest req{};
};

struct ServeCtx {
  ShmRing ring;
  int sock = -1;
  uint32_t slots = 4;
  uint32_t width = 1920;
  uint32_t height = 1080;
  std::mutex qmux;
  std::condition_variable qcv;
  std::deque<WorkItem> queue;
  std::mutex sendmux;
  std::atomic<bool> draining{false};
  std::vector<std::thread> pool;

  bool QueuePop(WorkItem* out) {
    std::unique_lock<std::mutex> lk(qmux);
    qcv.wait(lk, [&] { return !queue.empty() || draining.load(); });
    if (queue.empty()) return false;  // draining
    *out = queue.front();
    queue.pop_front();
    return true;
  }

  bool SendMsg(const void* buf, size_t n) {
    std::lock_guard<std::mutex> lk(sendmux);
    return SendAll(sock, buf, n);
  }
};

// 单请求处理：槽头校验 → canary 闸门 → 恢复 → 逆预乘 → 编码 → 原子落盘。
void ProcessRequest(ServeCtx* ctx, const WorkItem& item) {
  const ProtoRequest& req = item.req;
  const uint64_t t_all0 = NowUs();

  ProtoDone done{};
  done.magic = kProtoMagicDone;
  done.protocol_version = kProtoVersion;
  done.id = req.id;
  done.slot = req.slot;
  done.frame = req.frame;
  done.rendered_frame = -1;
  done.reason_code = static_cast<uint32_t>(OutReason::kNa);
  done.t_ready_wait_us = req.t_ready_wait_us;
  done.t_capture_raw_us = req.t_capture_raw_us;

  auto finish = [&](uint16_t status) {
    done.status_code = status;
    ctx->SendMsg(&done, sizeof(done));
    ProtoCredit credit{};
    credit.magic = kProtoMagicCredit;
    credit.protocol_version = kProtoVersion;
    credit.slot = req.slot;
    credit.seq = ctx->ring.control->gates[req.slot].seq.load(std::memory_order_relaxed);
    // 槽释放：门 EMPTY（release）+ released_total++（#110 语义；权威流控
    // 仍是随本消息发出的 credit）。
    ctx->ring.control->gates[req.slot].state.store(
        static_cast<uint32_t>(vtx_cef::SlotState::kEmpty), std::memory_order_release);
    ctx->ring.control->released_total.fetch_add(1, std::memory_order_relaxed);
    ctx->SendMsg(&credit, sizeof(credit));
    (void)t_all0;
  };

  if (req.slot >= ctx->slots || req.width != ctx->width || req.height != ctx->height ||
      req.encoder > 2 || req.level < 1 || req.level > 3 ||
      ::strnlen(req.out_path, sizeof(req.out_path)) == 0 ||
      ::strnlen(req.out_path, sizeof(req.out_path)) > kMaxOutPath) {
    finish(static_cast<uint16_t>(OutStatus::kBadRequest));
    return;
  }

  // ---- 槽头校验（magic/版本/run_id/几何）----
  const FrameSlotHeader* h = ctx->ring.SlotHeader(req.slot);
  if (h->magic != vtx_cef::kSlotMagic || h->protocol_version != vtx_cef::kProtocolVersion ||
      h->run_id != ctx->ring.control->run_id || h->width != ctx->width ||
      h->height != ctx->height || h->stride != ctx->width * 4u ||
      h->pixel_format != vtx_cef::kPixBgraPremul) {
    finish(static_cast<uint16_t>(OutStatus::kHeaderInvalid));
    return;
  }

  // ---- 槽数据紧凑拷贝（防御任意 stride；正常即整块 memcpy）----
  const size_t frame_bytes = static_cast<size_t>(ctx->width) * ctx->height * 4;
  std::vector<uint8_t> bgra(frame_bytes);
  const uint8_t* src = ctx->ring.SlotData(req.slot);
  const uint32_t stride = h->stride;
  for (uint32_t y = 0; y < ctx->height; ++y) {
    std::memcpy(bgra.data() + static_cast<size_t>(y) * ctx->width * 4,
                src + static_cast<size_t>(y) * stride, static_cast<size_t>(ctx->width) * 4);
  }

  // ---- v2 双像素 canary 闸门（验证；拒绝绝不落盘）----
  const uint64_t t_dec0 = NowUs();
  const bool tol1 = req.tol1 != 0;
  const GateResult gate =
      vtx_elec::VerifyCanary2(bgra.data(), ctx->width,
                              static_cast<uint32_t>(req.frame), tol1);
  done.t_decode_us = NowUs() - t_dec0;
  done.rendered_frame = static_cast<int32_t>(gate.rendered_frame);
  done.canary_match = gate.accepted
                          ? (gate.exact_match ? static_cast<uint8_t>(CanaryMatch::kExact)
                                              : static_cast<uint8_t>(CanaryMatch::kTol1))
                          : static_cast<uint8_t>(CanaryMatch::kNone);
  if (!gate.accepted) {
    done.reason_code = static_cast<uint32_t>(gate.reason);
    finish(static_cast<uint16_t>(OutStatus::kCanaryReject));
    return;
  }
  done.reason_code = static_cast<uint32_t>(vtx_elec::GateReason::kOk);

  // ---- 双像素恢复（宿主上报原像素优先；否则行 1 邻色）----
  const uint64_t t_res0 = NowUs();
  const char* restore_src = vtx_elec::RestoreCanary2Inplace(
      bgra.data(), ctx->width,
      req.has_orig ? req.orig1 : nullptr, req.has_orig ? req.orig2 : nullptr);
  done.t_restore_us = NowUs() - t_res0;
  done.restore_source = std::strcmp(restore_src, "reported_original") == 0
                            ? 0 : (std::strcmp(restore_src, "neighbor_row1") == 0 ? 1 : 2);

  // ---- SIMD 逆预乘（#111 pixel_ops 正式算子）----
  const uint64_t t_unp0 = NowUs();
  std::vector<uint8_t> rgba(frame_bytes);
  vtx_cef::unpremultiply_bgra_to_rgba(bgra.data(), rgba.data(), frame_bytes / 4);
  done.t_alpha_norm_us = NowUs() - t_unp0;

  // ---- PNG 编码（#111 双后端）----
  const uint64_t t_enc0 = NowUs();
  vtx_cef::PngEncodeOptions opt;
  opt.encoder = static_cast<vtx_cef::PngEncoder>(req.encoder);
  opt.level = req.level;
  opt.spng_filter = static_cast<vtx_cef::SpngFilter>(req.filter);
  std::vector<uint8_t> png;
  if (!vtx_cef::EncodePngRgba(rgba.data(), static_cast<int>(ctx->width),
                              static_cast<int>(ctx->height), opt, &png)) {
    finish(static_cast<uint16_t>(OutStatus::kEncodeError));
    return;
  }
  done.t_png_encode_us = NowUs() - t_enc0;
  done.png_bytes = png.size();
  done.png_crc32 = ::crc32(0L, Z_NULL, 0);
  done.png_crc32 = ::crc32(done.png_crc32, png.data(), png.size());

  // ---- 原子落盘（.tmp.<pid>.<tid> + rename；#111 语义）----
  const uint64_t t_w0 = NowUs();
  if (!vtx_cef::AtomicWriteBytes(std::string(req.out_path), png.data(), png.size())) {
    finish(static_cast<uint16_t>(OutStatus::kIoError));
    return;
  }
  done.t_disk_commit_us = NowUs() - t_w0;
  finish(static_cast<uint16_t>(OutStatus::kOk));
}

void WorkerLoop(ServeCtx* ctx) {
  vtx_cef::PngEncoderGlobalInit();  // 幂等线程安全（见 png_writer.h）
  for (;;) {
    WorkItem item{};
    if (!ctx->QueuePop(&item)) return;  // draining
    ProcessRequest(ctx, item);
  }
}

int RunServe(const std::string& shm_path, const std::string& sock_path, uint32_t slots,
             uint32_t width, uint32_t height, uint32_t threads, uint64_t idle_ms) {
  ServeCtx ctx;
  ctx.slots = slots;
  ctx.width = width;
  ctx.height = height;
  ctx.ring.Attach(shm_path, slots, width, height);
  const uint64_t run_id = ctx.ring.control->run_id;

  ctx.sock = ::socket(AF_UNIX, SOCK_STREAM, 0);
  if (ctx.sock < 0) Die(3, "socket() 失败");
  sockaddr_un addr{};
  addr.sun_family = AF_UNIX;
  if (sock_path.size() >= sizeof(addr.sun_path)) Die(3, "socket 路径过长");
  std::strncpy(addr.sun_path, sock_path.c_str(), sizeof(addr.sun_path) - 1);
  for (int i = 0; i < 200; ++i) {  // Electron server 可能尚在 listen 边缘
    if (::connect(ctx.sock, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == 0) break;
    if (i == 199) Die(3, (std::string("connect 失败: ") + std::strerror(errno)).c_str());
    ::usleep(5000);
  }

  // Hello（建连即发）。
  ProtoHello hello{};
  hello.magic = kProtoMagicHello;
  hello.protocol_version = kProtoVersion;
  hello.pid = ::getpid();
  hello.slots = slots;
  hello.width = width;
  hello.height = height;
  hello.shm_bytes = ctx.ring.total;
  hello.run_id = run_id;
  hello.unpremul_backend = std::stoi(HostnameOfBackend(vtx_cef::UnpremultiplyBackendName()));
  if (!SendAll(ctx.sock, &hello, sizeof(hello))) Die(3, "发送 Hello 失败");

  // 初始 credit ×slots。
  for (uint32_t k = 0; k < slots; ++k) {
    ProtoCredit credit{};
    credit.magic = kProtoMagicCredit;
    credit.protocol_version = kProtoVersion;
    credit.slot = k;
    credit.seq = 0;
    if (!SendAll(ctx.sock, &credit, sizeof(credit))) Die(3, "发送初始 credit 失败");
  }

  for (uint32_t i = 0; i < threads; ++i) ctx.pool.emplace_back(WorkerLoop, &ctx);

  // 主循环：读请求；EOF → 排空退出 0；空闲超时退出 5。
  const uint64_t idle_us = idle_ms * 1000ull;
  uint64_t last_msg = NowUs();
  for (;;) {
    ProtoRequest req{};
    fd_set rf;
    FD_ZERO(&rf);
    FD_SET(ctx.sock, &rf);
    timeval tv{static_cast<time_t>(idle_ms / 1000),
               static_cast<suseconds_t>((idle_ms % 1000) * 1000)};
    const int r = ::select(ctx.sock + 1, &rf, nullptr, nullptr, &tv);
    if (r < 0) {
      if (errno == EINTR) continue;
      break;
    }
    if (r == 0) {
      if (NowUs() - last_msg >= idle_us) {
        std::fprintf(stderr, "✗ 空闲超时（%llums）\n", static_cast<unsigned long long>(idle_ms));
        std::exit(5);
      }
      continue;
    }
    if (!RecvAll(ctx.sock, &req, sizeof(req))) break;  // EOF：优雅收尾
    last_msg = NowUs();
    if (req.magic != kProtoMagicRequest || req.protocol_version != kProtoVersion ||
        req.run_id != run_id) {
      std::exit(4);  // 协议错误（Electron 侧会经 EOF 显形）
    }
    {
      std::lock_guard<std::mutex> lk(ctx.qmux);
      ctx.queue.push_back(WorkItem{req});
    }
    ctx.qcv.notify_one();
  }

  // 排空在途后收尾。
  {
    std::lock_guard<std::mutex> lk(ctx.qmux);
    ctx.draining.store(true);
  }
  ctx.qcv.notify_all();
  for (auto& t : ctx.pool) t.join();
  ::close(ctx.sock);
  return 0;
}

}  // namespace

// ---------------------------------------------------------------- bench 模式
namespace {

// 1080p 内容类（镜像 #111 pixel_ops_selftest 的四类）生成到 BGRA（不透明，
// 预乘==直通；外加一类混合 alpha 度量逆预乘成本）。
std::vector<uint8_t> MakeContentClass(const char* kind, uint32_t w, uint32_t h) {
  std::vector<uint8_t> bgra(static_cast<size_t>(w) * h * 4);
  const std::string k = kind;
  uint32_t lcg = 0x12345678u;
  auto next = [&] { lcg = lcg * 1664525u + 1013904223u; return lcg >> 16; };
  for (uint32_t y = 0; y < h; ++y) {
    for (uint32_t x = 0; x < w; ++x) {
      uint8_t* p = &bgra[(static_cast<size_t>(y) * w + x) * 4];
      if (k == "flat") {
        p[0] = 0x66; p[1] = 0x99; p[2] = 0xCC; p[3] = 0xFF;
      } else if (k == "stripes") {
        const uint8_t v = ((x / 8) & 1) ? 0xF0 : 0x20;
        p[0] = v; p[1] = static_cast<uint8_t>(v ^ 0x55); p[2] = static_cast<uint8_t>(255 - v);
        p[3] = 0xFF;
      } else if (k == "gradient") {
        const double dx = (x + 0.5) / w - 0.5, dy = (y + 0.5) / h - 0.5;
        const double r = std::min(1.0, std::sqrt(dx * dx + dy * dy) * 2.0);
        p[0] = static_cast<uint8_t>(255 * (1 - r));       // B：径向蓝
        p[1] = static_cast<uint8_t>(255 * r);             // G：径向绿
        p[2] = static_cast<uint8_t>(128 + 127 * std::sin(6.28 * r));
        p[3] = 0xFF;
      } else if (k == "noise") {
        p[0] = static_cast<uint8_t>(next() & 0xFF);
        p[1] = static_cast<uint8_t>(next() & 0xFF);
        p[2] = static_cast<uint8_t>(next() & 0xFF);
        p[3] = 0xFF;
      } else {  // mixed_alpha：半透明棋盘（逆预乘 gather 路径）
        p[0] = static_cast<uint8_t>((x * 7) & 0xFF);
        p[1] = static_cast<uint8_t>((y * 5) & 0xFF);
        p[2] = 0x80;
        p[3] = static_cast<uint8_t>(((x / 16 + y / 16) & 1) ? 0xFF : 0x80);
      }
    }
  }
  return bgra;
}

struct BenchConf {
  const char* name;
  vtx_cef::PngEncodeOptions opt;
};

int RunBench(bool json) {
  const uint32_t W = 1920, H = 1080;
  vtx_cef::PngEncoderGlobalInit();
  const std::vector<std::string> classes = {"flat", "stripes", "gradient", "noise", "mixed_alpha"};
  const std::vector<BenchConf> confs = {
      {"spng_l1_sub_up", {vtx_cef::PngEncoder::kSpng, 1, vtx_cef::SpngFilter::kSubUp}},
      {"spng_l1_none", {vtx_cef::PngEncoder::kSpng, 1, vtx_cef::SpngFilter::kNone}},
      {"spng_l3_sub_up", {vtx_cef::PngEncoder::kSpng, 3, vtx_cef::SpngFilter::kSubUp}},
      {"fpng", {vtx_cef::PngEncoder::kFpng, 1, vtx_cef::SpngFilter::kSubUp}},
      {"zlib", {vtx_cef::PngEncoder::kZlib, 1, vtx_cef::SpngFilter::kSubUp}},
  };

  if (!json) std::printf("# 1080p 编码器选型基准（单核=30 次中位；4 线程=各 120 帧墙钟；解码=20 次中位）\n");
  std::string out = "{\"schema\":\"vtx.elec.output_helper.bench/1\",\"width\":" +
                    std::to_string(W) + ",\"height\":" + std::to_string(H) +
                    ",\"unpremul_backend\":\"" + vtx_cef::UnpremultiplyBackendName() +
                    "\",\"rows\":[";
  bool first_row = true;
  for (const auto& cls : classes) {
    auto bgra = MakeContentClass(cls.c_str(), W, H);
    std::vector<uint8_t> rgba(bgra.size());
    // 单核逆预乘成本（该内容类）。
    const int REPS = 30;
    std::vector<uint64_t> unp_times;
    for (int i = 0; i < REPS; ++i) {
      const uint64_t t0 = NowUs();
      vtx_cef::unpremultiply_bgra_to_rgba(bgra.data(), rgba.data(), bgra.size() / 4);
      unp_times.push_back(NowUs() - t0);
    }
    std::sort(unp_times.begin(), unp_times.end());

    for (const auto& conf : confs) {
      // 单核 encode。
      std::vector<uint64_t> enc_times;
      std::vector<uint8_t> png;
      for (int i = 0; i < REPS; ++i) {
        const uint64_t t0 = NowUs();
        if (!vtx_cef::EncodePngRgba(rgba.data(), W, H, conf.opt, &png)) Die(4, "编码失败");
        enc_times.push_back(NowUs() - t0);
      }
      std::sort(enc_times.begin(), enc_times.end());
      const uint64_t enc_med = enc_times[REPS / 2];
      const uint64_t unp_med = unp_times[REPS / 2];

      // 4 线程并发吞吐（每线程 30 帧，墙钟）。
      std::atomic<size_t> failures{0};
      const uint64_t tp0 = NowUs();
      std::vector<std::thread> ts;
      for (int t = 0; t < 4; ++t) {
        ts.emplace_back([&] {
          std::vector<uint8_t> rgba_t(bgra.size());
          std::vector<uint8_t> png_t;
          for (int i = 0; i < 30; ++i) {
            vtx_cef::unpremultiply_bgra_to_rgba(bgra.data(), rgba_t.data(), bgra.size() / 4);
            if (!vtx_cef::EncodePngRgba(rgba_t.data(), W, H, conf.opt, &png_t)) ++failures;
          }
        });
      }
      for (auto& t : ts) t.join();
      const uint64_t tp_us = NowUs() - tp0;
      const double fps4 = failures == 0 ? 120.0 / (tp_us / 1e6) : 0.0;

      // 下游解码成本（libspng 解码本编码器产物为 RGBA8——对本仓 RGBA8
      // 非隔行产物为恒等转换；解码缓冲按帧字节数分配，而非按 PNG 体积）。
      uint64_t dec_med = 0;
      {
        std::vector<uint8_t> decoded(static_cast<size_t>(W) * H * 4);
        std::vector<uint64_t> times;
        for (int i = 0; i < 20; ++i) {
          const uint64_t t0 = NowUs();
          spng_ctx* sp = spng_ctx_new(0);
          spng_set_png_buffer(sp, png.data(), png.size());
          size_t outsz = 0;
          if (spng_decoded_image_size(sp, SPNG_FMT_RGBA8, &outsz) != 0 ||
              outsz > decoded.size() ||
              spng_decode_image(sp, decoded.data(), outsz, SPNG_FMT_RGBA8, 0) != 0) {
            spng_ctx_free(sp);
            Die(4, "解码基准失败（产物非合法 PNG？）");
          }
          spng_ctx_free(sp);
          times.push_back(NowUs() - t0);
        }
        std::sort(times.begin(), times.end());
        dec_med = times[10];
      }

      if (!first_row) out += ",";
      first_row = false;
      out += "{\"class\":\"" + cls + "\",\"encoder\":\"" + conf.name +
             "\",\"unpremul_med_us\":" + std::to_string(unp_med) +
             ",\"encode_med_us\":" + std::to_string(enc_med) +
             ",\"png_bytes\":" + std::to_string(png.size()) +
             ",\"fps_4thread\":" + std::string(failures == 0 ? std::to_string(fps4) : "0") +
             ",\"decode_med_us\":" + std::to_string(dec_med) + "}";
      if (!json) {
        std::printf("%-12s %-14s unpmed=%6.2fms encmed=%7.2fms size=%8zuB 4thr=%7.1ffps dec=%6.2fms\n",
                    cls.c_str(), conf.name, unp_med / 1e3, enc_med / 1e3, png.size(), fps4,
                    dec_med / 1e3);
      }
    }
  }
  out += "]}";
  std::printf("%s\n", out.c_str());
  return 0;
}

// ---------------------------------------------------------------- check-header
int RunCheckHeader(const std::string& path) {
  std::FILE* f = std::fopen(path.c_str(), "rb");
  if (f == nullptr) Die(2, "打开槽头文件失败");
  FrameSlotHeader h{};
  if (std::fread(&h, 1, sizeof(h), f) != sizeof(h)) Die(2, "槽头文件不足 128B");
  std::fclose(f);
  std::printf("{\"magic\":\"0x%08X\",\"protocol_version\":%u,\"status_code\":%u,"
              "\"run_id\":%llu,\"document_gen\":%u,\"request_id\":%u,"
              "\"target_frame_index\":%d,\"canary_frame_index\":%d,"
              "\"width\":%u,\"height\":%u,\"stride\":%u,\"pixel_format\":%u,"
              "\"color_space\":%u,\"rejected_paints\":%u,"
              "\"t_ready_wait_us\":%llu,\"t_capture_raw_us\":%llu,"
              "\"t_alpha_norm_us\":%llu,\"t_png_encode_us\":%llu,"
              "\"t_disk_commit_us\":%llu}\n",
              h.magic, h.protocol_version, h.status_code,
              static_cast<unsigned long long>(h.run_id), h.document_gen, h.request_id,
              h.target_frame_index, h.canary_frame_index, h.width, h.height, h.stride,
              h.pixel_format, h.color_space, h.rejected_paints,
              static_cast<unsigned long long>(h.t_ready_wait_us),
              static_cast<unsigned long long>(h.t_capture_raw_us),
              static_cast<unsigned long long>(h.t_alpha_norm_us),
              static_cast<unsigned long long>(h.t_png_encode_us),
              static_cast<unsigned long long>(h.t_disk_commit_us));
  return 0;
}

}  // namespace

int main(int argc, char** argv) {
  ::signal(SIGPIPE, SIG_IGN);
  std::string shm, sock, check_header;
  uint32_t slots = 4, width = 1920, height = 1080, threads = 4;
  uint64_t idle_ms = 120000;
  bool selftest = false, selftest_json = false, bench = false, bench_json = false;
  for (int i = 1; i < argc; ++i) {
    const std::string a = argv[i];
    auto val = [&]() -> std::string {
      const auto pos = a.find('=');
      return pos == std::string::npos ? "" : a.substr(pos + 1);
    };
    if (a == "--selftest") selftest = true;
    else if (a == "--selftest-json") { selftest = true; selftest_json = true; }
    else if (a == "--bench") bench = true;
    else if (a == "--bench-json") { bench = true; bench_json = true; }
    else if (a.rfind("--check-header=", 0) == 0) check_header = val();
    else if (a.rfind("--shm=", 0) == 0) shm = val();
    else if (a.rfind("--sock=", 0) == 0) sock = val();
    else if (a.rfind("--slots=", 0) == 0) slots = std::stoul(val());
    else if (a.rfind("--width=", 0) == 0) width = std::stoul(val());
    else if (a.rfind("--height=", 0) == 0) height = std::stoul(val());
    else if (a.rfind("--threads=", 0) == 0) threads = std::stoul(val());
    else if (a.rfind("--idle-timeout-ms=", 0) == 0) idle_ms = std::stoull(val());
    else Die(2, (std::string("未知参数：") + a).c_str());
  }
  if (selftest) return vtx_elec::Canary2Selftest(!selftest_json, selftest_json);
  if (bench) return RunBench(bench_json);
  if (!check_header.empty()) return RunCheckHeader(check_header);
  if (shm.empty() || sock.empty()) {
    Die(2, "serve 模式需要 --shm= 与 --sock=（或 --selftest/--bench/--check-header=）");
  }
  return RunServe(shm, sock, slots, width, height, threads, idle_ms);
}
