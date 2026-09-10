---
name: video
description: "B站视频产线一体化 skill：选题调研（多智能体编排出 01-brief）→ HTML 帧驱动动效（片头标题/动态图表/下三分之一/端板）→ timeline.json + vtx 帧精确剪辑与合成渲染 QC（切分素材、多轨叠加、画中画、字幕烧录、多音轨混音）。Use whenever 用户提出新视频选题、要求调研视频主题/分析同类视频/判断选题可行性/生成 01-brief，或要求制作视频动效/动态图表/字幕条/片头片尾/透明图层，或要求剪辑视频、合成动效与实拍、切分素材、烧字幕、混音轨、渲染成片、新建视频项目，或提到 timeline.json / vtx / gfx 页面 / 帧驱动动画 / 01-brief / 视频调研 / 对标分析 / 选题池 / B站视频制作。"
---

# video · B站视频产线 skill（索引）

本 skill 覆盖一条视频从选题到成片的完整产线，按领域分三份完整文档，本 SKILL.md 只做**路由索引**——接到任务先查「任务路由」，去读对应文档再动手，不要凭本文件的概览直接开工。

| 领域 | 文档 | 管什么 |
|---|---|---|
| 调研 research | `references/research.md` | 多智能体选题调研（R0-R6 编排、采集脚本、独立评分、红队质询），产出 01-brief.md |
| 动效 gfx | `references/gfx.md` | HTML 帧驱动透明动效页（片头标题、动态图表、下三分之一、片尾端板）的设计协议与样式模式库 |
| 剪辑 edit | `references/edit.md` | timeline.json 契约 + vtx CLI 渲染 + audit/QC（切分、多轨、画中画、字幕、混音、响度） |

## 任务路由

| 用户任务 / 关键词 | 先读 |
|---|---|
| 新选题、调研可行性、对标分析、生成 01-brief、选题池 | `references/research.md` |
| 做动效页、动态图表、片头片尾、下三分之一、写 gfx/*.html | `references/gfx.md` |
| 剪辑、切素材、多轨合成、画中画、烧字幕、混音、渲染、QC；提到 timeline.json / vtx | `references/edit.md` |
| 新建视频项目（脚手架） | `references/edit.md`（内含 new_project.sh 用法） |

一次任务常跨领域：典型产线是 **调研（research）→ 脚本 → 动效（gfx）+ 剪辑（edit）→ 渲染 QC**。跨领域时按阶段依次读对应文档，不要一次全读。

## 全局铁律（三领域通用，细节以各文档为准）

1. **剪辑永远走 timeline.json + vtx**（见 `references/edit.md`）：AI 不手写 ffmpeg 剪辑命令；修改 timeline 后必须重跑 validate。
2. **gfx 页面必须帧驱动**（见 `references/gfx.md`）：动画状态 = 帧号 f 的纯函数，禁 CSS animation/transition、Date.now()、Math.random()、rAF 时序。
3. **调研必须走 R0-R6 编排**（见 `references/research.md`）：主编只编排不采集，采集与评分分离，红队质疑必逐条回应。
4. 时间量一律**整数帧**；项目时基 30fps（D24 换基，≠29.97）、48kHz 音频（每帧整 1600 采样）；区间 `[in, out)` 左闭右开。

## 文件地图

```
video/
├── SKILL.md               ← 本索引
├── vtx/                   ← 【程序本体】剪辑中间层 CLI（edit 领域的执行引擎）
│   ├── vtx.py             ← 唯一渲染入口：validate/audit/prepare/gfx/video/audio/mux/qc
│   ├── timeline.schema.json ← 契约 schema（含逐字段注释）
│   └── preview.html       ← 时间线可视化预览网页（人审用）
├── references/
│   ├── research.md        ← 调研编排方法论（R0-R6、公平性规则）
│   ├── gfx.md             ← 帧驱动协议、工具函数、样式模式库、设计基准
│   └── edit.md            ← 契约 v2、vtx 流程、audit/QC、音频工程、坑表
├── scripts/
│   ├── new_project.sh     ← edit：视频项目脚手架
│   ├── bilibili.py        ← research：B站搜索/统计/评论采集（WBI 签名+重试）
│   ├── github_repos.py    ← research：GitHub 仓库调研采集
│   ├── score_topic.py     ← research：选题评分（--rubric 行为锚点）
│   ├── scroll-shoot.mjs   ← 设计研究：CDP 滚动截图（WebGL/懒加载站可采）
│   ├── fetch-site-assets.py     ← 设计研究：递归抓取站点全部 CSS/内联样式/字体
│   └── dump-computed-styles.mjs ← 设计研究：渲染层计算样式采集+量化统计（色彩配比/字号分布）
└── assets/
    ├── title-card.html    ← gfx：标题卡起手模板
    ├── lower-third.html   ← gfx：下三分之一条起手模板
    ├── gfx-examples/      ← gfx：样式模式库实例页（gfxA/B/C.html + motion.html）
    ├── brief-template.md  ← research：01-brief 十节模板
    └── supplement.template.json ← research：评分补充表模板
```

**自包含与迁移**：本 skill 打包了程序本体（vtx/）+ 指示文档（references/）+ 脚本与模板（scripts/、assets/），整个目录拷走即可用。程序侧仅依赖 python3 标准库 + 系统 ffmpeg/ffprobe/google-chrome；脚本用自身位置定位 skill 根，无硬编码主目录路径。

## 产线衔接与外部资产

- 视频项目工作区在 `~/文档/04_bilibili视频/`（2026-08-26 D27 内部重构）：顶层 = 每期视频一个文件夹（如 `voice-ime/`）+ `_library/`（跨期资产：assets_audio 音频库 / corpus 字幕与叙事语料 / bg_test 抠像链 / reports 调研报告）+ `_archive/`（poc1/poc2/design-research 历史工程）。素材、timeline.json、gfx 页、out/ 产物都在**各期项目文件夹**内，与 skill 分离；渲染入口是本 skill 的 `vtx/vtx.py`；上层流程与全部决策记录在工作区 `AGENTS.md`。
- 动效页写好放项目 `gfx/`，由 edit 流程的 gfx 阶段逐帧渲染进时间线——gfx 文档只管页面本身，不碰合成。
- BGM/音效/环境声的选取授权与 AI 生成产线：见 `references/edit.md` 文末指针（`_library/assets_audio/` 资产库 + music skill → music_tools 产线）。
