// pixel_ops_selftest.cpp — #111 原生自检与基准：SIMD 逆预乘算子 +
// libspng/fpng 双编码器 + 原子落盘（无 CEF 依赖）。
//
// 用法：
//   pixel_ops_selftest               正确性全套（PASS/FAIL；全部 PASS 退出 0）
//   pixel_ops_selftest --bench       追加 1080p 性能基准（含 4 线程并发编码）
//   pixel_ops_selftest --bench-json  基准结果以 BENCH_JSON:<单行 JSON> 输出
//                                    （tests/gfx/test_cef_worker_pixel_ops.py 采集）
//
// 正确性用例面（对应工单验收/父工单验证门）：
//   1. lut_full_domain    LUT == 标量公式：(c,a)∈[0,255]² 全域 65,536 组穷举
//                         （超集覆盖 A∈{0,1,2,127,128,254,255}×C 全通道）
//   2. simd_pixel_domain  正式算子（SIMD 分派）经 8 像素混合 alpha 块与全不
//                         透明块逐 (c,a) 穷举 == 标量公式（两条 SIMD 分支均覆盖）
//   3. simd_frames        随机/边界/全透明/奇尺寸整帧 SIMD == 标量参考逐字节
//   4. encoder_roundtrip  {spng L1, spng L3, fpng, zlib}×多帧 编码→libspng 解码
//                         == 输入逐字节；同输入复编字节逐位一致（确定性）
//   5. encoder_pair       同一帧 spng 与 fpng 产物像素等价（文件字节可不同，
//                         双路解码回的 RGBA 必须相同且==输入）
//   6. atomic_write       成功路径目标字节精确 + 目录零 *.tmp.* 残留；
//                         4 线程×50 文件并发写零残留；非法目录失败不崩溃
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <string>
#include <thread>
#include <vector>

#include "fpng.h"
#include "spng.h"

#include "pixel_ops.h"
#include "frame_pipeline.h"
#include "png_writer.h"

namespace {

int g_fail = 0;
std::vector<std::string> g_bench_json;

void Check(bool ok, const char* name, const std::string& detail) {
  if (ok) {
    std::printf("PASS %-20s %s\n", name, detail.c_str());
  } else {
    ++g_fail;
    std::printf("FAIL %-20s %s\n", name, detail.c_str());
  }
}

// double → JSON 数字（定 4 位小数；证据可解析）
std::string fmt(double v) {
  char buf[64];
  std::snprintf(buf, sizeof(buf), "%.4f", v);
  return buf;
}

// 确定性 LCG（自证可复现，与内容类共用）
uint32_t LcgNext(uint32_t* s) {
  *s = *s * 1664525u + 1013904223u;
  return *s;
}

// 标量参考整帧（逐像素调 UnpremultiplyScalar —— 与 SIMD/LUT 相互独立的第三实现）
void ScalarRefFrame(const std::vector<uint8_t>& bgra, std::vector<uint8_t>* out) {
  out->assign(bgra.size(), 0);
  for (size_t i = 0; i + 3 < bgra.size(); i += 4) {
    const uint8_t a = bgra[i + 3];
    (*out)[i] = vtx_cef::UnpremultiplyScalar(bgra[i + 2], a);      // R
    (*out)[i + 1] = vtx_cef::UnpremultiplyScalar(bgra[i + 1], a);  // G
    (*out)[i + 2] = vtx_cef::UnpremultiplyScalar(bgra[i], a);      // B
    (*out)[i + 3] = a;
  }
}

// ---------------------------------------------------------------- 内容类
// 与 S 系页面等效的压力面（issue 背景：径向渐变/高斯模糊=fpng 弱项）
enum class Content { kOpaqueFlat, kCanaryLike, kGradient, kNoise, kMixedAlpha };
const char* ContentName(Content c) {
  switch (c) {
    case Content::kOpaqueFlat: return "opaque_flat";
    case Content::kCanaryLike: return "canary_like";
    case Content::kGradient: return "gradient";
    case Content::kNoise: return "noise";
    default: return "mixed_alpha";
  }
}

// 生成直通 RGBA 帧（编码基准输入；非预乘语义）
std::vector<uint8_t> MakeFrame(Content kind, uint32_t w, uint32_t h) {
  std::vector<uint8_t> rgba(static_cast<size_t>(w) * h * 4u);
  const double cx = w / 2.0, cy = h / 2.0;
  uint32_t lcg = 0x12345u;
  for (uint32_t y = 0; y < h; ++y) {
    for (uint32_t x = 0; x < w; ++x) {
      uint8_t* p = &rgba[(static_cast<size_t>(y) * w + x) * 4];
      switch (kind) {
        case Content::kOpaqueFlat:
          p[0] = 0x30; p[1] = 0x60; p[2] = 0x90; p[3] = 255;
          break;
        case Content::kCanaryLike: {  // 硬边条纹+色块（frame_canary.html 同族）
          const uint32_t band = (x / 160) % 3;
          p[0] = static_cast<uint8_t>(band == 0 ? 0x20 : band == 1 ? 0x80 : 0xE0);
          p[1] = static_cast<uint8_t>((y / 90) * 40 + 20);
          p[2] = static_cast<uint8_t>(0x40 + band * 50);
          p[3] = 255;
          break;
        }
        case Content::kGradient: {  // 径向渐变+垂直渐变（平滑低频=编码压力面）
          const double dx = (x - cx) / cx, dy = (y - cy) / cy;
          const double r = sqrt(dx * dx + dy * dy);
          p[0] = static_cast<uint8_t>(std::clamp(255 * (1.0 - r) + 0.5, 0.0, 255.0));
          p[1] = static_cast<uint8_t>(255 * (1.0 - y / (double)h) + 0.5);
          p[2] = static_cast<uint8_t>(std::clamp(40 + 160 * r + 0.5, 0.0, 255.0));
          p[3] = 255;
          break;
        }
        case Content::kNoise: {  // 不可压缩上界
          p[0] = static_cast<uint8_t>(LcgNext(&lcg) >> 24);
          p[1] = static_cast<uint8_t>(LcgNext(&lcg) >> 24);
          p[2] = static_cast<uint8_t>(LcgNext(&lcg) >> 24);
          p[3] = 255;
          break;
        }
        case Content::kMixedAlpha: {  // 半透明梯度（逆预乘/alpha 语义面）
          p[0] = static_cast<uint8_t>(x & 255);
          p[1] = static_cast<uint8_t>(y & 255);
          p[2] = static_cast<uint8_t>((x ^ y) & 255);
          p[3] = static_cast<uint8_t>((x + y) & 255);
          break;
        }
      }
    }
  }
  return rgba;
}

uint64_t NowNs() {
  return static_cast<uint64_t>(
      std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::steady_clock::now().time_since_epoch()).count());
}

