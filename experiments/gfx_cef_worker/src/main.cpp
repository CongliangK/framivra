// vtx_cef_worker — CEF Native OSR Worker（issue #109 CEF-01b；
// #110 CEF-02a 增帧服务模式）。
//
// 在 #108 构建骨架上交付双模式 OSR 启动与进程组自毁守卫：
//   - main() 第一行 prctl(PR_SET_PDEATHSIG, SIGKILL)（安全铁律）；
//   - --parent-alive-fd=<fd> 独立线程阻塞 read()，EOF/错误（宿主退出或被
//     SIGKILL）即 kill(0, SIGKILL) 终结整个 Worker 进程组（含 Zygote/GPU/
//     Renderer）—— 仅当自身是进程组长（监管器 start_new_session 契约）时
//     才组杀；误用（非组长）只自毁，绝不把信号广播进调用方进程组
//     （09:22 事故处方）；
//   - A0（Software OSR）/ A1（GPU 合成→CPU Buffer）双模式 CefSettings 与
//     命令行注入（见 app_handler.cpp）；
//   - OSR：SetAsWindowless(0) + external_begin_frame_enabled=true +
//     shared_texture_enabled=false（本阶段聚焦 CPU Buffer 通路），
//     SendExternalBeginFrame 驱动逐帧合成，OnPaint 全帧 BGRA（预乘）→
//     直通 RGBA → PNG（png_writer）；
//   - 真实后端探针（--probe / --probe-gpu-page）：页面内 WebGL
//     WEBGL_debug_renderer_info + WebGPU adapter info；chrome://gpu 特性
//     提取；结果随 --out-json 落档（供 env_manifest 采集消费）。
//
// #110 帧服务模式（--serve，见 serve.h/IPC_PROTOCOL.md）：
//   - 模式 1（Worker 原地落盘）/ 模式 2（4-Slot SHM 环零拷贝）双协议；
//   - --fault-inject 故障注入面（size_mismatch / abort_after_frame）；
//   - --sanity 自检沿用 #108（版本锁运行时自证），行为不变。
#if defined(__linux__)
#include <sys/prctl.h>
#endif
#if defined(__APPLE__)
#include "mac_runtime.h"
#include <crt_externs.h>
#endif
#include <sys/types.h>
#include <unistd.h>

#include <cerrno>
#include <signal.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <functional>
#include <map>
#include <string>
#include <thread>
#include <vector>

#include "app_handler.h"
#include "frame_pipeline.h"
#include "png_writer.h"
#include "serve.h"

#include "include/cef_api_hash.h"
#include "include/cef_app.h"
#include "include/cef_browser.h"
#include "include/cef_version.h"
#include "include/cef_version_info.h"

#define VTX_CHROME_VERSION_STRING    \
  MAKE_STRING(CHROME_VERSION_MAJOR)  \
  "." MAKE_STRING(CHROME_VERSION_MINOR) "." MAKE_STRING(           \
      CHROME_VERSION_BUILD) "." MAKE_STRING(CHROME_VERSION_PATCH)

