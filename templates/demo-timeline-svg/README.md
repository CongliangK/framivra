# JSON 时间线 Demo：静态 SVG 生产输入

这是一支 30 秒中文概念动效：JSON 编排画面、文字和同一个短声，再修改落点，承接到成片与品牌。`timeline.json` 是正式 v2 工程；`motion-plan.json` 是离线生成器的创作计划，不能交给 vtx 当工程执行。旧 Demo 和 `standard-demo` 性能输入均独立保留。

本模板以 `clip.format = svg-sequence` 接入产品。产品读取静态 SVG 帧，不执行生成器 JavaScript。原生成片、原生音频终链、人工正常速度视听验收均为 **UNRUN**；代码/生成输入的检查不能代替这些验收，也不代表 Mac、浏览器或 HTML 路线通过。

## 准备与正式运行

从仓库根目录执行。只读检查不会准备素材：

```bash
node templates/demo-timeline-svg/build_demo.js --check
node templates/demo-timeline-svg/test_demo.js
env PYTHONDONTWRITEBYTECODE=1 python3 templates/demo-timeline-svg/test_schema.py
```

以下命令明确写入本模板内被忽略的生成目录。准备脚本生成全部 SVG、静态序列 manifest、固定字体副本，以及母带前的音乐和可复用短声；不会下载安装任何依赖：

```bash
node templates/demo-timeline-svg/build_demo.js --prepare
node templates/demo-timeline-svg/test_demo.js --prepared
```

需要包含 SVG sequence producer 的 Framivra 版本。准备完毕并完成相应复审后，正式全链命令为：

```bash
python3 -m vtx -t templates/demo-timeline-svg/timeline.json all
```

此命令使用下述规则定位的已有 Inkscape 渲染 SVG，再走产品既有视频、音频、封装和 QC。无需新增 HTML/浏览器后端设置。准备脚本未调用此命令；仅拿到 SVG 或 WAV 不表示原生全链通过。

比较完整制作耗时时，应把 `--prepare` 和 `all` 放在同一计时窗口，不能只统计已经备好 SVG 的产品阶段。正式验收还应分别保留准备、图形、音频和全链回执；本模板的普通自测不执行这项性能测量。

如只开发视觉，可用 `--prepare --graphics-only`；它不会生成音频，不能据此声称工程已完整准备。重复准备会覆盖上述生成器拥有的已生成输入。需要保留另一次实验时，先把本模板复制到新项目目录再运行。

## 实际依赖与字体

- 图形生成：Node.js 标准库，无 npm 依赖。当前实现按 Node 18+ API 编写。
- 完整结构自测：`test_schema.py` 使用已有 `jsonschema` 校验仓库真实 `vtx/timeline.schema.json`，这是仅用于测试的依赖。缺少时明确返回 BLOCKED，不安装、不降级成部分验证、不报告通过。复制到仓外的模板，两项自测均可用 `--schema` 指定对应产品 schema。
- 程序配乐：Python 3.9+、NumPy、FFmpeg。顺序合成，生成器子进程超时受限，不包含外部采样、音色库、模型或下载器。首次审计环境为 Python 3.12.14、NumPy 2.3.5、FFmpeg 7.1.5；不承诺不同运行时跨版本位级相同。
- 原生输出：SVG 运行时身份实现支持 Linux ELF 与 macOS arm64，Windows 不支持。必须已有 Framivra、Inkscape、Fontconfig 的 `fc-scan`、FFmpeg/FFprobe；Linux 另需 system `ldd`。macOS 要求自包含 Inkscape.app 的真实 arm64 Mach-O 可执行文件及可解析依赖，不接受启动脚本。按 `VTX_INKSCAPE`、PATH 顺序定位；macOS 未找到时再查系统或用户 Applications 中的 Inkscape.app。后端版本、二进制及运行库身份和字体 face 身份由产品回执记录；私有配置隔离用户环境，仅使用声明字体。实现边界见 [SVG 后端](../../vtx/render/gfx_svg.py)，代码支持不等于对应平台或作品已通过真实渲染、音频终链与人工视听验收；resvg 不是此模板的生产依赖。
- 真实文本字体：Noto Sans CJK SC Regular/Bold 与 DejaVu Sans Book/Bold。确切文件、字重、TTC face index、版本和 SHA256 的唯一清单是 `fonts.lock.json`。不接受静默 fallback；准备时必须匹配固定字节。

