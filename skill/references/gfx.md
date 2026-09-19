# 动效（gfx）· HTML 帧驱动透明动效页

> **使用者向**：动效页设计协议与样式模式库。坑位来历与实测数据见 `../../docs/DEV.md`。

编写供 vtx 渲染的 HTML 动效页面（透明图层）。本文档管**设计协议与样式模式库**；页面的渲染、时移、合成、QC 由同 skill `references/edit.md` 的 vtx CLI 完成。索引见 `../SKILL.md`。

## 帧驱动协议（必须遵守，vtx 依赖它逐帧截图）

```html
<script>
const FPS = 30, N = <总帧数>;              // 全管线精确 30fps；N = 该 gfx 页所属事件的帧数（out−in）
const f = parseInt(new URLSearchParams(location.search).get("f") || "0");
const t = Math.min(f / FPS, N / FPS);      // t：本图层局部时间（秒）
/* 全部动画状态 = t 的纯函数。禁止：CSS animation/transition、Date.now()、
   Math.random()（用下面的 seeded 伪随机）、requestAnimationFrame 时序。 */
document.body.style.opacity = 1 - clamp01(seg(t, N/FPS - 0.4, N/FPS)); // 结尾 0.4s 淡出
</script>
```

- 画布固定 `1920x1080`、`background:transparent`、`overflow:hidden`；字体 `"Noto Sans CJK SC", sans-serif`（系统已装，无头渲染正常）。
- 动效时长 = 所属事件的帧数（`out`−`in`，契约字段见 `docs/ARCHITECTURE.md`）；页面内容的时间轴从 0 开始（vtx 负责把它平移到成片时间轴）。
- vtx 逐帧截图渲染 gfx 帧。默认走 **CDP 常驻浏览器引擎**（多 tab/页并行；不透明页与 CLI 逐字节一致、透明页双探针解算 alpha，≤5/255 舍入级）；失败自动回落 CLI 逐帧路径（`google-chrome --headless --screenshot --default-background-color=00000000 "?f=N"`），输出同为带 alpha 的 PNG 序列。两引擎对页面唯一要求：**从 `?f=` 查询参数确定性渲染**（CDP 引擎以 location.search 变更确认新文档，页面不得依赖滚动/焦点等副作用）。
- **外部依赖声明（cache_dependencies 契约）**：gfx 页引用的外部本地资产（CSS/JS/图片/字体等）必须在 timeline `project.cache_dependencies` 显式声明（相对项目根的路径列表）。声明的依赖内容参与 gfx 缓存键与回执输入身份——**改依赖即换键重渲**；依赖不存在或非文件会在 vtx 构造期直接报错。声明属项目级：任一依赖变化会使全部 gfx 页重渲（保守过失效，可接受）；未声明的间接依赖、网络资源不追踪，建议内嵌素材或全部显式声明。详见 `docs/cache-provenance.md`。
- **可选 Canvas 导出通道**：整页预期输出就是单个 canvas 的页面可选择实现导出函数：在返回前同步画完 `?f=N` 请求帧，然后 `window.__vtx_export_png = () => canvas.toDataURL('image/png')`。画布位图须精确等于渲染宽高（通常 1920×1080），叠层用透明画布；CSS 装饰与其他 DOM 元素**不**包含在 toDataURL 里——它们属于预期输出就不要 opt-in。协议期望立即返回字符串（非 Promise），不等待异步资源。渲染器校验 data URL/PNG 头/尺寸/校验和后直接缓存该帧（整体绕过截图机制，含透明页双探针路径）；无效导出自动回落同帧双背景截图，未实现该函数的页面渲染路径不变。本通道协议自始未变、保持可选；它不是批量通道（仍是逐帧导出）——曾设想替代它的 foreignObject 快照批量通道已因固有像素偏差经 golden 全零门槛判死移除，`__vtx_export_png` 校验失败的回落与路径打标（`canvas×N`/`canvas_fallback×N`）不变。详见 `docs/canvas-export.md`。
- **可选静态跨度声明（`__vtx_static_spans`，默认开启消费 `VTX_GFX_STATIC=1`）**：页面可声明 `window.__vtx_static_spans = [[a,b),...]`——帧号**左闭右开**、仅 `[0,N)` 内、互不重叠，语义承诺「区间内画面逐帧恒定」。渲染器只渲各跨度头帧，成员帧**字节复制**头帧（日志 `static_copied×N`/`span×k` 打标）；voice-ime 8 页已声明，1474 帧≈12.4% 直接免渲。**何时值得**：跨度 ≥30 帧才值得声明（端板定格、KV 画完后的静置段、暗态挂载段这类长静止区间）；**怎么生成**：禁止手写，用逐帧 DOM 序列化 sha1 指纹扫描产出（DOM 全等 ⇒ 光栅全等 ⇒ 字节全等；工具已归档 voice-ime `cache/reports/gfx_opt_20260911/{scan.py,inject.py,decl.json}`）；**红线**：含 `<canvas>` 的页禁用（位图不进 DOM 序列化会假阳性）、任何随 t/f 变化的状态不得落入跨度；正确性双兜底＝生成方端点复核＋`scripts/gfx_golden.py` 比对，改过已声明页面后必须重扫再声明。
- **可选 `__vtx_render(f)` 协议**：页面实现 `window.__vtx_render(f)` 可在页内直接置帧态，契约＝**同步纯函数**：幂等、与调用顺序无关、调用后页面视觉状态与以 `?f=f` 新加载完全一致。**当前唯一消费者 navfree 免导航截图通道已实测证伪封存**（负载下像素漂移且更慢，`VTX_GFX_NAVFREE` 默认 0——**勿开**，详录 DEV.md §二）；协议保留给未来渲染通道，页面接入无害。voice-ime 四页（c5a_main_path/c5b_hotkey/u3_install/llm_post）已重构并通过 2650 帧逐帧 DOM 哈希全等验证。

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