// ---------------------------------------------------------------- 用例
void TestLutFullDomain() {
  size_t mismatches = 0;
  for (int a = 0; a <= 255; ++a) {
    for (int c = 0; c <= 255; ++c) {
      const uint8_t want = vtx_cef::UnpremultiplyScalar(
          static_cast<uint8_t>(c), static_cast<uint8_t>(a));
      const uint8_t got = vtx_cef::UnpremulLutValue(
          static_cast<uint8_t>(a), static_cast<uint8_t>(c));
      if (got != want) {
        if (mismatches < 8) {
          std::printf("  c=%d a=%d want=%d got=%d\n", c, a, want, got);
        }
        ++mismatches;
      }
    }
  }
  Check(mismatches == 0, "lut_full_domain",
        "65,536 (c,a) 组全组合穷举 LUT==标量公式（mismatch=" +
            std::to_string(mismatches) +
            "；全域覆盖 A∈{0,1,2,127,128,254,255}×C∈[0,255] 全通道）");
}

void TestSimdPixelDomain() {
  size_t mismatches = 0;
  constexpr int kPixels = 32;  // Exercise both 8-lane AVX2 and 16-lane NEON.
  uint8_t block[kPixels * 4];
  uint8_t out[kPixels * 4];
  const uint8_t mixed_alpha[7] = {255, 0, 1, 128, 254, 127, 2};
  for (int a = 0; a <= 255 && mismatches == 0; ++a) {
    for (int c = 0; c <= 255; ++c) {
      // 块 0：混合 alpha 块（首像素=被测 (c,a)；其余 7 像素固定混合 alpha
      // → 必走通用整数修正路径），全块与标量参考逐字节对照。
      for (int k = 0; k < kPixels; ++k) {
        const int alpha = k == 0 ? a : mixed_alpha[(k - 1) % 7];
        const int chan = k == 0 ? c : (k * 37) & 255;
        block[k * 4 + 0] = static_cast<uint8_t>(chan);        // B
        block[k * 4 + 1] = static_cast<uint8_t>(chan ^ 0x5A); // G
        block[k * 4 + 2] = static_cast<uint8_t>(255 - chan);  // R
        block[k * 4 + 3] = static_cast<uint8_t>(alpha);       // A
      }
      vtx_cef::unpremultiply_bgra_to_rgba(block, out, kPixels);
      for (int k = 0; k < kPixels; ++k) {
        const uint8_t av = block[k * 4 + 3];
        const uint8_t wr = vtx_cef::UnpremultiplyScalar(block[k * 4 + 2], av);
        const uint8_t wg = vtx_cef::UnpremultiplyScalar(block[k * 4 + 1], av);
        const uint8_t wb = vtx_cef::UnpremultiplyScalar(block[k * 4 + 0], av);
        if (out[k * 4 + 0] != wr || out[k * 4 + 1] != wg ||
            out[k * 4 + 2] != wb || out[k * 4 + 3] != av) {
          if (mismatches < 8) {
            std::printf("  k=%d BGRA=(%d,%d,%d,%d) want=(%d,%d,%d,%d) "
                        "got=(%d,%d,%d,%d)\n", k, block[k*4], block[k*4+1],
                        block[k*4+2], av, wr, wg, wb, av, out[k*4],
                        out[k*4+1], out[k*4+2], out[k*4+3]);
          }
          ++mismatches;
          break;
        }
      }
      // 块 1：全不透明块（必走 shuffle 纯交换路径），每 c 值核对 B/R 交换。
      for (int k = 0; k < kPixels; ++k) {
        block[k * 4 + 0] = static_cast<uint8_t>(c + k);
        block[k * 4 + 1] = static_cast<uint8_t>((c * 3 + k) & 255);
        block[k * 4 + 2] = static_cast<uint8_t>(255 - c - k);
        block[k * 4 + 3] = 255;
      }
      vtx_cef::unpremultiply_bgra_to_rgba(block, out, kPixels);
      for (int k = 0; k < kPixels; ++k) {
        if (out[k * 4 + 0] != block[k * 4 + 2] ||
            out[k * 4 + 1] != block[k * 4 + 1] ||
            out[k * 4 + 2] != block[k * 4 + 0] || out[k * 4 + 3] != 255) {
          ++mismatches;
          break;
        }
      }
    }
  }
  Check(mismatches == 0, "simd_pixel_domain",
        "(c,a)∈[0,255]² 经 SIMD 双分支（reciprocal+shuffle/mask）穷举==标量，mismatch=" +
            std::to_string(mismatches) + "，backend=" +
            vtx_cef::UnpremultiplyBackendName());
}

void TestSimdFrames() {
  struct Case {
    const char* name;
    uint32_t w, h;
    Content content;
    bool all_transparent;
  };
  const Case cases[] = {
      {"1080p_mixed", 1920, 1080, Content::kMixedAlpha, false},
      {"odd_size_137x61", 137, 61, Content::kMixedAlpha, false},
      {"opaque", 320, 240, Content::kOpaqueFlat, false},
      {"all_transparent_dirty_rgb", 333, 77, Content::kOpaqueFlat, true},
  };
  size_t mismatches = 0;
  std::string detail;
  for (const Case& tc : cases) {
    // 构造预乘 BGRA 输入：直通 → 预乘（c*a/255，floor）模拟 OnPaint 面。
    std::vector<uint8_t> rgba = MakeFrame(tc.content, tc.w, tc.h);
    std::vector<uint8_t> bgra(rgba.size());
    for (size_t i = 0; i + 3 < rgba.size(); i += 4) {
      const uint32_t a = tc.all_transparent ? 0 : rgba[i + 3];
      bgra[i] = static_cast<uint8_t>(rgba[i + 2] * a / 255);      // B 预乘
      bgra[i + 1] = static_cast<uint8_t>(rgba[i + 1] * a / 255);  // G 预乘
      bgra[i + 2] = static_cast<uint8_t>(rgba[i] * a / 255);      // R 预乘
      bgra[i + 3] = static_cast<uint8_t>(a);
      if (tc.all_transparent) {  // 脏透明：RGB 塞垃圾值（A=0 强制清零面）
        bgra[i] = 0xAB; bgra[i + 1] = 0xCD; bgra[i + 2] = 0xEF;
      }
    }
    std::vector<uint8_t> simd_out(bgra.size());
    vtx_cef::unpremultiply_bgra_to_rgba(bgra.data(), simd_out.data(),
                                        bgra.size() / 4);
    std::vector<uint8_t> ref;
    ScalarRefFrame(bgra, &ref);
    if (simd_out != ref) {
      ++mismatches;
      detail += std::string(" ") + tc.name;
    }
    if (tc.all_transparent) {
      bool zeroed = true;
      for (size_t i = 0; i < simd_out.size(); ++i) {
        if (simd_out[i] != 0) { zeroed = false; break; }
      }
      if (!zeroed) {
        ++mismatches;
        detail += std::string(" ") + tc.name + "(A0脏RGB未清零)";
      }
    }
  }
  Check(mismatches == 0, "simd_frames",
        "整帧 SIMD==标量参考（1080p 混合 alpha/奇尺寸/不透明/全透明脏 RGB）"
        " mismatch=" + std::to_string(mismatches) + detail);
}

