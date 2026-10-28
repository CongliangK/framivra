# 时间线 API 与事务内核

本页是只读接口、首切片写事务与写入口迁移清单的唯一 home。结构权威仍为 [timeline schema](../../vtx/timeline.schema.json)，渲染校验仍归 [validate](../../vtx/pipeline/stages.py)。inspect/query 的 `ok` **仅表示可安全查询的数据形状**；reference 另按下述引用合同检查身份、选择与精度，均不表示 schema 合规、素材可用、插件已安装或可渲染。

## 公共面与零写态

[公共 Python API](../../vtx/api.py) 导出：

```python
inspect_timeline(path)
query_timeline(path, *, family=None, track_index=None, block_index=None,
               frame=None, interval=None)
timeline_capabilities()
```

返回 JSON 可序列化 dict；只读取指定文件，不创建 Ctx、不迁移、不解析素材路径、不加载/执行插件、不读外部字幕、不写 cache/out/回执/锁/事件文件。可在缺失素材的工程或 wheel 安装环境使用。文件路径按调用者工作目录解释并展开 `~`。能力查询不要求工程存在。调用者仍需限制其自身允许读取的文件范围；这不是授权或宿主隔离边界。

以下命令只读；源码入口也可用 `env python3 -m vtx` 替换已安装的 `vtx`：

```bash
vtx capabilities
vtx -t /path/to/timeline.json inspect
vtx -t /path/to/timeline.json query --family video --track-index 0 --block-index 1
vtx -t /path/to/timeline.json query --frame 30
vtx -t /path/to/timeline.json query --interval 30 60
```

`--dry-run` 对以上入口不改变结果。渲染/写态旗标（例如 `--draft`、`--events-jsonl`、`--json`）不支持；不会悄悄创建报告。

## Envelope 与错误

所有结果均有 `schema: "framivra.timeline.read/v1"`、`operation`、`ok`、`content_version`、`diagnostics`。`content_version` 为 `sha256:` 加**本次读入的完整原文件字节**的 64 位小写十六进制摘要；不是规范化 JSON、素材、缓存或全工程身份，不保证文件在返回后未改变。无法读入时为 null；可读但解析失败仍有摘要。

inspect 成功示例（摘要用占位值表示）：

```json
{
  "schema": "framivra.timeline.read/v1", "operation": "inspect", "ok": true,
  "content_version": "sha256:<64hex>", "diagnostics": [],
  "document": {"version": 2, "project": {"fps": 30, "duration_frames": 60},
               "sources": [], "video_tracks": [], "audio_tracks": [], "subtitles": []},
  "coverage": {"complete": true, "scope": "stored timeline blocks only", "unexpanded": []}
}
```

`document` 保留解析后的完整输入对象：所有已知/未知根键、扩展配置、轨与块属性、数组次序均不投影、不归一化。JSON 数字采用 Python 标准库 int/float 表示，不承诺保留数值词法、浮点任意精度或文件排版；本接口不写回输入。只接受 UTF-8 严格 JSON；重复键、NaN/Infinity、溢出为非有限浮点数的数字拒绝，避免静默覆盖键或非标准 JSON 混入。解析失败时 document 为 null；可解析但 shape 错误时仍返回原 document 和逐项诊断，query 拒绝查询该结构。

query 成功额外返回 `tracks` 与 `blocks`，不返回 document。元素例：

```json
{
  "tracks": [{"family": "video", "track_index": 0, "address": "/video_tracks/0",
              "data": {"name": "A", "kind": "aroll", "clips": [{"src": "s", "dst_in": 0, "dur": 10}]}}],
  "blocks": [{"family": "video", "track_index": 0, "block_index": 0,
              "address": "/video_tracks/0/clips/0", "range": [0, 10],
              "data": {"src": "s", "dst_in": 0, "dur": 10}}]
}
```

data 是完整原轨/块，不是渲染投影。轨 data 包含该轨所有块，**不可将轨 data 中的全部块当作命中集合**；命中集合只在 blocks。无匹配为 `ok:true`、空 blocks；无时间/块过滤时保留空轨。错误示例：

```json
{
  "schema": "framivra.timeline.read/v1", "operation": "query", "ok": false,
  "content_version": "sha256:<64hex>",
  "diagnostics": [{"code": "query.bounds", "address": "/query/frame",
                   "severity": "error", "message": "frame must be in [0,duration)"}],
  "tracks": [], "blocks": [],
  "coverage": {"complete": true, "scope": "stored timeline blocks only", "unexpanded": []}
}
```

