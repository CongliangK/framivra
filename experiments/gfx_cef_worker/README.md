# gfx_cef_worker — CEF Native OSR Worker 构建系统（#108）

路线 A（CEF Native OSR）阶段 1 交付物：**只做构建系统与版本锁**，不接线
`vtx/` 生产代码（那是 #109-#111/#114/#115 的事）。目标：可复现地取得 CEF
二进制 → C++20 编译 `libcef_dll_wrapper` + `vtx_cef_worker` → 产物全部
隔离在本目录，绝不污染仓库打包面（`pyproject.toml` / `setup.py` /
`MANIFEST.in` / `scripts/package_smoke.py`）。

## 目录

| 文件 | 职责 |
| --- | --- |
| `cef_lock.json` | 版本锁：精确 CEF/Chromium 版本 + 官方 URL + `size_bytes`/`sha256`/`sha1_official_index` 双校验和 |
| `build_cef_worker.sh` | 唯一构建入口：校验锁 → 下载（仓外缓存 `~/.cache/cef/`）→ 解包 → CMake+Ninja → 布置运行时 → ldd+sanity 验证 |
| `CMakeLists.txt` | C++20；编译 `libcef_dll_wrapper` 静态库与 `vtx_cef_worker`；`-O3 -march=x86-64-v3 -Wall -Wextra -Werror`；`RPATH=$ORIGIN` |
| `src/main.cpp` | worker 骨架：CefApp 子类占位（OSR 渲染循环属后续工单）+ `--sanity` 自检（见下） |

构建产物（均不入库，`.gitignore` 在册）：

- `build/` — CMake/Ninja 构建树；`build/bin/vtx_cef_worker` 为最终可执行。
- `cef_binary*/` — 解包出的 CEF 官方分发包（数百 MB，含 `libcef.so`）。

## 用法

```bash
bash experiments/gfx_cef_worker/build_cef_worker.sh
# 网络受限时：CEF_DL_PROXY=http://127.0.0.1:7890 bash ...（先直连，失败再挂代理）
```

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

## 下游熔断门提醒（#108 范围外，承接自工单）

阶段 1 只保证"能编、能锁、不泄漏"。单帧 IPC + PNG 编码耗时 > 18ms 或强杀
出现孤儿进程即触发 K1 熔断 —— 该测量属后续工单，本目录不预留接口。
