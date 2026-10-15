// canary_v2.cpp — v2 双像素 canary 消费端口实现（issue #113 ELEC-02）。
// 公式镜像 vtx/frame_canary.py（唯一 home）；selftest 全域穷举背书。
#include "canary_v2.h"

#include <algorithm>
#include <cstdio>
#include <cstring>

namespace vtx_elec {

void SignatureP1Rgb(uint32_t f, uint8_t* rgb) {
  const uint32_t hi = (f >> 8) & 0xFF;
  const uint32_t lo = f & 0xFF;
  rgb[0] = static_cast<uint8_t>(hi);
  rgb[1] = static_cast<uint8_t>(lo);
  rgb[2] = static_cast<uint8_t>((hi ^ lo ^ kCleanXorKey) & 0xFF);
}

void SignatureP2Rgb(uint32_t f, uint8_t* rgb) {
  const uint32_t hi = (f >> 8) & 0xFF;
  const uint32_t lo = f & 0xFF;
  rgb[0] = static_cast<uint8_t>(lo);
  rgb[1] = static_cast<uint8_t>(hi);
  rgb[2] = static_cast<uint8_t>((kP2MulR * lo + kP2MulG * hi + kClean2AddKey) & 0xFF);
}

void SignatureP1BgraPremul(uint32_t f, uint8_t bgra[4]) {
  uint8_t rgb[3];
  SignatureP1Rgb(f, rgb);
  bgra[0] = static_cast<uint8_t>(PremulRound(rgb[2]));
  bgra[1] = static_cast<uint8_t>(PremulRound(rgb[1]));
  bgra[2] = static_cast<uint8_t>(PremulRound(rgb[0]));
  bgra[3] = kCleanAlpha;
}

void SignatureP2BgraPremul(uint32_t f, uint8_t bgra[4]) {
  uint8_t rgb[3];
  SignatureP2Rgb(f, rgb);
  bgra[0] = static_cast<uint8_t>(PremulRound(rgb[2]));
  bgra[1] = static_cast<uint8_t>(PremulRound(rgb[1]));
  bgra[2] = static_cast<uint8_t>(PremulRound(rgb[0]));
  bgra[3] = kCleanAlpha;
}

// 反像区间数学（镜像 _premul_cands）：
//   premul(C)=v ⟺ 510v−255 ≤ 508C < 510v+255 ⟺ C ∈ [ceil((510v−255)/508),
//   ceil((510v+255)/508)−1]（整数闭区间，再钳 [0,255]）。
// tol1 档 = 三区间 [exact(v−1), exact(v), exact(v+1)] 之并（v±1 钳界后）。
namespace {

// 向上取整除法（除数 > 0，被除数可为负）。
inline int64_t CeilDiv(int64_t a, int64_t b) {
  return a >= 0 ? (a + b - 1) / b : -((-a) / b);
}

CandRange InverseForValue(int32_t v) {
  if (v < 0 || v > 255) return {1, 0};  // 空
  const int64_t lo = CeilDiv(510LL * v - 255, 508);
  const int64_t hi = CeilDiv(510LL * v + 255, 508) - 1;
  const int32_t clo = static_cast<int32_t>(std::max<int64_t>(lo, 0));
  const int32_t chi = static_cast<int32_t>(std::min<int64_t>(hi, 255));
  if (clo > chi) return {1, 0};  // 空集统一哨兵（v=255：premul 值域上限 254）
  return {clo, chi};
}

}  // namespace

CandRange PremulInverseExact(uint32_t v) { return InverseForValue(static_cast<int32_t>(v)); }

CandRange PremulInverseTol1(uint32_t v) {
  int32_t lo = 256, hi = -1;
  for (int32_t w = static_cast<int32_t>(v) - 1; w <= static_cast<int32_t>(v) + 1; ++w) {
    const CandRange r = InverseForValue(w);
    if (!r.Empty()) {
      lo = std::min(lo, r.lo);
      hi = std::max(hi, r.hi);
    }
  }
  if (lo > hi) return {1, 0};
  return {lo, hi};
}

namespace {

// 单像素候选（P 公式由解码器区分）。bgra 内存序 (B,G,R,A)。
template <typename DecodeFn>
void DecodeCandidates(const uint8_t bgra[4], bool tol1, DecodeFn decode,
                      std::vector<uint32_t>* out) {
  out->clear();
  const CandRange rb = tol1 ? PremulInverseTol1(bgra[0]) : PremulInverseExact(bgra[0]);
  const CandRange rg = tol1 ? PremulInverseTol1(bgra[1]) : PremulInverseExact(bgra[1]);
  const CandRange rr = tol1 ? PremulInverseTol1(bgra[2]) : PremulInverseExact(bgra[2]);
  for (int32_t b = rb.lo; b <= rb.hi; ++b) {
    for (int32_t g = rg.lo; g <= rg.hi; ++g) {
      for (int32_t r = rr.lo; r <= rr.hi; ++r) {
        uint32_t frame = 0;
        if (decode(static_cast<uint32_t>(r), static_cast<uint32_t>(g),
                   static_cast<uint32_t>(b), &frame)) {
          out->push_back(frame);
        }
      }
    }
  }
  std::sort(out->begin(), out->end());
  out->erase(std::unique(out->begin(), out->end()), out->end());
}

inline bool DecodeP1(uint32_t r, uint32_t g, uint32_t b, uint32_t* frame) {
  *frame = ((r & 0xFF) << 8) | (g & 0xFF);
  return (b & 0xFF) == ((r ^ g ^ kCleanXorKey) & 0xFF);
}

inline bool DecodeP2(uint32_t r, uint32_t g, uint32_t b, uint32_t* frame) {
  *frame = ((g & 0xFF) << 8) | (r & 0xFF);
  return (b & 0xFF) ==
         ((kP2MulR * (r & 0xFF) + kP2MulG * (g & 0xFF) + kClean2AddKey) & 0xFF);
}

}  // namespace

void DecodeCandidatesP1(const uint8_t bgra[4], bool tol1, std::vector<uint32_t>* out) {
  DecodeCandidates(bgra, tol1, DecodeP1, out);
}

void DecodeCandidatesP2(const uint8_t bgra[4], bool tol1, std::vector<uint32_t>* out) {
  DecodeCandidates(bgra, tol1, DecodeP2, out);
}

GateResult VerifyCanary2(const uint8_t* frame_bgra, uint32_t width,
                         uint32_t expected_frame, bool tol1) {
  GateResult res{false, 0, GateReason::kNoSignature, false};
  if (width < 2) return res;  // 契约由调用方保证；防御
  std::vector<uint32_t> s1, s2;
  DecodeCandidatesP1(frame_bgra, tol1, &s1);
  DecodeCandidatesP2(frame_bgra + 4, tol1, &s2);
  if (s1.empty() || s2.empty()) {
    res.reason = GateReason::kNoSignature;
    return res;
  }
  std::vector<uint32_t> inter;
  std::set_intersection(s1.begin(), s1.end(), s2.begin(), s2.end(),
                        std::back_inserter(inter));
  uint8_t exp1[4], exp2[4];
  SignatureP1BgraPremul(expected_frame, exp1);
  SignatureP2BgraPremul(expected_frame, exp2);
  res.exact_match = std::memcmp(frame_bgra, exp1, 4) == 0 &&
                    std::memcmp(frame_bgra + 4, exp2, 4) == 0;
  if (inter.size() == 1) {
    res.rendered_frame = inter[0];
    if (inter[0] == expected_frame) {
      res.accepted = true;
      res.reason = GateReason::kOk;
      return res;
    }
    res.reason = GateReason::kStaleRendered;
    return res;
  }
  if (inter.empty()) {
    res.reason = GateReason::kIncompatible;
    return res;
  }
  // 多候选：含期望帧 = 孪生防御分支（v2 联合孪生全域空，触发即协议常数
  // 被破坏）；不含 = ambiguous。
  for (uint32_t f : inter) {
    if (f == expected_frame) {
      res.reason = GateReason::kTwin;
      return res;
    }
  }
  res.reason = GateReason::kAmbiguous;
  return res;
}

const char* RestoreCanary2Inplace(uint8_t* frame_bgra, uint32_t width,
                                  const uint8_t orig1[4], const uint8_t orig2[4]) {
  if (orig1 != nullptr && orig2 != nullptr) {
    std::memcpy(frame_bgra, orig1, 4);
    std::memcpy(frame_bgra + 4, orig2, 4);
    return "reported_original";
  }
  // 行 1 邻色 (1,0)/(1,1)：仅当 fixture 证明顶行与行 1 等值时精确。
  std::memcpy(frame_bgra, frame_bgra + 4ull * width, 4);
  std::memcpy(frame_bgra + 4, frame_bgra + 4ull * width + 4, 4);
  return "neighbor_row1";
}

// ---------------------------------------------------------------- selftest
namespace {

int g_failures = 0;
void Check(bool ok, const char* what) {
  if (!ok) {
    ++g_failures;
    std::fprintf(stderr, "  ✗ %s\n", what);
  }
}

// 全域穷举：exact/tol1 反像区间与暴力表逐值一致。
void TestInverseTables() {
  for (int v = 0; v <= 255; ++v) {
    // 暴力：C 使 PremulRound(C)==v（exact）/ |premul(C)−v|≤1（tol1）。
    int elo = 256, ehi = -1, tlo = 256, thi = -1;
    for (int c = 0; c <= 255; ++c) {
      const int p = static_cast<int>(PremulRound(static_cast<uint32_t>(c)));
      if (p == v) { elo = std::min(elo, c); ehi = std::max(ehi, c); }
      if (std::abs(p - v) <= 1) { tlo = std::min(tlo, c); thi = std::max(thi, c); }
    }
    const CandRange e = PremulInverseExact(static_cast<uint32_t>(v));
    const CandRange t = PremulInverseTol1(static_cast<uint32_t>(v));
    Check((e.lo == (elo == 256 ? 1 : elo)) && (e.hi == (ehi == -1 ? 0 : ehi)),
          "exact 反像区间与暴力表一致");
    Check((t.lo == (tlo == 256 ? 1 : tlo)) && (t.hi == (thi == -1 ? 0 : thi)),
          "tol1 反像区间与暴力表一致");
  }
}

// 全域穷举：①真迹全域可确认（exact 与 tol1 双档）；②联合归因恒单例且
// == 自身（陈旧帧零伪报的结构性根：单例交集不可能是他帧）；
// ③恢复（reported/neighbor）后签名位 == 原像素；④跨模式免疫抽样。
void TestExhaustiveFrames(bool verbose) {
  uint32_t stale_sing = 0, tol1_stale_sing = 0;
  for (uint32_t f = 0; f <= kFrameMax; ++f) {
    // 构造 2x2 帧：行 0 = P1/P2 预乘签名，行 1 = 统一底色（fixture 等值）。
    alignas(16) uint8_t frame[2 * 2 * 4];
    uint8_t bg[4] = {0x20, 0x40, 0x80, 0xFF};
    uint8_t p1[4], p2[4];
    SignatureP1BgraPremul(f, p1);
    SignatureP2BgraPremul(f, p2);
    std::memcpy(frame + 0, p1, 4);
    std::memcpy(frame + 4, p2, 4);
    for (int x = 0; x < 2; ++x) std::memcpy(frame + 8 + 4 * x, bg, 4);

    const GateResult g = VerifyCanary2(frame, 2, f, false);
    Check(g.accepted && g.rendered_frame == f && g.exact_match,
          "真迹 exact 档全域接受");
    const GateResult gt = VerifyCanary2(frame, 2, f, true);
    Check(gt.accepted && gt.rendered_frame == f, "真迹 tol1 档全域接受");

    // 联合归因单例性：exact 与 tol1 档的 S1∩S2 都必须 == {f}。
    std::vector<uint32_t> s1, s2, inter;
    DecodeCandidatesP1(frame, false, &s1);
    DecodeCandidatesP2(frame + 4, false, &s2);
    std::set_intersection(s1.begin(), s1.end(), s2.begin(), s2.end(),
                          std::back_inserter(inter));
    if (!(inter.size() == 1 && inter[0] == f)) ++stale_sing;
    s1.clear(); s2.clear(); inter.clear();
    DecodeCandidatesP1(frame, true, &s1);
    DecodeCandidatesP2(frame + 4, true, &s2);
    std::set_intersection(s1.begin(), s1.end(), s2.begin(), s2.end(),
                          std::back_inserter(inter));
    if (!(inter.size() == 1 && inter[0] == f)) ++tol1_stale_sing;

    // 恢复（两种源）后签名位还原。
    uint8_t copy[16];
    std::memcpy(copy, frame, sizeof(copy));
    RestoreCanary2Inplace(copy, 2, bg, bg);
    Check(std::memcmp(copy + 0, bg, 4) == 0 && std::memcmp(copy + 4, bg, 4) == 0,
          "reported 原像素恢复精确");
    std::memcpy(copy, frame, sizeof(copy));
    RestoreCanary2Inplace(copy, 2, nullptr, nullptr);
    Check(std::memcmp(copy + 0, bg, 4) == 0 && std::memcmp(copy + 4, bg, 4) == 0,
          "neighbor 行 1 恢复精确（fixture 等值）");

    // 期望帧 != f 时必须拒绝且归因为 f（单例性质直接蕴含）。
    if (f > 0) {
      const uint32_t other = f - 1;
      const GateResult r = VerifyCanary2(frame, 2, other, true);
      Check(!r.accepted && r.rendered_frame == f && r.reason == GateReason::kStaleRendered,
            "陈旧观感对邻帧期望拒绝且归因自身");
    }
  }
  Check(stale_sing == 0, "exact 联合归因全域单例==自身");
  Check(tol1_stale_sing == 0, "tol1 联合归因全域单例==自身（±1 零穿透）");
  if (verbose) {
    std::printf("  exhaustive: 65536 frames, exact_single_viol=%u tol1_single_viol=%u\n",
                stale_sing, tol1_stale_sing);
  }
}

}  // namespace

int Canary2Selftest(bool verbose, bool json) {
  g_failures = 0;
  if (!json) std::printf("canary_v2 selftest（v2 双像素，全域穷举）…\n");
  TestInverseTables();
  TestExhaustiveFrames(verbose);
  if (json) {
    std::printf("{\"schema\":\"vtx.elec.canary_v2.selftest/1\",\"ok\":%s,\"failures\":%d}\n",
                g_failures == 0 ? "true" : "false", g_failures);
  } else {
    std::printf("%s canary_v2 selftest：failures=%d\n",
                g_failures == 0 ? "✓" : "✗", g_failures);
  }
  return g_failures;
}

}  // namespace vtx_elec
