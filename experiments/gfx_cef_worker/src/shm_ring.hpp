// shm_ring.hpp — OnPaint 4-Slot POSIX 共享内存环形池（issue #110 CEF-02a）。
//
// 布局（小端，偏移表权威镜像 = IPC_PROTOCOL.md + cef_client.py）：
//   [RingControl 384B（64 对齐）] [Slot0][Slot1][Slot2][Slot3]
//   Slot_i = [FrameSlotHeader 128B（工单冻结结构）][frame_bytes 像素数据]
//
// 槽位状态机（单写者=Worker 主捕获路径，单读者=宿主消费端）：
//   EMPTY --Claim(写者)--> WRITING --Publish(写者,release)--> READY
//   READY --Release(读者,release)--> EMPTY
//   WRITING --Abandon(写者,错误路径)--> EMPTY
// 同步语义（acquire/release 配对，跨进程共享原子）：
//   发布：先写槽头+像素数据，后 release-store state=READY —— 读者
//   acquire-load 见 READY 即看到完整帧；释放同理。
//
// 名字解绑设计（工单核心考察点）：
//   shm_open(O_CREAT|O_EXCL) → ftruncate → mmap → 立即尝试 shm_unlink。
//   解绑成功后名字消失，实体在最后 fd/映射释放后由内核回收，不依赖
//   退出/信号处理器。创建至解绑间被 SIGKILL 或解绑失败仍可能留名；
//   当前未检查解绑返回值，不能保证任何时刻零残留。消费端经 Unix
//   Socket SCM_RIGHTS 收到同一 fd（“映射同一 shm_open 句柄”）。
//   满溢（背压）：4 槽全满时写者暂停（Claim 阻塞轮询，不另分配内存，
//   RSS 由固定池界定），等待计时累计入 backpressure_wait_us。
#ifndef GFX_CEF_WORKER_SHM_RING_H_
#define GFX_CEF_WORKER_SHM_RING_H_

#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#include <atomic>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <memory>
#include <string>

#include "ipc_protocol.hpp"

namespace vtx_cef {

inline constexpr uint32_t kRingSlots = 4;  // 工单冻结：RING_SLOTS = 4

enum class SlotState : uint32_t {
  kEmpty = 0,
  kWriting = 1,
  kReady = 2,
};

// 每槽专享缓存行的生命周期门（与帧数据行分离，杜绝伪共享）。
struct alignas(64) SlotGate {
  std::atomic<uint32_t> state;  // SlotState
  std::atomic<uint32_t> seq;    // 该槽累计发布序号（发布时 +1，从 1 起）
  char pad[56];
};
static_assert(sizeof(SlotGate) == 64);

// 环控制块：几何 + 全局账目 + 4 槽门。创建者填充后以 release-store
// layout_ready=kRingMagic 发布；消费端 acquire 自旋等待该值。
struct alignas(64) RingControl {
  std::atomic<uint32_t> layout_ready;  // 0=初始化中；kRingMagic=就绪
  uint32_t magic;                      // kRingMagic
  uint16_t protocol_version;           // kProtocolVersion
  uint16_t ring_slots;                 // kRingSlots
  uint32_t max_width;
  uint32_t max_height;
  uint32_t frame_bytes;   // max_width*max_height*4（槽数据字节数）
  uint32_t slot_bytes;    // sizeof(FrameSlotHeader) + frame_bytes
  uint64_t run_id;        // 本次运行的陈旧帧判别基准
  int32_t owner_pid;
  uint32_t reserved0;
  std::atomic<uint64_t> published_total;
  std::atomic<uint64_t> released_total;
  std::atomic<uint64_t> backpressure_wait_us_total;
  char pad_to_gates[128 - 72];  // 门数组 128 起整缓存行（见下方断言）
  SlotGate gates[kRingSlots];
};
static_assert(offsetof(RingControl, run_id) == 32);
static_assert(offsetof(RingControl, published_total) == 48);
static_assert(offsetof(RingControl, gates) == 128, "门数组须整缓存行起始");
static_assert(sizeof(RingControl) == 384);

inline uint64_t NowUs() {
  struct timespec ts;
  ::clock_gettime(CLOCK_MONOTONIC, &ts);
  return static_cast<uint64_t>(ts.tv_sec) * 1000000ull +
         static_cast<uint64_t>(ts.tv_nsec) / 1000ull;
}

inline uint64_t AlignUp64(uint64_t v) { return (v + 63ull) & ~63ull; }

// 环内布局几何（创建/附着共用；不 mmap，纯计算）。
struct RingGeometry {
  uint64_t control_bytes = 0;   // = sizeof(RingControl)
  uint64_t slot_stride = 0;     // AlignUp64(sizeof(FrameSlotHeader)+frame_bytes)
  uint64_t total_bytes = 0;
  uint32_t frame_bytes = 0;

