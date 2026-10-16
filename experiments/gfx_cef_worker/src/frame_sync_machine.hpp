// frame_sync_machine.hpp — 五阶段单调世代状态机与 v2 双像素 Canary 闸门
// （issue #114 CEF-03a，路线 A 生死线 Part 1）。
//
// 职责（工单口径，无 CEF 依赖 —— 原生自检 frame_sync_selftest 可独立驱动，
// serve 模式经 RunState 装配驱动真实 OnPaint 流）：
//   - FrameSyncStateMachine 维护三元组 (document_gen, request_id,
//     target_frame_index) 与五个严格有序状态：
//       S0_IDLE → S1_NAV_OR_EVAL_ISSUED（?f=N 导航或 __vtx_render(N) 调用）
//               → S2_ASSETS_FONTS_READY（readyState==='complete' +
//                  document.fonts.ready + 全部 <img> decode() 完成信号）
//               → S3_JS_FRAME_APPLIED_AND_CANARY_SET（页内帧状态更新后经
//                  rAF→rAF→MessageChannel task 绘制 v2 双像素 Canary 并以
//                  console.debug("__VTX_READY__:"+json) 通知宿主；双 rAF 与
//                  MessageChannel 只作就绪候选，不构成提交完成证明）
//               → S4_COMPOSITOR_PUMPING（Invalidate(PET_VIEW) +
//                  SendExternalBeginFrame() + CefDoMessageLoopWork() 泵送）
//               → S5_ONPAINT_VERIFIED（真实 OnPaint 双像素验签通过）
//   - OnPaint 闸门：S4 前收到的异步残余 Paint 直接丢弃（early_paint_
//     dropped++）；尺寸不符拒收；双像素与期望帧预乘签名逐字节全等才匹配，
//     陈旧/半更新签名拒收并继续泵送（rejected_stale_paint_count++）；
//     匹配后原地恢复两个与请求绑定且已验证的原像素（绝不邻域复制通用化）。
//   - 全事件序列 JSONL 日志：每帧从 S3 到 S5 的 rejected_stale_paint_count
//     与 accepted_elapsed_us_since_ready 等逐事件可观测。
//
// 协议对齐：唯一 home = vtx/frame_canary.py（#105 r4 / #137 v2 冻结件）。
// 本文件是其 C++ 消费镜像：Clean wire 名 clean_1px_restore 实为 (0,0)/(1,0)
// 两像素；P1 xor 键 0x5A / P2 加性键 0x3C / 系数 (16,22) / α=254 均为冻结值，
// 禁止回退旧单像素公式或只验第一像素。Diagnostic 为双对角 8×8 块。
// 帧号签名不编码 document_gen/request_id —— 跨导航/重启后同帧号的世代隔离
// 由宿主侧机制承担（S4 前早期丢弃、请求间 S0 缴械、晚到就绪回调三元组
// 核对拒收、S4 泵送归属构造性绑定），证据见 frame_sync_selftest 与 JSONL。
#ifndef GFX_CEF_WORKER_FRAME_SYNC_MACHINE_H_
#define GFX_CEF_WORKER_FRAME_SYNC_MACHINE_H_

