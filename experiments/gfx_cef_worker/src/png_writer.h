// png_writer.h — 双后端 PNG(RGBA/8bit/非隔行) 编码器 + 原子落盘
// （issue #109 建立；#111 CEF-02b 升级）。
//
// #111 起编码面为双后端（--png-encoder / --png-level 切换）：
//   spng（默认推荐）— 内嵌 libspng v0.7.3（third_party/spng/，
//                      third_party_locks.json 哈希锚定）：
//                      SPNG_IMG_COMPRESSION_LEVEL=level（1..3）+
//                      SPNG_FILTER_CHOICE=SUB|UP；8-bit RGBA 输入走
//                      SPNG_FMT_PNG 布局（v0.7.3 编码面唯一接受的 fmt）。
//   fpng（极速对照） — 内嵌 fpng（third_party/fpng/）：fpng_init() 开启
//                      SSE4.1/PCLMUL 硬件 CRC32/Adler32；无压缩级别概念
//                      （level 被忽略）；写私有 fdEC 辅助块（PNG 标准内的
//                      ancillary 块，标准解码器跳过）。
//   zlib（遗留基线）— #109/#110 最小编码器（filter=0 + Z_DEFAULT_COMPRESSION，
//                      字节口径与 #110 产物一致）；保留作 #110 14.27ms 基线
//                      对照与回落面，level 被忽略。
//
// 落盘：AtomicWriteBytes —— 同目录 .tmp.<pid>.<tid> 临时名 + fwrite/fflush/
// fclose 全零返回校验 + rename() 原子替换（vtx/render/gfx_render.py:_atomic_write_bytes
// 与 vtx/runtime/fstxn.py 命名规范的 C++ 镜像）。
#ifndef GFX_CEF_WORKER_PNG_WRITER_H_
#define GFX_CEF_WORKER_PNG_WRITER_H_

#include <cstdint>
#include <string>
#include <vector>

namespace vtx_cef {

// ---------------------------------------------------------------- 编码后端
enum class PngEncoder { kSpng, kFpng, kZlib };

// spng 滤波选择（值域=libspng SPNG_FILTER_CHOICE 位集；工单默认 SUB|UP）。
enum class SpngFilter {
  kNone = 8,    // SPNG_FILTER_CHOICE_NONE（禁滤波=最快，文件较大）
  kSub = 16,    // 仅 SUB
  kUp = 32,     // 仅 UP
  kSubUp = 48,  // SUB|UP（工单口径默认）
  kAll = 248    // NONE|SUB|UP|AVG|PAETH（自适应全选，最慢）
};

// 名字互转（"spng"/"fpng"/"zlib"；"none"/"sub"/"up"/"sub_up"/"all"）；
// Parse 失败返回 false（out 允许 nullptr = 仅校验）。
bool ParsePngEncoderName(const std::string& name, PngEncoder* out);
const char* PngEncoderName(PngEncoder e);
bool ParseSpngFilterName(const std::string& name, SpngFilter* out);
const char* SpngFilterName(SpngFilter f);

// 编码参数（--png-encoder/--png-level/--png-filter 的运行时形态）。
struct PngEncodeOptions {
  PngEncoder encoder = PngEncoder::kSpng;  // 工单默认推荐
  int level = 1;  // spng: SPNG_IMG_COMPRESSION_LEVEL ∈ 1..3（fpng/zlib 忽略）
  SpngFilter spng_filter = SpngFilter::kSubUp;  // 工单口径 SUB|UP
};

// fpng 硬件加速初始化（SSE4.1/PCLMUL CRC32/Adler32）。幂等、线程安全；
// 工单要求启动时调用（main/selftest 入口显式调；编码路径内部亦兜底）。
void PngEncoderGlobalInit();

// 双编码器统一编码入口：rgba 为 w*h*4 直通 RGBA（行序自上而下）。
// 成功返回 true 且 png 为完整文件字节；失败返回 false（编码器错误/参数非法）。
// 确定性：同输入+同参数输出字节逐位一致（selftest 复跑校验）。
bool EncodePngRgba(const uint8_t* rgba, int width, int height,
                   const PngEncodeOptions& opt, std::vector<uint8_t>* png);

// #110 兼容重载（zlib 后端，字节与历史 #110 产物一致；计时段不受影响）。
bool EncodePngRgba(const uint8_t* rgba, int width, int height,
                   std::vector<uint8_t>* png);

// ---------------------------------------------------------------- 落盘

// 编码到内存 + 原子落盘一步完成（opt 选用编码后端）。
bool WritePngRgba(const std::string& path, const uint8_t* rgba, int width,
                  int height, const PngEncodeOptions& opt);

// .tmp.<pid>.<tid> + rename 原子落盘（同目录临时名；fwrite+fflush+fclose
// 返回值全 0 后 rename；任一失败 unlink 临时文件并返回 false）。
bool AtomicWriteBytes(const std::string& final_path, const uint8_t* data,
                      size_t len);

}  // namespace vtx_cef

#endif  // GFX_CEF_WORKER_PNG_WRITER_H_