// libspng 解码（RGBA8）：任何标准 PNG（含 fpng/zlib 产物）→ 逐字节 RGBA。
bool DecodeRgbaViaSpng(const std::vector<uint8_t>& png, std::vector<uint8_t>* rgba,
                       uint32_t* w, uint32_t* h) {
  spng_ctx* ctx = spng_ctx_new(0);
  if (ctx == nullptr) return false;
  bool ok = false;
  do {
    if (spng_set_png_buffer(ctx, png.data(), png.size()) != 0) break;
    struct spng_ihdr ihdr;
    if (spng_get_ihdr(ctx, &ihdr) != 0) break;
    if (ihdr.bit_depth != 8 || ihdr.color_type != 6) break;
    size_t len = 0;
    if (spng_decoded_image_size(ctx, SPNG_FMT_RGBA8, &len) != 0) break;
    rgba->resize(len);
    if (spng_decode_image(ctx, rgba->data(), len, SPNG_FMT_RGBA8, 0) != 0) {
      break;
    }
    *w = ihdr.width;
    *h = ihdr.height;
    ok = true;
  } while (false);
  spng_ctx_free(ctx);
  return ok;
}

// Run before any backend/LUT/PNG helper warms its function-local statics.
// The ordinary concurrent test below starts from an already encoded reference
// and therefore cannot exercise C++ first-use initialization. This scenario
// also needs production-flag object builds: default pure-test flags alone do
// not detect CEF's target-wide -fno-threadsafe-statics override.
void TestColdConcurrentInitialization() {
  constexpr int kThreads = 16, width = 257, height = 5;
  constexpr size_t pixels = width * height;
  std::atomic<int> ready{0}, failures{0};
  std::atomic<bool> go{false};
  std::vector<std::thread> threads;
  for (int t = 0; t < kThreads; ++t) {
    threads.emplace_back([&, t] {
      std::vector<uint8_t> bgra(pixels * 4), expected(pixels * 4), rgba(pixels * 4);
      for (size_t p = 0; p < pixels; ++p) {
        const uint32_t alpha = (p * 71 + static_cast<unsigned>(t) * 37) & 255;
        for (size_t c = 0; c < 3; ++c) {
          bgra[p * 4 + c] = static_cast<uint8_t>(
              (p * 19 + c * 23 + static_cast<unsigned>(t)) & 255);
        }
        bgra[p * 4 + 3] = static_cast<uint8_t>(alpha);
        // Independent arithmetic oracle: no production helper before go.
        for (size_t c = 0; c < 3; ++c) {
          const uint32_t value = alpha ? (bgra[p * 4 + 2 - c] * 255 + alpha / 2) / alpha : 0;
          expected[p * 4 + c] = static_cast<uint8_t>(std::min(value, 255u));
        }
        expected[p * 4 + 3] = static_cast<uint8_t>(alpha);
      }
      ++ready;
      while (!go.load()) std::this_thread::yield();
      vtx_cef::unpremultiply_bgra_to_rgba(bgra.data(), rgba.data(), pixels);
      if (rgba != expected) ++failures;
      (void)vtx_cef::UnpremultiplyBackendName();
      const vtx_cef::PngEncodeOptions options;
      std::vector<uint8_t> png, decoded;
      uint32_t decoded_width = 0, decoded_height = 0;
      if (!vtx_cef::EncodePngRgba(rgba.data(), width, height, options, &png) ||
          !DecodeRgbaViaSpng(png, &decoded, &decoded_width, &decoded_height) ||
          decoded_width != width || decoded_height != height || decoded != expected) {
        ++failures;
      }
    });
  }
  while (ready.load() != kThreads) std::this_thread::yield();
  go.store(true);
  for (auto& thread : threads) thread.join();
  Check(failures.load() == 0, "cold_concurrent_init",
        "16 first callers: independent scalar pixels and PNG roundtrip; failures=" +
            std::to_string(failures.load()));
}

void TestEncoderRoundtrip() {
  struct Case {
    const char* name;
    uint32_t w, h;
    Content content;
  };
  const Case cases[] = {
      {"64x48_mixed", 64, 48, Content::kMixedAlpha},
      {"odd_61x13", 61, 13, Content::kMixedAlpha},
      {"320x240_gradient", 320, 240, Content::kGradient},
      {"320x240_noise", 320, 240, Content::kNoise},
      {"1080p_mixed", 1920, 1080, Content::kMixedAlpha},
      {"1080p_gradient", 1920, 1080, Content::kGradient},
  };
  const struct {
    const char* name;
    vtx_cef::PngEncoder enc;
    int level;
  } backends[] = {
      {"spng_L1", vtx_cef::PngEncoder::kSpng, 1},
      {"spng_L3", vtx_cef::PngEncoder::kSpng, 3},
      {"fpng", vtx_cef::PngEncoder::kFpng, 0},
      {"zlib_legacy", vtx_cef::PngEncoder::kZlib, 0},
  };
  size_t failures = 0;
  std::string detail;
  for (const Case& tc : cases) {
    const std::vector<uint8_t> frame = MakeFrame(tc.content, tc.w, tc.h);
    for (const auto& be : backends) {
      vtx_cef::PngEncodeOptions opt;
      opt.encoder = be.enc;
      opt.level = be.level > 0 ? be.level : 1;  // fpng/zlib 忽略
      std::vector<uint8_t> png1, png2;
      if (!vtx_cef::EncodePngRgba(frame.data(), static_cast<int>(tc.w),
                                  static_cast<int>(tc.h), opt, &png1) ||
          !vtx_cef::EncodePngRgba(frame.data(), static_cast<int>(tc.w),
                                  static_cast<int>(tc.h), opt, &png2)) {
        ++failures;
        detail += std::string(" ") + tc.name + "/" + be.name + "(encode)";
        continue;
      }
      if (png1 != png2) {  // 确定性：同输入复编逐位一致
        ++failures;
        detail += std::string(" ") + tc.name + "/" + be.name + "(determinism)";
        continue;
      }
      std::vector<uint8_t> decoded;
      uint32_t dw = 0, dh = 0;
      if (!DecodeRgbaViaSpng(png1, &decoded, &dw, &dh) || dw != tc.w ||
          dh != tc.h || decoded != frame) {
        ++failures;
        detail += std::string(" ") + tc.name + "/" + be.name + "(roundtrip)";
      }
    }
  }
  Check(failures == 0, "encoder_roundtrip",
        "{spng L1/L3, fpng, zlib}×6 帧 编码→解码==输入逐字节 + 复编确定，"
        "failures=" + std::to_string(failures) + detail);
}

