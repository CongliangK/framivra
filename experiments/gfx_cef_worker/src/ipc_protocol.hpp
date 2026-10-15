// ipc_protocol.hpp — 定长二进制 IPC 协议（issue #110 CEF-02a）。
//
// 权威规格：experiments/gfx_cef_worker/IPC_PROTOCOL.md（偏移表/字节序/语义）。
// 设计要点：
//   - 全部消息定长：请求 64B、应答（FrameSlotHeader 原生布局）128B、
//     槽通知 32B、Hello 64B。字节序统一小端（目标面 linux64/x86-64）。
//   - FrameSlotHeader 为工单冻结结构（字段/顺序逐字实现），alignas(64)；
//     字段自然对齐后共 88 字节，结构体大小按 64 对齐取 128 —— 工单原文
//     「回传 64 字节 FrameSlotHeader」与冻结字段集（88 字节）算术冲突，
//     本实现以字段集为准：应答 = 完整 FrameSlotHeader（128B 原生布局），
//     偏差已在 IPC_PROTOCOL.md §0 记录。
//   - 校验/版本面：每消息自带 magic + protocol_version；槽头另有 run_id
//     （跨 run 陈旧帧检测）与 request_id/seq（序号）。
#ifndef GFX_CEF_WORKER_IPC_PROTOCOL_H_
#define GFX_CEF_WORKER_IPC_PROTOCOL_H_

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <type_traits>

