# Framivra —— 可复现、帧精确的视频制作工具箱

Framivra（读 "frame-ee-vra"）是 B 站视频产线的程序仓。内核 `vtx/` 提供 timeline.json 契约、帧精确渲染、混音响度终链与内容寻址缓存，**永不可关**；科技口播工作流在 `templates/tech-talk/`，执行技能在 `docs/skill/`。文档中的 `video_tools` 是路径占位名，不是另一套安装。大模型能力默认关闭。

## 目录导航

| 目录 / 入口 | 职责 |
|---|---|
| [vtx](<vtx/README.md>) | 永不可关的内核：渲染、validate/QC、响度、缓存、`api.py` 与 `pluginlib/` |
| [apps](<apps/README.md>)、[plugins](<plugins/README.md>) | 独立素材/产线应用与 timeline 管线扩展；安装态、键与 hooks 见 [catalog](docs/catalog.md) |
| [dsh-sidebar/](dsh-sidebar/README.md) | DSH 侧栏插件：时间线预览、引用与显式剪辑；独立于 Python 内核发行 |
| [docs](<docs/README.md>) | 文档总入口；[操作技能](<docs/skill/SKILL.md>)、指南、接口与开发资料 |
| [templates](<templates/README.md>) | 工作流、工程示例与组件开发范本 |
| [scripts](<scripts/README.md>)、[vt.py](<vt.py>) | 仓级工具；doctor、组件/模板 list/check、version、init 与链接修复 |
| [tests](<tests/README.md>) | 分领域回归、验收运行器与测试证据 |
| [experiments](<experiments/README.md>) | 可选后端与有界性能实验，不是默认生产入口 |
| [.github](<.github/GUIDE.md>) | CI、专用验收与发行证明工作流 |