void TestFusedEncoder() {
  const vtx_cef::SpngFilter filters[] = {
      vtx_cef::SpngFilter::kNone, vtx_cef::SpngFilter::kSub,
      vtx_cef::SpngFilter::kUp, vtx_cef::SpngFilter::kSubUp,
      vtx_cef::SpngFilter::kAll};
  vtx_cef::PngEncodeWorkspace workspace;
  vtx_cef::PngBuffer output;
  size_t failures = 0, cases = 0;
  for (const auto& dims : {std::pair{1, 1}, std::pair{7, 15}, std::pair{17, 16},
                         std::pair{33, 17}, std::pair{61, 31}, std::pair{65, 32},
                         std::pair{131, 33}, std::pair{257, 35}}) {
    const auto [w, h] = dims;
    const size_t row_bytes = static_cast<size_t>(w) * 4;
    // Deliberately use dirty RGB when alpha=0 and C>A: the fused path must
    // retain the scalar saturating/transparent semantics for every byte.
    auto compact = MakeFrame(Content::kMixedAlpha, w, h);
    for (size_t i = 0; i < compact.size(); i += 4) {
      compact[i + 3] = static_cast<uint8_t>((i / 4) * 37);
    }
    std::vector<uint8_t> reference;
    ScalarRefFrame(compact, &reference);
    for (size_t padding : {size_t{0}, size_t{13}}) {
      const size_t stride = row_bytes + padding;
      // A misaligned source and unused padding must never leak into pixels.
      std::vector<uint8_t> source(stride * h + 1, 0xE7);
      for (int row = 0; row < h; ++row) {
        std::memcpy(source.data() + 1 + row * stride,
                    compact.data() + row * row_bytes, row_bytes);
      }
      for (auto encoder : {vtx_cef::PngEncoder::kSpng,
                           vtx_cef::PngEncoder::kFpng, vtx_cef::PngEncoder::kZlib}) {
        for (int level = 1; level <= 3; ++level) {
          for (auto filter : filters) {
            vtx_cef::PngEncodeOptions opt;
            opt.encoder = encoder;
            opt.level = level;
            opt.spng_filter = filter;
            std::vector<uint8_t> expected;
            uint64_t alpha_us = 0;
            bool ok = vtx_cef::EncodePngRgba(reference.data(), w, h, opt, &expected) &&
                vtx_cef::EncodePngBgraPremultiplied(source.data() + 1, w, h, stride,
                                                   opt, &workspace, &output, &alpha_us);
            ok = ok && output.size() == expected.size() &&
                 std::memcmp(output.data(), expected.data(), expected.size()) == 0;
            if (encoder == vtx_cef::PngEncoder::kSpng) {
              ok = ok && workspace.rgba.size() == row_bytes * std::min(h, 16);
            }
            // Move assignment releases the previous allocation; a moved-from
            // object must remain safely reusable across all codec backends.
            vtx_cef::PngBuffer moved = std::move(output);
            output = std::move(moved);
            std::vector<uint8_t> png(output.data(), output.data() + output.size());
            std::vector<uint8_t> decoded;
            uint32_t dw = 0, dh = 0;
            ok = ok && DecodeRgbaViaSpng(png, &decoded, &dw, &dh) &&
                 dw == static_cast<uint32_t>(w) && dh == static_cast<uint32_t>(h) &&
                 decoded == reference;
            if (!ok) ++failures;
            ++cases;
          }
        }
      }
    }
  }
  Check(failures == 0, "fused_encoder",
        std::to_string(cases) + " stride/tile/backend/level/filter combinations: "
        "PNG bytes == old path; decoded pixels == scalar; failures=" +
        std::to_string(failures));

  // An aborted progressive source must never replace an already valid output.
  const std::vector<uint8_t> before(output.data(), output.data() + output.size());
  vtx_cef::PngEncodeOptions opt;
  int calls = 0;
  const auto fail_rows = [](void* user, size_t first, size_t count,
                            uint8_t* rgba) -> bool {
    ++*static_cast<int*>(user);
    if (first != 0) return false;
    std::memset(rgba, 0xFF, count * 4u);
    return true;
  };
  bool ok = !vtx_cef::EncodePngRgbaRows(1, 17, opt, fail_rows, &calls,
                                        &workspace, &output) && calls == 2;
  const uint8_t pixel[4] = {1, 2, 3, 255};
  ok = ok && !vtx_cef::EncodePngBgraPremultiplied(pixel, 1, 1, 3, opt,
                                                 &workspace, &output) &&
       !vtx_cef::EncodePngBgraPremultiplied(pixel, 1, 2, SIZE_MAX, opt,
                                           &workspace, &output) &&
       !vtx_cef::EncodePngBgraPremultiplied(nullptr, 1, 1, 4, opt,
                                           &workspace, &output) &&
       !vtx_cef::EncodePngBgraPremultiplied(pixel, 0, 1, 4, opt,
                                           &workspace, &output) &&
       output.size() == before.size() &&
       std::memcmp(output.data(), before.data(), before.size()) == 0;
  Check(ok, "fused_encoder_errors", "invalid sizes/stride/source and mid-image failure preserve output");
}

