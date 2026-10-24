// pixel_ops.cpp — exact SIMD BGRA unpremultiply + RGBA reorder.
//
// Opaque blocks need only a channel shuffle; binary-alpha blocks also mask
// transparent RGB to zero. General AVX2/NEON blocks share one small per-alpha
// integer reciprocal across RGB, then correct the quotient exactly. Scalar
// tails/fallback retain the exhaustive 65,536-entry reference LUT. There is no
// approximate division and no whole-frame alpha pre-scan.
#include "pixel_ops.h"

#if defined(__x86_64__) || defined(__i386__)
#include <immintrin.h>
#define VTX_PIXEL_OPS_X86 1
#elif defined(__aarch64__) || defined(__ARM_NEON)
#include <arm_neon.h>
#define VTX_PIXEL_OPS_NEON 1
#endif

#include <array>
#include <cstring>

namespace vtx_cef {
namespace {

// Full-domain scalar reference, also used for the short SIMD tail. The SIMD
// general path no longer gathers three unaligned 32-bit values from this LUT.
struct UnpremulLut {
  uint8_t v[256 * 256];
  UnpremulLut() {
    std::memset(v, 0, sizeof(v));
    for (int a = 1; a <= 255; ++a) {
      for (int c = 0; c <= 255; ++c) {
        // 与 #109 标量公式同式（含 >255 截断：c=255,a=254 → 256 → 255）。
        const uint32_t q = (c * 255 + a / 2) / a;
        v[a * 256 + c] = static_cast<uint8_t>(q > 255u ? 255u : q);
      }
    }
  }
};

const UnpremulLut& Lut() {
  static const UnpremulLut lut;  // C++11 起线程安全一次初始化
  return lut;
}

// Small, exact division reciprocals. With d=max(alpha,1), n<=65152 and
// r=min(65535,floor(65536/d)), floor(n*r/65536) is at most one below floor(n/d)
// and never above it. One integer remainder comparison gives the exact quotient.
// No floating-point approximation or per-channel 64 KiB LUT gather is needed.
[[maybe_unused]] const std::array<uint32_t, 256>& Reciprocals() {
  static constexpr auto table = [] {
    std::array<uint32_t, 256> values{};
    values[0] = values[1] = 65535;
    for (size_t a = 2; a < values.size(); ++a) values[a] = 65536u / a;
    return values;
  }();
  return table;
}

#if VTX_PIXEL_OPS_X86
inline bool CpuHasAvx2() { return __builtin_cpu_supports("avx2"); }
#endif

// ---------------------------------------------------------------- x86 AVX2
#if VTX_PIXEL_OPS_X86

// BGRA→RGBA 纯通道交换（B,R 字节对调；每 128 位 lane 内独立）。
__attribute__((target("avx2")))
inline __m256i ShuffleSwapBgraToRgba(__m256i px) {
  return _mm256_shuffle_epi8(
      px, _mm256_setr_epi8(2, 1, 0, 3, 6, 5, 4, 7, 10, 9, 8, 11, 14, 13, 12,
                           15, 2, 1, 0, 3, 6, 5, 4, 7, 10, 9, 8, 11, 14, 13,
                           12, 15));
}

__attribute__((target("avx2")))
inline __m256i UnpremulChannel8(__m256i c, __m256i half_alpha,
                                 __m256i denominator, __m256i reciprocal) {
  const __m256i one = _mm256_set1_epi32(1);
  const __m256i n = _mm256_add_epi32(
      _mm256_sub_epi32(_mm256_slli_epi32(c, 8), c), half_alpha);
  // n*r fits uint32 (<=65152*65535). Unsigned logical shift is intentional.
  __m256i q = _mm256_srli_epi32(_mm256_mullo_epi32(n, reciprocal), 16);
  const __m256i next_product = _mm256_mullo_epi32(_mm256_add_epi32(q, one),
                                                  denominator);
  // n and next_product are <=65407, so the signed comparison is also exact.
  q = _mm256_add_epi32(q, _mm256_and_si256(
      _mm256_cmpgt_epi32(_mm256_add_epi32(n, one), next_product), one));
  return _mm256_min_epu32(q, _mm256_set1_epi32(255));
}

// 8 general pixels: one small reciprocal-table gather, shared by RGB.
__attribute__((target("avx2")))
inline __m256i UnpremulBlock8(const __m256i px) {
  const __m256i a = _mm256_srli_epi32(px, 24);
  const __m256i half = _mm256_srli_epi32(a, 1);
  const __m256i denominator = _mm256_max_epu32(a, _mm256_set1_epi32(1));
  const __m256i reciprocal = _mm256_i32gather_epi32(
      reinterpret_cast<const int*>(Reciprocals().data()), a, 4);
  const __m256i mask = _mm256_set1_epi32(255);
  const __m256i b = UnpremulChannel8(_mm256_and_si256(px, mask), half,
                                     denominator, reciprocal);
  const __m256i g = UnpremulChannel8(
      _mm256_and_si256(_mm256_srli_epi32(px, 8), mask), half, denominator, reciprocal);
  const __m256i r = UnpremulChannel8(
      _mm256_and_si256(_mm256_srli_epi32(px, 16), mask), half, denominator, reciprocal);
  const __m256i rgb = _mm256_or_si256(r, _mm256_or_si256(
      _mm256_slli_epi32(g, 8), _mm256_slli_epi32(b, 16)));
  return _mm256_or_si256(_mm256_slli_epi32(a, 24), _mm256_and_si256(
      rgb, _mm256_cmpgt_epi32(a, _mm256_setzero_si256())));
}

__attribute__((target("avx2")))
void UnpremultiplyAvx2(const uint8_t* src, uint8_t* dst, size_t n) {
  const uint8_t* lut = Lut().v;
  const __m256i ff_alpha = _mm256_set1_epi32((int)0xFF000000u);
  size_t i = 0;
  for (; i + 8 <= n; i += 8) {
    const __m256i px = _mm256_loadu_si256(
        reinterpret_cast<const __m256i*>(src + i * 4));
    // 全不透明块判定：8 lane 的 alpha 字节全 0xFF → movemask 全 1。
    const __m256i eq = _mm256_cmpeq_epi32(_mm256_and_si256(px, ff_alpha),
                                          ff_alpha);
    if (_mm256_movemask_epi8(eq) == -1) {
      _mm256_storeu_si256(reinterpret_cast<__m256i*>(dst + i * 4),
                          ShuffleSwapBgraToRgba(px));  // 纯交换快路径
    } else {
      const __m256i transparent = _mm256_cmpeq_epi32(
          _mm256_and_si256(px, ff_alpha), _mm256_setzero_si256());
      if (_mm256_movemask_epi8(_mm256_or_si256(eq, transparent)) == -1) {
        // Most transparent canvas regions contain only alpha 0/255. A single
        // shuffle plus mask also clears dirty RGB at alpha=0, without 24 LUT
        // loads per block (or an extra whole-frame transparency scan).
        _mm256_storeu_si256(reinterpret_cast<__m256i*>(dst + i * 4),
                            _mm256_and_si256(ShuffleSwapBgraToRgba(px), eq));
      } else {
        _mm256_storeu_si256(reinterpret_cast<__m256i*>(dst + i * 4),
                            UnpremulBlock8(px));
      }
    }
  }
  for (; i < n; ++i) {  // 尾部标量（<8 像素；与 SIMD 同表）
    const uint8_t* s = src + i * 4;
    const uint32_t a = s[3];
    uint8_t* d = dst + i * 4;
    d[0] = lut[(a << 8) | s[2]];
    d[1] = lut[(a << 8) | s[1]];
    d[2] = lut[(a << 8) | s[0]];
    d[3] = static_cast<uint8_t>(a);
  }
}

// ---------------------------------------------------------------- aarch64 NEON
#elif VTX_PIXEL_OPS_NEON

inline uint8x8_t UnpremulChannel8(uint8x8_t c, uint16x8_t alpha,
                                  uint16x8_t denominator, uint16x8_t reciprocal) {
  const uint16x8_t one = vdupq_n_u16(1);
  const uint16x8_t n = vaddq_u16(vmulq_n_u16(vmovl_u8(c), 255),
                                 vshrq_n_u16(alpha, 1));
  uint16x8_t q = vcombine_u16(
      vshrn_n_u32(vmull_u16(vget_low_u16(n), vget_low_u16(reciprocal)), 16),
      vshrn_n_u32(vmull_u16(vget_high_u16(n), vget_high_u16(reciprocal)), 16));
  // q<=floor(n/d), hence (q+1)*d<=n+d<=65407: no 16-bit wraparound.
  const uint16x8_t next_product = vmulq_u16(vaddq_u16(q, one), denominator);
  q = vaddq_u16(q, vandq_u16(vcleq_u16(next_product, n), one));
  return vqmovn_u16(q);  // Exact min(255,q); alpha=0 is masked by the caller.
}

void UnpremultiplyNeon(const uint8_t* src, uint8_t* dst, size_t n) {
  const uint8_t* lut = Lut().v;
  size_t i = 0;
  for (; i + 16 <= n; i += 16) {
    // 结构化加载：b0=B[0::4] b1=G b2=R b3=A（16 lane）。
    const uint8x16x4_t bgra = vld4q_u8(src + i * 4);
    // 全不透明块判定：A 通道全 0xFF → vst4q 结构交换（R,G,B,A 写出即 RGBA）。
    const uint8x16_t all_ff = vdupq_n_u8(0xFF);
    if (vminvq_u8(vceqq_u8(bgra.val[3], all_ff)) != 0) {
      uint8x16x4_t rgba = {bgra.val[2], bgra.val[1], bgra.val[0],
                           bgra.val[3]};
      vst4q_u8(dst + i * 4, rgba);
      continue;
    }
    const uint8x16_t opaque = vceqq_u8(bgra.val[3], all_ff);
    const uint8x16_t transparent = vceqq_u8(bgra.val[3], vdupq_n_u8(0));
    if (vminvq_u8(vorrq_u8(opaque, transparent)) != 0) {
      const uint8x16x4_t rgba = {vandq_u8(bgra.val[2], opaque),
                                vandq_u8(bgra.val[1], opaque),
                                vandq_u8(bgra.val[0], opaque), bgra.val[3]};
      vst4q_u8(dst + i * 4, rgba);
      continue;
    }
    // NEON has no gather. Gather only one reciprocal per alpha from a tiny
    // 1 KiB table, then share it across all three vectorized color channels.
    uint16_t reciprocal_values[16];
    for (size_t lane = 0; lane < 16; ++lane) {
      reciprocal_values[lane] = static_cast<uint16_t>(
          Reciprocals()[src[(i + lane) * 4 + 3]]);
    }
    const uint16x8_t a_lo = vmovl_u8(vget_low_u8(bgra.val[3]));
    const uint16x8_t a_hi = vmovl_u8(vget_high_u8(bgra.val[3]));
    const uint16x8_t d_lo = vmaxq_u16(a_lo, vdupq_n_u16(1));
    const uint16x8_t d_hi = vmaxq_u16(a_hi, vdupq_n_u16(1));
    const uint16x8_t r_lo = vld1q_u16(reciprocal_values);
    const uint16x8_t r_hi = vld1q_u16(reciprocal_values + 8);
    uint8x16x4_t rgba;
    for (int channel = 0; channel < 3; ++channel) {
      const uint8x16_t color = bgra.val[2 - channel];
      rgba.val[channel] = vandq_u8(vmvnq_u8(transparent), vcombine_u8(
          UnpremulChannel8(vget_low_u8(color), a_lo, d_lo, r_lo),
          UnpremulChannel8(vget_high_u8(color), a_hi, d_hi, r_hi)));
    }
    rgba.val[3] = bgra.val[3];
    vst4q_u8(dst + i * 4, rgba);
  }
  for (; i < n; ++i) {  // 尾部标量（<16 像素）
    const uint8_t* s = src + i * 4;
    const uint32_t a = s[3];
    uint8_t* d = dst + i * 4;
    d[0] = lut[(a << 8) | s[2]];
    d[1] = lut[(a << 8) | s[1]];
    d[2] = lut[(a << 8) | s[0]];
    d[3] = static_cast<uint8_t>(a);
  }
}
#endif

// ---------------------------------------------------------------- 标量兜底
void UnpremultiplyScalarLoop(const uint8_t* src, uint8_t* dst, size_t n) {
  const uint8_t* lut = Lut().v;
  for (size_t i = 0; i < n; ++i) {
    const uint8_t* s = src + i * 4;
    const uint32_t a = s[3];
    uint8_t* d = dst + i * 4;
    d[0] = lut[(a << 8) | s[2]];
    d[1] = lut[(a << 8) | s[1]];
    d[2] = lut[(a << 8) | s[0]];
    d[3] = static_cast<uint8_t>(a);
  }
}

enum class Backend { kAvx2, kNeon, kScalar };

Backend DetectBackend() {
#if VTX_PIXEL_OPS_X86
  if (CpuHasAvx2()) {
    return Backend::kAvx2;
  }
#elif VTX_PIXEL_OPS_NEON
  return Backend::kNeon;
#endif
  return Backend::kScalar;
}

}  // namespace

uint8_t UnpremultiplyScalar(uint8_t c, uint8_t a) {
  if (a == 0 || a == 255) {
    return a == 0 ? 0 : c;
  }
  const uint32_t v = (static_cast<uint32_t>(c) * 255u + a / 2u) / a;
  return v > 255u ? 255u : static_cast<uint8_t>(v);
}

uint8_t UnpremulLutValue(uint8_t a, uint8_t c) {
  return Lut().v[(static_cast<uint32_t>(a) << 8) | c];
}

void unpremultiply_bgra_to_rgba(const uint8_t* src_bgra, uint8_t* dst_rgba,
                                size_t num_pixels) {
  if (src_bgra == nullptr || dst_rgba == nullptr || num_pixels == 0) {
    return;  // 契约：调用方保证非空/非零（防御，静默不动）
  }
  static const Backend backend = DetectBackend();  // 进程内一次探测
  switch (backend) {
#if VTX_PIXEL_OPS_X86
    case Backend::kAvx2:
      UnpremultiplyAvx2(src_bgra, dst_rgba, num_pixels);
      return;
#endif
#if VTX_PIXEL_OPS_NEON
    case Backend::kNeon:
      UnpremultiplyNeon(src_bgra, dst_rgba, num_pixels);
      return;
#endif
    default:
      UnpremultiplyScalarLoop(src_bgra, dst_rgba, num_pixels);
      return;
  }
}

const char* UnpremultiplyBackendName() {
  static const Backend backend = DetectBackend();
  switch (backend) {
    case Backend::kAvx2: return "avx2";
    case Backend::kNeon: return "neon";
    default: return "scalar";
  }
}

}  // namespace vtx_cef