namespace {

using Clock = std::chrono::steady_clock;

// ---------------------------------------------------------------- 运行选项

struct RunOptions {
  vtx_cef::Options cef;          // A0/A1 + 探针开关
  std::string url;               // 加载页（file:// / chrome:// …）
  std::string out_png;           // 空 = 不落 PNG
  std::string out_json;          // 空 = 不落结果 JSON
  std::string root_cache_path;   // 空 = 临时目录（退出时删除）
  int begin_frames = 6;          // load 后驱动的 external begin frame 数
  int timeout_ms = 45000;        // 阶段等待上限
  int hold_ms = 0;               // 出帧后保持渲染的时长（kill 演练用）
  int parent_alive_fd = -1;      // 监管器父存活管道读端
  // ---- #110 帧服务模式（--serve）----
  bool serve = false;
  int ipc_fd = -1;
  std::string ipc_socket;              // Unix socket 路径
  std::string url_template;            // 含 {f} 占位
  std::string fault_inject;            // none|size_mismatch|abort_after_frame
  int fault_abort_after_frames = 1;    // abort_after_frame 的 N
  int slot_wait_timeout_ms = 15000;    // 背压等空槽上限
  int serve_idle_timeout_ms = 120000;  // 无请求空闲上限
  int encode_threads = 2;              // 模式 1 编码线程池
  // ---- #111 双 PNG 编码后端（--png-encoder/--png-level/--png-filter）----
  std::string png_encoder = "spng";    // 默认推荐 libspng（对照 fpng/zlib）
  int png_level = 1;                   // spng SPNG_IMG_COMPRESSION_LEVEL 1..3
  std::string png_filter = "sub_up";   // 工单口径 SUB|UP（可改 none/sub/up/all）
  // ---- #114 帧同步状态机（--sync；详见 frame_sync_machine.h）----
  bool sync = false;                   // 启用五阶段状态机驱动
  std::string sync_mode = "n";         // n = 逐帧导航（?f=N）| p = 常驻页
  std::string sync_log;                // JSONL 全事件日志路径
  std::string sync_canary = "clean";   // clean | diagnostic | exact (Alpha production)
  std::string sync_fault = "none";     // 对抗注入面（见 Usage）
  int sync_pump_timeout_ms = 5000;     // S4 泵送硬超时（工单冻结 5000ms）
  int sync_ready_timeout_ms = 20000;   // S1→S3 就绪信号等待上限
};

void Usage(const char* argv0) {
  std::printf(
      "vtx_cef_worker (issue #109 CEF-01b)\n"
      "CEF:      %s\n"
      "Chromium: %s\n"
      "\n"
      "用法: %s --sanity\n"
      "      %s --mode=a0|a1 --url=<url> [选项]\n"
      "\n"
      "选项:\n"
      "  --mode=a0|a1            A0=Software OSR（默认）；A1=GPU 合成→CPU Buffer\n"
      "  --ipc-fd=N              已连接 Unix stream fd（与 --ipc-socket 互斥）\n"
      "  --ozone-platform=p      A1 平台切换: headless|x11|wayland（默认 headless）\n"
      "  --url=<url>             要加载渲染的页面\n"
      "  --out-png=<path>        输出 PNG（默认尺寸 1920x1080）\n"
      "  --out-json=<path>       输出运行结果 JSON（探针证据/计时/版本）\n"
      "  --root-cache-path=<dir> CEF root_cache_path（监管器运行目录；缺省临时目录）\n"
      "  --width=N --height=N    视口尺寸（默认 1920x1080）\n"
      "  --probe                 注入 WebGL/WebGPU 真实后端探针\n"
      "  --probe-gpu-page        渲染后再导航 chrome://gpu 提取特性（隐含 --probe）\n"
      "  --begin-frames=N        load 后驱动的 external begin frame 数（默认 6）\n"
      "  --hold-ms=N             出帧后保持渲染循环 N ms（监管 kill 演练）\n"
      "  --timeout-ms=N          阶段等待上限（默认 45000）\n"
      "  --parent-alive-fd=N     监管器父存活管道读端（EOF 即组杀，监管契约）\n"
      "\n"
      "帧服务模式（#110，协议详见 experiments/gfx_cef_worker/IPC_PROTOCOL.md）:\n"
      "  --serve                 进入帧服务循环（Unix Socket 定长协议 + 4-Slot SHM 环）\n"
      "  --ipc-socket=<path>     服务 Unix socket 路径（宿主目录；就绪行 VTXCEF_SERVE_READY）\n"
      "  --url-template=<tpl>    帧页模板，须含 {f} 占位（每请求代入帧号导航）\n"
      "  --fault-inject=<mode>   none|size_mismatch|abort_after_frame（DoD 故障注入）\n"
      "  --fault-abort-after-frames=N  abort_after_frame 的 N（默认 1）\n"
      "  --slot-wait-timeout-ms=N      4 槽全满背压等待上限（默认 15000）\n"
      "  --serve-idle-timeout-ms=N     无请求空闲上限（默认 120000）\n"
      "  --encode-threads=N            模式 1 编码线程池大小（默认 2）\n"
      "\n"
      "#111 PNG 编码后端（模式 1 落盘与 --out-png 共用；详见 README「双 PNG 编码后端」）:\n"
      "  --png-encoder=spng|fpng|zlib  spng=libspng v0.7.3 SUB|UP 滤波（默认，推荐）；\n"
      "                                fpng=极速对照（SSE4.1/PCLMUL，私有 fdEC 块）；\n"
      "                                zlib=#109/#110 遗留基线（filter-0+Z_DEFAULT）\n"
      "  --png-level=1..3              spng SPNG_IMG_COMPRESSION_LEVEL（默认 1；\n"
      "                                3 对齐 VTX_GFX_PNG_LEVEL；fpng/zlib 忽略）\n"
      "  --png-filter=none|sub|up|sub_up|all  spng 滤波选择（默认 sub_up=工单\n"
      "                                口径；none 最快文件较大——见 README 性能实测）\n"
      "\n"
      "#114 帧同步状态机（五阶段 S0→S5 + v2 双像素 Canary；搭配 --serve）:\n"
      "  --sync                  启用 FrameSyncStateMachine（替代固定 settle 等待）\n"
      "  --sync-mode=n|p         n = 逐帧导航 ?f=N（默认）；p = 常驻页 __vtx_render(N)\n"
      "  --sync-log=<path>       JSONL 全事件日志（状态转移/paint 判定/时序/计数）\n"
      "  --sync-canary=clean|diagnostic|exact  exact = native cross-capture (production Alpha)\n"
      "                          clean = v2 双像素（默认）；diagnostic = 8x8\n"
      "  --sync-fault=<mode>     none|stale_canary（恒画 f-1 签名）|no_canary（不画）|\n"
      "                          no_ready（不发就绪信号）|early_burst（S1 突发泵送）\n"
      "  --sync-pump-timeout-ms=N        S4 泵送硬超时（默认 5000，工单口径）\n"
      "  --sync-ready-timeout-ms=N       S1→S3 就绪等待上限（默认 20000）\n",
      CEF_VERSION, VTX_CHROME_VERSION_STRING, argv0, argv0);
}

// 严格 --key=value / 布尔旗标；未知参数 exit 2（沿 #108：不静默按默认面）。
bool ParseArgs(int argc, char* argv[], RunOptions* opt, bool* sanity) {
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    auto value = [&arg](const char* key) -> std::string {
      const size_t len = std::strlen(key);
      if (arg.compare(0, len, key) == 0 && arg.size() > len) {
        return arg.substr(len);
      }
      return {};
    };
    auto num = [&arg, &value](const char* key, int* out) {
      const std::string v = value(key);
      if (v.empty()) {
        return false;
      }
      char* end = nullptr;
      const long n = std::strtol(v.c_str(), &end, 10);
      if (end == nullptr || *end != '\0' || n < 0 || n > 1000000000L) {
        std::fprintf(stderr, "参数值非法: %s\n", arg.c_str());
        std::exit(2);
      }
      *out = static_cast<int>(n);
      return true;
    };
    if (arg == "--sanity") {
      *sanity = true;
    } else if (const std::string v = value("--mode="); !v.empty()) {
      opt->cef.mode = v;
    } else if (const std::string v = value("--ozone-platform="); !v.empty()) {
      opt->cef.ozone_platform = v;
    } else if (const std::string v = value("--url="); !v.empty()) {
      opt->url = v;
    } else if (const std::string v = value("--out-png="); !v.empty()) {
      opt->out_png = v;
    } else if (const std::string v = value("--out-json="); !v.empty()) {
      opt->out_json = v;
    } else if (const std::string v = value("--root-cache-path="); !v.empty()) {
      opt->root_cache_path = v;
    } else if (num("--width=", &opt->cef.width)) {
    } else if (num("--height=", &opt->cef.height)) {
    } else if (num("--begin-frames=", &opt->begin_frames)) {
    } else if (num("--timeout-ms=", &opt->timeout_ms)) {
    } else if (num("--hold-ms=", &opt->hold_ms)) {
    } else if (num("--parent-alive-fd=", &opt->parent_alive_fd)) {
    } else if (num("--slot-wait-timeout-ms=", &opt->slot_wait_timeout_ms)) {
    } else if (num("--serve-idle-timeout-ms=", &opt->serve_idle_timeout_ms)) {
    } else if (num("--encode-threads=", &opt->encode_threads)) {
    } else if (num("--png-level=", &opt->png_level)) {
    } else if (num("--sync-pump-timeout-ms=", &opt->sync_pump_timeout_ms)) {
    } else if (num("--sync-ready-timeout-ms=", &opt->sync_ready_timeout_ms)) {
    } else if (arg == "--sync") {
      opt->sync = true;
    } else if (const std::string v = value("--sync-mode="); !v.empty()) {
      opt->sync_mode = v;
    } else if (const std::string v = value("--sync-log="); !v.empty()) {
      opt->sync_log = v;
    } else if (const std::string v = value("--sync-canary="); !v.empty()) {
      opt->sync_canary = v;
    } else if (const std::string v = value("--sync-fault="); !v.empty()) {
      opt->sync_fault = v;
    } else if (const std::string v = value("--png-encoder="); !v.empty()) {
      opt->png_encoder = v;
    } else if (const std::string v = value("--png-filter="); !v.empty()) {
      opt->png_filter = v;
    } else if (num("--fault-abort-after-frames=",
                   &opt->fault_abort_after_frames)) {
    } else if (arg == "--serve") {
      opt->serve = true;
    } else if (num("--ipc-fd=", &opt->ipc_fd)) {
    } else if (const std::string v = value("--ipc-socket="); !v.empty()) {
      opt->ipc_socket = v;
    } else if (const std::string v = value("--url-template="); !v.empty()) {
      opt->url_template = v;
    } else if (const std::string v = value("--fault-inject="); !v.empty()) {
      opt->fault_inject = v;
    } else if (arg == "--probe") {
      opt->cef.probe = true;
    } else if (arg == "--probe-gpu-page") {
      opt->cef.probe = true;
      opt->cef.probe_gpu_page = true;
    } else if (arg == "-h" || arg == "--help") {
      Usage(argv[0]);
      std::exit(0);
    } else {
      std::fprintf(stderr, "未知参数: %s（-h 查看用法）\n", arg.c_str());
      return false;
    }
  }
  if (opt->cef.mode != "a0" && opt->cef.mode != "a1") {
    std::fprintf(stderr, "--mode 只接受 a0|a1，得到 %s\n",
                 opt->cef.mode.c_str());
    return false;
  }
  // #111：编码后端名与级别在参数面即拒非法值（exit 2，不静默回落）。
  if (!vtx_cef::ParsePngEncoderName(opt->png_encoder, nullptr)) {
    std::fprintf(stderr,
                 "--png-encoder 只接受 spng|fpng|zlib，得到 %s\n",
                 opt->png_encoder.c_str());
    return false;
  }
  if (opt->png_level < 1 || opt->png_level > 3) {
    std::fprintf(stderr, "--png-level 只接受 1..3，得到 %d\n", opt->png_level);
    return false;
  }
  if (!vtx_cef::ParseSpngFilterName(opt->png_filter, nullptr)) {
    std::fprintf(stderr,
                 "--png-filter 只接受 none|sub|up|sub_up|all，得到 %s\n",
                 opt->png_filter.c_str());
    return false;
  }
  if (opt->serve) {
    if ((opt->ipc_socket.empty() && opt->ipc_fd < 0) ||
        (!opt->ipc_socket.empty() && opt->ipc_fd >= 0) || opt->url_template.empty()) {
      std::fprintf(stderr,
                   "--serve 需要 --ipc-socket=<path> 与 --url-template=<tpl>\n");
      return false;
    }
    // #114：sync 参数面校验（非法值 exit 2，不静默回落）。
    if (opt->sync) {
      if (opt->sync_mode != "n" && opt->sync_mode != "p") {
        std::fprintf(stderr, "--sync-mode 只接受 n|p，得到 %s\n",
                     opt->sync_mode.c_str());
        return false;
      }
      if (opt->sync_canary != "clean" && opt->sync_canary != "diagnostic" &&
          opt->sync_canary != "exact") {
        std::fprintf(stderr, "--sync-canary 只接受 clean|diagnostic|exact，得到 %s\n",
                     opt->sync_canary.c_str());
        return false;
      }
      const std::vector<std::string> faults = {
          "none", "stale_canary", "no_canary", "no_ready", "early_burst"};
      if (std::find(faults.begin(), faults.end(), opt->sync_fault) ==
          faults.end()) {
        std::fprintf(stderr,
                     "--sync-fault 只接受 none|stale_canary|no_canary|no_ready|"
                     "early_burst，得到 %s\n",
                     opt->sync_fault.c_str());
        return false;
      }
      if (opt->sync_pump_timeout_ms <= 0 || opt->sync_ready_timeout_ms <= 0) {
        std::fprintf(stderr, "--sync-*-timeout-ms 须为正\n");
        return false;
      }
    }
    if (opt->fault_inject.empty() || opt->fault_inject == "none") {
      // 正常面
    } else if (opt->fault_inject == "size_mismatch") {
      // DoD①：视图实际渲染高 = 期望高-1（GetViewRect 与捕获卫兵错位）。
      opt->cef.render_width = opt->cef.width;
      opt->cef.render_height = opt->cef.height - 1;
    } else if (opt->fault_inject == "abort_after_frame") {
      // DoD③：RunServe 内部按 --fault-abort-after-frames 触发 abort()。
    } else {
      std::fprintf(stderr, "--fault-inject 只接受 none|size_mismatch|"
                           "abort_after_frame，得到 %s\n",
                   opt->fault_inject.c_str());
      return false;
    }
  }
  return true;
}

