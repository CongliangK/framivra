> **收录说明**：本文件原样收编自 voice-ime 项目 （2026-08-29 T1-S1 产出的设计基准页，随 voice-ime v3.x 全程验证）。调色/字号/组件模式可直接复用；项目专属行（如 voice-ime 页名引用）按语义替换为本项目对应页。

# _tokens · gfx 设计基准页（T1-S1，2026-08-29）

> **所有 gfx 开发 agent 动手前必读本页。** 本页从旧件（g4 系，D8/D21/D22 已验收）提取可复用设计 token，并固化 D22 禁则清单与帧驱动协议要点。本页与 gfx.md 冲突时以 gfx.md+源码为准；与需求单冲突时以需求单为准。
> 溯源：g4a/g4b/g4c/g5b/g6a/g6b 源码（备份在 `gfx/_old_v2/`）。

## 1. 帧驱动协议（硬门槛，违反=P0）

```
const FPS = 30, N = <总帧数>;                                    // 一律 30fps（D24）
const f = parseInt(new URLSearchParams(location.search).get("f") || "0");
const t = Math.min(f / FPS, N / FPS);                            // t：页面局部秒
```

- **三禁则**：①无 CSS animation/transition；②无 Date.now()/墙钟/requestAnimationFrame 时序；③无 Math.random()（用 seeded rng，gfx.md:42）。
- 全部动画状态 = t 的纯函数；**同 f 必须同像素**（已实测验证，preflight-T1 V1）。
- 画布：`html,body{width:1920px;height:1080px;background:transparent;overflow:hidden;font-family:"Noto Sans CJK SC","Noto Sans SC",sans-serif}`。
- 工具函数（直接复制）：

```js
const clamp01 = v => Math.max(0, Math.min(1, v));
const seg  = (x, a, b) => clamp01((x - a) / (b - a));   // 时间段→0..1
const eoc  = x => 1 - Math.pow(1 - x, 3);               // easeOutCubic（入场）
const eoe  = x => x >= 1 ? 1 : 1 - Math.pow(2, -10 * x);// easeOutExpo（数字滚动）
const eob  = x => { const c = 1.70158;                  // easeOutBack（卡片弹出，过冲）
  return 1 + (c + 1) * Math.pow(x - 1, 3) + c * Math.pow(x - 1, 2); };
function rng(a){return function(){a|=0;a=a+0x6D2B79F5|0;let x=Math.imul(a^a>>>15,1|a);
  x=x+Math.imul(x^x>>>7,61|x)^x;return((x^x>>>14)>>>0)/4294967296}}
const lerp = (a, b, m) => a + (b - a) * m;
```

## 2. D22 禁则清单（用户拍板铁律，违反=P1）

1. **禁深蓝/蓝紫渐变**（科技感装饰）；蓝色像素占比应为 0（自查项）。
2. **禁大标题+文字列表背景板**；口播会念的信息不上画面（图表数据/演示/图形/链接可上）；导航文字 ≤6 字。
3. **功能色仅限数据语义**：红=#dc2626（删除/失败）、绿=#16a34a（新增/成功）；不作胜负渲染。
4. 文字量「一眼读完不念」；主字段 ≤10 字、辅字段 ≤14 字/行。
5. **桌面演示画面保持真实系统外观**（模拟 UI 面板时用真实系统配色，非装饰色）。

## 3. 调色板（token 表）

| token | 值 | 用途 |
|---|---|---|
| INK | `#1a1a1a` | 近黑主文字/轴线/边框 |
| AMBER | `#d97706` | 琥珀强调（点亮/曲线/下划线/胶水） |
| AMBER_D | `#b45309` | 深琥珀（数值/次强调；来源 g4a:110 实用色） |
| KHAKI | `#c8a24a` | 卡其点缀（blob/装饰，克制） |
| GRAY | `#6b7280` | 灰说明文字 |
| GRAY_L | `#9ca3af` / `#a8a29e` | 浅灰脚注/弱标注 |
| BG_FULL_A | `#faf9f6` | gfx-full 暖白底（g4a/g4b） |
| BG_FULL_B | `#ffffff` | gfx-full 纯白底（图表页 g4c） |
| BG_END | `#f5f0e6` | 端板暖米底（g6b） |
| CARD | `#ffffff` + `border:1.5px solid rgba(26,26,26,.72)` + `box-shadow:0 6px 22px rgba(26,26,26,.08)` | 白卡节点（g4a:26-27） |
| EMPH_BG | `hexA(#d97706, .10)` | 强调态浅琥珀底（g4a:308） |
| ZEBRA | `#f7f5ef` | 表格斑马纹行（g5b:19） |
| RED / GREEN | `#dc2626` / `#16a34a` | 仅数据语义 |

## 4. 字号梯度（D22/gfx.md:71）

| 层级 | px | 备注 |
|---|---|---|
| 图表标题 | 44-64 | 逐字入场（0.12+i*0.05 起，eoc/eoe，translateY+rotate） |
| 节点名/主字段 | 26-40 | 节点卡 name 40px（g4a:32）/表行主字段 28-33px |
| 数据标签/组件标注 | 20-32 | 数值 27-66px（数字大号 eoe 滚动） |
| 辅字段/小字 | 18-22 | 等宽小字用 `"Noto Sans Mono CJK SC","Noto Sans CJK SC",monospace` |
| 脚注 | 15-20 | 灰，常驻 |

## 5. 组件模式（带源码出处，新作页直接借用手法）

