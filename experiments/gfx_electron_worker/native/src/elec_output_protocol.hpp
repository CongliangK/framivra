// elec_output_protocol.hpp — Electron 输出桥定长二进制控制协议
// （issue #113 ELEC-02；#110 ipc_protocol.hpp 纪律的镜像实现）。
//
// 权威规格：experiments/gfx_electron_worker/OUTPUT_BRIDGE.md。
// 传输：Unix stream socket（Electron 主进程 net.Server 监听，原生助手
// connect）。像素数据不走 socket——走 /dev/shm 4-Slot 环（见 OUTPUT_BRIDGE.md
// 的环布局；槽数据 = 预乘 BGRA，行主序、紧凑 stride=width*4）。
//
// 设计要点：
//   - 全部消息定长、小端（linux64/x86-64）；偏移静态断言钉死；
//     Node 侧镜像 = output_bridge.mjs 的 _MSG_* 打包器（tests 跨语言锁定）。
//   - 同步模型：socket 信用（credit）为权威流控（Node 无 mmap 原子，
//     不能跨进程跑 #110 的 SlotGate CAS——门字段仅作诊断面，偏差记录在
//     OUTPUT_BRIDGE.md §0）。助手每释放一槽即回一条 credit；Electron 侧
//     在途帧由此天然有界（≤ slots=4）。
//   - 回执 canary 归因取自解码结果（rendered_frame），绝不转录请求值。
#ifndef GFX_ELEC_WORKER_OUTPUT_PROTOCOL_H_
#define GFX_ELEC_WORKER_OUTPUT_PROTOCOL_H_

#include <cstddef>
#include <cstdint>
#include <type_traits>

namespace vtx_elec {

inline constexpr uint32_t kProtoMagicHello = 0x56584F48u;  // "VXOH"
inline constexpr uint32_t kProtoMagicCredit = 0x56584F43u;  // "VXOC"
inline constexpr uint32_t kProtoMagicRequest = 0x56584F52u; // "VXOR"
inline constexpr uint32_t kProtoMagicDone = 0x56584F44u;    // "VXOD"
inline constexpr uint16_t kProtoVersion = 1;

inline constexpr size_t kMaxOutPath = 223;  // 不含 NUL（证据目录长路径余量）

// Done::status_code。
enum class OutStatus : uint16_t {
  kOk = 0,
  kCanaryReject = 1,   // v2 闸门拒绝（陈旧/无签名/歧义；不落盘）
  kHeaderInvalid = 2,  // 槽头 magic/版本/run_id/几何不符
  kEncodeError = 3,
  kIoError = 4,        // 原子落盘失败
  kBadRequest = 5,     // 请求字段非法（槽号越界/路径空/编码器名未知）
};

// Done::canary_match（诊断：观测与期望签名的字节关系）。
enum class CanaryMatch : uint8_t {
  kNone = 0,   // 未过闸
  kExact = 1,  // 与期望预乘签名逐字节相等
  kTol1 = 2,   // ±1 容差内过闸（引擎预乘舍入差异）
};

// Done::reason_code（canary 拒绝的细粒度归因，canary_v2.h GateReason 镜像）。
enum class OutReason : uint32_t {
  kOk = 0,
  kStaleRendered = 1,
  kNoSignature = 2,
  kIncompatible = 3,
  kAmbiguous = 4,
  kTwin = 5,
  kNa = 0xFFFFFFFFu,  // 非 canary 状态时的占位
};

// Request::encoder。
enum class EncId : uint8_t { kSpng = 0, kFpng = 1, kZlib = 2 };

// ---------------------------------------------------------------- Hello（64B）
struct alignas(8) ProtoHello {
  uint32_t magic;
  uint16_t protocol_version;
  uint16_t reserved0;
  int32_t pid;
  uint32_t slots;
  uint32_t width;
  uint32_t height;
  uint64_t shm_bytes;
  uint64_t run_id;
  uint8_t unpremul_backend;  // 0=scalar 1=avx2 2=neon（pixel_ops 报告值）
  uint8_t reserved[23];
};
static_assert(sizeof(ProtoHello) == 64);
static_assert(std::is_standard_layout_v<ProtoHello>);

// ---------------------------------------------------------------- Credit（16B）
struct ProtoCredit {
  uint32_t magic;
  uint16_t protocol_version;
  uint16_t reserved0;
  uint32_t slot;
  uint32_t seq;  // 该槽第几次释放（诊断；从 1 起）
};
static_assert(sizeof(ProtoCredit) == 16);
static_assert(std::is_standard_layout_v<ProtoCredit>);

// ---------------------------------------------------------------- Request（320B）
struct alignas(8) ProtoRequest {
  uint32_t magic;
  uint16_t protocol_version;
  uint16_t reserved0;
  uint32_t id;                // Electron 侧请求序号（回执回显）
  uint32_t slot;              // 本帧像素所在槽（0..slots-1）
  int32_t frame;              // 期望帧号 f（16-bit 域）
  uint32_t gen;               // 宿主 generation（诊断透传）
  uint32_t width;
  uint32_t height;
  uint64_t run_id;            // 须与环控制块一致
  uint8_t encoder;            // EncId
  uint8_t level;              // spng 压缩级别 1..3
  uint8_t filter;             // vtx_cef::SpngFilter 值（8/16/32/48/248）
  uint8_t tol1;               // 1 = canary 闸门 ±1 档（默认）
  uint8_t has_orig;           // 1 = orig1/orig2 为宿主上报原像素
  uint8_t reserved1[3];
  uint8_t orig1[4];           // (0,0) 原像素 BGRA（has_orig=1 时使用）
  uint8_t orig2[4];           // (0,1) 原像素 BGRA
  uint64_t t_ready_wait_us;   // 生产侧计时透传（进入回执证据链）
  uint64_t t_capture_raw_us;
  char out_path[224];         // NUL 结尾；含 NUL 最长 223
};
static_assert(sizeof(ProtoRequest) == 296);
static_assert(offsetof(ProtoRequest, out_path) == 72);
static_assert(std::is_standard_layout_v<ProtoRequest>);

// ---------------------------------------------------------------- Done（256B）
struct alignas(8) ProtoDone {
  uint32_t magic;
  uint16_t protocol_version;
  uint16_t status_code;       // OutStatus
  uint32_t id;
  uint32_t slot;
  int32_t frame;              // 回显请求帧号
  int32_t rendered_frame;     // 解码归因帧号（canary 拒绝时可读）
  uint32_t reason_code;       // OutReason
  uint8_t canary_match;       // CanaryMatch
  uint8_t restore_source;     // 0=reported_original 1=neighbor_row1 2=n/a
  uint8_t reserved0[2];
  uint32_t reserved1;
  uint64_t t_ready_wait_us;   // 生产侧透传
  uint64_t t_capture_raw_us;
  uint64_t t_decode_us;       // canary 双像素反解
  uint64_t t_restore_us;
  uint64_t t_alpha_norm_us;   // SIMD 逆预乘
  uint64_t t_png_encode_us;
  uint64_t t_disk_commit_us;
  uint64_t png_bytes;
  uint32_t png_crc32;
  uint32_t reserved2[9];
};
static_assert(sizeof(ProtoDone) == 144);
static_assert(offsetof(ProtoDone, t_decode_us) == 56);
static_assert(std::is_standard_layout_v<ProtoDone>);

}  // namespace vtx_elec

#endif  // GFX_ELEC_WORKER_OUTPUT_PROTOCOL_H_
