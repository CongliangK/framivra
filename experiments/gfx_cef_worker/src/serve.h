// serve.h — vtx_cef_worker 帧服务模式（issue #110 CEF-02a）。
//
// 控制流与数据流分离的双架构（工单口径）：
//   模式 1（Worker 原地落盘，首选零 IPC 像素传输）：宿主经 Unix Domain
//   Socket 发 64B 定长 FrameRequest；Worker 本地线程池完成
//   OnPaint 捕获 → 1px 金丝雀核验 → SIMD 逆预乘 → PNG 编码 →
//   .tmp+rename 原子落盘；仅回传 128B FrameSlotHeader 计时回执。
//   模式 2（SHM 环跨进程传输，为 #68 直通 FFmpeg 预研）：先取空槽（4 槽
//   全满即背压暂停，不再发起下一帧 BeginFrame，等待计时入
//   backpressure_wait_us）→ OnPaint 紧凑拷入槽 → 发布 → Socket 发 32B
//   槽索引通知；消费端经 SCM_RIGHTS 收到的同一 shm_open 句柄零拷贝读取，
//   读毕写槽门 EMPTY 释放。
//
// 生命周期：RunServe 自持 CefInitialize→CefShutdown、监听 socket、SHM 环、
// 编码线程池与全部清理路径；宿主断开（EOF）即优雅收尾。零残留由
// shm_ring 的「即建即解绑」+ socket 路径清理 + root_cache 归属方回收共同保证。
#ifndef GFX_CEF_WORKER_SERVE_H_
#define GFX_CEF_WORKER_SERVE_H_

#include <string>

#include "app_handler.h"
#include "frame_sync_machine.hpp"
#include "png_writer.h"

namespace vtx_cef {

struct ServeOptions {
  Options cef;                       // a0/a1、视口、故障注入 render_*
  std::string ipc_socket;            // Unix socket 路径（宿主侧目录）
  std::string url_template;          // 含 "{f}" 占位的页面模板（sync Mode P
                                     // 时为常驻页 URL，不含 {f}）
  std::string root_cache_path;       // 空 = 自建临时目录（退出即删）
  int idle_timeout_ms = 120000;      // 无请求空闲上限（EOF 前兜底）
  int slot_wait_timeout_ms = 15000;  // 背压等空槽上限（超时回 TIMEOUT）
  int frame_budget_ms = 15000;       // 单帧 produce（导航→金丝雀命中）上限
  int ready_settle_ms = 250;         // 加载落定后余量（#114 sync 路径不用
                                     // 固定 settle —— 就绪信号驱动）
  int encode_threads = 2;            // 本地编码线程池大小
  int fault_abort_after_frames = -1; // >=0：成功交付 N 帧后 abort()（DoD③）
  PngEncodeOptions png;              // #111 模式 1 编码后端/级别（默认 spng L1）
  // ---- #114 frame sync（五阶段状态机驱动；详见 frame_sync_machine.hpp）----
  bool sync = false;                 // --sync：启用 FrameSyncStateMachine
  std::string sync_mode = "n";       // n = 逐帧导航（?f=N）；p = 常驻页
  std::string sync_log;              // --sync-log：JSONL 全事件日志路径
  std::string sync_canary = "clean"; // clean | diagnostic | exact (Alpha production)
  std::string sync_fault = "none";   // none|stale_canary|no_canary|no_ready|
                                     // early_burst（对抗注入面）
  int sync_pump_timeout_ms = 5000;   // S4 泵送硬超时（工单冻结 5000ms）
  int sync_ready_timeout_ms = 20000; // S1→S3 就绪信号等待上限
};

// 阻塞服务直至宿主断开/空闲超时/致命错误；返回进程退出码。
// argc/argv 透传自 main（CefMainArgs 需真实命令行）。
int RunServe(const ServeOptions& opt, int argc, char* argv[]);

}  // namespace vtx_cef

#endif  // GFX_CEF_WORKER_SERVE_H_