// ---------------------------------------------------------------- 工具

// 启动环境真相：/proc/self/environ 是 exec 时刻的原始环境块，不受进程
// 运行期 setenv 污染（实测 Chromium 会在运行期自行重注入
// WAYLAND_DISPLAY=wayland-0，exit 时 getenv 不可信）。NUL 分隔逐项解析。
std::map<std::string, std::string> LaunchEnvSnapshot() {
  std::map<std::string, std::string> out;
#if defined(__APPLE__)
  for (char** e = *_NSGetEnviron(); *e; ++e) {
    const std::string item(*e);
    const size_t eq = item.find('=');
    if (eq != std::string::npos) out[item.substr(0, eq)] = item.substr(eq + 1);
  }
  return out;
#else
  std::FILE* fh = std::fopen("/proc/self/environ", "rb");
  if (fh == nullptr) {
    return out;
  }
  std::string buf;
  char chunk[4096];
  size_t n;
  while ((n = std::fread(chunk, 1, sizeof(chunk), fh)) > 0) {
    buf.append(chunk, n);
  }
  std::fclose(fh);
  size_t start = 0;
  while (start < buf.size()) {
    const size_t end = buf.find('\0', start);
    const std::string item =
        buf.substr(start, (end == std::string::npos ? buf.size() : end) - start);
    const size_t eq = item.find('=');
    if (eq != std::string::npos) {
      out[item.substr(0, eq)] = item.substr(eq + 1);
    }
    if (end == std::string::npos) {
      break;
    }
    start = end + 1;
  }
  return out;
#endif
}

