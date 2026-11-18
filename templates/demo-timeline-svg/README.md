# JSON 时间线 Demo：锁定 SVG 性能源输入

[可编辑主 Demo](../demo-workbench/README.md)复用本片创作源，提供多后端和完整时间线练习；本页的 portable-v2 输入和验收契约不变。

这是一支 30 秒中文概念动效：JSON 编排画面、文字和同一个短声，再修改落点，承接到成片与品牌。`timeline.json` 是正式 v2 工程；`motion-plan.json` 是离线创作计划，不能交给 vtx 执行。产品以 `clip.format = svg-sequence` 读取静态 SVG，不执行生成器 JavaScript。

## 主性能基线

唯一活动输入为 **portable-v2：30 秒 / 900 帧 / 1920×1080 / 30fps SVG**。[源锁](benchmark/portable-v2.json) 固定 22 叶源码、字体锁、许可与自测；[输入锁](benchmark/portable-v2-input-lock.json) 固定两份完整 WAV 的编码字节、PCM、采样规格及整数生成算法身份。`scripts/demo.py` 逐件校验后只复制白名单，900 个 SVG 从可信源码逐帧重算，字体逐字节校验。README、benchmark 元数据及已有生成物不进入项目，verify 拒绝额外 README/源码。缺锁、缺输入或内容不符即失败，不从待验产物回写 expected。

本版消除私人字体/WAV 移交和特定 NumPy/FFmpeg 音频生成环境：公开固定字体可显式获取，音频本地由 Python 标准库生成。音色已改变，**不是旧基线的等价重编码**，旧成绩不能与本版混比；图形源码、构图、实际字体字节、时长和 cue 落点未改变。新版身份不代表性能提升或所有平台已验收。

### 准备与正式运行

