---
name: video
description: "B站视频产线一体化 skill：选题调研（R0-R6 多智能体编排出 01-brief）→ HTML 帧驱动动效（片头标题/动态图表/导航卡/端板）→ timeline.json + vtx 帧精确剪辑合成渲染 QC（多轨叠加/画中画/字幕烧录/混音/响度）→ A-roll 素材处理与音频链（双系统同步/降噪/响度归一/验证门）。Use whenever 用户提出新视频选题、要求调研视频主题/分析同类视频/判断选题可行性/生成 01-brief，或要求制作视频动效/动态图表/字幕条/片头片尾/透明图层，或要求剪辑视频、合成动效与实拍、切分素材、烧字幕、混音轨、渲染成片、新建视频项目，或要求处理实拍素材/双系统音视频同步/口播降噪/音频响度/混音验证门，或提到 timeline.json / vtx / gfx 页面 / 帧驱动动画 / 01-brief / 视频调研 / 对标分析 / 选题池 / B站视频制作 / A-roll / sync_audio / denoise_voice / content_gates。"
---
# video · B站视频产线 skill（索引 + 全链路）
> **使用者向**：如何用本 skill 完成视频制作。当前行为细节见 `references/`，开发者分析见 `docs/DEV.md`。
**接到任务先查路由表去读对应 reference 再动手**；本文件是「路线图」，字段语义与坑表以 references 为准。
## 迭代回路速查（改 timeline 前必读）
```
改 clip/字幕/音频前：vtx diff → vtx video --draft --range → （动了音频）vtx audio --report → vtx subs check/preview → vtx qc diff → 确认后 vtx all
```
| 命令 | 何时用 | 产物 / 耗时 |
|---|---|---|
| `vtx diff <旧timeline> <新timeline>` | 任何 timeline 改动的第一步，看影响面与推荐路径（generator/patch_script/manual_whitelist） | 控制台 + `--json out/tl_diff.json`；秒级 |
| `vtx video --draft --range <A> <B>` [--clip ID \| --track NAME] | 改了 clip/字幕后只看改动区间效果（时间点支持 秒/时间码/帧号f；gfx 冷缓存会提示先跑 vtx gfx） | `cache/preview/range_*.mp4`（含音频）；耗时双口径：纯回看窗（无改动，rseg 全命中）~5s，改动窗（窗内 rseg 内容键失效需重编码，含窗口混音）显著更长（详见 DEV.md「迭代回路基线」） |
| `vtx audio --report` | 改了音轨后快速判级（LUFS/TP/duck/空洞），FAIL 退出码 1 | 控制台 + `out/audio_report.json`；秒级-数十秒（渲 3 个 stem） |
| `vtx subs check` / `subs preview` | 字幕改动后必跑：check 全绿（<1s）→ preview 抽可疑条目（单张 ≤5s） | `out/subs_check.json` / 单张图 |
| `vtx qc diff`（走 `vtx diff` 入口） | 局部验证通过后与上一版报告对比，确认只有预期项翻转 | 控制台；秒级 |
| `vtx all` | 上述回路全绿后的正式全量渲染 | `out/final.mp4`；voice-ime 温态全链基线 100.8s |

