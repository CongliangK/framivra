# gfx_cef_worker — CEF Native OSR Worker 构建系统与双模式 Worker（#108/#109/#110/#111/#114）

路线 A（CEF Native OSR）阶段 1（#108，构建系统与版本锁）+ 阶段 1b（#109，
CEF-01b：Linux Headless 显示环境矩阵探测、A0/A1 双模式启动与进程组自毁
守卫）+ 阶段 2a（#110，CEF-02a：OnPaint 4-Slot 共享内存环形池与定长二进制
IPC 协议）+ 阶段 2b（#111，CEF-02b：AVX2/NEON SIMD BGRA 逆预乘算子与
libspng/fpng 双编码原子落盘适配器）+ 阶段 3a（#114，CEF-03a：五阶段单调
世代状态机与 v2 双像素 Canary 原地无损还原）。目标：可复现地取得 CEF
二进制 → C++20 编译 `libcef_dll_wrapper` + `vtx_cef_worker`（双模式 OSR
渲染 + 后端探针 + 监管契约 + 帧服务协议 + 双 PNG 编码后端 + 帧同步状态
机）→ 产物全部隔离在本目录，绝不污染仓库打包面（`pyproject.toml` /
`setup.py` / `MANIFEST.in` / `scripts/package_smoke.py`）。Python 生产接线为
`vtx/gfx_cef.py`，显式 `VTX_GFX=cef` 选择实验性 Alpha（见下节）；原生二进制不随
Python wheel 分发。

## macOS arm64 构建（#202）

同一入口 `build_cef_worker.sh` 在 Darwin 选择 `cef_lock.macosarm64.json`，锁定与
Linux 同版的官方 Minimal 分发包；大小、SHA-256、官方索引 SHA-1 全部核对。
需要 Xcode Command Line Tools、Python ≥3.9、CMake ≥3.21、Ninja、curl。
构建脚本只布置用户态产物，不修改系统安全策略。arm64 产物包含 Cocoa
`NSApplication`、CEF framework 与五种 Helper app，并逐包本地 ad-hoc 签名校验。
`build/bin/vtx_cef_worker` 为启动脚本；环境变量应使用真实 bundle 内入口：
`build/bin/vtx_cef_worker.app/Contents/MacOS/vtx_cef_worker`。原生自检仍位于
`build/bin/`。macOS x86_64 尚无分发锁，明确拒绝构建。

macOS 通过已连接的 `--ipc-fd=N` 接收宿主 socketpair；原生验证 AF_UNIX/
SOCK_STREAM/已连接状态并设 close-on-exec，防止 helper 继承拖延 EOF。
Linux 继续使用 `--ipc-socket`。协议布局保持不变，Darwin POSIX SHM 的
`fstat` 分配尺寸按系统页上取整，逻辑环几何仍为原始字节尺寸。父活管道
EOF 与独立监管守卫负责父死清理；macOS 没有 Linux PDEATHSIG。macOS
browser/helper 使用 `--use-mock-keychain`，凭据仅存在 worker 进程内；渲染
profile 不访问系统登录钥匙串，以免非交互关停等待钥匙串授权。

## 生产接线（实验性 Alpha）

CEF 通过 `VTX_GFX=cef` 显式选择；未知后端值拒绝执行，默认仍为 CDP。CEF 初始化、
验签或输出失败直接使 gfx 阶段失败，**不回落 CDP/CLI**。Python 客户端在 `vtx/`，
worker 及同目录锁定 CEF 运行库须另行构建。仅平台完整进程监管可用时启动（Linux `/proc`，macOS `libproc`）；
worker 当前禁用 Chromium sandbox，**只渲染受信任本地 HTML，不处理不可信页面**。

生产 CEF 固定使用 **Mode N**：每帧导航原页面 `?f=N`，不要求页面实现
`__vtx_render` 或 `__vtx_originals`，不修改项目 HTML。原生 `--sync-canary=exact`
在同一文档中先验 TL/BR 不透明探针，再移至 TR/BL（反转帧号 token）复验；
两次捕获的非覆盖区域须逐字节一致，再交叉恢复实际遮挡像素。签名导航之前，
另一次全新导航只等待资源就绪并取得连续一致的原生画面，**从未插入见证 DOM**；
完整重建像素（含四角）必须与此 baseline 逐字节相同，否则拒绝输出、不能进入 S5。
这个 baseline 仅有否决权，不提供目标帧证明，也不贡献任何恢复或输出字节。
输出为签名捕获的 BGRA 预乘像素经现有逆预乘/PNG 链的结果，**不使用邻行像素猜测**。
页面仍须遵守帧纯函数协议；异步视觉变化、根元素特效或结构选择器（如
`body:last-child`、`html:has`）的见证副作用可能导致显式超时，不承诺任意网页兼容。
`clean` 旧实验通道的邻域兜底不用于生产。renderer 在页面 inline script 前安装
可信错误观察器，并在接受捕获像素前核对 renderer fence：未捕获异常、语法错误、
未处理 Promise 拒绝及已覆盖的错误事件抑制反例以 `JS_ERR/3` 拒绝；协议、
文档绑定或 deadline 失败以 `TIMEOUT/1` 拒绝。已处理异常与普通 console 不作错误帧。
这些有限反例不构成任意网页正确性或不可信输入安全保证；短片 fixture 也不代替完整项目验收。

