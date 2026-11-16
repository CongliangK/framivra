# 安装与安全更新

## 受管非 AI 制作环境

推荐[README 的三平台一键命令](<../../README.md#一键安装完整非-ai-环境>)：`bootstrap.sh` / `bootstrap.ps1` 获取 Python 后运行 `scripts/install_source.py`，从任意工作目录部署。**联网写操作**：下载源码、PyPI 包、缺失的系统包和公开锁定字体；需要系统权限时明确请求，不由 DSH 提权。不覆盖系统 Python，不改已有项目、开发克隆或旧 wheel 安装。

### 默认套件与范围

实际依赖的唯一列表见[README 矩阵](<../../README.md#依赖矩阵>)。默认含完整 apps/plugins/templates 源码、独立 editable venv、FFmpeg/ffprobe、原生浏览器、SoX、实际音频同步/图像/合成依赖，以及 Noto/DejaVu/Inter/JetBrains Mono 字体与许可。CDP 是默认后端；CPU x264 和原生 AAC 可用，不为 libfdk_aac 自动获取未经审查的 nonfree 构建，不强装 GPU 驱动。

标准制作范围是 HTML/CDP 动效、普通媒体、字幕、音频同步、synth/sample 基础音乐与音效、FFmpeg 混音/响度/导出。完整工具源码不代表所有可选路线就绪：Blender 分轨审计、SVG/Inkscape、CEF/Electron、SF2/FluidSynth、SFZ/sfizz、参考母带匹配 matchering 及第三方采样/音源仍按组件另装；后者不是 AI，也不冒称默认已验收。第三方素材的存在/名称不证明授权。AI 权重、Torch、ComfyUI、ASR/TTS/生图服务与密钥均不安装；已有用户配置不被重置，enabled 字段也不是所有插件的统一执行禁用门。

### 统一位置与登记

| 系统 | 默认用户根 | 本轮资格 |
|---|---|---|
| Linux | `~/.local/share/framivra` | 支持 apt-get/dnf/pacman 引导；真实隔离安装被主干既有 [#381](https://github.com/CongliangK/framivra/issues/381) 的 QC 问题阻止 ready，未宣称完整通过。精简容器、缺软件源/图形库时可能失败，不静默降级 |
| macOS | `~/.local/share/framivra` | 仅 Apple Silicon、原生 Google Chrome.app；本轮 Linux 上实现，macOS 实机 UNRUN，Intel 不在当前 CDP 身份支持内 |
| Windows | `%LOCALAPPDATA%\framivra`（缺失时用户 AppData/Local） | 原生 x64 PowerShell/winget/Chrome 或 Edge；本轮未实机验收，后续由 Windows 用户验证；不声明 SVG 主 Demo 支持 |

根内固定 `source/`、`venv/`、`config.json`、`fonts/`、`verification/`、`installation.json`，POSIX 入口为 `bin/vtx`，Windows 为 `venv/Scripts/vtx.exe`。配置、字体、测试产物在源码外。Linux 字体只使用根内 fontconfig；macOS/Windows 还安装用户字体（已有不同字节同名文件拒绝覆盖）。缺包由平台包管理器获取，包源是否提供完整 FFmpeg/原生浏览器仍须实际测试；Fedora 可能需预先配置媒体源。原生工具已齐时不调用系统包管理器。

登记 schema 是 `framivra.installation/v1`，包含固定绝对 source/python/vtx/config 路径、完整 commit、版本、profile=`non-ai`、renderer=`cdp`、工具路径、字体环境和验收输出。仅实际 import、协议探测、音乐合成/QC、HTML/字幕/音视频冷暖全链、严格 QC 与解码通过后发布 `status=ready`；失败是 preparing/非零退出，不能当作就绪。安装探针使用独立 capability scope，不替代 900 帧 SVG 主片、正式发行冻结或跨平台验收。

DSH 先只读验证默认登记，再扫描旧位置；候选不自动执行或切换，必须选择、保存并检查真实 read/store 协议。Windows 命令仅在子进程设置 ExecutionPolicy Bypass，不修改持久执行策略，也不能绕过企业组策略。原生 Windows 使用绝对 exe，不把 Windows 分号 PATH 填入 POSIX 受信 PATH。工具环境由绑定该源码的受管 CLI 载入，不能只写没有消费者的工具路径；显式 `VTX_CONFIG` 仍优先。

### 计划、重跑与更新

引导器的 `--plan` 仍需通过 HTTPS 获取 Python 计划程序，之后不下载或安装依赖、不建安装目录、不执行候选；完全离线计划须先审读下载好的 `install_source.py`，再执行 `python3 install_source.py --plan`。没有 Python 时计划明确失败，不偷偷安装。`--yes` 允许已说明的下载/写入，不绕过系统管理员授权。`--no-system` 仅复用已安装原生工具，缺项失败。自定义 `--root` 适合隔离测试，DSH 只自动发现默认根，非默认路径需手动选取。

重跑复用已固定源码并重新验证；升级须显式 `--update`。source 的 origin、登记所有权、HEAD 与干净状态须一致；拒绝本地提交、未跟踪改动及 ignored→tracked 碰撞，不 reset/clean，不收养用户克隆。下载中断的唯一暂存目录保留供检查；重跑不覆盖它或用户数据。异常中断可能留下 `.install.lock`，先确认对应进程已停止再人工移除该锁；不自动删除未知锁或失败目录。

新通道默认明确 `main`，解析、抓取并登记实际完整提交，不把插件 release/latest 当作内核稳定版；也不在稳定失败后回落 main。可显式 `--ref` 选择完整 SHA 或版本 tag，但必须通过当前协议/制作测试。信任是官方 Git/HTTPS 与平台包源/PyPI，不宣称独立签名或 keyless provenance；原严格发行资产安装器不因此放宽。

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
