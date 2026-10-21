# MG 风格模板

按风格阅读和取用：每款目录集中制作思路、典型 HTML、两题材、媒体输入与独立脚本。制作卡在该款场景源码与页面中维护；各款能力限制在其 README，三路线共通资格见[验收边界](#验收边界)，不能由入口或测试项数推定成片完成。

| 风格包 | 制作主线 |
|---|---|
| [cel-boil](styles/cel-boil/README.md) | 角色动作与曝光表 |
| [isometric](styles/isometric/README.md) | 按依赖装配与激活 |
| [flat-vector](styles/flat-vector/README.md) | 主体触发因果与关系重排 |
| [line-art](styles/line-art/README.md) | 线条接力与具象叙事 |
| [soft-3d](styles/soft-3d/README.md) | 材质、关系与连续三维章节 |
| [morph](styles/morph/README.md) | 具名形态链 |
| [sticker](styles/sticker/README.md) | 问题、分解、证据与结论 |
| [hud](styles/hud/README.md) | 轨迹、检测与确认 |
| [collage](styles/collage/README.md) | 观点隐喻与铰接纸偶 |
| [aurora-glass](styles/aurora-glass/README.md) | 微 UI 任务焦点 |
| [bauhaus](styles/bauhaus/README.md) | 模数与机械重组 |
| [synthwave](styles/synthwave/README.md) | 路标旅程与抵达 |
| [pixel](styles/pixel/README.md) | 任务、障碍、行动与奖励 |
| [liquid](styles/liquid/README.md) | 汇聚、融合、分离与揭示 |
| [variety-captions](styles/variety-captions/README.md) | 反应事件、逐字与主体禁区 |

本包独立实现一般设计语言，不是原片复刻；资产使用须遵守[来源与许可](#来源与许可)。

## 选择入口

- **学制作、换题材**：进入上表某款 README，打开其典型 HTML；也可用 [HTML 总预览](html/index.html)。两题材、短制作卡、播放器与 JSON 编辑均在款内。共享[HTML 捕获协议](html/README.md)；HTML 尚无浏览器字体/像素/完整播放验收，也没有实际音轨。
- **生成独立媒体工程**：用该款 README 的 `build_media.py --content ... --dry`，按真实角色、任务、轨迹、章节等语义编辑 JSON；源码在款内 `scripts/`。需要的 Pillow/NumPy/Blender/FFmpeg 与本地字体按所选路径提供，缺失明确失败。
- **试验早期 Canvas prototype**：仍用根 `build.py`。十五款保留 prototype 状态，统一标题/条目接口不足以表达完整叙事，不能继承媒体路线的验证。每款实现位于自己的 `scripts/prototype.js`；共用 `runtime.js` 与 `prototype-helpers.js`，构建仅打包所选款实现及公共依赖。

款内 HTML 的题材编辑与[内核只读时间线预览](../../docs/skill/references/edit.md#时间线预览人审用)是不同工具，不互换输入或验收结论。当前回归入口为 [`tests/gfx/test_mg_html.py`](../../tests/gfx/test_mg_html.py)（HTML 命令流与控制逻辑）和 [`tests/gfx/test_mg_styles.py`](../../tests/gfx/test_mg_styles.py)（prototype）；逐款媒体测试同在 `tests/gfx/`，不代表真实浏览器像素或完整成片验收。

## 媒体工程与交付

两个公开 CLI 均从仓根调用；`--style` 名称保持上表 slug。`--content` 为 UTF-8 JSON，媒体输入按本款语义接口解释，不与 HTML 内嵌 JSON 或 prototype schema 互换。`--frames` 控制整数帧总长，30fps；修改时须同步 `[in,out)` 窗口和阅读预算。

先用款内 `--dry` 命令预检。去掉 `--dry` 会渲染源素材、编码并创建新工程，拒绝覆盖已有目录；部分失败保留日志与中间文件。随后按[内核路由](../../docs/skill/SKILL.md)依次 validate 和完整 all，生成成功不等于技术、艺术、声音验收完成。素材、cache/out、截图和字体保存在仓外，不提交 Git。媒体无 HTML gfx 时不会启动浏览器。

输出保存捕获的 renderer/composer 源码与哈希；系统字体不复制。默认本机 Noto CJK 的来源和 SIL OFL 1.1 另记回执，指定 `font_path` 时由调用方核查许可与覆盖；系统字体与环境改变可能影响字形。三维源重生成和冻结媒体消费分别判定，已知严格差异见[验收边界](#验收边界)，不改变门槛。

反应字幕须提供有权使用的本地背景视频，其长度/格式/音轨仍须经 validate，换背景必须重编 cue。三维 `full` 为逐帧 Blender，`storyboard` 为底片合成，后者不能冒称完整 Blender 帧序列。声音目前是未同步语义拍点的固定参考提示音，不是完成的配乐或声音设计。

## 验收边界

资格依据是 **2026-10-20 Linux 冻结研究**，不是当前源码或环境重跑。legacy Canvas prototype、独立 HTML 与 Pillow/NumPy/Blender 普通媒体分别验收；旧源码、影片、回执和 PR 合入不向后续字节转授通过。

- **Canvas / HTML**：输入、帧状态/命令流及局部真实 RGBA 检查不证明可读性；浏览器字体/像素、真实 HTML 成片与实时播放仍 **UNRUN**。历史补音频只修复音频支路，不代表浏览器或 all 成功，未绕过权限或 sandbox。
- **普通媒体**：优选15款/30片通过源帧→编码→timeline all→strict QC8/8→全片解码/帧数/零黑场，以及全帧静态条带和选定原生关键帧审查。普通 broll/timeline 无 gfx clips，不授予浏览器资格；静态全帧不等于逐帧原生全检、实时观看、听审或用户艺术终验，后三项仍未完成；局部复验不覆盖全部合法输入。
- **声音**：固定半秒网格提示音未消费视觉 cue，音频数值检查不证明听感或语义同步。旧28片的音频/条带索引不能证明或改指优选新30片；新影片须由自身证据绑定。AAC 尾 padding 不等于 stream 错位，双声道元数据不等于空间声。
- **三维**：A 外部源重生成严格 **FAIL**（3像素差1级），B仅 f47 单帧 **PASS**；冻结媒体消费通过不能覆盖源重生成失败。storyboard 不是连续 Blender 序列；参数、失败引擎及精确边界归 [Soft 3D](styles/soft-3d/README.md#验收限制)。
- **挂载**：历史六件 live 挂载 MISSING；源码验证不证明用户挂载 BAD=0，真实挂载仍未验，须按[文档消费面](../../docs/DOC-CHARTER.md#消费面)单独核验。

`source-receipt.json` 只绑定执行时捕获的源码、输入与环境，live 源码不继承旧回执。历史媒体、大文件与字体未上传 Git；[固定状态原文](https://github.com/CongliangK/framivra/blob/2f14b574f9ed431978e988ee2906c58912b45488/docs/reports/mg-styles/status.md#已验证媒体与能力边界)保留优选媒体路径，[证据身份与逐批验证](https://github.com/CongliangK/framivra/blob/2f14b574f9ed431978e988ee2906c58912b45488/docs/reports/mg-styles/validation.md#结论与证据身份)保留仓外原件、索引和音频证据，不能把历史路径当公开下载地址。

冻结 provenance：媒体126项对应 `dac7457fe628001cedbb648776affdc0b9ef76c9`；HTML历史11项对应 `436306580cd86e4b6080611702b806655d931fae`、最终13项对应 `a29d82ca209d589127901de5766b382263c19454`；`f3ea10e3c2b4662614cf0a5b2d0dc476d950a576` 的 D4 recipe 仅轻量检查，不是完整 suite 重跑。[冻结源码与独审](https://github.com/CongliangK/framivra/blob/2f14b574f9ed431978e988ee2906c58912b45488/docs/reports/mg-styles/validation.md#冻结源码与独审)及[失败与未验原文](https://github.com/CongliangK/framivra/blob/2f14b574f9ed431978e988ee2906c58912b45488/docs/reports/mg-styles/validation.md#失败与未验项目)保留精确归属与原始失败；旧失败不因修正版通过或文档整理变绿。

## 来源与许可

研究固定于上游 [mg-styles-15@49052d80](https://github.com/Vincentwei1021/mg-styles-15/tree/49052d80bfaf9fa8412cc9710530e5e95a3b8d8c)，提取叙事/构图/运动方法后独立实现；未搬入上游代码、影片、品牌、音轨、字体轮廓、照片、贴图或采样。制作卡和两题材用于替换语义关系，不是同组统计卡换色；各款完整Signature与艺术质量不能由入口数或上游AI评分认证。

上游[MIT许可](https://github.com/Vincentwei1021/mg-styles-15/blob/49052d80bfaf9fa8412cc9710530e5e95a3b8d8c/LICENSE)版权为 `Copyright (c) 2026 Vincentwei1021`；将来复制或实质改编代码仍须保留声明。根MIT不自动覆盖第三方OFL字体轮廓、采样、纸纹、HDRI、照片、Natural Earth数据或BSD-3-Clause highlight。模板以程序图形及系统字体为基础，新增资产逐件核许可；本机Noto CJK的SIL-1.1核查不能授予未知字体许可，字体未打包。

完整上游观察与早期能力矩阵见[固定研究原文](https://github.com/CongliangK/framivra/blob/3d9e6622d5d06eecf55ffbe2075da1fa994f93f5/docs/reports/mg-styles/research.md)；逐轮独审见[固定复审原文](https://github.com/CongliangK/framivra/blob/3d9e6622d5d06eecf55ffbe2075da1fa994f93f5/docs/reports/mg-styles/review.md)。

## Canvas prototype 接口

```bash
python3 templates/mg-styles/build.py --style isometric --output /tmp/my-mg-prototype --frames 180 --dry
```

去掉 `--dry` 创建单 HTML 入口、显式脚本缓存依赖、timeline 与 cue.wav；随后仍须 validate 和完整 all。普通动态 gfx 协议不适用于拒绝 JavaScript 的 strict 资源策略，不声明静态跨度。

`--content` 可省略字段，拒绝未知字段：

```json
{"title":"从发现到交付","subtitle":"先提出问题，再让证据说话","items":[{"label":"研究","value":30},{"label":"制作","value":75}],"palette":["#faf5e8","#202730","#e66b42","#49a69d"],"seed":7,"pace":1.0,"hold_frames":12}
```

`items` 为一至六条 label/value，value 0–100仅是视觉量，不自动解释统计单位；palette 为四个 RGB。标题最多180字符、label最多80字符；Canvas测宽缩排，最小24px（原设小字保持原字号下限），单行放不下或换行报错，长度上限不保证任意构图可读。`seed` 是有符号32位整数；`pace` 为0.5–2的节拍曲线指数（小于1前段更快，端点不变）；`hold_frames` 是小于总长一半的末态停留整数帧。

`variety-captions` prototype是在横屏中组织竖屏区域，不是原生9:16输出；line-art固定正弦路径、liquid圆形重叠和玻璃局部采样均有明确能力缺口。模拟Canvas乱序重放只证明相应命令与状态，不证明浏览器像素或艺术质量。交付组织先读[内核 HTML 创作组织](../../docs/skill/references/gfx.md#html-创作组织)。