缓存身份与路由权威见 [cache-provenance](../../docs/cache-provenance.md)。
原生逐事件日志、进程日志、逐帧回执保留在项目 `cache/reports/cef/`；失败亦保留。
所有运行 scratch/profile 落监管 `cache/tmp/`；Linux 长路径通过父 fd 别名访问，
macOS 使用继承的 Unix socketpair fd 连接，不依赖 `/proc`。

下例为**实际写操作**，仅在已确认允许写入且 `cache/`、`out/` 全新的项目副本执行；
不要把 `PROJECT_COPY` 指向原项目。全片验收还须检查内容门、外引 BGM 与 Blender 收尾，
不能以小 fixture 通过替代完整视频验收。没有上传步骤。

```bash
# PROJECT_COPY 由验收主控设置；worker 旁须有锁定运行库与资源文件。
env VTX_GFX=cef VTX_CEF_MODE=a0 VTX_GFX_PAGES=2 \
  VTX_GFX_CACHE_NS=cef-alpha-20261016 \
  VTX_CEF_WORKER="$PWD/experiments/gfx_cef_worker/build/bin/vtx_cef_worker" \
  python3 vtx/vtx.py -t "${PROJECT_COPY:?}/timeline.json" all
```

真实后端、全像素透明/不透明恢复、缓存补帧和短片全链 fixture：

```bash
env VTX_TEST_CEF_WORKER="$PWD/experiments/gfx_cef_worker/build/bin/vtx_cef_worker" \
  python3 -m unittest tests.test_gfx_cef_alpha -v
```

## 目录

