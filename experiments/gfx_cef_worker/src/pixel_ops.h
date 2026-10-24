// pixel_ops.h — AVX2/NEON SIMD BGRA 逆预乘算子（issue #111 CEF-02b）。
//
// 工单正式算子（确定性规格）：
//   unpremultiply_bgra_to_rgba(src_bgra, dst_rgba, num_pixels)
//     A == 0     → R=G=B=A=0（清理脏透明背景 RGB 垃圾值，提升 PNG 压缩率）
//     A == 255   → 仅交换 B/R（AVX2 _mm256_shuffle_epi8 / NEON vld4+vst4 快路径）
//     0 < A < 255→ 查表定点四舍五入逆预乘 min(255, (C*255 + A/2)/A) 并交换 B/R
//
// Exact integer division, not a reciprocal approximation:
//   d=max(A,1), n=C*255+A/2, r=min(65535,floor(65536/d));
//   q0=(n*r)>>16 is at most one below floor(n/d), because n<65536;
//   q=q0+((q0+1)*d<=n), then saturate at 255 and clear A==0.
// AVX2 gathers eight reciprocals from a 1 KiB table; NEON gathers sixteen
// reciprocals once and vectorizes all three channels. A 65,536-entry LUT remains
// the scalar fallback/reference. Full (C,A) domain tests check both formulas.
//
// Runtime backend selection (cached once per process):
//   x86_64 + AVX2: 8 pixels/block; opaque shuffle, binary-alpha shuffle/mask,
//                  exact reciprocal/correction for general alpha.
//   aarch64 + NEON: 16 pixels/block with the same three cases.
//   otherwise: scalar LUT fallback. All outputs are byte-identical.
//
// 本算子为路线 A/B 共享原生组件（#113 Electron 双通路输出桥接复用）：
// 无 CEF 依赖、查表只读，可独立链接进 shm_ring_selftest。
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