void TestFusedFilterEquivalence() {
  size_t failures = 0;
  // SUB differences span 0..255 by row; UP differences span 0..255 by
  // column. Include short rows, SIMD tails, tile transitions and equal scores.
  for (int width : {1, 2, 7, 8, 9, 15, 16, 17, 257, 259}) {
    constexpr int height = 257;
    const size_t row_bytes = static_cast<size_t>(width) * 4u;
    std::vector<uint8_t> frame(row_bytes * height);
    for (int y = 0; y < height; ++y) {
      for (size_t x = 0; x < row_bytes; ++x) {
        frame[y * row_bytes + x] = static_cast<uint8_t>((x / 4) * y + (x % 4) * 128);
      }
    }
    struct Rows { const uint8_t* data; size_t row_bytes; } rows{frame.data(), row_bytes};
    const auto copy = [](void* user, size_t first, size_t count, uint8_t* out) {
      const auto& source = *static_cast<Rows*>(user);
      std::memcpy(out, source.data + first * source.row_bytes, count * source.row_bytes);
      return true;
    };
    for (int level = 1; level <= 3; ++level) {
      vtx_cef::PngEncodeOptions opt;
      opt.level = level;
      vtx_cef::PngEncodeWorkspace work;
      vtx_cef::PngBuffer png;
      std::vector<uint8_t> expected;
      if (!vtx_cef::EncodePngRgba(frame.data(), width, height, opt, &expected) ||
          !vtx_cef::EncodePngRgbaRows(width, height, opt, copy, &rows, &work, &png) ||
          png.size() != expected.size() ||
          std::memcmp(png.data(), expected.data(), expected.size()) != 0) ++failures;
    }
  }
  Check(failures == 0, "fused_filter_exact", "all byte deltas, score ties, SIMD tails and tile borders match libspng bytes");
}

void TestSimdAlignmentAndTails() {
  size_t failures = 0;
  uint32_t state = 0x12345678;
  for (size_t offset = 0; offset < 32; ++offset) {
    for (size_t count = 1; count <= 65; ++count) {
      for (int kind = 0; kind < 4; ++kind) {
        std::vector<uint8_t> input(count * 4u + 64, 0x5A);
        std::vector<uint8_t> output(count * 4u + 64, 0xA5);
        for (size_t p = 0; p < count; ++p) {
          auto* pixel = input.data() + offset + p * 4;
          for (int c = 0; c < 3; ++c) pixel[c] = static_cast<uint8_t>(LcgNext(&state) >> 24);
          pixel[3] = kind == 0 ? 255 : kind == 1 ? 0
                       : kind == 2 ? (p % 3 == 0 ? 255 : 0)
                                   : static_cast<uint8_t>(LcgNext(&state) >> 24);
        }
        vtx_cef::unpremultiply_bgra_to_rgba(input.data() + offset,
                                            output.data() + offset, count);
        bool ok = true;
        for (size_t i = 0; i < output.size(); ++i) {
          if (i < offset || i >= offset + count * 4u) ok = ok && output[i] == 0xA5;
        }
        for (size_t p = 0; p < count; ++p) {
          const auto* src = input.data() + offset + p * 4;
          const auto* dst = output.data() + offset + p * 4;
          ok = ok && dst[0] == vtx_cef::UnpremultiplyScalar(src[2], src[3]) &&
               dst[1] == vtx_cef::UnpremultiplyScalar(src[1], src[3]) &&
               dst[2] == vtx_cef::UnpremultiplyScalar(src[0], src[3]) && dst[3] == src[3];
        }
        if (!ok) ++failures;
      }
    }
  }
  Check(failures == 0, "simd_alignment_tails", "32 alignments x65 lengths x4 alpha classes; poisoned output guards unchanged");
}

void TestFusedConcurrent() {
  constexpr int width = 137, height = 61;
  const auto bgra = MakeFrame(Content::kMixedAlpha, width, height);
  std::vector<uint8_t> rgba;
  ScalarRefFrame(bgra, &rgba);
  const vtx_cef::PngEncodeOptions options;
  std::vector<uint8_t> reference;
  bool ok = vtx_cef::EncodePngRgba(rgba.data(), width, height, options, &reference);
  std::atomic<int> failures{0};
  std::vector<std::thread> threads;
  for (int t = 0; t < 4; ++t) {
    threads.emplace_back([&] {
      vtx_cef::PngEncodeWorkspace workspace;
      vtx_cef::PngBuffer output;
      for (int frame = 0; frame < 50; ++frame) {
        if (!vtx_cef::EncodePngBgraPremultiplied(bgra.data(), width, height, width * 4u,
                                                options, &workspace, &output) ||
            output.size() != reference.size() ||
            std::memcmp(output.data(), reference.data(), reference.size()) != 0) {
          ++failures;
        }
      }
    });
  }
  for (auto& thread : threads) thread.join();
  Check(ok && failures.load() == 0, "fused_concurrent", "4 encoder lanes x50 frames; separate reused workspaces and exact PNG bytes");
}

void TestEncoderPairEquivalence() {
  const uint32_t w = 640, h = 480;
  const std::vector<uint8_t> frame = MakeFrame(Content::kGradient, w, h);
  std::vector<uint8_t> spng_png, fpng_png;
  vtx_cef::PngEncodeOptions so;
  so.encoder = vtx_cef::PngEncoder::kSpng;
  so.level = 1;
  vtx_cef::PngEncodeOptions fo;
  fo.encoder = vtx_cef::PngEncoder::kFpng;
  const bool ok1 = vtx_cef::EncodePngRgba(frame.data(), static_cast<int>(w),
                                          static_cast<int>(h), so, &spng_png);
  const bool ok2 = vtx_cef::EncodePngRgba(frame.data(), static_cast<int>(w),
                                          static_cast<int>(h), fo, &fpng_png);
  std::vector<uint8_t> d1, d2;
  uint32_t w1 = 0, h1 = 0, w2 = 0, h2 = 0;
  const bool dec1 = DecodeRgbaViaSpng(spng_png, &d1, &w1, &h1);
  const bool dec2 = DecodeRgbaViaSpng(fpng_png, &d2, &w2, &h2);
  Check(ok1 && ok2 && dec1 && dec2 && d1 == d2 && d1 == frame,
        "encoder_pair",
        "spng(L1) 与 fpng 文件字节 " +
            std::string(spng_png == fpng_png ? "相同" : "不同") + "（允许），"
            "双路解码像素逐字节等价且==输入（" +
            std::to_string(spng_png.size()) + "B vs " +
            std::to_string(fpng_png.size()) + "B）");
}

