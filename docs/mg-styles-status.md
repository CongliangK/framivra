# 15 款模板当前审查矩阵

源码已推送：[`3d050d452497c66a70064b921de2cf43476696ac`](https://github.com/CongliangK/framivra/tree/3d050d452497c66a70064b921de2cf43476696ac/templates/mg-styles)。[HTML 总入口](../templates/mg-styles/html/index.html) 可直接打开；每页的制作卡、两套不同题材及可编辑语义内容均在相邻场景 JS。使用方法见 [HTML 说明](../templates/mg-styles/html/README.md)。本表是当前摘要，历史、命令与影片哈希以 [验证记录](mg-styles-validation.md) 为准。

代码验证：媒体 `dac7457fe628001cedbb648776affdc0b9ef76c9` 的 126 项通过，77 文件前后哈希一致；HTML `436306580cd86e4b6080611702b806655d931fae` 的 11 项通过，40 文件前后一致；Liquid 后续 `3d050d4` 最短窗补丁单独通过实际液面 RGBA/命令测试及独立复验。表中 A/B/C/D 对应 `tests/test_mg_html_{a,b,c,d}.py`，另有共享播放器、跨款测试；**均不是浏览器像素验收**。

媒体“完整技术通过”指实际源帧、编码、现有 timeline `all`、strict QC 8/8、完整解码、零黑场与帧数对账。图像审查单列；“全帧静态”指逐幅缩略条带并结合原生关键帧，不是实时播放。所有 HTML 尚缺浏览器字体/像素/实时播放验证；所有声音均无听感验收，实际影片仍为固定半秒占位提示音，不能当成语义声音设计完成。

| 款式与 HTML | 制作卡 / 两题材源码 | HTML 代码测试 | 媒体技术验证（两题材） | 图像审查 | 未验证能力 / 近似 |
|---|---|---|---|---|---|
| [Flat Vector](../templates/mg-styles/html/flat-vector.html) | [因果图与主体擦场](../templates/mg-styles/html/flat-vector.js) | D；重排末态/6帧hold、palette | 历史两片完整通过；连线修正版 `domain-fixes/flat-{a,b}` all已通过，额外后验汇总中 | 旧两片全帧静态；新片条带/原生连线逐项复查 | 任意外部矢量素材、正式声效 |
| [Line Art](../templates/mg-styles/html/line-art.html) | [语义线条接力](../templates/mg-styles/html/line-art.scene.js) | A；乱序/内容/揭示预算 | `readable/line-{a,b}-readable` 完整通过，各240帧 | 新两片全帧静态；末30帧满色源RGBA恒等 | 任意外部路径与字形动画 |
| [Isometric](../templates/mg-styles/html/isometric.html) | [依赖装配](../templates/mg-styles/html/isometric.scene.js) | A；实际30°/三轴等长、依赖 | `fifth/iso-a-fixed` 与 `iso-b` 完整通过，各240帧 | 两片全帧静态及原生装配/末态 | 自由摄影机；不能以透视替代等角 |
| [Soft 3D](../templates/mg-styles/html/soft-3d.html) | [材质与章节布局](../templates/mg-styles/html/soft-3d.scene.js) | A；章节exclusive-end/6帧settled | 旧连续 `continuous/3d-{a,b}` 各120帧完整通过；新穿地修正版渲染中，未编码/QC | 旧连续全部240帧静态+原生动作；新几何各120帧检查无穿地，不是新片像素验收 | HTML仅投影/构图示意，非PBR；旧片穿地/上采样及外部再生成低位差异保留 |
| [CEL Boil](../templates/mg-styles/html/cel-boil.html) | [曝光表与角色动作](../templates/mg-styles/html/cel-boil.js) | B；实际sample→pose/中间姿势 | `fourth/cel-{a,b}` 完整通过，各240帧；自定义短窗另有真实PNG回归 | 两片全帧静态及动作原生帧 | 任意外部逐格角色、完整绘制动画 |
| [Collage](../templates/mg-styles/html/collage.html) | [观点隐喻与铰链](../templates/mg-styles/html/collage.js) | B；父子pivot/held几何 | `sixth/collage-{a,b}-fixed` 完整通过，240/180帧 | 两片全帧静态；关节/纸偶/撕纸原生帧 | 程序纸偶非照片拼贴；音素口型未实现 |
| [Liquid](../templates/mg-styles/html/liquid.html) | [融合/拉丝/断裂/迟到小滴](../templates/mg-styles/html/liquid.js) | B+独立补丁；最短13帧resolve/末6帧真实液面RGBA恒等 | `current/liquid-{a,b}-current` 完整通过，各240帧 | 两片全帧静态；融合颈/分离/文字mask原生帧 | 隐式场近似，非物理流体 |
| [Morph](../templates/mg-styles/html/morph.html) | [具名形态链](../templates/mg-styles/html/morph.js) | B；弧长轮廓/中介形/hold | `fourth/morph-{a,b}` 完整通过，各240帧 | 两片全帧静态及形变原生帧 | 单闭合轮廓，非任意多部件拓扑 |
| [Bauhaus](../templates/mg-styles/html/bauhaus.html) | [模数建立与重组](../templates/mg-styles/html/bauhaus.js) | D；拍表/模数/轴心 | `sixth/bauhaus-{a,b}` 完整通过，各180帧 | 两片全帧静态与原生构图 | 自由矢量、完整套色印刷与机械声音 |
| [Synthwave](../templates/mg-styles/html/synthwave.html) | [路标旅程与抵达](../templates/mg-styles/html/synthwave.js) | D；稳定world index/抵达hold | 历史两片完整通过；屏内裁剪修正版 `domain-fixes/synth-{a,b}` all已通过，额外后验汇总中 | 旧两片全帧静态；新路标边界原生/条带复查 | 图像镀铬/媒介近似，非磁带信号模拟 |
| [Aurora Glass](../templates/mg-styles/html/aurora-glass.html) | [微UI任务焦点](../templates/mg-styles/html/aurora-glass.js) | C；控件完成/显式留读/透视 | 历史两片完整通过；新 `readable/glass-*-v2` 阅读修正版生成中，未计通过 | 旧两片全帧静态及透视/折射原生帧；新片待验 | 图像空间光学非path tracing；需OffscreenCanvas |
| [Sticker](../templates/mg-styles/html/sticker.html) | [问题/分解/证据/结论](../templates/mg-styles/html/sticker.js) | C；分解预算/真实总览hold | 历史两片完整通过；新 `readable/sticker-*-v2` 阅读修正版生成中，未计通过 | 旧两片全帧静态及证据原生帧；新片待验 | 程序贴纸非照片；不自动核实数据来源 |
| [Pixel](../templates/mg-styles/html/pixel.html) | [任务/障碍/行动/奖励](../templates/mg-styles/html/pixel.scene.js) | A；动作窗/整数格点/内容 | `readable/pixel-{a,b}-fixed` 完整通过，300/180帧 | 新两片全帧静态；旧重置边界native修复复验 | 有限程序精灵；非专用像素字库 |
| [HUD](../templates/mg-styles/html/hud.html) | [具名轨迹/检测/确认](../templates/mg-styles/html/hud.js) | D；目标锚点/轨迹/安全区 | 历史两片完整通过；新 `domain-fixes/hud-{a,b}` all已通过，额外后验汇总中 | 旧两片全帧静态；新确认值/安全名带原生复查 | 遥测是显式模拟输入，非真实识别 |
| [Variety Captions](../templates/mg-styles/html/variety-captions.html) | [事件/情绪/逐字/主体禁区](../templates/mg-styles/html/variety-captions.js) | C；真实逐字调用/晚punch拒绝/外扩保护区 | `readable/captions-{a,b}-pixel-fixed` 完整通过，300/180帧 | 新两片全帧静态；短文案/主体避让原生帧 | HTML占位背景示意；非真人节目/自动理解 |

### 连续 3D 两轮分账

旧轮源码 `7decc1187d36586ed1959ff4bfabf962d7bf9527`：两份120帧/4秒真正逐帧Cycles CPU，原生960×540、64 samples、4线程、no denoise，1080p时间线上采样。全部编码/all/strict QC/解码/黑场/帧数和全片静态检查完成；旧穿地问题及严格重生成少量低位像素不同仍保留。不是三张底片的storyboard。

新轮冻结 renderer SHA-256 `c8f1838929c01a56d72f89f0f950b9135175cb2a6cd04e16f2a6aed776f67d40`，可匹配 `dac7457`：`continuous-ground-fixed/3d-{a,b}` 同样固定profile，不复用旧帧。2026-10-20 16:29 UTC 最近只读进度A19/120、B20/120；尚未编码/QC/全片图像检查。另只读求值各120帧的真实mesh最低z全部大于0，camera各120种状态，这仅为几何证据。

证据均在仓外 `/workspace/mg-style-evidence/`；当前优选媒体索引为 `preferred-media-index.json`，历史初始28片索引另存，不将旧音频/条带证据指向新影片。没有上传成片、大文件或字体。浏览器受阻路径及GitHub GraphQL `Forbidden` 均未重试；Draft PR未创建，已推送源码可先独立审查。
