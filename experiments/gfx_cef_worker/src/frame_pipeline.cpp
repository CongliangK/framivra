// frame_pipeline.cpp — 帧后处理原语实现（issue #110；#111 逆预乘迁至 pixel_ops）。
#include "frame_pipeline.h"

#include <cstring>

#include "pixel_ops.h"

namespace vtx_cef {

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

// #110 兼容包装：byte_len 口径 → #111 正式算子（pixel_ops.cpp 单一实现源，
// shm_ring_selftest 穷举语义不变——快路径/gather 路径见 pixel_ops.cpp 注释）。
void UnpremultiplyToRgba(const uint8_t* bgra_premul, size_t byte_len,
                         uint8_t* rgba_out) {
  if (byte_len % 4 != 0) {
    return;  // 契约：调用方保证 4 的倍数（防御，静默不动）
  }
  unpremultiply_bgra_to_rgba(bgra_premul, rgba_out, byte_len / 4);
}

}  // namespace vtx_cef
