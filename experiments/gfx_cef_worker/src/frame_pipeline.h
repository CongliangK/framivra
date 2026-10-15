// frame_pipeline.h — OnPaint 帧后处理原语（issue #110 CEF-02a）。
//
// 面向 4-Slot 环形池的三个纯函数件（无 CEF 依赖，selftest 可独立驱动）：
//   1) CopyCompactRows —— 任意源行步长的逐行紧凑拷贝（stride ≠ width*4
//      时杜绝跨行错位斜纹；工单红线项）；
//   2) DecodeCanaryFrameIndex —— (0,0) 像素金丝雀帧号核实（工单
//      canary_frame_index 的数据面）；
//   3) UnpremultiplyToRgba —— 预乘 BGRA → 直通 RGBA 的 SIMD 逆预乘
//      （256×256 精确 LUT + AVX2 gather；与 #109 标量公式逐值一致，
//      selftest 穷举 (c,a) 全组合验证；整帧不透明走纯通道交换快路径）。
#ifndef GFX_CEF_WORKER_FRAME_PIPELINE_H_
#define GFX_CEF_WORKER_FRAME_PIPELINE_H_

#include <cstddef>
#include <cstdint>

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

}  // namespace vtx_cef

#endif  // GFX_CEF_WORKER_FRAME_PIPELINE_H_