void TestAtomicWrite() {
  const std::filesystem::path dir = std::filesystem::temp_directory_path() /
      ("pixel_ops_selftest_" + std::to_string(::getpid()));
  std::filesystem::create_directories(dir);
  size_t failures = 0;
  // 成功路径：字节精确 + 零 *.tmp.* 残留
  std::string payload(7777, '\0');
  for (size_t i = 0; i < payload.size(); ++i) {
    payload[i] = static_cast<char>(i * 31 & 255);
  }
  const std::filesystem::path target = dir / "f0001.png";
  if (!vtx_cef::AtomicWriteBytes(target.string(),
                                 reinterpret_cast<const uint8_t*>(payload.data()),
                                 payload.size())) {
    ++failures;
  } else {
    std::FILE* fh = std::fopen(target.string().c_str(), "rb");
    if (fh == nullptr) {
      ++failures;
    } else {
      std::string got(payload.size(), 0);
      const size_t n = std::fread(got.data(), 1, got.size(), fh);
      std::fclose(fh);
      if (n != payload.size() || got != payload) ++failures;
    }
  }
  // 覆写路径：同名目标重写 → 新字节完整替换（rename 原子性面）
  const std::string payload2(999, 7);
  if (!vtx_cef::AtomicWriteBytes(target.string(),
                                 reinterpret_cast<const uint8_t*>(payload2.data()),
                                 payload2.size())) {
    ++failures;
  }
  // 失败路径：不存在目录 → false 不崩溃
  if (vtx_cef::AtomicWriteBytes(
          "/nonexistent_dir_9f3a/f0002.png",
          reinterpret_cast<const uint8_t*>(payload.data()), payload.size())) {
    ++failures;
  }
  // 并发：4 线程 × 50 文件
  {
    std::atomic<size_t> bad{0};
    std::vector<std::thread> threads;
    for (int t = 0; t < 4; ++t) {
      threads.emplace_back([&, t] {
        for (int i = 0; i < 50; ++i) {
          const std::string data(4096, static_cast<char>('A' + t));
          const auto p = dir / ("c_" + std::to_string(t) + "_" +
                                std::to_string(i) + ".png");
          if (!vtx_cef::AtomicWriteBytes(
                  p.string(),
                  reinterpret_cast<const uint8_t*>(data.data()), data.size())) {
            ++bad;
          }
        }
      });
    }
    for (auto& th : threads) th.join();
    failures += bad.load();
  }
  // 残留检查：目录内不允许任何 *.tmp.*（成功/失败路径均应清理）
  size_t residue = 0;
  for (const auto& entry : std::filesystem::directory_iterator(dir)) {
    const std::string name = entry.path().filename().string();
    if (name.find(".tmp.") != std::string::npos) ++residue;
  }
  std::error_code ec;
  std::filesystem::remove_all(dir, ec);
  Check(failures == 0 && residue == 0, "atomic_write",
        "字节精确/覆写/失败路径/4×50 并发写 全过，*.tmp.* 残留=" +
            std::to_string(residue) + " failures=" + std::to_string(failures));
}

// ---------------------------------------------------------------- 基准
double MsOf(uint64_t ns) { return static_cast<double>(ns) / 1e6; }

void AppendBenchJson(const std::string& line) {
  g_bench_json.push_back(line);
  std::printf("BENCH_JSON:%s\n", line.c_str());
}

void BenchUnpremultiply() {
  const uint32_t W = 1920, H = 1080;
  const std::vector<uint8_t> rgba_mixed = MakeFrame(Content::kMixedAlpha, W, H);
  const std::vector<uint8_t> rgba_opaque = MakeFrame(Content::kOpaqueFlat, W, H);
  struct Row {
    const char* name;
    const std::vector<uint8_t>& frame;
  };
  const Row rows[] = {{"opaque", rgba_opaque}, {"mixed_alpha", rgba_mixed}};
  std::vector<uint8_t> out(rgba_mixed.size());
  for (const Row& row : rows) {
    // 预乘 BGRA 输入（热身 + 驱逐冷启动效应后取最优/中位口径：重复 9 次取中位）
    std::vector<uint8_t> bgra(row.frame.size());
    for (size_t i = 0; i + 3 < row.frame.size(); i += 4) {
      const uint32_t a = row.frame[i + 3];
      bgra[i] = static_cast<uint8_t>(row.frame[i + 2] * a / 255);
      bgra[i + 1] = static_cast<uint8_t>(row.frame[i + 1] * a / 255);
      bgra[i + 2] = static_cast<uint8_t>(row.frame[i] * a / 255);
      bgra[i + 3] = static_cast<uint8_t>(a);
    }
    std::vector<double> simd_ms, ref_ms;
    for (int rep = 0; rep < 9; ++rep) {
      uint64_t t0 = NowNs();
      vtx_cef::unpremultiply_bgra_to_rgba(bgra.data(), out.data(),
                                          bgra.size() / 4);
      simd_ms.push_back(MsOf(NowNs() - t0));
      std::vector<uint8_t> ref;
      t0 = NowNs();
      ScalarRefFrame(bgra, &ref);
      ref_ms.push_back(MsOf(NowNs() - t0));
    }
    std::sort(simd_ms.begin(), simd_ms.end());
    std::sort(ref_ms.begin(), ref_ms.end());
    const double s = simd_ms[4], r = ref_ms[4];
    std::printf("[bench] unpremultiply %-11s simd=%.3fms (%.1f GB/s) "
                "scalar_ref=%.3fms speedup=%.1fx backend=%s\n",
                row.name, s, 8.2944 / s, r, r / s,
                vtx_cef::UnpremultiplyBackendName());
    AppendBenchJson("{\"kind\":\"unpremultiply\",\"content\":\"" +
                    std::string(row.name) + "\",\"simd_ms\":" + fmt(s) +
                    ",\"scalar_ms\":" + fmt(r) + ",\"speedup\":" + fmt(r / s) +
                    ",\"gbps\":" + fmt(8.2944 / s) + ",\"backend\":\"" +
                    vtx_cef::UnpremultiplyBackendName() + "\"}");
  }
}

void BenchEncoders() {
  const uint32_t W = 1920, H = 1080;
  const Content contents[] = {Content::kOpaqueFlat, Content::kCanaryLike,
                              Content::kGradient, Content::kNoise};
  const struct {
    const char* name;
    vtx_cef::PngEncoder enc;
    int level;
  } backends[] = {
      {"spng_L1", vtx_cef::PngEncoder::kSpng, 1},
      {"spng_L3", vtx_cef::PngEncoder::kSpng, 3},
      {"fpng", vtx_cef::PngEncoder::kFpng, 0},
      {"zlib", vtx_cef::PngEncoder::kZlib, 0},
  };
  for (Content ct : contents) {
    const std::vector<uint8_t> frame = MakeFrame(ct, W, H);
    for (const auto& be : backends) {
      vtx_cef::PngEncodeOptions opt;
      opt.encoder = be.enc;
      opt.level = be.level > 0 ? be.level : 1;
      std::vector<uint8_t> png;
      // 单编（拿到字节数 + 确定性基准）→ 计时 5 次取中位
      if (!vtx_cef::EncodePngRgba(frame.data(), static_cast<int>(W),
                                  static_cast<int>(H), opt, &png)) {
        std::printf("[bench] encode %s/%s FAILED\n", ContentName(ct), be.name);
        continue;
      }
      const size_t bytes = png.size();
      std::vector<double> ms;
      for (int rep = 0; rep < 5; ++rep) {
        const uint64_t t0 = NowNs();
        std::vector<uint8_t> again;
        vtx_cef::EncodePngRgba(frame.data(), static_cast<int>(W),
                               static_cast<int>(H), opt, &again);
        ms.push_back(MsOf(NowNs() - t0));
      }
      std::sort(ms.begin(), ms.end());
      std::printf("[bench] encode %-11s %-8s %.3fms %zuB (%.2f MB/s)\n",
                  ContentName(ct), be.name, ms[2], bytes,
                  8.2944 / ms[2]);
      AppendBenchJson("{\"kind\":\"encode\",\"content\":\"" +
                      std::string(ContentName(ct)) + "\",\"backend\":\"" +
                      be.name + "\",\"ms\":" + fmt(ms[2]) + ",\"bytes\":" +
                      std::to_string(bytes) + "}");
    }
  }
}