std::string JsonEscape(const std::string& s) {
  std::string out;
  out.reserve(s.size() + 8);
  for (char ch : s) {
    const auto c = static_cast<unsigned char>(ch);
    switch (c) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default:
        if (c < 0x20) {
          char buf[8];
          std::snprintf(buf, sizeof(buf), "\\u%04x", c);
          out += buf;
        } else {
          out += ch;
        }
    }
  }
  return out;
}

// 探针 payload 本身是 JSON.stringify 的完整 JSON 文本：形状完整则原样内嵌
// （消费方单次解析），否则按普通字符串转义兜底。
std::string EmbedProbePayload(const std::string& payload) {
  if (payload.empty()) {
    return "null";
  }
  if (payload.front() == '{' && payload.back() == '}') {
    return payload;
  }
  return "\"" + JsonEscape(payload) + "\"";
}

// 预乘 BGRA → 直通 RGBA：#111 起统一走 frame_pipeline→pixel_ops SIMD 算子
// （本文件历史内联标量实现已删；公式同源由 selftest 穷举保证）。

// ---------------------------------------------------------------- sanity（#108 原样）

struct VersionCheck {
  const char* name;
  int runtime;
  int locked;
};

bool Check(const VersionCheck& check) {
  const bool ok = check.runtime == check.locked;
  std::printf("%-22s runtime=%-8d locked=%-8d %s\n", check.name,
              check.runtime, check.locked, ok ? "ok" : "MISMATCH");
  return ok;
}