#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace vtx_cef {
namespace fsync {

// ---------------------------------------------------------------- 状态词汇

enum class State : uint8_t {
  S0_IDLE = 0,
  S1_NAV_OR_EVAL_ISSUED = 1,
  S2_ASSETS_FONTS_READY = 2,
  S3_JS_FRAME_APPLIED_AND_CANARY_SET = 3,
  S4_COMPOSITOR_PUMPING = 4,
  S5_ONPAINT_VERIFIED = 5,
};
const char* StateName(State s);

// 驱动模式：N = 逐帧导航（?f=N）；P = 常驻页 __vtx_render(N)。
enum class DriveMode : uint8_t { kModeN = 0, kModeP = 1 };
const char* DriveModeName(DriveMode m);

// Canary 模式：Clean = v2 双像素（验收主轨）；Diagnostic = 双对角 8×8。
// kExact: two opaque, disjoint corner probes in the SAME document. Both
// paints must verify; all unoccluded bytes must agree. Cross-copy restores the
// actual covered pixels (including alpha), without page hooks or neighbors.
// A separate never-instrumented navigation supplies a mandatory full-frame
// equality VETO only. It supplies neither target identity nor restoration bytes.
enum class CanaryMode : uint8_t { kClean = 0, kDiagnostic = 1, kExact = 2 };

// ---------------------------------------------------------------- 计数器面

struct Counters {
  uint64_t requests_begun = 0;          // BeginRequest 成功次数
  uint64_t requests_accepted = 0;       // 达到 S5 的请求数
  uint64_t requests_failed = 0;         // 终态失败（超时/致命）数
  uint64_t early_paint_dropped = 0;     // S1–S3 期间异步残余 Paint 丢弃（全程累计）
  uint64_t late_paint_dropped = 0;      // S5/S0 期间到达的 Paint 丢弃
  uint64_t rejected_stale_paint = 0;    // S4 中陈旧/半更新/无签名拒收（全程累计）
  uint64_t size_mismatch_paints = 0;    // 尺寸不符 Paint（全程累计）
  uint64_t late_ready_cb_rejected = 0;  // 三元组错配的晚到就绪回调拒收
  uint64_t duplicate_request_rejected = 0;
  uint64_t illegal_transition_rejected = 0;
};

// ---------------------------------------------------------------- Paint 判定

enum class PaintDisposition : uint8_t {
  kEarlyDropped = 0,   // S4 前：直接丢弃（工单：异步残余 Paint）
  kLateDropped = 1,    // S5 后 / S0：本请求已完成或未开始
  kSizeMismatch = 2,   // width/height ≠ 期望
  kRejectedStale = 3,  // S4 中签名不匹配（含半更新/无签名/归因失败）
  kAccepted = 4,       // 双像素精确匹配 → S5（已原地恢复原像素）
};

struct PaintVerdict {
  PaintDisposition disposition = PaintDisposition::kEarlyDropped;
  int32_t decoded_frame = -1;        // 双像素候选交集单例（接受时 == 目标帧，
                                     // 来自解码结果而非请求值回显）
  bool half_signature = false;       // 双像素恰一者与期望签名逐字节相等
  bool restored = false;             // 接受后已恢复两个绑定原像素
  bool restore_from_reported = false;  // true=页面上报原像素；false=行 1 邻域
                                       // 兜底（仅 fixture 证明等值时合法）
  uint64_t accepted_elapsed_us_since_ready = 0;  // S3 就绪信号→S5 接受
  uint64_t capture_us = 0;           // 接受帧紧凑拷贝+恢复耗时
};

// ---------------------------------------------------------------- 页面事件

// 注入桥 console.debug("__VTX_READY__:"+json) 的宿主侧解析面。
struct PageEvent {
  enum class Phase : uint8_t { kAssetsReady = 0, kCanarySet = 1 };
  Phase phase = Phase::kAssetsReady;
  uint32_t document_gen = 0;
  uint32_t request_id = 0;
  int32_t target_frame_index = 0;
  // canary_set 附带页面上报原像素（__vtx_originals() 钩子；RGBA 直通四元组，
  // 索引 [0]=r [1]=g [2]=b [3]=a）。has_reported_originals=false 走邻域兜底。
  bool has_reported_originals = false;
  uint8_t original_rgba[2][4] = {{0}};
};

// ---------------------------------------------------------------- 请求描述

struct RequestDesc {
  uint32_t document_gen = 0;
  uint32_t request_id = 0;
  int32_t target_frame_index = 0;
  DriveMode drive = DriveMode::kModeN;
  CanaryMode canary = CanaryMode::kClean;
  uint32_t expected_w = 0;
  uint32_t expected_h = 0;
};

// ---------------------------------------------------------------- 状态机

class FrameSyncStateMachine {
 public:
  // JSONL 事件汇（每行一个完整 JSON 对象；serve 模式接文件，自检接内存）。
  using EventSink = std::function<void(const std::string&)>;

  void SetEventSink(EventSink sink) { sink_ = std::move(sink); }

  // S0 → S1：发出导航/求值（宿主已完成 LoadURL / ExecuteJavaScript 装配）。
  // 契约：仅 S0 可开局；request_id 与在途/上一请求重复 → 拒绝（重复请求
  // 对抗面），状态不变。成功时记录 request_begin 事件并锁定三元组。
  bool BeginRequest(const RequestDesc& desc, uint64_t now_us);

