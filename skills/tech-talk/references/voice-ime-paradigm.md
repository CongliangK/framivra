# voice-ime 范式 · 实测成熟范式速查与编排方法论指针

> **tech-talk 产线知识**：gfx.md「voice-ime 成熟范式速查」段迁入（voice-ime = 本产线首期科技口播视频，v3.x-v4.x 九轮迭代的实测范式），并汇编 SKILL.md/edit.md 的编排方法论指针。**机制契约（帧驱动协议/Canvas 导出/静态跨度）仍在内核 `skill/references/gfx.md`，本文不含机制**——范式是"怎么编排动效讲节奏"，协议是"页面必须怎么写"。

## voice-ime 成熟范式速查（详细实现看对应源页 `voice-ime/gfx/`）

- **句锁节拍法**：所有节拍常量=页内秒/帧，按「挂载 dst 锚点表 + 口播句窗→页内秒对照注释」组织；词位无词级时间戳时标「待视觉抽验」——见 u2_backends.html 头注释。
- **曲线 KV/TP 机制**：KV=[进度,数值] 分段线性出曲线形状，TP=[讲述秒,进度] 把口播节奏映射成画图进度（讲述到哪画到哪）——g4c_vram.html。
- **骨架占位→落位两拍**：暖灰骨架条先占版式，真内容句拍落位时同步淡出，解决前段版式空——open_source_card.html。
- **暗态→词拍点亮状态机**：信息暗态在位不抢注意，口播词首一到 rgb()/mix() 色插值渐变点亮+定格辉光——nav_two_pillars.html。
- **overlay 卡避让几何**：上带卡=投影尾预算（墨迹下界<脸带顶，Haar 实测脸带）；下带卡=卡底+投影<字幕带保守线 860——e1_pain_card / p6_pipeline_strip。
- **数据外置架构**：HTML+data-*.json 分离（改数据不碰版式）+ data-sources.md 全页溯源登记——c2_components.html。
- **mix()/rgb() 色插值**：hex→hex 与数组→rgb 两形态，灰态→点亮态过渡的通用手段（e1/p6/nav/recap 共用）。
- **同页时间压缩复用**：同一页面二次使用时 TP 全表×系数重排讲述节奏（×0.75 实案）——c3_perf.html。
- **长挂载页安全复核**：f 超出设计窗时全部动画须为 sin 连续函数/seg 窗口，无数组按 t 索引、无定格越界——g6b_endboard.html。
- **虚线语义**：流程连线中 dashed 琥珀段=「隔层/异常/跳转」语义标记，实线墨色=正常流——e1_pain_card.html。

相关实证数据（gfx 引擎矩阵/静态跨度占比/重页硬下限）见内核 `skill/references/runbook.md` R3 基准表与 `docs/DEV.md` §一。

## 编排方法论指针（上层流程）

- **编排总纲**：本包 `references/narrative.md`（开场模板/正文节奏/演示对位三原则/字幕 ASR 锚定/结尾模板/硬约束；音频口径另见 `audio-mixing.md`）——编排每期 timeline 前先读。（历史源件：工作区 `_library/reports/编排方法论-科技口播视频.md`，内容已收编，跨机不必依赖）
- **修复工作法**（迭代纪律）：先问后做不脑测 / 在时间轴编排层定性（Blender=审片界面）/ 区分「编排对实现错」vs「编排本身错」/ 用户权威文本 > 代理回读推断。
- **用户手剪回读协议**（blend2json→ASR→用户权威文本对照）→ `apps/blender-bridge/README.md`「用户手剪回读协议」节。
- **gfx 静态跨度优化工具链**（逐帧 DOM 序列化 sha1 指纹扫描产 decl.json）：已归档 voice-ime `cache/reports/gfx_opt_20260911/{scan.py,inject.py,decl.json}`；红线与重扫纪律见内核 gfx.md「静态跨度声明」。
- 已完成（2026-10-03）：通用实拍规范已上提为本包 `references/shooting-guide.md`（完整手册）；`shooting.md` 为速查版，voice-ime/07-shooting-guide.md 留作项目装备记录。