  static RingGeometry ForSize(uint32_t max_width, uint32_t max_height) {
    RingGeometry g;
    g.frame_bytes = max_width * max_height * 4u;
    g.control_bytes = sizeof(RingControl);
    g.slot_stride = AlignUp64(static_cast<uint64_t>(sizeof(FrameSlotHeader)) +
                              g.frame_bytes);
    g.total_bytes = g.control_bytes + g.slot_stride * kRingSlots;
    return g;
  }
  uint64_t SlotOffset(uint32_t index) const {
    return control_bytes + slot_stride * index;
  }
  uint64_t DataOffset(uint32_t index) const {
    return SlotOffset(index) + sizeof(FrameSlotHeader);
  }
};

// ---------------------------------------------------------------- 写者（Worker 侧）

class ShmRingWriter {
 public:
  // 创建匿名化环形池：名字仅存活至 mmap 成功后立即 shm_unlink。
  // 失败时 *err 载明原因；返回 nullptr。
  static std::unique_ptr<ShmRingWriter> Create(uint32_t max_width,
                                               uint32_t max_height,
                                               uint64_t run_id, std::string* err);

  ~ShmRingWriter();

  ShmRingWriter(const ShmRingWriter&) = delete;
  ShmRingWriter& operator=(const ShmRingWriter&) = delete;

  // 申请空槽（EMPTY→WRITING）。全满时背压等待至 timeout_us：
  //   返回 >=0 槽号；-1 = 超时；-2 = 致命错误（不该发生）。
  // *backpressure_wait_us 恒为本调用实际等待时长。
  int ClaimSlot(uint64_t timeout_us, uint64_t* backpressure_wait_us);

  FrameSlotHeader* SlotHeader(uint32_t index);
  uint8_t* SlotData(uint32_t index);

  // 发布（WRITING→READY，release；seq++；published_total++）。
  void PublishSlot(uint32_t index);
  // 弃置（WRITING→EMPTY；错误路径，不计数发布）。
  void AbandonSlot(uint32_t index);

  const RingControl* control() const { return control_; }
  int fd() const { return fd_; }
  uint64_t run_id() const { return run_id_; }
  const RingGeometry& geometry() const { return geo_; }
  const void* base() const { return base_; }
  uint64_t total_bytes() const { return geo_.total_bytes; }

  // 诊断/自检：把整环字节落盘（跨语言协议锁定用；不参与生产路径）。
  bool DumpToFile(const std::string& path) const;

 private:
  ShmRingWriter() = default;
  void* base_ = nullptr;
  uint64_t total_bytes_ = 0;
  RingControl* control_ = nullptr;
  RingGeometry geo_;
  int fd_ = -1;
  uint64_t run_id_ = 0;
};

// ---------------------------------------------------------------- 读者（消费端）

class ShmRingView {
 public:
  // 附着到 SCM_RIGHTS 收到的 fd（内部 dup，不夺走调用方所有权）。
  static std::unique_ptr<ShmRingView> Attach(int shm_fd, std::string* err);

  ~ShmRingView();

  ShmRingView(const ShmRingView&) = delete;
  ShmRingView& operator=(const ShmRingView&) = delete;

