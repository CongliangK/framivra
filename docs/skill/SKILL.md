---
name: video
description: "video_tools v2 内核 skill：时间线契约与 vtx 帧精确渲染 CLI（validate/diff/subs/audit/prepare/gfx/video/audio/mux/qc/gc/snapshot）+ HTML 帧驱动 gfx 动效协议 + 缓存治理 + 混音响度链与验证门。Use whenever 用户要求剪辑视频、合成动效与实拍、制作纯 MG/Motion Graphics/视频动效/动态图表/字幕条/片头片尾/透明图层、切分素材、烧字幕、混音轨、渲染成片、新建视频项目、缓存治理，或提到 timeline.json / vtx / gfx 页面 / 帧驱动动画 / 修改配方 / runbook / sync_audio / denoise_voice / content_gates / A-roll，或要求处理实拍素材/双系统音视频同步/口播降噪/音频响度/混音验证门，或要求视频调研、对标分析、选题池、B 站科技口播制作（→ 先读 templates/tech-talk/）。选题调研/01-brief/选题可行性、B 站投稿封面、BGM/配乐路由、科技口播全流程 SOP 等产线任务先读 templates/tech-talk/（本仓 templates/ 工作流模板）——封面（make_cover）、调研（research 六件）、BGM（music skill 路由）的完整配方在彼处。"
---
# video · video_tools v2 内核 skill（路由索引 + 迭代回路）

