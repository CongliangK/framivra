---
name: video
description: "video_tools v2 内核 skill：时间线契约与 vtx 帧精确渲染 CLI（validate/diff/subs/audit/prepare/gfx/video/audio/mux/qc/gc/snapshot）+ HTML 帧驱动 gfx 动效协议 + 缓存治理 + 混音响度链与验证门。Use whenever 用户要求剪辑视频、合成动效与实拍、制作视频动效/动态图表/字幕条/片头片尾/透明图层、切分素材、烧字幕、混音轨、渲染成片、新建视频项目、缓存治理，或提到 timeline.json / vtx / gfx 页面 / 帧驱动动画 / 修改配方 / runbook / sync_audio / denoise_voice / content_gates / A-roll，或要求处理实拍素材/双系统音视频同步/口播降噪/音频响度/混音验证门，或要求视频调研、对标分析、选题池、B 站视频制作（→ 先读 skills/tech-talk/）。选题调研/01-brief/选题可行性、B 站投稿封面、BGM/配乐路由、科技口播全流程 SOP 等产线任务先读 skills/tech-talk/（本仓 skills/ 工作流包）——封面（make_cover）、调研（research 六件）、BGM（music skill 路由）的完整配方在彼处。"
---
# video · video_tools v2 内核 skill（路由索引 + 迭代回路）

> **内核向**：本文件是机制文档的路由索引与全仓分层地图。机制/契约/基准留本 skill 与 docs/（任何产线通用）；产线配方（六阶段创作流程/拍摄规范/voice-ime 范式/发布链/BGM/封面）在 **`skills/tech-talk/`** 工作流包。开发者分析见 `docs/DEV.md`。
> **接到任务先查路由表去读对应 reference 再动手**；本文件是「路线图」，字段语义与坑表以 references 为准。

## 三层架构一页（v2）

```
video_tools/
├── vtx/            ① 内核（永不可关）：timeline schema/validate/diff/subs/audit/snapshot
│                      + 渲染链 prepare/gfx/video/audio/mux + qc（含 TP）+ 混音响度终链
│                      （两遍静态增益 -14 LUFS + alimiter 限幅已并入 audio，§1.2）
│                      + 内容寻址缓存（gc/df/VERSIONS）+ pluginlib/api.py
├── skill/          ① 内核文档：本索引 + references/{edit,gfx,runbook}.md + assets/ 起手模板两件
├── apps/  (12 件)  ② 应用（产出独立素材，不进 timeline 管线；关停不影响主链）：
│                      asr / tts / imgen / matting / aroll / cover / research /
│                      blender-bridge / publish / bgm / gates / sync
├── plugins/ (4 件) ② 插件（扩展 timeline.json 词汇并钩进管线）：denoise / duck-static / blender-bridge（blender_bridge 键→all 收尾自动导出 .blend）/ bgm-render（sources[].bgm→音轨直引 music_tools 工程JSON自动渲染）+ registry.json
└── skills/         ③ 工作流包：tech-talk（科技口播 B 站视频制作范本；skill.json 声明 uses_apps）
```

分层判据（关停测试）：关停后主链产物不可信 → 内核；仍能可信出一期片 → apps/plugins 可卸载。apps/plugins 内部结构自包含（app.json/plugin.json + 代码 + README + tests），`env python3 vt.py doctor` 三检 / `apps|plugins|skills list`。

## 迭代回路速查（改 timeline 前必读）
```
改 clip/字幕/音频前：vtx diff → vtx video --draft --range → （动了音频）vtx audio --report → vtx subs check/preview → vtx qc diff → 确认后 vtx all
```
| 命令 | 何时用 | 产物 / 耗时 |
|---|---|---|
| `vtx diff <旧timeline> <新timeline>` | 任何 timeline 改动的第一步，看影响面与推荐路径（generator/patch_script/manual_whitelist） | 控制台 + `--json out/tl_diff.json`；秒级 |
| `vtx video --draft --range <A> <B>` [--clip ID \| --track NAME] | 改了 clip/字幕后只看改动区间效果（时间点支持 秒/时间码/帧号f；gfx 冷缓存会提示先跑 vtx gfx） | `cache/preview/range_*.mp4`（含音频）；耗时双口径：纯回看窗（无改动，rseg 全命中）~5s，改动窗（窗内 rseg 内容键失效需重编码，含窗口混音）显著更长（详见 DEV.md「迭代回路基线」） |
| `vtx audio --report` | 改了音轨后快速判级（LUFS/TP/duck/空洞），FAIL 退出码 1 | 控制台 + `out/audio_report.json`；秒级-数十秒（渲 3 个 stem） |
| `vtx subs check` / `subs preview` | 字幕改动后必跑：check 全绿（<1s）→ preview 抽可疑条目（单张 ≤5s）。check FAIL 先分辨存量与新增：`vtx diff out/subs_check_prev.json out/subs_check.json`（若留存上一版）或对改前 timeline 基线复跑对照，只修新增 FAIL | `out/subs_check.json` / 单张图 |
| `vtx qc diff`（走 `vtx diff` 入口） | 局部验证通过后与上一版报告对比，确认只有预期项翻转 | 控制台；秒级 |
| `vtx all` | 上述回路全绿后的正式全量渲染 | `out/final.mp4`；voice-ime 温态全链基线 100.8s |

