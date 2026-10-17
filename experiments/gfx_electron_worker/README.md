# gfx_electron_worker — Electron OSR 最小宿主（#112 / ELEC-01，路线 B 跟踪锚点）

> **[🔴 性能不合格 / 路线不采用](../../docs/DEV.md#electron-route-status)**：路线决策与性能依据见该唯一状态记录；以下保留实现与复现说明。

GFX 离屏渲染双路线战役**路线 B（Electron Offscreen Rendering）**起点交付物：以
最小 JS/Node 代码量验证 Electron OSR 的无头可运行性——离屏加载一页 gfx 帧
（`gfxA.html?f=0`）→ 捕获首帧 PNG → 采集渲染环境真值（`process.versions` 三元组 +
GPU 内部接口实测）→ 干净退出；严格锁定 Electron 版本；B0/B1 双模式独立进程启动；
并接入 `vtx/proc_supervisor.py`（#103）的进程组卫生体系。此最小探针本身**不接线 `vtx/` 生产
gfx 管线**（导航接线见下节）；下游熔断门 K1（单帧 IPC + PNG 编码 > 18ms 熔断）
的测量也不在本目录范围。

## Alpha 导航与管线接线（复现入口）

入口为 [gfx_electron.py](../../vtx/gfx_electron.py) 的
`render_page(html_path, outdir, n_frames, *, width=1920, height=1080, frames=None, ctx=None, timeout_s=15, electron_path=None, mode="b0", output="electron_native_topng", progress=None)`。
`ctx` 必填，以现有监管器分配项目内 profile/账本并回收进程组。输出
`f{frame:04d}.png`；结果含 `failed`、`path_counts`、requested/effective backend、
run_id、退出码与逐次证据目录。**调用方必须把非空 failed 当硬失败**；不读取旧帧作成功证据，
不静默回落。公共入口显式 `VTX_GFX=electron`；未知值硬失败，默认仍为 CDP，
CDP/CLI 保留自身历史回落规则。配置由 [gfx_backend.py](../../vtx/gfx_backend.py) 校验：
`VTX_ELECTRON_BIN` 指向可执行文件（默认本目录忽略区 node_modules 的 Electron），
`VTX_ELECTRON_MODE=b0`、`VTX_ELECTRON_OUTPUT=electron_native_topng` 是本实现唯一支持组合；
`VTX_ELECTRON_TIMEOUT` 为每帧秒数（默认 15）。B1/GPU/shared 路由不在本接线支持范围。
`VTX_GFX_CACHE_NS` 默认 `electron_b0`，可显式隔离验收批次；缓存身份机制见
[缓存与回执](../../docs/cache-provenance.md)。`VTX_GFX_SPLIT_FRAMES/SPLIT_MAX` 沿用分块规则，
`VTX_GFX_PAGES` 控制 Electron 块并行数（默认 1）；每 worker 单帧串行。
原 HTML、`?f=` 与 timeline 无需改写。空闲无需常驻服务；需可用的本地图形显示环境。

[导航 worker](navigation_worker.mjs) 仅实现 Mode N：每帧全新 WebContents
真实导航原 HTML 的 `?f=`，不依赖页面 opt-in API。页面世界无 Node/IPC/bridge；
宿主在隔离世界检查 URL/尺寸、等待 load、fonts、image decode 及真实请求静默，
再插入闭合 shadow canvas 的随机 nonce 标记。A/B/A/B 两个互斥位置的真实 OSR
paint 逐次验证 nonce，排除延迟 paint；同位置重复及非遮挡全图字节相等后，
从另一张已验证 compositor 图恢复原像素。另以插入标记前的无标记 snapshot
做全图否决门，防 DOM 标记影响 `:last-child` 等页面选择器；该 snapshot
**绝不作恢复源或目标帧绑定证据**。静态页以 invalidate 防饥饿。
输出通过 createFromBitmap→toPNG，并对**整张 decoded RGBA**与原生 witness PNG
交叉恢复结果逐字节对比，拒绝 alpha/颜色重建偏差；不猜邻行、不信 originals。

仅 `electron_native_topng` 为本适配支持的输出，`bitmap_shared_encoder` 显式拒绝。
本入口不依赖旧输出桥、原生助手或共享内存编码器；这些未准入新增件不在累计净 diff 中。
B0 是默认软件输出；B1 选项仅供独立探针复现。资源有界为单帧串行、
固定视口上限、请求数量上限、每帧硬超时；不用 SHM/助手队列。临时 PNG 同目录
独占创建、fsync 后 rename，失败无新半帧。覆盖不到恶意页面的任意未来延迟脚本，
四张见证是稳定性观测门而非任意 JavaScript 确定性的数学证明；不稳定/被遮挡页面保守拒绝。

[专用测试](test_navigation.py) 默认仅运行契约/像素单元测试；真实测试显式 opt-in。
下列命令**会写测试证据**（不渲视频、不访问原项目输出）；可设置
`VTX_ELECTRON_EVIDENCE_DIR` 将完整 fixture/PNG/log/receipt 留在检出外：

```bash
env VTX_TEST_ELECTRON=1 python3 -m unittest tests.test_electron_integration experiments.gfx_electron_worker.test_navigation experiments.gfx_electron_worker.test_page_errors experiments.gfx_electron_worker.test_promise_checkpoints -v
```

公共全链命令预演（零写态；真实全片执行须由主控授权指定验收副本、先隔离旧 cache/out，
然后显式去掉 `--dry-run`；全片验收记录见上述状态记录所指 PR）：

```bash
env VTX_GFX=electron VTX_ELECTRON_MODE=b0 VTX_GFX_CACHE_NS=electron_alpha_fresh \
    VTX_GFX_PAGES=2 python3 vtx/vtx.py -t /path/to/authorized-copy/timeline.json --dry-run all
```

## 目录

| 文件 | 职责 |
| --- | --- |
| `package.json` | 精确版本锁：`electron: 44.7.2`（无 `^`/`~`）；`type: module` |
| `package-lock.json` | npm 锁文件（`npm ci` 可复现安装的依据） |
| `main.mjs` | Electron 主进程最小宿主：B0/B1 双模式、offscreen 硬化、首帧捕获、GPU/版本真值采集、`--parent-alive-fd` 自毁守听 |
| `preload.cjs` | 隔离世界 preload（sandbox+contextIsolation）：页面 load 后回传自证（`f` 参数、`devicePixelRatio`） |
| `run_gfx_electron_worker.py` | vtx 宿主驱动：`SupervisedProcessGroup` 拉起 Electron → 首帧核验 → `vtx.env_manifest` 生成 `env_manifest.json` |
| `drill_sigkill.py` | kill -9 强杀宿主卫生演练（DoD；只杀自起进程） |

`node_modules/`（约 283MB，含 Electron 二进制）不入库（`.gitignore` 在册）。

## 版本锁策略

- **锁定 `electron@44.7.2`（精确，禁 `^`/`~`）**，Chromium **152.0.7977.149**、
  Node 24.21.0（本机 probe 实测）、V8 15.2.124.37-electron.0——2026-10-15 查证
  `releases.electronjs.org/releases.json`：44.7.2 为最新 stable（2026-10-13 发布）。
- **与宿主 Chrome 155 的对齐决策**（沿用 #108 `cef_lock.json` alignment_note 先例）：
  本机宿主 `google-chrome` stable=155；Electron stable 通道**没有** Chromium 155
  构建（155 仅存在于 45.0.0-alpha.7~13 与 46 nightly；45-beta 已是 156）。拒绝
  beta/nightly 通道（移动靶，破坏可复现性），锁最高 stable 44.7.2（=Chromium 152，
  与宿主差 3 个大版本）。CEF 侧（#108）因上游无 155 锁了 154；路线 A/B 同为
  「最近邻 stable」策略。Electron 补齐 155 stable 后应重锁对齐，届时跨路线像素
  比对（#102 env_manifest 门禁）会显形主版本差异。
- 二进制完整性：本目录 `package.json` 自带 `postinstall: node node_modules/electron/install.js`
  ——electron@44 的 npm 包**不再声明包级 postinstall**（上游移除，仅提供
  `install-electron` bin；官方与 npmmirror 的 tarball 逐字节一致，sha512 同
  `package-lock.json` integrity），不补这一步 `npm ci` 只装 JS 壳、无二进制。
  `install.js` 下载分发包后按 Electron 官方 `SHASUMS256.txt` 强制校验。
  国内镜像安装：

  ```bash
  env ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/ \
      npm ci --registry=https://registry.npmmirror.com
  # 失败再用代理：https_proxy=http://127.0.0.1:7890 http_proxy=… npm ci
  # 真·从零复验（绕过本地缓存强制重下+校验和验证）：
  # env electron_config_cache=/tmp/fresh force_no_cache=true ELECTRON_MIRROR=… npm ci …
  ```

  注：electron 44 的缓存变量只认小写 `electron_config_cache`（`ELECTRON_CACHE`
  无效），`force_no_cache=true` 强制重下。2026-10-15 实测从零下载
  `electron-v44.7.2-linux-x64.zip`（122,969,114B）sha256=
  `777b26a64aee3d185acb9f93c04782bb51eae7c2c61c793963ef24374e5a1539`，
  与官方/镜像 `SHASUMS256.txt` 一致。

## B0 / B1 双模式（工单口径，各自独立进程启动）

| 模式 | 定义 | 实现要点 |
| --- | --- | --- |
| **B0** | Software output device | `app.whenReady()` 之前 `app.disableHardwareAcceleration()`；软件输出设备直接在 CPU 渲染，省去 GPU→CPU 回读拷贝 |
| **B1** | GPU Accelerated → CPU Shared Memory Bitmap | 保留硬件加速；`whenReady` 后 `app.getGPUFeatureStatus()` + `await app.getGPUInfo('complete')` 采集真实 GPU 状态 |

本机实测（RTX 4080 + Xwayland/Xvfb）：B0 生效后端 `software_skia`；B1 渲染进程
WebGL 探针实测 `ANGLE (NVIDIA Corporation, NVIDIA GeForce RTX 4080 … OpenGL ES 3.2)`
→ `nvidia_egl`（Xvfb 下依然成立——EGL surfaceless 不依赖 X 服务器的 GLX）。
两模式首帧 PNG 逐字节一致（sha256 相同，帧状态是 f 的纯函数）。

## 安全硬化（工单逐项）

- 窗口：`width: 1920, height: 1080, show: false, frame: false, transparent: true,
  useContentSize: true`；`offscreen: { useSharedTexture: false, deviceScaleFactor: 1 }`
  ——**显式禁用共享纹理**（[GHSA-8x5q-pvf5-64mp](https://github.com/electron/electron/security/advisories/GHSA-8x5q-pvf5-64mp)
  UAF），首帧走 CPU shared memory bitmap；`deviceScaleFactor: 1` 显式写出
  （Electron 44 的锁定形态在 `offscreen` 选项内，旧的
  `webContents.setDeviceScaleFactor()` 已移除；页面世界 `devicePixelRatio=1` 由
  preload 回传自证）。
- `webPreferences`：`backgroundThrottling: false`、`contextIsolation: true`、
  `nodeIntegration: false`、`sandbox: true`、`spellcheck: false`、最小 preload。
- 命令行开关：`force-color-profile=srgb` + `hide-scrollbars`（锁定色彩与取景）。
- 帧出口用 `capturePage().toPNG()`（PNG 编码路径），不经 `NativeImage.toBitmap()`，
  规避 Electron ≥ 43 的 toBitmap 默认 sRGB 归一化变更面。

## 进程组卫生（接入 #103 `vtx/proc_supervisor.py`）

- 宿主驱动用 `SupervisedProcessGroup`（`backend_id="gfx_elec"`）拉起 Electron：
  运行目录 `cache/tmp/<UTC>-gfx_elec-<pid>-<n>/`，`app.setPath('userData', …)`
  指向其 `profile/` 子目录（工单「`cache/tmp/<UTC>-gfx_elec/`」落点族；unique
  后缀是 #103 的并行防撞机制）；supervisor 在 argv 尾部追加 `--parent-alive-fd=<fd>`。
- **协作契约（main.mjs）**：`fs.createReadStream(null, { fd })` 守听父存活管道，
  `end`/`close`（宿主暴毙 → EOF）后立即 `process.kill(-process.pid, 'SIGKILL')`
  自毁整棵 Electron（Main+GPU+Zygote+Renderer）。这是「锦上添花」层；权威层是
  supervisor 的守卫进程（EOF → killpg）+ 账本 reap（starttime/boot_id 核验）。
- 演练（DoD）：

  ```bash
  python3 experiments/gfx_electron_worker/drill_sigkill.py --mode=b0   # 或 b1
  # DRILL_RESULT {"ok": true, … "orphan_members_after_kill": [], … "tmp_residue_dirs": []}
  ```

## 用法

```bash
cd experiments/gfx_electron_worker && npm ci   # 首次安装（见上文镜像说明）
cd -

# B0 / B1 各渲一帧 gfxA.html?f=0 并生成 env_manifest.json（产物在 --out-root/<mode>_<UTC>/）
python3 experiments/gfx_electron_worker/run_gfx_electron_worker.py --mode=b0 --out-root /tmp/evidence
python3 experiments/gfx_electron_worker/run_gfx_electron_worker.py --mode=b1 --out-root /tmp/evidence

# Xvfb 下验证（另起 Xvfb 后）
Xvfb :77 -screen 0 1920x1080x24 -nolisten tcp &
DISPLAY=:77 python3 experiments/gfx_electron_worker/run_gfx_electron_worker.py --mode=b1 --out-root /tmp/evidence/xvfb
```

产物目录内容：`frame_f0000.png`（首帧）、`truth.json`（版本三元组 + GPU 内部接口
实测 + 页面自证）、`result.json`（摘要）、`env_manifest.json`（`vtx.env_manifest/1`
形态，含 `runtime_backend_truth.source="electron"` 证据与 Xvfb 归属探测）、
`electron.stdout.log` / `electron.stderr.log`。

## 验证门复现（2026-10-15 实测，证据 `/tmp/framivra-evidence/issue-112/`）

| 门 | 命令 | 结果 |
| --- | --- | --- |
| 静态语法 | `node --check experiments/gfx_electron_worker/main.mjs`（含 preload.cjs；`env python3 scripts/ci_checks.py static` 全量） | 通过 |
| 真实显示 B0/B1 | `env DISPLAY=:0 python3 …/run_gfx_electron_worker.py --mode={b0,b1}` | 均 1920×1080 首帧成功，sha256 一致 |
| Xvfb B0/B1 | `env DISPLAY=:77 …` | 同上，`under_xvfb=true` 入 manifest |
| kill -9 卫生 | `python3 …/drill_sigkill.py --mode={b0,b1}` | 全树（8 成员）残活=0、漏网=0、临时目录残留=0 |
| 从零复现 | `rm -rf node_modules && npm ci`（npmmirror） | 退出 0，`electron --version` = 44.7.2 |

## 下游熔断门提醒（#112 范围外）

本目录只证明「能起、能锁、能出帧、能干净死」。单帧 IPC + PNG 编码耗时 > 18ms
或强杀出现孤儿进程即触发 K1 熔断——性能测量与正式 gfx 阶段接线属后续工单。
