# JSON 时间线 Demo：主宣传与性能源输入

这是一支 30 秒中文概念动效：JSON 编排画面、文字和同一个短声，再修改落点，承接到成片与品牌。`timeline.json` 是正式 v2 工程；`motion-plan.json` 是离线创作计划，不能交给 vtx 执行。产品以 `clip.format = svg-sequence` 读取静态 SVG，不执行生成器 JavaScript。

## 主性能基线

[baseline.json](benchmark/baseline.json) 锁定唯一30秒基线的22叶运行源码、依赖、字体锁、许可与自测。`scripts/demo.py` 逐件核对完整字节后仅复制白名单；README、benchmark元数据和已有生成物不进入RUN，verify拒绝额外README或源码。commit/tree仅记创作出处，不是运行读取地址或被测engine身份；来源与依赖仍见 `SOURCES.json`、`fonts.lock.json` 和 `vendor/scoreweft/SOURCES.json`。源码匹配不代表性能、原生全链或人工视听通过。

`benchmark/input-lock.json` 只锁独立复核的两个完整 WAV 编码/PCM/采样规格及生成出处；900 个 SVG 由固定可信源码逐帧重算，字体按原锁核实际字节。缺锁、内容不符或白名单源文件缺失即停止，不从待验目录回写 expected，不联网获取或接受任意 HEAD 字节。HTML/CDP/CEF、字幕、普通媒体及恢复能力由独立机制测试覆盖，不能由 SVG 主片代替。

### 准备与正式运行

从可信源码根目录执行；完整克隆、`--depth 1` 浅克隆和包含 scripts/vtx/templates 的源码归档均可，不依赖历史 Git 对象。Git 只用于可选 engine 出处：无 Git、无 `.git` 或只能找到父仓时明确记录 unknown/null 与原因，不借父仓身份。普通 Demo 可以继续准备；正式性能比较必须先有独立可审计的精确 engine 身份，不能拿创作源 commit 代替。唯一准备/输入核验入口是 `scripts/demo.py`；它复用模板原 `build_demo.js`，不包含另一套媒体配方。先检查已有固定字体，不下载安装或以同名字体替代。preflight 是只读诊断：退出 0 只代表报告完整，须另看 `prepare_ready`，不表示准备或渲染成功。

```bash
FONT_DIR="/path/to/existing-font-files"
python3 scripts/demo.py preflight --fonts-dir "$FONT_DIR"
```

以下为**写操作**，必须在同一 Bash 会话中使用全新 RUN；不能复用用户工程、cache/out 或已有证据目录。用物理路径避免临时目录的系统 symlink 别名；prepare 拒绝已有输出、symlink 组件、缺父目录及仓/HOME/cache 内输出，失败保留原始日志并停止后续阶段。

```bash
set -euo pipefail
RUN=$(python3 -c 'import pathlib,tempfile; print(pathlib.Path(tempfile.mkdtemp(prefix="framivra-promo-")).resolve())')
PROJECT="$RUN/project"
python3 scripts/demo.py prepare --output "$PROJECT" --fonts-dir "$FONT_DIR" > "$RUN/prepare.log" 2>&1
python3 scripts/demo.py verify --project "$PROJECT" > "$RUN/input-check.log" 2>&1
python3 -m vtx -t "$PROJECT/timeline.json" --events-jsonl "$RUN/all.events.jsonl" all > "$RUN/all.log" 2>&1
python3 -m vtx -t "$PROJECT/timeline.json" qc --strict > "$RUN/qc-strict.log" 2>&1
```

prepare 默认真实生成全 900 SVG、manifest、字体副本、母带前音乐与短声，必须命中唯一 canonical 输入锁；保完整源/输入核验、真实 schema 和产品 validate 的日志。生成工具版本与二进制 SHA 只是出处，不是消费端平台白名单。

