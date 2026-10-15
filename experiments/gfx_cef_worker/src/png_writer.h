// png_writer.h — 最小 PNG(RGBA/8bit/非隔行) 编码器（issue #109）。
//
// 只为 worker 落一张帧 PNG：外部依赖仅系统 zlib（deflate + crc32），不引入
// 第三方图像库。输出满足 vtx/gfx_cli.py:png_invalid_reason() 的全部校验
// （签名/块结构/CRC/IDAT 可解码且总量与 IHDR 尺寸一致/无尾随数据）。
#ifndef GFX_CEF_WORKER_PNG_WRITER_H_
#define GFX_CEF_WORKER_PNG_WRITER_H_

#include <cstdint>
#include <string>
#include <vector>

namespace vtx_cef {

// rgba: w*h*4 字节，行序自上而下，每像素 R,G,B,A（直通 alpha，非预乘）。
// 成功写入返回 true；失败返回 false（含 I/O 错误与 zlib 失败）。
bool WritePngRgba(const std::string& path, const uint8_t* rgba, int width,
                  int height);

// #110：编码/落盘分步（模式 1 计时回执需要 t_png_encode_us 与
// t_disk_commit_us 分列；WritePngRgba 行为不变）。
// 编码到内存（内容与 WritePngRgba 落盘字节完全一致）。
bool EncodePngRgba(const uint8_t* rgba, int width, int height,
                   std::vector<uint8_t>* png);
// .tmp + rename 原子落盘（同目录同名 .tmp，可见性原子；内容完成后才改名）。
bool AtomicWriteBytes(const std::string& final_path, const uint8_t* data,
                      size_t len);

}  // namespace vtx_cef

#endif  // GFX_CEF_WORKER_PNG_WRITER_H_
