# production-workflow · 科技口播视频全流程总览（九阶段）

> **来源**：本包 SKILL.md 主链 + 工作区 `_library/reports/` 各方法论报告（编排方法论 / 终版制作经验 D31 / 修订迭代经验 D33-D34 / A-roll 流程 / 音频惯例调查）。提炼日期：2026-10-03。
> **定位**：本文是**路由总纲**——每阶段一段：入口工具/配方号、关键规则、验证门；细节一律引用包内/内核对应文档，不复制。配方 R1-R4（机制侧修改）在内核 `docs/skill/references/edit.md`，R5-R7（发布链/分轨审计/封面）在本包 `references/runbook-r5r6r7.md`。

## 阶段总表

| # | 阶段 | 入口 | 主要知识文档 |
|---|---|---|---|
| 1 | 调研立项 | `apps/research/`（R0-R6 多智能体） | `references/research.md` |
| 2 | 脚本 | 写稿硬约束 | [文风规范](voice-style.md) + [正文结构](narrative.md#二正文论证结构) |
| 3 | 结构预算 | 00-structure 冻结表 | [结构预算](#3-结构预算) + [正文结构](narrative.md#二正文论证结构) |
| 4 | 拍摄 | `references/shooting-guide.md` | 同左 |
| 5 | A-roll/同步/抠像 | `apps/aroll/` `apps/sync/` `apps/matting/` | `references/aroll-pipeline.md` |
| 6 | gfx 动效页 | 内核 gfx.md + 模板库 | `references/visual-baseline.md` + `assets/gfx-templates/` |
| 7 | 时间线编排 | timeline.json + vtx | 内核 edit.md + `narrative.md` |
| 8 | 音频/BGM | vtx audio + music skill | `references/audio-mixing.md` |
| 9 | QC→封面→发布冻结 | `apps/gates/` `apps/publish/` `apps/cover/` | `runbook-r5r6r7.md` + `cover.md` |

## 1 调研立项

- **入口**：`references/research.md`（R0-R6 多智能体编排为唯一执行方式；采集/评分脚本 `apps/research/`）。
- **关键规则**：三类源最低覆盖；score_topic 量化评分（需求.30/空白.25/差异化.25/时机.20）；红队质询独立复算。
- **验证门**：01-brief 按 [模板](../assets/brief-template.md) 完整填写，含争议记录；量化分与红队质疑是「做不做」的依据。

## 2 脚本

- **入口**：01-brief 定稿后 AI 初稿→用户审改→循环至定稿，产出 02-script（**锁定稿冻结后不再擅改**，实录差异走变更单）。
- **关键规则**：文风与自检按 [voice-style](voice-style.md)；结构与可信度要求见 [正文结构](narrative.md#二正文论证结构)；初稿不写分镜细节。
- **验证门**：用户审改是风格与事实最终把关（口播稿必须像用户自己的话）；需要 TTS 占位走 `apps/tts/`。

## 3 结构预算

- **入口**：02-script → 00-structure 分段结构蓝图（段边界帧数冻结表 + 画面类型构成 + 全片配比）。
- **关键规则**：段边界=冻结预算，三线（gfx/演示/剪辑音频）共同锚点；口播实测差值**只平移段界、不重排段序**（D29）；时基 30fps/48kHz、[in,out) 左闭右开。
- **验证门**：口播覆盖率 ~95%、出镜覆盖 55-70%/gfx 20-30% 参考带核对；结构性偏差须「已论证」并记补偿措施。

## 4 拍摄

- **入口**：`references/shooting-guide.md`（相机/机位/打光/录音/提词全规范；速查版=同目录 `shooting.md`）。
- **关键规则**：4K 30p/36mm 等效/相机悬显示器正上/双系统录音/长镜连续录制。
- **验证门**：开录前 LED 频闪检查；录完**先跑同步工具再进管线**。

## 5 A-roll / 同步 / 抠像

- **入口**：`references/aroll-pipeline.md`（aup3 提取→掐头去尾→ASR 配对→双系统同步→音轨替换→降噪→真空切割→构图统一）；需要换背景 → `apps/matting/`（D49：达芬奇 Magic Mask matte 主力）。
- **关键规则**：D-A 双源（素材即画面+口播单一事实源）；口型感知全局偏移默认 +200ms；CFR 归一化是硬前置。
- **验证门**：按 [同步工具验收](aroll-pipeline.md) 的 `OK` 条件复验，感知容忍度不能替代工具通过；「两遍录音/口型对不上」结论前必须做内容级验证（ASR+静音结构）。

## 6 gfx 动效页

先读 [HTML 创作组织](../../../docs/skill/references/gfx.md#html-创作组织)，在素材计划确定页面边界；本口播流程的素材分线与委派棒次不作为拆页依据。

- **入口**：内核 `docs/skill/references/gfx.md`（帧驱动协议）；**优先复用 `assets/gfx-templates/`** 已验收模板（六件参数化页 + `_tokens.md`）。
- **关键规则**：宏观取向（禁深蓝/背景板原则/pip 安全区）→ `visual-baseline.md`；编排手法 → `voice-ime-paradigm.md`；新页三件套=manifest+data-sources+自查脚本。
- **验证门**：同 f 同像素 md5；蓝像素占比 0；数据逐字比对；pip 安全区像素扫描。

## 7 时间线编排

- **入口**：手写/脚本生成 timeline.json（契约速览见内核 edit.md）；字幕锚定 `apps/asr/`。
- **关键规则**：叙事层硬约束（口播无缝/片尾同话题/反哑剧/演示对位）→ `narrative.md` §六；一切改动走幂等脚本+snapshot，绝不手改 JSON。
- **验证门**：**编排完先开 preview.html 人审，再 validate**；--draft 迭代（draft 先行省钱，画面问题在 draft 阶段修完）；用户 Blender 审片（`apps/blender-bridge/`）。

## 8 音频 / BGM

- **入口**：[audio-mixing](audio-mixing.md)（-14 LUFS 两遍静态增益链 / D47 避让量化口径 / 降噪链 / 双系统同步）；BGM 生成须先确认外部 music skill 可用，不可用时按该文「授权与资产」处理。
- **关键规则**：商业音乐绝对不用（唯一红线）；口播间隙必有铺底；改避让后必须 A/B 实证。
- **验证门**：G1 口播−间隙响度 / G2 全片 LUFS / G3 解码域 TP；使用 sfx 时另需 G8 互相关证据，通用 content_gates 不执行 G8，缺证据不能宣称全门通过（[覆盖与未运行规则](audio-mixing.md#七验证门汇总)）。BGM 挂载后分轨审计包（R6）交用户逐轨审听=交付门。

## 9 QC → 封面 → 发布冻结

- **入口**：`vtx qc`（机制门）+ `apps/gates/content_gates.py`（内容门+项目门）+ R5 发布链 + R7 封面。
- **关键规则**：mux 正式封装=libfdk_aac 192k，终检必测编码后解码 TP；4K lanczos 放大投稿版进 B 站高码率池；封面 16:10+安全区（D50）。
- **验证门**：全冷验证链耗时须结合 [DEV 性能基准](../../../docs/reports/development-history.md#一性能基准) 的项目、机器与测量条件判断，不作固定时限承诺；4K 断言 14 项；FREEZE 冻结点（md5 清单）+ CHANGELOG 变更单 + frozen timeline 三件套；收尾 `vtx gc` + snapshot 归档。

## 迭代与工作法（贯穿全程）

- 修复工作法：先问后做不脑测 / 在时间轴编排层定性 / 区分「编排对实现错」vs「编排本身错」/ 用户权威文本 > 代理回读推断。
- 委派编排：按交付物边界分棒、任务书五要素（铁律/帧号级预核实现状/验收标准/上下文数据块/产出路径）、长命令 run_in_background+轮询（看门狗三连轮教训）。
- 改动半径分级定回滚粒度：音频级 < 素材级 < 结构级，每级独立备份链。
