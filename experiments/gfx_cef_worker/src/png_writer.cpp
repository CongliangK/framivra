// png_writer.cpp — 双后端 PNG 编码实现 + 原子落盘（issue #109；#111 CEF-02b）。
//
// 编码后端实现要点：
//   spng：spng_ctx_new(ENCODER) → SPNG_ENCODE_TO_BUFFER → spng_set_ihdr
//         (8bit/RGBA/非隔行) → SPNG_IMG_COMPRESSION_LEVEL=level →
//         SPNG_FILTER_CHOICE=SUB|UP → spng_encode_image(SPNG_FMT_PNG,
//         SPNG_ENCODE_FINALIZE) → spng_get_png_buffer（成功后缓冲区所有权
//         转给调用者；buffer/ctx 分别由 RAII 释放，含异常路径）。每次独立 ctx。
//   fpng：进程级一次 fpng_init()（std::call_once；main/自检入口亦显式调）→
//         fpng_encode_image_to_memory(…, num_chans=4, flags=0)。
//   zlib：#109 原样（filter-0 扫描线 + compress2(Z_DEFAULT_COMPRESSION)），
//         字节口径与 #110 产物一致（基线对照面）。
#include "png_writer.h"

#if defined(__APPLE__)
#include <pthread.h>
#else
#include <sys/syscall.h>
#endif
#include <unistd.h>

#include <zlib.h>

#if defined(__x86_64__) || defined(__i386__)
#include <immintrin.h>
#elif defined(__aarch64__)
#include <arm_neon.h>
#endif

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <memory>
#include <mutex>

#include "fpng.h"
#include "spng.h"

