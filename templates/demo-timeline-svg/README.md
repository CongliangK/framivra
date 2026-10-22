# JSON 时间线 Demo：主宣传与性能源输入

这是一支 30 秒中文概念动效：JSON 编排画面、文字和同一个短声，再修改落点，承接到成片与品牌。`timeline.json` 是正式 v2 工程；`motion-plan.json` 是离线创作计划，不能交给 vtx 执行。产品以 `clip.format = svg-sequence` 读取静态 SVG，不执行生成器 JavaScript。

## 主性能基线

[baseline.json](benchmark/baseline.json) 是唯一源选择记录：固定已审提交中的完整模板 Git 子树，覆盖创作/生成源码、vendor、字体锁、许可与自测，不另复制创作包。来源与逐件依赖身份仍以包内 `SOURCES.json`、`fonts.lock.json`、`vendor/scoreweft/SOURCES.json` 为准。源 commit 不是被测 engine；新基线的性能实测、原生全链与人工视听仍为 **UNRUN**，不能移植历史成绩。

旧 [standard 功能 fixture](../../docs/guides/demo-fixture.md) 独立保留；本作品不替代 HTML/CDP/CEF、canvas、字幕、普通视频或发布回归。SVG 实现的平台边界见下节，代码支持不等于作品验收。

### 准备与正式运行

从可信的被测 engine 仓库根目录、同一个 Bash 会话执行。以下明确是**写操作**：只在全新 RUN 内导出固定源、准备和渲染，不使用用户原项目或已有 cache/out。先读基线并核对 Git 子树；来源对象缺失或不匹配就停止，不隐式 fetch，不改用 HEAD。旧源归档没有新 baseline，故单独保留其原字节与摘要；操作以当前仓本页为准。

```bash
set -euo pipefail
ENGINE_ROOT=$(pwd -P)
BASELINE="$ENGINE_ROOT/templates/demo-timeline-svg/benchmark/baseline.json"
RUN=$(mktemp -d /tmp/framivra-promo.XXXXXX)
cp "$BASELINE" "$RUN/baseline.json"
SOURCE_SPEC=$(python3 -c 'import json,sys; s=json.load(open(sys.argv[1], encoding="utf-8"))["source"]; print(s["commit"], s["path"], s["git_tree"])' "$RUN/baseline.json")
read -r SOURCE_COMMIT SOURCE_PATH SOURCE_TREE <<< "$SOURCE_SPEC"
test "$(GIT_NO_REPLACE_OBJECTS=1 git rev-parse "$SOURCE_COMMIT:$SOURCE_PATH")" = "$SOURCE_TREE"
PROJECT="$RUN/$SOURCE_PATH"
python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$RUN/baseline.json" > "$RUN/baseline.sha256"
git rev-parse HEAD 'HEAD^{tree}' > "$RUN/engine.identity"
git status --porcelain > "$RUN/engine.status"
GIT_NO_REPLACE_OBJECTS=1 git archive "$SOURCE_COMMIT" "$SOURCE_PATH" | tar -x -C "$RUN"
```

固定源归档不会带入工作目录额外 vendor 模块、未跟踪文件或旧生成资产。下列 `FONT_DIR` 必须改为已有固定字体文件的目录；不下载安装或用同名字体替代。`--check` 只检查输入可用性，完整准备生成所有 SVG、序列 manifest、字体副本、母带前音乐和短声；失败即停，不用 `--graphics-only` 冒充完整工程。

```bash
FONT_DIR="/path/to/existing-font-files"
node "$PROJECT/build_demo.js" --check --fonts-dir "$FONT_DIR"
node "$PROJECT/build_demo.js" --prepare --fonts-dir "$FONT_DIR" > "$RUN/prepare.log" 2>&1
node "$PROJECT/test_demo.js" --prepared --schema "$ENGINE_ROOT/vtx/timeline.schema.json" > "$RUN/input-check.log" 2>&1
env PYTHONDONTWRITEBYTECODE=1 python3 "$PROJECT/test_schema.py" --schema "$ENGINE_ROOT/vtx/timeline.schema.json" > "$RUN/schema-check.log" 2>&1
```