诊断稳定定位字段为 code/address/severity；message 供人读，JSON 语法错误另带 line/column。文件错误为 input.read/input.json，形状错误为 shape.type/shape.frame 或 timeline.*，查询错误为 query.*。仅用户输入错误结构化；开发异常不捕获伪装成输入错误。CLI 输出与 API dict 解码后相等（JSON 使用 ASCII 转义以兼容任意字符串），成功 exit 0，输入/查询诊断 exit 1，不支持的参数组合 exit 2（cli.arguments）；argparse 层的语法错误仍为标准 stderr 文本与 exit 2，内部异常仍有 traceback。消费者先检查退出码，再解析完整 stdout，不依赖 message 文案。

capabilities 使用相同 envelope，content_version 为 null，另有 capabilities 对象，声明 operations、cli_exit_codes、fps、整数帧、边界、暂态寻址、只读与未展开能力；没有写操作。

## 查询语义

- family 为 video / audio / subtitles。video 按 dst_in 与 dur 计算；audio、字幕按 in/out。返回 range 均为全局 `[start,end)`。
- 所有时间和位置选择器为整数，拒绝 bool 与 float（1.0 也拒绝）；frame 要求 `0 <= frame < duration_frames`；interval 要求 `0 <= start < end <= duration_frames`，不裁夹。区间匹配采用相交，不要求包含；端点相触不命中。frame 与 interval 互斥，其余过滤取交集。
- track_index/block_index 均从零开始。track_index 要求 family；block_index 还要求 track_index。位置越界为 query.not_found，不伪装成空结果。重名轨可以按位置区分。
- 字幕用虚拟 track_index=0，轨 address=/subtitles、data 为原数组；块 address=/subtitles/N。外部字幕也保留空的虚拟轨，不展开文件。
- 排序固定为 video、audio、subtitles，各自保持原始轨/块数组次序，绝不按时间排序。address 是 JSON Pointer **暂态数组位置**，仅绑定本次 content_version，不是稳定 ID；插入、删除、重排后不得重用旧地址。
- shape 检查只覆盖版本 2、现有 fps 约束、工程时长、必要容器与块查询字段、块范围及若干整数时间字段；不检查素材、引用存在性、几何、完整 EDL、插件键、重叠、响度或全 schema。完整工程数据检查和渲染校验不可互相冒充。

每个 inspect/query 都返回 coverage。非空 subtitles_src 时：

```json
{"complete": false, "scope": "stored timeline blocks only", "unexpanded": [
  {"address": "/subtitles_src", "reference": "subs.srt", "reason": "external subtitle file is not read"}
]}
```

因此任意帧查询都**不是完整渲染活跃集合**；消费者必须展示未展开状态，不把无内联字幕误当无字幕。coverage.complete 只描述存储块是否需外部字幕展开，不表示输入合法、渲染已验收或素材完整。inline 与非空 external 同时存在拒绝查询。

## 原文引用 reference/v1（只读、无图）

`reference_timeline` 由同一[引用内核](../../vtx/timeline_reference.py)服务 Python 与公开 CLI：

```python
reference_timeline(path, *, track_ids=None, block_ids=None, track_addresses=None,
                   block_addresses=None, global_frame=None, interval=None,
                   expected_content_version=None)
```

```bash
vtx -t /path/to/timeline.json reference
vtx -t /path/to/timeline.json reference --frame 30
vtx -t /path/to/timeline.json reference --track-id TRACK_ID --block-id BLOCK_ID
vtx -t /path/to/timeline.json reference --block-address /video_tracks/0/clips/0 --expected-content-version sha256:EXACT_VERSION
```

ID/address 旗标可重复，用于多轨/多块。所有选择器的 Python 集合参数为字符串 list/tuple；帧与 interval 沿用整数帧及 `[start,end)` 边界，不接受 bool/float，不裁夹。frame 与 interval 互斥。`--dry-run` 不改变只读结果；渲染/输出文件旗标仍按 read 的 `cli.arguments`/exit2 拒绝。reference 错误使用相同 diagnostics/envelope/退出约定，但 schema 是 `framivra.timeline.reference/v1`、operation 是 `reference`；其他读接口的数值表示合同不变。

成功结果包含：

