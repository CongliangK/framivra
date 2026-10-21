# Framivra —— 可复现、帧精确的视频制作工具箱

Framivra（读 "frame-ee-vra"）是 B 站视频产线的程序仓。内核 `vtx/` 提供 timeline.json 契约、帧精确渲染、混音响度终链与内容寻址缓存，**永不可关**；科技口播工作流在 `templates/tech-talk/`，执行技能在 `docs/skill/`。文档中的 `video_tools` 是路径占位名，不是另一套安装。大模型能力默认关闭。

## 四层架构一页

| 目录 / 入口 | 职责 |
|---|---|
| `vtx/` | 永不可关的内核：渲染、validate/QC、响度、缓存、`api.py` 与 `pluginlib/` |
| `apps/`、`plugins/` | 独立素材/产线应用与 timeline 管线扩展；安装态、键与 hooks 见 [catalog](docs/catalog.md) |
| `docs/skill/`、`templates/` | 工具技能与[工作流/开发范本集合](templates/README.md) |
| `scripts/`、`vt.py` | [仓级工具](scripts/README.md)；doctor、组件/模板 list/check、version、init 与链接修复 |

关停后主链产物不可信的是内核；仍能可信出片的是可卸载组件。apps/plugins 每件自包含（app.json/plugin.json + 代码 + README + tests）。**可卸载不等于无依赖**：已启用插件可以依赖相应 app，不能删除依赖后继续宣称扩展生效；[协议与边界](docs/ARCHITECTURE.md#3-应用边界apps)为准。

## 快速开始（clone 即用，零安装步骤）

公开样片从 [standard Demo](docs/guides/demo-fixture.md#公开-standard-demo-生产运行) 起步。自建项目先满足[依赖矩阵](#依赖矩阵)；「零安装」仅指不必 pip 安装本仓，不包含外部工具或媒体。以下克隆、初始化、脚手架与渲染均为**写操作**；`PROJECT` 必须是全新项目的绝对路径，避免覆盖已有 timeline。

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
- **组件**：[catalog](docs/catalog.md) 找 app/plugin，再读其 README。BGM、配乐与分轨混音由姊妹仓 music_tools 产出，`apps/bgm` 负责路由；挂入 timeline 后可用 [Blender 分轨审计](apps/blender-bridge/README.md#分轨审计包-audit_bundlepy人机协作交付门)参与混音裁决。
- **工作流**：[templates](templates/README.md) 包含完整流程、纯内核示例与开发范本；`python3 vt.py templates list|check` 检查工作流模板，doctor JSON 对应字段为 `skills`。

## 依赖矩阵

| 使用范围 | 依赖与边界 |
|---|---|
| 基础渲染 | Python ≥3.9、FFmpeg/ffprobe、Chrome/Chromium/Edge；`vt.py init` 自检 |
| 默认 CDP gfx | NumPy；显式 `VTX_GFX=cli` 不要求 NumPy，但有独立[环境身份与支持边界](docs/cache-provenance.md#gfx-environment) |
| 正式 AAC 封装 | 强烈建议 libfdk_aac；缺失回落并告警，交付仍须复测编码后解码真峰 |
| 可选媒体工具 | Blender、SoX；`video --gpu` 可用 NVIDIA NVENC，探测失败回落 x264 |
| 默认关闭的模型/服务 | [ASR](apps/asr/README.md)、[TTS](apps/tts/README.md)、[生图 API](apps/imgen/README.md)、[抠像/ComfyUI](apps/matting/README.md)、[降噪](plugins/denoise/README.md)：各 owner 维护解释器、服务、权重、配置与启用要求 |

默认标准库策略不代表所有后端只需标准库。模型与第三方资产的下载/许可限制见[第三方许可](docs/third-party-licenses.md)，不能把探测已有资产当作允许自动下载。

## 开发者文档与项目布局

[ARCHITECTURE](docs/ARCHITECTURE.md) 定当前结构与契约；[DEV](docs/DEV.md#专题入口) 路由开发专题与 [CI 覆盖](docs/DEV.md#ci-回归覆盖)；[CONTRIBUTING](CONTRIBUTING.md) 给测试/提交流程；编码智能体先读 [AGENTS](AGENTS.md)。[reports](docs/reports/README.md) 仅承载历史结论和证据，不代表当前能力验收。

视频项目与 `_library/` 放在独立工作区（例如 `~/videos/projects/`），不放仓内。程序修改一律改本仓；工作区历史副本是冻结快照。

## License

本仓 [LICENSE](LICENSE) 不替代上游代码、权重、服务与输出权利。[第三方许可](docs/third-party-licenses.md) 是固定证据与载荷/下载边界的唯一入口；未知或受限资产不算合规通过。默认源码未捆绑权重或工具二进制，不等于安装器的可选下载都已审查。