**硬性规则**：任何 timeline 修改，全量 `vtx all` 前必须先走上述局部验证回路；区间渲染只进 `cache/preview/`，不产生正式产物（out/ 不受影响）。
## 领域文档路由
| 领域 | 文档 | 管什么 |
|---|---|---|
| 调研 research | `references/research.md` | 多智能体选题调研（R0-R6 编排、采集脚本、独立评分、红队质询），产出 01-brief.md |
| 动效 gfx | `references/gfx.md` | HTML 帧驱动透明动效页（标题卡、图表、导航卡、overlay 卡、端板）的设计协议、模板库与样式模式库 |
| 剪辑 edit | `references/edit.md` | timeline.json 契约 + vtx CLI 全阶段（validate/audit/prepare/gfx/video/audio/mux/qc/gc）+ 音频工程 + 渲染坑表 + 缓存治理 |
| 修改配方/棒次 | `references/runbook.md` | 标准修改配方集 R1-R6 与子智能体棒次协议 |
| 实拍/音频脚本 | `scripts/`（多数脚本 `--help`；score_topic 用 `--rubric`） | sync_audio / denoise_voice / repack_voice / post_loudness / check_4k / content_gates / bed_static_duck / new_project 等 15 件 |
| 用户任务 / 关键词 | 先读 |
|---|---|
| 新选题、调研可行性、对标分析、生成 01-brief | `references/research.md` |
| 做动效页、动态图表、片头片尾、overlay 卡、写 gfx/*.html | `references/gfx.md` |
| 剪辑、切素材、多轨合成、画中画、烧字幕、混音、渲染、QC、缓存治理；提到 timeline.json / vtx | `references/edit.md` |
| 实拍素材处理、音视频同步、降噪、响度、验证门 | `references/edit.md` + 下文「音频创作流程」+ 脚本 --help |
| BGM/配乐生成、音色、乐谱 | **路由到 `music` skill**，见「音频创作流程·BGM 支线」 |
## 视频创作流程（六阶段）
> 编排层方法论（开场模板/演示对位/字幕策略/结尾模板）读 `_library/reports/编排方法论-科技口播视频.md`。
- **① 调研立项** → `references/research.md`（R0-R6 编排 + score_topic 量化评分，产出 `01-brief.md`；量化分与红队质疑是「做不做」的依据）。
- **② 稿子与结构**：01-brief 定稿后写脚本分段表（**锁定稿冻结后不再擅改**，实录差异走变更单）。产出 02-script（分段/字幕真值基准）+ 00-structure（结构帧预算）+ 提词稿。
- **③ 实拍与素材**：拍摄规范（4K 30p/相机悬屏上/双系统录音）见 `voice-ime/07-shooting-guide.md`。素材入库链：
```bash
python3 <video_tools>/aroll/aup3_extract.py 口播.aup3      # Audacity 工程提取
python3 <video_tools>/aroll/trim_takes.py ...              # VAD 掐头去尾+废片判定（ASR 配对）
python3 <video_tools>/scripts/sync_audio.py 相机轨 话筒轨   # GCC-PHAT 无标记同步（报告含偏移/ppm/verdict）
# 音轨替换 → 口型感知全局 +200ms（默认）→ 降噪链 → 头尾真空切割 → 构图统一
# 手册（可照抄）：_library/reports/A-roll实拍素材处理流程-2026-09-07.md
```
- **④ 动效页** → `references/gfx.md`（帧驱动协议；**优先复用 `assets/gfx-templates/` 七件已验收模板**，改 CONST 数据不碰机制代码；新页三件套=manifest+data-sources+自查脚本）。
- **⑤ 时间线编排**：手写/脚本生成 `timeline.json`（契约速览与「素材放哪条轨」速查表见 `references/edit.md`）。**编排完先开 `preview.html` 人审，再 validate**。批量重排写幂等脚本 + `vtx snapshot` 留快照。
- **⑥ 渲染与 QC**：
```bash
python3 <skill>/vtx/vtx.py -t <项目>/timeline.json validate        # 硬门，必跑
python3 <skill>/vtx/vtx.py -t <项目>/timeline.json --draft video   # 540p 迭代预览（可加 --frames N）
python3 <skill>/vtx/vtx.py -t <项目>/timeline.json all             # 全流程正式渲染 → out/final.mp4
python3 <skill>/vtx/vtx.py -t <项目>/timeline.json gc              # 收尾回收缓存（先 --dry 看）
python3 <video_tools>/scripts/content_gates.py -t ... --project-gates <项目门>.json   # 内容级验证门
python3 <video_tools>/scripts/check_4k.sh out/final_4k.mp4 ...     # 4K 投稿版断言（帧数门自动从 timeline 推导）
```
  发布链：母带 1080p30 → 4K lanczos 放大投稿版（进 B 站高码率池）→ FREEZE 冻结点（md5 清单）→ PUBLISH-PACK 物料。全冷缓存下渲染全链（含 4K）量级预期约 6 分钟。
## 音频创作流程（两条支线）
- **口播/同期声支线**（每期视频必经）：① 录音=电容麦/boom 同期声 + 相机内录麦常开（=同步参考轨），长镜连续录制（详见 07-shooting-guide §七）；② 同步=`scripts/sync_audio.py`（GCC-PHAT 相关法，无需拍掌/时间码）；③ 降噪=`scripts/denoise_voice.py`（陷波 80Hz 高通+工频 notch → Resemble 降噪级 → 48kHz；备胎 MossFormer2）；④ 入轨=timeline voice 轨事件（画面/声音同源，口型同步由构造保证；口型感知全局 +200ms）；⑤ 混音=bed 避让用静态分段方案 `scripts/bed_static_duck.py`（新项目默认）+ 两遍静态增益到 **-14 LUFS** + `scripts/post_loudness.sh` 限幅兜底，**终检必测编码后解码 TP**；⑥ 验证=`scripts/content_gates.py`（G1 口播−间隙响度 / G2 全片 LUFS / G3 解码域 TP / G7 零黑帧 / G10 帧数封装 + 项目门）。
- **BGM/音效支线** → 路由进 **`music` skill**（`~/.agents/skills/music/SKILL.md`，本体 music_tools 仓）：音色契约先行门 → 产线选路（公有领域改编=默认路线 / ACE-Step 时长精确 / synthlib 代码合成）→ 渲染+QC → **挂载进视频 timeline 后必做分轨审计包**（`blender_bridge/audit_bundle.py`，用户 Blender 逐轨审听=交付门）。授权红线：**商业音乐绝对不用**；音效宽松获取、AI 生成积极使用；现成资产在 `_library/assets_audio/`。
## 全链路闭环（推荐 SOP）
```
01-brief（调研拍板）→ 02-script 锁定稿 + 00-structure 帧预算 → 实拍（长镜+双系统）
  → A-roll 素材链（同步/降噪/构图）→ assets/aroll_final → gfx 动效页（模板库起步，逐页验收）
  → timeline.json 编排（preview.html 人审 → validate）→ --draft 迭代
  → 用户 Blender 审片（json2blend 预览工程）→ 意见修订（幂等脚本+snapshot）
  → 正式渲染 all → content_gates + check_4k 验证门 → 音频耳验（sfx/床乐）
  → BGM 分轨审计包（audit_bundle.py）→ FREEZE 冻结（md5）→ 4K 投稿版 → 发布物料
  → 收尾：vtx gc + snapshot 归档
```
迭代遵循**修复工作法**：先问后做不脑测 / 在时间轴编排层定性（Blender=审片界面）/ 区分「编排对实现错」vs「编排本身错」/ 用户权威文本 > 代理回读推断。
## 推荐方式与全局铁律
1. **委派模式**：复杂任务拆棒交子智能体流水执行，每棒落盘 REPORT；长命令子智能体内必须 run_in_background+轮询（同步等待会被看门狗杀）。
2. **验证门优先**：validate（机制）+ content_gates（内容）+ blackdetect（零黑帧）+ 视觉抽帧（**视觉模型读图只作线索，DOM/像素证据才是权威**）。
3. **迭代用 draft，正式才 --gpu**；缓存内容寻址，源变更自动换键，收尾习惯 `gc --dry` → `gc`。
4. **剪辑永远走 timeline.json + vtx**：不手写 ffmpeg 剪辑命令；改 timeline 必重跑 validate。
5. **gfx 页面必须帧驱动**：动画状态 = 帧号 f 的纯函数，禁 CSS animation/transition、Date.now()、Math.random()、rAF 时序。
6. **调研必须走 R0-R6 编排**：主编只编排不采集，采集与评分分离，红队质疑必逐条回应。
7. 时间量一律**整数帧**；时基 30fps（≠29.97）、48kHz（每帧整 1600 采样）；区间 `[in, out)` 左闭右开。
8. **修改任何程序或 skill 文档一律改 video_tools 仓**（live 挂载点全是符号链接）。
## CLI 速查
| 操作 | 命令 |
|---|---|
| 字幕检查（渲染前必跑，秒级） | `... vtx.py -t <项目>/timeline.json subs check`（对齐漂移/CPS/重叠/行宽，报告 out/subs_check.json） |
| 字幕预览（秒级看效果） | `... vtx.py -t <项目>/timeline.json subs preview <秒\|f帧号>...`（改字幕抽查首选，单张 ≤5s） |
| 项目脚手架 | `bash <skill>/scripts/new_project.sh <目录> [时长秒]` |
| 渲染前校验（必跑） | `python3 <skill>/vtx/vtx.py -t <项目>/timeline.json validate` |
| 全流程渲染 | `... vtx.py -t <项目>/timeline.json all` |
| 草稿预览 | `... video --draft [--frames N]`；`... mux --draft` |
| 正式 GPU | `... video --gpu` |
| 内容验证门 | `python3 <video_tools>/scripts/content_gates.py -t ... [--project-gates 门.json]` |
| 4K 投稿断言 | `bash <video_tools>/scripts/check_4k.sh <成片> ...` |
| 缓存治理 | `... gc --dry` / `gc` / `df` / `snapshot` |
| Blender 审片工程 | `blender --background --factory-startup --python <video_tools>/blender_bridge/json2blend.py -- <项目>/timeline.json <项目>/preview.blend`（json2blend/verify_blend 须由 blender 解释器运行，python3 直跑会 ModuleNotFoundError） |
| BGM 分轨审计包 | `python3 <video_tools>/blender_bridge/audit_bundle.py -t <项目>/timeline.json` |
| 音视频同步 / 降噪 / 响度 | `sync_audio.py` / `denoise_voice.py` / `post_loudness.sh` |
| 时间线人审预览 | 浏览器打开 `<skill>/vtx/preview.html` |
`<skill>` = `~/.agents/skills/video`（符号链接，历史路径继续可用）；`<video_tools>` = `~/文档/01_活跃项目/video_tools`。live 挂载点五项（SKILL.md / references/ / assets/ / vtx/ / scripts/）全部符号链接 → 权威本体 video_tools 仓；完整语义与坑表 → `references/edit.md`，各脚本 → `--help`。
## 产线衔接与外部资产
- 视频项目工作区在 `~/文档/04_bilibili视频/`（现状快照以其 AGENTS.md 为准）：每期视频一个文件夹 + `_library/`（assets_audio / corpus / reports 方法论 / blender_bridge 符号链接）+ `_archive/`。素材、timeline、gfx、out/ 都在**各期项目文件夹**内。
- 音频/BGM 产线 → `music` skill（music_tools / music_project 两仓）；视频是 BGM 的消费方（timeline 音轨直接引用产物，正式版挂载后走分轨审计包交付门）。
- 环境初始化与依赖矩阵（ffmpeg/libfdk_aac/Chrome/Blender/NVENC）→ video_tools 仓 README（`python3 init.py` 自检）。
