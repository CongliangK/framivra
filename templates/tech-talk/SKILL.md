---
name: tech-talk
description: 科技口播 B 站视频制作工作流范本（video 内核之上的产线 SOP）：选题调研、脚本、实拍、动效、时间线编排、渲染 QC 到投稿的全链路。当用户要做科技类口播/B站视频、提到 tech-talk、选题调研、B站投稿封面时使用。
---

# tech-talk · 科技口播 B 站视频制作范本（工作流包）

> **本包是 video 内核之上的工作流范本**：科技口播视频从选题调研到 4K 投稿的全链路 SOP、阶段配方与已验收模板资产。
> **机制契约见内核 skill**（`docs/skill/SKILL.md` 路由索引 + `docs/skill/references/`）：timeline.json 契约、vtx CLI 全阶段、gfx 帧驱动协议、D22 设计铁律、渲染坑表、缓存治理等机制文档一律不在本包重复——本包只回答「这条产线按什么顺序用哪些件」。
> 调用约定：内核命令 `python3 <video_tools>/vtx/vtx.py …`（或挂载路径 `~/.agents/skills/video/vtx/vtx.py`）；产线 app 一律走 `apps/<name>/`、插件走 `plugins/<name>/` 真实路径。

## 路径解析（先做）

`<video_tools>` 指 Framivra **真实仓根**，不是技能挂载目录。从挂载入口起步时先解析源件，再读取源件正文；后续 Markdown 相对链接按源件所在目录解析，`apps/`、`plugins/`、`docs/` 按仓根解析，避免客户端在软链接展开前折叠 `../`。

以下只读定位；自定义挂载或源码入口可用 `TECH_TALK_SKILL` 指定该 `SKILL.md` 路径：

```bash
env python3 - <<'PY'
import os
from pathlib import Path
entry = Path(os.environ.get('TECH_TALK_SKILL', '~/.agents/skills/tech-talk/SKILL.md')).expanduser()
source = entry.resolve(strict=True)
print('先读取源件:', source)
print('<video_tools>:', source.parents[2])
PY
```

## 制作范本总览