| 文件 | 职责 |
| --- | --- |
| `cef_lock.json` | 版本锁：精确 CEF/Chromium 版本 + 官方 URL + `size_bytes`/`sha256`/`sha1_official_index` 双校验和 |
| `build_cef_worker.sh` | 唯一构建入口：校验锁 → 下载（仓外缓存 `~/.cache/cef/`）→ 解包 → CMake+Ninja → 布置运行时 → ldd+sanity 验证 |
| `CMakeLists.txt` | C++20；编译 `libcef_dll_wrapper` 静态库、`vtx_cef_worker` 与 `shm_ring_selftest`；`-O3 -march=x86-64-v3 -Wall -Wextra -Werror`；`RPATH=$ORIGIN`；链接系统 zlib（PNG 编码） |
| `src/main.cpp` | worker 入口：PDEATHSIG 首行 + `CefExecuteProcess` 子进程再入 + `--parent-alive-fd` 守卫线程（EOF→`kill(0,SIGKILL)`，仅组长）+ A0/A1 参数面（含 `--serve` 帧服务分支）+ 外置泵阶段机 + 结果 JSON（探针证据/计时/启动环境快照） |
| `src/app_handler.{h,cpp}` | `WorkerApp`（OnBeforeCommandLineProcessing 按 A0/A1 注入开关 + OnScheduleMessagePumpWork）与 `WorkerClient`（OSR OnPaint 全帧 BGRA→RGBA、#110 serve 捕获路径、GetViewRect、console marker 探针通道）+ 共享装配（MakeWorkerSettings/CreateOsrBrowser/PumpDriver） |
| `src/ipc_protocol.hpp` | #110 定长二进制协议：工单冻结 `FrameSlotHeader`（偏移静态断言=线上规范）+ 64B `FrameRequest` + 32B `SlotNotice` + 64B `HelloInfo`（SCM_RIGHTS 随带 SHM fd） |
| `src/shm_ring.hpp` | #110 4-Slot POSIX SHM 环：`shm_open`→`ftruncate`→`mmap`→**立即 `shm_unlink` 零残留**；EMPTY/WRITING/READY 门（acquire/release 跨进程同步）；满环背压等待入账 |
| `src/frame_pipeline.{h,cpp}` | 帧原语：任意行步长逐行紧凑拷贝（防斜纹）、(0,0) 金丝雀解码；#111 起逆预乘迁至 pixel_ops（本文件保留 byte_len 口径兼容包装） |
| `src/pixel_ops.{h,cpp}` | #111 正式算子 `unpremultiply_bgra_to_rgba`：65,536 项精确 LUT + AVX2 单遍混合式（块级不透明 shuffle 快路径 / gather 通用路径）+ NEON 结构交换（aarch64 编译期门控）+ 标量兜底；与 #109 标量公式逐值一致（selftest 全域穷举） |
| `src/serve.{h,cpp}` | #110 帧服务模式：模式 1（线程池逆预乘+双后端 PNG+`.tmp.<pid>.<tid>`+rename 原子落盘，回 128B 计时回执）/ 模式 2（先取空槽再生产；满环背压停发 BeginFrame；槽索引通知） |
| `src/shm_ring_selftest.cpp` | 原生自检（状态机/背压/步长紧凑/陈旧 run_id/SIMD 穷举/金丝雀）+ `--dump-ring` 跨语言偏移锁定（无 CEF 依赖） |
| `src/png_writer.{h,cpp}` | #111 双后端 PNG(RGBA/8bit) 编码：`--png-encoder=spng\|fpng\|zlib` × `--png-level=1..3` × `--png-filter=none\|sub\|up\|sub_up\|all`（默认 spng L1 SUB\|UP=工单口径）；原子落盘 `AtomicWriteBytes`（fwrite+fflush+fclose 全零校验 + rename；异常 unlink） |
| `src/pixel_ops_selftest.cpp` | #111 原生自检与基准：(c,a)∈[0,255]² 全域穷举（LUT/SIMD 双分支）+ 双编码器往返/互证 + 原子写语义 + `--bench`/`--bench-json` 1080p 性能证据面（无 CEF 依赖） |
| `third_party/spng/`、`third_party/fpng/` | #111 内嵌第三方源码：libspng v0.7.3（`spng.c`+`spng.h`，BSD-2-Clause）与 fpng（`fpng.cpp`+`fpng.h`，Unlicense）单文件上游源，随 git 入库 |
| `third_party_locks.json` | #111 第三方源码版本锁：精确版本/来源 URL/size/sha256 哈希锚定（CEF 锁同纪律；改版本必须整包重锁） |
| `src/frame_sync_machine.hpp` | #114 五阶段单调世代状态机 `FrameSyncStateMachine`：`(document_gen, request_id, target_frame_index)` 三元组 + S0→S5 严格序 + v2 双像素 Canary 闸门（早期丢弃/陈旧与半更新拒收/精确匹配接受+原地恢复）+ JSONL 全事件面（无 CEF 依赖） |
| `src/frame_sync_machine.cpp` | #114 状态机实现：vtx/frame_canary.py 冻结公式的 C++ 消费镜像（P1 xor 0x5A / P2 加性 0x3C×(16,22) / α=254 / 预乘精确反像）；自检与 serve 共用同一判定核心 |
| `src/frame_sync_selftest.cpp` | #114 原生对抗自检：旧 paint/跨世代同帧/重复请求/签名半更新/超时/早期 paint/尺寸不符/晚到回调等确定性 fixture（无 CEF 依赖；`--jsonl-out` 供 Python 跨语言校验） |
| `pages/frame_sync_v2.html` | #114 状态机验收页：透明底 canvas（f 纯函数）+ `?f=`（Mode N）+ `__vtx_render(f)`（Mode P）+ `__vtx_originals()`（canary 位原像素上报钩子） |
| `cef_client.py` | 兼容导入；唯一 Python 协议客户端为 `../../vtx/cef_client.py`（协议镜像见 `IPC_PROTOCOL.md`） |
| `pages/frame_canary.html` | #110 金丝雀帧页：f 纯函数（(0,0) 像素编码帧号 + 硬边条纹防步长错位） |
| `IPC_PROTOCOL.md` | #110 协议权威文档（环布局/偏移表/状态机/双模式时序/故障注入面/工单偏差记录） |
| `probe_display_matrix.py` | 三态显示环境 × A0/A1 × ozone 矩阵探测（#109）：每格独立 worker 跑 `gfxA.html?f=0` 出 PNG + 内部探针（WEBGL_debug_renderer_info + chrome://gpu）→ 归类生效后端 → 落 `env_manifest.json` |

构建产物（均不入库，`.gitignore` 在册）：

- `build/` — CMake/Ninja 构建树；`build/bin/vtx_cef_worker` 为最终可执行。
- `cef_binary*/` — 解包出的 CEF 官方分发包（数百 MB，含 `libcef.so`）。

## 用法

