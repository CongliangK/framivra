# PR#1（Windows 适配）→ main 合并报告

- 分支：`merge-pr1-candidate`，merge commit **f0c98dd**（parent① 7a75655 = main，parent② bab3134 = pr-1，真双亲 merge commit）
- 实验场所：git worktree `/tmp/pr1_merge`（主工作树与 main 分支指针全程未动）
- 合并人：ZCode（GLM），2026-09-11

---

## 1. 冲突文件裁定表

| 文件 | 冲突点 | 裁定 | 理由 |
|---|---|---|---|
| `vtx/infra.py` | PR：`Ctx.cache = cache/<input_fingerprint>/` + `project_fingerprint`（全量 sha256 所有源媒体+vtx/*.py）；main：`cache = root/cache` 平铺 | **保留 main 布局**；PR 的指纹降级为「回执身份」`Ctx.input_fingerprint`（见 cache_state.py 重写） | PR 布局每次 CLI 全量读 GB 级源（voice-ime 15G 资产），且整仓换目录会废掉 main 13bed7a 的 gc 键集回收/df/snapshot/manifest 记账（它们全部锚定 `cache/{gfx,seg,audio,reports,tmp,snapshots}` 平铺布局）。回执侧价值（输入变→不继承旧成功）用便宜材料同等保留 |
| `vtx/infra.py` | PR：`Ctx.gfx_dir(clip)`=sha256(绝对路径+dur) | **放弃 PR 版**，保留 main `hygiene.gfx_dir`（键=html 字节+依赖+fps+视口+dur） | main 内容键覆盖 PR 全部场景（同名页不同内容→自然分键；同页换时长→dur 在键内），且同内容跨路径可复用、内容变更即换键（PR 路径键对内容变更不敏感，还依赖外层指纹兜底——本合并已把内容敏感性直接放进键里） |
| `vtx/infra.py` | PR：receipt 绑指纹 + dry-run 不落回执 | **采纳 PR**，融合 main 的 `extra` 参数（d3dfec6 audio 响度入 receipt） | dry-run 改写真实回执是 main 现存 wart；extra 通道保留 |
| `vtx/infra.py`（新增） | PR：`begin_stage`（依赖门+DOWNSTREAM 下游失效）、`filter_filename` | **全部采纳**（错误信息改中文+给出重跑指引；vtx.py 侧友好捕获退出） | PR 核心回归修复之一：失败重跑不得保留旧 QC PASS；单跑阶段前置校验 |
| `vtx/cache_state.py`（新增文件） | PR：project_fingerprint 全量哈希 + DOWNSTREAM + load_receipt | **重写**：`receipt_fingerprint`（timeline 字节+源 stat 采样指纹+gfx html 字节+声明依赖内容）、`media_stat_digest`、`validate_cache_dependencies`、DOWNSTREAM、load_receipt 原样 | 见上；回执敏感度 == 缓存键敏感度（任何键会变的输入变化，回执门同样重置），零全量媒体 I/O |
| `vtx/hygiene.py`（main 独有，PR 未触） | — | **_fp 采样补强**（首/尾 64KB sha1 并入 stat 指纹）；**gfx_key 并入 cache_dependencies 内容** | ①关闭 PR 回归场景「同尺寸+恢复时间戳的同路径替换→旧 seg/audio 误命中」——媒体重编码首尾必变，128KB/源 的 I/O 可忽略；②PR 的显式依赖声明落到正确粒度（只有 gfx 消费这些资产；声明属项目级=保守过失效，可接受）。布局/gc/df/snapshot/VERSIONS/manifest 零改动 |
| `vtx/composite.py` | PR：`subtitles=` → `subtitles=filename=`+`filter_filename` 双层转义 | **采纳** | Windows 盘符冒号必需；Linux 普通路径转义后逐字符不变（`\\':`,`\\'][,;` 转义集与 POSIX 路径无交集），`filename=` 显式形式与位置参数等价。真 ffmpeg 集成测试（路径含 `中文 #,[]; '`）通过 |
| `vtx/composite.py` | PR：vseg 目录变体 `tag-n{nseg}-gpu{gpu}` | **放弃目录变体，改为段文件名携带内容键** `_seg_content_key`（区间+画布/draft/gpu+每层上游内容键与参数+段字幕 ass 字节 → `seg{si}_{a:06d}_{key}.mp4`） | 更强的融合：不仅隔离 nseg/gpu 变体（PR④），还关闭 **main 现存漏洞**——改字幕/改图层/换 gfx 内容后，分段「存在性+帧数」缓存命中旧段（PR 回归场景②的 main 版）。上游层标识复用 13bed7a 内容键，底层变→段键变。render_one 改为返回 segp、`ex.map` 保序喂 concat |
| `vtx/composite.py` | PR：concat 清单相对名 → 绝对 POSIX 路径+`'\''` 转义 | **采纳** | 含 `#`/空格/中文目录下相对项被 URL 基准解析出错（PR Windows 实测；Linux 同理）；`-safe 0` 原有，绝对路径合法。run_e2e 的 `素材 # inputs`/`中文 # project` 目录全流程通过 |
| `vtx/composite.py` | PR：`c.gfx_dir(cl)` vs main：`hygiene.gfx_dir(c,cl)` | **保留 main** | 同 infra 裁定 |
| `vtx/gfx_render.py` | PR：Canvas 导出（`__vtx_export_png` dataURL 信封校验+回落）；main d52946d：override 单截快路径+flatten 守卫 | **融合**：canvas 探测先行（首帧 navigate 后 `typeof` 检查）；opt-in 页 `transparent=True` 且整体旁路 override/flatten/两截（画布自带精确 alpha）；导出失效帧回落白黑双截（transparent=True 保证回落路径保 alpha）。do_frame 顺序：canvas→override→两截。统计 dict 增加 `canvas_export` | 两机制正交：canvas 是页面自愿的旁路，非 opt-in 页走 main 快路径不变；回落路径与 override 无交互（canvas 页不设 override）。flatten 守卫与 canvas 并存无冲突（互斥分支） |
| `vtx/gfx_render.py` | PR：`as_uri()` 替代 `f"file://{path}"`（3 处 navigate：do_frame/首帧/flatten 探针） | **采纳** | Linux 上 `html` 本就是绝对路径（`file://` + `/abs` = `file:///abs`），普通路径两形式逐字符一致（行为零变化）；特殊字符（`#` 截断为 fragment、空格、Unicode、`?`）则 as_uri 正确编码——Windows 盘符与 Linux 特殊目录通吃。voice-ime 28 页 ×~11k 帧真渲染通过 |
| `vtx/vtx.py` | PR：主循环 begin_stage（含 draft 后缀映射）；main：gc/df/snapshot 子命令+gfx 页级并行 | **叠加**：main 循环内插 begin_stage（RuntimeError 友好打印退出）；Ctx 构造加 ValueError 捕获（cache_dependencies 快速失败的友好报错） | main 的 gc/df/snapshot/gfx 并行全部保留；PR 的阶段门生效于所有入口（含 all） |
| `vtx/vtx.py` | PR：clean 用 `cache_root` | **回退为 main 原样**（`c.cache`） | 合并布局下 `cache_root` 属性已不存在（PR 版是 no-op 改动，保留会 AttributeError） |
| `vtx/timeline.schema.json` | PR：`cache_dependencies` 字段 | **采纳**（无冲突） | 与 Ctx 构造校验、gfx_key、回执身份配套 |
| `docs/cache-provenance.md` | PR 原文描述 `cache/<input-fingerprint>/` 布局 | **重写**为合并后架构（per-产物键+采样指纹+段内容键+回执门） | 文档必须与 main 现实一致（任务要求） |
| `docs/canvas-export.md` | PR 原文基本成立 | **微调**：缓存布局表述改为 per-产物内容寻址；Linux 复验说明 | 同上 |
| `tests/test_cache_state.py` | PR 断言 `c.cache` 随输入变（整仓指纹布局） | **适配**：等效断言改为 `input_fingerprint`/`hygiene.gfx_key`/`media_stat_digest` 随内容变；保留全部场景（时间戳恢复、依赖失效、缺失依赖 fail-fast、回执失效、同名页隔离） | 布局断言随被放弃的设计一起退役；测试意图逐条保留且全部通过 |
| `tests/run_cache_regression.py` | PR 断言 `cache/**/gfx-*` 目录>1 | **适配**为 `cache/gfx/` 子目录>1；另加沙箱兼容（`sys.executable` 可能指向包装器 *.AppImage → 回退 `shutil.which('python3')`，VTX_TEST_PYTHON 可覆盖） | 布局适配同上；沙箱 shim 不改变被测语义（仅修复子进程解释器解析） |
| `tests/run_e2e.py` | 同上沙箱问题（本机首次运行即中招：sys.executable=ZCode AppImage，子进程变 Electron） | 同上 shim | 环境兼容 |
| `tests/test_graphics_portability.py`、`tests/verify_e2e.py` | 无冲突 | **原样保留** | 与合并后代码兼容（`infra.filter_filename`、`gfx.decode_canvas_export` 均在） |

### 被放弃的另一侧逻辑清单（及理由）

PR 侧放弃：
1. **整仓指纹缓存目录** `cache/<input-fingerprint>/`——GB 级每次 CLI 全量 I/O；摧毁 gc/df/snapshot/manifest 布局；过失效（改一条字幕→全部 gfx/seg/audio 重来）。合并后：失效精确到产物键，回执门保证编排正确性。
2. **vtx/*.py 实现哈希入指纹**——任何代码改动→全缓存失效，开发迭代不可接受。替代：升级渲染器后显式 `vtx clean`/`gc`（文档已写明）。
3. **`Ctx.gfx_dir` 路径哈希版**——被 hygiene.gfx_key 内容键覆盖（见裁定表）。
4. **`clean` 的 `cache_root` 改动**——合并布局下无意义。
5. **vseg 目录变体命名**——被段内容键取代（更强：连 main 的旧漏洞一起关闭）。

main 侧放弃：
1. **`_fp` 纯 stat 指纹**（path+size+mtime_ns）——同尺寸+恢复时间戳替换会误命中（PR 回归场景）。补强为首/尾 64KB 采样（保留 stat 全部成分，追加内容采样）。
2. **vseg 段「存在性+帧数」缓存键只含 si+a**——字幕/图层/gfx 内容变后旧段仍命中（main 现存漏洞）。改为段文件名含内容键。
3. **dry-run 落回执**——PR 的 dry 守卫采纳。

（无人放弃：main 的 gc/df/snapshot/VERSIONS/manifest/budget_gate/页级并行/PIL 快路径/override/flatten 守卫全部原样保留。）

---

## 2. 验证证据

环境：Fedora 44，Chrome 152（/usr/bin/google-chrome），ffmpeg 7.x（含 libfdk_aac、subtitles 滤镜、NVENC/RTX 4080），numpy 2.5.1 + PIL 12.3.0。

### a. PR 自带 5 个测试（全过）
- `python -m unittest discover -s tests -v`（VTX_TEST_CHROME/VTX_TEST_FFMPEG 均设真二进制）：**5/5 OK**
  - test_cache_state ×2：时间戳恢复的内容替换→指纹变；依赖内容变→gfx 键变；缺失依赖→ValueError；回执门失效链；同名页/换时长 gfx 键隔离。
  - test_graphics_portability ×3：PNG 信封校验（合法/截断/损坏/尺寸不符）；真 Chrome 两帧 canvas/透明 HTML/无效导出回退+白黑合成一致性；真 ffmpeg 特殊字符路径字幕实烧（灰度>150 证明烧上）。
- `tests/run_cache_regression.py`（真渲染，warm 编辑后 vs 冷启动）：`max_frame_mean_difference=0.0`，`audio_equal=true`，`output_changed=true`，`same_named_pages_have_separate_cache=true` → **PASS**（同尺寸+恢复时间戳源替换、改字幕、改 HTML 后，warm 重建与冷启动逐像素一致）。
- `tests/run_e2e.py`：单段/两段并行全流程 ×2 工程（路径含 `#`/空格/中文），严格 QC、全解码、blackdetect、真峰全部通过，`PASS: True`（帧差 max 0.0095/255）。
- `tests/verify_e2e.py`：PCM 逐样本一致、19 个全分辨率探帧（含段缝/字幕边界/gfx 边界）全部 <2 → `pass: true`。

