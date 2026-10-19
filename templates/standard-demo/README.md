# standard-demo —— 公开、版本化、可审计的 demo 工程输入包（#177 / #91-1）

> 唯一入口：`scripts/demo.py`（preflight / prepare / verify 三步）。本包只冻结
> 公开输入定义（timeline/HTML/CSS/JS/字体/引用），所有渲染、preview、QC、CEF、
> 性能与人工视听结论一律 **UNRUN**。专题说明见 `docs/demo-fixture.md`（docs 层）。

## profiles

| profile | 版本 | 内容 | 状态 |
| --- | --- | --- | --- |
| quick | v1 | 1920×1080·30fps·90 帧；复用 templates/skill-demo 的 testsrc2+sine 生成法、timeline 结构与 gfxA 页面（来源文件不改） | 可 prepare |
| standard | v1 | 360 帧：两段 180 帧普通视频段、跨段字幕（横穿第 180 帧剪辑点）、双 gfx 页（帧驱动 canvas + 静态 DOM）、CJK/透明/低 alpha；结构自 tests/run_e2e.py 适配 | 可 prepare |
| gfx-stress | v1 | 仅以固定 commit（70b6853）+ suite_manifest 完整 SHA256 + S1–S7 页面完整哈希引用现存冻结套件 tests/gfx_bench_suite；不复制不重定义，S8 保持历史未纳入 | 引用型，UNRUN |

standard 的双页与 gfx-stress 多页用于冻结输入覆盖/渲染压力验证，不是纯 MG 创作拆页范本；新作品组织见 [HTML 创作组织](../../docs/skill/references/gfx.md#html-创作组织)。

## 冻结锚

`benchmark/benchmark_manifest.json`（benchmark_id=`framivra-standard-demo`）锁：
generator（scripts/demo.py）SHA256、ffmpeg/ffprobe 工具链身份（version_line +
binary SHA256）、全部输入资产完整 SHA256 与大小、解码级规格（帧数/尺寸/帧率/
PTS 摘要/PCM 指纹/原始像素指纹）、媒体生成参数、coverage required/optional。
标准输入是先冻结的期望值：prepare 时只允许验证，不自动接受新输出改锁；
未列入工具链 = unsupported/unverified。重冻结走
`python3 scripts/demo.py freeze-manifest`（维护操作，需真实 ffmpeg）。

## 字体

standard 随包字体 `profiles/standard/v1/fonts/NotoSansSC-VF.ttf`
（Noto Sans SC，[OFL-1.1](profiles/standard/v1/fonts/OFL.txt)，官方源
google/fonts 固定 hash 获取）。页面经包内 `@font-face` 本地引用，不外连、
不依赖系统 font-family 名。实际字形消费与视觉像素正确性属后续 renderer 验收。

## 快速入口

```bash
env python3 scripts/demo.py preflight --profile standard   # 只读体检（版本化 JSON）
```

prepare/verify 属写态/校验命令，用法与边界见 `docs/demo-fixture.md`
（不在此复述，一处事实一处 home）。
