# 克隆安装与安全更新

从 [README 快速开始](../../README.md#快速开始clone-即用零安装步骤) 手动克隆，或使用下列平台引导器；均运行仓内幂等 `vt.py init`，不要求 gh CLI。引导器会写克隆目录与配置，并可能更新源码；不是零写态检查。

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
