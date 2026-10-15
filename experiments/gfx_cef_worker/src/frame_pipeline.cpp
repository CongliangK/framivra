// frame_pipeline.cpp — 帧后处理原语实现（issue #110）。
#include "frame_pipeline.h"

#include <immintrin.h>

#include <cstring>

namespace vtx_cef {
namespace {

// 256×256 精确 LUT：lut[a*256+c] = (c*255 + a/2)/a（a=0 行为 0）。
// 与 #109 标量公式同式生成 —— SIMD 路径与标量参考逐值一致由构造保证，
// shm_ring_selftest 穷举 (c,a) 全组合复核。
struct UnpremulLut {
  uint8_t v[256 * 256];
  UnpremulLut() {
    std::memset(v, 0, sizeof(v));
    for (int a = 1; a <= 255; ++a) {
      for (int c = 0; c <= 255; ++c) {
        // 与 #109 标量公式同式（含 >255 截断：c*255/a 在 a<255 时可越界，
        // 如 c=255,a=254 → 256 → 255）。
        const uint32_t q = (c * 255 + a / 2) / a;
        v[a * 256 + c] =
            static_cast<uint8_t>(q > 255u ? 255u : q);
      }
    }
  }
};
const UnpremulLut& Lut() {
  static const UnpremulLut lut;  // C++11 起线程安全一次初始化
  return lut;
}

inline bool CpuHasAvx2() {
#if defined(__x86_64__) || defined(__i386__)
  return __builtin_cpu_supports("avx2");
#else
  return false;
#endif
}

}  // namespace

void CopyCompactRows(const uint8_t* src, size_t src_stride, uint8_t* dst,
                     uint32_t width, uint32_t height) {
  const size_t row = static_cast<size_t>(width) * 4u;
  if (src_stride == row) {
    std::memcpy(dst, src, row * height);  // 紧凑源：整块拷贝
    return;
  }
  for (uint32_t y = 0; y < height; ++y) {
    std::memcpy(dst + row * y, src + src_stride * y, row);  // 逐行紧凑
  }
}

bool DecodeCanaryFrameIndex(const uint8_t* compact_bgra, uint32_t width,
                            uint32_t height, int32_t* frame_out) {
  if (width == 0 || height == 0 || compact_bgra == nullptr) {
    return false;
  }
  const uint8_t b = compact_bgra[0];
  const uint8_t g = compact_bgra[1];
  const uint8_t r = compact_bgra[2];
  const uint8_t a = compact_bgra[3];
  if (a != 255 || r != 0x43) {  // 不透明 + 'C' 魔位（预乘==直通）
    return false;
  }
  *frame_out = static_cast<int32_t>(g) | (static_cast<int32_t>(b) << 8);
  return true;
}

uint8_t UnpremultiplyScalar(uint8_t c, uint8_t a) {
  if (a == 0 || a == 255) {
    return a == 0 ? 0 : c;
  }
  const uint32_t v = (static_cast<uint32_t>(c) * 255u + a / 2u) / a;
  return v > 255u ? 255u : static_cast<uint8_t>(v);
}

void UnpremultiplyToRgba(const uint8_t* bgra_premul, size_t byte_len,
                         uint8_t* rgba_out) {
  if (byte_len % 4 != 0) {
    return;  // 契约：调用方保证 4 的倍数（防御，静默不动）
  }
  const size_t pixels = byte_len / 4;
  const uint8_t* lut = Lut().v;

  // —— 快路径判定：整帧不透明（alpha 恒 255）→ 逆预乘退化通道交换。
  if (CpuHasAvx2()) {
    const __m256i ff = _mm256_set1_epi32(static_cast<int>(0xFF000000u));
    bool opaque = true;
    size_t i = 0;
    for (; i + 8 <= pixels; i += 8) {
      const __m256i px = _mm256_loadu_si256(
          reinterpret_cast<const __m256i*>(bgra_premul + i * 4));
      const __m256i eq = _mm256_cmpeq_epi32(
          _mm256_and_si256(px, ff), ff);  // 每像素 alpha 字节 == 0xFF？
      if (_mm256_movemask_epi8(eq) != -1) {
        opaque = false;
        break;
      }
    }
    if (opaque) {
      for (; i < pixels; ++i) {
        const uint8_t* s = bgra_premul + i * 4;
        if (s[3] != 255) {
          opaque = false;
          break;
        }
      }
    }
    if (opaque) {  // 纯交换：BGRA → RGBA（含尾部标量收尾）
      i = 0;
      for (; i + 8 <= pixels; i += 8) {
        const __m256i px = _mm256_loadu_si256(
            reinterpret_cast<const __m256i*>(bgra_premul + i * 4));
        // 32 位字内字节序反转（B,G,R,A → A,R,G,B 后再掩回 BGRA 交换语义）
        const __m256i br = _mm256_shuffle_epi8(
            px, _mm256_setr_epi8(2, 1, 0, 3, 6, 5, 4, 7, 10, 9, 8, 11, 14, 13,
                                 12, 15, 2, 1, 0, 3, 6, 5, 4, 7, 10, 9, 8, 11,
                                 14, 13, 12, 15));
        _mm256_storeu_si256(reinterpret_cast<__m256i*>(rgba_out + i * 4), br);
      }
      for (; i < pixels; ++i) {
        const uint8_t* s = bgra_premul + i * 4;
        uint8_t* d = rgba_out + i * 4;
        d[0] = s[2]; d[1] = s[1]; d[2] = s[0]; d[3] = s[3];
      }
      return;
    }
  }

  // —— 通用路径（含透明像素）：LUT + AVX2 gather，8 像素一步。
  if (CpuHasAvx2()) {
    const __m256i mask_ff = _mm256_set1_epi32(0xFF);
    size_t i = 0;
    for (; i + 8 <= pixels; i += 8) {
      const uint8_t* s = bgra_premul + i * 4;
      alignas(32) uint32_t chan[4][8];   // [B,G,R,A]
      alignas(32) uint32_t out[4][8];    // [R,G,B,A]（解码后）
      for (int k = 0; k < 8; ++k) {
        chan[0][k] = s[k * 4 + 0];
        chan[1][k] = s[k * 4 + 1];
        chan[2][k] = s[k * 4 + 2];
        chan[3][k] = s[k * 4 + 3];
      }
      for (int c = 0; c < 3; ++c) {      // 三色通道逐一 LUT 逆预乘
        const __m256i vc = _mm256_load_si256(
            reinterpret_cast<const __m256i*>(chan[c]));
        const __m256i va = _mm256_load_si256(
            reinterpret_cast<const __m256i*>(chan[3]));
        const __m256i vidx = _mm256_add_epi32(
            _mm256_slli_epi32(va, 8), vc);
        const __m256i vg = _mm256_i32gather_epi32(
            reinterpret_cast<const int*>(lut), vidx, 1);
        _mm256_store_si256(reinterpret_cast<__m256i*>(out[c]),
                           _mm256_and_si256(vg, mask_ff));
      }
      uint8_t* d = rgba_out + i * 4;
      for (int k = 0; k < 8; ++k) {      // BGRA→RGBA 重排
        d[k * 4 + 0] = static_cast<uint8_t>(out[2][k]);
        d[k * 4 + 1] = static_cast<uint8_t>(out[1][k]);
        d[k * 4 + 2] = static_cast<uint8_t>(out[0][k]);
        d[k * 4 + 3] = static_cast<uint8_t>(chan[3][k]);
      }
    }
    for (; i < pixels; ++i) {            // 尾部标量
      const uint8_t* sp = bgra_premul + i * 4;
      uint8_t* d = rgba_out + i * 4;
      const uint8_t a = sp[3];
      d[0] = lut[a * 256 + sp[2]];
      d[1] = lut[a * 256 + sp[1]];
      d[2] = lut[a * 256 + sp[0]];
      d[3] = a;
    }
    return;
  }

  // 非 AVX2 兜底（v3 目标面正常不达）：LUT 标量。
  for (size_t i = 0; i < pixels; ++i) {
    const uint8_t* s = bgra_premul + i * 4;
    uint8_t* d = rgba_out + i * 4;
    const uint8_t a = s[3];
    d[0] = lut[a * 256 + s[2]];
    d[1] = lut[a * 256 + s[1]];
    d[2] = lut[a * 256 + s[0]];
    d[3] = a;
  }
}

}  // namespace vtx_cef