- `document: {state:"stored", raw, content_version, location, project}`。raw 是**严格 UTF-8 完整落盘文本**，包括首尾空白；不是解析对象的重序列化。content_version 与 inspect 相同，为本次完整原字节 SHA256。`stored` 只表示文件已落盘，不表示已受管、可渲染或已验收。
- `document.location: {path:<文件名>, relative_to:"timeline parent directory"}`，仅在该时间线父目录的工程相对定位，不是跨工作区唯一身份；workspaceId/宿主根定位由 host 附加，内核不虚构。
- `document.project: {kind:"managed"|"legacy", project_id:string|null, head:string|null}`。仅显式 `_vtx` 通过共享 store 的 protocol1、project_id/head、完整全局对象 ID 检查后为 managed。坏 metadata 不回退 legacy；此处不读/初始化 store 历史，不证明事务证据完整。legacy 无协议稳定 ID，即使恰有自定义 id 也不提供稳定复制；source_id 只是已存在的源引用。JSON 数值（包括超长整数）不会冒充字符串身份、源引用或其他字符串字段；带引号的纯数字 ID 仍按共享字符串 ID 规则校验，未知扩展数值仅由 raw 保留词法。
- `selection: {global_frame, fps, interval, tracks, blocks}`。fps 是落盘真实值并经过现有 fps 约束。轨/块各含 `family, stable_id, address, content_version, raw`；块另含全局 `range`。raw 是原 token span，保留完整未知嵌套字段、超53bit整数、高精度十进制词法、空白与 Unicode/其他转义；document.raw 始终是权威。span 不含该值外侧的分隔空白。不返回可能舍入的解析 data，不通过 JSON roundtrip 伪造原文。派生帧超过 `2**53-1` 明确 `reference.precision` 拒绝，未知扩展数字的原文不受该派生字段限制。
- 真实受管轨/块 stable_id 来自其原 ID；legacy 及字幕虚拟轨 stable_id 为 null，不把数组位置/pathhash 冒充 ID。字幕虚拟轨 address 为 `/subtitles`；external-only 且无内联数组时其 raw 为 null。
- 仅非 gfx 视频/音频块引用存在且唯一的 source 时给 `source_id`。本片不探源媒体，不能确证原生 fps/CFR、源时长与归一化，因此**不提供 source_frame**，也不用 video.currentTime 假装精确帧。
- 成功时 `coverage` 与 read 相同；输入 shape 校验失败时为 null，不回显未验证外引值。外部字幕不读取、不展开，`complete:false` 不声称完整活跃渲染集合。`image:{state:"not-generated",kind:null}` 明确本片未生成图；不渲染、不把旧输出或 UI 截图当 current-composite，未来须区分 current-composite/verified-output/ui-screenshot。

空选择仅引用整 document，tracks/blocks 为空；仅指定帧/区间时选择命中存储块。显式轨选择纳入该轨命中块；显式块选择纳入其所属轨；多个选择器取并集后保留 video/audio/subtitles 原始顺序。轨 raw 仍含全轨，不等于命中块集合。显式块不在窗口、显式轨窗口内无存储块为 `reference.conflict`；不存在为 `reference.not_found`，重复选择同一对象（含 ID/address 双重指定）为 `reference.duplicate`。重名轨或重复 source 身份为 `reference.ambiguous`，悬空源引用为 `reference.source`，不静默猜测。此检查仍不是完整 render/schema/EDL 校验。

**地址只在一个 content_version 中有效**：地址选择强制传 expected_content_version，缺失为 `reference.version_required`；任意请求传入版本与落盘原字节不一致为 `reference.stale`，不选择新位置上的另一个块。ID选择仅 managed 可用，legacy 为 `reference.unmanaged`，不自动 adopt。读取、原文截取、选择、版本均用同一次落盘 bytes 快照；文件返回后仍可变化，不承诺加锁或持续当前。重复 JSON 键等由现有严格 parser 拒绝；轻量 token span 只定位已验证文法，不建立第二解析/校验权威。

