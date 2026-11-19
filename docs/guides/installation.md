# 安装与安全更新

## 受管非 AI 制作环境

推荐[README 三平台命令](<../../README.md#一键安装完整非-ai-环境>)：[POSIX](<../../scripts/bootstrap.sh>) / [PowerShell 引导器](<../../scripts/bootstrap.ps1>) 准备 Python，再运行[源码安装器](<../../scripts/install_source.py>)，工作目录不限。**联网写操作**：准备源码、venv、缺失原生依赖和锁定字体；需要系统权限时明确请求，不由 DSH 提权。不覆盖系统 Python、不收养开发克隆、不改已有项目或旧 wheel 安装。

### 默认套件与范围

实际依赖唯一列表见[README 矩阵](<../../README.md#依赖矩阵>)。默认含完整 apps/plugins/templates 源码、editable venv、FFmpeg/ffprobe、原生浏览器、SoX、音频同步/图像/合成依赖与 Noto/DejaVu/Inter/JetBrains Mono 字体及许可。默认 CDP；保留 CPU x264、原生 AAC、`ass` 等完整编码器/滤镜检查，不因镜像失败改用裁剪版、未知静态包或跳过制作验收；libfdk_aac/GPU 不是硬要求。

标准范围是 HTML/CDP、普通媒体、字幕、同步、基础 synth/sample 音乐与音效、混音/响度/导出。Blender、SVG/Inkscape、CEF/Electron、SF2/FluidSynth、SFZ/sfizz、matchering 和第三方音源仍按组件另装；非 AI 不等于默认已验收，素材名称不证明许可。默认不获取模型、Torch、ComfyUI、AI 服务或密钥；已有用户配置不重置，enabled 也不是所有插件的统一执行禁用门。macOS 完整 FFmpeg 的 whisper.cpp/ggml/tesseract 是编译库依赖，**不是下载模型，也不能宣传为不含任何 AI 相关库**。

### 统一位置与登记

| 系统 | 默认用户根 | 适配与资格 |
|---|---|---|
| Linux | `~/.local/share/framivra` | apt-get/dnf/pacman；保留发行版/组件/架构与签名，不跨装发行版包。精简容器或缺库时明确失败；主干既有 [#381](https://github.com/CongliangK/framivra/issues/381) 阻止隔离安装 ready，本轮不修不绕 |
| macOS | `~/.local/share/framivra` | 自动 bottles 路线限 Apple Silicon、macOS ≥15、原生 Google Chrome.app。macOS 26.5.2 arm64 已实机验收非 AI/CDP；Intel、其余系统版本及从无 Homebrew/Chrome 起步未验，不静默转源码编译 |
| Windows | `%LOCALAPPDATA%\framivra`（缺失时用户 AppData/Local） | Win10 x64 已实跑到字体/CLI 协议，旧 Git 权限修复后可用；soxr 缺 C++ 运行库阻塞制作验收。新 MinGit/自动运行库修复待实机复验，不声明 SVG 主 Demo 支持；Linux portable PS7 夹具不证明 PS5 整体验收 |

根内含 `source/`、`venv/`、`config.json`、`fonts/`、`verification/`、`installation.json` 和下载缓存；POSIX 入口 `bin/vtx`，Windows 入口 `venv/Scripts/vtx.exe`。配置、字体、验证产物在源码外。Linux 只用根内 fontconfig；macOS/Windows 另复制到用户字体目录，不覆盖不同字节的同名文件。已合格工具复用，缺失或 FFmpeg 能力不足才准备；keg-only/私有工具路径由受管 CLI 真实加载，重跑仍核验，不把登记路径当合格证明。

登记 schema 为 `framivra.installation/v1`，含绝对 source/python/vtx/config 路径、完整 commit、版本、`non-ai`/`cdp`、工具、字体环境与验收输出。仅实际 import、协议、音乐合成/QC、HTML/字幕/音视频冷暖全链、严格 QC、解码通过后登记 `ready`；失败保留 `preparing` 并非零退出。capability scope 不替代 900 帧 SVG 主片、发行冻结或跨平台实测。

DSH 先只读验证默认登记再扫描旧位置，候选不自动执行/切换，须选择、保存并检查真实 read/store。Windows 的 ExecutionPolicy Bypass 仅限子进程，不改持久策略或绕企业组策略；使用绝对 exe，不把 Windows 分号 PATH 填入 POSIX 受信 PATH。显式 `VTX_CONFIG` 仍优先。

Windows 主流程须普通非提升 PowerShell；提升写态在引导下载前拒绝，`--plan` 仍只读。新持久用户根继承可信父目录 ACL，不自动修既有 ACL。私有 Git 改用 MinGit ZIP，同主体解包完整运行库，不启动提权 Git 安装器。旧 Inno 目录原样保留，健康已登记工具仍复用；受限旧树不自动 takeown/reset、授 Everyone 或设置 `safe.directory=*`。

### 国内优先与系统副作用

`--network cn` 默认开启，`--network official` 选择官方渠道；均只换传输地址，不暗换同名文件的版本、架构、构建或许可。HTTP 用已有代理环境，国内已知镜像定向直连；不写死本机代理端口、不改全局代理/Git 重写或用户持久 no_proxy。**项目首次 RAW/Git 源码和 Gyan 原 ZIP 尚无已证实的国内等价缓存**，这些请求仍需官方可达；不能承诺全部国内化。HEAD/元数据可达不等于包签名、安装或制作认证。

- Python：先由 pip 解析当前解释器/平台的完整 wheel 计划，保存 `cache/python-plan.json`；TUNA → 阿里云 → 官方，之后按固定文件 SHA256 下载并仅从本地安装。无 wheel 时明确失败，不静默转 sdist 编译。
- HTTP：公共 `cache/downloads/` 按 SHA256 保存完整/部分文件，使用 Range/If-Range、大小/哈希核对和原子发布。网络中断保留可重试数据，拒绝把错误内容当成功；完整缓存每次复核。无统一 1800 秒大文件墙钟截止，仍有连接/空闲超时和有限重试；不保证远端允许续传或永不重传。字体 Debian 固定包走 TUNA → USTC → 官方原路径，五个 RAW 文件仍用同一官方提交；不改字体锁、许可或安装清单里的规范来源 URL。
- Linux：临时复制 apt legacy/deb822、dnf repo 或 pacman Include 配置；不永久重写已有系统源。Debian/Ubuntu 主镜像为 TUNA，Fedora/Arch 为 USTC；失败回同一可信上游（Arch 回原可信 mirrorlist），不轮询不存在的全站覆盖；自定义 provider 保留。Ubuntu FFmpeg 要 universe，ports 架构不混 archive；包解析/缓存由原管理器负责。
- Fedora：受限 `ffmpeg-free` 不当完整成功；额外确认后，从已信任 Fedora 安装 `distribution-gpg-keys`，临时启 RPM Fusion **free** 并验证签名，可能导入 RPM key 数据库、执行 `swap --allowerasing`；不自行持久注册 RPM Fusion、不启 nonfree/tainted。Arch 明示并确认 `-Syu` 整机升级，避免 partial upgrade；自定义外部下载器不在当前自动恢复支持内。
- Ubuntu：优先已有合格原生浏览器；缺失时仅 amd64 选择 Google 官方签名 apt Chrome（核对锁定 key 及公开指纹），不静默走 Snap。额外确认 **Google 包正常 postinst 可能注册其自己的持久更新源**，这是厂商包副作用，不等于安装器获准重写其他源；其他架构需自行提供合格原生浏览器。
- Homebrew：优先使用 `PATH` 上的 brew；不自动更新 brew 本体或改 remote。旧 brew 若无法解析签名 API，先自行 `brew update`（更新 Homebrew/元数据），或将受支持的新版 brew 放在 `PATH` 前端，再重跑。TUNA API/bottles 使用其官方回退，不把 cask/API 镜像当 Chrome DMG 镜像。普通 FFmpeg 不可用或缺 libass 时，先核验 Apple Silicon 默认前缀下已装的 `ffmpeg-full` 同套 ffmpeg/ffprobe，再考虑安装；不 force-link，递归依赖也强制 bottle，无匹配 bottle 就失败。Chrome 仍是厂商 DMG。缺 Homebrew 时执行官方安装脚本，cn 下**新建 brew** 优先 TUNA、失败官方，新 origin 可能保留所选镜像；CLT 仍是 Apple 渠道。Linux 尚无 Python 的 stage-zero 先用用户现有可信系统源，不冒称此阶段已强制国内镜像。
- Windows：Python 原 exe、普通 MinGit 原 ZIP 使用固定身份的国内候选/官方回退；FFmpeg 仍取 Gyan 官方同一原 ZIP，SoX 私有解包完整归档。保留全部 helper/DLL/许可，不拿 busybox 实验变体代替。已有 Edge 可复用；缺浏览器时 winget 使用官方动态元数据，不冒称固定字节或高校镜像覆盖。
- Windows C++：先真实 import 全套依赖并做 soxr 最小重采样；仅 ImportError 且白名单 CRT 确定缺失（含 ctypes 包装的 126）才准备固定微软 VC++ 14.44.35211 x64 原件。SHA256 与 Windows Authenticode Valid/Microsoft 发布者均须通过；没有已证实的高校等价镜像。确认/`--yes` 后仅厂商 exe 请求 UAC，会更新系统共享运行库、包缓存和卸载登记，可能影响其他应用。不拷 DLL/改 PATH/强制降级或自动重启；`--no-system` 在下载前阻止，取消/信任失败硬停。3010/1641 要求用户重启后重跑；0/1638 也仅在新进程实际 import/重采样通过后继续原制作验收。

`--yes` 授权已展示的事务，不绕系统管理员权限；没有它时上述新增仓库/替换/厂商源注册/整机升级须明示确认。原生包安装本身会改包数据库/缓存并执行厂商脚本，不承诺无系统副作用。包源真实性与编解码 GPL/专利/再分发义务是不同问题，国内镜像不消除许可风险。

### 维护跨平台安装验收

- macOS 创建或修复受管 venv 时链接解释器，兼容 Xcode/CLT 的 framework Python，不改为复制可执行文件。
- 发现 BGM 不依赖 POSIX；发布目录时兼容部分 Windows 绝对路径的混合斜杠，不改运行数据。
- GPU 使用原生锁；Windows 派发并发任务前实测可用内存，探测失败即拒派。
- hue 素材先规范 RGB 色域，不放宽 QC；冷暖快照须计入 Linux core 持久化图形包。

### 计划、重跑与更新

引导器 `--plan` 使用已有合格 Python，在下载 bundle 前输出只读概览，不联网、不建根、不安装或执行候选；没有 Python 明确失败。详细离线计划需预先审读完整七文件 bundle，再执行 `python3 install_source.py --plan`，不再是单脚本离线运行。`--no-system` 不安装系统包/导 key/注册厂商源；缺合格工具或 ensurepip 时失败。非默认 `--root` 需在 DSH 手动选取。

默认源码通道明确 `main`，或用 `--ref` 选 SHA/tag。引导 bundle 七文件从同一完整提交获取，不能每次请求漂移 main；`--source-commit <SHA>` 让新装 main 源码绑定到已解析提交。复用旧 owned source 或显式选择其他 ref 时，不把 installer 版次当该源码身份，仍须核对实际完整 commit 与兼容性。七文件含源码安装、公共下载、POSIX/Windows 原生准备、Python 依赖、运行字体及冻结提取 helper；信任仍是官方 Git/HTTPS、平台签名源与文件 pin，不宣称独立签名/keyless provenance，也不把插件 latest 当内核稳定版。

**一般直接重跑同一命令即可恢复，不用删根重装。** 若安装器明确指出旧 macOS 源码的运行态 PATH 会遮住合格工具，使用 `--update` 更新 owned source 后重试。无新更新事务时复用原 owned source commit；Python 计划在解释器/平台/需求身份不变时复用，身份变化或新更新事务才重解析。`--update` 开启更新事务；一旦批准目标，失败重试（包括再次带 `--update`）先恢复该 commit/stage 和已完成的依赖计划，不因 main 前移另起事务。`.source-progress.json` 记录已批准 commit 与唯一自有 stage；继续其 Git 获取/验证，不收养未知克隆，不 reset/clean 开发目录。origin、所有权、HEAD、干净状态和 ignored→tracked 碰撞仍检查；异常目录不静默删除。

安装日志的 `Source commit` 是实际验收的源码提交。若上次 `--update` 已经完成 Git 检出，但旧源码无法通过验收，而上游已修复，则给原引导命令加 `--update --restart-update`（写操作），显式重新选取目标并完整验收。该选项不会覆盖尚在进行的源码 checkout journal，也不删除原下载缓存；若新尝试中断，去掉 `--restart-update` 再续跑。

根 `.install.lock` 是内核 advisory lock：**不删除“stale”锁文件**；文件保留不表示锁仍被占用，持锁进程退出后自动释放，活锁冲突明确失败。HTTP 部分文件、Git stage、pip 计划与系统包缓存各由对应层恢复；断点恢复不承诺跨层原子回滚，也不绕过任何原有 ready 判据。

## 手动克隆与旧式引导器

从 [README 开发者快速开始](../../README.md#快速开始clone-即用零安装步骤) 手动克隆，或使用以下旧式源码引导器；它们只运行幂等 `vt.py init`，**不是上方完整制作套件**，不登记新的受管安装。无需 gh CLI，写克隆目录与配置并可能更新源码。

## 安装选择与信任

版本解析、固定 tag、离线候选与身份验证的唯一规则见 [源码稳定基线](../releases.md#安装选择与信任)。执行引导器前须接受该信任边界，可先下载审读；HTTPS 下载不等于独立签名或 keyless provenance 验证。

## Linux / macOS

```bash
curl -fsSL https://raw.githubusercontent.com/CongliangK/framivra/main/scripts/install.sh | bash
```

默认克隆位置 `~/.local/share/framivra-repo`。引导器探测 python3≥3.9 与 git；缺失时给 apt/dnf/brew 或 python.org / git-scm.com 指引，**不 sudo、不自动安装包管理器依赖**。兼容 macOS 系统 bash 3.2。

init 生成 `config.local.json`；参数原样透传，如 `bash -s -- -- --fix`。curl|bash 有可用 `/dev/tty` 时将交互接回终端。完成后提示进入仓库与 doctor 用法；例行检查使用 README 的离线形态。

## Windows

以下为原生 PowerShell，无需 WSL/Git Bash；脚本先保存再执行，控制台直连不吞交互：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "iwr -UseBasicParsing https://raw.githubusercontent.com/CongliangK/framivra/main/scripts/install_windows.ps1 -OutFile $env:TEMP\vt-i.ps1; & $env:TEMP\vt-i.ps1"
```

默认克隆位置 `%LOCALAPPDATA%\framivra-repo`。引导器检测 python≥3.9 与 git；缺失时使用 winget 用户级安装（Git 包为 Git.Git），失败给手动指引。winget 新装后需新开终端更新 PATH。企业域控无 winget 时手动装 Python 与 Git；隔离内网不在支持范围。

脚本名后参数原样交给 init，短横线开头须加引号，如 `& $env:TEMP\vt-i.ps1 '--fix'`；`--fix` 自动装缺失基础依赖。

## 可选音乐环境

完整 clone/源码安装包含 [bgm 执行本体](../../apps/bgm/README.md)，无需旧 music_tools 仓或外部 music skill。内核 wheel **不包含 app 源码**，仅 `pip install framivra` 不会安装音乐组件；使用完整仓入口 `python3 apps/bgm/bgm.py --help`。

音乐基础运行需要 NumPy + FFmpeg；可在独立 Python 环境安装 NumPy，并用宿主 `bgm.python` 指定解释器。运行配置 JSON 由 `MUSIC_RUNTIME` 或宿主 `bgm.runtime` 指向，默认 `$XDG_DATA_HOME/framivra/music/runtime.json`（未设 XDG 时为 `~/.local/share/framivra/music/runtime.json`），资产放同根 `assets/`；默认文件不存在仍可基础代码合成，显式指定的文件缺失则报错。配置格式与引擎前置条件以组件 README 为准。普通视频不要求音乐环境，选装音乐不应触发所有重服务探测。

源码不捆绑音源、采样、乐谱、模型或第三方预览，也不自动下载；用户自行获取并核验[许可与来源](../third-party-licenses.md)。旧 `bgm.repo` / `bgm.cli` 已废弃，不再选择旧仓、包装器或执行器；请迁移到仓内入口与上述解释器/运行配置。旧资产迁移须先列清单、校验内容与许可，再经确认搬到新资产根并切换用户配置及活引用，不用旧路径软链延续依赖；冻结历史项目不重写，也不算当前活依赖。

## 共同更新安全边界

重复执行会安全更新并重跑幂等 init。仅干净克隆（`git status --porcelain` 为空）才 fetch 并 `reset --hard`：`main` 用 `refs/remotes/origin/main`；`stable` 先解析发行 tag，与显式 tag 均核验后使用 `refs/tags/<tag>`。Linux/macOS 显式 refspec 浅 fetch。下列情况拒绝更新，不静默丢改动：

- 脏克隆（含未跟踪文件）；清理或删除克隆后重试，或用 Linux/macOS `--skip-update`、Windows `-SkipUpdate` 直接复用。
- 本地提交偏离 `.git` 内同步 marker，避免 reset 丢提交。
- 上游将被忽略路径（如 `config.local.json`）转为跟踪文件；碰撞预检拒绝覆盖。先把冲突文件备份到克隆外，再删除克隆重装。
- 符号链接目录或 origin 不匹配的克隆，拒绝覆盖。

## 环境变量与平台差异

| 变量 | 共同规则 | 平台差异 |
|---|---|---|
| `FRAMIVRA_DIR` | 必须绝对路径，可含空格；不得使用运行时数据根，否则运行数据使克隆变脏、后续更新被拒 | Linux/macOS 禁 `~/.local/share/video-tools`；词法规范化拒绝 `/./`、`/../` 变体，不依赖 GNU realpath，也不解析符号链接（两路径经符号链接同址不拦）。Windows 禁 `%LOCALAPPDATA%\framivra`，并拒绝以点/空格结尾的路径段 |
| `FRAMIVRA_REPO` | 默认 `https://github.com/CongliangK/framivra.git`，可使用可信镜像；拒绝空白 | 无 |
| `FRAMIVRA_REF` | 默认 `stable`；可指定 `vX.Y.Z` / `vX.Y.Z-rc.N` 或显式 `main`；拒绝空白 | 版本与信任规则见上方唯一入口 |

所有变量拒绝空值、控制字符、短横线开头；空白仅 DIR 放行，Linux/macOS DIR 全空白仍拒绝。变量未设置时使用各自默认值。实际平台资格及冻结发布规则见 [稳定支持范围](../releases.md#稳定支持范围)，实现存在不等于实机验收。