namespace vtx_cef {

// ---------------------------------------------------------------- 常量面

inline constexpr uint32_t kSlotMagic = 0x56545846u;    // "VTXF"（工单冻结值）
inline constexpr uint32_t kRingMagic = 0x56545852u;    // "VTXR" 环控制块
inline constexpr uint32_t kRequestMagic = 0x56545851u; // "VTXQ" 帧请求
inline constexpr uint32_t kNoticeMagic = 0x5654584E;   // "VTXN" 槽索引通知
inline constexpr uint32_t kHelloMagic = 0x56545848u;   // "VTXH" 连接问候
inline constexpr uint16_t kProtocolVersion = 1;

// FrameSlotHeader::status_code（工单冻结语义）。
enum StatusCode : uint16_t {
  kStatusOk = 0,
  kStatusTimeout = 1,
  kStatusSizeMismatch = 2,
  kStatusJsErr = 3,
};

// FrameSlotHeader::pixel_format（工单冻结语义）。
enum PixelFormat : uint8_t {
  kPixBgraPremul = 1,   // OnPaint 原始面（模式 2 槽内即此格式）
  kPixRgbaStraight = 2, // 逆预乘直通面（模式 1 PNG 编码输入）
};

inline constexpr uint8_t kColorSpaceSrgb = 1;

// FrameRequest::flags。
enum RequestFlag : uint16_t {
  kFlagWantSlot = 1u << 0,  // 模式 2：像素经 SHM 槽交付（否则模式 1 落盘）
  kFlagDumpRaw = 1u << 1,   // 诊断：同时把交付字节写 <out_png_path>.raw
};

// ---------------------------------------------------------------- 槽头（工单冻结）

// 4-Slot 环形池每槽头部：64 字节对齐的帧描述/计时控制块。
// 生命周期状态机不在本结构（工单字段集无空闲字），见 shm_ring.hpp 的
// SlotGate（WRITING/READY/EMPTY 发布语义）。
struct alignas(64) FrameSlotHeader {
  uint32_t magic;               // 0x56545846 ("VTXF")
  uint16_t protocol_version;    // 1
  uint16_t status_code;         // 0=OK, 1=TIMEOUT, 2=SIZE_MISMATCH, 3=JS_ERR
  uint64_t run_id;
  uint32_t document_gen;        // 每次导航自增
  uint32_t request_id;          // 每次帧请求自增
  int32_t target_frame_index;   // 请求的帧号 f
  int32_t canary_frame_index;   // 从 (0,0) 像素解码核实的帧号
  uint32_t width;
  uint32_t height;
  uint32_t stride;              // 槽内实测行字节步长（紧凑 = width*4）
  uint8_t pixel_format;         // 1 = BGRA_PREMUL, 2 = RGBA_STRAIGHT
  uint8_t color_space;          // 1 = sRGB
  uint16_t rejected_paints;     // 本帧请求期间拒绝的陈旧 OnPaint 次数
  uint64_t t_ready_wait_us;     // 导航→加载落定等待
  uint64_t t_capture_raw_us;    // OnPaint→槽/暂存紧凑拷贝
  uint64_t t_alpha_norm_us;     // SIMD 逆预乘（模式 1）
  uint64_t t_png_encode_us;     // PNG 编码（模式 1）
  uint64_t t_disk_commit_us;    // .tmp+rename 原子落盘（模式 1）
};

// 偏移表 = 线上规范（Python 镜像 cef_client.py 必须与此逐字段一致；
// tests/test_cef_worker_shm.py 用 shm_ring_selftest --dump-ring 跨语言锁定）。
static_assert(sizeof(FrameSlotHeader) == 128, "槽头 64 对齐后定长 128");
static_assert(offsetof(FrameSlotHeader, magic) == 0);
static_assert(offsetof(FrameSlotHeader, protocol_version) == 4);
static_assert(offsetof(FrameSlotHeader, status_code) == 6);
static_assert(offsetof(FrameSlotHeader, run_id) == 8);
static_assert(offsetof(FrameSlotHeader, document_gen) == 16);
static_assert(offsetof(FrameSlotHeader, request_id) == 20);
static_assert(offsetof(FrameSlotHeader, target_frame_index) == 24);
static_assert(offsetof(FrameSlotHeader, canary_frame_index) == 28);
static_assert(offsetof(FrameSlotHeader, width) == 32);
static_assert(offsetof(FrameSlotHeader, height) == 36);
static_assert(offsetof(FrameSlotHeader, stride) == 40);
static_assert(offsetof(FrameSlotHeader, pixel_format) == 44);
static_assert(offsetof(FrameSlotHeader, color_space) == 45);
static_assert(offsetof(FrameSlotHeader, rejected_paints) == 46);
static_assert(offsetof(FrameSlotHeader, t_ready_wait_us) == 48);
static_assert(offsetof(FrameSlotHeader, t_capture_raw_us) == 56);
static_assert(offsetof(FrameSlotHeader, t_alpha_norm_us) == 64);
static_assert(offsetof(FrameSlotHeader, t_png_encode_us) == 72);
static_assert(offsetof(FrameSlotHeader, t_disk_commit_us) == 80);
static_assert(std::is_standard_layout_v<FrameSlotHeader>);

// ---------------------------------------------------------------- 请求（宿主→Worker，64B）

struct FrameRequest {
  uint32_t magic;            // kRequestMagic
  uint16_t protocol_version; // kProtocolVersion
  uint16_t flags;            // RequestFlag 位集
  uint32_t request_id;       // 宿主侧序号（回执/通知原样带回）
  int32_t frame_index;       // 目标帧号 f（代入 --url-template 的 {f}）
  int32_t expected_width;    // 期望帧宽（0 = worker 视口宽）；不符→SIZE_MISMATCH
  int32_t expected_height;   // 期望帧高（0 = worker 视口高）
  char out_png_path[40];     // NUL 结尾；含 NUL 最长 39 字符（模式 1 落盘目标，
                             // 模式 2 仅 kFlagDumpRaw 时写 <path>.raw）
};
static_assert(sizeof(FrameRequest) == 64, "请求定长 64（工单口径）");
static_assert(offsetof(FrameRequest, out_png_path) == 24);
static_assert(std::is_standard_layout_v<FrameRequest>);

inline constexpr size_t kMaxOutPngPath = 39;  // 不含 NUL

// ---------------------------------------------------------------- 槽通知（Worker→宿主，32B，模式 2）

struct SlotNotice {
  uint32_t magic;             // kNoticeMagic
  uint16_t protocol_version;  // kProtocolVersion
  uint16_t status_code;       // 0=OK：slot_index 有效；≠0：错误（slot_index=0xFF）
  uint32_t request_id;
  int32_t frame_index;
  uint8_t slot_index;         // 0..RING_SLOTS-1；0xFF = 无槽（错误路径）
  uint8_t reserved[3];
  uint32_t seq;               // 该槽发布序号（ABA 防御/诊断）
  uint64_t backpressure_wait_us;  // 本请求等空槽耗时（4 槽全满背压度量）
};
static_assert(sizeof(SlotNotice) == 32, "槽通知定长 32");
static_assert(std::is_standard_layout_v<SlotNotice>);

// ---------------------------------------------------------------- Hello（Worker→宿主，64B，建连即发）

struct HelloInfo {
  uint32_t magic;             // kHelloMagic
  uint16_t protocol_version;  // kProtocolVersion
  uint16_t flags;             // bit0：随消息附 SHM fd（SCM_RIGHTS）
  uint64_t run_id;            // 本次 serve 运行标识（槽头 run_id 必须相等）
  int32_t pid;
  uint32_t ring_slots;        // = 4（kRingSlots）
  uint32_t max_width;         // 槽帧容量（= worker 视口）
  uint32_t max_height;
  uint32_t frame_bytes;       // max_width*max_height*4（每槽数据字节数）
  uint32_t reserved[7];
};
static_assert(sizeof(HelloInfo) == 64, "Hello 定长 64");
static_assert(std::is_standard_layout_v<HelloInfo>);

// ---------------------------------------------------------------- 小工具

// 定长消息整读整写辅助：sock 循环收满 n 字节；EOF/错误/超时返回 false。
// （实现于 serve.cpp；此处声明供协议面共用。）
int RecvAllOrShort(int fd, void* buf, size_t n, int timeout_ms);
bool SendAll(int fd, const void* buf, size_t n);

// 请求路径合法性：非空、无内嵌 NUL 前的长度 ≤ kMaxOutPngPath。
inline bool RequestPathValid(const FrameRequest& req) {
  const size_t n = ::strnlen(req.out_png_path, sizeof(req.out_png_path));
  return n > 0 && n <= kMaxOutPngPath;
}

}  // namespace vtx_cef

#endif  // GFX_CEF_WORKER_IPC_PROTOCOL_H_