// Mechanism comparison only: both paths use current SIMD and the same pinned
// spng/options. This is not CEF capture, the 900-frame baseline, or full-chain
// evidence. Keep the staged vector API as an independently exercised reference.
void BenchFusedPipeline() {
  constexpr int width = 1920, height = 1080;
  const vtx_cef::PngEncodeOptions opt;
  for (int kind = 0; kind < 4; ++kind) {
    const char* names[] = {"opaque", "transparent", "binary_alpha", "mixed_alpha"};
    auto bgra = MakeFrame(kind == 3 ? Content::kMixedAlpha : Content::kCanaryLike,
                          width, height);
    if (kind == 1 || kind == 2) {
      for (size_t pixel = 0; pixel < bgra.size() / 4; ++pixel) {
        bgra[pixel * 4 + 3] = kind == 1 || pixel % 7 != 0 ? 0 : 255;
      }
    }
    std::vector<uint8_t> rgba(bgra.size()), reference;
    vtx_cef::PngEncodeWorkspace workspace;
    vtx_cef::PngBuffer fused;
    std::vector<double> staged_ms, fused_ms;
    bool ok = true;
    for (int sample = 0; sample < 9; ++sample) {
      const auto staged = [&] {
        const uint64_t begin = NowNs();
        vtx_cef::unpremultiply_bgra_to_rgba(bgra.data(), rgba.data(), bgra.size() / 4);
        ok = vtx_cef::EncodePngRgba(rgba.data(), width, height, opt, &reference) && ok;
        staged_ms.push_back(MsOf(NowNs() - begin));
      };
      const auto tiled = [&] {
        const uint64_t begin = NowNs();
        ok = vtx_cef::EncodePngBgraPremultiplied(bgra.data(), width, height, width * 4u,
                                                opt, &workspace, &fused) && ok;
        fused_ms.push_back(MsOf(NowNs() - begin));
      };
      if (sample % 2 == 0) { staged(); tiled(); }
      else { tiled(); staged(); }
      ok = ok && fused.size() == reference.size() &&
           std::memcmp(fused.data(), reference.data(), reference.size()) == 0;
    }
    Check(ok, "bench_fused_bytes", std::string(names[kind]) + ": staged/fused PNG bytes equal");
    std::sort(staged_ms.begin(), staged_ms.end());
    std::sort(fused_ms.begin(), fused_ms.end());
    const size_t scratch = workspace.rgba.capacity() + workspace.previous_row.capacity();
    AppendBenchJson("{\"kind\":\"fused_pipeline\",\"content\":\"" +
                    std::string(names[kind]) + "\",\"encoder\":\"spng_L1_sub_up\"," +
                    "\"staged_ms\":" + fmt(staged_ms[4]) + ",\"fused_ms\":" + fmt(fused_ms[4]) +
                    ",\"scratch_bytes\":" + std::to_string(scratch) +
                    ",\"png_bytes_equal\":" + (ok ? "true" : "false") + "}");
  }
}