协议上限：输入完整 UTF-8 bytes 和整个 ASCII 转义 JSON wire（含结尾换行）各 **8MiB**；超限分别 `input.limit`/`output.limit`，绝不截断原文或返回部分成功。output.limit 时 document/selection/coverage 为 null；无全文摘要时 input.limit 的 content_version 为 null。内核与 CLI 使用相同 wire 预算，包含 raw 字符串转义后的膨胀。先按原文及所选 span 组合长度作安全下界早停，再逐字符精确计数（含 6/12 字节 Unicode 转义、键、容器、分隔符、标量及换行），计数饱和即拒绝；不会为了检查上限先生成超限 escaped JSON。span 仅以游标定位、预算通过后才复制，未知超长整数保留词法而不作大整数转换；inspect/query/store 的解析合同不变。这是有界分配机制，不是固定宿主 RSS 承诺或主性能基线。错误时 selection 为 null；输入/metadata 错误可能无 document。能力查询另有 reference 子项声明此 schema/上限/版本要求。全程不写 cache/out/store/锁/回执，不读取源媒体、插件或字幕外引。

## 显式写事务第一切片（不是完整编辑器）

**以下调用会写工程及持久历史**，没有 dry-run；先在副本核对请求，不自动迁移现有工程。公共 API 为 `timeline_transaction(path, request)`、`timeline_store_status(path, *, recover=False)`、`timeline_store_capabilities()`；CLI/Python 共用 [内核](../../vtx/timeline_store.py)，无需 Ctx、素材、渲染或插件。

```bash
vtx store capabilities
vtx -t /path/to/timeline.json store status
# 下列两条为实际写操作，不是预演：
vtx -t /path/to/timeline.json store --request /path/to/request.json
vtx -t /path/to/timeline.json store recover
```

request 为严格 JSON 对象，必需 `operation`、`op_id`、`base_revision`，拒绝未知请求键。`base_revision` 直接使用 inspect 的原字节 `content_version`，不使用 mtime。op_id/project_id/对象 ID 为 1–128 位、首位字母或数字、其余字母数字及 `_.:-` 的字符串；所有受管对象 ID 全局唯一。version 仍为 2，新增可选根 `_vtx: {protocol:1, project_id, head}`；未知未来协议拒写。管理对象为 sources、视频/音频轨、clip/event、内联字幕，不往自由 `_meta` 塞身份。

| operation | 额外请求字段 | 首切片限制 |
|---|---|---|
| create | document | base 为 null，create-only 拒现存；完整受管对象 ID 由调用者提供，document 不带 _vtx |
| adopt | 无 | 仅显式为旧工程所有非 source 对象分配 ID，sources.id 沿用；旧非 source 已有 ID（包括合法但不属本协议的 ID）、坏类型/重复一律拒绝，不覆盖；返回地址→新 ID 的 id_mapping |
| replace | document | 全文替换，须携带当前 _vtx，保留完整 ID/对象种类清单；禁止增删/改签 ID；调用者显式负责全文及扩展值 |
| apply | changes | 非空 `[{"id":"对象ID","set":{"text":"新字幕"}}]`；仅 capabilities 列出的本层字段，拒绝任意 JSON Pointer、身份及扩展路径 |
| undo | head | 只撤当前已提交 head，仍是带新 op_id/base CAS 的新提交；文件必须逐字节等于原 head 结果；不能 undo create/adopt |

没有命名的 move/copy/split、插入删除、ID 改签、轨重命名或 UI 操作；不得宣称完整编辑。replace 可保留 ID 重排既有对象，但不提供复制/切分语义或映射。apply/adopt 保留全部未知 JSON 数据，不投影/展开 `subtitles_src`，不读取/重写字幕外引；replace 的 document 是调用者显式提供的全文。写入允许重排键/格式，不承诺原排版；严格拒重复键、NaN/Infinity、bool 帧以及重序列化会损失十进制值的高精度浮点输入。

### 发布与恢复协议

同目录允许多个工程：按实际文件系统顺序解析父路径（包括 symlink 后的 `..`），再加最终文件名确定 canonical 目标与独立 `.vtx-store-<文件名SHA256>/`，新 manifest 绑定该文件名及 canonical 完整位置（已发布的旧版 filename-only manifest 仍可读、不自动改写）；项目叶 symlink/多硬链与非普通文件拒绝。历史不在 cache/out，renderer 只读当前工程，日志不提供另一份 HEAD；不要移动/删改历史、锁文件或只复制工程后期待 undo。status 可创建锁与 manifest，但不恢复/修改工程；inspect/query 始终零写态。