- **节点卡**：白卡+细黑边+eob 弹入（scale .6→1+translateY 46px）+强调窗口（琥珀边框+浅琥珀底+微 glow）+底部 pbar 进度条 scaleX（g4a:26-37/285-329）。
- **SVG 连线**：path getTotalLength 截断生长 + stroke-dasharray；粒子沿 getPointAtLength(u*L) 流动+morph（g4a:216-273）。
- **callout 跟随**：白底黑字+琥珀左条，多窗口 max alpha 竞争选择（g4a:349-368）。
- **曲线图表**：KV 关键帧+vAt 分段线性+讲述时间 TP→进度 xAt+polyline 截断+曲线头读数（g4c:103-139）。
- **行式数据表**：ROWT 错峰入场+hl 行亮起 1.4s 回落斑马纹+eoe 数字滚动（g5b:97-113）。
- **preedit/commit 机制**：字长出 display 切换（光标真跟随）+commit 前虚线下划线+commit 琥珀闪+退格回改（g4b:298-325）。
- **键帽**：白底灰边 border-bottom 3.5px 立体感+按下 scale .88+描边加深（g4b:80-85/328-338；黑键帽变体 g6a 样式）。
- **盖章徽章**：eob 落章过冲+冲击环扩散+微震（g4d，已废弃但手法可拆用）。
- **diff 动效**：红字划线删除+红底纹渐隐+绿色新增（g3:152-167，B3 规整页参考）。
- **呼吸**：主体持续动作用低频 sin（周期 2-4s），如 drop-shadow 半径 `14+8*sin(t*.85)`（g6b:60）。

## 6. 淡入淡出与出入（硬规则）

| 轨型 | 全局 opacity | 出入 |
|---|---|---|
| gfx-full（无下层） | **恒 1**（`document.body.style.opacity=1`） | 硬切（D14/D21） |
| gfx-overlay（有下层） | 元素级动画自由；页级可渐显 | **结尾 0.4s alpha 淡出**：`document.body.style.opacity=1-clamp01(seg(t,N/FPS-.4,N/FPS))` |

- 入场节奏：0.5-1.5s，stagger 0.05-0.3s/元素。
- gfx-full 右上预留出镜小窗安全区 ~440×260（B2/B3 gfx 期；C1/C2 信息密集帧可短暂隐藏——s2-visual §三）。

## 7. 自查流程（每页交付前必做，写进 checks/<名>.json）

```bash
# 三关键帧：f=0（入场前）、f=N/2（主体）、f=N-1（结尾帧）
google-chrome --headless --disable-gpu --hide-scrollbars --window-size=1920,1080 \
  --default-background-color=00000000 --screenshot=checks/<名>_f<k>.png "file://$PWD/gfx/<名>.html?f=<k>"
```

自查三项：
1. **协议**：同 f 渲两次 md5 一致；grep 无 `animation`/`transition`/`Date.now`/`Math.random`（`requestAnimationFrame` 同禁）。
2. **风格**：D22 禁则逐条过（§2）；蓝色像素占比=0（PIL 采样）。
3. **数据**：画面数字与需求单口径逐字比对（数据溯源写 data-sources.md）。

截图检查要点：无黑块（透明≠黑）、无错位、overlay 页四角 alpha=0、全屏页四角=底色。

## 8. 命名与登记（0-interfaces §二 冻结）

- 新页：`c1_config / c2_components / c3_perf / c4_pipeline / c4f_filter / c4g_regulate / c4l_llm / c4b_branch / c4t_tick / c5a_main_path / c5b_hotkey / b1_bricks / u3_install / cutaway_cand / cutaway_key / c4_mini / w_u1 / w_b1`；改造页沿用 `g6a_title / g6b_endboard`。
- 每页登记 `manifest.json`：{文件名, 用途, 服务段, N, dur, 分辨率, 轨型, 状态}；**N=dur×30 精确，同页所有挂载 dur 一致**。
- `<title>` 帧数标注必须与代码 N 一致（防 24fps 残留式误读）。

## 9. 页面骨架模板（新作页起手）

```html
<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title><页名> · <用途>（N帧 · <dur>s · T1）</title><style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:1920px;height:1080px;background:transparent;
  font-family:"Noto Sans CJK SC","Noto Sans SC",sans-serif;overflow:hidden}
/* gfx-full：<div id="bg" style 覆盖 inset:0 全出血底色>; overlay：无 bg，大面积透明 */
</style></head><body>
<div id="stage"><!-- 元素 --></div>
<script>
/* 帧驱动协议（§1）+ 工具函数 + 全部状态=纯函数 */
</script></body></html>
```

## 10. 高频坑追加（S5-S7 实测教训，2026-08-29）

- **绝对定位子元素坐标系陷阱**（3 页独立复现：c5a/c4t/c4_mini）：position:absolute 的子元素 left/top 参照**最近定位祖先**（非页面原点）——嵌在容器卡（如 #nEngine、泳道、条带）内的卡片若误写页面坐标会整体平移出画（典型症状：元素「消失」或被画布右/底缘截断）。写法：嵌套元素一律用**容器局部坐标**；自检时对每个新容器组做一次「元素 bbox 在画布内」像素断言。
- **条带/小组件视觉 QC 双陷阱**：视觉模型对空壳条带可能幻觉出完整内容、对正常页可能误报灰度——小组件必须 PIL 像素断言+放大裁剪送审双轨。
- **grep 校验文案时注意**：视觉模型 OCR 的数字/专有名词（Fedora 版本号、MiB 数值）误读率不低——一切以源码 grep 与 DOM dump 为准，视觉仅作布局判断。