## 样式模式库（按需取用；起手模板在本 skill assets/，实例页见 tech-talk assets/）

实例页已随产线资产迁至 `skills/tech-talk/assets/gfx-examples/`（源自 poc1/poc2 已验证页面，v2 迁出内核），下表实例位置均相对该目录：

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
| 毛玻璃卡片 | ~~`background:rgba(15,23,42,.55)` + `backdrop-filter:blur(6px)` + 内高光 inset~~ **已作废**：深底玻璃属禁用深蓝系；替代=白底暖色卡 `#fff`/`rgba(255,253,250,.92)` + 浅暖描边+柔投影（见 gfx-templates/ overlay 卡族） | gfxA |
| 漂移渐变背景 | 两个 blur(150px) 径向渐变 div 按 `sin/cos(t)` 平移——**暖底版**（卡其 #c8a24a/琥珀 #d97706 极淡斑，白底之上）为现行基线；poc 深底版实例仅作机制参考 | motion / endboard.html |

起手模板（内核通用件）：本 skill `assets/title-card.html`（标题卡）和 `assets/lower-third.html`（下三分之一条），复制到项目 `gfx/` 改文案与配色即可；产线已验收七件参数化模板（gfx-templates/）在 `skills/tech-talk/assets/gfx-templates/`。

页面写好后如何挂进时间线（timeline 里 kind=gfx 的 clip、帧序列渲染），见 `references/edit.md`。

## 模板库（→ skills/tech-talk/assets/gfx-templates/）

产线已验收模板（六件参数化页 + `_tokens.md` 设计基准，合七件）随产线资产迁至 **`skills/tech-talk/assets/gfx-templates/`**——参数化自已验收页面，**文案与数据全部集中在页首 CONST DATA 区，改数据不碰机制代码**；每件头部注释块标明用途/来源/改哪里/避让红线，逐件适用场景见各模板头注。`_tokens.md` 为 D22 调色板 token 的产线落地面，**D22 定义权威仍在本文「设计基准」节**（tech-talk 反向引用）。

## voice-ime 成熟范式速查（→ skills/tech-talk/references/voice-ime-paradigm.md）

句锁节拍法 / 曲线 KV-TP 机制 / 骨架占位两拍 / 暗态点亮状态机 / overlay 避让几何 / 数据外置 / mix 色插值 / 同页时间压缩 / 长挂载安全复核 / 虚线语义——**十条实测范式已迁至 `skills/tech-talk/references/voice-ime-paradigm.md`**（产线编排知识；详细实现看对应源页 `voice-ime/gfx/`），编排方法论指针（编排总纲/修复工作法/手剪回读协议）同在该文件。

## 设计基准

- **背景板三原则（设计铁律）**：背景只放「口播不会念的东西」——图表、数据、演示、图形、代码；**大标题+文字列表的画面形态禁用**（讲解归口播、背景归演示）；段落导航性文字 ≤6 字可用；端板/信息卡属"不会念的指路信息"可用但要克制。gfx 产出以「图表数据型 + 演示型」为主。
- **配色**：深蓝/蓝紫渐变"科技感"属无信息装饰设计，**禁用**。可接受方向＝纯白极简 / 卡通 cute / 暖色（卡其）。默认基线＝白底（#fff/#faf9f6）+ 近黑文字（#1a1a1a）+ 暖色点缀（卡其 #c8a24a / 琥珀 #d97706）+ 图表数据色（功能色仅用于数据语义：红=删除/失败、绿=新增/成功）；桌面演示画面保持真实系统外观。
- 一次只承载一个信息主次：1920x1080 下图表标题 44-64px、数据标签 24-32px、脚注 18-20px；文字量以「一眼读完不念」为上限。
- 节奏：入场 0.5-1.5s（stagger 0.05-0.3s/元素）、主体持续动作用低频呼吸（sin，周期 2-4s）；**有下层的叠层**结尾统一 0.4s alpha 淡出；**无下层（gfx-full/底画面）页面禁用全局 opacity 淡入淡出**（黑画布上会露黑），交界走硬切。
- 动效页是**信息层**：叠层页面大面积留透明让底视频透气；全屏页面靠内容（图表/演示）而非装饰底色承载画面。
- **自包含底色原则**：页面设计时必须回答「它正下方最坏情况是什么」——透明底+深色字的设计一旦失去预期的浅色下垫（演示被剪短/轨序重排/挂到黑场画布上），立即变黑底深字不可读。**不依赖时间线恰好性的下垫，需要浅底就自带**；gfx-full 整页自带底色（含结尾淡出的露底安全性）。（实案详录见 DEV.md）
- **pip 安全区红线**：项目使用右下 420px 方形悬浮窗（区域 (1440,600)-(1860,1020)）时，**pip 在位期间的 gfx 页关键内容禁入 x≥1440 ∧ y≥600 安全区**——受保护元素须整体满足「右界 <1440 或底界 <600」之一；设计期就按此约束布局，事后返工=整版重排+全量重渲帧缓存。验收门=对终态帧做安全区像素扫描（与底色差 >45 判墨迹）；装饰性投影辉光/无文字容器可豁免但须留视觉裁定记录。
- 新页三件套：`gfx/manifest.json` 登记 + `gfx/data-sources.md` 数据溯源行 + 自查脚本（双渲 md5 + PIL 像素断言）；入场节拍 ROWT 按字幕逐句校准（对位法见 voice-ime u2_backends 实案）。