初始化先取同目标稳定的外置 `.bootstrap.lock`（仅同步发布，不存工程身份/HEAD，永不删除），在随机 sibling `.initializing-*` 私有目录内 write/flush/fsync manifest → rename manifest → fsync 私有目录 → rename 整个完整目录到 canonical history → fsync 工程父目录，然后才可执行事务。首次 mkdir、半写/fsync、两个 rename 与目录同步任一点进程崩溃，canonical 要么尚未发布，要么已有完整 manifest；下次 status/recover/事务在同一 bootstrap 锁内重试父目录同步。未发布私有目录不可能含运行过的事务，重试另建目录，不扫描选取、不采用也不删除其残件；其存在不阻止安全重新初始化。旧实现或外来 canonical 目录缺 manifest（即使空、仅 lock、完整单个 `.publish-*`、多个或损坏临时）一律 recovery_required 并提示人工检查，绝不猜身份/清证据。已发布新 manifest 的位置不符也拒绝，不能凭同 SHA 接管 decoy。旧版已发布 manifest 不具完整位置绑定，不宣称可鉴别其跨目录复制。初始化失败不返回成功、不改工程，发布后父 fsync 失败下次重试也不会永卡；这不是物理断电保证。

顺序固定：同一线程锁+跨进程 flock → 验证/恢复所有已发布 intent → 严格读 → op_id/request_digest 幂等 → 原字节 CAS → 内存操作及共享无素材结构校验 → before/after 完整字节、SHA、完整请求与结果 fsync 并原子发布 intent 目录 → 工程 staging fsync → 最终原字节 CAS → os.replace 工程（线性化点）与父目录 fsync → 持久 commit 结果。没有默认 JSON 回退，不调用 fstxn.locked_update，不嵌套重入锁。

恢复只补证据：current==after 时补 commit；==before 时记 abort，**不自动 roll-forward**，同 op 重试返回 aborted，欲再试须新 op_id；其他字节、缺失/坏证据或无法判定时 recovery_required，绝不覆盖。create 的 before=null 特例允许仍缺失的目标记 abort。已提交同 op 同 payload 返回原结果与当下 current_revision，不倒退后续编辑；不同 payload 返回 idempotency.conflict。提交替换开始后同步/标记失败为 outcome_unknown（带 op_id），必须相同请求重试或 recover，不得误称未提交。

成功 envelope 为 `schema: "framivra.timeline.store/v1"`、operation/ok、op_id/request_digest、base_revision/revision/project_id，首次与重试另带 current_revision/replayed；失败包含 code/message。CLI 成功 exit0，输入/CAS/恢复错误 exit1，不支持的旗标组合 exit2；拒绝 `--dry-run`、渲染旗标与 `--json` 输出文件。

[共享无素材校验](../../vtx/timeline_structure.py) 用于 store 写入及显式带 `_vtx` 的受管工程 validate，检查 schema、ID、源引用、整数帧/区间、EDL、内联/外引字幕冲突和轨重叠；`_vtx` 键存在即适用，坏值、缺字段或未知协议不回退 legacy。未受管工程保持原只读/validate 的 Unicode source ID、高精度扩展数值及精确诊断行为；写入的 strict/ID 限制不扩散到 legacy validate。adopt 不改已有 source.id 或引用：符合上述稳定 ID 规则才可接管，不符合的合法 legacy source ID 明确拒写并保留原字节，不自动重命名/迁移；高精度扩展（含超出 Decimal 指数范围的合法 JSON 数值）在 adopt/status/recover 返回 input.json 失败 envelope，拒写而非舍入，不影响 legacy 只读；此 adopt 是受限显式接管，不是全面兼容迁移。重复 source ID 在 validate 中不再静默覆盖。**提交不等于可渲染**：不探素材、不查实际源帧数、不载入插件，不代替正式 validate/all。保证仅为本地 POSIX 合作线程/进程与进程崩溃；最终 CAS 后仍可能被非合作外写竞态覆盖。Windows/NFS/物理断电未测，不作绝对保证。

## 生产写入口支持矩阵与迁移清单

以下为当前源码盘点，不是本切片已经实现的写能力。手工编辑/外部脚本无法穷举；仓内测试夹具、验收探针、冻结模板复制不算生产编辑器。

