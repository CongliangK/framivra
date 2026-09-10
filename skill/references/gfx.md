# 动效（gfx）· HTML 帧驱动透明动效页

编写供 vtx 渲染的 HTML 动效页面（透明图层）。本文档管**设计协议与样式模式库**；页面的渲染、时移、合成、QC 由同 skill `references/edit.md` 的 vtx CLI 完成。索引见 `../SKILL.md`。

## 帧驱动协议（必须遵守，vtx 依赖它逐帧截图）

```html
<script>
const FPS = 24, N = <总帧数>;              // N == timeline 里该 overlay 的 out - in
const f = parseInt(new URLSearchParams(location.search).get("f") || "0");
const t = Math.min(f / FPS, N / FPS);      // t：本图层局部时间（秒）
/* 全部动画状态 = t 的纯函数。禁止：CSS animation/transition、Date.now()、
   Math.random()（用下面的 seeded 伪随机）、requestAnimationFrame 时序。 */
document.body.style.opacity = 1 - clamp01(seg(t, N/FPS - 0.4, N/FPS)); // 结尾 0.4s 淡出
</script>
```

- 画布固定 `1920x1080`、`background:transparent`、`overflow:hidden`；字体 `"Noto Sans CJK SC", sans-serif`（系统已装，无头渲染正常）。
- 动效时长 = timeline `out - in` 帧数；页面内容的时间轴从 0 开始（vtx 负责把它平移到成片时间轴）。
- vtx 用 `google-chrome --headless --screenshot --default-background-color=00000000 "?f=N"` 逐帧截图，输出带 alpha 的 PNG 序列。

## 独立预览与自查

```bash
# 渲染任意单帧看效果（快速迭代）
google-chrome --headless --disable-gpu --window-size=1920,1080 \
  --default-background-color=00000000 --screenshot=/tmp/g.png "file://$PWD/gfx/gfxA.html?f=48"
# 迭代期至少检查 3 帧：入场（N*0.1）、主体（N*0.5）、结尾前（N*0.9），确认无黑块/错位
```

黑块自查要点：页面任何地方都不能出现"用黑色假装透明"——透明就是 `background:transparent`，结尾淡出只改 `opacity`，不要用黑底 fillRect。

## 标准工具函数（复制即用）

```js
const clamp01 = v => Math.max(0, Math.min(1, v));
const seg = (t, a, b) => clamp01((t - a) / (b - a));        // 时间段映射到 0..1
const eoc = x => 1 - Math.pow(1 - x, 3);                     // easeOutCubic（入场）
const eoe = x => x >= 1 ? 1 : 1 - Math.pow(2, -10 * x);      // easeOutExpo（数字滚动）
const eob = x => { const c = 1.70158;                        // easeOutBack（卡片弹出，过冲）
  return 1 + (c + 1) * Math.pow(x - 1, 3) + c * Math.pow(x - 1, 2); };
function rng(a){return function(){a|=0;a=a+0x6D2B79F5|0;let x=Math.imul(a^a>>>15,1|a);
  x=x+Math.imul(x^x>>>7,61|x)^x;return((x^x>>>14)>>>0)/4294967296}}  // seeded 伪随机
```

## 样式模式库（按需取用，完整示例见 assets/ 起手模板与 assets/gfx-examples/ 实例页）

实例页已随 skill 收录在 `~/.agents/skills/video/assets/gfx-examples/`（源自 poc1/poc2 已验证页面），下表实例位置均相对该目录：

