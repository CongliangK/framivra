# runbook · 标准修改配方集（R1-R4）与棒次协议

> 修改任务的操作入口。字段与缓存语义见 [edit](edit.md)，HTML 协议见 [gfx](gfx.md)。发布、BGM 分轨审计与封面配方见 [tech-talk R5/R6/R7](../../../templates/tech-talk/references/runbook-r5r6r7.md)。

## 使用规则

1. 先读对应配方与任务书；参数、素材或依赖不清楚时查对应 reference 和当前 CLI，不能把示例路径当作本机事实。
2. 修改 timeline 前用 `snapshot` 留锚；修改用可重复执行的生成器或幂等补丁。重大素材迁移另保留不随自动轮转删除的长期版本。
3. 先做对应局部验证，再走下方正式交付链。`diff` 的影响估计用于定位，不能替代当前输入回执或真实渲染。
4. 长命令按执行环境支持的后台会话与轮询机制运行。一个项目同一时间只允许一个写入命令，运行期间不修改输入。
5. 本文命令会写项目的 `cache/`、`out/` 或快照；正式渲染可能替换已有成片。先确认项目与输出位置，冻结项目使用独立副本，不把操作示例当成只读检查。

命令约定：先将 `VIDEO_TOOLS` 设为实际仓库根目录、`PROJ` 设为本次项目的绝对路径；`OLD` 是留存的改前 timeline 路径。以下从项目目录执行，不依赖任何私人 voice-ime 工作区：

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
- 单阶段排查须满足当前身份依赖：audio←prepare；有 gfx 轨时 video←gfx；mux←video+audio；qc←mux。完整说明见 [缓存与回执](../../cache-provenance.md#阶段回执pr1)。
- 启用内容门的交付再执行下例；4K 导出、冻结与 BGM 审计按项目工作流的 [R5/R6/R7](../../../templates/tech-talk/references/runbook-r5r6r7.md) 完成，不将它们视作所有内核项目的固定前置。

```bash
python3 "$VIDEO_TOOLS/apps/gates/content_gates.py" -t timeline.json --media out/final.mp4
python3 "$VTX" -t timeline.json gc --dry
```

`gc --dry` 只查看回收清单，实际回收另按任务授权执行。耗时与历史测量口径统一见 [DEV 性能基准](../../DEV.md#一性能基准)，不据旧项目数字承诺当前机器耗时。

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

**耗时基准指针**：audio、mux、QC 历史测量与 diff 估计常数的来源见 [DEV 性能基准](../../DEV.md#一性能基准)；本配方不另维护估时表。

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
- 区间分支不走正式阶段回执门，不写正式成片/回执；可冷填充所需 seg/抽音频缓存，但不会自动渲染缺失 gfx。它不能代替正式交付链。
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

- 无 voice 轨或无法对齐时检查会 WARN，用实际音画预览补核。阈值与背景回退语义见 [字幕预检](edit.md#字幕预检与预览subs-check--preview2026-09-12-方案四)。
- 单张字幕预览可能使用 qc_clean 或黑场背景，不能据它确认改后全画面。按 R2 准备 gfx 并做区间音画预览，再执行共用 `all`。
- 字幕字节进入分段缓存键；实际失效范围取决于字幕覆盖与切段边界，不承诺总是固定几个段，也不需要手工清段缓存。

## R3 · gfx 页改动（HTML / 声明依赖 / 新页）

先按 [HTML 创作组织](gfx.md#html-创作组织) 确认模块边界，遵守帧驱动协议；本地 CSS/JS/图片/字体按 [依赖声明](gfx.md#帧驱动协议必须遵守vtx-依赖它逐帧截图) 登记。快照后修改页面或资产：

```bash
python3 "$VTX" -t timeline.json validate
python3 "$VTX" -t timeline.json gfx
```

核验改后实际帧、透明度、时间窗与布局，再按 R2 做区间预览，最后执行共用 `all`。HTML 或依赖变更也改变整体回执身份，不能因为音轨参数未变而省略 prepare/audio。

**gfx 基准表与重测方法**：历史矩阵、静态跨度占比及重页分析统一见 [DEV 性能基准](../../DEV.md#一性能基准)。这些是指定机器/项目/版本的测量，不能当作本轮性能证明。重测使用独立项目副本与已冻结的 Golden；记录输入、引擎环境、缓存冷热、耗时和像素比较结果。没有独立基准时只报告实际测量，不宣称等价或提速。

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

- 线性回退 `VTX_PIPELINE=0` 仍包含 validate→audit→prepare→gfx→video→audio→mux→qc，完整 `all` 的 finish 合同不变。
- `VTX_SEG_NVENC=0` 可用于 CPU 编码对照。GPU 探测回落、机器负载与并发设置须写入测量记录，不能混算性能。
- `scripts/bench_cold.sh` 是会清 cache/回执并覆盖成片的历史分步基准工具，采用截断链；它不代替包含 finish 的正式交付，不直接用于已冻结项目。历史数字见 DEV，不据旧报告豁免本轮失败。
- 中断后先查退出码、日志与当前回执；缓存修复或清理限定在已确认的项目副本内。重新运行正式交付链后再确认 QC，不凭产物存在宣布成功。

## 主控-子智能体棒次协议

页面/模块边界先依 [HTML 创作组织](gfx.md#html-创作组织) 写入任务书；一棒不等于一个 HTML、clip 或轨道。

1. 任务书写明仓库/项目路径、目标、允许修改范围、输出位置、验证与提交权限。
2. 报告写结论、改动与证据、实际运行命令及耗时、遗留问题；分清通过、失败和未运行。项目报告保存在约定的持久目录，不依赖可清理临时缓存。
3. 渲染中间物用项目 `cache/tmp/<runid>/`，留证用 `cache/reports/`；不要假定所有机器的系统 `/tmp` 都是磁盘或都可存放大文件。

## 命令正字法速查

- 子命令：`all | validate | audit | prepare | gfx | video | audio | mux | qc | clean | gc | df | snapshot | subs | diff`。
- `--draft [--frames N]`：`--draft` 只接受 video/mux；`--frames` 只接受 video draft。`--gpu` 只接受 video，不能加在 all 后。
- `all --until` 终点：`validate | audit | prepare | gfx | video | mux | qc`，audio 不作终点；交付语义见共用链。
- `audio --report` 与 `--report-only` 互斥，且不能与 dry-run 组合。`diff --json` 用于 timeline 影响报告，报告对比模式不接受该开关。
- Blender 的 json2blend/verify_blend 必须由 Blender 解释器运行，具体调用见 [edit 工具入口](edit.md) 与 [Blender bridge](../../../apps/blender-bridge/README.md)。发布与分轨命令不在此重复维护。
