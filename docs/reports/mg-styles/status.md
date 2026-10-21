# MG 模板状态与来源

制作入口是[风格集合](../../../templates/mg-styles/README.md)及[HTML参考页](../../../templates/mg-styles/html/index.html)。本页汇总2026-10-20冻结研究结果，不把旧源码或影片验收转授给后续字节；精确快照、失败和证据根见[验证摘要](validation.md)。

**三条路线分别判断**：legacy Canvas原型、新独立HTML参考、Pillow/NumPy/Blender普通媒体。下表只有普通媒体完成实际编码、timeline all、strict QC、全片解码/帧数/零黑场与全帧静态条带+原生关键帧审查；不是浏览器像素或实时播放。全部HTML的字体/像素/实时播放、全部影片的听审与用户艺术终验仍 **未完成**；固定半秒提示音不是语义声音设计。测试中的帧状态/命令流也不证明可读性或艺术质量。

## 已验证媒体与能力边界

工程路径相对仓外 `/workspace/mg-style-evidence/`；优选集合为 `preferred-media-index.json` 的15款/30片。每款制作卡和语义输入由链接目标维护，此处不再复述源码清单。

| 风格 | 两套优选媒体 | 保留的近似或未验范围 |
|---|---|---|
| [Flat Vector](../../../templates/mg-styles/styles/flat-vector/README.md) | `domain-fixes/flat-{a,b}` | 受控因果图与基本形rig，非任意矢量/关节角色；正式声效未验。 |
| [Line Art](../../../templates/mg-styles/styles/line-art/README.md) | `readable/line-{a,b}-readable`，各240帧 | 有限语义线条，非任意路径/字形动画；末30帧满色源RGBA相同不等于人类读完。 |
| [Isometric](../../../templates/mg-styles/styles/isometric/README.md) | `fifth/iso-a-fixed`、`iso-b`，240/210帧 | 等角2.5D，不是自由透视摄影机、复杂交通或真实AO。 |
| [Soft 3D](../../../templates/mg-styles/styles/soft-3d/README.md) | `continuous-ground-fixed/3d-{a,b}`，各120帧 | Cycles原生540p上采样、有粒噪；解析形变非物理软体。A外部源重生成严格 **FAIL**；HTML仅投影示意。 |
| [CEL Boil](../../../templates/mg-styles/styles/cel-boil/README.md) | `fourth/cel-{a,b}`，各240帧 | 有限程序角色/曝光模拟，非艺术家逐张绘制或任意角色输入。 |
| [Collage](../../../templates/mg-styles/styles/collage/README.md) | `sixth/collage-a-fixed`、`readable/collage-b-readable`，各240帧 | 程序纸偶而非照片拼贴；无音素口型，B结论100帧只证明该例留时。 |
| [Liquid](../../../templates/mg-styles/styles/liquid/README.md) | `current/liquid-{a,b}-current`，各240帧 | 隐式场近似，非物理流体/复杂体积守恒；HTML最短末态6帧不是长文阅读验收。 |
| [Morph](../../../templates/mg-styles/styles/morph/README.md) | `fourth/morph-{a,b}`，240/180帧 | 有限单闭合轮廓，非孔洞/任意多部件拓扑。 |
| [Bauhaus](../../../templates/mg-styles/styles/bauhaus/README.md) | `sixth/bauhaus-{a,b}`，各180帧 | 受控模数几何，非自由矢量或完整套色印刷；机械声未验。 |
| [Synthwave](../../../templates/mg-styles/styles/synthwave/README.md) | `domain-fixes/synth-{a,b}` | 图像镀铬、VHS事件近似，非物理材质/完整磁带信号链。 |
| [Aurora Glass](../../../templates/mg-styles/styles/aurora-glass/README.md) | `readable/glass-{ideas,reflection}-v2`，各300帧 | 图像空间光学非path tracing；局部扭曲需清晰时段阅读，HTML需OffscreenCanvas。 |
| [Sticker](../../../templates/mg-styles/styles/sticker/README.md) | `readable/sticker-{phone,water}-v2`，各360帧 | 程序贴纸，外部来源由输入声明、不自动核实；总览不是细字阅读位置。 |
| [Pixel](../../../templates/mg-styles/styles/pixel/README.md) | `readable/pixel-{a,b}-fixed`，300/180帧 | 有限程序精灵与阈值栅格字体，非专门像素字库/完整芯片音乐。 |
| [HUD](../../../templates/mg-styles/styles/hud/README.md) | `domain-fixes/hud-{a,b}` | 显式模拟遥测，非真实测量、识别或自由事件系统。 |
| [Variety Captions](../../../templates/mg-styles/styles/variety-captions/README.md) | `readable/captions-{a,b}-pixel-fixed`，300/180帧 | 显式反应cue，非自动理解真人节目；HTML背景只是布局示意。 |

## 来源与许可

研究固定于上游 [mg-styles-15@49052d80](https://github.com/Vincentwei1021/mg-styles-15/tree/49052d80bfaf9fa8412cc9710530e5e95a3b8d8c)，阅读LICENSE、README、全部prompt/index/cues及引用场景模块，提取叙事/构图/运动方法后独立实现；未搬入上游代码、影片、品牌、音轨、字体轮廓、照片、贴图或采样。制作卡和两题材用于替换语义关系，不是同组统计卡换色；各款完整Signature与艺术质量不能由入口数或上游AI评分认证。

上游[MIT许可](https://github.com/Vincentwei1021/mg-styles-15/blob/49052d80bfaf9fa8412cc9710530e5e95a3b8d8c/LICENSE)版权为 `Copyright (c) 2026 Vincentwei1021`；将来复制或实质改编代码仍须保留声明。根MIT不自动覆盖第三方OFL字体轮廓、采样、纸纹、HDRI、照片、Natural Earth数据或BSD-3-Clause highlight。模板以程序图形及系统字体为基础，新增资产逐件核许可；本机Noto CJK的SIL-1.1核查不能授予未知字体许可，字体未打包。

完整上游观察与早期能力矩阵见[固定研究原文](https://github.com/CongliangK/framivra/blob/3d9e6622d5d06eecf55ffbe2075da1fa994f93f5/docs/reports/mg-styles/research.md)；逐轮独审见[固定复审原文](https://github.com/CongliangK/framivra/blob/3d9e6622d5d06eecf55ffbe2075da1fa994f93f5/docs/reports/mg-styles/review.md)。PR #254合入只确认源码提交，不补授浏览器、听审或艺术验收。
