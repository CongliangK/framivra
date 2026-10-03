# gfx-templates · 场景→模板索引（模板包 R2，2026-10-03）

> **结论先行**：六件已验收参数化模板覆盖 tech-talk 全部叙事阶段的 gfx 需求——新作页**一律从本目录起手**（复制→改 _tokens→改文案→validate），不从零写。设计基准=`_tokens.md`（调色/字号/组件模式/D22 禁则）；机制契约（帧驱动协议）=内核 `skill/references/gfx.md`；成熟编排手法速查=`references/voice-ime-paradigm.md`。

## 一、模板清单与适用场景

| 模板 | 轨型/底色 | 适用场景 | 对应 voice-ime 手法（paradigm 溯源） |
|---|---|---|---|
| `chart_curve.html` | gfx-full · 白底 | 实测曲线/性能数据（延迟、显存、帧率随参数变化）；口播讲到哪画到哪 | 曲线 KV/TP 机制（g4c_vram）：KV=[进度,数值] 定形、TP=[讲述秒,进度] 映射口播节奏；数字大号 eoe 滚动 |
| `data_table_grouped.html` | gfx-full · 白底暖色 | 分组数据表/配置项清单/对比矩阵（两档方案、参数对照） | 行式数据表（g5b→c1）：ROWT 错峰入场+hl 行亮起 1.4s 回落+斑马纹 |
| `endboard.html` | gfx-full · 暖米底 | 片尾端板（仓库地址/签名句/BGM 收尾）；长挂载安全复核 | g6b：呼吸 sin 持续动作；f 超设计窗须全 sin/seg 连续（无数组按 t 索引） |
| `nav_card.html` | gfx-full · 白底暖色 | 开场路线图/两支柱导航卡/段间转场导航 | 暗态→词拍点亮状态机（nav_two_pillars）：信息暗态在位，口播词首一到 mix()/rgb() 渐变点亮+定格辉光；导航文字 ≤6 字 |
| `overlay_card_top.html` | gfx-overlay · 透明 | 上带叠层卡（口播出镜期头顶区强调卡/流程卡） | overlay 卡避让几何（e1_pain_card）：上带=投影尾预算，墨迹下界 < 脸带顶 |
| `overlay_card_bottom.html` | gfx-overlay · 透明 | 下带流程条/字幕带上方横条（演示期步骤推进条） | 下带避让（p6_pipeline_strip）：卡底+投影 < 字幕带保守线 860；虚线琥珀段=「隔层/异常/跳转」语义 |

## 二、叙事阶段→推荐模板映射

| 叙事阶段 | 推荐模板 | 说明 |
|---|---|---|
| P0 开场（零铺垫演示后路线图） | `nav_card.html` | 「今天讲两件事」两支柱导航；词拍点亮跟随口播 |
| U 段·怎么用（功能/配置/边界） | `data_table_grouped.html`（配置清单）/ `chart_curve.html`（实测数据） | 讲配置走表、讲实测走曲线；同页二次使用 TP×系数压缩节奏（c3 实案） |
| B 段·怎么做（原理/流水线） | `chart_curve.html`（原理曲线）+ `overlay_card_bottom.html`（演示期步骤条） | 全屏页讲结构，overlay 条随演示步骤推进 |
| 出镜段强调（口播画外重点） | `overlay_card_top.html` | 叠层卡不占主画面，避脸带/字幕带 |
| P6 收尾端板 | `endboard.html` | 签名句+仓库地址+BGM 收尾；无「下期」元素（除非拍板） |
| gfx 期出镜补偿（pip 小窗安全区） | （任意 gfx-full 页） | 页面右上预留 ~440×260 出镜小窗安全区（_tokens.md §6） |

## 三、使用步骤（复制→改 _tokens→改文案→validate）

1. **复制**：`cp <模板>.html <项目>/gfx/<页名>.html`——页名按 02-script 素材指向起名，登记 manifest.json（{文件名,用途,服务段,N,dur,轨型,状态}；N=dur×30 精确）。
2. **改 _tokens**：对照 `_tokens.md` §3 调色板/§4 字号梯度改页内常量；**数值语义色只许 RED #dc2626 / GREEN #16a34a**。
3. **改文案**：只改 CONST 数据/文案常量，不碰帧驱动机制代码（FPS=30、f 解析、三禁则）；数据外置页同步改 data-*.json + data-sources.md 溯源。
4. **validate/自查**：三关键帧截图（f=0 / N/2 / N-1）自查三项——协议（同 f 渲两次 md5 一致、grep 无 animation/transition/Date.now/Math.random）、风格（D22 逐条+蓝色像素占比=0）、数据（画面数字与需求单逐字比对）；`<title>` 帧数标注与代码 N 一致。

## 四、风格固化提醒（硬红线）

- **禁深蓝/蓝紫渐变科技风**；基线=白底+暖色（卡其/琥珀）+数据色（`references/visual-baseline.md`；数值以 `_tokens.md` 为准）。
- **禁大标题+文字列表背景板**；口播会念的信息不上画面；文字量「一眼读完不念」（主字段 ≤10 字、辅字段 ≤14 字/行）。
- gfx-full 恒 opacity=1、硬切出入；gfx-overlay 结尾 0.4s alpha 淡出（_tokens.md §6）。
- 违反任一禁则=P0/P1 级返工，自查不过不交付。