  // 页面就绪回调：kAssetsReady → S1→S2；kCanarySet → S2→S3（记录
  // t_ready 时点与上报原像素）。三元组（document_gen/request_id/
  // target_frame_index）与在途请求不匹配的回调 = 旧世代晚到回调，拒收
  // 计数（世代隔离证据面），状态不变。
  bool NotifyPageEvent(const PageEvent& ev, uint64_t now_us);

  // S3 → S4：宿主收到就绪信号后开启外部帧泵（就绪信号只是候选，非完成
  // 证明 —— 提交完成只能由 S5 真实像素验签确立）。
  bool EnterPumping(uint64_t now_us);

  // Only in exact S1: native pixels from a separate fresh navigation that has
  // NEVER had witness DOM. Equality can veto the signed reconstruction, never
  // establish its frame identity or contribute output/restoration bytes.
  bool SetExactBaseline(const uint8_t* src, size_t stride, uint32_t width,
                        uint32_t height, uint64_t now_us);

  // OnPaint 喂入（UI 线程同步调用）。
  //   src：CEF OnPaint 原始 BGRA_PREMUL 缓冲（行步长 stride 字节）；
  //   dst：接受帧紧凑拷贝目标（可空 = 只判定不拷贝，供早期丢弃/拒收路径
  //        零带宽）；接受时逐行紧凑拷贝后原地恢复 (0,0)/(0,1) 两像素。
  // 判定序（工单口径）：S4 前直接丢弃 → 尺寸卫兵 → 双像素逐字节全等
  // （Clean）/双对角 8×8 块核验（Diagnostic）→ 匹配即 S5 + 恢复 + 计时。
  PaintVerdict FeedPaint(const uint8_t* src, size_t stride, uint32_t width,
                         uint32_t height, uint8_t* dst, uint64_t now_us);

  // 终态失败（泵送超时等）：记录 request_end(failed) 并回到 S0（可复用）。
  bool FailRequest(const char* reason, uint64_t now_us);

  // 显式复位（S5 完成后的收尾 / 异常路径）：记录 request_end 并回 S0。
  void Reset(uint64_t now_us, const char* reason);

  // —— 查询面（serve 回执/测试断言消费；计数器为全程累计口径）——
  State state() const { return state_; }
  const RequestDesc& request() const { return desc_; }
  bool accepted() const {
    return state_ == State::S5_ONPAINT_VERIFIED;
  }
  bool needs_relocation() const { return exact_first_verified_; }
  uint64_t t_request_begin_us() const { return t_begin_us_; }
  uint64_t t_ready_us() const { return t_canary_set_us_; }  // S3 时点
  const Counters& counters() const { return counters_; }

  // 请求作用域统计（回执 rejected_paints / JSONL 每帧口径的数据源）。
  struct RequestStats {
    uint64_t early_paint_dropped = 0;
    uint64_t rejected_stale_paint = 0;
    uint64_t paints_fed = 0;
    uint64_t accepted_elapsed_us_since_ready = 0;
  };
  RequestStats request_stats() const {
    RequestStats s;
    s.early_paint_dropped = req_early_;
    s.rejected_stale_paint = req_rejected_;
    s.paints_fed = req_paints_;
    s.accepted_elapsed_us_since_ready = last_accepted_elapsed_us_;
    return s;
  }

 private:
  void Emit(const std::string& event, uint64_t now_us, const char* from,
            const char* to, const std::string& extra_json_fields = {});

