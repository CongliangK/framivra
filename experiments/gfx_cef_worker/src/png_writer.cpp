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

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <mutex>

#include "fpng.h"
#include "spng.h"

namespace vtx_cef {
namespace {

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

// ---------------------------------------------------------------- libspng
bool EncodeSpng(const uint8_t* rgba, int width, int height, int level,
                SpngFilter filter, std::vector<uint8_t>* png) {
  std::unique_ptr<spng_ctx, decltype(&spng_ctx_free)> ctx(
      spng_ctx_new(SPNG_CTX_ENCODER), &spng_ctx_free);
  if (ctx == nullptr) {
    std::fprintf(stderr, "png_writer: spng_ctx_new 失败\n");
    return false;
  }
  bool ok = false;
  do {
    if (spng_set_option(ctx.get(), SPNG_ENCODE_TO_BUFFER, 1) != 0) {
      std::fprintf(stderr, "png_writer: SPNG_ENCODE_TO_BUFFER 设置失败\n");
      break;
    }
    struct spng_ihdr ihdr;
    std::memset(&ihdr, 0, sizeof(ihdr));
    ihdr.width = static_cast<uint32_t>(width);
    ihdr.height = static_cast<uint32_t>(height);
    ihdr.bit_depth = 8;   // 8-bit
    ihdr.color_type = 6;  // RGBA
    // 工单口径：SUB|UP 滤波选择（可用 --png-filter 改）+ 压缩级别
    // （1..3；3 对齐 VTX_GFX_PNG_LEVEL）。
    if (spng_set_ihdr(ctx.get(), &ihdr) != 0 ||
        spng_set_option(ctx.get(), SPNG_IMG_COMPRESSION_LEVEL, level) != 0 ||
        spng_set_option(ctx.get(), SPNG_FILTER_CHOICE,
                        static_cast<int>(filter)) != 0) {
      std::fprintf(stderr, "png_writer: spng 头/选项设置失败\n");
      break;
    }
    // v0.7.3 编码面仅接受 SPNG_FMT_PNG/SPNG_FMT_RAW：8-bit RGBA 输入布局
    // 与 SPNG_FMT_PNG 逐字节一致（行内像素字节，滤波字节由本库生成）。
    const size_t len =
        static_cast<size_t>(width) * static_cast<size_t>(height) * 4u;
    const int ret = spng_encode_image(ctx.get(), rgba, len, SPNG_FMT_PNG,
                                      SPNG_ENCODE_FINALIZE);
    if (ret != 0) {
      std::fprintf(stderr, "png_writer: spng_encode_image 失败: %s\n",
                   spng_strerror(ret));
      break;
    }
    size_t out_len = 0;
    int err = 0;
    // spng_ctx_new uses the standard allocator. Adopt before validating length
    // so success, defensive rejection, and vector allocation failure all free it.
    std::unique_ptr<void, decltype(&std::free)> buf(
        spng_get_png_buffer(ctx.get(), &out_len, &err), &std::free);
    if (buf == nullptr || out_len == 0) {
      std::fprintf(stderr, "png_writer: spng_get_png_buffer 失败: %s\n",
                   spng_strerror(err));
      break;
    }
    png->assign(static_cast<const uint8_t*>(buf.get()),
                static_cast<const uint8_t*>(buf.get()) + out_len);
    ok = true;
  } while (false);
  return ok;
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
  if (rgba == nullptr || png == nullptr || width <= 0 || height <= 0) {
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
  std::vector<uint8_t> png;
  if (!EncodePngRgba(rgba, width, height, opt, &png)) {
    return false;
  }
  return AtomicWriteBytes(path, png.data(), png.size());
}

bool AtomicWriteBytes(const std::string& final_path, const uint8_t* data,
                      size_t len) {
  // 命名规范：同目录 <final>.tmp.<pid>.<tid>（vtx/fstxn.py 语义的 C++ 镜像；
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