### b. voice-ime 真实工程 E2E（9分18秒 1080p30，5 轨 69 层，45 源，150 字幕，16 音轨）
- 隔离：`cp -rs` 到 `/tmp/pr1_e2e`（实际落盘 `/home/liangruochong/pr1_scratch/pr1_e2e`，/tmp 为 tmpfs，遵循 hygiene「GB 级严禁系统 /tmp」纪律）；解除全部写透符号链接（out/ 整树重建、vseg list/subs、subs.ass/manifest 等）。
- `python3 vtx/vtx.py -t timeline.json all`：validate→audit→prepare(45 源重抽)→gfx(28 页全部内容键命中)→video(82 个 seg 全部按新采样键重编 + 8 段分段渲染，拼接自检 16746==16746)→audio(16 轨 72 事件、13 轨 duck、-14 LUFS)→mux(libfdk_aac)→qc。**QC 74/75**。
- 唯一失败项 `gfx 窗口 c4_mini 生效（均值差 2.2/覆盖 2.8%）` 与 live 工程 `out/qc_report.json` 里的同项失败**数值逐位一致**——live 时间线固有状态（该页探帧处近全透明），非合并回归。
- 回执绑定 input_fingerprint ✓，VERSIONS 记账 ✓，切点序列与 live 完全一致（2093/4186/6280/8373/10466/12560/14656，确定性保持）。
- live 工程（/home/.../voice-ime）的 out/、final.mp4、receipt 均未被改动（mtimes 保持 12:04）。