```bash
bash experiments/gfx_cef_worker/build_cef_worker.sh
# 网络受限时：CEF_DL_PROXY=http://127.0.0.1:7890 bash ...（先直连，失败再挂代理）

# 独立渲染一帧（#109 DoD①：PNG 过 png_invalid_reason）
build/bin/vtx_cef_worker --mode=a0 --url="file://$PWD/templates/skill-demo/gfxA.html?f=0" --out-png=/tmp/a0.png
build/bin/vtx_cef_worker --mode=a1 --ozone-platform=headless --url=... --out-png=/tmp/a1.png --probe-gpu-page --out-json=/tmp/a1.json

# 三态显示环境矩阵探测（证据默认落 /tmp/framivra-evidence/issue-109/matrix/）
python3 experiments/gfx_cef_worker/probe_display_matrix.py [--xvfb-prefix /tmp/xvfb-dl/root]

# 监管 DoD 验收（kill -9 宿主 → 100ms 树归零 → reap 残留=0）
VTX_TEST_CEF_WORKER=$PWD/experiments/gfx_cef_worker/build/bin/vtx_cef_worker \
  python3 -m unittest tests.test_cef_worker_supervision -v

# #110 帧服务 DoD 验收（4-Slot SHM 环 + 定长协议：原生自检/逐字节一致/
# 故障注入三连/监管 kill 零残留；同门控变量）
VTX_TEST_CEF_WORKER=$PWD/experiments/gfx_cef_worker/build/bin/vtx_cef_worker \
  python3 -m unittest tests.test_cef_worker_shm -v

# #111 算子/双编码器 DoD 验收（全域穷举/双后端 Tier-1 互证/崩溃窗口/
# 性能证据采集；同门控变量，pixel_ops_selftest 须在同目录）
VTX_TEST_CEF_WORKER=$PWD/experiments/gfx_cef_worker/build/bin/vtx_cef_worker \
  python3 -m unittest tests.test_cef_worker_pixel_ops -v

# #114 帧同步状态机 DoD 验收（原生对抗自检 + 真实 CEF Mode N/P +
# 全故障注入 + JSONL 跨语言校验；frame_sync_selftest 须在同目录）
VTX_TEST_CEF_WORKER=$PWD/experiments/gfx_cef_worker/build/bin/vtx_cef_worker \
  python3 -m unittest tests.test_cef_frame_sync -v

# 原生自检（无 CEF 依赖，可独立跑）
experiments/gfx_cef_worker/build/bin/shm_ring_selftest
experiments/gfx_cef_worker/build/bin/pixel_ops_selftest [--bench|--bench-json]
experiments/gfx_cef_worker/build/bin/frame_sync_selftest [--jsonl-out=<path>]
```

worker 常用参数：`--mode=a0|a1`（A0=Software OSR：`--disable-gpu
--disable-gpu-compositing --disable-gpu-vsync --hide-scrollbars
--force-color-profile=srgb`；A1=GPU 合成→CPU Buffer：`--enable-gpu-
rasterization --ignore-gpu-blocklist --disable-gpu-vsync
--hide-scrollbars --force-color-profile=srgb` + `--ozone-platform=
headless|x11|wayland`）、`--root-cache-path=`（监管器运行目录，缺省临时
目录退出即删）、`--hold-ms=`（出帧后保活渲染，供监管 kill 演练）、
`--parent-alive-fd=`（#103 监管契约，EOF 即组杀全树）。A0/A1 均额外注入
`--enable-unsafe-swiftshader`（工单外追加，偏差说明：Chromium 131+ 默认禁
软件 WebGL，不解锁则探针在软件路径无后端证据可采；渲染路径不受影响）。

要求：g++ ≥ 11（C++20）、CMake ≥ 3.21、Ninja、python3、curl。产物自校验：

```bash
experiments/gfx_cef_worker/build/bin/vtx_cef_worker --sanity
# SANITY OK ... —— 编译期宏与 libcef.so 运行时版本逐项比对全部一致才退出 0
```

入口脚本不接收构建选项：未知参数以退出码 2 拒绝（`-h`/`--help` 查看用法）。

## 版本锁策略

- 锁定对象 = 官方 **Minimal Distribution**（`cef-builds.spotifycdn.com`），
  Linux x86_64（`cef_lock.json`）与 macOS arm64（`cef_lock.macosarm64.json`），
  stable 通道；改版本必须整包重锁（url/size/sha256/sha1 联动）。
- `sha256` 为主校验（下载后强制比对）；`sha1_official_index` 与官方索引
  交叉校验。注意两个校验和同记于锁文件，对同一分发包同步篡改即可自洽，
  单靠锁内字段防不住假锁——实际防线是官方索引交叉核对 + git 对锁变更的
  审计。
- 解包复用强制版本核对：已解包的 `cef_binary*/` 目录名必须含锁
  `cef_version` 精确分量（`cef_binary_{cef_version}`）才可复用；存在版本
  不匹配的解包目录时拒绝构建（exit 1，提示清理或重锁对齐），防重锁/多版本
  累积后静默编错版本。
- Chromium 大版本对齐宿主 Chrome（消除跨大版本 Blink 排版差异）。锁定时的
  对齐决策与查证记录见 `cef_lock.json` 的 `alignment_note`。
- 二进制永不入库：tarball 缓存在 `~/.cache/cef/`（可用 `CEF_CACHE_DIR`
  改道），解包物由 `.gitignore` 的 `cef_binary*/` 规则隔离。
- #111 第三方**源码**（libspng/fpng 单文件）随 git 入库（上游即单文件
  发布形态，构建零网络依赖）；版本/URL/sha256 锚定于
  `third_party_locks.json`（同 CEF 锁纪律：改版本必须整包重锁）。