> **内核向**：本文件是机制文档的操作路由索引。机制/契约/基准留本 skill 与 docs/（任何产线通用）；产线配方（六阶段创作流程/拍摄规范/voice-ime 范式/发布链/BGM/封面）在 **`templates/tech-talk/`** 工作流包。开发者分析见 `docs/DEV.md`。
> **接到任务先查路由表去读对应 reference 再动手**；本文件是「路线图」，字段语义与坑表以 references 为准。创作起手、页面拆分与多人分工，先读 [HTML 创作组织](references/gfx.md#html-创作组织)。

## 架构与组件入口

内核 `vtx/` 永不可关；`apps/` / `plugins/` 提供可卸载组件，`templates/` 承载工作流与开发范本。分层判据与协议见 [ARCHITECTURE](../ARCHITECTURE.md#0-判据与四层架构)，仓库布局见 [README](../../README.md#四层架构一页)，各组件职责见 [三层路由](../../README.md#三层路由)，安装态与键面清单见 [组件目录](../catalog.md)。

## 迭代回路速查

修改前留快照；修改后用 `vtx diff` 定位影响，再按 [runbook R1-R4](references/runbook.md) 选择局部验证：

| 改动 | 局部验证 |
|---|---|
| 音频 | validate → prepare → audio --report；检查受影响窗口 |
| 画面 / gfx | validate → gfx 准备页面缓存 → video --draft --range；窗口来源 range/clip/track 三选一 |
| 字幕 | subs check → subs preview；再按画面路径核对实际音画 |

局部验证通过后，正式交付统一 `vtx -t <timeline.json> all`。缓存可复用不等于旧阶段成功可继承；完整链负责当前 prepare/gfx/video/audio/mux/qc 及启用插件的 finish。区间预览不走正式回执门、不替代成片验证；不会自动渲染缺失 gfx。判断整片混音前须有当前输入的 prepare/audio 成功，否则区间可能采用窗口自测增益，即使只改字幕或 HTML 也不能据其响度判断成片。报告对比使用 `vtx diff <旧报告> <新报告>`，没有 `qc diff` 子命令。

阶段选项写在子命令后。`--report/--report-only` 仅 audio 可用、相互互斥且不接受 dry-run；`--gpu` 仅 video 可用。具体命令、写入位置与失败处理只在 runbook 维护；耗时记录见 [DEV](../DEV.md)。

## 领域路由表
| 用户任务 / 关键词 | 先读 | 职责与边界 |
|---|---|---|
| 剪辑 edit、切素材、多轨合成、画中画、烧字幕、混音、渲染、QC、缓存治理；timeline.json / vtx | `references/edit.md` | timeline 契约、CLI 全阶段（validate/audit/prepare/gfx/video/audio/mux/qc/gc）、音频工程、渲染坑表、三大生成模块接口 |
| 动效 gfx、纯 MG、多角色/场景、透明叠加、动效页/图表/片头片尾/overlay 卡、写 gfx/*.html | [gfx 创作组织与协议](references/gfx.md#html-创作组织)，按需选 [MG 风格工程](../../templates/mg-styles/README.md) | HTML 创作组织、帧驱动/依赖协议、CDP/Canvas/静态跨度、通用技术检查与按需样式；D22 仅限 tech-talk 工作流 |
| 显式 SVG 序列、Inkscape 渲染 | [SVG 序列入口与限制](references/gfx.md#显式-svg-序列) | 独立于浏览器同页 SVG 的路线 |
| 实拍素材处理、音视频同步、降噪、响度、验证门 | `references/edit.md` + `references/runbook.md` + 各脚本 `--help` | 管线前置素材与音频处理 |
| 标准修改任务（改音量/改编排/改字幕/改 gfx 页/全冷重渲）、棒次 | `references/runbook.md` | R1-R4、R2s 与子智能体棒次协议；R5/R6/R7 产线配方见 `templates/tech-talk/references/runbook-r5r6r7.md` |
| 抠像/背景合成 matting、换背景、绿幕、背景替换、Magic Mask、matte、带 alpha 前景合成 | `apps/matting/matting.md`（app 专属） | 真人实拍抠像（达芬奇主力、自研链备用）→ alpha ProRes4444 → 背景合成全流程 |
| 科技口播全流程 SOP / 六阶段、voice-ime 范式 | `templates/tech-talk/SKILL.md` | 制作总览→六阶段→音频支线→SOP 闭环→uses 声明表；动效编排范式见 `templates/tech-talk/references/voice-ime-paradigm.md` |
| 新选题、调研 research 可行性、对标分析、生成 01-brief | 先读 `templates/tech-talk/SKILL.md`，再 `templates/tech-talk/references/research.md` | R0-R6 多智能体调研 |
| 制作/修改 B 站投稿封面 cover、封面文案、封面裁切预览 | `templates/tech-talk/references/cover.md` | B 站封面产线 |
| 实拍怎么拍、拍摄规范（机位/打光/录音/双系统） | `templates/tech-talk/references/shooting.md` | 实拍规范 |
| BGM/配乐生成、音色、乐谱 | **`music` skill** | 挂载后分轨审计包配方见 `templates/tech-talk/references/runbook-r5r6r7.md` R6 |

## 推荐方式与全局铁律
1. **委派模式**：复杂任务按 [HTML 创作组织](references/gfx.md#html-创作组织) 定模块边界后拆分；按任务需要保留结论与证据，交接要求见 [runbook](references/runbook.md#主控-子智能体棒次协议)；长命令按执行环境支持的后台会话与轮询机制运行。
2. **验证门优先**：validate（机制）+ content_gates（内容）+ blackdetect（零黑帧）+ 视觉抽帧（技术结论按实际渲染与相应证据判定，DOM 相等不能证明像素相等）；艺术审查与用户视觉确认按 [创作交付检查](references/gfx.md#html-创作组织) 分别记录。
3. **迭代用 draft，正式交付用完整 all**；单独 video 可选 `--gpu`。缓存身份见 references，收尾先 `gc --dry` 查看清单，回收另按授权执行。
4. **剪辑永远走 timeline.json + vtx**：不手写 ffmpeg 剪辑命令；改 timeline 必重跑 validate。
5. **gfx 页面必须帧驱动**：动画状态 = 帧号 f 的纯函数，禁 CSS animation/transition、Date.now()、Math.random()、rAF 时序。
6. **tech-talk 调研走 R0-R6 编排**：主编只编排不采集，采集与评分分离，红队质疑必逐条回应。
7. 时间量一律**整数帧**；时基 30fps（≠29.97）、48kHz（每帧整 1600 采样）；区间 `[in, out)` 左闭右开。
8. **修改任何程序或文档一律改 video_tools 仓**（live 挂载点全是符号链接）。

## CLI 速查（组件真实路径）
| 操作 | 命令 |
|---|---|
| 渲染前校验（必跑） | `python3 <video_tools>/vtx/vtx.py -t <项目>/timeline.json validate` |
| 全流程渲染 | `... vtx.py -t <项目>/timeline.json all`（完整链含 finish；截断语义见 runbook） |
| 草稿预览 | `... video --draft [--frames N]`；`... mux --draft`；区间 `... video --draft --range <A> <B>` |
| 正式 GPU | `... video --gpu` |
| 字幕检查（渲染前必跑，秒级） | `... vtx.py -t <项目>/timeline.json subs check`（对齐漂移/CPS/重叠/行宽，报告 out/subs_check.json） |
| 字幕预览（秒级看效果） | `... vtx.py -t <项目>/timeline.json subs preview <秒\|f帧号>...`（改字幕抽查首选，单张 ≤5s） |
| 项目脚手架 | `bash <video_tools>/scripts/new_project.sh <目录> [时长秒]` |
| 内容验证门 | `env python3 <video_tools>/apps/gates/content_gates.py -t ... [--project-gates 门.json]` |
| 4K 投稿断言 | `bash <video_tools>/apps/publish/check_4k.sh <成片> ...` |
| B 站投稿封面 | `env python3 <video_tools>/apps/cover/make_cover.py all --out <项目>/cover --bg-src <imgen原图> --title "…" --sub "…"`（规格/QC 见 `templates/tech-talk/references/cover.md`） |
| Blender 审片工程 | `blender --background --factory-startup --python <video_tools>/apps/blender-bridge/json2blend.py -- <项目>/timeline.json <项目>/preview.blend`（json2blend/verify_blend 须由 blender 解释器运行，python3 直跑会 ModuleNotFoundError） |
| Blender 自动导出（v1.2） | timeline 顶层 `"blender_bridge": true`（或 `{out, verify}`）→ `vtx all` 收尾自动导出 `<项目>/preview.blend`（plugins/blender-bridge，免手敲命令；blender 缺失收尾报错不吞） |
| BGM 音轨直引工程 JSON（v1.2） | sources[] 加 `{"id":"bgm_main","path":"<music_tools 工程>.json","bgm":{"output":"master"}}`（output 双档：`master` 母带终态 / `premaster` 母前档，plugins/bgm-render `OUTPUTS`；亦可 `stem:<轨名>` 取分轨总线 bus.wav，各 stem 挂独立轨自行混音）→ validate/prepare 自动 `bgm render` → 48k stereo 入内容寻址缓存（plugins/bgm-render；一工程共享缓存只渲一次多源派生，工程/$ref/参数变更自动重渲） |
| BGM 分轨审计包 | `python3 <video_tools>/apps/blender-bridge/audit_bundle.py -t <项目>/timeline.json` |
| 音视频同步 / 降噪 | `env python3 <video_tools>/apps/sync/sync_audio.py …` / `env python3 <video_tools>/plugins/denoise/denoise_voice.py …`（响度限幅已并入 vtx audio，无独立步骤） |
| 缓存治理 | `... gc --dry` / `gc` / `df` / `snapshot` |
| 时间线人审预览 | 浏览器打开 `<video_tools>/vtx/preview.html` |
| 环境三检 / 分层清单 | `env VTX_DOCTOR_OFFLINE=1 python3 <video_tools>/vt.py doctor` / `vt.py apps|plugins|templates list` |

`<video_tools>` = 本仓根（live 注册根 `~/.agents/skills/video/` 下六件链接：`SKILL.md`、`references`、`assets` 指向本仓 `docs/skill/` 对应件，`vtx`、`scripts`、`templates` 指向本仓同名目录）。完整语义与坑表 → `references/edit.md`，各脚本 → `--help`。

## 产线衔接
- 科技口播产线（调研→实拍→动效→渲染→发布）完整 SOP → `templates/tech-talk/SKILL.md`；视频项目工作区在 `~/文档/04_bilibili视频/`（各期项目文件夹 + `_library/` 方法论/资产 + `_archive/`）。
- 音频/BGM 产线 → `music` skill（music_tools / music_project 两仓）；视频是 BGM 的消费方（timeline 音轨直接引用产物，正式版挂载后走分轨审计包交付门）。
- 环境初始化与依赖矩阵 → 本仓 README（`env python3 vt.py init` 自检）。
