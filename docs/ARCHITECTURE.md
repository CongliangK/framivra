# ARCHITECTURE · 系统结构与扩展契约

> 本文件是当前架构及 v2 冻结契约的权威：定义内核边界、数据流、扩展协议、安全与验收不变量。契约或稳定 API 的签名/语义变更须过独立对抗审查，不接受实现者单方面改约。操作从 [Skill](skill/SKILL.md) 路由，开发与专题从 [DEV](DEV.md) 路由；迁移过程及其一次性基线保存在[原定稿记录](https://github.com/CongliangK/framivra/blob/3d9e6622d5d06eecf55ffbe2075da1fa994f93f5/docs/ARCHITECTURE.md)，不是当前性能承诺或待执行清单。

## 0. 判据与四层架构

### 0.1 内核边界

**把组件关停后，主链产物还可信、可交付吗？** 不可信/不可交付的能力属于永不可关的 `vtx/` 内核；可替换素材来源或交付增强属于可卸载组件。validate、渲染、混音/limiter、qc 与缓存共同构成可信产物；时间线操作、脚手架、插件运行时和公共 API 是内核配套能力，不因某项可手工替代而变成插件。

### 0.2 应用与插件

| 层 | 契约与职责 |
|---|---|
| `vtx/` 内核（永不可关） | timeline 结构/校验、帧精确渲染、音频终链、缓存与验收；仅内核也能完成可信出片 |
| `apps/` 应用 | 产出独立素材或产线辅助结果，不扩展 timeline 管线；替换素材来源不改变内核语法 |
| `plugins/` 插件 | 扩展 timeline 键并接入管线；键/钩子承担内核级稳定承诺，缺失或禁用不能忽略已声明的键 |
| `templates/` 工作流与范本 | 组合内核/组件、配方和资产，不另设运行时或文档职责层 |

ASR 只负责产出字幕素材，消费与烧录由内核 `subtitles_src` 定义；降噪通过源级插件参与 prepare。内容发布复检属于 gates 应用，TP 可信度门属于内核。调色扩展缝不预建，须有真实消费者与协议验收对象后再审查。

目录和安装态唯一清单是 [catalog](catalog.md)；`docs/skill/` 承载通用操作，仓级工具归 [scripts](../scripts/README.md)，文档职责与写入权归 [DOC-CHARTER](DOC-CHARTER.md)。

## 1. 内核数据流与模块

### 1.1 执行与状态

```text
素材 + timeline.json → Ctx / validate → audit → prepare
                                              ↓
                                  gfx ∥ audio ∥ video 预编码
                                              ↓
                                         video → mux → qc → finish
```

这是完整 `all` 的默认调度；**`VTX_PIPELINE=0` 仍支持线性调度**。单阶段命令不等于完整交付；`--until` 的截断映射、正式交付步骤及前置条件由 [音频与交付流程](skill/references/edit.md#正式交付链r1-r4-共用) 定义，finish 条件见 §2.5。

| 职责 | 实现 home（均在 `vtx/`） | 边界 |
|---|---|---|
| 输入与时间线 | `timeline.schema.json`、`constraints.py`、`infra.Ctx`、`stages.validate`、`preview.html` | schema 是结构权威，validate 是执行校验权威，preview 是预览权威；只读预览不替代 validate，低分辨率测试上下文不等于正式项目规格 |
| 时间线操作 | `diffcmd.py`、`timeline_diff.py`、`impact.py`、`subs.py`、`range.py` | diff/snapshot/subs/区间预览辅助迭代；subs 的 render/preview/check 是信息性命令，不自动进入流水线 |
| 图形 | `gfx_backend.py`、`gfx_render.py`、`gfx_cli.py`、`gfx_cef.py`、`gfx_electron*.py`、`gfx_svg*.py`、`frame_source.py` | HTML 默认 CDP，支持显式 CLI、需 native worker 的 Alpha CEF、可选 Electron 复现；SVG 序列独立处理 Inkscape/字体/完成证书；FrameSource 是帧消费抽象，不是第二渲染后端 |
| 音视频 | `stages.py`、`media/audio_filters.py`、`composite.py`、`encoders.py`、`qc.py`、`media/subtitles_src.py` | prepare 抽取源音频，audio 混音终链，video 分段合成/字幕烧录，mux 探测 libfdk_aac 并回落、原子封装与记录 VERSIONS/history，qc 判定产物 |
| 身份与恢复 | `cache_state.py`、`hygiene.py`、`fstxn.py`、`recovery.py`、`loudness_cache.py` | 内容键、回执依赖、原子写、完成标记与跨进程锁共同决定复用；不是全源文件防篡改证明 |
| 扩展 | `pluginlib/`、`api.py` | 前者负责发现/加载/钩子，后者是组件复用的稳定内核 API |
| 诊断与证据 | `diagnostics.py`、`events.py`、`execution_links.py`、`acceptance.py` | 事件不绕过校验；离线 verify 不执行包内命令，完整性不等于验收通过或生产者真实性 |
| 资源 | `resource_budget.py` | 显式调用的预算原语，不代表已自动接入全流水线 |

HTML strict 依赖闭包**仅适用 CDP**；环境身份/资源闭包不承诺跨平台像素一致。资格见 [gfx](skill/references/gfx.md)，缓存、声明依赖及环境身份见 [缓存语义](cache-provenance.md)，事件/验收/预算见 [DEV 专题入口](DEV.md#专题入口)。

段级多层合成采用**逐层 pass**：每 pass 恰两输入 overlay，中间 FFV1 intra 无损产物落 `cache/vseg_pass`；单层及整片回落仍用单 graph。pass 键纳入合成代码的 AST 归一化摘要，剥离注释/docstring/格式，语法级改动改变身份；`cache/vseg/.graph_digest` 供 diff 提示代码摘要漂移或段缓存被回收后的冷建风险。不把历史 overlay/framesync 实验当作当前所有输入的确定性证明。

### 1.2 响度终链

`audio` / `all` 的 `out/audio.wav` 是归一化与限幅终态，无需项目侧可选步骤补安全链。`mix_audio_wav` 采用测量后静态增益，输出链为 `volume=…dB,aformat=sample_fmts=s16,alimiter=limit=0.599:level=false:latency=true`；整片与区间预览同增益、同限幅，s16 中间量化不能视为可删细节。

| 终态判据 | 行为 |
|---|---|
| 目标响度 | -14 LUFS |
| I 偏离目标 ≤1.5 LU | 静默通过 |
| I 偏离 ∈(1.5, 3] LU | WARN 放行，内容级问题交发布侧 G2 拦截，调整 bed 后重渲 |
| I 偏离 >3 LU、TP >-1.0 dBFS、I/TP 解析失败 | 硬 FAIL，不放宽阈值掩盖增益或限幅问题 |
| 整片音频时长 | 与期望值偏差最多 ±1 采样，超出 FAIL |

`build_audio` 执行测量→增益/限幅→复测→时长校验→回执；仅整片路径写 `post_loudness`（含 input/output I、gain、target、tp_dbfs 与输出 sha256）。阶段依赖通过后，`Ctx.begin_stage("audio")` 用同一文件事务撤销 audio 及 `DOWNSTREAM["audio"]` 的成功记录，避免中断留下陈旧 PASS。`raw_i` 只是归档值，不免首测、不用于二次施增益；range 仅经指纹校验复用 `gain_db`，陈旧回执回落窗口自测并告警。

干跑不落产物、不修改回执。独立既有 WAV 维护不是该阶段事务，不更新或撤销 audio/mux/qc 回执，不能用于补签或验收新 WAV；其写态与重复处理风险归 [音频工程](skill/references/edit.md#既有-wav-维护)。

### 1.3 TP 与 QC

**解码域 TP ≤-1.0 dBFS** 是内核 qc、gates G3 与 audio_report 的统一阈值；qc 与 G3 消费同一 loudness_cache 回执，G3 做发布侧复检，audio_report 对超阈判 FAIL。qc 的响度分级与 §1.2 一致，测量失败显性 FAIL；不把限幅导致的内容级损耗变成另一套内核硬门。限幅后 WAV 仍超 TP 阈须定位并提交对抗审查，不得就地放宽。

### 1.4 导入与 API

内核用包内相对导入，外部用 `from vtx import ...` / `import vtx.xxx`，插件经 `vtx.api` 复用 `audio_key`、`resolve_source_wav`、`plugin_code_digest`、`cfg_get` 等稳定能力，不捅内部实现。`vtx/vtx.py` 源码启动器先移出自身目录、锚定仓根，再调用 `vtx.cli.main`，避免同名自遮蔽。

**运行时管线、tests 与 apps 禁止注入 repo/vtx，禁止 `spec_from_file_location`**；平铺/包导入混用会产生双模块实例、拆裂回执与锁身份。apps 内部不强制包化。组件目录锚定自身文件；Blender audit_bundle 的 vtx 定位顺序是 `VTX_VTX_PY` → 向上含 `vt.py` 标记的仓根下 `vtx/vtx.py` → 技能安装路径回落。

## 2. 管线扩展协议

### 2.1 三不变量

**① 缓存键贡献。** 插件通过 `contribute_keys(c)` 将产物键并入 gc 白名单；渲染前的 budget_gate 可自动 gc，未登记的产物不能保活。源级产物以 `media_stat_digest(源)` / `vtx.api.audio_key` 为基础，加插件名、版本、参数与**插件代码摘要**，不得用 GB 级源全文件 md5 替代 stat 身份。代码摘要递归覆盖子目录/子包 `.py`，排除 `tests/` 与 `__pycache__/`，按根相对路径排序并编码路径/内容边界，输出 12 位摘要；测试改动不应翻转产物身份。

自立缓存目录需登记 registry `cache_dirs`；可选 `cache_dir_keys(c) -> {目录名: 键集}` 经 `get_cache_dir_keys` 聚合，仅对已登记目录生效。gc 的 `plugin_cache` 类别按一级子目录键保留当前键、回收陈旧键，豁免 `<digest>.lock`；未声明键集的目录维持整目录白名单。`contribute_keys` 与 `cache_dir_keys` 是不变量入口，**不是钩子，不参与 hooks 对账**。外部 manifest 的 `cache_dirs` 暂无生效通道，外部音频键仍可走 contribute_keys。

**② 回执指纹。** 保留 timeline 字节、源 stat、gfx HTML 与声明依赖成分，在末尾追加生效插件的**名称+版本+全部生效参数实例摘要+代码内容摘要**。安装/卸载/升级/改参/改默认值或代码使对应身份变化，不能用旧回执放行陈旧产物。**空生效清单省略整个插件段**，保持无插件段的 v1 指纹字节兼容；有段录制的旧回执与卸载后的无段重算仍失配。

生效要求 installed+enabled、声明钩子，且 timeline 含插件任一注册键；**无键声明的钩子插件全局生效**，不是“没写键就不执行”。键扫描、生效判断、参数摘要共用 scope：`root`、`project`、`sources[]`、`audio_tracks[]`、`audio_tracks[].events[]`。

**③ 安装态与键注册表。** `plugins/registry.json` 汇总全量官方 manifest 的键声明，并提供缺失插件的安装提示；官方实体只认 `plugins/<name>/`，目录缺失即 not_installed，不允许同名 external 顶替。对 timeline 中的插件键：

| 状态 | validate 结果 |
|---|---|
| 已注册、已安装且启用 | 合法生效 |
| 已注册但未安装 | 报错 + 安装提示 |
| 已安装但禁用 | 报错 + 启用提示，不能静默跳过 |
| 完全未知 | 警告、不阻断 |

`_meta` 永久作为自由命名空间，不触发上述未知键分支；插件仍可校验已知 `_meta` 标记防双施。“缺省关闭”只表示有键插件在不写键时无行为。

### 2.2 协议 1.1：源音频、事件与校验

- **缝 A `prepare_source(c, sid) -> Path | None`**：插件自算 `cache/audio/` 内容寻址路径，保证产物在盘后返回；命中直返、缺失产出，None 表示不受理并回落裸提取。不得把加工音频写入裸提取键，`(c, sid, wav) -> bool` 不受支持。prepare 与 range 冷填充共用 `source_audio_extraction`；产出侧、`_audio_graph` 和 `audio_report._collect_inputs` 统一经 `resolve_source_wav` 逐源解析/memo，禁止旁路裸键。
- **环境缺失 fail-hard**：已生效源插件缺权重/环境必须说明缺什么并 exit 1，不回落裸音频。doctor health 是组件环境预检通道；denoise 仅注册 prepare_source，允许在 prepare 失败，不为无键 timeline 强制额外 validate 探测。需要早检的插件可显式挂 validate。
- **缝 B `audio_events(c, tracks) -> list`**：filtergraph 构建前，对全量 tracks 做纯内存 events 变换，**不写回 timeline**；区间渲染走同一缝。duck-static 与手动烘焙工具共用 `transform_events`，保持全局增益×口播窗额外衰减、切段 src_in 连续，不分叉算法。
- **`validate(c) -> list[str]`**：内核扫描键后对生效插件调用；错误并入 errs 阻断，空列表通过，未安装/禁用分支不重复报错。

`audio_tracks[].duck_strategy` 必须为对象，`mode` 仅 `static`；默认 `global_db=-8`、`duck_db=-4`、`voice_track="voice"`、`merge_gap=20`、`attack=5`、`release=8`（后三项为帧数）。validate 检查数值类型与 voice_track 存在，拒绝同轨 `duck: true` 动态 sidechain 与静态策略叠加；存在 `_meta.bed_static_duck` 即阻断并提示恢复 pre-duck 备份，防已烘焙时间线双施。

### 2.3 manifest schema v1

```json
{
  "name": "^[a-z][a-z0-9-]*$（与目录名一致）",
  "version": "语义化版本串",
  "kind": "app | plugin",
  "entry": {"cli": ["根内相对路径；纯路由 app 可为空数组"]},
  "health": {"type": "command | config_key", "...": "命令/键名"},
  "requires": {"core": ">=MAJOR.MINOR.PATCH", "binaries": [], "python_packages": [], "platforms": []},
  "config_keys": [],
  "docs": "README 相对路径"
}
```

plugin 还声明 `extends.schema_keys: [{path, key}]` 与 `extends.hooks`。path 值域见 §2.1，hooks 值域为 `prepare_source` / `audio_events` / `validate` / `probe_source` / `finish`。manifest 静态登记 kind/键面，**代码是钩子注册的单一事实源**；加载使用 `importlib.import_module` 包导入，声明未注册或注册未声明均报错。registry hooks 缺失或为空列表时回落实体 manifest，不能通过清空表静默废掉插件。

**Core API 兼容门**使用 `vtx.__version__` 内核发行版本，不另设 epoch。`requires.core` 仅接受稳定三段最低版本 `>=MAJOR.MINOR.PATCH`；可选 `requires.plugin_protocol` 仅接受精确字符串 `"1.1"` / `"1.2"`，前者仅有 §2.2 三钩子，后者另含 §2.5 两钩子。显式 `"1.0"`、其他版本、空串、非字符串均拒绝，不推断、不协商范围。

实体 manifest 加载后，先判 core，再判显式协议及 manifest/有效 registry hooks 的协议值域，**然后才允许包导入和 hooks 对账**；模块已缓存也逐次检查。错误需含检测内核版本、声明值、允许协议与升级入口。discovery 保留协议值，由运行时拒绝不兼容 external，不能静默漏掉。缺少 core 或 plugin_protocol 各自按遗留策略处理，**不等于认证**；requires 对象仍必需。顶层未知 `protocol` 无协商语义，插件 version 仍是插件发行版本。矩阵见 [插件兼容](reference/plugin-core-compatibility.md)。

### 2.4 安全边界

- external 应用/插件默认 disabled，只有 config `external_apps[]` / `external_plugins[]` 的 `enabled: true` 启用；路径含 `..` 拒绝。官方插件启停由 registry 控制。
- manifest、entry、docs 与 health 脚本解析符号链接后必须在组件根内；health 首 token 必须是根内相对脚本，拒绝绝对路径/`..`/断链/根外落点。内置目录必须留在对应扫描根内，外部件只能显式注册，不借目录符号链接冒充内置。
- external 包导入只临时将父目录 **append** 到 sys.path，导入后摘除，并断言 `mod.__file__` 仍在插件根内，防同名模块顶替官方件或标准库；不使用 spec 加载。
- 已生效插件加载失败或 hooks 不一致必须 fail-hard；无键插件对所有 timeline 生效，显式启用就是代码信任边界，不是隔离沙箱。其代码变化同样纳入产物与回执身份。

### 2.5 协议 1.2：虚拟源与收尾

**缝 C `probe_source(c, sid) -> {has_audio, has_video, nb_frames, fps, w, h} | None`** 返回与 Ctx.probe 同构的 facts；None 回落 ffprobe。validate 源检查与音频事件时长校验必须同走该缝，允许 music_tools 工程 JSON 等非媒体源。

**缝 D `finish(c) -> list[str]`** 在完整 `all` 或 `all --until qc` 的 mux/qc 后触发，空列表成功。qc 判定失败不阻断 finish；qc/finish 任一失败最终 exit 1，正式产物已就绪不等于验收通过。draft、dry-run、终点早于 qc 的截断链不触发；单独 mux/qc 不触发，`all --until video` 在线性模式还未运行 audio。Blender 插件顶层 `blender_bridge: true|{out, verify}` 表示收尾直接导出审片工程，不需另行补隐式动作。

**bgm-render 的源与缓存契约：**

1. `sources[].bgm` 引用工程；probe 通过 `bgm show structure --json` 的 duration_s，必须与 render 的 build_render_plan/atrim 工程时长一致。prepare 派生 48kHz stereo s16 音频到 `cache/audio/{audio_key}::bgm:{version}:{digest}.wav`；digest 含键值、插件代码、`$ref` 拆分件及 extra_deps 内容，主工程字节不变不代表缓存有效。
2. output 为 `master|premaster|stem:<轨名>`。轨名用 `^[A-Za-z0-9][A-Za-z0-9_.-]*$` **fullmatch**（尾随换行也拒绝），仅经 subprocess 列表参数传递。stem 取 `stems/<轨名>.bus.wav`：含 gain/pan/space 的 float32 工程全长总线分轨，不是结果 JSON 中的引擎级 stems，也不是带 loudnorm/IR 尾的 master。缺失轨名 fail-hard 并列可用轨名；分轨 facts 时长仍等于工程时长。
3. 同工程共享一次渲染到 `cache/bgm/<render_digest>/`；render_digest 含工程字节、`$ref`、extra_deps 与代码，**不含输出选择和轨名**。成功以 tmp+os.replace 原子写 `_complete.json`，`<digest>.lock` 跨进程互斥；命中直接派生，不重复渲染。marker 完好但目标 WAV 缺失/空文件时覆盖重渲一次；不存在轨名等工程问题不触发自愈。
4. extra_deps 文件做 sha256[:16] 内容签名，目录按排序的相对路径与文件内容摘要递归签名；跳过 `__pycache__`、`.lock`、`.tmp`。文件数 >5000 或累计 >2GiB 时降级为 `(相对路径, size, mtime_ns)` 清单签名，`DIRC:` / `DIRL:` 前缀进入 digest JSON，降级可观测但不是全内容证明。
5. registry `cache_dirs: ["bgm"]` 与 `cache_dir_keys` 保留当前 render_digest、回收陈旧目录，弃用 bgm 后可作 legacy 回收；派生 WAV 仍经 contribute_keys。锁伴生件不得被 gc 删掉，破坏跨进程互斥。
6. CLI 按 config `bgm.cli` → 用户 `.local/bin/bgm` → 配置 bgm.repo 的 `python3 -m bgm_gen` 解析；config 路径不存在/不可执行时尝试下一通道，全败列每通道原因，不能假成功或回落未处理产物。

## 3. 应用边界（apps/）

### 3.1 自包含与依赖

每件 app 的 manifest、代码、README、tests 随目录自包含；当前清单以 [catalog](catalog.md) 为准，不在本页重复枚举。应用可卸载不代表已启用插件的依赖可忽略：如 Blender finish 依赖其应用/工具，生效后仍须按插件失败语义处理。matting 的项目路径由组件自身锚点、环境变量或 CLI 配置，不硬编码用户目录。

### 3.2 职责特例

- **bgm** 是纯路由声明 app，entry.cli 可空，把配乐需求与分轨审计知识路由到 music_tools，不含执行本体。
- **gates** 负责 G1/G2/G7/G10/项目门的发布前复检；G3 是同阈值复检，内核 qc/TP 不可关。
- **sync** 是管线前置素材工具，sync/repack 邻接源替换与 conform 写操作，必须幂等并遵守备份纪律。
- **Blender 审片/分轨审计**与 **publish 4K** 是可卸载的审阅/发布增强，不回写主线可信度；其具体交付门由组件 README 定义。

## 4. 模板集合（templates/）

### 4.1 工作流包

tech-talk 与 skill-demo 由 `skill.json` + `SKILL.md` 配对识别；manifest 含 name/version/uses_plugins/optional_plugins/docs/assets，`SKILL.md` 是工作流正文；发现检查不要求有无 frontmatter（tech-talk 含元数据，skill-demo 无），references/assets/scripts/templates 随包自包含。缺配对文件、坏 JSON、非对象 manifest 或错误 uses 声明均失败；uses 按**产线用法**而非最小依赖填写，具体值只由各 skill.json 维护。

通用 title-card/lower-third 属内核资产；voice-ime 七件 gfx、gfx-examples 与 `_tokens.md` 属 tech-talk。禁深蓝/蓝紫渐变与暖色基线是该工作流约束，不强加通用 gfx；matting 全流程归组件文档，创作路由见 [Skill](skill/SKILL.md)。

### 4.2 开发与验收范本

hello-app 示范自包含 app；hello-plugin 示范装/未装/禁用分支、缓存键贡献、环境 fail-hard 与卸载回收。两者默认 disabled，须 external 显式注册并 enable 才进入运行面；模板发现不注册、不启用、不探测它们。skill-demo 是零 apps/plugins 依赖的纯内核关停验收道具，包含素材生成、gfx、timeline 与 expected_check，后者以 `qc --strict` 断言全检含 TP。

## 5. 管理入口、分发与配置

### 5.1 vt.py 统一入口

`vt.py init` 是仓级依赖检查/初始化入口，`doctor` 检查版本、组件、工作流模板与挂载；`apps|plugins|templates list|check` 提供分层清单/体检，另有 version 与链接修复入口。

- **版本检查**：例行使用 `VTX_DOCTOR_OFFLINE=1`，只相对本地引用报告 clean/dirty/ahead/behind；联网 fetch 会写 .git（5s 超时），按写操作审查。VERSION 仅为发布标识，**无 tag 不告警**，发行冻结门独立判定。
- **组件检查**：manifest、health、requires 与 `import vtx` 实际解析路径；enabled 组件失败才作 FAIL 门，缺失/disabled 仅提示。health command 必须走子进程，不跨目录 import/spec；该入口用 `shutil.which` / `env python3` 找解释器，禁 `sys.executable`。
- **模板检查**：包完好、uses、挂载链接及内核 SKILL 索引新鲜度；doctor JSON 保留 `skills` 键与既有子字段，数据来自 templates。
- **写操作区分**：init 的 `--fix` 安装缺失基础依赖；doctor 的 `--fix` 修复三检内可自动修复的链接/注册表；`--fix-links` 重建挂载。不得混用；预演和实际操作见 [安装指南](guides/installation.md)。

### 5.2 包与命令归属

包名 `framivra`，console scripts 为 `vtx` / `vt`。clone 可直接用仓根 vt.py 和 `python3 -m vtx`，pip 提供全局命令与稳定 import；两路径必须同码同义。

wheel 包含 `vtx` / `vtx.pluginlib`、仓级 vt 模块、schema/preview 与官方插件声明资源；setup 从唯一 registry 源复制声明，sdist 带重建所需源。**apps/plugins/templates 执行代码、仓级 scripts/docs 不属于 wheel 承诺**；打包边界以 pyproject/setup/MANIFEST 为准。

组件命令直达 `apps/` / `plugins/`，仓级维护命令直达 scripts 职责目录；不提供根部组件别名或旧命令转发壳。技能 live 挂载与链接修复独立于组件别名；安装器保留仓内相对链接解包及 Windows 文件/目录物化。外部工作区和挂载必须逐件实查，仓内测试不能证明外部源件存在或链接有效，缺失/待办不得核销为完成。外部三载体的未结项与本机复验状态以 [已知问题④](reference/known-issues.md#external-installation-evidence) 为准。

### 5.3 配置

唯一加载器 `vtx/pluginlib/config.py`：**`VTX_CONFIG` > 仓根 `config.local.json`**。imgen 与其他组件同入口，不读 IMGEN_CONFIG、不迁移或改写旧配置；坏 JSON 警告后按空配置继续，兼容读取 comfyui/asr/tts/imgen/denoise 键。已知路径型键自动展开 `~`，其他路径消费方用 expand_path/expanduser，不展开任意 URL/文本。config.example.json 与 gitignored config.local.json 均在仓根，插件通过 `vtx.api.cfg_get` 读取。

## 6. 验收不变量

1. **回归与真实执行**：完整架构验收要求全量回归通过；按 [CONTRIBUTING](../CONTRIBUTING.md) 执行变更检查与文档门，如实报告全量/精选、工具/平台 skip 和未运行项；门绿不等于散文正确、外部环境已核销或性能达标。
2. **内核独立**：apps/plugins 全禁时，永不可关的纯内核完整 all 仍可出片并通过含 TP 的 qc。
3. **插件正确性**：安装/缺失/禁用三分支、gc 保活、改版本/参数/代码的产物与回执失效、external 默认关闭→显式启用→卸载回收须实际覆盖。
4. **入口与身份**：当前命令参数/退出码保持；clone/pip 同码同义；活代码无本机 `/home/<user>` 硬编码、失效旧入口、repo/vtx 注入或 spec 加载。运行产物不入库，测试资产不可被忽略。
5. **安全复审**：独立对抗审查覆盖键分支绕过、缓存键伪造、回执成分遗漏、路径逃逸，不以实现者自测代替。
6. **性能证据**：比较必须约定同项目/输入/环境/链路，原迁移影子基线及历史性能门只作溯源；当前发行资格由 [源码稳定基线](releases.md) 判定，不以旧报告、旧吞吐或未复现缺陷宣称本次验收完成。