namespace vtx_cef {

void PngBuffer::FreeBytes::operator()(uint8_t* data) const noexcept {
  std::free(data);
}

struct PngBufferAccess {
  static void Adopt(PngBuffer* png, void* bytes, size_t len) noexcept {
    png->clear();
    png->allocated_.reset(static_cast<uint8_t*>(bytes));
    png->allocated_size_ = len;
  }
  static std::vector<uint8_t>* Vector(PngBuffer* png) { return &png->bytes_; }
};

namespace {

bool ImageSize(int width, int height, size_t* row_bytes, size_t* frame_bytes) {
  if (width <= 0 || height <= 0 ||
      static_cast<size_t>(width) > std::numeric_limits<size_t>::max() / 4u) {
    return false;
  }
  const size_t row = static_cast<size_t>(width) * 4u;
  if (static_cast<size_t>(height) > std::numeric_limits<size_t>::max() / row) {
    return false;
  }
  *row_bytes = row;
  *frame_bytes = row * static_cast<size_t>(height);
  return true;
}


// ---------------------------------------------------------------- zlib 遗留
//（#109 逻辑原样：filter-0 扫描线 + 单 IDAT + Z_DEFAULT_COMPRESSION）
void PutU32(std::vector<uint8_t>* out, uint32_t v) {
  out->push_back(static_cast<uint8_t>(v >> 24));
  out->push_back(static_cast<uint8_t>(v >> 16));
  out->push_back(static_cast<uint8_t>(v >> 8));
  out->push_back(static_cast<uint8_t>(v));
}

void PutChunk(std::vector<uint8_t>* out, const char tag[4],
              const uint8_t* data, size_t len) {
  PutU32(out, static_cast<uint32_t>(len));
  const size_t body = out->size();
  out->insert(out->end(), tag, tag + 4);
  if (len > 0) {
    out->insert(out->end(), data, data + len);
  }
  const uint32_t crc =
      static_cast<uint32_t>(crc32(crc32(0L, Z_NULL, 0), out->data() + body,
                                  4 + len));
  PutU32(out, crc);
}

bool EncodeZlibLegacy(const uint8_t* rgba, int width, int height,
                      std::vector<uint8_t>* png) {
  const size_t row_bytes = 1u + static_cast<size_t>(width) * 4u;
  std::vector<uint8_t> raw(row_bytes * static_cast<size_t>(height));
  for (int y = 0; y < height; ++y) {
    uint8_t* dst = raw.data() + row_bytes * static_cast<size_t>(y);
    dst[0] = 0;  // filter = None
    std::memcpy(dst + 1, rgba + static_cast<size_t>(y) * width * 4u,
                static_cast<size_t>(width) * 4u);
  }
  uLongf compressed = compressBound(static_cast<uLong>(raw.size()));
  std::vector<uint8_t> idat(compressed);
  if (compress2(idat.data(), &compressed, raw.data(),
                static_cast<uLong>(raw.size()),
                Z_DEFAULT_COMPRESSION) != Z_OK) {
    std::fprintf(stderr, "png_writer: zlib compress2 失败\n");
    return false;
  }
  idat.resize(compressed);

  png->clear();
  png->reserve(8 + 25 + 12 + idat.size() + 12);
  static const uint8_t kSignature[8] = {0x89, 'P', 'N', 'G', 0x0D, 0x0A,
                                        0x1A, 0x0A};
  png->insert(png->end(), kSignature, kSignature + 8);
  uint8_t ihdr[13];
  ihdr[0] = static_cast<uint8_t>(static_cast<uint32_t>(width) >> 24);
  ihdr[1] = static_cast<uint8_t>(static_cast<uint32_t>(width) >> 16);
  ihdr[2] = static_cast<uint8_t>(static_cast<uint32_t>(width) >> 8);
  ihdr[3] = static_cast<uint8_t>(width);
  ihdr[4] = static_cast<uint8_t>(static_cast<uint32_t>(height) >> 24);
  ihdr[5] = static_cast<uint8_t>(static_cast<uint32_t>(height) >> 16);
  ihdr[6] = static_cast<uint8_t>(static_cast<uint32_t>(height) >> 8);
  ihdr[7] = static_cast<uint8_t>(height);
  ihdr[8] = 8;   // bit depth
  ihdr[9] = 6;   // color type = RGBA
  ihdr[10] = 0;  // compression
  ihdr[11] = 0;  // filter
  ihdr[12] = 0;  // interlace
  PutChunk(png, "IHDR", ihdr, sizeof(ihdr));
  PutChunk(png, "IDAT", idat.data(), idat.size());
  PutChunk(png, "IEND", nullptr, 0);
  return true;
}

// Exact libspng SUB/UP score: min(delta, 256-delta), with subtraction
// modulo 256. Ties choose SUB (its PNG filter number precedes UP). SIMD sums
// byte magnitudes rather than using a different heuristic or sampled rows.
struct FilterScores { uint64_t sub = 0, up = 0; };

inline unsigned ByteMagnitude(uint8_t value) {
  return std::min<unsigned>(value, 256u - value);
}

[[maybe_unused]] FilterScores ScoreSubUpScalar(const uint8_t* row, const uint8_t* previous,
                               size_t size) {
  FilterScores sum;
  for (size_t i = 0; i < size; ++i) {
    const uint8_t left = i < 4 ? 0 : row[i - 4];
    sum.sub += ByteMagnitude(static_cast<uint8_t>(row[i] - left));
    sum.up += ByteMagnitude(static_cast<uint8_t>(row[i] - previous[i]));
  }
  return sum;
}

#if defined(__x86_64__) || defined(__i386__)
__attribute__((target("avx2")))
FilterScores ScoreSubUpAvx2(const uint8_t* row, const uint8_t* previous,
                             size_t size) {
  FilterScores sum;
  size_t i = 0;
  for (; i < std::min<size_t>(4, size); ++i) {
    sum.sub += ByteMagnitude(row[i]);
    sum.up += ByteMagnitude(static_cast<uint8_t>(row[i] - previous[i]));
  }
  __m256i sub = _mm256_setzero_si256(), up = _mm256_setzero_si256();
  const __m256i zero = _mm256_setzero_si256();
  for (; size - i >= 32; i += 32) {
    const __m256i current = _mm256_loadu_si256(
        reinterpret_cast<const __m256i*>(row + i));
    const __m256i left = _mm256_loadu_si256(
        reinterpret_cast<const __m256i*>(row + i - 4));
    const __m256i above = _mm256_loadu_si256(
        reinterpret_cast<const __m256i*>(previous + i));
    sub = _mm256_add_epi64(sub, _mm256_sad_epu8(
        _mm256_abs_epi8(_mm256_sub_epi8(current, left)), zero));
    up = _mm256_add_epi64(up, _mm256_sad_epu8(
        _mm256_abs_epi8(_mm256_sub_epi8(current, above)), zero));
  }
  alignas(32) uint64_t sub_lanes[4], up_lanes[4];
  _mm256_store_si256(reinterpret_cast<__m256i*>(sub_lanes), sub);
  _mm256_store_si256(reinterpret_cast<__m256i*>(up_lanes), up);
  for (int lane = 0; lane < 4; ++lane) {
    sum.sub += sub_lanes[lane];
    sum.up += up_lanes[lane];
  }
  for (; i < size; ++i) {
    sum.sub += ByteMagnitude(static_cast<uint8_t>(row[i] - row[i - 4]));
    sum.up += ByteMagnitude(static_cast<uint8_t>(row[i] - previous[i]));
  }
  return sum;
}
#elif defined(__aarch64__)
FilterScores ScoreSubUpNeon(const uint8_t* row, const uint8_t* previous,
                             size_t size) {
  FilterScores sum;
  size_t i = 0;
  for (; i < std::min<size_t>(4, size); ++i) {
    sum.sub += ByteMagnitude(row[i]);
    sum.up += ByteMagnitude(static_cast<uint8_t>(row[i] - previous[i]));
  }
  uint32x4_t sub = vdupq_n_u32(0), up = vdupq_n_u32(0);
  for (; size - i >= 16; i += 16) {
    const uint8x16_t current = vld1q_u8(row + i);
    const uint8x16_t left = vld1q_u8(row + i - 4);
    const uint8x16_t above = vld1q_u8(previous + i);
    const uint8x16_t sub_abs = vreinterpretq_u8_s8(
        vabsq_s8(vreinterpretq_s8_u8(vsubq_u8(current, left))));
    const uint8x16_t up_abs = vreinterpretq_u8_s8(
        vabsq_s8(vreinterpretq_s8_u8(vsubq_u8(current, above))));
    sub = vpadalq_u16(sub, vpaddlq_u8(sub_abs));
    up = vpadalq_u16(up, vpaddlq_u8(up_abs));
  }
  sum.sub += vaddvq_u32(sub);
  sum.up += vaddvq_u32(up);
  for (; i < size; ++i) {
    sum.sub += ByteMagnitude(static_cast<uint8_t>(row[i] - row[i - 4]));
    sum.up += ByteMagnitude(static_cast<uint8_t>(row[i] - previous[i]));
  }
  return sum;
}
#endif

int SelectSubUpFilter(const uint8_t* row, const uint8_t* previous, size_t size) {
  // Retain upstream's overflow sentinel and tie behavior for enormous rows.
  // Normal worker viewports are far below this bound.
  if (size > static_cast<size_t>(INT32_MAX / 128)) return SPNG_FILTER_CHOICE_SUB | SPNG_FILTER_CHOICE_UP;
  using ScoreFn = FilterScores (*)(const uint8_t*, const uint8_t*, size_t);
#if defined(__x86_64__) || defined(__i386__)
  static const ScoreFn score = __builtin_cpu_supports("avx2") ? ScoreSubUpAvx2
                                                               : ScoreSubUpScalar;
#elif defined(__aarch64__)
  static const ScoreFn score = ScoreSubUpNeon;
#else
  static const ScoreFn score = ScoreSubUpScalar;
#endif
  const FilterScores sum = score(row, previous, size);
  return sum.sub <= sum.up ? SPNG_FILTER_CHOICE_SUB : SPNG_FILTER_CHOICE_UP;
}

// ---------------------------------------------------------------- libspng
bool EncodeSpngOwned(const uint8_t* rgba, int width, int height, int level,
                      SpngFilter filter, PngRowSource source, void* user,
                      PngEncodeWorkspace* workspace, PngBuffer* png) {
  std::unique_ptr<spng_ctx, decltype(&spng_ctx_free)> ctx(
      spng_ctx_new(SPNG_CTX_ENCODER), &spng_ctx_free);
  if (ctx == nullptr) {
    std::fprintf(stderr, "png_writer: spng_ctx_new 失败\n");
    return false;
  }
  if (spng_set_option(ctx.get(), SPNG_ENCODE_TO_BUFFER, 1) != 0) {
    std::fprintf(stderr, "png_writer: SPNG_ENCODE_TO_BUFFER 设置失败\n");
    return false;
  }
  spng_ihdr ihdr{};
  ihdr.width = static_cast<uint32_t>(width);
  ihdr.height = static_cast<uint32_t>(height);
  ihdr.bit_depth = 8;
  ihdr.color_type = SPNG_COLOR_TYPE_TRUECOLOR_ALPHA;
  if (spng_set_ihdr(ctx.get(), &ihdr) != 0 ||
      spng_set_option(ctx.get(), SPNG_IMG_COMPRESSION_LEVEL, level) != 0 ||
      spng_set_option(ctx.get(), SPNG_FILTER_CHOICE,
                      static_cast<int>(filter)) != 0) {
    std::fprintf(stderr, "png_writer: spng 头/选项设置失败\n");
    return false;
  }
  const size_t row_bytes = static_cast<size_t>(width) * 4u;
  const size_t frame_bytes = row_bytes * static_cast<size_t>(height);
  int ret = 0;
  if (source == nullptr) {
    ret = spng_encode_image(ctx.get(), rgba, frame_bytes, SPNG_FMT_PNG,
                             SPNG_ENCODE_FINALIZE);
  } else {
    // libspng copies each submitted row before returning. Convert a small tile,
    // encode it while it is cache-hot, then reuse the same allocation. No full
    // RGBA frame is written and read back for the default spng path.
    const size_t tile_rows = std::min<size_t>(16, static_cast<size_t>(height));
    workspace->rgba.resize(row_bytes * tile_rows);
    const bool select_sub_up = filter == SpngFilter::kSubUp;
    if (select_sub_up) workspace->previous_row.assign(row_bytes, 0);
    ret = spng_encode_image(ctx.get(), nullptr, 0, SPNG_FMT_PNG,
                             SPNG_ENCODE_PROGRESSIVE | SPNG_ENCODE_FINALIZE);
    if (ret == 0) {
      for (size_t first = 0; first < static_cast<size_t>(height); first += tile_rows) {
        const size_t count = std::min(tile_rows, static_cast<size_t>(height) - first);
        if (!source(user, first, count, workspace->rgba.data())) return false;
        for (size_t row = 0; row < count; ++row) {
          const uint8_t* current = workspace->rgba.data() + row * row_bytes;
          if (select_sub_up) {
            const uint8_t* previous = row == 0 ? workspace->previous_row.data()
                                               : current - row_bytes;
            // v0.7.3 allows FILTER_CHOICE updates between progressive rows.
            // Set exactly the same winner as get_best_filter(), but avoid its
            // two scalar score passes; spng still filters/compresses the row.
            ret = spng_set_option(ctx.get(), SPNG_FILTER_CHOICE,
                                   SelectSubUpFilter(current, previous, row_bytes));
            if (ret != 0) return false;
          }
          ret = spng_encode_row(ctx.get(), current, row_bytes);
          const bool last = first + row + 1 == static_cast<size_t>(height);
          if (ret != (last ? SPNG_EOI : 0)) {
            std::fprintf(stderr, "png_writer: spng_encode_row 失败: %s\n",
                         spng_strerror(ret));
            return false;
          }
        }
        if (select_sub_up && first + count < static_cast<size_t>(height)) {
          std::memcpy(workspace->previous_row.data(),
                       workspace->rgba.data() + (count - 1) * row_bytes, row_bytes);
        }
      }
      ret = 0;  // The final row returned EOI and finalized the PNG.
    }
  }
  if (ret != 0) {
    std::fprintf(stderr, "png_writer: spng_encode_image 失败: %s\n",
                 spng_strerror(ret));
    return false;
  }
  size_t out_len = 0;
  int err = 0;
  // Adopt before checking length: even a defensive rejection of a non-null,
  // empty result must free the transferred allocation exactly once.
  std::unique_ptr<void, decltype(&std::free)> buf(
      spng_get_png_buffer(ctx.get(), &out_len, &err), &std::free);
  if (buf == nullptr || out_len == 0 || err != 0) {
    std::fprintf(stderr, "png_writer: spng_get_png_buffer 失败: %s\n",
                 spng_strerror(err));
    return false;
  }
  PngBufferAccess::Adopt(png, buf.release(), out_len);
  return true;
}

bool EncodeSpng(const uint8_t* rgba, int width, int height, int level,
                SpngFilter filter, std::vector<uint8_t>* png) {
  PngBuffer owned;
  if (!EncodeSpngOwned(rgba, width, height, level, filter, nullptr, nullptr,
                       nullptr, &owned)) return false;
  // Legacy vector API remains byte-compatible. New production callers retain
  // the owned buffer and avoid this copy entirely.
  png->assign(owned.data(), owned.data() + owned.size());
  return true;
}

// ---------------------------------------------------------------- fpng
std::once_flag g_fpng_once;

void FpngInitOnce() { fpng::fpng_init(); }

bool EncodeFpng(const uint8_t* rgba, int width, int height,
                std::vector<uint8_t>* png) {
  std::call_once(g_fpng_once, FpngInitOnce);
  // num_chans=4（RGBA 直通）；flags=0 最快路径（无级别概念）。
  if (!fpng::fpng_encode_image_to_memory(
          rgba, static_cast<uint32_t>(width), static_cast<uint32_t>(height), 4,
          *png, 0)) {
    std::fprintf(stderr, "png_writer: fpng 编码失败\n");
    return false;
  }
  return true;
}

}  // namespace

bool ParsePngEncoderName(const std::string& name, PngEncoder* out) {
  // out==nullptr 允许（仅校验名字合法性的参数面用法）。
  if (name == "spng") {
    if (out != nullptr) *out = PngEncoder::kSpng;
    return true;
  }
  if (name == "fpng") {
    if (out != nullptr) *out = PngEncoder::kFpng;
    return true;
  }
  if (name == "zlib") {
    if (out != nullptr) *out = PngEncoder::kZlib;
    return true;
  }
  return false;
}

const char* PngEncoderName(PngEncoder e) {
  switch (e) {
    case PngEncoder::kSpng: return "spng";
    case PngEncoder::kFpng: return "fpng";
    default: return "zlib";
  }
}

bool ParseSpngFilterName(const std::string& name, SpngFilter* out) {
  if (name == "none") {
    if (out != nullptr) *out = SpngFilter::kNone;
    return true;
  }
  if (name == "sub") {
    if (out != nullptr) *out = SpngFilter::kSub;
    return true;
  }
  if (name == "up") {
    if (out != nullptr) *out = SpngFilter::kUp;
    return true;
  }
  if (name == "sub_up" || name == "sub-up" || name == "subup") {
    if (out != nullptr) *out = SpngFilter::kSubUp;
    return true;
  }
  if (name == "all") {
    if (out != nullptr) *out = SpngFilter::kAll;
    return true;
  }
  return false;
}

const char* SpngFilterName(SpngFilter f) {
  switch (f) {
    case SpngFilter::kNone: return "none";
    case SpngFilter::kSub: return "sub";
    case SpngFilter::kUp: return "up";
    case SpngFilter::kAll: return "all";
    default: return "sub_up";
  }
}

void PngEncoderGlobalInit() {
  std::call_once(g_fpng_once, FpngInitOnce);  // fpng 硬件加速（幂等）
}

bool EncodePngRgba(const uint8_t* rgba, int width, int height,
                   const PngEncodeOptions& opt, std::vector<uint8_t>* png) {
  size_t row_bytes = 0, frame_bytes = 0;
  if (rgba == nullptr || png == nullptr ||
      !ImageSize(width, height, &row_bytes, &frame_bytes)) {
    return false;
  }
  switch (opt.encoder) {
    case PngEncoder::kSpng:
      if (opt.level < 1 || opt.level > 3) {
        std::fprintf(stderr, "png_writer: spng level 须为 1..3，得到 %d\n",
                     opt.level);
        return false;
      }
      return EncodeSpng(rgba, width, height, opt.level, opt.spng_filter, png);
    case PngEncoder::kFpng:
      return EncodeFpng(rgba, width, height, png);
    default:
      return EncodeZlibLegacy(rgba, width, height, png);
  }
}

bool EncodePngRgba(const uint8_t* rgba, int width, int height,
                   const PngEncodeOptions& opt, PngBuffer* png) {
  size_t row_bytes = 0, frame_bytes = 0;
  if (rgba == nullptr || png == nullptr ||
      !ImageSize(width, height, &row_bytes, &frame_bytes)) return false;
  PngBuffer next;
  bool ok = false;
  if (opt.encoder == PngEncoder::kSpng) {
    if (opt.level < 1 || opt.level > 3) return false;
    ok = EncodeSpngOwned(rgba, width, height, opt.level, opt.spng_filter,
                         nullptr, nullptr, nullptr, &next);
  } else {
    ok = EncodePngRgba(rgba, width, height, opt, PngBufferAccess::Vector(&next));
  }
  if (!ok) return false;
  *png = std::move(next);
  return true;
}

bool EncodePngRgbaRows(int width, int height, const PngEncodeOptions& opt,
                       PngRowSource source, void* user,
                       PngEncodeWorkspace* workspace, PngBuffer* png) {
  size_t row_bytes = 0, frame_bytes = 0;
  if (source == nullptr || workspace == nullptr || png == nullptr ||
      !ImageSize(width, height, &row_bytes, &frame_bytes)) return false;
  PngBuffer next;
  if (opt.encoder == PngEncoder::kSpng) {
    if (opt.level < 1 || opt.level > 3) return false;
    if (!EncodeSpngOwned(nullptr, width, height, opt.level, opt.spng_filter,
                         source, user, workspace, &next)) return false;
  } else {
    // Alternate encoders consume a contiguous frame. Preserve their behavior,
    // reuse the allocation between frames, and never silently switch backend.
    workspace->rgba.resize(frame_bytes);
    if (!source(user, 0, static_cast<size_t>(height), workspace->rgba.data()) ||
        !EncodePngRgba(workspace->rgba.data(), width, height, opt, &next)) {
      return false;
    }
  }
  *png = std::move(next);
  return true;
}

bool EncodePngRgba(const uint8_t* rgba, int width, int height,
                   std::vector<uint8_t>* png) {
  // #110 兼容口径：zlib 遗留编码器（t_png_encode_us 计时段语义不变；
  // filter/level 字段对该后端无效）。
  PngEncodeOptions legacy;
  legacy.encoder = PngEncoder::kZlib;
  legacy.level = 1;
  legacy.spng_filter = SpngFilter::kSubUp;
  return EncodePngRgba(rgba, width, height, legacy, png);
}

bool WritePngRgba(const std::string& path, const uint8_t* rgba, int width,
                  int height, const PngEncodeOptions& opt) {
  if (!rgba || width <= 0 || height <= 0) {
    return false;
  }
  PngBuffer png;
  if (!EncodePngRgba(rgba, width, height, opt, &png)) {
    return false;
  }
  return AtomicWriteBytes(path, png.data(), png.size());
}

bool AtomicWriteBytes(const std::string& final_path, const uint8_t* data,
                      size_t len) {
  // 命名规范：同目录 <final>.tmp.<pid>.<tid>（vtx/runtime/fstxn.py 语义的 C++ 镜像；
  // pid+tid 保证并发编码线程互不碰撞，残件模式 *.tmp.<pid>.* 可辨识可清扫）。
  char suffix[64];
#if defined(__APPLE__)
  uint64_t tid = 0;
  if (pthread_threadid_np(nullptr, &tid) != 0) return false;
#else
  const auto tid = ::syscall(SYS_gettid);
#endif
  std::snprintf(suffix, sizeof(suffix), ".tmp.%ld.%ld",
                static_cast<long>(::getpid()),
                static_cast<long>(tid));
  const std::string tmp = final_path + suffix;
  std::FILE* fh = std::fopen(tmp.c_str(), "wb");
  if (fh == nullptr) {
    std::fprintf(stderr, "png_writer: 无法打开 %s\n", tmp.c_str());
    return false;
  }
  if (std::fwrite(data, 1, len, fh) != len) {
    std::fprintf(stderr, "png_writer: 原子写不完整（%s）\n", tmp.c_str());
    std::fclose(fh);
    std::remove(tmp.c_str());
    return false;
  }
  if (std::fflush(fh) != 0) {  // 用户态缓冲全数下刷
    std::fprintf(stderr, "png_writer: fflush 失败（%s）\n", tmp.c_str());
    std::fclose(fh);
    std::remove(tmp.c_str());
    return false;
  }
  if (std::fclose(fh) != 0) {  // 关闭完成（含缓冲下刷错误显形）
    std::fprintf(stderr, "png_writer: fclose 失败（%s）\n", tmp.c_str());
    std::remove(tmp.c_str());
    return false;
  }
  if (std::rename(tmp.c_str(), final_path.c_str()) != 0) {
    std::fprintf(stderr, "png_writer: rename 落盘失败 %s -> %s\n",
                 tmp.c_str(), final_path.c_str());
    std::remove(tmp.c_str());
    return false;
  }
  return true;  // 原子替换完成：目标任一时刻见到的都是完整文件
}

}  // namespace vtx_cef