  // 等任一槽 READY（自旋+让出）；返回槽号，-1 = 超时。
  int WaitReady(uint64_t timeout_us);

  // 槽头校验：magic/版本/run_id 与环一致 → true（陈旧/损坏帧拒收）。
  bool ValidateHeader(uint32_t index) const;

  const FrameSlotHeader* SlotHeader(uint32_t index) const;
  const uint8_t* SlotData(uint32_t index) const;

  // 释放槽（READY→EMPTY，release；released_total++）。
  void Release(uint32_t index);

  const RingControl* control() const { return control_; }
  const RingGeometry& geometry() const { return geo_; }

 private:
  ShmRingView() = default;
  void* base_ = nullptr;
  uint64_t total_bytes_ = 0;
  RingControl* control_ = nullptr;
  RingGeometry geo_;
  int fd_ = -1;
};

// ---------------------------------------------------------------- 内联实现

inline std::unique_ptr<ShmRingWriter> ShmRingWriter::Create(
    uint32_t max_width, uint32_t max_height, uint64_t run_id,
    std::string* err) {
  if (max_width == 0 || max_height == 0 ||
      max_width > 16384 || max_height > 16384) {
    *err = "非法槽容量 " + std::to_string(max_width) + "x" +
           std::to_string(max_height);
    return nullptr;
  }
  const RingGeometry geo = RingGeometry::ForSize(max_width, max_height);

  // 匿名化命名：pid + 纳秒 + 指针熵 → 碰撞概率可忽略；O_EXCL 命中即视作
  // 陈旧残骸（先解绑再重试一次）。
  std::string name;
  int fd = -1;
  for (int attempt = 0; attempt < 2; ++attempt) {
    struct timespec ts;
    ::clock_gettime(CLOCK_REALTIME, &ts);
    const uint32_t cookie = static_cast<uint32_t>(ts.tv_nsec) ^
                            static_cast<uint32_t>(reinterpret_cast<uintptr_t>(&geo));
    name = "/vtx_shm_" + std::to_string(static_cast<long>(::getpid())) + "_" +
           std::to_string(cookie);
    fd = ::shm_open(name.c_str(), O_CREAT | O_EXCL | O_RDWR, 0600);
    if (fd >= 0) {
      break;
    }
    if (attempt == 0) {
      ::shm_unlink(name.c_str());  // 唯一命名碰撞 → 必为残骸，清扫
    }
  }
  if (fd < 0) {
    *err = std::string("shm_open 失败: ") + std::strerror(errno);
    return nullptr;
  }

  // ===== 零残留关键序：先 ftruncate+mmap 成功，再立即解绑名字。=====
  if (::ftruncate(fd, static_cast<off_t>(geo.total_bytes)) != 0) {
    *err = std::string("ftruncate 失败: ") + std::strerror(errno);
    ::shm_unlink(name.c_str());
    ::close(fd);
    return nullptr;
  }
  void* base = ::mmap(nullptr, geo.total_bytes, PROT_READ | PROT_WRITE,
                      MAP_SHARED, fd, 0);
  if (base == MAP_FAILED) {
    *err = std::string("mmap 失败: ") + std::strerror(errno);
    ::shm_unlink(name.c_str());
    ::close(fd);
    return nullptr;
  }
  // 名字使命完成（消费端经 SCM_RIGHTS 拿 fd，不经名字）：解绑成功后
  // 名字消失，内核随最后 fd/映射释放回收对象；此处未检查解绑失败。
  ::shm_unlink(name.c_str());

  auto* ring = new ShmRingWriter();
  ring->base_ = base;
  ring->total_bytes_ = geo.total_bytes;
  ring->control_ = static_cast<RingControl*>(base);
  ring->geo_ = geo;
  ring->fd_ = fd;
  ring->run_id_ = run_id;

  auto* c = ring->control_;
  c->layout_ready.store(0, std::memory_order_relaxed);
  c->magic = kRingMagic;
  c->protocol_version = kProtocolVersion;
  c->ring_slots = kRingSlots;
  c->max_width = max_width;
  c->max_height = max_height;
  c->frame_bytes = geo.frame_bytes;
  c->slot_bytes = static_cast<uint32_t>(geo.slot_stride);
  c->run_id = run_id;
  c->owner_pid = static_cast<int32_t>(::getpid());
  c->reserved0 = 0;
  c->published_total.store(0, std::memory_order_relaxed);
  c->released_total.store(0, std::memory_order_relaxed);
  c->backpressure_wait_us_total.store(0, std::memory_order_relaxed);
  for (uint32_t i = 0; i < kRingSlots; ++i) {
    c->gates[i].state.store(static_cast<uint32_t>(SlotState::kEmpty),
                            std::memory_order_relaxed);
    c->gates[i].seq.store(0, std::memory_order_relaxed);
  }
  std::atomic_thread_fence(std::memory_order_release);
  c->layout_ready.store(kRingMagic, std::memory_order_release);
  return std::unique_ptr<ShmRingWriter>(ring);
}

inline ShmRingWriter::~ShmRingWriter() {
  if (base_ != nullptr) {
    ::munmap(base_, total_bytes_);
  }
  if (fd_ >= 0) {
    ::close(fd_);  // 解绑成功后，最后 fd/映射释放即对象消亡
  }
}

inline int ShmRingWriter::ClaimSlot(uint64_t timeout_us,
                                    uint64_t* backpressure_wait_us) {
  const uint64_t t0 = NowUs();
  uint64_t waited = 0;
  *backpressure_wait_us = 0;
  for (;;) {
    for (uint32_t i = 0; i < kRingSlots; ++i) {
      uint32_t expected = static_cast<uint32_t>(SlotState::kEmpty);
      if (control_->gates[i].state.compare_exchange_strong(
              expected, static_cast<uint32_t>(SlotState::kWriting),
              std::memory_order_acquire, std::memory_order_relaxed)) {
        if (waited > 0) {  // 背压等待入账（成功路径同样累计）
          control_->backpressure_wait_us_total.fetch_add(
              waited, std::memory_order_relaxed);
        }
        *backpressure_wait_us = waited;
        return static_cast<int>(i);
      }
    }
    if (waited >= timeout_us) {
      control_->backpressure_wait_us_total.fetch_add(
          waited, std::memory_order_relaxed);
      *backpressure_wait_us = waited;
      return -1;
    }
    ::usleep(200);
    waited = NowUs() - t0;
  }
}

inline FrameSlotHeader* ShmRingWriter::SlotHeader(uint32_t index) {
  return reinterpret_cast<FrameSlotHeader*>(
      static_cast<uint8_t*>(base_) + geo_.SlotOffset(index));
}

inline uint8_t* ShmRingWriter::SlotData(uint32_t index) {
  return static_cast<uint8_t*>(base_) + geo_.DataOffset(index);
}

inline void ShmRingWriter::PublishSlot(uint32_t index) {
  auto& gate = control_->gates[index];
  gate.seq.fetch_add(1, std::memory_order_relaxed);
  control_->published_total.fetch_add(1, std::memory_order_relaxed);
  gate.state.store(static_cast<uint32_t>(SlotState::kReady),
                   std::memory_order_release);
}

inline void ShmRingWriter::AbandonSlot(uint32_t index) {
  control_->gates[index].state.store(static_cast<uint32_t>(SlotState::kEmpty),
                                     std::memory_order_release);
}

inline bool ShmRingWriter::DumpToFile(const std::string& path) const {
  std::FILE* fh = std::fopen(path.c_str(), "wb");
  if (fh == nullptr) {
    return false;
  }
  const size_t wrote = std::fwrite(base_, 1, total_bytes_, fh);
  const int closed = std::fclose(fh);
  return wrote == total_bytes_ && closed == 0;
}

inline std::unique_ptr<ShmRingView> ShmRingView::Attach(int shm_fd,
                                                        std::string* err) {
  const int fd = ::dup(shm_fd);
  if (fd < 0) {
    *err = std::string("dup(shm fd) 失败: ") + std::strerror(errno);
    return nullptr;
  }
  struct stat st {};
  if (::fstat(fd, &st) != 0 || st.st_size < static_cast<off_t>(sizeof(RingControl))) {
    *err = "shm fd 无效（fstat 失败或尺寸非正）";
    ::close(fd);
    return nullptr;
  }
  const uint64_t total = static_cast<uint64_t>(st.st_size);
  void* base = ::mmap(nullptr, total, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
  if (base == MAP_FAILED) {
    *err = std::string("消费端 mmap 失败: ") + std::strerror(errno);
    ::close(fd);
    return nullptr;
  }
  auto* view = new ShmRingView();
  view->base_ = base;
  view->total_bytes_ = total;
  view->control_ = static_cast<RingControl*>(base);
  // 等创建方发布布局（layout_ready release ↔ 此处 acquire）。
  const uint64_t t0 = NowUs();
  while (view->control_->layout_ready.load(std::memory_order_acquire) !=
         kRingMagic) {
    if (NowUs() - t0 > 5'000'000ull) {
      *err = "环布局就绪超时（layout_ready 未发布）";
      ::close(fd);
      delete view;
      return nullptr;
    }
    ::usleep(100);
  }
  if (view->control_->magic != kRingMagic ||
      view->control_->protocol_version != kProtocolVersion) {
    *err = "环控制块 magic/版本不符";
    ::close(fd);
    delete view;
    return nullptr;
  }
  view->geo_ = RingGeometry::ForSize(view->control_->max_width,
                                     view->control_->max_height);
  uint64_t expected_allocation = view->geo_.total_bytes;
#if defined(__APPLE__)
  // Darwin POSIX SHM fstat reports the VM-page-rounded allocation, while the
  // wire geometry remains byte-exact and shared with Linux/Python.
  const uint64_t page = static_cast<uint64_t>(::sysconf(_SC_PAGESIZE));
  expected_allocation = ((expected_allocation + page - 1) / page) * page;
#endif
  if (expected_allocation != total) {
    *err = "环尺寸与几何不符（控制块字段损坏？）";
    ::close(fd);
    delete view;
    return nullptr;
  }
  view->fd_ = fd;
  return std::unique_ptr<ShmRingView>(view);
}

inline ShmRingView::~ShmRingView() {
  if (base_ != nullptr) {
    ::munmap(base_, total_bytes_);
  }
  if (fd_ >= 0) {
    ::close(fd_);
  }
}

inline int ShmRingView::WaitReady(uint64_t timeout_us) {
  const uint64_t t0 = NowUs();
  for (;;) {
    for (uint32_t i = 0; i < kRingSlots; ++i) {
      if (control_->gates[i].state.load(std::memory_order_acquire) ==
          static_cast<uint32_t>(SlotState::kReady)) {
        return static_cast<int>(i);
      }
    }
    if (NowUs() - t0 > timeout_us) {
      return -1;
    }
    ::usleep(100);
  }
}

inline bool ShmRingView::ValidateHeader(uint32_t index) const {
  const FrameSlotHeader* h = SlotHeader(index);
  return h->magic == kSlotMagic && h->protocol_version == kProtocolVersion &&
         h->run_id == control_->run_id;
}

inline const FrameSlotHeader* ShmRingView::SlotHeader(uint32_t index) const {
  return reinterpret_cast<const FrameSlotHeader*>(
      static_cast<const uint8_t*>(base_) + geo_.SlotOffset(index));
}

inline const uint8_t* ShmRingView::SlotData(uint32_t index) const {
  return static_cast<const uint8_t*>(base_) + geo_.DataOffset(index);
}

inline void ShmRingView::Release(uint32_t index) {
  control_->gates[index].state.store(static_cast<uint32_t>(SlotState::kEmpty),
                                     std::memory_order_release);
  control_->released_total.fetch_add(1, std::memory_order_relaxed);
}

}  // namespace vtx_cef

#endif  // GFX_CEF_WORKER_SHM_RING_H_