  State state_ = State::S0_IDLE;
  RequestDesc desc_{};
  uint32_t last_request_id_ = 0;       // 重复请求守卫（上一完成请求）
  bool has_last_request_ = false;      // 未完成过任何请求时无守卫对象
  uint64_t t_begin_us_ = 0;
  uint64_t t_assets_us_ = 0;
  uint64_t t_canary_set_us_ = 0;       // S3：accepted_elapsed_us_since_ready 基准
  uint64_t t_pump_start_us_ = 0;
  uint64_t seq_ = 0;                   // JSONL 事件序号
  Counters counters_{};
  uint8_t reported_original_bgra_[2][4] = {{0}};  // 页面上报（预乘口径）
  bool has_reported_originals_ = false;
  // 请求作用域计数（JSONL 每帧可观测面；BeginRequest 清零）。
  uint64_t req_early_ = 0;
  uint64_t req_rejected_ = 0;
  uint64_t req_paints_ = 0;
  uint64_t last_accepted_elapsed_us_ = 0;
  bool exact_first_verified_ = false;
  std::vector<uint8_t> exact_first_;
  std::vector<uint8_t> exact_baseline_;
  std::vector<uint8_t> exact_candidate_;
  EventSink sink_;
};

// ---------------------------------------------------------------- canary 编解码
// （vtx/frame_canary.py 冻结公式的 C++ 镜像；公式变更必须两端同步）

inline constexpr uint8_t kCleanXorKey = 0x5A;   // P1 xor 键（clean 冻结值）
inline constexpr uint8_t kClean2AddKey = 0x3C;  // P2 加性键（clean 冻结值）
inline constexpr int kP2MulR = 16;              // P2 校验和 R 系数
inline constexpr int kP2MulG = 22;              // P2 校验和 G 系数
inline constexpr uint8_t kCleanAlpha = 254;     // clean α（避开 255/0）
inline constexpr uint8_t kDiagXorKey = 0xA5;    // 诊断 P1 xor 键
inline constexpr uint8_t kDiag2AddKey = 0xC3;   // 诊断 P2 加性键
inline constexpr uint8_t kDiagAlpha = 255;      // 诊断 α（不透明纯色块）
inline constexpr int kDiagSize = 8;             // 诊断块边长（像素）
inline constexpr uint32_t kFrameMax = 0xFFFF;   // 帧号域上限（16-bit）

// BGRA 预乘四元组（Little-Endian 内存字节序）。
struct Bgra {
  uint8_t b = 0, g = 0, r = 0, a = 0;
  bool operator==(const Bgra& o) const {
    return b == o.b && g == o.g && r == o.r && a == o.a;
  }
  bool operator!=(const Bgra& o) const { return !(*this == o); }
};

// 半向上舍入预乘：floor((2*c*a + 255)/510) == round(c*a/255)（对 a=255 恒等、
// a=254 与 vtx/frame_canary._premul_bgra 逐值一致）。
inline uint8_t PremulRound(uint8_t c, uint8_t a) {
  return static_cast<uint8_t>((2 * static_cast<int>(c) * a + 255) / 510);
}

// P1/P2 直向签名 RGB（冻结公式；mode=true 为诊断轨道）。
void SignatureRgbP1(bool diag, int f, uint8_t* rgb);
void SignatureRgbP2(bool diag, int f, uint8_t* rgb);

// clean 轨预乘存储口径期望签名（accept 判据：双像素逐字节全等）。
Bgra SignatureBgraPremulP1(int f);
Bgra SignatureBgraPremulP2(int f);

// 预乘存储值 v 的精确反像区间 {C : round(C*254/255) == v}（朴素 floor/ceil
// 会让约半数帧号校验和碰撞歧义，#105b 复审拦截 —— 必须闭区间精确解）。
std::vector<int> PremulCands(uint8_t v);

// 预乘签名像素反解候选帧集（P1/P2 公式各一）。用于归因（receipt 的
// canary 值取自解码结果、拒收理由的 rendered=N 归因），不是 accept 判据。
void DecodeCandidatesP1(const uint8_t px_bgra[4], std::vector<int>* out);
void DecodeCandidatesP2(const uint8_t px_bgra[4], std::vector<int>* out);

// 诊断 8×8 块核验：整块同色 + 校验和通过 → 解码帧号（P1=左上/P2=右下）。
// 返回 false 表示块非 uniform（foreign/撕裂带）或校验和撕裂。
bool DecodeDiagBlock(const uint8_t* compact_bgra, size_t stride, uint32_t width,
                     uint32_t height, bool bottom_right, int32_t* frame_out);

}  // namespace fsync
}  // namespace vtx_cef

#endif  // GFX_CEF_WORKER_FRAME_SYNC_MACHINE_H_