| 当前入口与源码证据 | 当前支持与风险 | 后续统一内核迁移面 |
|---|---|---|
| [Blender 回写](../../apps/blender-bridge/blend2json.py#L90-L193) | 视频移动/边裁/同族换轨后重排；音频 in/out/src_in 可回写，换音轨和部分 loop 移动拒收；字幕、音量、pan 不回写。重叠时**先写后 exit 2**，非事务覆盖 | 精确位置寻址、编辑计划、冲突/重叠预检、提交前拒绝；不得照抄过时文件头的“不回写音频” |
| [ASR 合并](../../apps/asr/tl_merge.py#L45-L85)，[调用者](../../apps/asr/asr_cli.py) | 只替换 subtitles；保留其他字段，支持 dry-run，cache/reports 备份旧字幕，同目录临时文件+replace。无原文版本 CAS；不协调 subtitles_src | 字幕集合替换、引用冲突与并发版本门；保留备份语义 |
| [静态 duck 烘焙](../../plugins/duck-static/bed_static_duck.py#L126-L168) | 按轨名查找后分段替换 events 与 _meta，参数幂等标记和可选备份；无版本门、非事务覆盖 | 事件批量变换、元数据扩展保留、原文版本冲突 |
| [通用脚手架](../../scripts/new_project.sh#L11-L35) | heredoc 整体写 timeline，可覆盖既有文件；创建 cache/out | 新工程创建与禁止无意覆盖 |
| [口播脚手架](../../templates/tech-talk/scripts/new_project.sh#L94-L115) | 已存在则不覆盖，递归改占位路径与 _meta，再写新文件 | 保留新建语义与原始模板扩展 |
| [MG build](../../templates/mg-styles/build.py#L16-L65) | 新目录限定，dry 返回工程，生成 gfx/音频后写 timeline | 新建工程与完整源/轨数据 |
| [MG media](../../templates/mg-styles/build_media.py#L111-L153) | 调用 build 后再替换视频轨/追加源，第二次覆盖 timeline | 新建过程的整批计划与失败边界 |
| [既有只读预览页](../../vtx/preview.html#L264-L281)（非写入口） | 文件/粘贴 JSON 读取展示，不写工程；自带页面数据检查不是渲染校验 | 后续统一读模型消费面，不虚构编辑/导出能力 |
| [只读 CLI](../../vtx/cli/timeline.py)、[只读模型](../../vtx/timeline.py)、[引用模型](../../vtx/timeline_reference.py) | inspect/query 为完整解析数据；reference 提供权威原文和带版本暂态地址；均零写态 | store 从原文与 content_version 起步；不得从会展开外部字幕的 Ctx.tl 回写 |

上述生产写入口当前**全部未迁移**，仍可能非合作直接写工程，不能宣称统一人/AI 编辑已完成。[fstxn](../../vtx/runtime/fstxn.py) 的 file_lock 由 store 单次复用，不用其默认损坏 JSON 回退/无 fsync 的 locked_update。此切片没有常驻服务、DSH 桥或消费 UI。

## 行为验证

[真实行为测试](../../tests/contracts/test_timeline_api.py) 覆盖完整扩展保留、缺素材/未知插件、输入字节身份、暂态地址/数组次序、交集与端点、严格 JSON、坏 shape 仍可检查、结构化诊断、外部字幕不完整标注、开发错误透出、CLI/Python 同值及无工程写副作用。[事务行为测试](../../tests/contracts/test_timeline_store.py) 在仓外临时项目覆盖双进程/线程同 base 恰一成功、重试不倒退、严格输入/ID/扩展/字幕外引、create-only、head undo、每阶段异常/SIGKILL、每次真实 write/fsync/rename/replace 调用 EIO、坏历史与源码 CLI/API 同值。[兼容回归](../../tests/contracts/test_timeline_store_compat.py) 补充相同 SHA 工程的绝对/相对 symlink/.. 寻址、跨路径双进程 CAS/恢复、坏别名拒绝，以及 legacy 读校验与 managed 严格拒绝的边界。执行：`env python3 -m unittest tests.contracts.test_timeline_api tests.contracts.test_timeline_store tests.contracts.test_timeline_store_compat -v`。[引用测试](../../tests/contracts/test_timeline_reference.py) 以真实临时文件/公开 CLI 覆盖严格原文与块 span、未知嵌套/大整数/高精度/转义、managed/legacy、ID与版本寻址、帧边界/fps、外部字幕、输入及 wire 上限与零写态；执行 `env python3 -m unittest tests.contracts.test_timeline_reference -v`。测试通过不等于渲染或整仓全量通过；发行包安装烟测另需在仓外运行。
