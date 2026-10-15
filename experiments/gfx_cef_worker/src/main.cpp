// vtx_cef_worker — CEF Native OSR Worker（issue #109 CEF-01b）。
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
// --sanity 自检沿用 #108（版本锁运行时自证），行为不变。
#include <sys/prctl.h>
#include <sys/types.h>
#include <unistd.h>

#include <cerrno>
#include <signal.h>

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
#include "png_writer.h"

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
      "  --parent-alive-fd=N     监管器父存活管道读端（EOF 即组杀，监管契约）\n",
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
  return true;
}

// ---------------------------------------------------------------- 工具

// 启动环境真相：/proc/self/environ 是 exec 时刻的原始环境块，不受进程
// 运行期 setenv 污染（实测 Chromium 会在运行期自行重注入
// WAYLAND_DISPLAY=wayland-0，exit 时 getenv 不可信）。NUL 分隔逐项解析。
std::map<std::string, std::string> LaunchEnvSnapshot() {
  std::map<std::string, std::string> out;
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

// 预乘 BGRA → 直通 RGBA 的通道解预乘（PNG 语义；不透明像素恒等变换）。
inline uint8_t Unpremultiply(uint8_t c, uint8_t a) {
  if (a == 0 || a == 255) {
    return a == 0 ? 0 : c;
  }
  const uint32_t v = (static_cast<uint32_t>(c) * 255u + a / 2u) / a;
  return v > 255u ? 255u : static_cast<uint8_t>(v);
}

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
  ::prctl(PR_SET_PDEATHSIG, SIGKILL);

  // ===== CEF 子进程路径：同一可执行以 --type=* 再入（zygote/GPU/renderer/
  //        utility），必须先交 CefExecuteProcess 接管，返回值 >= 0 即子进程
  //        已走完（其 argv 是 Chromium 参数面，不进本 worker 参数解析）。
  {
    CefMainArgs subprocess_args(argc, argv);
    CefRefPtr<vtx_cef::WorkerApp> subprocess_app =
        new vtx_cef::WorkerApp(nullptr, nullptr);
    const int subprocess_exit = CefExecuteProcess(subprocess_args,
                                                  subprocess_app, nullptr);
    if (subprocess_exit >= 0) {
      return subprocess_exit;
    }
  }

  bool sanity = false;
  RunOptions opt;
  if (!ParseArgs(argc, argv, &opt, &sanity)) {
    Usage(argv[0]);
    return 2;
  }
  if (sanity) {
    return Sanity();
  }
  if (opt.url.empty()) {
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
  const std::map<std::string, std::string> launch_env = LaunchEnvSnapshot();

  // ===== CEF 初始化（工单口径：windowless + no_sandbox + 单线程消息循环
  //        + external_message_pump 外置泵）。
  CefMainArgs main_args(argc, argv);
  CefSettings settings;
  settings.no_sandbox = true;
  settings.multi_threaded_message_loop = false;
  settings.external_message_pump = true;
  settings.windowless_rendering_enabled = true;
  settings.log_severity = LOGSEVERITY_WARNING;  // 证据可读：保留警告去噪声
  CefString(&settings.root_cache_path).FromString(root_cache.string());

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
    // ===== 外置泵：OnScheduleMessagePumpWork 唤醒即时干活；空闲保底每
    //        ~8ms 干一拍（CEF 计时器任务依赖周期驱动）。
    const auto pump_once = [&]() {
      state.pump_scheduled.store(false, std::memory_order_relaxed);
      CefDoMessageLoopWork();
    };
    const auto pump_until = [&](const std::function<bool()>& done,
                                int timeout_ms) -> bool {
      const auto deadline =
          Clock::now() + std::chrono::milliseconds(timeout_ms);
      int idle = 0;
      while (!done()) {
        if (Clock::now() >= deadline) {
          return false;
        }
        if (state.pump_scheduled.load(std::memory_order_relaxed) ||
            ++idle >= 4) {
          idle = 0;
          pump_once();
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
      }
      pump_once();
      return true;
    };

    // ===== 建窗：OSR（windowless + external begin frame + 非 shared texture）
    CefRefPtr<vtx_cef::WorkerClient> client =
        new vtx_cef::WorkerClient(&opt.cef, &state);
    CefWindowInfo window_info;
    window_info.SetAsWindowless(0);
    window_info.shared_texture_enabled = false;
    window_info.external_begin_frame_enabled = true;
    CefBrowserSettings browser_settings;
    browser_settings.background_color = 0;  // 透明（gfxA 协议要求 alpha 通道）
    CefString url_str;
    url_str.FromString(opt.url);
    if (!CefBrowserHost::CreateBrowser(window_info, client, url_str,
                                       browser_settings, nullptr, nullptr)) {
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

    // 阶段 4：OnPaint 全帧 BGRA（预乘）→ 直通 RGBA → PNG
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
          for (size_t i = 0; i + 3 < state.buffer.size(); i += 4) {
            const uint8_t b = state.buffer[i];
            const uint8_t g = state.buffer[i + 1];
            const uint8_t r = state.buffer[i + 2];
            const uint8_t a = state.buffer[i + 3];
            rgba[i] = Unpremultiply(r, a);
            rgba[i + 1] = Unpremultiply(g, a);
            rgba[i + 2] = Unpremultiply(b, a);
            rgba[i + 3] = a;
          }
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
        png_ok = vtx_cef::WritePngRgba(opt.out_png, rgba.data(), w, h);
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