int Sanity() {
  const VersionCheck checks[] = {
      {"CEF_VERSION_MAJOR", cef_version_info(0), CEF_VERSION_MAJOR},
      {"CEF_VERSION_MINOR", cef_version_info(1), CEF_VERSION_MINOR},
      {"CEF_VERSION_PATCH", cef_version_info(2), CEF_VERSION_PATCH},
      {"CHROME_VERSION_MAJOR", cef_version_info(4), CHROME_VERSION_MAJOR},
      {"CHROME_VERSION_MINOR", cef_version_info(5), CHROME_VERSION_MINOR},
      {"CHROME_VERSION_BUILD", cef_version_info(6), CHROME_VERSION_BUILD},
      {"CHROME_VERSION_PATCH", cef_version_info(7), CHROME_VERSION_PATCH},
  };
  bool ok = true;
  for (const VersionCheck& check : checks) {
    ok = Check(check) && ok;
  }

  const char* runtime_hash = cef_api_hash(CEF_API_VERSION, 0);
  const bool hash_ok = runtime_hash != nullptr &&
                        std::strcmp(runtime_hash, CEF_API_HASH_PLATFORM) == 0;
  std::printf("%-22s runtime=%s\n%-22s locked=%s %s\n", "CEF_API_HASH_PLATFORM",
              runtime_hash != nullptr ? runtime_hash : "(null)",
              "", CEF_API_HASH_PLATFORM, hash_ok ? "ok" : "MISMATCH");
  ok = hash_ok && ok;

  CefRefPtr<vtx_cef::WorkerApp> app = new vtx_cef::WorkerApp(nullptr, nullptr);
  const bool wrapper_ok = app->GetBrowserProcessHandler().get() == app.get();
  std::printf("%-22s %s\n", "libcef_dll_wrapper",
              wrapper_ok ? "ok" : "MISMATCH");
  ok = wrapper_ok && ok;

  if (ok) {
    std::printf("SANITY OK: cef=%s chromium=%s (libcef.so 与版本锁一致)\n",
                CEF_VERSION, VTX_CHROME_VERSION_STRING);
    return 0;
  }
  std::fprintf(stderr, "SANITY FAIL: libcef.so 与编译期版本锁不一致\n");
  return 1;
}

}  // namespace

