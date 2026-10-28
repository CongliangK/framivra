# 动效（gfx）· HTML 帧驱动动效页

> 动效页面的创作组织、帧驱动、Canvas 导出与静态跨度协议；渲染操作见 [剪辑配方](edit.md)，历史试验见 [开发记录](../../reports/development-history.md)。

编写供 vtx 渲染的 HTML 动效页面（全屏画面或透明叠层）。本文档管**设计协议与样式模式库**；页面的渲染、时移、合成、QC 由 vtx CLI 完成。索引见 [SKILL](../SKILL.md)。

## HTML 创作组织

需要可改内容的原创风格样板时，从 [MG 风格集合](../../../templates/mg-styles/README.md)进入各款制作说明、典型页面与独立媒体脚本；真实技术、图像、播放和声音验收分别以其状态记录为准。

**纯 MG 新建或重设计先定同页场景树，再分工；这不改变 gfx clip/alpha/多轨契约。**

- **默认一个 HTML 入口**：背景、角色、图表、文字、粒子、转场和章节切换作为同页 DOM/SVG/Canvas 模块。透明、多场景或多人协作本身不是拆页理由；模板文件是范例，不是作品页数清单。
- **共用帧表与坐标**：`f`/`t` 是该 clip 的局部帧/时间，不是成片全局帧。需要作品绝对帧时，作者按固定入点映射；从 0 开始的整段 clip 才可直接用 `f`。布局共用画布原点，嵌套局部坐标显式换算，不另启时钟。
- **模块分工不拆渲染入口**：任务书注明入口、模块/命名空间、帧窗、层序和集成人；JS/CSS 可拆文件并声明依赖，不另做 HTML 等待自动合组。
- **仅按独立资产需求拆页**：如口播底视频上可独立叠加、跨片复用或单独编辑/替换的字幕条、角标、信息卡；不透明底页与透明叠层也可分开制作。素材计划须记用途/编辑边界、不放同页的原因、底画面/层序/时间窗、页面×帧数及透明渲染/合成/缓存成本（未测标估算/待测）。保留素材可编辑性，不靠改权限、绕校验或删效果降成本。
- **不迁移旧素材来冒充引擎提速**：多页能力、既有口播素材及冻结测试/demo 按原用途保留；重设计是新 workload。通用自动合页不能替代起手设计，[#213](https://github.com/CongliangK/framivra/issues/213) 已 closed/not_planned，不是继续开发路线。

**交付分三项记结论**：技术验证检查帧确定性、布局/alpha、数据与实际渲染；独立艺术审查检查叙事、构图、节奏与动效表现；用户观看最终成片给出视觉确认。DOM/像素与 QC 证明不了艺术质量，艺术评价也不能替代技术验证，二者都不代替用户视觉确认。

## 帧驱动协议（必须遵守，vtx 依赖它逐帧截图）

下例的整页淡出仅适用于已有底画面的独立叠层；全屏底页须去掉该 opacity 行，页内转场由场景模块控制。

```html
<script>
const FPS = 30, N = <总帧数>;              // 全管线精确 30fps；N = 该 gfx clip 的 dur（整数帧）
const f = parseInt(new URLSearchParams(location.search).get("f") || "0");
const t = Math.min(f / FPS, N / FPS);      // t：本图层局部时间（秒）
/* 全部动画状态 = t 的纯函数。禁止：CSS animation/transition、Date.now()、
   Math.random()（用下面的 seeded 伪随机）、requestAnimationFrame 时序。 */
document.body.style.opacity = 1 - clamp01(seg(t, N/FPS - 0.4, N/FPS)); // 结尾 0.4s 淡出
</script>
```

- 画布尺寸匹配项目视口（模板示例为 `1920x1080`），使用 `overflow:hidden`；独立叠层可透明，全屏底页确保底色与转场不会意外露黑。确认所用字体在实际渲染环境可用，不假定模板字体已安装。
- 动效时长为 gfx clip 的 `dur` 帧（秒数为 `dur / FPS`），不是音频事件的 `out−in`；页面内容的时间轴从 0 开始，`dst_in` 仅指定成片入点。字段见 [timeline schema](../../../vtx/timeline.schema.json)，vtx 负责时间轴平移。
- vtx 默认用 CDP 常驻浏览器逐帧渲染，页面须从 `?f=` 确定性得到当前帧。CDP 与 CLI 具有独立环境身份，不承诺任意页面跨引擎像素或 PNG 字节相同。CDP 自动回落 CLI 的页不能发布成功凭证；确需 CLI 时显式 `VTX_GFX=cli` 重渲，strict 策略不支持该路径。身份与失败边界见 [缓存与回执](../../cache-provenance.md#gfx-environment)；可选后端的实现存在不等于验收通过，见 [支持与验证边界](../../DEV.md#支持与验证边界)。
- **外部依赖声明**：本地 CSS/JS/图片/字体等列入 gfx clip 的 `cache_dependencies`；真正共享的依赖列入 `project.cache_dependencies`。两级声明取并集，路径都相对 timeline 根目录；clip 依赖变更使消费它的页面换键，项目依赖变更影响全部 gfx 页。依赖必须存在且为文件。默认 legacy 不证明资源闭包，未声明间接资源不能获得可靠失效保证；strict 仅接受受限静态 HTML/CSS/本地图像字体，拒绝脚本、SVG 与网络资源，仅支持 POSIX CDP，不能用于本文的 JavaScript 动画示例。完整约束见 [HTML 依赖](../../cache-provenance.md#html-依赖)。
- **可选 `__vtx_render(f)` 协议**：同步、幂等、与调用顺序无关；调用后的视觉状态须与 `?f=f` 新加载一致。消费者 navfree 路径未获正式采用，`VTX_GFX_NAVFREE` 保持默认 0；历史实验见 [DEV](../../DEV.md)。DOM 哈希不能代替视觉等价验证。

## Canvas 导出与静态跨度

两者都是可选声明，不要求为性能重写页面。帧、视口、字体与依赖仍须满足上方协议；输入或依赖变化后重新核验，不据声明本身授予像素等价。

### Canvas 导出

仅在**全部预期画面都位于一个 canvas** 时提供以下同步接口；CSS 装饰、其他 DOM 和未完成的异步资产不在导出内，属于画面时不要 opt-in：

```javascript
// 按 ?f=N 同步画完当前帧后再暴露函数；返回字符串而非 Promise。
window.__vtx_export_png = () => canvas.toDataURL('image/png');
```

位图宽高必须等于实际视口，格式为 8-bit RGB/RGBA PNG；叠层用透明 canvas。渲染器检查 data URL、PNG 签名、块边界/CRC、IHDR 尺寸/格式、IDAT 与完整 IEND；这是包络检查，不是任意不可信 PNG 的完整安全解码器。

有效导出直接缓存该帧，绕过截图路径；无效导出回落**同一帧**的白黑双背景截图。未声明接口的页保留原截图路径，包括 CDP 透明背景 override 与 flatten 守卫。`canvas_export` 只表示页面 opt-in，实际路径看逐帧计数（失败回落另计 canvas_fallback），不能据布尔值断言全部帧走快路。缓存身份见 [缓存与回执](../../cache-provenance.md)。

### 静态跨度

```javascript
// 整数帧、左闭右开、[0,N) 内且不重叠；这是作者的像素恒定承诺。
window.__vtx_static_spans = [[40, 184], [540, 772]];
```

`VTX_GFX_STATIC=1` 为默认值：区间头帧渲染或复用，缺失成员从头帧逐字节复制（static_copied 计数、span×k 标签）；已有成员不动。头帧不在当前分片范围也会按需加入渲染。缺失/部分声明只优化明确范围；解析失败告警后退回逐帧渲染。作者不得依赖运行器宽松解析来接受重叠或不合法声明。

渲染器**不验证区间视觉恒定**。DOM 序列化相等不能证明字体、图片、Canvas 位图或最终绘制结果相等；端点抽查也不能代替全区间验证。采用前关闭静态复制，用相同输入/环境逐帧渲染整个声明区间，核对实际像素并保留证据；页面或依赖改变后重验，无独立验证就不声明。无需私人扫描/注入脚本，不将呼吸、数字滚动等随 f/t 变化的画面列入跨度。严格实验对 static_copy 的额外证明见 [Golden 证据](../../cache-provenance.md#golden-evidence)。

### 可移植性与验证

CDP/CLI 用 `Path.resolve().as_uri()` 生成本地 HTML URL，保留 Windows 盘符并编码空格、Unicode 与 `#`；字幕路径分别按 FFmpeg 选项/滤镜解析层转义，参数直接传递而不经 shell。分段 concat 使用 UTF-8 绝对斜杠路径和 concat 引号，不能手改为受特殊目录名影响的相对 URL。

[图形回归](../../../tests/gfx/test_graphics_portability.py)覆盖坏 PNG/尺寸、Canvas 回落、透明合成、静态跨度、特殊字符路径和字幕烧录；CPU 规则测试不等于真实浏览器验证。按 [测试入口](../../../tests/README.md) 运行，真实集成还需 NumPy、`VTX_TEST_CHROME` 与具 subtitles filter 的 `VTX_TEST_FFMPEG`，未提供时明确 skip。临时 fixture 无需私人媒体，TEMP/TMP 可指定适合容量的目录。

全链合成验证是**写操作**：`python3 tests/runners/run_e2e.py --repo <checkout> --output <new-directory> --ffmpeg <executable> --chrome <executable>` 要求输出目录不存在，以单段/双段各跑完整链、strict QC、完整解码、黑帧/真峰与全片缩略对照；再用 `python3 tests/runners/verify_e2e.py <output-directory> --ffmpeg <executable>` 核对逐样本音频及含接缝/字幕/gfx 边界的全分辨率样帧。两入口均需 NumPy；执行结果才是本版本证据。

foreignObject 批量导出及 VTX_GFX_BATCH 已撤销；NAVFREE、BeginFrame 的否决与未测边界见[开发记录](../../reports/development-history.md)。历史结果不证明其他通道提速，`__vtx_render` 不授予生产资格。

## 显式 SVG 序列

此页上文的 DOM/SVG/Canvas 指浏览器同页场景树。已有离散 SVG 帧素材时可显式使用独立的 `svg-sequence` 路由：gfx clip 的 `format` 设为 `"svg-sequence"`，`src` 指向项目根相对的 `framivra-svg-sequence/v1` manifest；其 `frame_count` 与 clip 的 `dur` 相等，帧号是 clip 局部帧。结构字段见 [timeline schema](../../../vtx/timeline.schema.json)，受支持的静态元素、路径/字体及缓存约束以 [SVG 实现](../../../vtx/render/gfx_svg.py) 为准。生成器只记录、不执行；不把任意 SVG 或 HTML 内嵌 SVG 当作此路线已支持。

运行时身份实现支持 Linux ELF 与 macOS arm64，Windows 不支持；具体工具、应用分发与声明字体要求见 [实际依赖与字体](../../../templates/demo-timeline-svg/README.md#实际依赖与字体)。代码支持不等于对应平台或作品已验收。不支持同一时间线混用 HTML/SVG gfx，也不接受 HTML strict 依赖策略；它不是浏览器失败时的自动回退。显式真实两帧检查见 [Inkscape smoke](../../../tests/runners/run_svg_inkscape_smoke.py)，会写指定证据目录，缺工具/字体报错；该检查不代替完整音视频链和人工视听验收。

## 独立预览与自查

```bash
# 写操作：在项目根启动已安装的 Google Chrome，渲染单帧并覆盖 /tmp/g.png。
html_uri="$(python3 -c 'from pathlib import Path; print(Path("gfx/gfxA.html").resolve().as_uri())')"
google-chrome --headless --disable-gpu --window-size=1920,1080 \
  --default-background-color=00000000 --screenshot=/tmp/g.png "${html_uri}?f=48"
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

## 样式模式库（按需取用；实例页见 tech-talk assets/）

下表路径相对 `templates/tech-talk/assets/gfx-examples/`；供取用页内手法，入口组织遵守上文「HTML 创作组织」。

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

本 skill 的 `assets/title-card.html` 与 `assets/lower-third.html` 是保留原样的历史 24fps 基准输入，不是可直接用于当前 30fps 协议的起手模板。新页可从 [现有 30fps 模板用途索引](../../../templates/tech-talk/assets/gfx-templates/INDEX.md) 按全屏或透明叠层用途选取；仍须核对各页的布局边界与所属工作流约定。

页面写好后如何挂进时间线（timeline 里 kind=gfx 的 clip、帧序列渲染），见 [剪辑配方](edit.md)。

## 模板库（→ templates/tech-talk/assets/gfx-templates/）

[模板用途索引](../../../templates/tech-talk/assets/gfx-templates/INDEX.md)列出六件参数化页；文案/数据只改页首 CONST DATA 区，机制代码不动。各页头注说明用途、来源、修改点与避让红线；`_tokens.md` 提供 token，D22 范围与定义仍以本文「设计基准」为准，不扩展为内核通用限制。

## voice-ime 成熟范式速查（→ templates/tech-talk/references/voice-ime-paradigm.md）

[十条动效编排范式与方法论](../../../templates/tech-talk/references/voice-ime-paradigm.md)统一维护场景手法、编排总纲、修复工作法与手剪回读协议；详细实现看对应源页 `voice-ime/gfx/`，不在此重复清单。

## 通用技术检查

- 明确页面下垫与层序；需要浅底可读性的内容自带足够底色，不依赖时间线偶然安排。全屏底页的全局透明度变化可能露出黑场，转场须按实际下层验证。
- 在实际视口、字体与透明合成路径检查边界、遮挡、文字与数据；同帧重渲可检查重复性，但不证明所有帧或其他机器的结果。
- 交付结论按上方「交付分三项」分别记录。

## 设计基准

**以下为 tech-talk 科技口播工作流的 D22 等既有创作约定，保留该工作流的风格与布局要求；它们不是 vtx 内核对所有视频的配色、题材、字号或 pip 硬约束。** 其他作品按自身简报选择审美，仍遵守帧驱动、依赖与实际渲染验证要求。工作流宏观说明见 [visual-baseline](../../../templates/tech-talk/references/visual-baseline.md)，具体 token 与模板见 [设计 token](../../../templates/tech-talk/assets/gfx-templates/_tokens.md)。

- **背景板三原则（设计铁律）**：背景只放「口播不会念的东西」——图表、数据、演示、图形、代码；**大标题+文字列表的画面形态禁用**（讲解归口播、背景归演示）；段落导航性文字 ≤6 字可用；端板/信息卡属"不会念的指路信息"可用但要克制。gfx 产出以「图表数据型 + 演示型」为主。
- **配色**：深蓝/蓝紫渐变"科技感"属无信息装饰设计，**禁用**。可接受方向＝纯白极简 / 卡通 cute / 暖色（卡其）。默认基线＝白底（#fff/#faf9f6）+ 近黑文字（#1a1a1a）+ 暖色点缀（卡其 #c8a24a / 琥珀 #d97706）+ 图表数据色（功能色仅用于数据语义：红=删除/失败、绿=新增/成功）；桌面演示画面保持真实系统外观。
- 一次只承载一个信息主次：1920x1080 下图表标题 44-64px、数据标签 24-32px、脚注 18-20px；文字量以「一眼读完不念」为上限。
- 节奏：入场 0.5-1.5s（stagger 0.05-0.3s/元素）、主体持续动作用低频呼吸（sin，周期 2-4s）；**有下层的叠层**结尾统一 0.4s alpha 淡出；**无下层（gfx-full/底画面）页面禁用全局 opacity 淡入淡出**（黑画布上会露黑），交界走硬切。
- 动效页是**信息层**：叠层页面大面积留透明让底视频透气；全屏页面靠内容（图表/演示）而非装饰底色承载画面。
- **pip 安全区红线**：项目使用右下 420px 方形悬浮窗（区域 (1440,600)-(1860,1020)）时，**pip 在位期间的 gfx 页关键内容禁入 x≥1440 ∧ y≥600 安全区**——受保护元素须整体满足「右界 <1440 或底界 <600」之一；设计期就按此约束布局，事后返工=整版重排+全量重渲帧缓存。验收门=对终态帧做安全区像素扫描（与底色差 >45 判墨迹）；装饰性投影辉光/无文字容器可豁免但须留视觉裁定记录。
- 新页三件套：`gfx/manifest.json` 登记 + `gfx/data-sources.md` 数据溯源行 + 自查脚本（双渲 md5 + PIL 像素断言）；入场节拍 ROWT 按字幕逐句校准（对位法见 voice-ime u2_backends 实案）。
