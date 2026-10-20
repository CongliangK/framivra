# 15 款模板当前审查矩阵

源码已推送：[`2030a84ee6e951a6c83b431bab10d53db0b0492d`](https://github.com/CongliangK/framivra/tree/2030a84ee6e951a6c83b431bab10d53db0b0492d/templates/mg-styles)。[HTML 总入口](../templates/mg-styles/html/index.html) 可直接打开；每页的制作卡、两套不同题材、具体编辑练习及可编辑语义内容均在相邻场景 JS。使用方法见 [HTML 说明](../templates/mg-styles/html/README.md)。本表是当前摘要，历史、命令与影片哈希以 [验证记录](mg-styles-validation.md) 为准。

代码验证：媒体 `dac7457fe628001cedbb648776affdc0b9ef76c9` 的 126 项通过，77 文件前后哈希一致；HTML `a29d82ca209d589127901de5766b382263c19454` 的 13 项通过，40 文件前后一致；后续 `f3ea10e` 仅将 D 组已有编辑练习拆为独立字段，15 卡各八字段的轻量测试及独立复验通过；`2030a84` 新增 Collage B 阅读样本，以真实240帧全链和独审验证，不继承旧测试结果。表中 A/B/C/D 对应 `tests/test_mg_html_{a,b,c,d}.py`，另有共享播放器、跨款测试；**均不是浏览器像素验收**。 以上测试各自绑定所列精确快照，不自动转授给后续源码；`3d050d4` 仅是早期 Liquid 补丁的历史证据快照。

媒体“完整技术通过”指实际源帧、编码、现有 timeline `all`、strict QC 8/8、完整解码、零黑场与帧数对账。图像审查单列；“全帧静态”指逐幅缩略条带并结合原生关键帧，不是实时播放。所有 HTML 尚缺浏览器字体/像素/实时播放验证；所有声音均无听感验收，实际影片仍为固定半秒占位提示音，不能当成语义声音设计完成。

| 款式与 HTML | 制作卡 / 两题材源码 | HTML 代码测试 | 媒体技术验证（两题材） | 图像审查 | 未验证能力 / 近似 |
|---|---|---|---|---|---|
| [Flat Vector](../templates/mg-styles/html/flat-vector.html) | [因果图、主体迁移与重排](../templates/mg-styles/html/flat-vector.js) | D；起点迁移/重排末态/6帧hold/palette | 连线修正版 `domain-fixes/flat-{a,b}` 完整技术通过 | 新两片全帧静态及原生连线复查 | 任意外部矢量素材、正式声效 |
| [Line Art](../templates/mg-styles/html/line-art.html) | [语义线条接力](../templates/mg-styles/html/line-art.scene.js) | A；乱序/内容/揭示预算 | `readable/line-{a,b}-readable` 完整通过，各240帧 | 新两片全帧静态；末30帧满色源RGBA恒等 | 任意外部路径与字形动画 |
| [Isometric](../templates/mg-styles/html/isometric.html) | [依赖装配](../templates/mg-styles/html/isometric.scene.js) | A；实际30°/三轴等长、依赖 | `fifth/iso-a-fixed` 与 `iso-b` 完整通过，240/210帧 | 两片全帧静态及原生装配/末态 | 自由摄影机；不能以透视替代等角 |
| [Soft 3D](../templates/mg-styles/html/soft-3d.html) | [材质与章节布局](../templates/mg-styles/html/soft-3d.scene.js) | A；章节exclusive-end/6帧settled/六物体fit | 新连续 `continuous-ground-fixed/3d-{a,b}` 各120帧完整技术通过 | 新两片全部240帧静态条带及原生关键帧独审；实际mesh逐帧无穿地 | HTML仅投影/构图示意，非PBR；媒体540p上采样/粒噪；A源重生成严格比较FAIL |
| [CEL Boil](../templates/mg-styles/html/cel-boil.html) | [曝光表与角色动作](../templates/mg-styles/html/cel-boil.js) | B；实际sample→pose/中间姿势 | `fourth/cel-{a,b}` 完整通过，各240帧；自定义短窗另有真实PNG回归 | 两片全帧静态及动作原生帧 | 任意外部逐格角色、完整绘制动画 |
| [Collage](../templates/mg-styles/html/collage.html) | [观点隐喻与铰链](../templates/mg-styles/html/collage.js) | B；父子pivot/held几何 | `sixth/collage-a-fixed` 与 `readable/collage-b-readable` 完整通过，各240帧 | 两片全帧静态；B结论100帧，关节/纸偶/撕纸原生帧 | 程序纸偶非照片拼贴；音素口型未实现 |
| [Liquid](../templates/mg-styles/html/liquid.html) | [融合/拉丝/断裂/迟到小滴](../templates/mg-styles/html/liquid.js) | B+独立补丁；最短13帧resolve/末6帧真实液面RGBA恒等 | `current/liquid-{a,b}-current` 完整通过，各240帧 | 两片全帧静态；融合颈/分离/文字mask原生帧 | 隐式场近似，非物理流体 |
| [Morph](../templates/mg-styles/html/morph.html) | [具名形态链](../templates/mg-styles/html/morph.js) | B；弧长轮廓/中介形/hold | `fourth/morph-{a,b}` 完整通过，240/180帧 | 两片全帧静态及形变原生帧 | 单闭合轮廓，非任意多部件拓扑 |
| [Bauhaus](../templates/mg-styles/html/bauhaus.html) | [模数建立与重组](../templates/mg-styles/html/bauhaus.js) | D；共同模数/tick预算/角点旋转界 | `sixth/bauhaus-{a,b}` 完整通过，各180帧 | 两片全帧静态与原生构图 | 自由矢量、完整套色印刷与机械声音 |
| [Synthwave](../templates/mg-styles/html/synthwave.html) | [路标旅程与抵达](../templates/mg-styles/html/synthwave.js) | D；稳定world index/抵达hold | 屏内裁剪修正版 `domain-fixes/synth-{a,b}` 完整技术通过 | 新两片全帧静态及路标边界原生复查 | 图像镀铬/媒介近似，非磁带信号模拟 |
| [Aurora Glass](../templates/mg-styles/html/aurora-glass.html) | [微UI任务焦点](../templates/mg-styles/html/aurora-glass.js) | C；控件完成/显式留读/透视 | `readable/glass-{ideas,reflection}-v2` 完整技术通过，各300帧 | 新两片全部600帧静态条带及12张原生图独审 | 图像空间光学非path tracing；需OffscreenCanvas |
| [Sticker](../templates/mg-styles/html/sticker.html) | [问题/分解/证据/结论](../templates/mg-styles/html/sticker.js) | C；分解预算/真实总览hold | `readable/sticker-{phone,water}-v2` 完整技术通过，各360帧 | 新两片全部720帧静态条带及证据/结论原生图独审 | 程序贴纸非照片；不自动核实数据来源 |
| [Pixel](../templates/mg-styles/html/pixel.html) | [任务/障碍/行动/奖励](../templates/mg-styles/html/pixel.scene.js) | A；不同任务动作/采集消失/奖励尾窗 | `readable/pixel-{a,b}-fixed` 完整通过，300/180帧 | 新两片全帧静态；旧重置边界native修复复验 | 有限程序精灵；非专用像素字库 |
| [HUD](../templates/mg-styles/html/hud.html) | [具名轨迹/检测/确认](../templates/mg-styles/html/hud.js) | D；目标锚点/轨迹/安全区/准确终值 | `domain-fixes/hud-{a,b}` 完整技术通过 | 新两片全帧静态及确认值/安全名带原生复查 | 遥测是显式模拟输入，非真实识别 |
| [Variety Captions](../templates/mg-styles/html/variety-captions.html) | [事件/情绪/逐字/主体禁区](../templates/mg-styles/html/variety-captions.js) | C；真实逐字调用/晚punch拒绝/外扩保护区 | `readable/captions-{a,b}-pixel-fixed` 完整通过，300/180帧 | 新两片全帧静态；短文案/主体避让原生帧 | HTML占位背景示意；非真人节目/自动理解 |

### 连续 3D 两轮分账

旧轮源码 `7decc1187d36586ed1959ff4bfabf962d7bf9527`：两份120帧/4秒真正逐帧Cycles CPU，原生960×540、64 samples、4线程、no denoise，1080p时间线上采样。全部编码/all/strict QC/解码/黑场/帧数和全片静态检查完成；旧穿地问题及严格重生成少量低位像素不同仍保留。不是三张底片的storyboard。

新轮冻结 renderer SHA-256 `c8f1838929c01a56d72f89f0f950b9135175cb2a6cd04e16f2a6aed776f67d40`，可匹配 `dac7457`：`continuous-ground-fixed/3d-{a,b}` 各120帧/4秒，全部编码/all/strict QC 8/8/解码/零黑场/帧数对账完成，不复用旧帧。两片全部240帧静态条带及原生关键帧独审完成；逐帧world mesh最低z全部大于0。固定960×540、64 samples、CPU 4线程、seed=0、animated_seed=false、无降噪；Linux6.18.55、Blender4.3.2，宿主Python3.12.14、FFmpeg7.1.5。A实际帧耗时13.43–47.66秒、中位29.86秒；B21.07–46.62秒、中位29.475秒。这不是原生1080或实时播放验收。

外部Blender源重新生成与冻结媒体消费分账：各仅新进程重渲一次f47，A严格RGBA比较 **FAIL**（518400像素中3个RGB像素差1级），B该单帧RGBA相同。原始FAIL保留，不重试筛选、不改容差；不宣称全部外部重生成逐位一致。冻结资产消费另在全新目录各仅执行一次 `all` 与 strict QC，均退出0；A/B全部120帧的解码RGB哈希与PTS逐行相同，成片SHA也分别相同（`frozen-consumption-replay/comparison.json`）。这是本环境冻结资产消费通过，不改写外部源重生成结论。

证据均在仓外 `/workspace/mg-style-evidence/`；当前优选媒体索引为 `preferred-media-index.json`，历史初始28片索引另存，不将旧音频/条带证据指向新影片。没有上传成片、大文件或字体。浏览器受阻路径及GitHub GraphQL `Forbidden` 均未重试；Draft PR未创建，已推送源码可先独立审查。
