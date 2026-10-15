// pixel_ops.h — AVX2/NEON SIMD BGRA 逆预乘算子（issue #111 CEF-02b）。
//
// 工单正式算子（确定性规格）：
//   unpremultiply_bgra_to_rgba(src_bgra, dst_rgba, num_pixels)
//     A == 0     → R=G=B=A=0（清理脏透明背景 RGB 垃圾值，提升 PNG 压缩率）
//     A == 255   → 仅交换 B/R（AVX2 _mm256_shuffle_epi8 / NEON vld4+vst4 快路径）
//     0 < A < 255→ 查表定点四舍五入逆预乘 min(255, (C*255 + A/2)/A) 并交换 B/R
//
// 确定性口径：65,536 项预计算 LUT（UNPREMUL_LUT[a][c]，含上表三支语义；
// LUT[255][c]==c 恒等、LUT[0][c]==0），与 #109 标量参考公式逐值一致 ——
// 由 pixel_ops_selftest 对 (c,a)∈[0,255]² 全域穷举复核（非抽检）。
//
// 后端分派（运行时 CPU 探测，进程内缓存）：
//   x86_64 + AVX2 → 单遍混合式：逐 8 像素块全不透明走 shuffle 纯交换，
//                   否则走 4 字节 gather LUT 查表（同一块内混合 alpha 也正确）
//   aarch64 + NEON→ 逐 16 像素块：全不透明 vld4q/vst4q 结构交换快路径；
//                   否则 LUT 精确标量（与 AVX2 路径逐值同源）
//   其他          → LUT 标量兜底（与 SIMD 路径逐字节一致）
//
// 本算子为路线 A/B 共享原生组件（#113 Electron 双通路输出桥接复用）：
// 无 CEF 依赖、无全局状态（除 const LUT），可独立链接进 shm_ring_selftest。
#ifndef GFX_CEF_WORKER_PIXEL_OPS_H_
#define GFX_CEF_WORKER_PIXEL_OPS_H_

#include <cstddef>
#include <cstdint>

namespace vtx_cef {

// #109 标量参考公式（穷举验证基准）：
//   a==0 → 0；a==255 → c；否则 min(255, (c*255 + a/2)/a)。
uint8_t UnpremultiplyScalar(uint8_t c, uint8_t a);

// LUT 直读（UNPREMUL_LUT[a][c]；供穷举验证/测试消费，不进热路径）。
uint8_t UnpremulLutValue(uint8_t a, uint8_t c);

// 工单正式算子：预乘 BGRA（OnPaint 源面字节序 B,G,R,A）→ 直通 RGBA。
// 契约：num_pixels 像素；src/dst 不得别名（重叠区未定义）；
// dst 容量 ≥ num_pixels*4。任何输入输出确定（无数据依赖分支以外的分歧面）。
void unpremultiply_bgra_to_rgba(const uint8_t* src_bgra, uint8_t* dst_rgba,
                                size_t num_pixels);

// 本进程实际生效的后端名（"avx2"/"neon"/"scalar"；证据/回执用）。
const char* UnpremultiplyBackendName();

}  // namespace vtx_cef

#endif  // GFX_CEF_WORKER_PIXEL_OPS_H_