### c. --dry-run all 与 main 基线对比（voice-ime，同一隔离副本顺序执行）
- 两侧 `[dry-run]` 命令数 **95 == 95**；对 12-hex 内容键做掩码 + gfx「已缓存/渲染」状态归一（先跑侧无缓存必渲染，后跑侧命中——运行顺序产物，键值两侧完全一致）后，剩余差异**全部**属于：
  1. cache/audio、cache/seg 的内容键名（设计变更：采样指纹）；
  2. 段文件名（设计变更：内容键）；
  3. 300 字符截断处露出的键尾片段。
- 除设计内键名变化外**无任何滤镜/命令差异**。`subtitles=filename=` 形式因大 graph 命令打印截断不可见于 dry 输出，改由真实渲染验证（集成测试烧录 + E2E 字幕 QC 全过）。
- 注：`--dry-run all` 在两侧均以 rc=1 结束于分段拼接自检（dry 下 concat 不执行、产物不存在→帧数自检失败）——**main 现存行为，非合并引入**。

### d. py_compile
`python3 -m py_compile vtx/*.py tests/*.py` 全部通过（收尾复跑通过）。

---

## 3. 遗留风险与建议后续测试

1. **源指纹采样哈希非对抗性**：首/尾 64KB+size+mtime 不防「刻意构造的中间字节篡改且恢复时间戳」。与 PR 原文档口径一致（流程溯源，非防篡改）。如需强保证，后续可加 `VTX_STRICT_FP=1` 全量哈希开关。
2. **cache_dependencies 为项目级**：任一依赖变化→全部 gfx 页重渲（保守过失效）。若未来单页依赖差异大，可演进为 per-clip 声明。
3. **段内容键的内存中上游键依赖**：`_seg_content_key` 引用 `L["seg"].name`/`pngdir.name`——若未来 seg/gfx 键格式变化，段键自动跟随（安全方向）；但段缓存命中前提是上游目录名稳定，改动 hygiene 键格式会使全部分段重渲（可再生，成本可接受）。
4. **`c4_mini` 项目固有 QC 失败**：建议项目侧核对该页 15235/15284/15334 帧的预期内容（live 已带此失败，与本合并无关）。
5. **dry-run 分段自检误报**：main 遗留——dry 下拼接自检对不存在的产物报错退出。建议后续给自检加 `if not c.dry` 门（一行修复，本次合并未顺手改以保持行为最小差异）。
6. **gfx 页协议未声明依赖时**（现有全部工程如此），HTML 外链资产变更仍不触发重渲（PR 与合并后共同边界）——gfx.md/cache-provenance.md 已声明，建议真实工程逐步补 `cache_dependencies`。
7. **跨平台**：Linux 侧已全面复验；macOS 未测（PR 亦未覆盖）。Windows 侧本合并保留了 PR 全部修复且未改动其语义，但建议 Windows 机器上复跑 `tests/run_e2e.py` 终验。
8. 测试沙箱兼容 shim（`PYTHON=…AppImage 回退 which`）是本机 ZCode 包装环境特有；常规环境 `sys.executable` 直用，行为不变。

## 4. 工件位置

- 合并分支 worktree：`/tmp/pr1_merge`（merge commit f0c98dd）
- 回归/E2E 产物：`/tmp/pr1_regr`（cache regression）、`/tmp/pr1_e2e_synth`（合成 E2E）、`/tmp/pr1_e2e`→`/home/liangruochong/pr1_scratch/pr1_e2e`（voice-ime E2E，约 12G，可 `rm -rf` 回收）
- dry-run 对照：`/home/liangruochong/pr1_scratch/dry_{main,merged}.txt` 与 `dry_norm_diff.txt`
