# ARCHITECTURE · v2 插件化架构设计冻结件

> **本文件是 v2 重构的设计冻结件**（v2.5 拍板方案全文落实），与 `docs/plans-v2/` 基线配套。
> 后续所有迁移/实现阶段以本文件为准；**修改本文件须过对抗审查**，不接受实现过程中单方面修订。
> 术语来源标注约定：〔用户拍板〕= v2.5 方案定稿裁定；〔对抗审查裁定〕= 对抗审查 P0 裁定；
> 〔代码现状〕= 迁移前 main 分支实测事实（文件:行号），实现时以此为基准核对。

---

## 0. 判据与四层架构

### 0.1 关停测试（内核 vs 可卸载的唯一判据）

对任一组件问一个问题：**把它关停后，主链产物还可信、可交付吗？**

- 不可信/不可交付 → **内核**（vtx/，永不可关）。
- 仍能可信产出一期片 → **可卸载组件**（apps/ 或 plugins/）。

推论：validate/qc/混音响度链这类"产物可信度"的构成部分一律内核；asr/imgen 这类"素材来源替代"一律可卸载。〔用户拍板〕

### 0.2 应用(app)与插件(plugin)的定义

| 维度 | 应用 app | 插件 plugin |
|---|---|---|
| 产物 | 产出**独立素材**，不进 timeline 管线 | **扩展 timeline.json 词汇**并钩进管线 |
| 对主线影响 | 报错不影响渲染主线 | 装与不装，JSON 语法不同 |
| 稳定性等级 | 维护宽松（坏了换素材来源） | **内核级稳定承诺**（键/钩子语义冻结） |
| 判据 | 关停后主链产物不受影响 | 关停后写了对应键的 timeline 不可渲染 |

〔用户拍板〕例：asr 是应用（字幕是它的产物，不是它的运行时）；denoise 是插件（`sources[]` 级键，prepare 抽音频时按需触发，进 cache）。

### 0.3 审查裁定表（P0 四条）

| # | 裁定 | 理由 |
|---|---|---|
| 1 | **limiter 上内核**（不是插件） | 内核 TP 门 + limiter 若可选关 = 默认出片必挂 qc——安全链不能是可选件 |
| 2 | **gates 留应用**，但 G3 的 TP 判定并入内核 qc | gates 是发布前内容级复检（G1/G2/G7/G10/项目门），TP 单项属产物可信度 |
| 3 | **调色缝本轮不预建** | 无现役消费者，预建缝=无验收对象的抽象；留待真实需求出现再立 P1 协议 |
| 4 | **字幕 = 内核 `subtitles_src` 键**（消费侧） | timeline 引用字幕文件来源属时间线词汇；与 asr 解耦（asr 只是产出该文件的一种应用） |

〔对抗审查裁定〕

### 0.4 目标目录形态

```
video_tools/
├── vtx/            # 内核包（永不可关，见 §1）
├── apps/           # 12 件应用（见 §3）
├── plugins/        # 官方插件（denoise / duck-static，见 §2）+ registry.json
├── skills/         # 工作流包（tech-talk，见 §4）
├── examples/       # hello-app + hello-plugin 纪律范本 + skill-demo 虚拟歌手骨架（见 §4.2）
├── scripts/        # 内核工具与仓级基建：new_project / gfx_golden / bench_cold + run_all_tests / docs_smoke + 迁走件的兼容 symlink
├── vt.py           # 统一入口（doctor/list/check/version/init，见 §5.1）
└── pyproject.toml  # 包 video-tools，console scripts vtx/vt（见 §5.2）
```

---

## 1. 内核（vtx/，永不可关）

### 1.1 能力清单

内核 = 时间线操作 + 渲染 + 可信度三块，具体：