**硬性规则**：任何 timeline 修改，全量 `vtx all` 前必须先走上述局部验证回路；区间渲染只进 `cache/preview/`，不产生正式产物（out/ 不受影响）。
**风格约定：阶段选项写在子命令之后**（如 `vtx -t x video --draft --range 0 6`）。多数阶段开关放到顶层也能解析，但 `--report/--report-only` 仅限 `audio` 阶段、且与 `--dry-run` 冲突时会被阶段门拒绝（vtx.py 阶段校验），统一写在子命令后可避免踩差异。

## 领域路由表
| 领域 | 文档 | 管什么 |
|---|---|---|
| 剪辑 edit | `references/edit.md` | timeline.json 契约 + vtx CLI 全阶段（validate/audit/prepare/gfx/video/audio/mux/qc/gc）+ 音频工程 + 渲染坑表 + 缓存治理 + 三大生成模块接口 |
| 动效 gfx | `references/gfx.md` | HTML 帧驱动透明动效页协议（CDP/Canvas/静态跨度）+ 工具函数 + D22 设计铁律 + 样式模式库 |
| 修改配方/棒次 | `references/runbook.md` | 标准修改配方 R1-R4、R2s 与子智能体棒次协议（R5/R6/R7 产线配方 → `skills/tech-talk/references/runbook-r5r6r7.md`） |
| 抠像/背景合成 matting | `apps/matting/matting.md`（app 专属文档） | 真人实拍抠像（达芬奇 Magic Mask 主力 + 自研链备用）→ 带 alpha ProRes4444 → 背景合成全流程；内核路由即本行 |
| 调研 research / 封面 cover / 拍摄规范 / voice-ime 范式 | `skills/tech-talk/references/`（research.md / cover.md / shooting.md / voice-ime-paradigm.md） | 产线知识：R0-R6 多智能体调研、B 站封面产线、实拍规范、动效编排范式 |
| 科技口播全流程 SOP / 六阶段 | `skills/tech-talk/SKILL.md` | 工作流范本（制作总览→六阶段→音频支线→SOP 闭环→uses 声明表） |

