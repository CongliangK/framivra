# gfx_cef_worker — CEF Native OSR Worker 构建系统与双模式 Worker（#108/#109/#110）

路线 A（CEF Native OSR）阶段 1（#108，构建系统与版本锁）+ 阶段 1b（#109，
CEF-01b：Linux Headless 显示环境矩阵探测、A0/A1 双模式启动与进程组自毁
守卫）+ 阶段 2a（#110，CEF-02a：OnPaint 4-Slot 共享内存环形池与定长二进制
IPC 协议）。目标：可复现地取得 CEF 二进制 → C++20 编译 `libcef_dll_wrapper` +
`vtx_cef_worker`（双模式 OSR 渲染 + 后端探针 + 监管契约 + 帧服务协议）→
产物全部隔离在本目录，绝不污染仓库打包面（`pyproject.toml` / `setup.py` /
`MANIFEST.in` / `scripts/package_smoke.py`）。不接线 `vtx/` 生产代码（后续
工单的事）。

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
| `src/frame_pipeline.{h,cpp}` | 帧原语：任意行步长逐行紧凑拷贝（防斜纹）、(0,0) 金丝雀解码、AVX2 LUT 逆预乘（与 #109 标量公式穷举一致 + 不透明快路径） |
| `src/serve.{h,cpp}` | #110 帧服务模式：模式 1（线程池逆预乘+PNG+`.tmp`+rename 原子落盘，回 128B 计时回执）/ 模式 2（先取空槽再生产；满环背压停发 BeginFrame；槽索引通知） |
| `src/shm_ring_selftest.cpp` | 原生自检（状态机/背压/步长紧凑/陈旧 run_id/SIMD 穷举/金丝雀）+ `--dump-ring` 跨语言偏移锁定（无 CEF 依赖） |
| `src/png_writer.{h,cpp}` | 最小 PNG(RGBA/8bit) 编码器（zlib），产物过 `vtx/gfx_cli.py:png_invalid_reason()`；#110 增 `EncodePngRgba`+`AtomicWriteBytes` 分步计时件（原函数行为不变） |
| `cef_client.py` | #110 Python 消费端（工单落点 `vtx/cef_client.py` 因本单边界禁改 `vtx/` 而随 worker 同目录交付；协议镜像见 `IPC_PROTOCOL.md`） |
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
build/bin/vtx_cef_worker --mode=a0 --url="file://$PWD/examples/skill-demo/gfxA.html?f=0" --out-png=/tmp/a0.png
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

# 原生自检（无 CEF 依赖，可独立跑）
experiments/gfx_cef_worker/build/bin/shm_ring_selftest
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
  Linux x86_64，stable 通道；改版本必须整包重锁（url/size/sha256/sha1 联动）。
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

## 下游熔断门提醒（#108/#109/#110 范围外，承接自工单）

#109 只保证"能起、能渲一帧、能探明环境、死得干净"；#110 保证"帧能经
定长协议 + SHM 环正确、有背压、死得干净地交付到消费端"。单帧 IPC +
PNG 编码耗时 > 18ms 或强杀出现孤儿进程即触发 K1 熔断 —— 当前 283ms/
帧全链含每帧导航与固定 250ms 落定余量（防字体/布局抖动的保守值），
稳态优化（页面状态化替代逐帧导航、settle 自适应、编码流水线、libspng/
fpng）属后续工单。