- 运行时布置以分发包实际清单为准（= CEF 官方 `CEF_BINARY_FILES` /
  `CEF_RESOURCE_FILES`：libcef.so、libvk_swiftshader.so、libvulkan.so.1、
  v8_context_snapshot.bin、vk_swiftshader_icd.json、chrome-sandbox、
  icudtl.dat、*.pak、locales/）。工单提到的 `libEGL.so`/`libGLESv2.so`
  在 CEF Linux 分发包中不存在（CEF 非 Windows 不交付 ANGLE 库），构建
  脚本按"存在才拷"处理，`RPATH=$ORIGIN` 语义不变。

## 三态显示环境矩阵实测结论（#109，目标机 RTX 4080 + GNOME Wayland 会话）

`probe_display_matrix.py` 全矩阵实测（证据：`matrix_summary.md`，各格
`result.json`/`env_manifest.json`/`out.png`/`worker.log`）：

| 显示环境 | 模式 | 生效后端（内部探针实测） |
| --- | --- | --- |
| ① `env -u DISPLAY -u WAYLAND_DISPLAY` | A0 / A1-headless | `swiftshader`（纯无头 → SwiftShader 回退） |
| ① 纯无头 | A1-x11 | 启动失败（ozone x11 无 X server，~60ms 快速失败） |
| ② `xvfb-run -a` | A0 | `swiftshader`（A1-headless 在本机归入下行泄漏场景，实测 `nvidia_egl`） |
| ② Xvfb | A1-x11 | `software_skia`（**llvmpipe 陷阱**：Xvfb 无 NVIDIA GLX/EGL） |
| ② Xvfb（WAYLAND_DISPLAY 泄漏在场） | A1-headless/wayland | `nvidia_egl`（ANGLE 经真 wayland-0 上 NVIDIA） |
| ③ 真实 `DISPLAY=:0`（XWayland） | A1-x11 | `nvidia_egl`（RTX 4080 硬件合成） |
| ③ 原生 Wayland | A1-wayland | `nvidia_egl` |

结论：**激活 NVIDIA EGL 硬件合成的组合 = 真实 X11 `DISPLAY=:0` 或真实
`WAYLAND_DISPLAY`（A1 对应 ozone x11/wayland）**；纯无头与 Xvfb+x11 均退
化为软件（SwiftShader/llvmpipe）。注意 `xvfb-run` 不剥 `WAYLAND_DISPLAY`
——网格里的 ②-a1-headless 拿到 NVIDIA 是 wayland-0 泄漏所致（worker 以
`/proc/self/environ` 记录 exec 时刻启动环境佐证；Chromium 运行期会重注入
`WAYLAND_DISPLAY`，exit 时 getenv 不可信）。

## 帧服务模式与 4-Slot SHM 环（#110 CEF-02a）

协议权威：`IPC_PROTOCOL.md`（布局/偏移表/状态机/时序/故障注入面/工单偏差
记录）。核心设计：

- **零残留**：`shm_open(O_EXCL)` → `mmap` → **立即 `shm_unlink`**，SHM
  实体仅由 fd 维系（消费端经 Unix Socket SCM_RIGHTS 收同一句柄零拷贝
  mmap）；任何进程死亡（含 SIGKILL/abort）由内核回收 —— `/dev/shm`
  任何时刻零 `vtx_shm_*` 残留，不依赖退出/信号处理器。
- **双模式**：模式 1 = Worker 线程池原地落盘 PNG（`.tmp`+rename 原子
  提交），仅回 128B 计时回执；模式 2 = 先取空槽再生产，4 槽全满即背压
  （暂停发起下一帧 BeginFrame，`backpressure_wait_us` 随通知回传并累计
  入环控制块，超时明确 TIMEOUT 不死等）。
- **帧正确性**：OnPaint 任意行步长逐行紧凑拷入槽（杜绝跨行斜纹）+
  (0,0) 金丝雀像素核实帧号（不符即拒绝计数）+ 槽头 run_id 跨 run 残帧
  拒收；SIMD 逆预乘与 #109 标量公式 (c,a) 全组合穷举一致。

验收（门控测试 `tests/test_cef_worker_shm.py`，`VTX_TEST_CEF_WORKER` 同
#109）：槽读出 == `.raw` 直写 == #109 一次性 PNG 解码帧逐字节一致
（A0；A1 同帧证据加分项）；DoD 故障注入三连（尺寸不符→SIZE_MISMATCH、
消费端阻塞 5s→背压 4.97s 计时入账 + RSS 增量 4.7MB≪64MB 预算、
worker abort()→消费端明确 EOF/RST 错误 + 树归零 + 零残留）；监管
kill -9 宿主→105ms 树归零 + reap 后临时目录零残留。