自测核源身份、完整静态帧、字体字节和音频结构，不证明真实渲染或视听质量。准备后仍从上述 engine 根目录复用正式全链；它使用已有 Inkscape，再走产品视频、音频、封装与普通 QC。严格 QC 是另一次调用：

```bash
python3 -m vtx -t "$PROJECT/timeline.json" --events-jsonl "$RUN/all.events.jsonl" all > "$RUN/all.log" 2>&1
python3 -m vtx -t "$PROJECT/timeline.json" qc --strict > "$RUN/qc-strict.log" 2>&1
```

### 实际运行证据

源基线不存成绩；每次 run 独立保留上述目录及实际 engine commit/tree/dirty、完整 argv、相关且已脱敏的运行环境与配置、工具版本与二进制/运行库身份、退出码和事件完整性。dirty engine 还需精确改动及未跟踪运行依赖身份，不能只报 HEAD。保存准备回执、序列 manifest、逐个 SVG、两个 WAV 与实际字体的哈希，并核对全片帧数/PTS/音频样本及人工视听；目标值不充当测量值，缺失项写 UNRUN/null。

制作端到端（prepare + all）、已备输入 all、strict QC 分开计时；总窗口若包含自测也须明列，不把阶段相加当墙钟。冷项目仅指新 cache/out，不代表 OS 页缓存冷。比较须同时固定源/生成输入身份、工具链、配置与质量口径，分别标明被测 engine；旧 standard、微型 fixture 和历史宣传片成绩不重标为本次结果。

只开发视觉时可用原 `--prepare --graphics-only`，但不属于完整基线运行。重复准备会覆盖当前 PROJECT 的生成输入；另一次实验必须新建 RUN，不覆盖证据。

## 实际依赖与字体

- 图形生成：Node.js 标准库，无 npm 依赖。当前实现按 Node 18+ API 编写。
- 完整结构自测：`test_schema.py` 使用已有 `jsonschema` 校验仓库真实 `vtx/timeline.schema.json`，这是仅用于测试的依赖。缺少时明确返回 BLOCKED，不安装、不降级成部分验证、不报告通过。复制到仓外的模板，两项自测均可用 `--schema` 指定对应产品 schema。
- 程序配乐：Python 3.9+、NumPy、FFmpeg。顺序合成，生成器子进程超时受限，不包含外部采样、音色库、模型或下载器。首次审计环境为 Python 3.12.14、NumPy 2.3.5、FFmpeg 7.1.5；不承诺不同运行时跨版本位级相同。
- 原生输出：SVG 运行时身份实现支持 Linux ELF 与 macOS arm64，Windows 不支持。必须已有 Framivra、Inkscape、Fontconfig 的 `fc-scan`、FFmpeg/FFprobe；Linux 另需 system `ldd`。macOS 要求自包含 Inkscape.app 的真实 arm64 Mach-O 可执行文件及可解析依赖，不接受启动脚本。按 `VTX_INKSCAPE`、PATH 顺序定位；macOS 未找到时再查系统或用户 Applications 中的 Inkscape.app。后端版本、二进制及运行库身份和声明字体文件的 face 枚举清单由产品回执记录；须核对锁内期望 face，枚举不证明逐字形实际选面；私有配置隔离用户环境，仅使用声明字体。实现边界见 [SVG 后端](../../vtx/render/gfx_svg.py)，代码支持不等于对应平台或作品已通过真实渲染、音频终链与人工视听验收；resvg 不是此模板的生产依赖。
- 真实文本字体：Noto Sans CJK SC Regular/Bold 与 DejaVu Sans Book/Bold。确切文件、字重、TTC face index、版本和 SHA256 的唯一清单是 `fonts.lock.json`。不接受静默 fallback；准备时必须匹配固定字节。

生成器会先检查项目内已准备的字体，再检查锁文件内的 Linux 系统路径提示。该提示不是 Mac 路径，也不保证其他电脑已装字体。已有相同字体文件时，把上面的 `FONT_DIR` 设为包含清单所列原文件名的目录。

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
