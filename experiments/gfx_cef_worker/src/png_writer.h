// png_writer.h — 最小 PNG(RGBA/8bit/非隔行) 编码器（issue #109）。
//
// 只为 worker 落一张帧 PNG：外部依赖仅系统 zlib（deflate + crc32），不引入
// 第三方图像库。输出满足 vtx/gfx_cli.py:png_invalid_reason() 的全部校验
// （签名/块结构/CRC/IDAT 可解码且总量与 IHDR 尺寸一致/无尾随数据）。
#ifndef GFX_CEF_WORKER_PNG_WRITER_H_
#define GFX_CEF_WORKER_PNG_WRITER_H_

#include <cstdint>
#include <string>

namespace vtx_cef {

// rgba: w*h*4 字节，行序自上而下，每像素 R,G,B,A（直通 alpha，非预乘）。
// 成功写入返回 true；失败返回 false（含 I/O 错误与 zlib 失败）。
bool WritePngRgba(const std::string& path, const uint8_t* rgba, int width,
                  int height);

}  // namespace vtx_cef

#endif  // GFX_CEF_WORKER_PNG_WRITER_H_
