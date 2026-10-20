# 动效（gfx）· HTML 帧驱动动效页

> **使用者向**：动效页设计协议与样式模式库。坑位来历与实测数据见 `../../DEV.md`。

编写供 vtx 渲染的 HTML 动效页面（全屏画面或透明叠层）。本文档管**设计协议与样式模式库**；页面的渲染、时移、合成、QC 由同 skill `references/edit.md` 的 vtx CLI 完成。索引见 `../SKILL.md`。

## HTML 创作组织

**新建或重设计纯 MG（Motion Graphics）作品时，先定场景树，再分工和写页面。** 以下是创作组织原则，不改变 gfx clip/alpha/多轨渲染契约：

- **同一作品默认一个 HTML 渲染入口**：背景、角色、图表、文字、粒子与转场作为同页 DOM/SVG/Canvas 场景树中的逻辑模块；章节和场景切换在页内完成。透明组件可以直接叠在同页不透明底画面上，透明本身不是拆页理由。
- **共享时间与坐标**：模块共用该 gfx clip 的局部帧 `f`/时间 `t`，场景窗口和节拍以同一帧表计算，不能各启一套独立时钟。`?f=` 不是成片全局帧；确需作品绝对帧时，由作者按已固定的 clip 入点显式映射（作品从 0 开始且由整段 clip 承载时就是 `f`），不假设渲染器自动提供。布局共用页面画布原点；嵌套容器局部坐标须明确换算。
- **角色分工不决定渲染边界**：即使安排 20 个创作角色，仍可分别维护同页的场景、数据、样式与动画函数；角色数不等于 HTML、轨道或 clip 数。任务书写明共用入口、负责模块/命名空间、帧窗口、层序与集成人；JS/CSS 拆文件只为源码维护，遵守下文依赖声明，不把模块另做 HTML 再等渲染时合组。
- **多 HTML 的适用条件**：存在明确的独立叠加、跨片复用或单独编辑/替换需求，才按资产边界拆页。例如口播或综艺的底视频上，需要独立替换、错开出现的 HTML 字幕条、角标、信息卡；不透明底页与透明叠层也可独立制作。仅因有透明元素、多场景、多角色或多人协作，不成立拆页理由。
- **拆页前记账**：在项目素材计划中记录独立用途/复用或编辑边界、为何不放同页、底画面与层序、时间窗，以及新增页面×帧数、透明渲染与合成/缓存的额外成本（未测就标估算/待测）。保留独立素材可编辑性，不靠改权限、绕过校验或移除效果减成本。
- **先组织源码，不做事后自动合页补救**：本原则不要求迁移所有旧项目，也不禁止工具多页能力。通用自动合组不能替代起手设计；相关试点 [#213](https://github.com/CongliangK/framivra/issues/213) 为 closed/not_planned，不作为继续开发路线。既有口播多页素材与冻结测试/demo 输入按其原用途保留；重设计作品是新 workload，减少页面数量不能作为同输入引擎提速证据。

起手判断示例：纯 MG 宣传片含多章、角色和半透明粒子 → 同一 HTML 内按帧编排；真人口播加可替换角标/信息卡 → 有独立资产需求时可拆 HTML，并登记成本。模板里的不同文件是可取用的范例，不是新作品的页数清单。

**交付分三项记结论**：技术验证检查帧确定性、布局/alpha、数据与实际渲染；独立艺术审查检查叙事、构图、节奏与动效表现；用户观看最终成片给出视觉确认。DOM/像素与 QC 证明不了艺术质量，艺术评价也不能替代技术验证，二者都不代替用户视觉确认。

## 帧驱动协议（必须遵守，vtx 依赖它逐帧截图）

下例的整页淡出仅适用于已有底画面的独立叠层；全屏底页须去掉该 opacity 行，页内转场由场景模块控制。

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

- 画布尺寸匹配项目视口（模板示例为 `1920x1080`），使用 `overflow:hidden`；独立叠层可透明，全屏底页确保底色与转场不会意外露黑。确认所用字体在实际渲染环境可用，不假定模板字体已安装。
- 动效时长 = 所属事件的帧数（`out`−`in`，契约字段见 `docs/ARCHITECTURE.md`）；页面内容的时间轴从 0 开始（vtx 负责把它平移到成片时间轴）。
- vtx 默认用 CDP 常驻浏览器逐帧渲染，页面须从 `?f=` 确定性得到当前帧。CDP 与 CLI 具有独立环境身份，不承诺任意页面跨引擎像素或 PNG 字节相同。CDP 自动回落 CLI 的页不能发布成功凭证；确需 CLI 时显式 `VTX_GFX=cli` 重渲，strict 策略不支持该路径。身份与失败边界见 [缓存与回执](../../cache-provenance.md#gfx-environment)。
- **外部依赖声明**：本地 CSS/JS/图片/字体等列入 gfx clip 的 `cache_dependencies`；真正共享的依赖列入 `project.cache_dependencies`。两级声明取并集，路径都相对 timeline 根目录；clip 依赖变更使消费它的页面换键，项目依赖变更影响全部 gfx 页。依赖必须存在且为文件。默认 legacy 不证明资源闭包，未声明间接资源不能获得可靠失效保证；strict 仅接受受限静态 HTML/CSS/本地图像字体，拒绝脚本、SVG 与网络资源，仅支持 POSIX CDP，不能用于本文的 JavaScript 动画示例。完整约束见 [HTML 依赖](../../cache-provenance.md#html-依赖)。
- **可选 Canvas 导出通道**：预期输出全部在一个 canvas 内时，可同步绘制请求帧并提供 `window.__vtx_export_png = () => canvas.toDataURL("image/png")`。位图尺寸须匹配视口；CSS 装饰和其他 DOM 不在导出范围，若它们属于输出就不要 opt-in。函数立即返回字符串，不能等待异步资源。渲染器验证 PNG 后缓存该帧；无效导出回落同帧截图。这是逐帧可选通道，详细边界见 [Canvas 导出](../../canvas-export.md)。
- **可选静态跨度声明**：`window.__vtx_static_spans = [[a, b], ...]` 中每对表示 `[a, b)`，须在 `[0, N)` 内、互不重叠。开启 `VTX_GFX_STATIC=1` 时，渲染器只渲头帧并将其字节复制到其余帧；它不验证区间视觉恒定。声明是作者的像素恒定承诺，DOM 序列化相等不能证明字体、图片、Canvas 位图或实际绘制结果相等。无独立验证时不声明；采用前须关闭静态复制，用相同输入/环境逐帧渲染声明区间并核对实际像素，保留证据，页面或依赖改变后重验。无需仓外私人扫描脚本；端点抽查不能代替整个跨度验证。
- **可选 `__vtx_render(f)` 协议**：同步、幂等、与调用顺序无关；调用后的视觉状态须与 `?f=f` 新加载一致。消费者 navfree 路径未获正式采用，`VTX_GFX_NAVFREE` 保持默认 0；历史实验见 [DEV](../../DEV.md)。DOM 哈希不能代替视觉等价验证。

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

实例页在 `templates/tech-talk/assets/gfx-examples/`（源自 poc1/poc2 已验证页面），下表实例位置均相对该目录；它们供取用页内手法，页面组织先按上文「HTML 创作组织」决定：

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
| 半透明卡片 | 透明底色 + 描边/阴影；按项目风格选择色调，tech-talk 使用下方 D22 约定 | gfxA |
| 漂移渐变背景 | 径向渐变 div 按 `sin/cos(t)` 平移；配色按项目约定，实际渲染需检查边界与性能 | motion / endboard.html |

起手模板（内核通用件）：本 skill `assets/title-card.html`（标题卡）和 `assets/lower-third.html`（下三分之一条），按已确定的页面边界复制或取用其页内组件，再改文案与配色；产线已验收七件参数化模板（gfx-templates/）在 `templates/tech-talk/assets/gfx-templates/`。

页面写好后如何挂进时间线（timeline 里 kind=gfx 的 clip、帧序列渲染），见 `references/edit.md`。

## 模板库（→ templates/tech-talk/assets/gfx-templates/）

产线已验收模板（六件参数化页 + `_tokens.md` 设计基准，合七件）随产线资产迁至 **`templates/tech-talk/assets/gfx-templates/`**——参数化自已验收页面，**文案与数据全部集中在页首 CONST DATA 区，改数据不碰机制代码**；每件头部注释块标明用途/来源/改哪里/避让红线，逐件适用场景见各模板头注。`_tokens.md` 为 D22 调色板 token 的产线落地面，**D22 的适用范围及定义仍见本文「设计基准」节**（tech-talk 反向引用，不扩展为内核通用限制）。

## voice-ime 成熟范式速查（→ templates/tech-talk/references/voice-ime-paradigm.md）

句锁节拍法 / 曲线 KV-TP 机制 / 骨架占位两拍 / 暗态点亮状态机 / overlay 避让几何 / 数据外置 / mix 色插值 / 同页时间压缩 / 长挂载安全复核 / 虚线语义——**十条实测范式已迁至 `templates/tech-talk/references/voice-ime-paradigm.md`**（产线编排知识；详细实现看对应源页 `voice-ime/gfx/`），编排方法论指针（编排总纲/修复工作法/手剪回读协议）同在该文件。

## 通用技术检查

- 明确页面下垫与层序；需要浅底可读性的内容自带足够底色，不依赖时间线偶然安排。全屏底页的全局透明度变化可能露出黑场，转场须按实际下层验证。
- 在实际视口、字体与透明合成路径检查边界、遮挡、文字与数据；同帧重渲可检查重复性，但不证明所有帧或其他机器的结果。
- 将技术验证、艺术审查与用户视觉确认分别记录，遵守上方交付检查。

## 设计基准

**以下为 tech-talk 科技口播工作流的 D22 等既有创作约定，保留该工作流的风格与布局要求；它们不是 vtx 内核对所有视频的配色、题材、字号或 pip 硬约束。** 其他作品按自身简报选择审美，仍遵守帧驱动、依赖与实际渲染验证要求。工作流宏观说明见 [visual-baseline](../../../templates/tech-talk/references/visual-baseline.md)，具体 token 与模板见 [设计 token](../../../templates/tech-talk/assets/gfx-templates/_tokens.md)。

- **背景板三原则（设计铁律）**：背景只放「口播不会念的东西」——图表、数据、演示、图形、代码；**大标题+文字列表的画面形态禁用**（讲解归口播、背景归演示）；段落导航性文字 ≤6 字可用；端板/信息卡属"不会念的指路信息"可用但要克制。gfx 产出以「图表数据型 + 演示型」为主。
- **配色**：深蓝/蓝紫渐变"科技感"属无信息装饰设计，**禁用**。可接受方向＝纯白极简 / 卡通 cute / 暖色（卡其）。默认基线＝白底（#fff/#faf9f6）+ 近黑文字（#1a1a1a）+ 暖色点缀（卡其 #c8a24a / 琥珀 #d97706）+ 图表数据色（功能色仅用于数据语义：红=删除/失败、绿=新增/成功）；桌面演示画面保持真实系统外观。
- 一次只承载一个信息主次：1920x1080 下图表标题 44-64px、数据标签 24-32px、脚注 18-20px；文字量以「一眼读完不念」为上限。
- 节奏：入场 0.5-1.5s（stagger 0.05-0.3s/元素）、主体持续动作用低频呼吸（sin，周期 2-4s）；**有下层的叠层**结尾统一 0.4s alpha 淡出；**无下层（gfx-full/底画面）页面禁用全局 opacity 淡入淡出**（黑画布上会露黑），交界走硬切。
- 动效页是**信息层**：叠层页面大面积留透明让底视频透气；全屏页面靠内容（图表/演示）而非装饰底色承载画面。
- **pip 安全区红线**：项目使用右下 420px 方形悬浮窗（区域 (1440,600)-(1860,1020)）时，**pip 在位期间的 gfx 页关键内容禁入 x≥1440 ∧ y≥600 安全区**——受保护元素须整体满足「右界 <1440 或底界 <600」之一；设计期就按此约束布局，事后返工=整版重排+全量重渲帧缓存。验收门=对终态帧做安全区像素扫描（与底色差 >45 判墨迹）；装饰性投影辉光/无文字容器可豁免但须留视觉裁定记录。
- 新页三件套：`gfx/manifest.json` 登记 + `gfx/data-sources.md` 数据溯源行 + 自查脚本（双渲 md5 + PIL 像素断言）；入场节拍 ROWT 按字幕逐句校准（对位法见 voice-ime u2_backends 实案）。