| 用户任务 / 关键词 | 先读 |
|---|---|
| 剪辑、切素材、多轨合成、画中画、烧字幕、混音、渲染、QC、缓存治理；提到 timeline.json / vtx | `references/edit.md` |
| 做动效页、动态图表、片头片尾、overlay 卡、写 gfx/*.html | `references/gfx.md` |
| 实拍素材处理、音视频同步、降噪、响度、验证门 | `references/edit.md` + `references/runbook.md` + 各脚本 `--help` |
| 标准修改任务（改音量/改编排/改字幕/改 gfx 页/全冷重渲） | `references/runbook.md`（R1-R4 配方） |
| 抠像、换背景、绿幕、背景替换、Magic Mask、matte、带 alpha 前景合成 | `apps/matting/matting.md` |
| 新选题、调研可行性、对标分析、生成 01-brief | `skills/tech-talk/`（先读其 SKILL.md，再 `references/research.md`） |
| 制作/修改 B 站投稿封面、封面文案、封面裁切预览 | `skills/tech-talk/references/cover.md` |
| 实拍怎么拍（机位/打光/录音/双系统） | `skills/tech-talk/references/shooting.md` |
| BGM/配乐生成、音色、乐谱 | **路由到 `music` skill**；挂载后分轨审计包配方见 `skills/tech-talk/references/runbook-r5r6r7.md` R6 |

## 推荐方式与全局铁律
1. **委派模式**：复杂任务拆棒交子智能体流水执行，每棒落盘 REPORT；长命令子智能体内必须 run_in_background+轮询（同步等待会被看门狗杀）。
2. **验证门优先**：validate（机制）+ content_gates（内容）+ blackdetect（零黑帧）+ 视觉抽帧（**视觉模型读图只作线索，DOM/像素证据才是权威**）。
3. **迭代用 draft，正式才 --gpu**；缓存内容寻址，源变更自动换键，收尾习惯 `gc --dry` → `gc`。
4. **剪辑永远走 timeline.json + vtx**：不手写 ffmpeg 剪辑命令；改 timeline 必重跑 validate。
5. **gfx 页面必须帧驱动**：动画状态 = 帧号 f 的纯函数，禁 CSS animation/transition、Date.now()、Math.random()、rAF 时序。
6. **调研必须走 R0-R6 编排**（tech-talk）：主编只编排不采集，采集与评分分离，红队质疑必逐条回应。
7. 时间量一律**整数帧**；时基 30fps（≠29.97）、48kHz（每帧整 1600 采样）；区间 `[in, out)` 左闭右开。
8. **修改任何程序或文档一律改 video_tools 仓**（live 挂载点全是符号链接）。

## CLI 速查（新权威路径；仓根兼容 symlink 仅为未知消费者兜底）
| 操作 | 命令 |
|---|---|
| 渲染前校验（必跑） | `python3 <video_tools>/vtx/vtx.py -t <项目>/timeline.json validate` |
| 全流程渲染 | `... vtx.py -t <项目>/timeline.json all`（`--until <stage>` 截断，如 `all --until video` → mux → qc） |
| 草稿预览 | `... video --draft [--frames N]`；`... mux --draft`；区间 `... video --draft --range <A> <B>` |
| 正式 GPU | `... video --gpu` |
| 字幕检查（渲染前必跑，秒级） | `... vtx.py -t <项目>/timeline.json subs check`（对齐漂移/CPS/重叠/行宽，报告 out/subs_check.json） |
| 字幕预览（秒级看效果） | `... vtx.py -t <项目>/timeline.json subs preview <秒\|f帧号>...`（改字幕抽查首选，单张 ≤5s） |
| 项目脚手架 | `bash <video_tools>/scripts/new_project.sh <目录> [时长秒]` |
| 内容验证门 | `env python3 <video_tools>/apps/gates/content_gates.py -t ... [--project-gates 门.json]` |
| 4K 投稿断言 | `bash <video_tools>/apps/publish/check_4k.sh <成片> ...` |
| B 站投稿封面 | `env python3 <video_tools>/apps/cover/make_cover.py all --out <项目>/cover --bg-src <imgen原图> --title "…" --sub "…"`（规格/QC 见 `skills/tech-talk/references/cover.md`） |
| Blender 审片工程 | `blender --background --factory-startup --python <video_tools>/apps/blender-bridge/json2blend.py -- <项目>/timeline.json <项目>/preview.blend`（json2blend/verify_blend 须由 blender 解释器运行，python3 直跑会 ModuleNotFoundError） |
| Blender 自动导出（v1.2） | timeline 顶层 `"blender_bridge": true`（或 `{out, verify}`）→ `vtx all` 收尾自动导出 `<项目>/preview.blend`（plugins/blender-bridge，免手敲命令；blender 缺失收尾报错不吞） |
| BGM 音轨直引工程 JSON（v1.2） | sources[] 加 `{"id":"bgm_main","path":"<music_tools 工程>.json","bgm":{"output":"master"}}` → validate/prepare 自动 `bgm render` 母带 → 48k stereo 入内容寻址缓存（plugins/bgm-render；缓存命中零渲染，工程/$ref/参数变更自动重渲） |
| BGM 分轨审计包 | `python3 <video_tools>/apps/blender-bridge/audit_bundle.py -t <项目>/timeline.json` |
| 音视频同步 / 降噪 | `env python3 <video_tools>/apps/sync/sync_audio.py …` / `env python3 <video_tools>/plugins/denoise/denoise_voice.py …`（响度限幅已并入 vtx audio，无独立步骤） |
| 缓存治理 | `... gc --dry` / `gc` / `df` / `snapshot` |
| 时间线人审预览 | 浏览器打开 `<video_tools>/vtx/preview.html` |
| 环境三检 / 分层清单 | `env python3 <video_tools>/vt.py doctor` / `vt.py apps|plugins|skills list` |

`<video_tools>` = 本仓根（live 挂载 `~/.agents/skills/video/` 为符号链接，历史路径继续可用——vtx/references/assets/scripts/skills 五+一链接）。完整语义与坑表 → `references/edit.md`，各脚本 → `--help`。

## 产线衔接
- 科技口播产线（调研→实拍→动效→渲染→发布）完整 SOP → `skills/tech-talk/SKILL.md`；视频项目工作区在 `~/文档/04_bilibili视频/`（各期项目文件夹 + `_library/` 方法论/资产 + `_archive/`）。
- 音频/BGM 产线 → `music` skill（music_tools / music_project 两仓）；视频是 BGM 的消费方（timeline 音轨直接引用产物，正式版挂载后走分轨审计包交付门）。
- 环境初始化与依赖矩阵 → 本仓 README（`env python3 vt.py init` 自检）。