实测（RTX 4080 机器，2026-10-15）：模式 2 全链槽往返（含每帧导航 +
250ms 落定余量 + 金丝雀核实 + 发布/释放）avg ≈ 283ms/帧；纯传输面
（槽发布→通知→mmap 读→门释放，不含渲染）为微秒级——K1 熔断门的
18ms 单帧预算约束的是稳态帧管线（settle 自适应/编码流水化属下游工单）。

## 双 PNG 编码后端与 SIMD 逆预乘（#111 CEF-02b）

`--png-encoder=spng|fpng|zlib`（默认 `spng`，工单推荐）× `--png-level=1..3`
（仅 spng 生效；3 对齐 `VTX_GFX_PNG_LEVEL=3`）× `--png-filter=none|sub|up|
sub_up|all`（仅 spng 生效；默认 `sub_up`=工单口径 `SPNG_FILTER_CHOICE =
SPNG_FILTER_CHOICE_SUB | SPNG_FILTER_CHOICE_UP`）。模式 1 落盘与一次性
`--out-png` 共用同一编码面；`t_png_encode_us` 回执字段语义不变。

- **spng**：内嵌 libspng v0.7.3（third_party/spng/，锁 third_party_locks.json）。
  编码入口 `spng_encode_image(SPNG_FMT_PNG)`——v0.7.3 编码面仅接受
  `SPNG_FMT_PNG`/`SPNG_FMT_RAW`（无 `SPNG_FMT_RGBA8` 编码支持），8-bit
  RGBA 输入布局与 `SPNG_FMT_PNG` 逐字节一致，工单语义完整达成（偏差
  记录见 IPC_PROTOCOL.md §0）。
- **fpng**：内嵌 fpng（third_party/fpng/）；启动时 `fpng::fpng_init()`
  开启 SSE4.1/PCLMUL 硬件 CRC32/Adler32；无压缩级别/滤波概念（flags=0）。
  产物为标准 PNG（私有 `fdEC` ancillary 块置 IDAT 前，标准解码器按规范
  跳过；实测过 `png_invalid_reason` 与 PIL/libspng 解码）。
- **zlib**：#109/#110 遗留最小编码器（filter-0 + `Z_DEFAULT_COMPRESSION`），
  字节口径与 #110 产物一致，保留作基线对照与回落面。
- **原子落盘**：`AtomicWriteBytes` —— 同目录 `<final>.tmp.<pid>.<tid>` →
  `fwrite`+`fflush`+`fclose` 返回值全零校验 → `rename()` 原子替换；异常
  路径 `unlink` 临时件。写者被 SIGKILL 的崩溃窗口内：目标 `f*.png` 恒为
  完整件或不存在（绝无半件），可能残留的 `*.tmp.<pid>.*` 为可辨识可清扫
  模式（tests/test_cef_worker_pixel_ops.py::AtomicWriteKillWindow 实证）。
- **正确性**：逆预乘算子与 #109 标量公式 (c,a)∈[0,255]² **全域穷举**
  零失配（LUT 与 SIMD 双分支分别穷举）；双编码器产物解码回 RGBA 与输入
  逐字节一致，spng/fpng 两路像素 Tier-1 全等（#104 `analyze_frame_diff`）。

### 性能实测（1080p，7950X 单核中位数，2026-10-15）

逆预乘（AVX2）：不透明帧 0.36-0.43ms（≈19-23GB/s；标量参考 6.1-6.6ms，
**15-17×**）；混合 alpha 帧 1.9ms（3.9×）。工单「全不透明页 <0.3ms」
在 0.36-0.43ms 量级达成率未满（差距 <50%，见遗留风险）。

单帧管线（unpremul + encode）与 4 线程并发编码吞吐：

| 内容类 | spng L1 sub_up（默认） | spng L1 none | fpng | 4线程 spng sub_up | 4线程 spng none | 4线程 fpng |
| --- | --- | --- | --- | --- | --- | --- |
| 全平色 | 13.0ms | 1.3ms | 1.7ms | 303fps | 3147fps | 1156fps |
| 条纹页（canary 同族） | 13.1ms | 1.5ms | 2.0ms | 301fps | 2969fps | 1258fps |
| 径向渐变（fpng 弱项） | 22.6ms | 9.6ms | 5.2ms | 173fps | 412fps | 674fps |
| LCG 噪声（上界） | 96.5ms | 84.7ms | 5.4ms | 40fps | 46fps | 673fps |

**工单 DoD 冲突显形**：工单同时要求（a）默认 `SPNG_FILTER_CHOICE =
SUB|UP` 与（b）`unpremultiply + spng(level=1)` 单帧单核 ≤8ms、4 线程
≥300fps。实测 libspng v0.7.3 的 SUB|UP **行滤波选择**（每行对候选滤波
求和启发式）在本机使编码耗时 3-14×于禁滤波（见上表），两要求在渐变/
噪声类内容上不可同时成立。本实现以工单口径为默认（sub_up），提供
`--png-filter=none` 逃生档（全平/条纹类 1.3-1.5ms 满足 ≤8ms 与 ≥300fps；
渐变类 9.6ms/412fps 仍越 8ms 单核线但过 300fps 线）；fpng 全内容类满足
两线（≤5.4ms、≥673fps）但文件体积在渐变类为 spng 的 2.4×（1476KB vs
611KB——正是工单背景所述矛盾）。编码后端选型归 #113 桥接工单/产线调参
决策，本单以可切换 + 全数字交付。

