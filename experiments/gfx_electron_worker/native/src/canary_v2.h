// canary_v2.h — v2 双像素帧同步水印协议的 C++ 消费端口（issue #113 ELEC-02）。
//
// 权威 home：vtx/frame_canary.py（#105 r4 冻结；本文件是其 clean 模式
// （clean_1px_restore，双像素 (0,0)+(1,0)）消费面判据的逐公式移植，
// 供 Electron 输出桥的原生编码助手在槽数据上做「v2 签名验证与双像素恢复」。
// 禁止改动公式常数——任何变更须先改 vtx/frame_canary.py（唯一 home）。
//
// 像素物理口径（与 Python 模块头一致）：
//   - 注入桥以 rgba(...,254/255) 绘制两签名像素；原生 OSR 缓冲区为
//     BGRA 预乘存储：A=254、RGB = round(C·254/255)（半向上，
//     整数精确式 (508·C+255)//510）。
//   - 不同引擎的预乘舍入实现存在 ±1 差异可能（Python match_bgra_premul
//     的 tol=1 校准口径）→ 本端口提供两档反解：
//       exact（tol=0）：C 的预乘精确等于观测值 v；
//       tol1        ：|premul(C) − v| ≤ 1（PNG 通路 ±1 立方候选的 BGRA 对应）。
//     闸门判据（#116 同款）：S1∩S2 唯一且 == 期望帧才接受。
//     ±1 扩张只增不减候选：陈旧帧观感的交集仍含其自身（精确反像子集），
//     单例只能是真实绘制帧——0 伪报性质保持（selftest 全域穷举背书）。
//
// 字节序：BGRA 内存四元组 (buf[0]=B, buf[1]=G, buf[2]=R, buf[3]=A)。
#ifndef GFX_ELEC_WORKER_CANARY_V2_H_
#define GFX_ELEC_WORKER_CANARY_V2_H_

#include <cstdint>
#include <vector>

namespace vtx_elec {

// ---- v2 冻结常数（镜像 vtx/frame_canary.py；改动禁止）----
inline constexpr uint8_t kCleanXorKey = 0x5A;   // P1 xor 校验和键
inline constexpr uint8_t kClean2AddKey = 0x3C;  // P2 加性校验和键
inline constexpr int kP2MulR = 16;              // P2 校验和 R 系数
inline constexpr int kP2MulG = 22;              // P2 校验和 G 系数
inline constexpr uint8_t kCleanAlpha = 254;     // 签名 α（刻意避开 255/0）
inline constexpr uint32_t kFrameMax = 0xFFFF;   // 16-bit 帧号域

// ---- 签名公式 ----

// P1 直向签名 RGB：R=hi, G=lo, B=(hi^lo^K1)&0xFF。
void SignatureP1Rgb(uint32_t f, uint8_t* rgb);
// P2 直向签名 RGB：R=lo, G=hi（通道互换），B=(16R+22G+K2)&0xFF。
void SignatureP2Rgb(uint32_t f, uint8_t* rgb);

// 预乘舍入（半向上）：(508·c+255)/510，整数精确（无浮点）。
inline constexpr uint32_t PremulRound(uint32_t c) {
  return (508u * c + 255u) / 510u;
}

// 存储口径签名（BGRA 内存四元组，RGB 已预乘、A=254）。
void SignatureP1BgraPremul(uint32_t f, uint8_t bgra[4]);
void SignatureP2BgraPremul(uint32_t f, uint8_t bgra[4]);

// ---- 反像（预乘存储值 → 候选直向值区间，闭区间含端点）----
// exact：{C ∈ [0,255] : PremulRound(C) == v}；空区间以 lo>hi 表示。
// tol1 ：{C : |PremulRound(C) − v| ≤ 1}（对 ±1 引擎舍入校准）。
struct CandRange {
  int32_t lo;  // 含
  int32_t hi;  // 含；lo>hi = 空集
  bool Empty() const { return lo > hi; }
};
CandRange PremulInverseExact(uint32_t v);
CandRange PremulInverseTol1(uint32_t v);

// ---- 候选帧集 ----
// 对 BGRA 像素（预乘存储）按 P1/P2 公式反解全部候选帧号（笛卡尔组合 ×
// 校验和过滤；tol1=false 用精确反像，true 用 ±1 扩张）。输出升序去重。
void DecodeCandidatesP1(const uint8_t bgra[4], bool tol1, std::vector<uint32_t>* out);
void DecodeCandidatesP2(const uint8_t bgra[4], bool tol1, std::vector<uint32_t>* out);

// ---- 闸门（accept_paint_bgra_inplace 的 C++ 端口）----
enum class GateReason : uint32_t {
  kOk = 0,
  kStaleRendered = 1,     // 交集单例为他帧（归因可信）
  kNoSignature = 2,       // 任一像素无签名结构
  kIncompatible = 3,      // 双像素候选不相容（交集空）
  kAmbiguous = 4,         // 交集多候选
  kTwin = 5,              // 防御分支：交集含期望帧与孪生（协议常数被破坏）
};

struct GateResult {
  bool accepted;
  uint32_t rendered_frame;   // 接受=期望帧；拒绝=交集单例归因（无则 0）
  GateReason reason;
  bool exact_match;          // 观测字节与期望预乘签名逐字节相等（诊断面）
};

// 双像素闸门：frame_bgra 为整帧行主序 BGRA 预乘缓冲，
// (0,0)=frame_bgra[0..3]（P1）、(0,1)=frame_bgra[4..7]（P2）。
// width ≥ 2 且缓冲 ≥ 2 行（邻域剥离源在行 1）由调用方保证（此处只读判定）。
// 接受条件：S1∩S2 == {expected_frame}（tol1 档位由参数决定）。
GateResult VerifyCanary2(const uint8_t* frame_bgra, uint32_t width,
                         uint32_t expected_frame, bool tol1);

// 双像素原地恢复（接受后调用）：(0,0)/(0,1) ← orig1/orig2（宿主上报原像素，
// 与请求绑定）；未提供（nullptr）时回落行 1 邻色 (1,0)/(1,1)——仅当
// fixture 证明顶行与行 1 等值时精确（本仓基准页满足，见 bench 页说明）。
// 返回实际使用的恢复源（"reported_original" / "neighbor_row1"）。
const char* RestoreCanary2Inplace(uint8_t* frame_bgra, uint32_t width,
                                  const uint8_t orig1[4], const uint8_t orig2[4]);

// ---- selftest（--selftest 消费；全域穷举，无外部依赖）----
// 返回 0=全过；非 0=失败数。verbose 时逐项打印。
int Canary2Selftest(bool verbose, bool json);

}  // namespace vtx_elec

#endif  // GFX_ELEC_WORKER_CANARY_V2_H_
