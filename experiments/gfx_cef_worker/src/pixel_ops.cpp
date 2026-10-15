// pixel_ops.cpp — AVX2/NEON SIMD BGRA 逆预乘算子实现（issue #111 CEF-02b）。
//
// 设计（工单规格 → 实现映射）：
//   1. 65,536 项预计算 LUT：lut[a*256+c] = a==0 ? 0 : min(255,(c*255+a/2)/a)。
//      LUT[255][c] = c（恒等）、LUT[0][c] = 0（脏 RGB 清零）—— 表本身即完整
//      规格，三支语义无需运行时分支，SIMD 与标量路径共用同表=逐值一致。
//   2. AVX2 单遍混合式（替代 #110 的「先全域扫描判不透明再整块换」两遍式：
//      混合 alpha 帧免第二遍全量重读，不透明帧仍是纯 shuffle 流）：
//        逐 8 像素块：alpha 全 255 → _mm256_shuffle_epi8 纯 B/R 交换（零除法、
//        零查表）；否则三色通道 gather LUT 查表（4 字节 gather + 尾部 4 字节
//        填充防越界读），输出 = A | lutR<<16 | lutG<<8 | lutB。
//   3. NEON（aarch64，编译期门控；x86 产物不编入）：逐 16 像素块全不透明走
//      vld4q_u8(B,G,R,A)+vst4q_u8(R,G,B,A) 结构化交换（零算术）；混合块走
//      同一 LUT 的精确标量查表（与 AVX2 路径同表同值）。
//   4. 无 AVX2/NEON → LUT 标量兜底（与 SIMD 路径逐字节一致）。
#include "pixel_ops.h"

#if defined(__x86_64__) || defined(__i386__)
#include <immintrin.h>
#define VTX_PIXEL_OPS_X86 1
#elif defined(__aarch64__) || defined(__ARM_NEON)
#include <arm_neon.h>
#define VTX_PIXEL_OPS_NEON 1
#endif

#include <cstring>

namespace vtx_cef {
namespace {

// 65,536 项 LUT + 4 字节填充：AVX2 gather 以 scale=1 取 4 字节，
// idx=0xFFFF 时会读 lut[0xFFFF..0x10002] —— 无填充则越界 3 字节（UB）。
struct UnpremulLut {
  uint8_t v[256 * 256 + 4];
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

#if VTX_PIXEL_OPS_X86
inline bool CpuHasAvx2() { return __builtin_cpu_supports("avx2"); }
#endif

// ---------------------------------------------------------------- x86 AVX2
#if VTX_PIXEL_OPS_X86

// BGRA→RGBA 纯通道交换（B,R 字节对调；每 128 位 lane 内独立）。
inline __m256i ShuffleSwapBgraToRgba(__m256i px) {
  return _mm256_shuffle_epi8(
      px, _mm256_setr_epi8(2, 1, 0, 3, 6, 5, 4, 7, 10, 9, 8, 11, 14, 13, 12,
                           15, 2, 1, 0, 3, 6, 5, 4, 7, 10, 9, 8, 11, 14, 13,
                           12, 15));
}

// 8 像素通用块：三色通道 LUT gather（含 A==0/A==255 支，语义完备）。
inline __m256i UnpremulBlock8(const __m256i px, const uint8_t* lut) {
  // 像素字（小端 u32）= 0xAARRGGBB：byte0=B, byte1=G, byte2=R, byte3=A。
  const __m256i a_hi = _mm256_and_si256(
      _mm256_srli_epi32(px, 16), _mm256_set1_epi32(0x0000FF00));  // A<<8
  const __m256i b_ch = _mm256_and_si256(px, _mm256_set1_epi32(0xFF));
  const __m256i g_ch = _mm256_and_si256(_mm256_srli_epi32(px, 8),
                                        _mm256_set1_epi32(0xFF));
  const __m256i r_ch = _mm256_and_si256(_mm256_srli_epi32(px, 16),
                                        _mm256_set1_epi32(0xFF));
  const __m256i idx_b = _mm256_or_si256(a_hi, b_ch);  // (A<<8)|B
  const __m256i idx_g = _mm256_or_si256(a_hi, g_ch);
  const __m256i idx_r = _mm256_or_si256(a_hi, r_ch);
  const __m256i gb = _mm256_i32gather_epi32(reinterpret_cast<const int*>(lut),
                                            idx_b, 1);
  const __m256i gg = _mm256_i32gather_epi32(reinterpret_cast<const int*>(lut),
                                            idx_g, 1);
  const __m256i gr = _mm256_i32gather_epi32(reinterpret_cast<const int*>(lut),
                                            idx_r, 1);
  const __m256i m255 = _mm256_set1_epi32(0xFF);
  // 输出 lane（小端 u32 = 0xAABBGGRR）：byte0=R、byte1=G、byte2=B、byte3=A。
  // R 结果原位（byte0）、G 左移 8（byte1）、B 左移 16（byte2）。
  return _mm256_or_si256(
      _mm256_and_si256(px, _mm256_set1_epi32((int)0xFF000000u)),
      _mm256_or_si256(
          _mm256_and_si256(gr, m255),
          _mm256_or_si256(_mm256_slli_epi32(_mm256_and_si256(gg, m255), 8),
                          _mm256_slli_epi32(_mm256_and_si256(gb, m255), 16))));
}

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
      _mm256_storeu_si256(reinterpret_cast<__m256i*>(dst + i * 4),
                          UnpremulBlock8(px, lut));
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
    // 混合块：同一 LUT 精确查表（与 AVX2/标量同值；NEON 无 gather 指令）。
    for (size_t k = 0; k < 16; ++k) {
      const uint32_t a = bgra.val[3][k];
      uint8_t* d = dst + (i + k) * 4;
      d[0] = lut[(a << 8) | bgra.val[2][k]];
      d[1] = lut[(a << 8) | bgra.val[1][k]];
      d[2] = lut[(a << 8) | bgra.val[0][k]];
      d[3] = static_cast<uint8_t>(a);
    }
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