关停后主链产物不可信的是内核；仍能可信出片的是可卸载组件。apps/plugins 每件自包含（app.json/plugin.json + 代码 + README + tests）。**可卸载不等于无依赖**：已启用插件可以依赖相应 app，不能删除依赖后继续宣称扩展生效；[协议与边界](docs/ARCHITECTURE.md#3-应用边界apps)为准。

## DSH 插件（npm 安装）

**推荐使用可视化界面：** 在 DSH 的 **插件 → 添加插件** 中输入 npm 包名 `framivra-dsh-sidebar`，点击 **安装**（**下载插件并写入当前 profile**）。无需克隆本仓或手动解压；镜像未同步时切换到 npm 官方源。

兼容版本、安装源与首次配置见[插件安装说明](<dsh-sidebar/README.md#可视化安装推荐>)；供智能体使用的 npm/DSH 命令见[命令行安装](<dsh-sidebar/README.md#命令行安装可选>)。npm 包仅提供 DSH 侧栏，不包含 Python 内核或媒体工具；命令行渲染从下方快速开始进入。

## 一键安装完整非 AI 环境

先安装上方 npm 侧栏，再在任意工作目录执行对应命令。**这是联网写操作**：准备完整源码、独立 Python 环境、FFmpeg/ffprobe、SoX、原生浏览器及中英文字体；默认 CDP，不下载 AI 模型、不启动 AI 服务。缺少系统依赖时可能要求管理员授权；执行前可下载审读脚本。

**Linux：**

```bash
curl -fsSL https://raw.githubusercontent.com/CongliangK/framivra/main/scripts/bootstrap.sh | bash
```

**macOS（Apple Silicon）：**

```bash
curl -fsSL https://raw.githubusercontent.com/CongliangK/framivra/main/scripts/bootstrap.sh | bash
```

**Windows x64（普通、非管理员原生 PowerShell）：**

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; $p = Join-Path $env:TEMP ('framivra-' + [guid]::NewGuid().ToString('N') + '.ps1'); Invoke-WebRequest -UseBasicParsing https://raw.githubusercontent.com/CongliangK/framivra/main/scripts/bootstrap.ps1 -OutFile $p; powershell -NoProfile -ExecutionPolicy Bypass -File $p
```

完成后在 DSH **插件 → Framivra → 内核**中点击**扫描已有安装**，选择已登记的非 AI/CDP 环境，**保存配置 → 检查连接**。扫描不执行候选、不自动切换连接；旧内核不搬迁、不覆盖。

当前主干存在[色域/QC 既有问题 #381](https://github.com/CongliangK/framivra/issues/381)，已在 Linux 安装验收中复现；受影响时安装器保持 preparing、非零退出，不发布 ready。安装器不会绕过该制作验收。

统一布局、标准制作套件与选装边界、更新/重试、信任与三平台实测状态见[安装指南](<docs/guides/installation.md#受管非-ai-制作环境>)。这里的“完整”指 HTML/CDP 动效、字幕、普通媒体、音频同步、基础配乐合成及混音导出链，不意味着所有可选引擎、商业素材或实验后端均已安装。源码通道默认显式 `main`，登记实际提交；不是已冻结稳定发行或签名供应链声明。

## 快速开始（clone 即用，零安装步骤）

从[可编辑主 Demo](templates/demo-workbench/README.md)开始：用 CDP、SVG 或实验性 CEF 渲染同源的 30 秒作品，在真实时间线上编辑字幕、图形与音效。独立的 [portable-v2 SVG 性能基线](templates/demo-timeline-svg/README.md#主性能基线)不变。

自建项目先满足[依赖矩阵](#依赖矩阵)；「零安装」仅指不必 pip 安装本仓。以下克隆、初始化、脚手架与渲染均为**写操作**；`PROJECT` 必须是全新项目的绝对路径，避免覆盖已有 timeline。

```bash
git clone https://github.com/CongliangK/framivra
cd framivra
python3 vt.py init                     # 幂等依赖自检/指引，生成 config.local.json；--fix 自动装
env VTX_DOCTOR_OFFLINE=1 python3 vt.py doctor
PROJECT="/绝对路径/新视频项目"
bash scripts/new_project.sh "$PROJECT" 30
```

脚手架不生成或下载媒体。把含音轨的 CFR 30 fps 素材放到 `$PROJECT/assets/v01.mp4`，或修改 timeline 的 sources、剪辑与音频事件匹配素材；源长度须覆盖引用区间。完成编排后，在同一 Bash 会话执行：

```bash
python3 vtx/vtx.py -t "$PROJECT/timeline.json" validate
python3 vtx/vtx.py -t "$PROJECT/timeline.json" all
```

完整 `all` 及 QC/插件结果成功后查看 `$PROJECT/out/final.mp4`；输出存在不是验收通过。[正式交付链](docs/skill/references/edit.md#正式交付链r1-r4-共用)说明当前回执、截断链和局部预览的边界。

例行 doctor 使用上述离线形态，只比较本地已有引用；联网 doctor 会 `git fetch` 写 `.git`。live skill 符号链接（如 `~/.agents/skills/video/`）的修复先用 `python3 vt.py --fix-links --dry` 预演，实际修复须按写操作处理。

<a id="installer-trust"></a>

自动安装及更新见 [Linux/macOS 与 Windows 安装指南](docs/guides/installation.md)：执行前阅读其信任、源码更新与下载边界。发布维护见 [releases](docs/releases.md)；`env python3 scripts/release/release_github.py --help` 仅查看参数，不执行发布。

## 三层路由

- **创作**：[Skill](docs/skill/SKILL.md) → [编辑与修改配方](docs/skill/references/edit.md) / [HTML 创作组织](docs/skill/references/gfx.md#html-创作组织)。纯 MG 从 [风格集合](templates/mg-styles/README.md) 选起点；科技口播从 [tech-talk](templates/tech-talk/SKILL.md) 起步。
- **组件**：[catalog](docs/catalog.md) 找 app/plugin，再读其 README。BGM、配乐与分轨混音由仓内 [bgm](apps/bgm/README.md) 生成，不依赖旧音乐源码仓或外部 music skill；挂入 timeline 后可用 [Blender 分轨审计](apps/blender-bridge/README.md#分轨审计包-audit_bundlepy人机协作交付门)参与混音裁决。
- **工作流**：[templates](templates/README.md) 包含完整流程、纯内核示例与开发范本；`python3 vt.py templates list|check` 检查工作流模板，doctor JSON 对应字段为 `skills`。

## 依赖矩阵

| 使用范围 | 依赖与边界 |
|---|---|
| 基础渲染 | Python ≥3.9、FFmpeg/ffprobe、Chrome/Chromium/Edge；`vt.py init` 自检 |
| 默认 CDP gfx / 图像制作 | NumPy、Pillow；显式 `VTX_GFX=cli` 不要求 NumPy，但有独立[环境身份与支持边界](docs/cache-provenance.md#gfx-environment) |
| 音频同步 | NumPy、SciPy、SoundFile、soxr、FFmpeg，Windows 另需可用的微软 C++ 运行库；一键安装器准备实际依赖，策略见[安装指南](<docs/guides/installation.md#国内优先与系统副作用>) |
| 制作字体 | Noto Sans CJK SC、Inter、JetBrains Mono、DejaVu Sans；安装器固定公开字节并保留许可，公开 SVG Demo 仍须使用其输入锁 |
| 正式 AAC 封装 | 强烈建议 libfdk_aac；缺失回落并告警，交付仍须复测编码后解码真峰 |
| 额外媒体路线 | Blender、Inkscape/SVG 按各组件安装；SoX 非内核硬依赖但包含在一键套件。`video --gpu` 可用 NVIDIA NVENC，探测失败回落 x264 |
| 可选音乐生成 | [bgm](apps/bgm/README.md)：NumPy + FFmpeg；运行环境外置，音源/模型自行安装，不捆绑、不自动下载；普通视频不要求音乐环境 |
| 默认关闭的模型/服务 | [ASR](apps/asr/README.md)、[TTS](apps/tts/README.md)、[生图 API](apps/imgen/README.md)、[抠像/ComfyUI](apps/matting/README.md)、[降噪](plugins/denoise/README.md)：各 owner 维护解释器、服务、权重、配置与启用要求 |

默认标准库策略不代表所有后端只需标准库。模型与第三方资产的下载/许可限制见[第三方许可](docs/third-party-licenses.md)，不能把探测已有资产当作允许自动下载。

## 开发者文档与项目布局

[ARCHITECTURE](docs/ARCHITECTURE.md) 定当前结构与契约；[DEV](docs/DEV.md#专题入口) 路由开发专题与 [CI 覆盖](docs/DEV.md#ci-回归覆盖)；[CONTRIBUTING](CONTRIBUTING.md) 给测试/提交流程；编码智能体先读 [AGENTS](AGENTS.md)。[reports](docs/reports/README.md) 仅承载历史结论和证据，不代表当前能力验收。

视频项目与 `_library/` 放在独立工作区（例如 `~/videos/projects/`），不放仓内。程序修改一律改本仓；工作区历史副本是冻结快照。

## License

本仓 [LICENSE](LICENSE) 不替代上游代码、权重、服务与输出权利。[第三方许可](docs/third-party-licenses.md) 是固定证据与载荷/下载边界的唯一入口；未知或受限资产不算合规通过。默认源码未捆绑权重或工具二进制，不等于安装器的可选下载都已审查。