| 模式 | 关键手法 | 实例位置 |
|---|---|---|
| 逐字动力学标题 | 每字独立 `seg(t, 起始+i*0.06, …)` + `eoe`，translateY+rotate 入场；渐变文字 `background-clip:text` | `gfxA.html` |
| 频谱/柱状图 | 柱高 = `sin(t*speed+phase)^2 * amp * 入场进度`，canvas 绘制 | gfxA |
| 折线图逐点画出 | polyline 按 `Math.floor(48*seg(t,…))` 截断 points | `gfxB.html` |
| 环形进度 | SVG `stroke-dasharray = C*进度 C`，中心数字随进度滚动 | gfxB |
| 数字滚动 | `Math.round(target * eoe(seg(t, a, b)))` | gfxB |
| 下三分之一条 | `transform:scaleX(eoc(seg(t,…)))` 左入 + 定时 `opacity` 退出 | `gfxC.html` |
| 片尾端板 | 端板 `scale(.8+.2*eob)` + 呼吸 `drop-shadow(… sin(t*3))` + 徽章延迟弹出 | gfxC |
| SVG 连线粒子 | path `getTotalLength()` 截断画线 + `getPointAtLength(u*L)` 放粒子 | `motion.html` |
| 毛玻璃卡片 | `background:rgba(15,23,42,.55)` + `backdrop-filter:blur(6px)` + 内高光 inset | gfxA |
| 漂移渐变背景 | 两个 blur(160px) 径向渐变 div 按 `sin/cos(t)` 平移 | motion |

起手模板：`~/.agents/skills/video/assets/title-card.html`（标题卡）和 `~/.agents/skills/video/assets/lower-third.html`（下三分之一条），复制到项目 `gfx/` 改文案与配色即可。

页面写好后如何挂进时间线（timeline 里 kind=gfx 的 clip、帧序列渲染），见 `references/edit.md`。

## 设计基准

- **背景板三原则（D22 铁律，2026-08-25 用户拍板）**：背景只放「口播不会念的东西」——图表、数据、演示、图形、代码；**大标题+文字列表的画面形态禁用**（讲解归口播、背景归演示）；段落导航性文字 ≤6 字可用；端板/信息卡属"不会念的指路信息"可用但要克制。gfx 产出以「图表数据型 + 演示型」为主。
- **配色（D22）**：深蓝/蓝紫渐变"科技感"属无信息装饰设计，**禁用**。可接受方向＝纯白极简 / 卡通 cute / 暖色（卡其）。默认基线＝白底（#fff/#faf9f6）+ 近黑文字（#1a1a1a）+ 暖色点缀（卡其 #c8a24a / 琥珀 #d97706）+ 图表数据色（功能色仅用于数据语义：红=删除/失败、绿=新增/成功）；桌面演示画面保持真实系统外观。
- 一次只承载一个信息主次：1920x1080 下图表标题 44-64px、数据标签 24-32px、脚注 18-20px；文字量以「一眼读完不念」为上限。
- 节奏：入场 0.5-1.5s（stagger 0.05-0.3s/元素）、主体持续动作用低频呼吸（sin，周期 2-4s）；**有下层的叠层**结尾统一 0.4s alpha 淡出；**无下层（gfx-full/底画面）页面禁用全局 opacity 淡入淡出**（黑画布上会露黑，D21 教训），交界走硬切（D14）。
- 动效页是**信息层**：叠层页面大面积留透明让底视频透气；全屏页面靠内容（图表/演示）而非装饰底色承载画面。
- **自包含底色原则（2026-09-08 v3.4 教训）**：页面设计时必须回答「它正下方最坏情况是什么」——透明底+深色字的设计一旦失去预期的浅色下垫（演示被剪短/轨序重排/挂到黑场画布上），立即变黑底深字不可读（g6a_title 实案：v3.3 叠演示尾巴成立，剪尾后落黑场，改不透明暖白 #f2ede4 修复）。**不依赖时间线恰好性的下垫，需要浅底就自带**；gfx-full 整页自带底色（含结尾淡出的露底安全性）。
- 新页三件套：`gfx/manifest.json` 登记 + `gfx/data-sources.md` 数据溯源行 + 自查脚本（双渲 md5 + PIL 像素断言）；入场节拍 ROWT 按字幕逐句校准（对位法见 voice-ime u2_backends 实案）。