1. **时间线操作**：schema（timeline.schema.json 结构权威）/ validate / audit / diff / snapshot / subs（render/preview/check，信息性命令不进流水线〔代码现状 subs.py 模块头〕）/ clean / gc / df / preview.html（可视化预览权威）/ new_project 脚手架。契约三处互指（validate=机制权威、schema=结构权威、preview.html=预览权威〔代码现状 stages.py:1-7〕）。
2. **HTML gfx 渲染**：CDP/Chrome 帧截图链（gfx_render.py、encoders.py 的 Chrome 定位）。
3. **ffmpeg 合成**：prepare（音频提取）/ video（分段并行合成 + 字幕烧录）/ mux（含 libfdk_aac 探测回落、原子替换、VERSIONS/history）/ composite / encoders / 区间渲染。
4. **响度终链**：两遍静态增益 -14 LUFS + **limiter 并入 build_audio 尾部**（§1.2）。
5. **qc 含 TP 判定**（§1.3）。
6. **`subtitles_src` 键**：timeline 可声明字幕文件来源路径，内核只负责引用与烧录语义；谁产出该文件（asr/手写/别的工具）内核不感知〔对抗审查裁定 #4〕。
7. **内容寻址缓存**：gfx/seg/audio 键 + manifest 记账 + gc/df/snapshot/VERSIONS/budget_gate（hygiene.py）+ 回执身份/依赖门/下游失效（cache_state.py）+ 响度回执（loudness_cache.py，跨进程 flock〔代码现状 loudness_cache.py 模块头〕）。
8. **pluginlib**：插件加载/注册表/钩子协议运行时（§2），与 **api.py**（供 apps/plugins/skills 复用的稳定内核 API 面）。
9. **内核工具留 scripts/**：new_project.sh / gfx_golden.py / bench_cold.sh 三件（渲染链自检与基准，属内核可信度基建）。

**死资产删除**：`vtx/models/*.rnnn`（bd.rnnn / sh.rnnn）——全仓 grep 无任何代码引用〔代码现状：vtx/models/ 仅两文件，vtx/*.py 无 rnnn/models 引用〕。

### 1.2 响度终链合并（limiter 入 build_audio 尾部）

现状：`vtx audio` 两遍静态增益到 -14 LUFS 但**不限幅**，峰值安全由项目侧 `scripts/post_loudness.py` 事后兜底〔代码现状 stages.py:323 mix_audio_wav docstring「峰值安全由项目侧 post_loudness.sh 的 alimiter 兜底」〕；post_loudness.py 独立进程做 测量→`volume={gain}dB,alimiter=limit=0.599:level=false:latency=true`→复测 |after-target|≤1.5 LU→时长校验（±1 采样）→**回执手术**（摘除 receipt 的 audio 阶段与 DOWNSTREAM['audio'] 全部下游，再回写 audio 阶段附 post_loudness 字段，中断不留陈旧 PASS）〔代码现状 post_loudness.py:33-67、cache_state.py:91-96〕。

v2 定案〔用户拍板〕：**整套逻辑吸收进 `vtx build_audio` 尾部**，即 `all`/`audio` 产出的 out/audio.wav 恒为"归一+限幅"终态；post_loudness.py/.sh 变薄壳调同一内核函数（CLI 命令面保留，§6.4），不再有独立进程语义。要点：

- **施加位置**：alimiter 段下沉进 `mix_audio_wav` 输出链（`volume=…dB` 后追加 `,alimiter=limit=0.599:level=false:latency=true`）——build_audio 与区间预览（range.py 直调 mix_audio_wav）**统一过 limiter**，杜绝"成片有限幅、预览无限幅"的分叉〔对抗审查 P0 复审 P1-1/P1-2 裁定〕；终态校验与回执 `post_loudness` 字段仅 build_audio（win=None）路径执行。
- **终态校验分级（总控裁定 2026-09-13 影子复验期，修订原"照搬 ±1.5"）**：机制/内容分离——硬 FAIL：限幅后 TP > -1.0 dBFS（limiter 失效）、时长漂移 ±1 采样、I 偏离目标 >3 LU（增益链断裂）；WARN：I 偏离 ∈ (1.5, 3]（限幅响度损耗属内容级——配乐密集/原始混音偏 quiet，发布侧 G2 拦截，提示调 bed 重渲）；≤1.5 静默通过（v1 语义）。证据：live voice-ime v1 回执 input_i=-14.0→output_i=-15.5（\|偏离\|=1.5 恰压线已交付），同内容 v2 浮点域限幅实测 -15.6——边界内容 + 0.1 LU 测量抖动不得成为管线 flaky 硬 FAIL。回执 post_loudness 字段增记 tp_dbfs。
- 测量→增益→alimiter→复测→时长校验→回执，顺序与阈值逐条照搬 post_loudness.py 现实现。回执字段 `raw_i`/`gain_db` 语义保留；**勘误**（P0 复审 P1-1）：stages.py:353-356 注释所称「raw_i 供 post_loudness.sh 免首测复用」已失修——post_loudness.py:38 每次实测（docs/post-loudness.md:11 记载旧 wrapper 曾因读该字段双施 gain 的事故），全仓无代码消费回执 raw_i；`gain_db` 的唯一真实消费者是 `vtx/range.py:100`（区间渲染复用整片增益）——吸收后语义不变（预览与整片同增益同限幅）。迁移时一并修正该失修注释。
- **R4 链消费者改写清单**（吸收后随 P1 同步改）：bench_cold.sh 四处（L12/54/156/203-260 独立 post_loudness 步骤与逐步骤计时表；门 8 的 291.4s 口径随链结构重定）、tts/tts_cli.py:70 与 tts/indextts_worker.py:70 与 scripts/repack_voice.py:150 的「audio 后 mux 前必跑」提示文案、vtx/vtx.py:63-64/419 的 --until 帮助文本〔对抗审查 P0 复审 P1-3〕。
- 干跑（--dry-run）不落产物不手术，与现行门控纪律一致。
- R4 全冷链（`all --until video` → post_loudness → mux → qc）简化为 `all --until video` → mux → qc；`--until` 语义表随迁更新。

### 1.3 TP 阈值权威统一

现状三处口径不一：

- gates G3：解码域 TP ≤ **-1.0 dBFS**，`--g3-tp` 默认值〔代码现状 content_gates.py:143〕。
- qc：ebur128 peak=true 顺带测 TP 只**归档回执**（loudness_cache），自身检查只用 I，注释明言「TP 门在 gates G3」〔代码现状 qc.py:322-339〕。
- audio_report：TP_WARN=-1.0 / TP_FAIL=+3.0，且 -1~+3 判 **INFO**（参考值不计 WARN），依据是「alimiter -4.45dBFS 设计 headroom 内 v4.2 定稿实测 TP +0.7~+0.8 属设计内」〔代码现状 audio_report.py:12-15、59-60、89-94〕。

v2 定案〔用户拍板〕：**G3 的 -1.0 dBFS 为唯一权威**，三处对齐——

1. 内核 qc 新增 TP 判定项（消费 loudness_cache 回执，解码域，阈 -1.0）；gates G3 保留为发布侧复检（同一阈值、同一回执，不重测）〔对抗审查裁定 #2〕。
2. limiter 恒在 build_audio 尾部后，audio.wav 域 TP 应稳定 ≤ -1.0，audio_report 的 -1~+3「设计内」INFO 档**失去存在依据**，判级改写为与权威一致的 FAIL 阈；`audio_report.py:12-15` 的「TP+0.7~+0.8 属设计内」口径注释**一并删除改写**（该口径属 post_loudness 可选时代的实测记录）。
3. 实现时若发现限幅后 wav 域 TP 仍 > -1.0（alimiter 瞬态过冲），按对抗审查流程上报裁定，不得就地放宽阈值。

### 1.4 包化规程

- `vtx/` 加 `__init__.py`，模块内**全部改相对导入**（`from .infra import Ctx` 风格）。
- `vtx/vtx.py` 改**薄启动器**：先把脚本自身所在目录移出 `sys.path`、插入 repo 根，再 `from vtx.cli import main`——防 `vtx/vtx.py` 文件名与包名同名自遮蔽（目录在 path 上时 `import vtx` 会命中目录而非包）。保留 `python3 vtx/vtx.py ...` 直接可跑（现状入口〔代码现状 vtx/vtx.py:87-91 的 sys.path 自注入〕语义等价重写）。
- **废弃一切 `repo/vtx` 的 sys.path 注入**（现状运行时六处：vtx/vtx.py:91、vtx/vtx.py:175、scripts/post_loudness.py:4、scripts/content_gates.py:49、scripts/export_4k.py:48、scripts/gfx_golden.py:47；另有 tests/ 12+ 处注入〔对抗审查 P0 复审 P1-2 勘误〕）。动机：平铺 import 与包 import 并存会造成同一模块双实例——infra 的 receipt 锁/hygiene 的 manifest 锁/loudness_cache 的 flock 若落在两个模块对象上，锁形同虚设（「双模块裂分」）。
- tests/ 的平铺 import 与注入锚定随 P1 包化一并改写为 `from vtx import`（§6.2 清单已补）。验收门 7(c) 的禁令范围为**运行时管线代码 + tests**；apps 内部结构不强制包化（独立 CLI 自治），但同样禁止注入 repo/vtx、禁止 spec_from_file_location。
- 外部一律 `from vtx import ...` / `import vtx.xxx`；pyproject 安装后同码同义（§5.2）。

---

## 2. 管线扩展协议 v1（音频侧）

协议先于一切钩子：**三不变量是地基**，任何插件不满足三条之一即不合规。

### 2.1 三不变量

**不变量 1 · 缓存键贡献**。插件经 `contribute_keys(c)` 把自己的缓存键并入 gc 的存留白名单。现状缺口〔代码现状 hygiene.py:231-245〕：`gc_plan` 把 `cache/audio/` 下不在 `_current_keys` ak 集合内的文件、`cache/` 根下不在 known 集合内的目录一律判垃圾，而 `budget_gate` 在渲染阶段前自动触发 gc〔hygiene.py:440-455〕——插件产物若不注册键，**渲染中就会被自动回收**。denoise 缓存键构成 = `media_stat_digest(源)` + 插件名 + 版本 + 参数摘要（**勿用全文件 md5**，GB 级源全量哈希违背缓存卫生层哲学〔代码现状 hygiene.py:39-53〕）。

**不变量 2 · 回执指纹**。`cache_state.receipt_fingerprint` 现状 = timeline 字节 + 源 stat 身份 + gfx html 字节 + 声明依赖内容〔代码现状 cache_state.py:70-87〕。v2 **必须掺入「生效插件清单」**（插件名 + 版本 + 对本 timeline 生效的参数摘要 **+ 插件代码内容摘要**——红队终审 P1-1 裁定 2026-09-14：官方插件改 DEFAULTS 不 bump version、无键 external 改代码两类盲区实测使 digest 不变，掺代码摘要后「防插件升级/改默认参/改代码后旧回执放行陈旧产物」的宣称成立）。混入位置在现有哈希序末尾追加一段，不改变既有成分。**空生效清单时省略该段**〔总控裁定 2026-09-13，P1 影子基线温态复验的前提〕：无插件时间线的指纹与 v1 逐字节一致、v1 回执在 v2 下继续有效（迁移兼容）；不变量 ② 由"段的出现/内容变化"保证——装/卸/升级/改参任一状态翻转都使录制侧指纹与重算侧失配（含卸载后回落 v1 形态：带段录制的旧回执对无段重算同样失配被拒）。〔P2b 落地注：「对本 timeline 生效」= installed+enabled 且 timeline 声明了该插件任一注册键（hooks.active_plugins 的 c 过滤）——无插件键时间线生效清单恒空，省略段的 v1 兼容由此保证。〕

**不变量 3 · 安装态与键注册表**。

- **安装态**判定 = `plugins/<name>/` 目录存在 且 `plugins/registry.json` 静态注册表未禁用。
- `registry.json` 静态登记**全量官方插件**的键声明（manifest 的 schema_keys 汇总），同时是「未装插件报错文案」的数据源（报错能给出安装指引而非裸 Unknown key）。
- **validate 三分支语义**（插件键出现在 timeline 时）：
  1. 已注册 + 已安装启用 = 合法生效；
  2. 已注册 + 未安装 = **报错 + 安装提示**；
  3. 已安装 + 被禁用 = **报错 + 启用提示**——绝不静默忽略。「缺省关闭」指不写键无行为，**不是写了键可跳过**；
  4. 完全未知键 = **警告不阻断**（新增行为；现状 validate 对未知键完全静默〔代码现状 stages.py:30-89 无未知键检查〕）。
- `_meta` 为**永久自由命名空间白名单**：插件/脚本标记（现状已有 `_meta.bed_static_duck`〔bed_static_duck.py:112-118〕、`_meta.audio_report`〔audio_report.py:146〕）不触发上述分支。

### 2.2 两处钩子缝（v1 仅音频侧）

**缝 A · prepare 源产物钩子**（denoise 型）：

- timeline `sources[]` 级键（如 `"denoise": {...}`），一源一产物进 `cache/audio/`（内容寻址，键见不变量 1）。
- **源产物提取收敛为单一实现**：prepare 的逐源提取（stages.py:197-212）与区间渲染冷填充 `_ensure_audio_wavs`（range.py:105-119，现状为复刻的裸提取命令）合并为同一函数、同一缝触发〔对抗审查 P0 复审 P1-5 裁定〕——否则区间预览将绕过降噪裸抽 wav。声明了键的源先出降噪 wav，后续 `_audio_graph` 读取点（stages.py:235）用降噪产物而非裸提取。
- **环境缺失 fail-hard**（权重不在/conda env 缺 → 阶段失败退出，不回落裸音频——回落=产物标注降噪实未降噪，不可信）。环境**预检通道 = doctor 组件段 health 探针**（组件级早发现）；渲染期 fail-hard 兜底在 prepare 源产物钩子内〔P2 审查 P2-3 调和本条与 v1.1 小注的矛盾：validate 期探测 conda/权重对无键时间线是纯开销，denoise 现 hooks=[prepare_source] 不挂 validate 预检、接受 prepare 期失败；需要时可经 v1.1 validate 钩子按需补挂〕。**读取点清单**：主链 `_audio_graph`、prepare 产出侧、`audio_report._collect_inputs`（P2 审查 P1-1 修复——报告的轨级 stem 渲染同走 `resolve_source_wav`，裸键读取即插件时间线报告必挂）三处一律同解析。
- **契约 v1.1（P2b 落地细化）**：钩子契约为 `prepare_source(c, sid) -> Path | None`——插件**自算**内容寻址产物路径（形如 `cache/audio/{audio_key}::denoise:{version}:{参数摘要}.wav`；audio_key 经内核 API 面 `vtx.api.audio_key` 取得，插件禁捅内部模块）并保证产物在盘后返回（缓存命中直返/缺失产出/环境缺失打印缺什么 + exit 1）。v1 草案的 `(c, sid, wav) -> bool`（wav = 调用方裸提取键路径）会让降噪产物写进裸键——裸键名与内容不符即内容寻址破坏，已废弃。**读取点同规**：`_audio_graph` 的事件源 wav 经 `hooks.resolve_source_wav` 与 source_audio_extraction 产出侧同一解析（逐源 memo），prepare 与 graph 两处恒一致；环境预检现由 doctor health 探针承担（validate 期环境预检可经 v1.1 validate 钩子按需补挂，本轮 denoise 只注册 prepare_source）。

**缝 B · 音轨事件内存变换钩子**（duck-static 型）：

- 轨级键 `duck_strategy: {"mode": "static", ...}`（对象参数缺省化，缺省对齐 bed_static_duck CLI 默认：global_db -8 / duck_db -4 / voice_track "voice" / merge_gap 20 / attack 5 / release 8〔P2b 落地注：冻结稿的裸串 `"static"` 值形状细化为对象——缺省化后与 CLI 默认逐项对齐，语义不变面更窄〕），与既有 `duck: true`（sidechaincompress 动态避让〔代码现状 stages.py:268-307〕）**互斥校验**（两键同轨 = validate 报错）。
- **validate 钩子（协议 v1.1 增补，P2b 落地）**：新增扩展点 `validate`——validate 阶段在 `_scan_plugin_keys` 后调用 `hooks.run_validate(c) -> list[str]`（错误行并入 errs 阻断；插件暴露 `validate(c) -> list[str]`，空 = 通过）。用途：① duck:true 与 duck_strategy 同轨互斥；② `_meta.bed_static_duck` 存在即报错（防现役 timeline 已用脚本烘焙过静态避让再挂插件键**双施**——提示先从 pre-duck 备份恢复）；③ 键值形状（对象 / mode 值域 / 数值参数类型 / voice_track 轨存在）早失败。仅对三分支放行后的生效插件调用，未装/禁用分支不重复报。
- 钩子 = **纯内存 events 列表变换**：在 `_audio_graph` 构建 filtergraph 前对全量 tracks 生效，**不写回 timeline**（幂等天然成立）；变换逻辑与 bed_static_duck.py 现实现一致（全局增益×口播窗内额外衰减×切段 src_in 连续〔代码现状 bed_static_duck.py:93-109〕——P2b 起两者共用同一 transform_events 纯函数，脚本烘焙与管线渲染永不分叉）。区间渲染（`--range`）走同一 `_audio_graph` 前缝，窗口模式自动正确，无需特判。

### 2.3 manifest schema v1

```json
{
  "name": "^[a-z][a-z0-9-]*$（与目录名一致）",
  "version": "语义化版本串",
  "kind": "app | plugin",
  "entry": { "cli": ["相对路径或空数组（bgm 类纯路由声明 app）"] },
  "health": { "type": "command | config_key", "...": "探测命令/键名" },
  "requires": { "core": ">=版本", "binaries": [], "python_packages": [], "platforms": [] },
  "config_keys": [],
  "docs": "README 相对路径"
}
```

plugin 额外：

```json
{ "extends": { "schema_keys": [{ "path": "json_pointer 风格定位", "key": "键名" }],
               "hooks": ["prepare_source", "audio_events", "validate"] } }
```

（hooks 值域 = 已冻结扩展点：缝 A `prepare_source` / 缝 B `audio_events` / v1.1 `validate`〔P2b 增补〕，见 §2.2。）

规则〔用户拍板〕：

- manifest 只**静态登记** kind + schema_keys（registry.json 的数据源）；**hooks 由代码注册为单一事实源**，加载时与 manifest 声明做一致性校验（声明了没注册 / 注册了没声明都报错）。
- 插件加载走**包导入**（`importlib.import_module`），**禁 `spec_from_file_location`**（绕过包语义，是双模块裂分的另一入口）。

### 2.4 安全节

- external 插件/应用注册**默认 disabled**（config 的 `external_plugins[]`/`external_apps[]` 条目 `enabled: true` 才生效——路径含 `..` 一律拒绝；官方内置件的启停走 `plugins/registry.json`）〔P4 审查 P2-6 调和：实现即 config 条目开关，原文"registry.json 显式 enable"为设计稿笔误〕。
- health 探测命令必须为**插件根内相对路径**，拒绝对路径与 `..`（防 health 探针被指向任意可执行）。
- 外部路径 resolve 后做**白名单根校验**（插件目录之外的任何路径不加载）。
- external 模块导入锚 = 父目录 **append** 进 sys.path + 导入后即摘除 + `mod.__file__` 落点断言（防 external 父目录下同名模块顶替官方插件/stdlib——P4 审查 P1-1 裁定）。
- **无键声明插件 = 全局生效语义**（红队终审登记）：hooks 声明但无 schema_keys 的插件（如纯 audio_events 型）对**所有** timeline 生效且无键位门——安装即信任；其代码变更由代码内容摘要兜底失效（上一条）。external 默认 disabled 是信任边界。
- 官方声明（registry.json）的插件实体**只认 `plugins/<name>/`**——目录缺失即 not_installed（安装破损），不被同名 external 顶替身份（P4 审查 P2-2 裁定）。
- 已知限制：external 插件 manifest 的 `cache_dirs` 声明暂无生效通道（get_cache_dirs 只读静态注册表；external 插件缓存键走 contribute_keys 的 ak 集不受影响）〔P4 审查 P2-9 记录〕。

---

## 3. 应用（apps/，12 件）

### 3.1 清单与自包含纪律

12 件：**asr、tts、imgen、matting（含 resolve_matte）、aroll、cover、research、blender-bridge、publish、bgm、gates、sync**。

- matting 迁移时顺带参数化：resolve_matte 现有 12 个 .py 含硬编码绝对路径（本机 home/Resolve 路径），统一改为 HERE 相对 / 环境变量 / CLI 参数三通道〔用户拍板；硬编码清单实测 card_strip/step2-6/prep/probe/mouse_ctl/wait_tracking_done/vram_guard/resolve_ctl〕。
- bgm 为**路由声明型** app：manifest `entry.cli` 可空，职责是把 BGM 需求路由到姊妹仓 music_tools（分轨审计包交付门等知识链接），不含可执行本体。
- gates：G3 TP 判定已上内核（§1.3），app 保留 G1/G2/G7/G10/项目门的发布前复检职责〔对抗审查裁定 #2〕。
- sync（sync_audio + repack_voice）标注「**管线前置素材工具**」子类：不进管线，但须幂等 + 备份纪律（repack 替换 voice 轨源、sync 重采样 conform 都是不可逆写操作的近邻）。
- 每件 app 自包含：`app.json`（manifest schema §2.3）+ 代码 + README + tests 随目录走〔用户拍板〕。

### 3.2 逐件边界表（关停测试落表）

**内核组成模块**（内核的组成部分而非可关停组件，不适用关停测试；列出以界定边界——渲染链/validate/qc/limiter/缓存直接构成产物可信度，diff/subs/snapshot/gc/new_project 为配套操作面，pluginlib/api.py 为协议运行时与对外契约面，关停它们等于功能缺失而非"降级仍可信"〔对抗审查 P0 复审 P2-1 裁定〕）：

| 组件 | 关停后果 | 归类理由 |
|---|---|---|
| validate | 契约无人把关，切点越界/重叠直进渲染 | 产物可信度构成 |
| audit | 编排体检消失（建议性但影响交付判断） | 内核时间线操作〔代码现状 stages.py:92-193〕 |
| gfx（CDP 渲染） | kind=gfx 轨全部无法渲染 | 渲染链构成 |
| prepare | 无源音频，audio/mux 依赖门必挂 | 渲染链构成 |
| video | 无画面 | 渲染链构成 |
| audio（混音+响度） | 无声音/响度不可控 | 渲染链构成 |
| mux | 无封装产物 | 渲染链构成 |
| qc（含 TP 判定） | 质检门消失，final.mp4 可信度无从谈起 | 产物可信度构成 |
| limiter（build_audio 尾部） | TP 失控，默认出片必挂 TP 门 | 对抗审查裁定 #1 |
| diff | 迭代回路（影响面/推荐路径）断裂 | 内核时间线操作 |
| subs | 字幕检查/预览/渲染消失，烧录链受损 | 内核时间线操作 |
| snapshot | timeline 无回滚锚点 | 内核时间线操作 |
| gc/df/缓存 | 盘膨胀失控、键集卫生失效 | 渲染基建（budget_gate 依赖） |
| new_project | 无脚手架（可手建，属可用性而非可信度） | 内核工具，随包分发 |
| pluginlib | 无插件加载，但**仅内核仍可出片**（这正是设计目标） | 协议运行时，关停=插件不可用而非主线不可用 |
| api.py | 外部组件失去稳定入口，退化各自 sys.path hack | 内核对外契约面 |

**应用件**（全部「关停仍能可信出一期片」→ 可卸载）：

| 组件 | 关停后果 | 归类理由 |
|---|---|---|
| asr | 字幕换人工/其他来源听写 | 产出独立素材，不进管线 |
| tts | 口播占位换真人录音 | 同上 |
| imgen | 背景图换实拍/手工素材 | 同上 |
| matting（含 resolve_matte） | 放弃抠像镜头，改原生背景拍摄 | 同上 |
| aroll | 素材预处理退回手工（Audacity 手导出/手切） | 同上 |
| cover | 封面手工合成 | 同上 |
| research（6 件采集/评分/样式研究） | 调研退回人工浏览 | 独立素材产出 |
| blender-bridge | 失去 Blender 审片预览与分轨审计包（体验降级，非可信度） | 消费 timeline 但产物不回写主线 |
| publish（export_4k/check_4k/compare） | 4K 投稿版退回 1080p 母带直投 | 发布增强 |
| bgm | BGM 需求自行路由 music_tools | 纯路由声明 |
| gates | 发布前内容复检消失（内核 qc/TP 仍在） | 复检非构成 |
| sync | 双系统同步/实录替换退回手工对齐 | 管线前置素材工具 |

---

## 4. 工作流包（skills/）

### 4.1 tech-talk

```
skills/tech-talk/
├── skill.json     # {name, version, uses_plugins[], optional_plugins[], docs, assets}
├── SKILL.md       # 无 frontmatter（挂载层负责元数据，本体纯文档）
├── references/    # 从 skill/references/ 迁入的产线知识（§8）
└── assets/
```

uses 声明按**产线用法定**（不是最小依赖）：`uses_apps: asr, tts, imgen, matting, gates, publish, sync, blender-bridge, bgm` 级别全量声明 + `uses_plugins` / `optional_plugins` 按实际挂接（denoise 是否 uses 待产线定案后回填，初版进 optional_plugins）〔用户拍板〕。

### 4.2 examples/（纪律范本）

- **hello-app**：最小 app 结构范本（manifest/health/README/tests 自包含）。
- **skill-demo**：虚拟歌手搞笑视频工作流包骨架（零 apps/plugins 依赖的纯内核包）——
  兼验收门 4 关停测试道具（gen_fixture.sh/gfxA.html/timeline.json/expected_check.sh 四件，
  expected_check 以 `qc --strict` 断言全检含 TP 项）。
- **hello-plugin**（音频插件完整纪律范本）：演示装/未装/禁用**三分支**报错文案（§2.1 不变量 3）、`contribute_keys` 缓存键贡献（§2.1 不变量 1）、环境缺失 fail-hard（§2.2 缝 A）、**卸载后缓存失效**（键出白名单 → gc 回收，验收门 6 的对照实现）。

examples 本身走 external 注册路径安装（默认 disabled，§2.4）——范本即安全节的活体测试。

---

## 5. 入口与分发

### 5.1 vt.py（统一入口，吸收 init.py）

吸收 `init.py` 现有七小节依赖自检（基础/编码/可选/程序/大模型 + 3b/4b 细分〔代码现状 init.py 实测结构，P0 复审 P2-4 勘误〕）。原 init.py 经 `spec_from_file_location` 复用 asr/client.py、imgen/secrets.py 探测逻辑的做法**废除**——doctor 对 apps 的健康探测一律走 manifest `health.command` 子进程调用（各 app CLI 自带探针子命令），零跨目录 import、零 spec 加载〔对抗审查 P0 复审 P1-2 裁定〕。子进程一律 `shutil.which` / `env python3` 拼 Python 路径，**禁 `sys.executable`**（本机 AppImage argv[0] 劫持：sys.executable 指向错误解释器〔用户全局环境注意事项〕）。

子命令：

- `doctor [--json|--fix]` 三检：
  1. **版本**：本地 HEAD vs origin/HEAD 漂移；`git fetch` 5s 超时；`VTX_DOCTOR_OFFLINE=1` 跳过；报 clean/dirty/ahead/behind 四态；VERSION 文件仅发布标识，**无 tag 不告警**。
  2. **apps+plugins**：manifest 有效 + health 探测 + requires 依赖检查；**enabled 才 FAIL 门**（缺失/disabled 仅提示）；附「`import vtx` 实际解析路径」自检（双模块裂分的常驻哨兵）。
  3. **skills**：包完好 + uses 声明齐备 + 挂载链接有效 + 内核 SKILL.md 索引新鲜度。
- `apps|plugins|skills list|check`：分层清单与单项体检。
- `version` / `init`（原 init.py 语义；注意 **init 的 `--fix` 与 doctor 的 `--fix` 语义不同**：前者安装缺失基础依赖，后者修复三检中可自动修复项（链接/注册表），不得混用）/ `--fix-links`（重建挂载符号链接）。

### 5.2 pyproject.toml

- 包名 `video-tools`；console scripts：`vtx`（渲染 CLI）与 `vt`（§5.1 入口）。
- **pip 包只含内核**（vtx/ + pluginlib + scripts/ 内核工具 + 内核文档）；apps/plugins/skills/examples 不进发行包。
- **clone 即用与 pip 安装并存**：repo 根可直接 `python3 vt.py` / `python3 -m vtx`（不依赖安装步骤）；pip 安装提供全局命令与稳定 import 路径。两路径必须同码同义（验收门 8 的 import<1s 顺带守护包导入健康）。

### 5.3 config 统一

收敛到 `vtx/pluginlib/config.py`：

- `VTX_CONFIG` 环境变量覆盖配置路径（对齐 imgen 现有 `IMGEN_CONFIG` 语义〔代码现状 imgen_cli.py:111〕，IMGEN_CONFIG 保留读取并打**废弃公告**）。
- 行为差异统一裁定〔对抗审查 P0 复审 P2-3〕：坏 JSON **警告全局化**（任何组件读 config 遇坏 JSON 一律警告不阻断、按空配置继续——asr 现行为〔代码现状 asr/client.py:88-96〕推广，init.py 现状的静默 {}〔init.py:134-142〕与 tts/imgen 的各自行为收敛到此单一语义）；`~` 展开全局化（config 值路径一律支持 `~/`〔代码现状 config.example.json tts.repo 等键即 ~ 路径〕）；imgen 的 `IMGEN_CONFIG` 保留读取并打废弃公告。
- 旧键 `comfyui/asr/tts/imgen/denoise`〔代码现状 config.example.json〕**兼容读**（v2 不改键名，只统一加载入口）。

---

## 6. 兼容层与提交纪律

### 6.1 symlink 兼容层

六目录 + 迁走脚本**原位置留相对 symlink**：`asr → ../apps/asr`、`tts → ../apps/tts`、`imgen → ../apps/imgen`、`matting → ../apps/matting`、`aroll → ../apps/aroll`、`blender_bridge → ../apps/blender-bridge`；scripts/ 下迁走件同理（`scripts/content_gates.py → ../apps/gates/content_gates.py` 等）。live 挂载（`~/.agents/skills/video/` 五项、`_library/blender_bridge/`）在过渡期继续经旧路径解析成功〔代码现状挂载清单 skill/SKILL.md:108〕。

### 6.2 定位改写

- `scripts/content_gates.py`、`export_4k.py`、`gfx_golden.py`、`post_loudness.py` 现用 `resolve().parents[1] / "vtx"` sys.path 注入〔代码现状 content_gates.py:49、post_loudness.py:4 等〕→ 全部改 `from vtx import`。
- `tests/` 全部测试文件的 sys.path 注入（12+ 处）与平铺 import 随 P1 包化改写为 `from vtx import`〔对抗审查 P0 复审 P1-2〕。
- `blender_bridge/audit_bundle.py` 的 vtx 定位链（VTX_VTX_PY > blender_bridge 上级 vtx/vtx.py > 技能路径〔代码现状 audit_bundle.py:303-310〕）随目录迁移**重算**：上级由仓根变 apps/blender-bridge/，须改为 repo 根锚定。

### 6.3 外部三载体核销清单（文件级）

1. `music_tools/skill/SKILL.md:35`：audit_bundle 路径引用〔代码现状已核：`~/文档/01_活跃项目/video_tools/blender_bridge/audit_bundle.py`〕。
2. `~/文档/04_bilibili视频/AGENTS.md`：video_tools 目录表多处（行 11/21-26/40 等：scripts 15 件清单、blender_bridge、skill 五链接）。
3. `~/文档/04_bilibili视频/_library/blender_bridge/`：六条符号链接（audit_bundle/blend2json/json2blend/subs_alpha/verify_blend/README）目标为旧路径〔P0 复审 P2-2 勘误：实测六条非三条〕。

核销 = 逐文件改为新路径（symlink 兼容层兜底期间也要改，兼容层是给未知消费者的，已知载体直接收敛）。

### 6.4 一组件一提交纪律

每迁移一个组件一个提交，提交内完成：

1. `git mv` 至新位置 + manifest（app.json / 并入 registry.json）；
2. 定位改写（§6.2 该组件相关的全部 `from vtx import` 化）；
3. symlink 建立；
4. **.gitignore 同提交改锚定**（§7.2 对应行）；
5. 旧路径 `--help` 冒烟（symlink 解析 + CLI 命令面在）；
6. `git status --ignored` 干净门（无新暴露的未忽略产物/无漏改锚定的忽略规则）。

CLI 命令面保留承诺：`post_loudness.sh` / `bed_static_duck.py` / `content_gates.py` / `denoise_voice.py` 四件的参数与用法串不变（薄壳/迁移不改接口；bed_static_duck 作为插件后其 `_meta` 标记内的 `script` 字段〔代码现状 bed_static_duck.py:116〕随新路径更新）。

---

## 7. 迁移映射表

### 7.1 路径映射（旧 → 新）

| # | 旧路径 | 新路径 |
|---|---|---|
| 1 | `asr/` | `apps/asr/` |
| 2 | `tts/` | `apps/tts/` |
| 3 | `imgen/` | `apps/imgen/` |
| 4 | `matting/` | `apps/matting/`（含 resolve_matte；硬编码参数化 §3.1） |
| 5 | `aroll/` | `apps/aroll/` |
| 6 | `blender_bridge/` | `apps/blender-bridge/` |
| 7 | `scripts/make_cover.py` | `apps/cover/` |
| 8 | `scripts/bilibili.py` `github_repos.py` `score_topic.py` `fetch-site-assets.py` `dump-computed-styles.mjs` `scroll-shoot.mjs`（6 件） | `apps/research/` |
| 9 | `scripts/export_4k.py` `check_4k.sh` `compare_4k_quality.py` | `apps/publish/` |
| 10 | `scripts/content_gates.py` | `apps/gates/` |
| 11 | `scripts/sync_audio.py` `repack_voice.py` | `apps/sync/` |
| 12 | `scripts/denoise_voice.py` `denoise_init.sh` | `plugins/denoise/` |
| 13 | `scripts/bed_static_duck.py` | `plugins/duck-static/` |
| 14 | `scripts/post_loudness.py` + `post_loudness.sh` | **内核吸收**（§1.2）+ 原位薄壳 |
| 15 | `vtx/models/*.rnnn` | **删除**（死资产，§1.1） |
| 16 | `init.py` / `init.sh` | `vt.py` 吸收（§5.1）；init.sh 留薄壳 |

残留 `scripts/`：`new_project.sh`、`gfx_golden.py`、`bench_cold.sh`（内核工具）。

### 7.2 .gitignore 影响清单

| 现规则 | 迁移动作 |
|---|---|
| `matting/MatAnyone2/`、`matting/BiRefNet_HR/`、`matting/__pycache__/` | 改锚 `apps/matting/...` |
| `matting/resolve_matte/*.log`、`matting/resolve_matte/uinject` | 改锚 `apps/matting/resolve_matte/...` |
| `asr/.venv/` `tts/.venv/` `imgen/.venv/` | 改锚 `apps/*/.venv/`（或收宽为 `apps/*/.venv/` 通配） |
| `!asr/tests/fixtures/**` `!tts/...` `!imgen/...`（fixtures 反排除） | 随目录改锚 `apps/*/tests/fixtures/**`（全局 `*.wav/*.png` 规则不变，反排除必须同步否则测试资产被忽略） |

每条随对应组件提交同改（§6.4 第 4 步），`git status --ignored` 干净门验证。

---

## 8. 文档双层映射表（知识守恒）

原则：**内核文档**（机制/契约/基准，任何产线通用）留 skill/ 与 docs/；**产线知识**（tech-talk 这条流水线的配方/规范/模板）进 skills/tech-talk/。逐件：

| 文档/知识 | 去向 |
|---|---|
| README.md | **内核留**，瘦身（目录表改为 v2 形态，apps/plugins/skills 各一句话路由） |
| skill/references/edit.md | **内核留**（timeline 契约 + vtx 全阶段 + 渲染坑表 + **5.5 字/s 基准值**〔代码现状 stages.py:152-160、edit.md:54〕+ 缓存治理） |
| skill/references/gfx.md | **内核留**（gfx 帧驱动协议 + **D22 权威定义**——禁深蓝/蓝紫渐变科技风、暖色基线〔代码现状 cover.md:36、gfx-templates 内 D22 token〕） |
| skill/references/runbook.md R1-R4 + R2s + 棒次协议 + R3 表 | **内核留**（含 R3 表加单机口径声明）；R4 按 §1.2 简化重写 |
| runbook R5/R6/R7（发布链/BGM 挂载/封面） | **tech-talk 收**（产线配方：4K 投稿、BGM 审计、封面制作各依赖具体 app 组合） |
| skill/references/research.md（R0-R6 编排） | **tech-talk 收** |
| skill/references/cover.md | **tech-talk 收** |
| 拍摄规范（voice-ime/07-shooting-guide.md 引用） | **tech-talk 收** |
| voice-ime 范式（实测事故值/编排方法论引用） | **tech-talk 收** |
| skill/assets/gfx-templates/（七件模板）+ gfx-examples/ + `_tokens.md` | **tech-talk 收**（产线已验收视觉资产〔用户拍板〕；`_tokens.md` 为 D22 调色板 token，**D22 定义**仍内核 gfx.md，tech-talk 反向引用） |
| skill/assets/title-card.html / lower-third.html | **内核留** skill/assets/（通用起手模板，与产线无关） |
| config.example.json / config.local.json | **根目录不动**（内核分发件；config.local.json 保持 gitignored） |
| brief 模板（skill/assets/brief-template.md） | **tech-talk 收** |
| skill/references/matting.md | **apps/matting/ 收**（组件专属全流程）+ 内核 skill 路由表留一行指路 |
| docs/DEV.md、cache-provenance.md、canvas-export.md、audio-audit.md、post-loudness.md | **内核 docs/ 留**（post-loudness.md 随 §1.2 合并重写为「响度终链」章节） |

内核 skill/SKILL.md 改为 v2 路由索引（doctor/分层结构），tech-talk 的 SKILL.md 承接现行「六阶段创作流程」使用者向内容。

---

## 9. 验收硬门（9 条，全部通过才算 v2 完成）

1. **全测试绿**：tests/ 全量通过（test_post_loudness.py 随 §1.2 改为直测内核函数，spec_from_file_location 加载脚本路径的旧加载方式〔代码现状 tests/test_post_loudness.py:7-9〕随薄壳重写）。
2. **影子基线归一化一致（两段口径）**〔对抗审查 P0 复审 P0-1/P1-4 裁定〕：**不变段**（validate / diff / subs 全部输出 + qc 除新增 TP 项外的全部 checks）——P1/P2/P3/P5 每次复验与首跑基线（r1）逐项一致；**重锚段**（audio --report 全部输出 + qc 含 TP 项）——因 §1.2/§1.3 响度终链合并属设计内变化，P1 落地后已在影子复跑产出**重锚基线 r2**（`plans-v2/baseline-r2/`，合法性实测：解码域 TP -3.1 ≤ -1.0 ✅、整片 I -15.6 ∈ **[-15.8, -14.0]**〔合法带按实证修正：v1 交付 -15.5/v2 实测 -15.6 的边界内容 + 0.1 级域差，原 [-15.5,-14] 不可满足，超出修正带即真回归〕、判级不劣于首跑已知项 ✅），此后 P2/P3/P5 对 r2 逐项一致（`compare.sh <新目录> ../baseline-r2`）。预期差异经 `known-diffs.conf` 白名单承载（r2 建立后已清空），**白名单外任何差异即 FAIL**。
3. **旧路径冒烟**：docs_smoke 提取全部旧路径引用逐一验证 + **外部三载体**（§6.3）核销后可解析。
4. **关停测试**：apps+plugins 全禁 → 仅内核仍可出片（`all` 走通 + qc 含 TP 判定通过）——§0.1 判据的实测化。
5. **语法差异门 + 缓存身份门**：插件装/未装 timeline JSON 语法差异符合三分支语义（§2.1 不变量 3）；插件产物缓存键进 gc 白名单（渲染中 budget_gate 触发不误杀，§2.1 不变量 1）；插件升级/改参 → 回执指纹变（旧回执不放行陈旧产物，§2.1 不变量 2）。
6. **examples 外部注册**：hello-app/hello-plugin 经 external 注册路径（默认 disabled → 显式 enable）装/卸全流程可复现，卸载后缓存失效符合预期。
7. **三 grep 门**：(a) 活代码（py/sh/mjs/json，白名单显式豁免 docs/plans* 历史归档）零 `/home/liangruochong` 本机硬编码；(b) 旧路径形态消解——按 §7.1 映射表 grep 旧形态（`blender_bridge/`、`matting/`、`scripts/make_cover` 等）在现行代码/文档中逐条消解（兼容 symlink 除外）；(c) import 卫生——**运行时管线代码与 tests** 零 `sys.path.insert` 注入 repo/vtx、零 `spec_from_file_location`（§1.4/§2.3 的静态验证；vt.py 走子进程探测不涉 spec）。
8. **bench ±10% + import <1s**：`bench_cold.sh` 三轮中位相对迁移前基线（291.4s 口径〔代码现状 docs/DEV.md 性能基准节〕）偏差 ≤±10%（bench_cold.sh 随 §1.2 链结构变化改写后重定口径——291.4s 为 v1 含独立 post_loudness 步骤的口径，v2 合并后以 ±10% 容差吸收结构性差异〔P0 复审 P1-3〕）；`python3 -c "import vtx"` 冷导入 <1s。
9. **红队通过**：对本文件 §2 协议的对抗审查（三分支绕过/缓存键伪造/回执指纹遗漏/安全节路径逃逸四类攻击面）全部未突破。

---

## 附：实现顺序依赖（非冻结内容，供排期参考）

§1.4 包化 → §1.2/1.3 响度终链与 TP 统一 → §2 协议三不变量与 pluginlib → §2.2 两缝 + plugins/denoise、duck-static → §3 apps 逐件迁移（§6.4 纪律）→ §4 skills/examples → §5 入口分发 → §6.3 外部核销 → §9 验收门逐条闭合。每阶段结束跑既有回归（tests/ + 影子基线）。
