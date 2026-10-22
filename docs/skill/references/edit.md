# 剪辑与修改配方 · timeline.json + vtx

本页是剪辑操作、R1–R4 修改配方与交接的唯一入口；[HTML 创作](gfx.md)、[缓存/回执](../../cache-provenance.md)、[契约](../../ARCHITECTURE.md)分别维护其机制，开发专题从 [DEV](../../DEV.md) 路由。发布、BGM 分轨审计与封面走 [tech-talk R5/R6/R7](../../../templates/tech-talk/references/runbook-r5r6r7.md)。

按任务阅读：[准备与新项目](#使用规则) → [正式交付](#正式交付链r1-r4-共用)；局部修改选 [音频](#r1--纯音频变更音量--duck--fade--bgm-源)、[编排](#r2--时间线编排clip-窗口--轨道--字幕)、[字幕](#r2s--纯字幕修订)、[gfx](#r3--gfx-页改动html--声明依赖--新页)、[冷重渲](#r4--全冷重渲新机--新副本--全链回归)。

## 工具与输入

剪辑用 timeline.json + vtx，不手写 FFmpeg 剪辑命令；修改后必须 validate，失败修数据、不绕过。时间量为整数帧、区间左闭右开；时基与字段由 [timeline schema](../../../vtx/timeline.schema.json) 定义。

渲染入口为 `vtx/vtx.py`、`python3 -m vtx` 或安装后的 `vtx`；`vt.py` / `vt` 是仓库管理入口，不是渲染别名。实际依赖和平台资格见 [README](../../../README.md)，不能从脚本存在推定工具可用。修改程序/文档须改仓库，不改挂载副本；挂载边界见 [文档宪章](../../DOC-CHARTER.md)。

## 时间线预览（人审用）
`preview.html` 是单文件网页（无外部依赖），把 timeline.json 渲染成可视化时间线供**人类**审阅编排：视频轨/动效层/字幕/音轨分行显示，事件块带源名、时长、vol/pan/loop 与淡入淡出帧数标注；支持缩放、适应宽度、悬停播放头、点击事件块看全字段详情，并有**预览级校验**（整数帧、EDL 不变量、同轨视频片段重叠、越界、同轨音频重叠等）——发现问题标红，但最终以 `vtx validate` 为准。
先把 `VIDEO_TOOLS` 设为实际仓库根目录；在有桌面打开工具的平台使用：

```bash
xdg-open "$VIDEO_TOOLS/vtx/preview.html"
# 打开后：内置 poc2 示例自动载入；「打开 timeline.json」选文件、拖拽文件到页面、或「粘贴 JSON」均可替换
```
只接受顶层 `version: 2` 的当前时间线；旧版本、缺失版本和非数值版本不会自动转换。打开、拖放或粘贴非法 JSON / 不支持的版本时显示错误，保留已载入视图。预览器只读展示，不编辑或导出时间线，也不替代最终 `vtx validate`。

使用时机：**编排完 timeline.json → 先开预览器人工过一遍 → 再跑 validate/渲染**；用户反馈修改意见时，预览器是双方看同一份编排的共同语言。
## 使用规则

1. 先读对应配方与任务书；参数、素材或依赖不清楚时查对应 reference 和当前 CLI，不能把示例路径当作本机事实。
2. 修改 timeline 前用 `snapshot` 留锚；修改用可重复执行的生成器或幂等补丁。重大素材迁移另保留不随自动轮转删除的长期版本。
3. 先做对应局部验证，再走下方正式交付链。`diff` 的影响估计用于定位，不能替代当前输入回执或真实渲染。
4. 长命令按执行环境支持的后台会话与轮询机制运行。一个项目同一时间只允许一个写入命令，运行期间不修改输入。
5. 本文命令会写项目的 `cache/`、`out/` 或快照；正式渲染可能替换已有成片。先确认项目与输出位置，冻结项目使用独立副本，不把操作示例当成只读检查。

命令约定：先将 `VIDEO_TOOLS` 设为实际仓库根目录、`PROJ` 设为本次项目的绝对路径；`OLD` 是留存的改前 timeline 路径。以下从项目目录执行，不依赖任何私人 voice-ime 工作区：

新项目先选尚不存在的 `PROJ`，执行以下**写操作**创建脚手架；已有项目不重建，脚本会覆盖 timeline/schema，不能当作已有工程无损初始化。随后把素材放入 assets/、页面放入 gfx/，编辑 timeline.json。

```bash
bash "$VIDEO_TOOLS/scripts/new_project.sh" "$PROJ"
```

```bash
export VTX="$VIDEO_TOOLS/vtx/vtx.py"
cd "$PROJ"
python3 "$VTX" -t timeline.json snapshot
```

## 正式交付链（R1-R4 共用）

输入变化会重置阶段成功身份，包括只改音轨、字幕、HTML 或声明依赖的情况。缓存键未变可节省计算，但旧阶段成功不能直接沿用。**正式交付统一运行完整 `all`**：

```bash
python3 "$VTX" -t timeline.json all
```

它执行 validate、audit、prepare、gfx、video、audio、mux、qc，并在完整链收尾运行启用插件的 finish 钩子。默认依赖图会并行安排独立阶段；`VTX_PIPELINE=0` 则按上述线性顺序运行。完整 `all` 对两种模式都适用，无需外接 post_loudness。

- `all --until video` 只适合中间检查：线性模式尚未执行 audio；截断链后单跑 mux/qc 也不会补触发 finish。`all --until qc` 可到完整收尾，但日常交付用不截断的 `all`。
- 完整链的 QC 失败仍可执行 finish，退出码汇总 QC/finish 失败。产物存在不代表通过；检查退出码、`out/qc_report.json` 与插件结果，不把非零当预期成功。
- 单阶段排查须满足当前身份依赖：audio←prepare；有 gfx 轨时 video←gfx；mux←video+audio；qc←mux。回执格式与失效机制见 [缓存与回执](../../cache-provenance.md#阶段回执pr1)。
- 启用内容门的交付再执行下例；4K 导出、冻结与 BGM 审计按项目工作流的 [R5/R6/R7](../../../templates/tech-talk/references/runbook-r5r6r7.md) 完成，不将它们视作所有内核项目的固定前置。

```bash
python3 "$VIDEO_TOOLS/apps/gates/content_gates.py" -t timeline.json --media out/final.mp4
python3 "$VTX" -t timeline.json gc --dry-run
```

`gc --dry-run` 只查看回收清单，实际回收另按任务授权执行。耗时与历史测量口径统一见 [DEV 性能基准](../../reports/development-history.md#一性能基准)，不据旧项目数字承诺当前机器耗时。

## R1 · 纯音频变更（音量 / duck / fade / BGM 源）

适用：只改 `audio_tracks` 及其音频素材。快照后实施幂等修改，检查影响面：

```bash
python3 "$VTX" diff "$OLD" timeline.json --json out/tl_diff.json
python3 "$VTX" -t timeline.json validate
python3 "$VTX" -t timeline.json prepare
python3 "$VTX" -t timeline.json audio --report
```

- `audio --report` 会先混音再写 `out/audio_report.json`，FAIL 返回非零；它需要当前 prepare。只对旧文件出报告的 `--report-only` 不能认证新输入。
- 若 diff 显示视频或 gfx 受影响，转 R2/R3 追加相应预览；即使视频缓存全命中，最终仍走共用 `all`，不能跳过当前 video 回执。
- 调 volume 改的是轨间相对比例，整片还会归一与限幅；验收要听受影响窗口，不能只看成片 LUFS。duck 开关或参数变更做 A/B 对照。
- 补丁检查目标值，防重复施加增益。AAC 编码后的解码域真峰仍需 QC/内容门确认，WAV 通过不能代替成片通过。

## R2 · 时间线编排（clip 窗口 / 轨道 / 字幕）

适用：改 `video_tracks`、事件窗口、轨道或字幕。快照后修改生成器或幂等补丁，再用 `diff` 检查预期影响：

```bash
python3 "$VTX" diff "$OLD" timeline.json --json out/tl_diff.json
python3 "$VTX" -t timeline.json validate
python3 "$VTX" -t timeline.json gfx
```

`gfx` 为改后项目准备当前页面缓存，无 gfx 轨时没有页面渲染任务。将 `START`、`END` 设为改动邻域的真实时间点后预览（支持秒、时间码、后缀 `f` 帧号）：

```bash
python3 "$VTX" -t timeline.json video --draft --range "$START" "$END"
```

- `--range`、`--clip`、`--track` 是三种互斥的窗口选择，不能把 `--range` 与另外两项组合。`--clip` 按素材 stem 匹配，具体选窗语义见 CLI。
- 区间产物只写 `cache/preview/range_*.mp4`，不走正式阶段回执门，不写正式成片/回执或 VERSIONS；可冷填充所需 seg/抽音频缓存，但不会自动渲染缺失 gfx。它不能代替正式交付链。
- 若需用区间音频判断整片混音，先确保当前输入的 prepare/audio 成功，无论本次是否改音轨。否则区间可能采用窗口自测增益，只能作局部预览，不能据其响度判断成片。
- 涉及字幕再走 R2s；涉及画面切点检查边界两侧与黑帧。内容寻址自动选择可复用缓存，不例行手工删除 cache/seg。

局部验证通过后运行共用 `all`。diff 的 `advice.recommended_path` 用于选择生成器/幂等补丁，不能据其估计阶段表跳过真实依赖门。

## R2s · 纯字幕修订

适用：只改字幕文本或窗口。修改前留存旧字幕检查报告，快照并实施修改后：

```bash
python3 "$VTX" -t timeline.json subs check
python3 "$VTX" -t timeline.json subs preview 12.5 f360
```

示例预览时间点应替换为实际可疑条目的时间。`subs check` 写 `out/subs_check.json`；新增 FAIL 须处理，存量问题单列，不能把已有失败记成通过。需要对照时，`PREVIOUS_REPORT` 指向保留的旧报告：

```bash
python3 "$VTX" diff "$PREVIOUS_REPORT" out/subs_check.json
```

- 无 voice 轨或无法对齐时检查会 WARN，用实际音画预览补核。阈值与背景回退语义见 [字幕预检](#字幕预检与预览subs-check--preview2026-09-12-方案四)。
- 单张字幕预览可能使用 qc_clean 或黑场背景，不能据它确认改后全画面。按 R2 准备 gfx 并做区间音画预览，再执行共用 `all`。
- 字幕字节进入分段缓存键；实际失效范围取决于字幕覆盖与切段边界，不承诺总是固定几个段，也不需要手工清段缓存。

## R3 · gfx 页改动（HTML / 声明依赖 / 新页）

先按 [HTML 创作组织](gfx.md#html-创作组织) 确认模块边界，遵守帧驱动协议；本地 CSS/JS/图片/字体按 [依赖声明](gfx.md#帧驱动协议必须遵守vtx-依赖它逐帧截图) 登记。快照后修改页面或资产：

```bash
python3 "$VTX" -t timeline.json validate
python3 "$VTX" -t timeline.json gfx
```

核验改后实际帧、透明度、时间窗与布局，再按 R2 做区间预览，最后执行共用 `all`。HTML 或依赖变更也改变整体回执身份，不能因为音轨参数未变而省略 prepare/audio。

**gfx 基准表与重测方法**：历史矩阵、静态跨度占比及重页分析统一见 [DEV 性能基准](../../reports/development-history.md#一性能基准)。这些是指定机器/项目/版本的测量，不能当作本轮性能证明。重测使用独立项目副本与已冻结的 Golden；记录输入、引擎环境、缓存冷热、耗时和像素比较结果。没有独立基准时只报告实际测量，不宣称等价或提速。

**gfx env 速查**（CDP 路径；调参需重新验证）：

| env | 默认 | 说明 |
|---|---|---|
| `VTX_GFX_PAGES` | 7 | 页级并行浏览器实例数 |
| `VTX_GFX_SPLIT_FRAMES` | 450 | 页内分片阈值（帧，0 关闭） |
| `VTX_GFX_SPLIT_MAX` | 3 | 单页最大分片数 |
| `VTX_GFX_TABS` | 4 | 每实例 tab 数 |
| `VTX_GFX_NAVFREE` | 0 | 免导航路径不用于正式交付，原因见 DEV |
| `VTX_GFX_STATIC` | 1 | 消费作者声明的静态跨度；不证明其正确性 |

CDP 页内透明探针回落与跨引擎回落不同：前者保持当前渲染身份；后者不能补签成功。显式 `VTX_GFX=cli` 使用独立身份重渲，strict 依赖策略不支持 CLI。详见 [渲染环境身份](../../cache-provenance.md#gfx-environment)。

## R4 · 全冷重渲（新机 / 新副本 / 全链回归）

确认依赖、资产与字体可用，在独立项目副本上运行共用完整 `all`，再做项目要求的内容门、人工视听与发布检查。需要真正冷跑时先约定并记录清理范围；不在冻结/已交付项目原位清缓存或覆盖输出。

- `VTX_SEG_NVENC=0` 可用于 CPU 编码对照。GPU 探测回落、机器负载与并发设置须写入测量记录，不能混算性能。
- `scripts/bench_cold.sh` 是会清 cache/回执并覆盖成片的历史分步基准工具，采用截断链；它不代替包含 finish 的正式交付，不直接用于已冻结项目。历史数字见 DEV，不据旧报告豁免本轮失败。
- 中断后先查退出码、日志与当前回执；缓存修复或清理限定在已确认的项目副本内。重新运行正式交付链后再确认 QC，不凭产物存在宣布成功。

## 命令正字法速查

- 子命令：`all | validate | audit | prepare | gfx | video | audio | mux | qc | clean | gc | df | snapshot | subs | diff`。
- `--draft [--frames N]`：`--draft` 只接受 video/mux；`--frames` 只接受 video draft。`--gpu` 只接受 video，不能加在 all 后。
- `all --until` 终点：`validate | audit | prepare | gfx | video | mux | qc`，audio 不作终点；交付语义见共用链。
- `audio --report` 与 `--report-only` 互斥，且不能与 dry-run 组合。`diff --json` 用于 timeline 影响报告，报告对比模式不接受该开关。
- Blender 的 json2blend/verify_blend 必须由 Blender 解释器运行，具体调用见 [Blender bridge](../../../apps/blender-bridge/README.md)。发布与分轨命令不在此重复维护。
- `subs render` 独立生成 `cache/subs.ass`，相同字节跳过；报告对比支持 qc/audit/subs_check，状态改变返回 DIFF（退出码 1），不是 `qc diff`。
- CLI 的 `--dry-run` 只预览阶段计划，不执行渲染、测量或插件钩子，也不落字幕/清单/快照；计划输出不代表阶段执行或产物验证通过。
- 头窗草稿：`video --draft --frames N` 后可 `mux --draft`，产物为 out/draft.mp4 与 out/draft_final.mp4，不覆盖正式成片；有 gfx 时同样需要当前 gfx 前置。正式 GPU 用 `video --gpu`；硬件不可用的回落必须读取日志，不能假定实际 NVENC。

## 缓存卫生与体积治理

先用 `df` 看占用、`gc --dry-run` 看清单；`gc` 是实际回收，`gc --all` 深度回收仍保留 reports/snapshots 与交付件。`clean` 清理 cache/（含其中证据与快照）及 out/ 可再生条目，默认保护 final、VERSIONS、history、FREEZE 等交付件；仅显式危险旗标 `--i-know-this-nukes-everything` 才将交付件也纳入删除，锁与拒绝路径仍保留。清理须单独确认范围，`--keep-days N` 可暂缓近期 gc 条目，不例行手工删 seg。完整规则见 [缓存与回执](../../cache-provenance.md)。

`snapshot` 保存 timeline 与引用文本资产并自动轮转；重大迁移另留不随轮转删除的版本。mux 通过 `out/VERSIONS.json` 记账并轮转旧成片到 `out/history/`；将覆盖的 final 与末条 md5 不符会告警并标记 superseded，不把记录当作媒体未变的保证。

`VTX_CACHE_BUDGET_GB` 设置预算后，gfx/video 前超限提示 gc 清单；`VTX_GC_AUTO=1` 允许直接回收，是写态选择。audit 报告 volume 节记录 cache/out 与 tmp 孤儿；临时媒体和持久证据分开，见 [交接](#主控-子智能体棒次协议)。

## audit 指标审计（建议性，不阻断渲染）

validate 是机制硬门，audit 是内容体检；报告在 `out/audit_report.json`。画面构成、黑场/无声窗、口播覆盖、语速与字幕问题须逐条解释；建议性发现不拦截渲染，输入或执行失败仍可能使审计无法运行。报告注明当前基准；科技口播的内容政策见 [工作流](../../../templates/tech-talk/references/production-workflow.md)，不把私人语料统计当作所有作品合同。

## 字幕预检与预览（subs check / preview，2026-09-12 方案四）
- **`subs check`（零渲染，timeline 层面可跑）**：①字幕窗 vs voice 轨口播窗对齐（重叠取最大重叠窗算起点偏移，无重叠取最近外沿——滞后窗口末端为正/超前窗口起点为负；FAIL ±1.5s / WARN ±0.5s，`--align-threshold` 可调；最近窗距离 >5s 或无 voice 轨 → WARN 不静默，报告 coverage 如实扣减）；②阅读速度 CPS >9 FAIL / >7.5 WARN / <0.6s WARN（与 audit 同基准）；③字幕时间重叠双标 FAIL；④行宽 28 字×2 行 FAIL（PlayResX 1920/Fontsize 58/MarginL-R 60 推算）。报告 `out/subs_check.json` + 人类可读表格，末行 PASS/FAIL（FAIL 退出码 1，可做 CI 门）。
- **`subs preview <秒|f帧号>...`**：单帧预览图 `out/subs_preview_f<N>.png`。背景三级回退：qc_clean 提帧 → 图层单帧合成（素材不全自动跳过）→ 黑场兜底（WARN，仅审字幕位置/样式）。活跃集判定与真实烧录的 cs 量化逐位一致（`_fc` 同式），烧录用 `ass_head` 同源样式；实际输入路径与有损编码仍可能产生像素差，不能把字幕预览当作最终成片等价证明。
修改顺序和音画复核见 [R2s](#r2s--纯字幕修订)。

## 音频工程（build_audio 内置）

轨内子混音补齐全长 → duck → 保留相对比例的总混音 → 两遍静态增益 → limiter → 定长裁切；out/audio.wav 为归一+限幅终态，直接进入 mux，无额外 post_loudness 步骤。响度目标、终链参数及 FAIL/WARN 分级唯一见 [ARCHITECTURE §1.2](../../ARCHITECTURE.md#12-响度终链)。不要换成单遍 dynamic loudnorm；验收还须测 AAC 编码后解码真峰，WAV 通过不代表成片通过。

`duck:true` 由 `duck_by` 指定的轨触发（默认 voice），`duck_params` 可覆盖压缩参数；新项目启用或改变 duck 必做开/关 A/B 及无口播窗口复听，不能据整片 LUFS 掩盖局部抬升。参数与历史故障见 [混音实现](../../../vtx/pipeline/stages.py) 和 [开发记录](../../reports/development-history.md)。正式 BGM 的分轨审计、用户独听和重跑纪律见 [组件交付门](../../../apps/blender-bridge/README.md#分轨审计包-audit_bundlepy人机协作交付门)。

### 既有 WAV 维护

`scripts/audio/normalize_project_audio.py` 独立归一已有项目 `out/audio.wav`，不是 timeline 重渲，也不是正式链的追加步骤。参数为 `project`（默认当前目录）、`--target`、`--ffmpeg`，输出 JSON；源码函数为 `vtx.pipeline.stages.normalize_project_audio`。只读查看：

```bash
env python3 scripts/audio/normalize_project_audio.py --help
```

实际执行是**写操作**：测量、限幅与时长检查后原子替换 WAV，不更新或撤销 audio/mux/qc 回执；重复施加增益不保证字节或响度不变。它不是交付、恢复或补签回执流程，不能用旧成功回执验收新 WAV。需要调整声音时修改 timeline，重跑完整 `vtx all` 并核对成片 QC 与项目内容门；阶段顺序见 [正式交付链](#正式交付链r1-r4-共用)，终链合同见 [ARCHITECTURE §1.2](../../ARCHITECTURE.md)。

## 渲染排错边界

- 帧精确操作使用帧号，不以 `-ss` 粗定位替代断言；素材须 CFR 且匹配项目 fps。透明素材与底层次序、窗口时移、alpha 淡出须在实际合成中检查。
- QC 的帧数、底轨切点、上层窗口、空窗泄漏、响度和音频能量是有限技术检查；帧数准确或 CFR 补帧不能证明内容正确。检查切点两侧、黑帧与实际视听，不根据单张或 DOM 相等宣称整片通过。
- 中断产物按 [恢复合同](../../cache-provenance.md#中断与恢复) 处理；不要根据旧建议原位删除 qc_clean 或跳过当前阶段回执。QC 报告在 `out/qc_report.json`。
- 实现层的 framesync/tpad、probe、NVENC、字幕量化与编码故障分析在 [开发记录](../../reports/development-history.md)，不把旧机器测量当作当前性能或平台资格。

## timeline.json 契约（速览，v2 轨道等价模型）
```jsonc
{
  "version": 2,
  "project": { "fps": 30, "width": 1920, "height": 1080, "duration_frames": 720 },
  "sources":  [ { "id": "v01", "path": "assets/v01.mp4", "transform": "crop_center_scale" } ],
  "video_tracks": [   // 全部轨道等价：数组顺序＝图层顺序（[0] 最底，末位最上）；画布为黑场
    { "name": "aroll", "kind": "aroll", "clips": [   // 出镜口播（底层主画面）
        { "src": "v01", "src_in": 0, "src_out": 96, "dst_in": 0, "dur": 96, "fade_in_frames": 12 } ] },
    { "name": "broll", "kind": "broll", "clips": [   // 实拍 B-roll 全屏覆盖窗（scale/x/y 缺省＝全屏）
        { "src": "v02", "src_in": 0, "src_out": 48, "dst_in": 96, "dur": 48 } ] },
    { "name": "pip", "kind": "pip", "clips": [        // 画中画小窗
        { "src": "v07", "src_in": 0, "src_out": 96, "dst_in": 48, "dur": 96,
          "scale": 0.26, "x": 1460, "y": 90, "opacity": 0.92,
          "fade_in_frames": 12, "fade_out_frames": 12 } ] },
    { "name": "gfx", "kind": "gfx", "clips": [        // HTML 动效层（src 指向 html，帧序列按需渲染）
        { "src": "gfx/gfxA.html", "dst_in": 0, "dur": 192 } ] }
  ],
  "subtitles": [ { "in": 12, "out": 88, "text": "示例字幕" } ],   // ASS 烧录于全部图层之上
  "audio_tracks": [
    { "name": "bed",   "events": [ { "src": "v05", "in": 0, "out": 720, "volume": 0.22, "loop": true } ] },
    { "name": "voice", "events": [ { "src": "v03", "in": 60, "out": 252, "volume": 0.85,
        "fade_in_frames": 12, "fade_out_frames": 20 } ] },
    { "name": "amb",   "events": [ { "src": "v08", "in": 400, "out": 500, "volume": 0.3,
        "pan": -0.6, "fade_in_frames": 24, "fade_out_frames": 24 } ] }
  ]
}
```
**v2 核心语义**：所有视频轨道等价，无 base 特权、无全覆盖要求（空洞帧＝黑场，是规划问题不是校验错误）；`kind`（aroll/broll/screen/pip/gfx）纯语义标注，渲染机制完全不区分，只服务于编排规范、预览着色与画面构成统计；clip 的 `scale/x/y/opacity` 全部可选（缺省＝全屏不透明，全屏走 transform 归一）；fade 统一为 **alpha 淡入淡出**（无下层时自然露出黑场＝视觉淡黑）。gfx 的非默认 scale 按源 PNG 尺寸向下取偶数；最终尺寸（含 draft 缩放）任一边不足 2 像素时报错。内核与预览器均只接受 `version: 2`，不接受或自动转换旧 v1（role+overlays）及缺失版本的时间线。
**"这个素材放哪条轨"速查**：
| 素材 | 轨道 | 说明 |
|---|---|---|
| 出镜口播（含对镜头演示） | `aroll` | 通常为 tracks[0] 底层 |
| 实拍空镜/产品特写/环境 | `broll` | 全屏覆盖窗（缺省几何即全屏） |
| 录屏、软件界面实况、产品演示 | `screen` | 全屏或分区均可 |
| 小窗画面（角标人像/引用片段/双视角） | `pip` | 必须给 scale/x/y |
| **全屏流程图/表格/数据展示/原理动画** | `gfx-full`（kind=gfx） | clip 只写 dst_in+dur，缺省全屏；覆盖实拍层 |
| 标题卡/下三分之一/角标/强调字 | `gfx-overlay`（kind=gfx） | 页面自身留透明区域，叠在最上（仅低于字幕） |
gfx 的"全屏 vs 局部"由 HTML 页面设计决定（帧恒为全画布 PNG，透明区域即"局部"），timeline 层不区分——不要为全屏图形发明新 kind。表中轨型是独立素材的放置规则，不是角色/组件的拆页清单；页面边界先按 [HTML 创作组织](gfx.md#html-创作组织) 决定，再按本文挂载。
不变量（validate 强制）：`src_out - src_in == dur`（gfx clip 除外）；切割范围 ≤ 素材实测帧数；**同轨 clips 不重叠**（视频与音频皆是，唯一机制约束）；clip/事件不超出 duration_frames；fade 总长 ≤ 片段长。音频事件可加 `src_in`（取源音频中段）与 `loop`（循环平铺）。**source 可以是纯音频文件**（wav/mp3：音效、BGM、环境声等资产库素材直接进 timeline；时长换算项目帧网格，只能被音频事件引用，视频 clip 引用会被拒绝）。完整字段语义读 `~/.agents/skills/video/vtx/timeline.schema.json`，里面有每个字段的注释。

**字幕来源 `subtitles_src`（v2 内核键）**：根级声明 `"subtitles_src": "subs/ep.srt"`（相对 timeline 路径），与根级 `subtitles[]` 互斥——内核只负责引用与烧录（.srt 时间戳按项目 fps 换算整帧 / .json 帧值直用），谁产出该文件（asr/手写/其他工具）内核不感知；文件内容参与回执指纹（改字幕文件即失效重渲）。

## 插件与素材工具

插件键遵守三分支：已安装启用才生效；未安装/禁用分别报错并提示安装/启用；未知键仍警告，`_meta` 为自由命名空间。键面和协议分别以 [组件目录](../../catalog.md) 与 [兼容门](../../reference/plugin-core-compatibility.md) 为准；不要靠改版本标签绕过协议检查。

| 任务 | 唯一配方入口 | 回到渲染链前的检查 |
|---|---|---|
| denoise / 静态 duck / BGM 工程 / Blender finish | [denoise](../../../plugins/denoise/README.md)、[duck-static](../../../plugins/duck-static/README.md)、[bgm-render](../../../plugins/bgm-render/README.md)、[blender-bridge](../../../plugins/blender-bridge/README.md) | 按项目声明启用，检查当前 prepare 与 finish 结果 |
| ASR 字幕锚定 | [ASR](../../../apps/asr/README.md) | 修改 timeline 前保留备份；先 dry-run，再 validate/audit 和 R2s |
| TTS 占位与实录替换 | [TTS](../../../apps/tts/README.md)、[sync/repack](../../../apps/sync/README.md) | 先核对窗口与源文件，替换须备份；按 R1 验证后完整 all |
| 背景生成与 alpha 合成 | [imgen](../../../apps/imgen/README.md)、[matting](../../../apps/matting/matting.md) | 生成付费先确认；尺寸、色彩/alpha 与背景接口按组件核验 |
| 科技口播 / 拍摄 / 内容门 | [tech-talk](../../../templates/tech-talk/SKILL.md) | 使用项目工作流，不把工作流审美规则扩大为内核限制 |

应用为可选素材工具，不是基础渲染前置；初始化/启用、外部服务、费用与文件契约只在组件说明维护。已声明插件的依赖仍是当前项目必要条件，不能以“可选”跳过。素材许可和实际路径由项目核验，不假定私人资产库存在。

## 主控-子智能体棒次协议

页面/模块边界先依 [HTML 创作组织](gfx.md#html-创作组织) 写入任务书；一棒不等于一个 HTML、clip 或轨道。

1. 任务书写明仓库/项目路径、目标、允许修改范围、输出位置、验证与提交权限。
2. 报告写结论、改动与证据、实际运行命令及耗时、遗留问题；分清通过、失败和未运行。项目报告保存在约定的持久目录，不依赖可清理临时缓存。
3. 渲染中间物用项目 `cache/tmp/<runid>/`，留证用 `cache/reports/`；不要假定所有机器的系统 `/tmp` 都是磁盘或都可存放大文件。