## 下游熔断门提醒（#108-#111 范围外，承接自工单）

#109 只保证"能起、能渲一帧、能探明环境、死得干净"；#110 保证"帧能经
定长协议 + SHM 环正确、有背压、死得干净地交付到消费端"；#111 保证
"逆预乘算子确定性等价 + 双 PNG 编码可切换且产物全过校验 + 落盘原子"。
单帧 IPC + PNG 编码耗时 > 18ms 或强杀出现孤儿进程即触发 K1 熔断 ——
当前 283ms/帧全链含每帧导航与固定 250ms 落定余量（防字体/布局抖动的
保守值），稳态优化（页面状态化替代逐帧导航、settle 自适应、编码流水线）
属后续工单；编码面（libspng/fpng 双后端 + `--png-filter`）已由 #111 交付。

## 五阶段帧同步状态机与 v2 双像素 Canary（#114 CEF-03a）

正面解决 CEF Issue #4166 的两大死穴：① 页面 JS Ready 后首个 `OnPaint`
可能是旧帧或半合成中间帧；② 静态画面 Viz 短路不触发 `OnPaint`。**不靠
固定 `sleep(40ms)` 碰运气** —— #110 的 250ms 固定 settle 在 `--sync`
路径下被就绪信号驱动完全替代（实测 Mode N 全链 ~90ms/帧 vs 旧路径
283ms/帧，见下方实测）。核心组件：

- **`FrameSyncStateMachine`**（`src/frame_sync_machine.{hpp,cpp}`，无 CEF
  依赖）：三元组 `(document_gen, request_id, target_frame_index)` + 五个
  严格有序状态 `S0_IDLE → S1_NAV_OR_EVAL_ISSUED → S2_ASSETS_FONTS_READY
  → S3_JS_FRAME_APPLIED_AND_CANARY_SET → S4_COMPOSITOR_PUMPING →
  S5_ONPAINT_VERIFIED`。非法转移一律拒绝并计数（严格单调性）。
- **注入桥 JS**（serve 内建，`BuildSyncBridgeJs`）：S2 就绪收集
  （`readyState==='complete'` + `document.fonts.ready` + 全部 `<img>`
  `decode()`）→ 帧状态施加（Mode P `__vtx_render(N)`；Mode N 由 `?f=`
  导航完成）→ **rAF → rAF → MessageChannel task**（只作就绪候选，不构成
  提交完成证明）→ v2 双像素 Canary（Clean：P1@(0,0)+P2@(1,0) 同扫描线，
  rgba(·,254/255)；Diagnostic：双对角 8×8）→ `console.debug
  ("__VTX_READY__:"+json)` 通知宿主（三元组随装配注入，晚到/异世代回调
  在宿主侧拒收计数）。
- **S4 外部帧泵**：`Invalidate(PET_VIEW)` + `SendExternalBeginFrame()` +
  `CefDoMessageLoopWork()`，1ms 轮询间隔，单帧硬超时 5000ms（工单冻结值，
  `--sync-pump-timeout-ms` 可调）。S1–S3 期间同样泵送（推进 rAF）——期间
  到达的 OnPaint 全部早期丢弃（`early_paint_dropped`）。
- **S5 验签闸门**（`FeedPaint`）：尺寸卫兵 → Clean 双像素与期望帧预乘
  签名**逐字节全等**才匹配（对齐 `vtx/frame_canary.py` 的
  `accept_paint_bgra_inplace` 口径；Diagnostic 走双角 8×8 uniform 解码）。
  陈旧帧拒收并归因（双像素候选交集单例 → `stale_paint:rendered=N`）、
  半更新签名拒收（P1 新 P2 旧 → `half_signature`）、无签名/歧义拒收
  （宁拒不猜）。**接受后原地恢复两个与请求绑定且已验证的原像素**
  （canary_set 事件页面上报、经同帧验签绑定世代；无上报时行 1 邻域兜底
  显形 `restore_source="neighbor_row1"` —— 仅 fixture 证明等值时成立，
  不作通用恢复法），随后推入 #110 编码落盘队列。
- **世代隔离**（帧号签名不编码 document_gen/request_id，故由宿主侧机制
  承担并有证据）：① S4 前早期围栏（旧世代字节重放即丢弃）；② 请求间
  S0 缴械（无在途请求时 paint 一律丢弃）；③ 晚到就绪回调三元组核对
  拒收（`late_ready_cb_rejected`）；④ S4 泵送归属构造性绑定。残余理论
  窗口（旧世代同帧号 paint 恰落在新请求 S4 中段）要求旧文档在已失去
  compositing 归属后仍产出新帧 —— CEF 导航 commit 语义下不可达；同帧号
  重请求的逐字节一致性由 f 纯函数页面保证（tests 实证）。