当前 canonical 生成方是本仓冻结的 macOS arm64 工具链（Python 3.14.3、NumPy 2.4.2、FFmpeg-full 9.0.2、Node 24.14.1，均为本机 Homebrew arm64 二进制），2026-10-23 由两次独立真实生成（结构规格、源码、字体完全相同，两个 WAV 全字段逐字节相等）后重新冻结 `benchmark/input-lock.json`。其他机器（如 Linux x86_64）不重生成音频：把同一两个 canonical WAV 私有移交到仅含 `bed-premaster.wav` / `content-cue.wav` 的物理目录，并给 preflight 与 prepare 显式增加 `--audio-inputs DIR`。入口先核编码字节、全部 PCM 与规格，再用原生成器备齐图形、复制音频并按同一完整标准复核；不要求未用的 NumPy，不下载、不自动 fallback、不接纳新 hash 或新增平台锁。缺移交文件或字节不同即失败；GitHub 不上传音频二进制。该路线记为 `canonical-transfer`，不冒充本机音频生成。2026-10-23 前的旧 Linux x86_64 锁（Python 3.12.14、NumPy 2.3.5、FFmpeg 7.1.5）整件原字节见[父提交固定版本](https://github.com/CongliangK/framivra/blob/f71e09d56b21346834b8a6853798d4568cc90bbb/templates/demo-timeline-svg/benchmark/input-lock.json)与仓外迁移记录，历史不删不改，另立平行活动锁不允许。

准备成功不等于原生渲染或视听通过。`all` 复用正式视频、音频、封装与普通 QC；strict QC 是单独一次调用。不要用裸 `--graphics-only` 的不完整输入绕过完整验证。

### 实际运行证据

源规格不存性能成绩。每次 run 分开保存实际 engine commit/tree/dirty、完整 argv、相关且已脱敏环境与配置、工具/运行库和字体身份、退出码与完整事件；dirty engine 还需精确改动及未跟踪依赖身份。保存准备回执、源与生成输入哈希，实际核全片 900 帧/PTS/音频样本与内容；目标值不是测量值，未执行项写 UNRUN/null，人工视听单独记录。

制作端到端（prepare + all）、已备输入 all、strict QC 分开计时；包含 schema/自测等步骤的窗口必须明列，并行阶段不能相加冒充墙钟。冷项目只指全新 cache/out，不声称 OS 页缓存冷；暖态须按回执和缓存字节核真实复用。同输入、工具/环境、配置与质量口径下才能比较 engine，机制探针或历史成绩不能重标为本次结果。新的实验新建 RUN，不覆盖已有证据。

## 实际依赖与字体

- 图形生成：Node.js 标准库，无 npm 依赖。当前实现按 Node 18+ API 编写。
- 完整结构自测（prepare 内部调用）：`test_schema.py` 使用已有 `jsonschema` 校验仓库真实 `vtx/timeline.schema.json`，这是仅用于测试的依赖。缺少时明确返回 BLOCKED，不安装、不降级成部分验证、不报告通过。复制到仓外的模板，两项自测均可用 `--schema` 指定对应产品 schema。
- 程序配乐：Python 3.9+、NumPy、FFmpeg。顺序合成，生成器子进程超时受限，不包含外部采样、音色库、模型或下载器。当前 canonical 冻结环境为 macOS arm64（Apple M4，Darwin）：Python 3.14.3、NumPy 2.4.2、FFmpeg-full 9.0.2（2026-10-23 重冻结）；首审环境为 Linux x86_64 的 Python 3.12.14、NumPy 2.3.5、FFmpeg 7.1.5（旧锁见上文父提交固定链接）；不承诺不同运行时跨版本位级相同。
- SVG 实际依赖：prepare 内的产品 validate 也会调用 SVG 引擎与字体身份探针；`prepare_ready` 仅表示已列预检项通过，不代表这些探针或完整原生能力已通过。SVG 运行时身份实现支持 Linux ELF 与 macOS arm64，Windows 不支持。必须已有 Framivra、Inkscape、Fontconfig 的 `fc-scan`、FFmpeg/FFprobe；Linux 另需 system `ldd`。macOS 要求自包含 Inkscape.app 的真实 arm64 Mach-O 可执行文件及可解析依赖，不接受启动脚本。按 `VTX_INKSCAPE`、PATH 顺序定位；macOS 未找到时再查系统或用户 Applications 中的 Inkscape.app。后端版本、二进制及运行库身份和声明字体文件的 face 枚举清单由产品回执记录；须核对锁内期望 face，枚举不证明逐字形实际选面；私有配置隔离用户环境，仅使用声明字体。实现边界见 [SVG 后端](../../vtx/render/gfx_svg.py)，代码支持不等于对应平台或作品已通过真实渲染、音频终链与人工视听验收；resvg 不是此模板的生产依赖。
- 真实文本字体：Noto Sans CJK SC Regular/Bold 与 DejaVu Sans Book/Bold。确切文件、字重、TTC face index、版本和 SHA256 的唯一清单是 `fonts.lock.json`。不接受静默 fallback；准备时必须匹配固定字节。

生成器会先检查项目内已准备的字体，再检查锁文件内的 Linux 系统路径提示。该提示不是 Mac 路径，也不保证其他电脑已装字体。已有相同字体文件时，把上面的 `FONT_DIR` 设为包含清单所列原文件名的目录。

字体不会提交到本模板仓库；准备阶段复制到 `assets/fonts/`，manifest 显式引用这些项目根相对路径，产品使用隔离字体配置读取。更换字体或字形需要重新审核字体锁和视觉，不以同名 family 冒充同一依赖。许可全文见 `licenses/`。

## 编排和缓存边界

时间以正式 `timeline.json` 与 `motion-plan.json` 为准：第一次共同出现为 7.6 秒；13.7 秒经过旧落点时不触发内容；第二次为 15.4 秒；17 秒承接已经发生的结果，没有第三次 cue。

`gfx/demo.frames.json` 按 clip 局部帧索引逐帧对应；所有清单路径相对工程根目录。SVG 只使用受控图形、真实文本和本地 clipPath 引用。源 `renderer.js` 保留原运动实现；其 SVG 描述中的 “no audio” 指图形文件本身，正式工程音频来自独立音轨。

`sources` 分别指向母带前 bed 与同一份短声 WAV；两个音频事件都从这个短声源的第 0 帧播放。完整 cue 试听轨和试听母带不进入正式工程，不能替换成一个从 0 秒起播放的烘焙事件。源内已有设计增益/声像，正式音轨使用单位增益；产品音频终链须重新测量并听审，不能套用试听母带结论。

片内中文已由 SVG 文字绘制，因此正式工程显式保留 `subtitles: []`。它是产品 schema 的必填根字段，空字幕列表也不能省略。

生成器、运动计划、渲染源码、音频源码、最小 synth 依赖与实际字体均登记在 clip 的 `cache_dependencies`；产品还读取 manifest、每帧 SVG 与字体字节。自建作品改源码后须自行重生成、重新验证；缓存失效不会执行 JavaScript。本固定 30 秒 benchmark 不接受改源或回写锁绕过 prepare/verify。`.generated/preparation.json` 只记录输入/帧哈希，不是原生成功回执。

## 来源与许可

原创视觉沿用已审的 JSON 时间线构图和 `timeline-motion-r1` 运动源码，没有复用旧复杂版本；来源字节锚见 `SOURCES.json`。原配乐的音符、短声、增益、声像和母带前导出保持一致，适配仅改为项目相对的固定依赖，去掉独立试听母带处理。

Scoreweft 的 MIT 程序合成最小依赖随包提供，固定提交、逐文件 SHA256、上游链接见 `vendor/scoreweft/SOURCES.json`，完整许可见 `vendor/scoreweft/LICENSE`。该子集仅含 synth 路线必需源码，未修改 vendored 文件；不包括上游其他音色、采样、谱、预览或模型。新项目源码遵守仓库根 MIT 许可，依赖许可各自保留。

Noto CJK 适用 SIL OFL 1.1；DejaVu 使用 Bitstream Vera 许可，DejaVu 修改为公有领域。NumPy、FFmpeg、Inkscape 仅使用用户已有安装，不随模板分发工具二进制。生成帧、字体副本、大 WAV、缓存和成片均在本模板 `.gitignore` 中。

`test_demo.js` 保源文件/字体身份、静态生成确定性、普通时间点状态、音轨绑定和真实 schema 根必填字段；`test_schema.py` 另对完整工程做真实产品 schema 结构校验。它们不保运行时语义/媒体检查、真实产品渲染、实际字体选中、音画同步、响度或艺术质量。后者必须由真实运行和独立复审给出。