使用包含 scripts/vtx/templates 的源码发行（完整克隆、浅克隆或源码归档），不是仅安装内核 wheel。工具前提见[实际依赖与字体](#实际依赖与字体)。默认不联网、不安装系统字体；以下为只读诊断：

```bash
python3 scripts/demo.py fonts
python3 scripts/demo.py preflight --fonts-dir "/path/to/existing-font-files"
```

`fonts` 列出固定公开来源与包/字体 SHA256，未提供已有目录时报告 MISSING、退出 1；`fonts --fonts-dir DIR` 校验已有字体。preflight 退出 0 只代表报告完整；平台不支持时 `prepare_supported` 为 false，平台、字体或工具有缺项时 `prepare_ready` 为 false。依赖诊断逐项列出 Python/Node/FFmpeg/ffprobe/Inkscape/fc-scan/jsonschema 的缺失或失败；Inkscape 只发现路径和字节，不启动进程。预检不联网、不证明运行库/选面/原生渲染通过。

以下为**显式联网与写操作**：从 Debian 官方公开包下载并校验字体，不执行包安装器；生成音频、帧、缓存及成片。必须在同一 Bash 会话中使用全新物理 RUN，不能复用用户工程或证据目录。prepare 拒绝已有输出、symlink 组件、缺父目录及仓/HOME/cache 内输出；失败保留日志且停止后续步骤。

```bash
set -euo pipefail
RUN=$(python3 -c 'import pathlib,tempfile; print(pathlib.Path(tempfile.mkdtemp(prefix="framivra-promo-")).resolve())')
PROJECT="$RUN/project"
python3 scripts/demo.py prepare --output "$PROJECT" --fetch-fonts > "$RUN/prepare.log" 2>&1
python3 scripts/demo.py verify --project "$PROJECT" > "$RUN/input-check.log" 2>&1
python3 -m vtx -t "$PROJECT/timeline.json" --events-jsonl "$RUN/all.events.jsonl" all > "$RUN/all.log" 2>&1
python3 -m vtx -t "$PROJECT/timeline.json" qc --strict > "$RUN/qc-strict.log" 2>&1
```

示例使用默认正式分段，不设置单段绕行；[帧时钟修复 #359](https://github.com/CongliangK/framivra/pull/359) 已合入。完整解码和 strict QC 仍不能替代全部 900 帧 PTS=`n/30` 的独立检查；历史单段成功、旧多段失败均保留原身份，不自动升级成当前平台通过。自定义 TMPDIR 也须位于仓/HOME/cache 外且无链接组件。

**Windows 尚不支持本主片的 SVG 后端。** 可以在 PowerShell 运行只读预检 `python -X utf8 scripts/demo.py preflight`；`prepare` 会在下载和创建项目之前拒绝执行。Windows 的 HTML 能力检查不替代本主片验收。

离线准备时把 `--fetch-fonts` 换为 `--fonts-dir DIR`，目录须含原文件名的四份锁定字体；两参数互斥。也可先显式运行 `python3 scripts/demo.py fonts --output "$RUN/fonts"`（联网写入新目录），再复用该目录。网络失败或包/字体 SHA 不符会明确失败，不自动采用其他版本或本机同名字体；无系统全局安装。已有完整项目携带字体，正式渲染不需要联网。

prepare 真实生成全 900 SVG、manifest、字体副本和两份新音频，再执行完整输入核验、真实 schema 自测与产品 validate。`--audio-inputs DIR` 仅是可选复用：目录只能含已核验的 portable-v2 `bed-premaster.wav` / `content-cue.wav`；旧 WAV 必须拒绝。回执记为 `canonical-transfer`，不冒充生成，正常跨设备准备不需要该参数。

唯一准备/输入核验入口仍是 `scripts/demo.py prepare/verify`，内部使用模板 `build_demo.js`。不要用裸 `--graphics-only` 绕过完整验证。准备成功不等于全链成功：正式 `all` 包含混音、封装和普通 QC，strict QC 单独执行。Git 只提供可选被测 engine 出处；无 Git/无自身 `.git` 时明确 unknown/null，不借父仓身份，仍可准备普通 Demo；性能比较必须另有可审计的精确 engine 身份。

### 实际运行证据

源规格不存性能成绩。分别保留 engine commit/tree/dirty、argv、脱敏环境/配置、工具/运行库/字体身份、退出码、完整事件、准备回执与输入哈希；dirty engine 还需精确改动和未跟踪依赖身份。实际核全片 900 帧/PTS/音频样本与内容；目标值不是测量值，未执行项写 UNRUN/null，人工视听单列。

制作端到端（prepare + all）、已备输入 all、strict QC 分开计时；首轮字体下载另列，不与离线准备混比。冷项目仅指新 cache/out，不声称 OS 页缓存冷；暖态按回执/缓存字节核复用，并行阶段不能相加冒充墙钟。同输入、工具/环境、配置和质量口径才比较 engine；新实验新建 RUN，不覆盖旧证据。HTML/CDP/CEF、字幕、普通媒体与恢复能力仍由独立机制测试覆盖。

## 实际依赖与字体

- 图形生成用 Node.js 18+ 标准库，无 npm 依赖；音频生成用 Python 3.9+ 标准库，无 NumPy、音乐插件、外部采样、音色库、模型、FFmpeg 编码或私有工程依赖。
- prepare 保留真实 schema 检查：`test_schema.py` 需要 `jsonschema`，缺少即 BLOCKED，不安装、不降级为部分验证。复制到仓外的两项自测可用 `--schema` 指定产品 schema。
- 产品链仍需 Framivra、FFmpeg/ffprobe、Inkscape 和 Fontconfig `fc-scan`。SVG 运行身份支持 Linux ELF 与 macOS arm64，**Windows SVG 不支持**；Linux 另需 system `ldd`。macOS 要求自包含 Inkscape.app 的真实 arm64 Mach-O 程序及可解析依赖，不接受启动脚本。按 `VTX_INKSCAPE`、PATH 顺序定位，macOS 再查系统/用户 Applications。prepare 的产品 validate 也会探测这些身份；preflight 的 `prepare_ready` 不代表实际探针/原生能力已通过。[SVG 后端](../../vtx/render/gfx_svg.py)用私有配置隔离用户环境；face 枚举不等于逐字形选面或跨平台像素等价。

**字体规范**：优先系统通用或业内标准公开字体，避免冷门、需特殊授权安装的字体。本片沿用 Noto Sans CJK SC Regular/Bold 与 DejaVu Sans Book/Bold；family、字重、TTC face index、版本和字体 SHA256 的权威为 [fonts.lock.json](fonts.lock.json)。不能把“系统同名”当成同一依赖，也不能静默 fallback。

公开来源固定为 Debian 官方 [Noto CJK 20220127+repack1-1](https://deb.debian.org/debian/pool/main/f/fonts-noto-cjk/fonts-noto-cjk_20220127+repack1-1_all.deb) 和 [DejaVu 2.37-8](https://deb.debian.org/debian/pool/main/f/fonts-dejavu/fonts-dejavu-core_2.37-8_all.deb)；完整包 SHA、成员路径及下载大小由 [获取模块](../../scripts/demo_fonts.py)维护。标准库仅解出四份字体，先核包再核字体；不需要 Debian 系统、包管理器或原生成机器。其他发行包即使也叫 2.37，字节可能不同，不能替换。字体复制到项目 `assets/fonts/`，manifest 声明相对路径；更换字体须更新独立基线身份并复核视觉，不是修改锁来掩盖差异。

**音频规范**：[生成器](audio/build.py)固定波形/频率表、整数相位、包络、声像、量化与小端 PCM24 布局，不依赖跨平台 libm 浮点结果。保留原音符编排、非静音 bed 和独立可复用短声；新合成音色不声称与原 Scoreweft nylon/FM 相同。输出均为 48kHz 双声道 PCM24，bed 1440000 采样、cue 54400 采样，保留零尾与幅度检查；不预先替代产品响度/限幅终链，不用静音或烘焙 cue 降低混音覆盖。字节确定性不等于听感质量批准。

## 编排和缓存边界

以 `timeline.json` 与 `motion-plan.json` 为准：第一次共同出现为 7.6 秒；13.7 秒经过旧落点不触发内容；第二次为 15.4 秒；17 秒承接结果，没有第三次 cue。两事件都从同一个短声源的第 0 帧播放各 34 帧，不能换成从 0 秒播放的烘焙音轨。源内含设计增益/声像，正式音轨单位增益；终链重新测量并听审。

`gfx/demo.frames.json` 按 clip 局部帧索引，路径相对工程根；SVG 只用受控图形、真实文字和本地 clipPath。`renderer.js` 的 “no audio” 描述只指图形文件。片内中文由 SVG 绘制，正式工程保留必填 `subtitles: []`。

生成源码、字体与历史 vendor 来源闭包仍登记为 `cache_dependencies`，并由源锁校验；保留 vendor 是既有归属/发布许可兼容，不执行其引擎。产品还读取 manifest、每帧 SVG 和字体字节。自建作品改源须重生成重验；缓存失效不会执行 JavaScript。`.generated/preparation.json` 不是产品成功回执。

## 版本迁移与历史恢复

这是[维护者批准](https://github.com/CongliangK/framivra/issues/345#issuecomment-6896296666)的输入版本迁移，不是旧 JSON 回执的无缝兼容：`baseline_id` 增加 `portable-v2`；准备/核验 JSON `schema_version` 为 2；source 从旧 commit/tree 改为 revision/运行叶摘要，音频来源从 NumPy 工具链改为固定整数算法。准备回执还绑定入口与字体获取模块 SHA；脚本变动须重新准备，不能给旧回执补签。

[旧源锁](benchmark/baseline.json)和[旧音频锁](benchmark/input-lock.json)保留原字节，仅供历史审计，不是第二活动基线。旧源码、许可证、原准备/核验流程完整保存在[固定提交 5852490](https://github.com/CongliangK/framivra/tree/5852490c28089a41ea4d9a54e45fe45eeb97b1a8)；它保留旧 macOS canonical/私下 WAV 移交约定，但该约定不再是新项目路径。更早 Linux 锁仍可从[原固定版本](https://github.com/CongliangK/framivra/blob/f71e09d56b21346834b8a6853798d4568cc90bbb/templates/demo-timeline-svg/benchmark/input-lock.json)恢复。

核验旧项目须在上述固定提交的独立检出中运行原 `scripts/demo.py verify --project <旧项目>`，不要把旧锁复制给新脚本；恢复检出属于写操作，不覆盖现有工程或日常 checkout。新 verify 对旧 baseline_id 明确拒绝并给恢复版本。旧证据、旧成绩不改身份，输入变化另立新版、独立核验并重测；CI/门禁变更继续遵守[写态红线](../../AGENTS.md#写态红线)。

## 来源与许可

原创视觉沿用 `timeline-motion-r1`，来源字节锚在 `SOURCES.json`。Scoreweft 原 MIT synth 子集、固定提交与逐文件哈希保留在 `vendor/scoreweft/`，未改其源件；新音频不导入它。新源码遵守仓库 MIT 许可，完整上游许可仍随项目保留。

Noto CJK 适用 SIL OFL 1.1；DejaVu 使用 Bitstream Vera 许可，DejaVu 修改为公有领域；全文见 `licenses/`。字体、大 WAV、生成帧、缓存和成片不提交模板仓库。下载字体不分发或安装媒体工具二进制。

`test_demo.js` 与 `test_schema.py` 保留源/字体身份、全帧静态重算、语义时间点、音轨绑定和真实 schema 检查；独立标准库测试另外验证公开字体获取失败、完整音频固定哈希/PCM/非静音/尾零及新旧身份拒混。实际字体选中、音画同步、响度、原生渲染和艺术质量仍须真实运行与复审，不由单元测试替代。
