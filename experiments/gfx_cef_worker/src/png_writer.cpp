// png_writer.cpp — PNG(RGBA/8bit/非隔行) 编码实现（issue #109）。
//
// 结构：签名 + IHDR + 单 IDAT（zlib deflate 的「滤波扫描线」流：每行前缀
// 滤波字节 0=None）+ IEND；每块尾附 CRC32（zlib crc32()）。
// gfx_cli.png_invalid_reason 会对 IDAT 联合流做增量解压并核对解出总量 ==
// h*(1+w*4)，本编码器按该口径逐字节生成。
#include "png_writer.h"

#include <zlib.h>

#include <cstdio>
#include <cstring>
#include <vector>

namespace vtx_cef {
namespace {

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

}  // namespace

bool WritePngRgba(const std::string& path, const uint8_t* rgba, int width,
                  int height) {
  if (!rgba || width <= 0 || height <= 0) {
    return false;
  }
  // 滤波扫描线：每行 = 滤波字节 0 + w*4 像素字节。
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
  // Z_DEFAULT_COMPRESSION：PNG 的 zlib 流（含 2 字节头 + adler32 尾）。
  if (compress2(idat.data(), &compressed, raw.data(),
                static_cast<uLong>(raw.size()),
                Z_DEFAULT_COMPRESSION) != Z_OK) {
    std::fprintf(stderr, "png_writer: zlib compress2 失败\n");
    return false;
  }
  idat.resize(compressed);

  std::vector<uint8_t> png;
  png.reserve(8 + 25 + 12 + idat.size() + 12);
  static const uint8_t kSignature[8] = {0x89, 'P', 'N', 'G', 0x0D, 0x0A,
                                        0x1A, 0x0A};
  png.insert(png.end(), kSignature, kSignature + 8);

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
  PutChunk(&png, "IHDR", ihdr, sizeof(ihdr));
  PutChunk(&png, "IDAT", idat.data(), idat.size());
  PutChunk(&png, "IEND", nullptr, 0);

  std::FILE* fh = std::fopen(path.c_str(), "wb");
  if (fh == nullptr) {
    std::fprintf(stderr, "png_writer: 无法打开输出 %s\n", path.c_str());
    return false;
  }
  const size_t written = std::fwrite(png.data(), 1, png.size(), fh);
  const int closed = std::fclose(fh);
  if (written != png.size() || closed != 0) {
    std::fprintf(stderr, "png_writer: 写入不完整（%zu/%zu 字节）\n", written,
                 png.size());
    return false;
  }
  return true;
}

bool EncodePngRgba(const uint8_t* rgba, int width, int height,
                   std::vector<uint8_t>* png) {
  // 与 WritePngRgba 同一编码路径（落盘字节逐字节一致）；仅终点改为内存，
  // 供 #110 模式 1 的 t_png_encode_us / t_disk_commit_us 分步计时。
  if (!rgba || width <= 0 || height <= 0 || png == nullptr) {
    return false;
  }
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

bool AtomicWriteBytes(const std::string& final_path, const uint8_t* data,
                      size_t len) {
  // .tmp 写满 + rename：消费方任一时刻看到的 final_path 都是完整文件。
  const std::string tmp = final_path + ".tmp";
  std::FILE* fh = std::fopen(tmp.c_str(), "wb");
  if (fh == nullptr) {
    std::fprintf(stderr, "png_writer: 无法打开 %s\n", tmp.c_str());
    return false;
  }
  const size_t written = std::fwrite(data, 1, len, fh);
  const int closed = std::fclose(fh);
  if (written != len || closed != 0) {
    std::fprintf(stderr, "png_writer: 原子写不完整（%zu/%zu 字节）\n", written,
                 len);
    std::remove(tmp.c_str());
    return false;
  }
  if (std::rename(tmp.c_str(), final_path.c_str()) != 0) {
    std::fprintf(stderr, "png_writer: rename 落盘失败 %s -> %s\n",
                 tmp.c_str(), final_path.c_str());
    std::remove(tmp.c_str());
    return false;
  }
  return true;
}

}  // namespace vtx_cef