// DoD：unpremultiply + spng(L1) 单帧单核合计；以及 4 线程并发编码吞吐。
void BenchPipelineAndThreads() {
  const uint32_t W = 1920, H = 1080;
  const Content contents[] = {Content::kOpaqueFlat, Content::kCanaryLike,
                              Content::kGradient, Content::kNoise};
  vtx_cef::PngEncodeOptions spng1;
  spng1.encoder = vtx_cef::PngEncoder::kSpng;
  spng1.level = 1;
  vtx_cef::PngEncodeOptions spng1_none = spng1;
  spng1_none.spng_filter = vtx_cef::SpngFilter::kNone;
  vtx_cef::PngEncodeOptions fpng0;
  fpng0.encoder = vtx_cef::PngEncoder::kFpng;
  for (Content ct : contents) {
    std::vector<uint8_t> rgba = MakeFrame(ct, W, H);
    // 预乘 BGRA → SIMD → spng L1（=模式 1 编码线程的完整负载）
    std::vector<uint8_t> bgra(rgba.size());
    for (size_t i = 0; i + 3 < rgba.size(); i += 4) {
      const uint32_t a = rgba[i + 3];
      bgra[i] = static_cast<uint8_t>(rgba[i + 2] * a / 255);
      bgra[i + 1] = static_cast<uint8_t>(rgba[i + 1] * a / 255);
      bgra[i + 2] = static_cast<uint8_t>(rgba[i] * a / 255);
      bgra[i + 3] = static_cast<uint8_t>(a);
    }
    std::vector<uint8_t> rgba_out(rgba.size());
    std::vector<double> ms;
    for (int rep = 0; rep < 5; ++rep) {
      std::vector<uint8_t> png;
      const uint64_t t0 = NowNs();
      vtx_cef::unpremultiply_bgra_to_rgba(bgra.data(), rgba_out.data(),
                                          bgra.size() / 4);
      vtx_cef::EncodePngRgba(rgba_out.data(), static_cast<int>(W),
                             static_cast<int>(H), spng1, &png);
      ms.push_back(MsOf(NowNs() - t0));
    }
    std::sort(ms.begin(), ms.end());
    std::printf("[bench] pipeline %-11s unpremul+spng_L1(sub_up)=%.3fms "
                "(#110 zlib 基线 14.27ms, K1 预算 18ms)\n",
                ContentName(ct), ms[2]);
    AppendBenchJson("{\"kind\":\"pipeline\",\"filter\":\"sub_up\",\"content\":\"" +
                    std::string(ContentName(ct)) +
                    "\",\"unpremul_plus_spng_l1_ms\":" + fmt(ms[2]) + "}");
    // filter=none 变体（DoD ≤8ms 评估面：同内容对照）
    std::vector<double> ms2;
    for (int rep = 0; rep < 5; ++rep) {
      std::vector<uint8_t> png;
      const uint64_t t0 = NowNs();
      vtx_cef::unpremultiply_bgra_to_rgba(bgra.data(), rgba_out.data(),
                                          bgra.size() / 4);
      vtx_cef::EncodePngRgba(rgba_out.data(), static_cast<int>(W),
                             static_cast<int>(H), spng1_none, &png);
      ms2.push_back(MsOf(NowNs() - t0));
    }
    std::sort(ms2.begin(), ms2.end());
    std::printf("[bench] pipeline %-11s unpremul+spng_L1(none) =%.3fms "
                "(#110 zlib 基线 14.27ms, K1 预算 18ms)\n",
                ContentName(ct), ms2[2]);
    AppendBenchJson("{\"kind\":\"pipeline\",\"filter\":\"none\",\"content\":\"" +
                    std::string(ContentName(ct)) +
                    "\",\"unpremul_plus_spng_l1_ms\":" + fmt(ms2[2]) + "}");
    // fpng 变体（DoD：unpremultiply+fpng 单帧耗时）
    std::vector<double> ms3;
    for (int rep = 0; rep < 5; ++rep) {
      std::vector<uint8_t> png;
      const uint64_t t0 = NowNs();
      vtx_cef::unpremultiply_bgra_to_rgba(bgra.data(), rgba_out.data(),
                                          bgra.size() / 4);
      vtx_cef::EncodePngRgba(rgba_out.data(), static_cast<int>(W),
                             static_cast<int>(H), fpng0, &png);
      ms3.push_back(MsOf(NowNs() - t0));
    }
    std::sort(ms3.begin(), ms3.end());
    std::printf("[bench] pipeline %-11s unpremul+fpng       =%.3fms\n",
                ContentName(ct), ms3[2]);
    AppendBenchJson("{\"kind\":\"pipeline\",\"filter\":\"fpng\",\"content\":\"" +
                    std::string(ContentName(ct)) +
                    "\",\"unpremul_plus_fpng_ms\":" + fmt(ms3[2]) + "}");
  }
  // 4 线程并发：每线程独立缓冲各编 24 帧（spng L1 sub_up 与 none 双口径），
  // 栅栏起步；并发产物与单线程参考逐字节对照（竞态/非确定性显形）。
  const vtx_cef::PngEncodeOptions kConcurrentCfgs[] = {spng1, spng1_none,
                                                        fpng0};
  const char* kCfgNames[] = {"sub_up", "none", "fpng"};
  for (Content ct : contents) {
   for (size_t cfg = 0; cfg < 3; ++cfg) {
    const vtx_cef::PngEncodeOptions enc_opt = kConcurrentCfgs[cfg];
    const std::vector<uint8_t> frame = MakeFrame(ct, W, H);
    std::vector<uint8_t> reference;
    vtx_cef::EncodePngRgba(frame.data(), static_cast<int>(W),
                           static_cast<int>(H), enc_opt, &reference);
    constexpr int kThreads = 4;
    constexpr int kPerThread = 24;
    std::atomic<int> ready{0};
    std::atomic<bool> go{false};
    std::atomic<size_t> byte_mismatch{0};
    std::vector<std::thread> threads;
    std::vector<std::vector<uint8_t>> outs(kThreads);
    for (int t = 0; t < kThreads; ++t) {
      threads.emplace_back([&, t] {
        std::vector<uint8_t> png;
        ++ready;
        while (!go.load()) std::this_thread::yield();
        for (int i = 0; i < kPerThread; ++i) {
          if (!vtx_cef::EncodePngRgba(frame.data(), static_cast<int>(W),
                                      static_cast<int>(H), enc_opt, &png)) {
            ++byte_mismatch;
            return;
          }
        }
        outs[t] = std::move(png);
      });
    }
    while (ready.load() < kThreads) std::this_thread::yield();
    const uint64_t t0 = NowNs();
    go.store(true);
    for (auto& th : threads) th.join();
    const double wall_ms = MsOf(NowNs() - t0);
    for (int t = 0; t < kThreads; ++t) {
      if (outs[t] != reference) ++byte_mismatch;  // 并发确定性/竞态显形
    }
    const double fps = kThreads * kPerThread / (wall_ms / 1000.0);
    std::printf("[bench] concurrent %-11s enc(%s) 4线程x%d帧 wall=%.1fms "
                "吞吐=%.0ffps（DoD≥300）mismatch=%zu\n",
                ContentName(ct), kCfgNames[cfg], kPerThread, wall_ms, fps,
                byte_mismatch.load());
    AppendBenchJson("{\"kind\":\"concurrent\",\"filter\":\"" +
                    std::string(kCfgNames[cfg]) + "\",\"content\":\"" +
                    std::string(ContentName(ct)) + "\",\"threads\":4," +
                    "\"frames\":" + std::to_string(kThreads * kPerThread) +
                    ",\"wall_ms\":" + fmt(wall_ms) + ",\"fps\":" + fmt(fps) +
                    ",\"byte_mismatch\":" +
                    std::to_string(byte_mismatch.load()) + "}");
   }
  }
}

}  // namespace

int main(int argc, char* argv[]) {
  bool bench = false;
  bool bench_json = false;
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg == "--bench") bench = true;
    else if (arg == "--bench-json") { bench = true; bench_json = true; }
    else {
      std::fprintf(stderr, "pixel_ops_selftest: 未知参数 %s\n", arg.c_str());
      return 2;
    }
  }
  (void)bench_json;
  TestColdConcurrentInitialization();
  vtx_cef::PngEncoderGlobalInit();  // fpng 硬件加速（工单：启动时）
  std::printf("== pixel_ops_selftest（#111 SIMD 逆预乘 + 双编码器 + 原子落盘）"
              "==\n");
  std::printf("unpremultiply backend: %s\n", vtx_cef::UnpremultiplyBackendName());
  TestLutFullDomain();
  TestSimdPixelDomain();
  TestSimdFrames();
  TestSimdAlignmentAndTails();
  TestEncoderRoundtrip();
  TestEncoderPairEquivalence();
  TestFusedEncoder();
  TestFusedFilterEquivalence();
  TestFusedConcurrent();
  TestAtomicWrite();
  if (bench) {
    std::printf("== bench（1080p；中位数；本机实测）==\n");
    BenchUnpremultiply();
    BenchEncoders();
    BenchPipelineAndThreads();
    BenchFusedPipeline();
  }
  std::printf("== %s（fail=%d）==\n", g_fail == 0 ? "ALL PASS" : "FAIL", g_fail);
  return g_fail == 0 ? 0 : 1;
}