int main(int argc, char* argv[]) {
  // ===== 工单铁律：main() 入口第一行 PDEATHSIG（父死内核即刻击杀本进程）。
  //        本二进制被 CEF 以 --type=zygote/gpu/renderer/utility 再入执行子
  //        进程 —— 它们同样过这行：exec 派生的子进程全挂 PDEATHSIG。
#if defined(__linux__)
  ::prctl(PR_SET_PDEATHSIG, SIGKILL);
#endif
  const std::map<std::string, std::string> launch_env = LaunchEnvSnapshot();
#if defined(__APPLE__)
  vtx_cef::MacRuntime mac_runtime(argc, argv);
#endif

  // ===== CEF 子进程路径：同一可执行以 --type=* 再入（zygote/GPU/renderer/
  //        utility），必须先交 CefExecuteProcess 接管，返回值 >= 0 即子进程
  //        已走完（其 argv 是 Chromium 参数面，不进本 worker 参数解析）。
  int subprocess_exit = -1;
  {
    CefMainArgs subprocess_args(argc, argv);
    CefRefPtr<vtx_cef::WorkerApp> subprocess_app =
        new vtx_cef::WorkerApp(nullptr, nullptr);
    subprocess_exit = CefExecuteProcess(subprocess_args, subprocess_app, nullptr);
  }  // Release the application reference before terminating the subprocess.
  if (subprocess_exit >= 0) {
    // Chromium rotates the stack guard in zygote-forked children. Returning
    // through this pre-fork main frame would check its stale saved guard
    // (__stack_chk_fail, confirmed in a utility core). Keep stack protection
    // and guard rotation enabled; only bypass this obsolete frame's epilogue.
    // Unlike _exit/quick_exit, exit retains normal stdio flushing and atexit/
    // static destruction, just like returning from main. Worker/browser RAII
    // and CefShutdown remain on the unchanged browser-only path below.
    std::exit(subprocess_exit);
  }

  bool sanity = false;
  RunOptions opt;
  if (!ParseArgs(argc, argv, &opt, &sanity)) {
    Usage(argv[0]);
    return 2;
  }
  // #111：fpng 硬件加速初始化（SSE4.1/PCLMUL CRC32/Adler32；幂等）——
  // 工单要求启动时调用（早于任何编码请求；spng/zlib 无全局态）。
  vtx_cef::PngEncoderGlobalInit();
  vtx_cef::PngEncoder png_backend = vtx_cef::PngEncoder::kSpng;
  if (!vtx_cef::ParsePngEncoderName(opt.png_encoder, &png_backend)) {
    return 2;  // ParseArgs 已拒非法值；此处仅防御
  }
  vtx_cef::PngEncodeOptions png_opt;
  png_opt.encoder = png_backend;
  png_opt.level = opt.png_level;
  if (!vtx_cef::ParseSpngFilterName(opt.png_filter, &png_opt.spng_filter)) {
    return 2;  // 同上
  }
  if (sanity) {
    return Sanity();
  }
  if (!opt.serve && opt.url.empty()) {
    std::fprintf(stderr, "缺少 --url（-h 查看用法）\n");
    return 2;
  }

  // ===== --parent-alive-fd 守卫线程（监管契约；在 CEF 派生前启动，
  //        覆盖初始化窗口内宿主暴毙的场景）。
  if (opt.parent_alive_fd >= 0) {
    const int fd = opt.parent_alive_fd;
    const pid_t self = ::getpid();
    std::thread([fd, self]() {
      char buf[64];
      for (;;) {
        const ssize_t n = ::read(fd, buf, sizeof(buf));
        if (n > 0) {
          continue;  // 宿主不写数据；读到任何数据都视作仍存活
        }
        if (n < 0 && errno == EINTR) {
          continue;
        }
        break;  // EOF(0) 或错误(<0)：宿主已退出/管道断裂
      }
      // 仅当自身是进程组长（监管器 start_new_session 契约成立）才允许
      // kill(0) 组杀全树（含 Zygote/GPU/Renderer）；否则只自毁——绝不把
      // SIGKILL 广播进调用方进程组（09:22 事故形态零容忍）。
      if (::getpgid(0) == self) {
        ::kill(0, SIGKILL);
      } else {
        std::fprintf(stderr,
                     "vtx_cef_worker: --parent-alive-fd EOF 但自身非进程组"
                     "长（getpgid=%ld, pid=%ld）——仅自毁，不组杀\n",
                     static_cast<long>(::getpgid(0)),
                     static_cast<long>(self));
        ::kill(self, SIGKILL);
      }
    }).detach();
  }

  // ===== #110 帧服务模式：自持 CEF/Socket/SHM 环/编码池生命周期
  //        （守卫已武装，覆盖其 CEF 初始化窗口；见 serve.h）。
  if (opt.serve) {
    vtx_cef::ServeOptions sopt;
    sopt.cef = opt.cef;
    sopt.ipc_socket = opt.ipc_socket;
    sopt.ipc_fd = opt.ipc_fd;
    sopt.url_template = opt.url_template;
    sopt.root_cache_path = opt.root_cache_path;
    sopt.slot_wait_timeout_ms = opt.slot_wait_timeout_ms;
    sopt.idle_timeout_ms = opt.serve_idle_timeout_ms;
    sopt.encode_threads = opt.encode_threads;
    sopt.png = png_opt;              // #111 模式 1 编码后端/级别/滤波
    sopt.sync = opt.sync;            // #114 五阶段状态机
    sopt.sync_mode = opt.sync_mode;
    sopt.sync_log = opt.sync_log;
    sopt.sync_canary = opt.sync_canary;
    sopt.sync_fault = opt.sync_fault;
    sopt.sync_pump_timeout_ms = opt.sync_pump_timeout_ms;
    sopt.sync_ready_timeout_ms = opt.sync_ready_timeout_ms;
    if (opt.fault_inject == "abort_after_frame") {
      sopt.fault_abort_after_frames = opt.fault_abort_after_frames;
    }
    return vtx_cef::RunServe(sopt, argc, argv);
  }

  // ===== root_cache_path：监管路径由参数指定；缺省建临时目录退出即删。
  std::filesystem::path root_cache(opt.root_cache_path);
  const bool owns_cache = opt.root_cache_path.empty();
  if (owns_cache) {
    std::error_code ec;
    root_cache = std::filesystem::temp_directory_path(ec) /
                 ("vtx_cef_worker-" + std::to_string(::getpid()));
  }
  {
    std::error_code ec;
    std::filesystem::create_directories(root_cache, ec);
    if (ec) {
      std::fprintf(stderr, "vtx_cef_worker: root_cache_path 不可创建: %s (%s)\n",
                   root_cache.string().c_str(), ec.message().c_str());
      return 1;
    }
  }

  const Clock::time_point t0 = Clock::now();
  vtx_cef::RunState state;
  // 启动环境快照（exec 时刻真相；Chromium 运行期会重注入 WAYLAND_DISPLAY，
  // exit 时 getenv 不可信——矩阵证据保真依赖此快照）

  // ===== CEF 初始化（工单口径：windowless + no_sandbox + 单线程消息循环
  //        + external_message_pump 外置泵；#110 起经共享装配，与 serve 模式
  //        同源，防配置漂移）。
  CefMainArgs main_args(argc, argv);
  CefSettings settings = vtx_cef::MakeWorkerSettings(root_cache.string());

  CefRefPtr<vtx_cef::WorkerApp> app = new vtx_cef::WorkerApp(&opt.cef, &state);
  if (!CefInitialize(main_args, settings, app, nullptr)) {
    std::fprintf(stderr, "vtx_cef_worker: CefInitialize 失败\n");
    if (owns_cache) {
      std::error_code ec;
      std::filesystem::remove_all(root_cache, ec);
    }
    return 1;
  }

  int exit_code = 0;
  std::string exit_reason = "ok";
  do {
    // ===== 外置泵（#110 抽提为 PumpDriver，语义与 #109 逐拍一致）。
    vtx_cef::PumpDriver pump{&state};
    const auto pump_once = [&]() { pump.Once(); };
    const auto pump_until = [&](const std::function<bool()>& done,
                                int timeout_ms) -> bool {
      return pump.Until(done, timeout_ms);
    };

    // ===== 建窗：OSR（windowless + external begin frame + 非 shared texture）
    CefRefPtr<vtx_cef::WorkerClient> client =
        new vtx_cef::WorkerClient(&opt.cef, &state);
    CefString url_str;
    url_str.FromString(opt.url);
    if (!vtx_cef::CreateOsrBrowser(client, url_str, opt.cef.width,
                                   opt.cef.height)) {
      exit_code = 1;
      exit_reason = "CreateBrowser 失败";
      break;
    }
    // 建窗为异步：OnAfterCreated 捕获 browser 引用
    CefRefPtr<CefBrowser> browser;
    if (!pump_until(
            [&]() {
              std::lock_guard<std::mutex> lock(state.mu);
              return state.browser != nullptr;
            },
            opt.timeout_ms)) {
      exit_code = 124;
      exit_reason = "浏览器创建超时（OnAfterCreated 未回）";
      break;
    }
    {
      std::lock_guard<std::mutex> lock(state.mu);
      browser = state.browser;
    }

    // 阶段 1：等首屏加载完成（is_loading 翻转）
    if (!pump_until([&]() { return state.loaded.load(); }, opt.timeout_ms)) {
      exit_code = 124;
      exit_reason = "页面加载超时（isLoading 未翻转）";
      break;
    }
    const int ms_load = static_cast<int>(
        std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - t0)
            .count());
    pump_until([]() { return false; }, 250);  // 字体/布局落定余量

    // 阶段 2（--probe）：页面内真实后端探针（WebGL/WebGPU）
    if (opt.cef.probe) {
      browser->GetMainFrame()->ExecuteJavaScript(vtx_cef::BuildPageProbeJs(),
                                                 "vtx_cef_probe", 0);
      const bool got = pump_until(
          [&]() {
            std::lock_guard<std::mutex> lock(state.mu);
            return !state.page_probe.empty();
          },
          8000);
      if (!got) {
        std::lock_guard<std::mutex> lock(state.mu);
        if (state.page_probe.empty()) {
          state.last_error = "页面探针无响应（console marker 未回收）";
        }
      }
    }

    // 阶段 3：external begin frame 驱动出帧（OSR 无内部 vsync）
    Clock::time_point t_first_paint = Clock::now();
    bool painted = false;
    for (int i = 0; i < opt.begin_frames && !painted; ++i) {
      const int before = state.paints.load(std::memory_order_relaxed);
      browser->GetHost()->SendExternalBeginFrame();
      if (pump_until(
              [&]() {
                return state.paints.load(std::memory_order_relaxed) > before;
              },
              120)) {
        painted = state.paints.load(std::memory_order_relaxed) > before;
        if (painted) {
          t_first_paint = Clock::now();
        }
      }
    }
    const int ms_first_paint =
        painted ? static_cast<int>(std::chrono::duration_cast<
                                       std::chrono::milliseconds>(
                                       t_first_paint - t0).count())
                : -1;

    // 阶段 4：OnPaint 全帧 BGRA（预乘）→ SIMD 逆预乘 → 直通 RGBA → PNG
    //         （#111：编码后端按 --png-encoder/--png-level 选用，默认 spng）
    int64_t png_bytes = 0;
    bool png_ok = false;
    if (!opt.out_png.empty()) {
      std::vector<uint8_t> rgba;
      int w = 0;
      int h = 0;
      {
        std::lock_guard<std::mutex> lock(state.mu);
        w = state.width;
        h = state.height;
        if (!state.buffer.empty()) {
          rgba.resize(state.buffer.size());
          vtx_cef::UnpremultiplyToRgba(state.buffer.data(),
                                       state.buffer.size(), rgba.data());
        }
      }
      if (rgba.empty()) {
        exit_code = 1;
        exit_reason = "未收到任何 OnPaint（buffer 空）";
      } else if (w != opt.cef.width || h != opt.cef.height) {
        exit_code = 1;
        exit_reason = "OnPaint 尺寸不符: " + std::to_string(w) + "x" +
                      std::to_string(h);
      } else {
        png_ok = vtx_cef::WritePngRgba(opt.out_png, rgba.data(), w, h,
                                       png_opt);
        if (!png_ok) {
          exit_code = 1;
          exit_reason = "PNG 写入失败: " + opt.out_png;
        } else {
          std::error_code sz_ec;
          png_bytes = static_cast<int64_t>(
              std::filesystem::file_size(opt.out_png, sz_ec));
        }
      }
    }

    // 阶段 5（--probe-gpu-page）：chrome://gpu 特性提取（同浏览器换导航）
    if (opt.cef.probe_gpu_page && exit_code == 0) {
      state.loaded.store(false);
      browser->GetMainFrame()->LoadURL("chrome://gpu/");
      if (pump_until([&]() { return state.loaded.load(); }, opt.timeout_ms)) {
        pump_until([]() { return false; }, 400);  // WebUI 渲染落定
        browser->GetMainFrame()->ExecuteJavaScript(
            vtx_cef::BuildGpuPageProbeJs(), "vtx_cef_gpu_probe", 0);
        const bool got = pump_until(
            [&]() {
              std::lock_guard<std::mutex> lock(state.mu);
              return !state.gpu_page_probe.empty();
            },
            8000);
        if (!got) {
          std::lock_guard<std::mutex> lock(state.mu);
          if (state.gpu_page_probe.empty()) {
            state.last_error =
                (state.last_error.empty() ? "" : state.last_error + "; ") +
                "chrome://gpu 提取无响应（WebUI console 通道不可用？）";
          }
        }
      } else {
        std::lock_guard<std::mutex> lock(state.mu);
        state.last_error =
            (state.last_error.empty() ? "" : state.last_error + "; ") +
            "chrome://gpu 导航超时";
      }
    }

    // 阶段 6（--hold-ms）：保持合成循环活跃（监管 kill 演练的"渲染途中"）
    if (opt.hold_ms > 0 && exit_code == 0) {
      const auto hold_until =
          Clock::now() + std::chrono::milliseconds(opt.hold_ms);
      while (Clock::now() < hold_until) {
        browser->GetHost()->SendExternalBeginFrame();
        pump_once();
        std::this_thread::sleep_for(std::chrono::milliseconds(16));
      }
    }

    // ===== 收尾：优雅关浏览器 → 等回调 → 结果 JSON → CefShutdown
    browser->GetHost()->CloseBrowser(true);
    pump_until([&]() { return state.closed.load(); }, 5000);

    if (!opt.out_json.empty()) {
      const int ms_total = static_cast<int>(
          std::chrono::duration_cast<std::chrono::milliseconds>(
              Clock::now() - t0).count());
      std::string page_probe, gpu_probe, last_err;
      int w = 0;
      int h = 0;
      {
        std::lock_guard<std::mutex> lock(state.mu);
        page_probe = state.page_probe;
        gpu_probe = state.gpu_page_probe;
        last_err = state.last_error;
        w = state.width;
        h = state.height;
      }
      std::string js = "{\n";
      js += "  \"worker\": \"vtx_cef_worker/issue-109\",\n";
      js += "  \"cef_version\": \"" + std::string(CEF_VERSION) + "\",\n";
      js += "  \"chromium_version\": \"" +
            std::string(VTX_CHROME_VERSION_STRING) + "\",\n";
      js += "  \"mode\": \"" + opt.cef.mode + "\",\n";
      js += "  \"ozone_platform\": \"" +
            (opt.cef.mode == "a1" ? opt.cef.ozone_platform : std::string()) +
            "\",\n";
      js += "  \"url\": \"" + JsonEscape(opt.url) + "\",\n";
      js += "  \"pid\": " + std::to_string(::getpid()) + ",\n";
      js += "  \"pgid\": " + std::to_string(::getpgid(0)) + ",\n";
      // worker 启动（exec）时的显示环境（/proc/self/environ 真相；矩阵格②
      // 的 DISPLAY 由 xvfb-run 动态分配，脚本侧不可知）
      {
        auto env_it = launch_env.find("DISPLAY");
        auto wenv_it = launch_env.find("WAYLAND_DISPLAY");
        js += "  \"env\": {\"DISPLAY\": \"" +
              (env_it != launch_env.end() ? JsonEscape(env_it->second)
                                          : std::string()) +
              "\", \"WAYLAND_DISPLAY\": \"" +
              (wenv_it != launch_env.end() ? JsonEscape(wenv_it->second)
                                           : std::string()) +
              "\"},\n";
      }
      js += "  \"paint\": {\"width\": " + std::to_string(w) +
            ", \"height\": " + std::to_string(h) +
            ", \"paints\": " + std::to_string(state.paints.load()) + "},\n";
      js += "  \"png\": {\"path\": \"" + JsonEscape(opt.out_png) +
            "\", \"ok\": " + (png_ok ? "true" : "false") +
            ", \"bytes\": " + std::to_string(png_bytes) + "},\n";
      js += "  \"timing_ms\": {\"load\": " + std::to_string(ms_load) +
            ", \"first_paint\": " + std::to_string(ms_first_paint) +
            ", \"total\": " + std::to_string(ms_total) + "},\n";
      js += "  \"page_probe\": " + EmbedProbePayload(page_probe) + ",\n";
      js += "  \"gpu_page_probe\": " + EmbedProbePayload(gpu_probe) + ",\n";
      js += "  \"error\": \"" + JsonEscape(last_err) + "\",\n";
      js += "  \"exit\": \"" + exit_reason + "\",\n";
      js += "  \"exit_code\": " + std::to_string(exit_code) + "\n}\n";
      std::FILE* fh = std::fopen(opt.out_json.c_str(), "wb");
      if (fh != nullptr) {
        std::fwrite(js.data(), 1, js.size(), fh);
        std::fclose(fh);
      } else {
        std::fprintf(stderr, "vtx_cef_worker: 结果 JSON 写入失败: %s\n",
                     opt.out_json.c_str());
      }
    }
  } while (false);

  CefShutdown();
  if (owns_cache) {
    std::error_code ec;
    std::filesystem::remove_all(root_cache, ec);
  }
  return exit_code;
}
