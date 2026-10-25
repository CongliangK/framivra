// frame_pipeline.h — OnPaint 帧后处理原语（issue #110 CEF-02a）。
//
// 面向 4-Slot 环形池的三个纯函数件（无 CEF 依赖，selftest 可独立驱动）：
//   1) CopyCompactRows —— 任意源行步长的逐行紧凑拷贝（stride ≠ width*4
//      时杜绝跨行错位斜纹；工单红线项）；
//   2) DecodeCanaryFrameIndex —— (0,0) 像素金丝雀帧号核实（工单
//      canary_frame_index 的数据面）；
//   3) UnpremultiplyToRgba —— 预乘 BGRA → 直通 RGBA 的 SIMD 逆预乘
//      （实现在 pixel_ops.{h,cpp}：AVX2/NEON 精确整数逆元修正 + 标量 LUT；
//      与 #109 标量公式逐值一致，selftest 穷举
//      (c,a) 全组合验证；本符号保留为 byte_len 口径的兼容包装）。
#ifndef GFX_CEF_WORKER_FRAME_PIPELINE_H_
#define GFX_CEF_WORKER_FRAME_PIPELINE_H_

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <limits>

#include "pixel_ops.h"
#include "png_writer.h"

namespace vtx_cef {

// 紧凑目标行步长（恒等 width*4）。
inline uint32_t CompactStride(uint32_t width) { return width * 4u; }

// 逐行紧凑拷贝：src 行步长任意（OnPaint 紧凑缓冲步长即 width*4；
// dirtyRects 外包框/对齐缓冲可能更宽），dst 必须有 height*width*4 容量。
void CopyCompactRows(const uint8_t* src, size_t src_stride, uint8_t* dst,
                     uint32_t width, uint32_t height);

// 金丝雀编码（pages/frame_canary.html 约定）：
//   (0,0) BGRA 不透明像素：R=0x43('C')，G=f&0xFF，B=(f>>8)&0xFF，A=255。
// 解码失败（透明/魔位不符）返回 false。
bool DecodeCanaryFrameIndex(const uint8_t* compact_bgra, uint32_t width,
                            uint32_t height, int32_t* frame_out);

// #109 标量参考公式（穷举验证基准）：(c*255 + a/2)/a。
uint8_t UnpremultiplyScalar(uint8_t c, uint8_t a);

// SIMD 逆预乘 + BGRA→RGBA 通道重排。byte_len 必须为 4 的倍数；
// rgba_out 与 bgra_premul 不得别名。整帧 alpha==255 时走纯交换快路径。
void UnpremultiplyToRgba(const uint8_t* bgra_premul, size_t byte_len,
                         uint8_t* rgba_out);

// Fused CPU/PNG path. The input remains borrowed only until this synchronous
// call returns; it is never retained by the workspace or the encoded result.
// Compact or padded top-down BGRA is accepted; it must not alias workspace
// storage. spng encodes cache-hot converted tiles rather than writing and
// reading a complete intermediate RGBA frame.
// alpha_us receives conversion-only time, including across tile boundaries.
// Keep this adapter inline so users of the codec alone need not link pixel_ops.
inline bool EncodePngBgraPremultiplied(
    const uint8_t* bgra, int width, int height, size_t stride,
    const PngEncodeOptions& opt, PngEncodeWorkspace* workspace,
    PngBuffer* png, uint64_t* alpha_us = nullptr) {
  if (alpha_us != nullptr) *alpha_us = 0;
  if (bgra == nullptr || width <= 0 || height <= 0 || workspace == nullptr ||
      png == nullptr ||
      static_cast<size_t>(width) > std::numeric_limits<size_t>::max() / 4u) {
    return false;
  }
  const size_t row_bytes = static_cast<size_t>(width) * 4u;
  const size_t rows = static_cast<size_t>(height);
  if (stride < row_bytes ||
      rows > std::numeric_limits<size_t>::max() / row_bytes ||
      (rows - 1) > (std::numeric_limits<size_t>::max() - row_bytes) / stride) {
    return false;
  }
  struct Source {
    const uint8_t* bgra;
    size_t stride, row_bytes;
    uint64_t alpha_ns = 0;
    bool timed = false;
  } source{bgra, stride, row_bytes, 0, alpha_us != nullptr};
  const auto read_rows = [](void* user, size_t first, size_t count,
                            uint8_t* rgba) -> bool {
    auto& s = *static_cast<Source*>(user);
    using Clock = std::chrono::steady_clock;
    const auto begin = s.timed ? Clock::now() : Clock::time_point{};
    if (s.stride == s.row_bytes) {
      unpremultiply_bgra_to_rgba(s.bgra + first * s.stride, rgba,
                                  count * (s.row_bytes / 4u));
    } else {
      for (size_t row = 0; row < count; ++row) {
        unpremultiply_bgra_to_rgba(s.bgra + (first + row) * s.stride,
                                    rgba + row * s.row_bytes, s.row_bytes / 4u);
      }
    }
    if (s.timed) {
      s.alpha_ns += static_cast<uint64_t>(
          std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - begin)
              .count());
    }
    return true;
  };
  const bool ok = EncodePngRgbaRows(width, height, opt, read_rows, &source,
                                    workspace, png);
  if (alpha_us != nullptr) *alpha_us = source.alpha_ns / 1000u;
  return ok;
}

}  // namespace vtx_cef

#endif  // GFX_CEF_WORKER_FRAME_PIPELINE_H_