本包面向口播产线；纯 MG 起手直接读内核 [HTML 创作组织](../../docs/skill/references/gfx.md#html-创作组织)，不把本包的拍摄阶段、轨道骨架或模板页数当作必需结构。

- **一条主链**：调研立项 → 稿子与结构 → 实拍与素材 → 动效页 → 时间线编排 → 渲染与 QC → 发布链（4K/封面/冻结）。
- **两条音频支线**：口播/同期声支线（每期必经）与 BGM/音效支线（外部 music skill 可用时路由；缺失时按 [已有资产与受阻处理](references/audio-mixing.md#六授权与资产)；分轨审计包仍是交付门）。
- **产线挂载件**：见文末「uses 声明表」——12 件 app 全量使用；denoise 已列入 uses_plugins，按素材需要选择处理，duck-static 列入 optional_plugins（键声明进 timeline 才生效）。
- 编排层方法论（开场模板/演示对位/字幕策略/结尾模板）已提炼收编为本包 `references/narrative.md`（原外部报告 `~/文档/04_bilibili视频/_library/reports/编排方法论-科技口播视频.md` 为历史源件）；gfx 页面写法（帧驱动协议 + D22）读内核 `docs/skill/references/gfx.md`，成熟页面范式速查见本包 `references/voice-ime-paradigm.md`。

## references/ 知识文档路由（2026-10-03 收编，宏观经验自包含、跨项目可用）

| 文档 | 管什么 | 何时读 |
|---|---|---|
| `references/production-workflow.md` | **九阶段全流程总览**（调研→脚本→结构预算→拍摄→A-roll→gfx→编排→音频→QC/封面/冻结），每阶段入口工具/配方号/规则/验证门 | 新项目起手、接手中途项目先读 |
| `references/narrative.md` | **叙事逻辑**：开场钩子/正文论证结构/演示对位三原则/字幕与口播节奏/结尾收束/验证门 + 硬约束单列（口播无缝/片尾同话题/无口播不留演示等） | 写稿、排时间线、审片定性前 |
| `references/voice-style.md` | **文风规范**（决策级，提炼自 voice-ime 用户精调锁定稿）：句式节奏/零语气词零反问/人称口径/数字口径/禁忌词表 + 速查卡 15 条硬规则与初稿自检清单 | 写口播稿前必读、交稿前跑自检清单 |
| `references/visual-baseline.md` | **色调与视觉宏观基准**：禁深蓝/白底暖色/背景板原则/pip 规范/封面 D50/视觉判读证据原则（数值以 `assets/gfx-templates/_tokens.md` 为准） | gfx 设计、封面、视觉验收前 |
| `references/aroll-pipeline.md` | **A-roll 素材全链处理手册**（aup3→同步→替换→降噪→真空切割→构图统一，参数化通用版） | 实拍素材入库阶段 |
| `references/audio-mixing.md` | **混音/响度/避让基准**：-14 LUFS 两遍静态增益链、D47 避让量化口径、降噪链、双系统同步、授权红线 | 音频阶段与任何 duck/音量改动前 |
| `references/shooting-guide.md` | **实拍完整通用手册**（相机/机位/打光几何/录音/提词/同步纪律；`shooting.md` 为其速查版） | 拍摄准备阶段 |
| `references/research.md` / `cover.md` / `runbook-r5r6r7.md` / `voice-ime-paradigm.md` / `shooting.md` | 既有件：调研编排 / 封面产线 / R5-R7 配方 / gfx 范式速查 / 拍摄速查 | 按各自场景 |

## 六阶段创作流程

> **新项目一键开局**（生成目录树 + 工程文档四件 + 01-brief + 可 validate 的 timeline 骨架 + gates/mixing 预设，幂等可重跑）：
> ```bash
> bash <video_tools>/templates/tech-talk/scripts/new_project.sh <项目目录> --title "视频标题" --id 项目代号
> ```

- **① 调研立项** → `references/research.md`（R0-R6 多智能体编排 + score_topic 量化评分，产出 `01-brief.md`；模板 `assets/brief-template.md`，量化分与红队质疑是「做不做」的依据）。采集/评分脚本在 `apps/research/`（bilibili / github_repos / score_topic 等）。
- **② 稿子与结构**：01-brief 定稿后写脚本分段表（**写口播文案前必读 `references/voice-style.md` 文风速查卡，交稿前跑其自检清单**；**锁定稿冻结后不再擅改**，实录差异走变更单）。产出 02-script（分段/字幕真值基准）+ 00-structure（结构帧预算）+ 提词稿。**工程文档模板四件在 `templates/`**：`00-structure.md`（段×帧数×字数预算，5.5 字/s 换算）/ `02-script.md`（分段表+冻结纪律）/ `03-assets-plan.md`（五线素材判定清单）/ `06-teleprompter.md`（提词分镜+录制检查表）。需要 TTS 占位时 → `apps/tts/`（spec.json 驱动，实录替换链见下文音频支线）。
- **③ 实拍与素材**：拍摄规范（4K 30p / 相机悬屏上 / 双系统录音 / 长镜连续录制）→ `references/shooting.md` 速查（完整通用手册=`references/shooting-guide.md`，含 voice-ime/07-shooting-guide 装备版指针）。素材入库链：
```bash
env python3 <video_tools>/apps/aroll/aup3_extract.py 口播.aup3   # Audacity 工程提取
env python3 <video_tools>/apps/aroll/trim_takes.py ...            # VAD 掐头去尾+废片判定（ASR 配对）
# 双系统同步：按下方 sync 组件用法执行（GCC-PHAT 无标记同步）
# 音轨替换 → 口型感知全局 +200ms（默认）→ 降噪链（plugins/denoise，可选）→ 头尾真空切割 → 构图统一
# 手册：<video_tools>/templates/tech-talk/references/aroll-pipeline.md
```
同步所需解释器、完整命令与报告参数见 [sync 组件用法](../../apps/sync/README.md#开启步骤)。
- **抠像支线**：需要「真人出现在新背景前」的镜头 → 抠像 + 背景合成，全流程见 `apps/matting/matting.md`（resolve_matte 主力，产物带 alpha ProRes4444；背景图走 `apps/imgen/` bg4k 硬接口）。
- **④ 动效页** → 先按内核 [HTML 创作组织](../../docs/skill/references/gfx.md#html-创作组织) 定页内模块/独立素材边界，再读帧驱动协议 + D22 设计铁律；**优先复用本包 `assets/gfx-templates/` 已验收模板**（六件参数化页 + `_tokens.md` 设计基准；场景→模板映射表与使用步骤见 `assets/gfx-templates/INDEX.md`；改 CONST 数据不碰机制代码）；新页三件套=manifest+data-sources+自查脚本；实例页参考 `assets/gfx-examples/`。
- **⑤ 时间线编排**：新项目**优先运行上方脚手架**（写入工程、占位素材并改写路径）；手工复制 `assets/presets/timeline.skeleton.json` 时必须同时将同级 `placeholders/` 整目录复制到项目根，再 validate（轨级 duck_strategy/duck_params 示范与实例化步骤见骨架 `_meta`），再按帧预算铺真实编排（契约速览与「素材放哪条轨」速查表见内核 `docs/skill/references/edit.md`）。**编排完先开 `preview.html` 人审，再 validate**。批量重排写幂等脚本 + `vtx snapshot` 留快照。口播字幕锚定 → `apps/asr/`（`asr_cli.py timeline` 就地合并 subtitles[]）。
- **⑥ 渲染与 QC**：
以下渲染命令会更新项目 `cache/`、`out/`，正式渲染可能替换已有成片；先确认项目与输出位置。draft 只用于画面迭代，局部预览不能替代完整 `all`；区间音画预览及其当前输入的 prepare/audio 前置见内核 [runbook R2](../../docs/skill/references/edit.md#r2--时间线编排clip-窗口--轨道--字幕)。
```bash
python3 <video_tools>/vtx/vtx.py -t <项目>/timeline.json validate        # 硬门，必跑
python3 <video_tools>/vtx/vtx.py -t <项目>/timeline.json gfx             # 准备当前页面缓存，无 gfx 轨时无页面任务
python3 <video_tools>/vtx/vtx.py -t <项目>/timeline.json video --draft   # 540p 画面迭代预览（可加 --frames N）
python3 <video_tools>/vtx/vtx.py -t <项目>/timeline.json all             # 全流程正式渲染 → out/final.mp4
python3 <video_tools>/vtx/vtx.py -t <项目>/timeline.json gc              # 收尾回收缓存（先 --dry-run 看）
env python3 <video_tools>/apps/gates/content_gates.py -t ... --project-gates <项目门>.json   # 内容级验证门（项目门骨架=`assets/presets/gates.json`，字段写法见其 _doc）
bash <video_tools>/apps/publish/check_4k.sh out/final_4k.mp4 ...         # 4K 投稿版断言（帧数门自动从 timeline 推导）
```
  发布链：母带 1080p30 → 4K lanczos 放大投稿版（`apps/publish/export_4k.py` 8 段并行 nvenc p4，进 B 站高码率池）→ FREEZE 冻结点 + CHANGELOG 变更单（**脚本化：`scripts/freeze.py` 一键生成/追加多版本节，禁手写**，qc 段人工核对补写）→ PUBLISH-PACK 物料骨架（`scripts/publish_pack.py`，TODO 段人工补写；冻结/变更单必须落项目目录 git 区，严禁写 cache/，D52 实案）。验证链与 4K 发布链的历史测量及适用条件见 [DEV 性能基准](../../docs/reports/development-history.md#一性能基准)，不据旧项目数字承诺当前机器耗时。

## 音频创作流程（两条支线）

- **口播/同期声支线**（每期视频必经）：① 录音=电容麦/boom 同期声 + 相机内录麦常开（=同步参考轨），长镜连续录制（详见 `references/shooting.md`）；② 同步=`apps/sync/sync_audio.py`（GCC-PHAT 相关法，无需拍掌/时间码）；③ 降噪=可选 [denoise 插件](../../plugins/denoise/README.md)（生产链、适用域与 timeline `sources[].denoise` 用法以组件文档为准；备选算法见 `references/shooting-guide.md`）；④ 入轨=timeline voice 轨事件（画面/声音同源，口型同步由构造保证；口型感知全局 +200ms）；⑤ 混音=bed 避让可选插件 `duck_strategy` 静态分段方案（`plugins/duck-static/`，与 `duck:true` 互斥）或 sidechaincompress 动态避让（启用前必做 A/B 响度对照，见内核 edit.md）+ **vtx audio 内置两遍静态增益到 -14 LUFS + alimiter 限幅终态**（post_loudness 已并入 build_audio 尾部，v2 §1.2，无独立步骤；量化口径参数化预设=`assets/presets/mixing.json`，含 duck 静态/sidechain 备用/降噪链/响度门数值），**终检必测编码后解码 TP**；⑥ 验证=`apps/gates/content_gates.py`（G1 口播−间隙响度 / G2 全片 LUFS / G3 解码域 TP（内核 qc 同阈复检）/ G7 零黑帧 / G10 帧数封装 + 项目门）。
- **BGM/音效支线** → 仅在当前技能清单可用时路由进 **`music` skill**，缺失时按 [授权与资产](references/audio-mixing.md#六授权与资产) 处理；可用时流程如下（挂载 `~/.agents/skills/music/`，本体 music_tools 仓）：音色契约先行门 → 产线选路（公有领域改编=默认路线 / ACE-Step 时长精确 / synthlib 代码合成）→ 渲染+QC → **挂载进视频 timeline 后必做分轨审计包**（`apps/blender-bridge/audit_bundle.py`，用户 Blender 逐轨审听=交付门；配方见 `references/runbook-r5r6r7.md` R6）。授权红线：**商业音乐绝对不用**；音效宽松获取、AI 生成积极使用；现成资产在 `_library/assets_audio/`。

## 全链路闭环（推荐 SOP）

```
01-brief（调研拍板）→ 02-script 锁定稿 + 00-structure 帧预算 → 实拍（长镜+双系统）
  → A-roll 素材链（同步/降噪/构图）→ assets/aroll_final → gfx 动效页（模板库起步，逐页验收）
  → timeline.json 编排（preview.html 人审 → validate）→ --draft 迭代
  → 用户 Blender 审片（apps/blender-bridge/json2blend 预览工程）→ 意见修订（幂等脚本+snapshot）
  → 正式渲染 all → content_gates + check_4k 验证门 → 音频耳验（sfx/床乐）
  → BGM 分轨审计包（apps/blender-bridge/audit_bundle.py）→ FREEZE 冻结（md5）
  → 4K 投稿版（apps/publish/）→ 发布物料 + 投稿封面（references/cover.md，apps/cover/）
  → 收尾：vtx gc + snapshot 归档
```

迭代遵循**修复工作法**：先问后做不脑测 / 在时间轴编排层定性（Blender=审片界面）/ 区分「编排对实现错」vs「编排本身错」/ 用户权威文本 > 代理回读推断。标准修改配方（R1-R4 机制侧见[内核编辑配方](../../docs/skill/references/edit.md)；R5 发布链 / R6 分轨审计 / R7 封面见 `references/runbook-r5r6r7.md`）。

## 产线衔接与外部资产

- 视频项目工作区在 `~/文档/04_bilibili视频/`（现状快照以其 AGENTS.md 为准）：每期视频一个文件夹 + `_library/`（assets_audio / corpus / reports 方法论 / blender_bridge 符号链接）+ `_archive/`。素材、timeline、gfx、out/ 都在**各期项目文件夹**内。
- 音频/BGM 产线与外部 `music` 可用性检查 → [授权与资产](references/audio-mixing.md#六授权与资产)；视频消费已有产物，正式版挂载后仍走分轨审计包交付门。
- 环境初始化与依赖矩阵（ffmpeg/libfdk_aac/Chrome/Blender/NVENC）→ video_tools 仓 `env python3 vt.py init` 自检 / `env VTX_DOCTOR_OFFLINE=1 python3 vt.py doctor` 三检。

## uses 声明表（产线用法定，非最小依赖）

| 层 | 件 | 本产线角色 |
|---|---|---|
| 内核 | `vtx/` + `docs/skill/` | 渲染 CLI 与机制契约（本包不复制机制文档） |
| app | `apps/research/` | 选题调研采集与评分（R0-R6 采集侧） |
| app | `apps/aroll/` | A-roll 素材链（aup3 提取 / VAD 掐头去尾） |
| app | `apps/sync/` | 双系统同步与实录 take 替换 |
| app | `apps/asr/` | 口播字幕产线（subtitles[] 锚定） |
| app | `apps/tts/` | 口播占位语音（spec.json 贴预算合成） |
| app | `apps/imgen/` | 背景图与封面底图生成（bg4k 硬接口） |
| app | `apps/matting/` | 真人抠像与背景合成（resolve_matte 主力） |
| app | `apps/bgm/` | BGM 需求路由声明（本体 music_tools 仓） |
| app | `apps/blender-bridge/` | Blender 审片预览 + BGM 分轨审计包 |
| app | `apps/cover/` | B 站投稿封面产线（make_cover 一条龙） |
| app | `apps/gates/` | 发布前内容级复检（G1/G2/G7/G10+项目门） |
| app | `apps/publish/` | 4K 投稿版发布链与断言 |
| plugin（可选） | `plugins/denoise/` | 口播降噪（sources[] 级键；已登记 uses_plugins，按素材选择处理） |
| plugin（可选） | `plugins/duck-static/` | 床乐静态分段避让（duck_strategy 键；与 duck:true 互斥） |

声明数据源 `skill.json`（doctor 工作流模板段逐件在册校验，JSON 保留 skills 字段）。修改本包任何程序或文档一律改 video_tools 仓（live 挂载点全是符号链接）。