- **JSONL 全事件日志**（`--sync-log=<path>`，无缓冲落盘）：`request_begin/
  state_transition/paint/ready_cb_rejected/duplicate_request_rejected/
  request_end` 逐事件携带 `document_gen/request_id/target_frame_index`、
  `rejected_stale_paint_count`（请求作用域）、`accepted_elapsed_us_since_
  ready`（S3→S5）等字段 —— 工单 DoD「清晰观测每一帧从 S3 到 S5 经历的
  拒收计数与接受时延」的数据面。

驱动模式与故障注入（`--sync` 系列参数，见 `-h`）：

```bash
# Mode N（逐帧导航 ?f=N，默认）
vtx_cef_worker --mode=a0 --serve --ipc-socket=<sock> \
  --url-template="file://…/frame_sync_v2.html?f={f}" \
  --sync --sync-mode=n --sync-log=<path>.jsonl
# Mode P（常驻页 __vtx_render(N)；url_template 为固定页地址）
vtx_cef_worker … --sync --sync-mode=p --url-template="file://…/frame_sync_v2.html"
# 对抗注入：stale_canary（恒画 f-1）/no_canary/no_ready/early_burst
vtx_cef_worker … --sync --sync-fault=stale_canary --sync-pump-timeout-ms=3000
```

验收（门控测试 `tests/test_cef_frame_sync.py`，`VTX_TEST_CEF_WORKER` 同
#109；#115 契约：不使用 MEDIA_CONDITIONS 保留字）：

- **原生对抗自检** `frame_sync_selftest`（108 项断言全 PASS）：旧 paint、
  跨世代同帧（旧世代字节在 S1 窗口重放被围栏丢弃 + 旧回调拒收）、重复
  请求、签名半更新（P1新P2旧/P1旧P2新）、泵送超时、尺寸不符、早期/
  晚到 paint、非法转移、公式已知值向量（f=0/0xFFFF 预乘字节）、双像素
  联合解码全域探针单例闭环（含 P1 孪生帧 23167/23168）、恢复 ≠ 邻域
  复制证明（上报原像素 ≠ 行 1 底纹时恢复取上报值）。
- **真实 CEF（A0 软件路径实测，2026-10-15，RTX 4080 机器）**：
  - Mode N 帧序列 `[0, 450, 899, 0(跨世代同帧)]`：全部 S0→S5 严格序、
    receipt `canary_frame_index` == 解码结果、交付 PNG `(0,0)/(0,1)` ==
    恢复后的透明真值（canary 字节不在）、跨世代同帧两次交付逐字节一致；
  - Mode P 帧序列 `[0, 450, 12, 12, 899]`：常驻页 `document_gen` 恒 1，
    请求 2（目标 450）的 S4 拒收归因 `stale_paint:rendered=0` ——
    **真实 CEF #4166 旧 canary 尾随 Paint 被捕获并归因的证据**；
  - 故障注入：`stale_canary` → S4 持续拒收 2700+ 次（rendered=f-1）后
    5000ms 硬超时（TIMEOUT 回执 + JSONL `pump_timeout`）；`no_ready` →
    S1→S3 超时（无 settle 碰运气）；重复 request_id → status=4
    DUPLICATE；`size_mismatch` → status=2 + 实测 1920x1079 显形；
  - A1（GPU 合成→CPU Buffer，ozone=headless）：状态机口径同构
    （严格序/解码 canary/恢复源=reported_original）。
- **性能**：Mode N 全链（导航→S2/S3 就绪→S4 泵送→验签→恢复→spng PNG
  落盘）实测 ~90ms/帧（旧路径 283ms/帧，固定 settle 消除）；Mode P
  常驻页 ~50ms/帧。静态画面饥饿问题在本通路未复现（Invalidate+
  SendExternalBeginFrame 每毫秒泵送强制重合成，每泵必有 OnPaint）——
  该结论仅对本实测环境成立，S6 高压对抗（2000 帧/120 帧静态跨度）属
  #115 裁决范围。

遗留边界（诚实清单）：诊断轨 8×8 验签为 uniform 块解码（块内撕裂带
拒收），跨区域对角撕裂的全画幅裁决（TL vs BR 双角帧号比对）已实现归因
方向但高压统计口径归 #115；receipt 128B 冻结结构无 `early_paint_dropped`
字段位，该计数仅入 JSONL（DoD 口径即 JSONL）。生产 Alpha 的无钩子帧驱动
DOM 恢复走上方 `exact` 双位置原生捕获；旧 `clean` 模式仍不支持通用原像素获取。
跨引擎光栅等价、完整 voice-ime 成片和长时高压统计须由各自验收证明，不能从
短片 fixture 或旧实验帧率数据推导。