生成器会先检查项目内已准备的字体，再检查锁文件内的 Linux 系统路径提示。该提示不是 Mac 路径，也不保证其他电脑已装字体。已有相同字体文件时可指定包含清单所列原文件名的目录：

```bash
node templates/demo-timeline-svg/build_demo.js --prepare --fonts-dir /path/to/existing-font-files
```

字体不会提交到本模板仓库；准备阶段复制到 `assets/fonts/`，manifest 显式引用这些项目根相对路径，产品使用隔离字体配置读取。更换字体或字形需要重新审核字体锁和视觉，不以同名 family 冒充同一依赖。许可全文见 `licenses/`。

## 编排和缓存边界

时间以正式 `timeline.json` 与 `motion-plan.json` 为准：第一次共同出现为 7.6 秒；13.7 秒经过旧落点时不触发内容；第二次为 15.4 秒；17 秒承接已经发生的结果，没有第三次 cue。

`gfx/demo.frames.json` 按 clip 局部帧索引逐帧对应；所有清单路径相对工程根目录。SVG 只使用受控图形、真实文本和本地 clipPath 引用。源 `renderer.js` 保留原运动实现；其 SVG 描述中的 “no audio” 指图形文件本身，正式工程音频来自独立音轨。

`sources` 分别指向母带前 bed 与同一份短声 WAV；两个音频事件都从这个短声源的第 0 帧播放。完整 cue 试听轨和试听母带不进入正式工程，不能替换成一个从 0 秒起播放的烘焙事件。源内已有设计增益/声像，正式音轨使用单位增益；产品音频终链须重新测量并听审，不能套用试听母带结论。

片内中文已由 SVG 文字绘制，因此正式工程显式保留 `subtitles: []`。它是产品 schema 的必填根字段，空字幕列表也不能省略。

生成器、运动计划、渲染源码、音频源码、最小 synth 依赖与实际字体均已显式登记在 clip 的 `cache_dependencies`。生产接口还读取 manifest、每帧 SVG 与字体实际内容；编辑生成源码后必须重新准备，缓存失效本身不会替你执行 JavaScript。`.generated/preparation.json` 记录输入和帧哈希，不是原生成功回执。

## 来源与许可

原创视觉沿用已审的 JSON 时间线构图和 `timeline-motion-r1` 运动源码，没有复用旧复杂版本；来源字节锚见 `SOURCES.json`。原配乐的音符、短声、增益、声像和母带前导出保持一致，适配仅改为项目相对的固定依赖，去掉独立试听母带处理。

Scoreweft 的 MIT 程序合成最小依赖随包提供，固定提交、逐文件 SHA256、上游链接见 `vendor/scoreweft/SOURCES.json`，完整许可见 `vendor/scoreweft/LICENSE`。该子集仅含 synth 路线必需源码，未修改 vendored 文件；不包括上游其他音色、采样、谱、预览或模型。新项目源码遵守仓库根 MIT 许可，依赖许可各自保留。

Noto CJK 适用 SIL OFL 1.1；DejaVu 使用 Bitstream Vera 许可，DejaVu 修改为公有领域。NumPy、FFmpeg、Inkscape 仅使用用户已有安装，不随模板分发工具二进制。生成帧、字体副本、大 WAV、缓存和成片均在本模板 `.gitignore` 中。

`test_demo.js` 保源文件/字体身份、静态生成确定性、普通时间点状态、音轨绑定和真实 schema 根必填字段；`test_schema.py` 另对完整工程做真实产品 schema 结构校验。它们不保运行时语义/媒体检查、真实产品渲染、实际字体选中、音画同步、响度或艺术质量。后者必须由真实运行和独立复审给出。
