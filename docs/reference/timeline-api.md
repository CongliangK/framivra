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

## 显式当前帧图像（第一切片）

公共 API：`frame_timeline(path, *, frame, expected_content_version=None, render=False, max_png_bytes=8388608)`；CLI：

```bash
# 默认只引用并返回 render.not_authorized，不启动 renderer/ffprobe、不开锁、不建目录：
vtx -t /path/to/timeline.json timeline frame --frame 30
# 警告：以下实际写 cache/preview；需先独立准备当前 seg/gfx，不会自动准备：
vtx -t /path/to/timeline.json timeline frame --frame 30 --render
```

返回独立 `framivra.timeline.frame/v1` envelope：content_version、reference、image、diagnostics。reference 原样保留同版本完整原文/暂态地址/选择；图像不是解析数据投影。成功 image 的 kind 为 `current-composite`、approximate=false，含 PNG 相对工程路径、SHA256/实际字节数、画布宽高、整数 frame、fps={num,den}、content_version、input_fingerprint、render_key 与 cache_hit。图像来自共享 RenderPlan 在 `[frame,frame+1)` 的有序图层/黑画布/ASS 合成，直接 RGB PNG；不是 final/qc_clean 提帧、黑场兜底、丢层近似或已编码成片的像素等价保证。空画布的真实黑场可以成功。

首切片授权渲染**仅 Linux fd-anchored 写平台、纯内核工程和已完成当前 warm seg/gfx**；安装但本工程未激活的有键插件不阻挡，任何生效插件在 Ctx/潜在 import 前静态拒绝 `plugins.unsupported`。复用真实纯内核 validate 规则及素材探测，不执行插件钩子、不准备虚拟源；更宽插件/跨平台/冷态准备支持仍未完成。默认不渲染路径跨平台仍可用。schema 结构失败、素材/缓存缺失、版本不符、运行失败/取消均不返回成功图像；无 prepare/gfx/all/final/qc_clean fallback。不得把 frame 成功视作正式交付/QC 通过。

写域仅 `cache/preview/frame_<render_key>/`（PNG、完成标记、ASS、随机 FFV1 暂存及锁），不改 timeline、store、普通 cache/seg/gfx、out、receipt 或 delivery 状态。相同 key 锁串行；完整 PNG 和同 SHA 完成标记均通过才可命中。发布前后复核原文完整 SHA/输入身份/有序上游字节/环境；中断和部分写不生成完成标记。遗留失败暂存可存在，不是成功缓存。媒体子进程不脱离调用者 process-group；取消时 kill 并 communicate/wait 回收。CLI 主线程临时处理 SIGINT/SIGTERM 并恢复 handler；Python API 不安装 signal handler。SIGKILL 或库调用者未处理的 SIGTERM 属硬退出边界，不承诺 finally。沿用 fstxn 合作进程/非对抗性边界，不承诺物理断电或非合作输入 ABA 攻击防御。

键包括原文版本、Fast 媒体 stat 输入指纹、整数帧/fps/画布、ASS 精确字节、有序层/上游内容键和当前上游字节 SHA、实际 ffmpeg/动态依赖/字体环境、graph/PNG profile/实现源码身份；环境采集复用既有 gfx 证书工具。**Fast 不等于全媒体内容哈希**，跨机器逐像素等价未证。输入/完整 ASCII JSON wire 各 8MiB；输出超限拒绝而不截断引用。PNG 实读上限 max_png_bytes 为 1..8MiB（`--max-png-bytes`），超限不发布完成结果；编码器瞬态文件可大于该交付上限。CLI 成功0、业务拒绝1、错误旗标组合2，仅 stdout 完整 JSON，不另落报告。

## 显式写事务第一切片（v1；不是完整编辑器）

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
| edit | operations | 有序受管语义批次，见下节；不扩展 apply 或解除 replace 身份约束 |
| undo | head | 只撤当前已提交 head，仍是带新 op_id/base CAS 的新提交；文件必须逐字节等于原 head 结果；不能 undo create/adopt；新结果含反向 edit 变更清单 |

语义编辑使用独立的 `edit`，不是把 `replace` 的身份限制解除，也不改变既有 `apply.changes` 平面协议。尚无 UI、ID 改签或完整 gfx 连续编辑。apply/adopt/edit 保留全部未知 JSON 数据，不投影/展开 `subtitles_src`，不读取/重写字幕外引；replace 的 document 是调用者显式提供的全文。写入允许重排键/格式，不承诺原排版；严格拒重复键、NaN/Infinity、bool 帧以及重序列化会损失十进制值的高精度浮点输入。

### Managed 语义编辑与零写计划

Python 与 CLI 使用**同一份请求**；`operations` 非空、有序，每项以 `op` 指定下表操作，未列请求键拒绝。只有当前 `_vtx` 工程可编辑，不自动 adopt。所有新对象 ID 必须由调用者预先生成并放入请求；同 op 重试保持整个请求不变。

```python
from vtx.api import inspect_timeline, timeline_edit_plan, timeline_transaction
request = {"operation": "edit", "op_id": "edit-request-1",
           "base_revision": inspect_timeline(path)["content_version"],
           "operations": [{"op": "block.split", "id": "clip-id",
                           "at": 15, "new_id": "right-id"}]}
plan = timeline_edit_plan(path, request)  # 零写，不创建历史目录、锁或 intent
# 确认 plan 后才执行；以下是实际写操作：
result = timeline_transaction(path, request)
```

```bash
vtx -t /path/to/timeline.json store plan --request /path/to/request.json
# 实际写操作（相同 request；不是第二种 publisher）：
vtx -t /path/to/timeline.json store --request /path/to/request.json
```

plan 是零写的**未持锁、尽力 snapshot 预测**：只读工程与已存在的完整 history，绝不创建 store、目录、锁、manifest、journal 或 intent，也不恢复、不预留 op_id/ID/版本。重复读取仅检测已观察到的变化（`plan.not_ready`，可重试），不能保证跨文件原子快照、强一致或随后提交成功；即使未检测到变化，另一 writer 仍可随后改变历史。提交重新持锁验证 history、原字节 CAS 与最终结构。与 commit 一样，严格检查当前文件后先判已有 op_id/request_digest，再判 base；因此原请求 replay 允许旧 base，同 ID 新 payload（包括仅改 base）先报 `idempotency.conflict`，不是普通 stale conflict。

所有分支仍使用 `schema:"framivra.timeline.store/v1", operation:"plan"`：

| 只读观测 | 结果与字段 |
|---|---|
| 无该 op 历史，base 匹配 | `ok:true,replayed:false`，返回 `base_revision/request_digest/document/edit/prediction`；仅此分支运行 transformer。document 保留旧 head，不是提交回执 |
| 已 committed，相同完整请求 | `ok:true,replayed:true`，返回 `base_revision/request_digest/current_revision/prediction` 与 `result`；result 是持久化原提交结果加本次 current_revision/replayed:true，表示重传无需新 plan，不含顶层 document/edit |
| 同 op_id 不同完整请求 | `ok:false,code:"idempotency.conflict"`，不返回新 document/edit |
| 已 aborted，相同请求 | `ok:false,code:"aborted"`；需要新 op_id 才能再试，不返回原意图的未提交 after |
| 任何 pending intent 或损坏/缺失的既有 history 证据 | `ok:false,code:"recovery_required"`；须显式 recover 或人工检查，不猜终态、不自动修复、不生成新计划 |

replay 的 `result.revision/edit` **仅是历史提交及其当时窗口**，不是当前工程或当前→after 的新变化；undo 或后续编辑后仍如此，`current_revision` 可不同于历史 revision。没有独立持久化的 plan 时不从当前文档重算历史，不伪造新的 after 窗口。不存在 store 时照常纯计划；缺失工程按 base 次序返回 conflict/input.missing，无任何初始化。plan/commit 均不调用 Ctx、FFmpeg、素材 probe、插件或外字幕解析。`edit.validation` 明确区分结构通过、源边界 unprobed、assets/plugins unchecked、render required，绝不把结构通过写成源长 verified。文件读取可能由文件系统更新 atime；零写保证不创建或改写文件内容、权限、mtime/ctime，不等于禁止系统访问时间记账。

下表字段均是 `op` 以外的键；`?` 为可选。`id/track_id` 是稳定 ID；仅字幕目标 `track_id:"subtitles"` 是虚拟轨选择器，不支持多字幕层。所有帧/位置必须为非 bool 整数，时间 `[start,end)`；移动/复制的 `at` 指全局新起点。

| op | 字段 | 语义 |
|---|---|---|
| source.add | data | 完整新 source，含新 id/path；同 path 多登记允许，不复制媒体 |
| source.remove | id, cascade? | 不删除文件；剩余 source 用户最终拒绝。cascade 若给出，必须恰好枚举当前所有普通 clip/event 用户 ID；空轨仍须同批显式删轨 |
| source.update | id, set | 仅 path/transform；共享 source ID 不变，列出所有共享用户及影响窗口 |
| track.add | family, index, data | family=video/audio；index 为实际数组插入层序，data 含新轨及至少首块的完整 ID/类型字段 |
| track.remove | id | 删除轨及后代；duck/voice 引用必须同批显式修复或删掉消费者，否则最终拒绝 |
| track.copy | id, ids, name, index | ids 为旧轨及全部后代的精确 old→new ID 映射；源默认共享，extension 深拷贝，绝不全局替字符串 |
| track.reorder | family, ids | 提供该 family 全部轨 ID 恰一次；video 数组即真实 z-order |
| track.rename | id, name | 保留 ID；同步已知显式 duck_by、隐式 voice 及 duck_strategy.voice_track；旧名歧义拒绝，不改历史 _meta 或未知引用 |
| track.references | id, set, unset? | 仅 audio 的 duck_by/duck_strategy；unset 为这些字段名列表，与 set 不重叠；专用引用入口，最终引用校验 |
| block.insert | track_id, data | 完整新块；普通视频必须有源窗，gfx 是文件路径而非 source ID；不把 audio.src_out 冒称模型字段 |
| block.remove | id | 删除末块不能留下空轨；可同批显式删轨，不自动填 dummy |
| block.copy | id, new_id, track_id, at | 保留时长/源窗/参数/extension，必须显式 placement；不覆盖已有内容 |
| block.move | id, track_id, at | 仅 placement/父轨，保留源窗/时长/ID；普通视频 kind 之间可换轨，普通/gfx、视频/音频/字幕之间拒绝 |
| block.relink | id, src | clip/event 的显式源/文件引用变更，不改变源窗；普通源 ID 最终检查，gfx 仍是文件引用 |
| block.trim | id, start, end, fade_policy? | inward：视频同步 dst_in/src_in/src_out/dur；音频同步 in/out/src_in，loop 保累计 phase，不发明 src_out；字幕不改字。outward 因无权威源边界事实明确拒绝 |
| block.split | id, at, new_id, fade_policy?, text_policy?, texts? | 严格内部切点；原 ID 留左、新 ID 右，源共享。字幕必须 text_policy=duplicate-text 或 explicit 配 texts=[左,右] |
| parameters.update | id 或 target:"project", path, value | path 是非空局部对象键列表；已知参数整值替换，未知 extension 可逐层定点 set，父对象须已存在；数组作为完整 JSON 值替换 |
| parameters.unset | id 或 target:"project", path | 删除可选参数恢复真实缺省；不可删除必需 volume/text 或不存在键 |
| project.resize | duration_frames | ≥1；越界内容最终拒绝，须同批显式 trim/remove；不改 FPS/分辨率 |
| range.ripple | scope, mode, crossing, resize 及下述模式字段 | 仅显式 scope，不假设 linked AV group；resize 必须 bool |

`parameters` 已知白名单：source.transform；clip 的 scale/x/y/opacity/fade_in_frames/fade_out_frames/cache_dependencies；event 的 volume/pan/loop/fade；audio track 的 duck/duck_params；subtitle.text；project 的 gfx_dependency_policy/cache_dependencies。id/_vtx、源/文件引用、duck_by/duck_strategy、轨名/kind、时间位置、子对象容器不能经参数入口偷渡；改引用用对应语义操作。未知字段保持 JSON value 深拷贝，不表示任意扩展键已获插件类型验证；未知扩展中的隐藏引用、Blender 映射、bake 历史等可能陈旧，结果始终告警，不作递归全局替换。

`range.ripple.scope` 必须恰为 `{"track_ids":[实际轨ID...],"subtitles":false}`，非空且不重复：

- `mode:"shift"` 配 `at/delta`（delta 非零），`crossing:"reject"`：起点 ≥at 的块整体平移，其余跨 at 的块拒绝；at 不得超画布。resize=true 时画布同加 delta。
- `mode:"delete"` 配 `start/end`、`crossing:"reject"|"cut"`：删除 `[start,end)`，后续块平移 `-(end-start)`；reject 拒绝任何相交块，cut 才明确 trim/split/delete。跨两端的每个块须由 `split_ids:{旧ID:右ID}` 恰好提供新 ID；其他条目也拒绝。音频保持源 phase，跨区间字幕必须 text_policy:"duplicate-text"；resize=true 同缩画布。scope 外不动，若缩画布令它们越界则整批拒绝。

fade 默认 `preserve-content`：保留外侧 fade，内部接缝设 0。切点落入已有局部 fade 中段无法精确保留曲线，明确拒绝；可显式 `reapply-edge-fades` 重施原边缘 fade 并收到行为变化告警。任何模式都不静默 clamp，短段容不下 fade 仍拒绝。gfx 目前只有 move/copy/delete；连续 trim/split/crossing ripple 拒绝，不能把改 dur 假装保持原动画内容。

一批在内存完成后才校验最终结构/已知引用/各轨不重叠（字幕也是一个虚拟轨）；允许中间位置交换等暂态。一批仍只发布一个 intent/before/after/工程替换，共用 CAS/op_id/retry/recover/undo，不产生第二个 journal。成功的 `edit` 字段含 `id_locations/created_ids/deleted_ids/changed_ids/source_users/changed_references/affected_ranges/warnings/validation/requires_render_validation`；ID→位置绑定新 revision，不能复用旧 read 地址。undo 返回的是本次反向变化清单，不复用原编辑清单。

### 发布与恢复协议

同目录允许多个工程：按实际文件系统顺序解析父路径（包括 symlink 后的 `..`），再加最终文件名确定 canonical 目标与独立 `.vtx-store-<文件名SHA256>/`，新 manifest 绑定该文件名及 canonical 完整位置（已发布的旧版 filename-only manifest 仍可读、不自动改写）；项目叶 symlink/多硬链与非普通文件拒绝。历史不在 cache/out，renderer 只读当前工程，日志不提供另一份 HEAD；不要移动/删改历史、锁文件或只复制工程后期待 undo。status 可创建锁与 manifest，但不恢复/修改工程；inspect/query 始终零写态。

初始化先取同目标稳定的外置 `.bootstrap.lock`（仅同步发布，不存工程身份/HEAD，永不删除），在随机 sibling `.initializing-*` 私有目录内 write/flush/fsync manifest → rename manifest → fsync 私有目录 → rename 整个完整目录到 canonical history → fsync 工程父目录，然后才可执行事务。首次 mkdir、半写/fsync、两个 rename 与目录同步任一点进程崩溃，canonical 要么尚未发布，要么已有完整 manifest；下次 status/recover/事务在同一 bootstrap 锁内重试父目录同步。未发布私有目录不可能含运行过的事务，重试另建目录，不扫描选取、不采用也不删除其残件；其存在不阻止安全重新初始化。旧实现或外来 canonical 目录缺 manifest（即使空、仅 lock、完整单个 `.publish-*`、多个或损坏临时）一律 recovery_required 并提示人工检查，绝不猜身份/清证据。已发布新 manifest 的位置不符也拒绝，不能凭同 SHA 接管 decoy。旧版已发布 manifest 不具完整位置绑定，不宣称可鉴别其跨目录复制。初始化失败不返回成功、不改工程，发布后父 fsync 失败下次重试也不会永卡；这不是物理断电保证。

顺序固定：同一线程锁+跨进程 flock → 验证/恢复所有已发布 intent → 严格读 → op_id/request_digest 幂等 → 原字节 CAS → 内存操作及共享无素材结构校验 → before/after 完整字节、SHA、完整请求与结果 fsync 并原子发布 intent 目录 → 工程 staging fsync → 最终原字节 CAS → os.replace 工程（线性化点）与父目录 fsync → 持久 commit 结果。没有默认 JSON 回退，不调用 fstxn.locked_update，不嵌套重入锁。

恢复只补证据：current==after 时补 commit；==before 时记 abort，**不自动 roll-forward**，同 op 重试返回 aborted，欲再试须新 op_id；其他字节、缺失/坏证据或无法判定时 recovery_required，绝不覆盖。create 的 before=null 特例允许仍缺失的目标记 abort。已提交同 op 同 payload 返回原结果与当下 current_revision，不倒退后续编辑；不同 payload 返回 idempotency.conflict。提交替换开始后同步/标记失败为 outcome_unknown（带 op_id），必须相同请求重试或 recover，不得误称未提交。

成功 envelope 为 `schema: "framivra.timeline.store/v1"`、operation/ok、op_id/request_digest、base_revision/revision/project_id，首次与重试另带 current_revision/replayed；失败包含 code/message。CLI 成功 exit0，输入/CAS/恢复错误 exit1，不支持的旗标组合 exit2；拒绝 `--dry-run`、渲染旗标与 `--json` 输出文件。

[共享无素材校验](../../vtx/timeline_structure.py) 用于 store 写入及显式带 `_vtx` 的受管工程 validate，检查 schema、ID、源引用、整数帧/区间、EDL、内联/外引字幕冲突和轨重叠；`_vtx` 键存在即适用，坏值、缺字段或未知协议不回退 legacy。未受管工程保持原只读/validate 的 Unicode source ID、高精度扩展数值及精确诊断行为；写入的 strict/ID 限制不扩散到 legacy validate。adopt 不改已有 source.id 或引用：符合上述稳定 ID 规则才可接管，不符合的合法 legacy source ID 明确拒写并保留原字节，不自动重命名/迁移；高精度扩展（含超出 Decimal 指数范围的合法 JSON 数值）在 adopt/status/recover 返回 input.json 失败 envelope，拒写而非舍入，不影响 legacy 只读；此 adopt 是受限显式接管，不是全面兼容迁移。重复 source ID 在 validate 中不再静默覆盖。**提交不等于可渲染**：不探素材、不查实际源帧数、不载入插件，不代替正式 validate/all。保证仅为本地 POSIX 合作线程/进程与进程崩溃；最终 CAS 后仍可能被非合作外写竞态覆盖。Windows/NFS/物理断电未测，不作绝对保证。

## 显式 v2 兼容写地基（A1；旧入口尚未迁移）

v1 请求不带 `protocol`，其 digest、提交结果、重放和 managed edit plan 合同保留；不是把旧请求补默认 mode 后重算身份。显式 `protocol:2` 才走以下新政策，[纯策略与证据校验](../../vtx/timeline_write.py)仍进入同一 [store 发布器](../../vtx/timeline_store.py)的锁、CAS、intent、工程替换与恢复；没有第二 parser/publisher、sidecar HEAD 或默认 adopt。新 intent 为 protocol2、结果 schema 为 `framivra.timeline.store/v2`，工程 `_vtx.protocol` 仍为 1。同一 history 可逐条读取 v1/v2，不改写旧证据。

### 同次原字节读取与双维模式

```python
from vtx.api import read_timeline_for_write, timeline_write_plan, timeline_transaction
snapshot = read_timeline_for_write(path)
# snapshot = {raw_bytes: bytes|None, revision: str|None,
#             document: dict|None, identity_mode: "managed"|"preserve-unmanaged"}
```

此 API 仅一次原字节读取，用同一份 bytes 严格解析、校验并计算 revision；不构造 Ctx、不读/初始化历史、锁、备份、外部字幕、素材或插件。返回独立可变 document；修改它不会修改文件。目标缺失才返回三个 None；现存 JSON null、坏 JSON/结构、重复键、非有限/高精度数值均抛出 StoreError（ValueError 子类，带 code）或 OSError，不伪装缺失。仍为未持锁瞬时快照；调用者需把返回 revision 原样用于 CAS。

每个 v2 请求必需 `protocol/operation/op_id/base_revision/identity_mode/payload_mode/purpose`，拒绝未知或缺失字段。identity_mode 与 payload_mode **正交**：

- `identity_mode:"managed"`：当前工程必须有完整有效 `_vtx` 和全局稳定 ID；create 的 document 由调用者提供全部 ID、不得带 `_vtx`，内核按完整固定请求确定性分配 project_id/head。不更改 replace 的完整 ID/kind 清单约束。坏 `_vtx` 不能降为 legacy，也不允许隐式 adopt/downgrade。
- `identity_mode:"preserve-unmanaged"`：工程没有 `_vtx`，create/replace/undo 绝不插入 `_vtx` 或非 source ID。source ID 保持真实 legacy 字符串/Unicode 引用合同，仍拒绝重复和悬空引用；既有其他 id 只是 schema 检查的自定义数据，不当作全局身份、不按位置重签。未知扩展由完整 document 保留；replace 的完整输入仍由调用者负责。写用数值严格度不扩散到 legacy 只读/validate。
- `payload_mode:"document"`：规范化 JSON 写出，不承诺排版。create 的 purpose 必须是 `create`；其他 managed 操作必须是 `edit`；其他 preserve-unmanaged 操作必须是 `legacy-write`。managed 支持 create/replace/apply/edit/reconcile/undo；preserve-unmanaged 只支持 create/replace/undo。无 v2 adopt 捷径，显式 adopt 继续用原 v1。
- `payload_mode:"exact-import"`：下节独立的 create-only 字节通道，不能用于普通 live 覆盖或 undo。

```python
snapshot = read_timeline_for_write(path)
request = {"protocol": 2, "operation": "replace", "op_id": "legacy-write-1",
           "base_revision": snapshot["revision"],
           "identity_mode": snapshot["identity_mode"], "payload_mode": "document",
           "purpose": "legacy-write", "document": snapshot["document"]}
# 此示例只适用于 preserve-unmanaged；managed 用 purpose:"edit"。
request["document"]["subtitles"][0]["text"] = "修订字幕"
plan = timeline_write_plan(path, request)  # 零写；不初始化 status/history/lock/backup
# 确认 plan 后才可调用 timeline_transaction；该调用会真实写工程与持久历史。
```

`timeline_write_plan` 支持所有 v2 政策组合（包括缺失目标 create/exact）；不带 protocol 的旧 edit 请求仍委托原 `timeline_edit_plan`，后者合同不变。新 plan 成功有 mode/purpose、request_digest/base_revision、prediction；非 replay 另有预测 document/revision 和适用的 edit/reconciliation。其 document 含预测新 head，revision 对应实际提交 bytes（不是 v1 plan 的保留旧 head 文档）。重放返回持久历史 result，不从当前文档重算 after；当前文件仍须先通过严格检查，null 不绕过。historical idempotency 先于 CAS，相同 op_id 改 mode/payload/purpose/base 都是完整请求冲突。未决/坏历史拒绝，绝不恢复或预留；零写、atime 与未持锁尽力观测限制同上节。

新成功提交结果在原身份/hash 字段外包含 `identity_mode/payload_mode/purpose/detached_copy`；无 `_vtx` 时 project_id 为 null。所有 metadata（含 before/after identity mode）、冻结请求、before/after 精确 bytes 和结果受同一证据校验，恢复只比较当前 bytes 与 frozen before/after，不重跑 transformer 或读取旧 payload 路径。

### 稳定 ID owner reconciliation 与撤销

`reconcile` 必需额外字段为 `owner/owned_ids/new_ids/operations`：owner 为显式非空标签；owned_ids 是本次允许变动/删除的现存全局稳定 ID；new_ids 恰好列出本批新增 ID，不能复用旧 ID。operations 仍是上文已审查的受限 managed 语义批次，不接全文替换、JSON Pointer、数组位置映射或按文本猜 ID。未有可靠 ID/业务映射的旧 writer 必须明确拒绝，不能自动制造身份。

最终比较包括对象 kind、父容器、非 owned 同级次序及全部未知深字段：非 owned 数据/位置、root/project 数据不得改变；隐式引用修复若影响非 owned 也拒绝。owned 对象的未知字段仅通过既有定点语义变换保留/修改，不投影重建。结果 reconciliation 列出 owner/base_revision、changes/creates/deletes/preserved_ids/warnings；这是调用者声明的业务范围审计，**不是宿主授权或持久 owner 注册表**。ASR/静态 duck 已按下文 B1 迁移；Blender、脚手架、A2/snapshot 及 gfx/HTML 其他写入口仍未迁移，不能据此宣称所有 writers 已完成。

仅 managed、document、edit-purpose 的 v2 reconcile 可另带**成对**可选 `owned_meta_keys` 与 `meta_updates`。例如 `owned_meta_keys:["bed_static_duck"]`、`meta_updates:{"bed_static_duck":{"baked":true}}`：scope 必须为非空、唯一、非空白字符串列表，不得包含 `_meta`/`_vtx`；updates 必须为非空 JSON 对象，其键为 scope 子集。所有键均指 **root `_meta` 下的直接字面键**，点/斜杠不解析为 dotted path/JSON Pointer，不接管整个 `_meta`、project、source 或 `_vtx`；值中的 id/_vtx 只是普通 JSON 数据。没有 meta_remove；null 是普通值，不表示删除。其他 actions/unmanaged 请求不接受这些字段，不能只传其中一个或传空 scope。

语义批次与 updates 在同一内存 candidate 上组合、一次范围审计、一个 intent 和一次工程 publish；不经 replace、不做第二事务，也不扩大普通 parameters 编辑权限。只有显式带该 pair 时允许 `operations:[]` identity candidate（例如 segments/volume 已符合目标，只需首次 marker）；旧 reconcile/edit 的空数组仍拒绝。原 root `_meta` 缺失可为授权键建立 dict；原值非 dict 报 `reconcile.meta_container`，不得强转/擦除。非 owned 兄弟及其他 root/project/source 数据仍按 typed JSON 严格比较，不能用删键/换容器逃逸。

新 pair 的 reconciliation receipt 额外含 `owned_meta_keys`（声明顺序）与 `changed_meta_keys`（排序，presence 差异也算变化），后者从 before/after 独立重算，不从输入猜测；无变化则空列表。history 校验独立复算 scope/变化并核对 after 的 updates 值，拒绝省略变化、True/1 偷换和非 owned 改动；status/plan/replay/recover 不放宽。无 pair 的原有效 v1/v2 请求 canonical bytes/digest、summary/result 和历史读取严格保持，不补默认字段、不重签。仍使用 store/v2 与 protocol2，按明确 pair 字段 presence 分支扩展，不发明 v3；能力公布于 `v2.reconciliation_meta`。meta-only 变化不伪造 affected object IDs/窗口；CAS、重试、undo、recovery 仍走原 publisher。[owned-meta 契约测试](../../tests/contracts/test_timeline_owned_meta.py)覆盖手算 split/volume/marker、一批发布、literal/null、typed scope/receipt、旧空数组拒绝及现有崩溃边界。

v2 的范围、变更摘要及证据按 JSON 值递归比较：object 键序无关，array 次序保留；boolean 与 number 严分。JSON 只有一种 number，int/float 同值（含 ±0）相等，不比较数字词法；实际可写输入仍受 strict parser 的有限值/十进制往返精度约束，Python tuple/非字符串键拒绝。owned 真变化须列入 changed_ids/changes，并追踪父轨、共享源及 project 的影响窗口。v1 有效请求 digest、结果与字节不重签、不改写，原 v1 摘要保持；历史 boolean 被换成 0/1 属损坏，status/plan/replay 拒绝而非放宽恢复。

managed undo 额外字段仍为 head：只撤当前 managed head，保持原字节 CAS；也能消费原 v1 已提交记录。preserve-unmanaged undo 额外字段为 `undo_of`：必须显式选择 committed 操作且当前 bytes 逐字节等于该记录 after，然后**原字节**恢复 before；语义相等但排版变化也拒绝。不维护时间戳/sidecar HEAD，不猜最后操作。create 的 before 不存在，不把 undo 变成删文件；adopt 跨 mode、exact artifact 均不可隐式撤销。撤销本身是新请求/新提交，仍有幂等与恢复。

[JSON 值回归](../../tests/contracts/test_timeline_write_json.py) 覆盖真实 plan/CLI 提交、嵌套非 owned/root/project 范围、owned 类型变化摘要及 typed receipt 损坏后的 status/plan/replay 零写拒绝，并保留 object 键序无关和 array 顺序边界。

### Exact-import 与 detached-copy

Python 额外字段恰为 `raw_bytes`，只收已固化的 bytes；CLI 用唯一的 `payload_file` 替代，按调用者 cwd 解释并一次读取普通非 symlink/多硬链文件；不收 stdin、URL、目录或内联 raw_utf8。读入发生在 digest/锁之前，journal 存 frozen UTF-8 文本和 after.bin，不只记录可变源路径。CLI 的 `store plan --request ...` 也走相同冻结和纯计划，提交仍需显式 `store --request ...`；文件旗标不是另一 publisher。

仅 `operation:"create"`、base=null 且目标不存在可用。purpose 只允许 `create/frozen-copy/detached-copy`；前两者要求 preserve-unmanaged。managed 原 bytes（含旧 project_id/head）只能用 `detached-copy`，要求完整 managed payload；不生成新 project_id 同时假称字节一致、不复制来源 history 或承诺来源 undo。目标有独立创建历史，即使两个文件的 `_vtx` 相同也不是同一可撤销工作副本。source 文件随后变化只会使重新请求的 digest 不同，不能改变已固化 intent。

exact 保留首尾空白、键次序、Unicode escape、数值词法与完整原文件 SHA；依然用同一 strict writable parser/schema 校验，重复键/NaN/高精度不可写数值明确拒绝，绝不舍入或从解析 document 重序列化。exact 不是 live overwrite、一般备份恢复或 snapshot 绕过口；目标碰撞拒绝。

### A2 pinned artifact target：未实现、未验证

当前 path-store 仅支持上文普通本地 POSIX 合作工程；**不能安全代接 snapshot 临时目录**。没有已实现 PinnedArtifactTarget，也没有 snapshot adapter；A1 不代表完整兼容地基完成。

拟议最小内部边界（设计，不是可调用 API）：`PinnedArtifactTarget(parent_fd, work_fd, final_name, timeline_name, final_path)`。parent_fd/work_fd 必须在已验证、受信任目录打开并用 fstat 固定 dev/inode；所有文件、history、锁与 staging 操作需 dirfd-relative、拒绝 leaf alias，不能在校验后回落字符串 resolve 或 chdir。final_path 仅为最终公开目标与 history manifest 的绑定身份，不能把临时工作路径写成最终契约；拒绝同 SHA decoy history。

engine 必须把已有同核发布状态机扩展为：在 pinned 私有工作目录冻结 timeline/intent/完整产物并逐级 fsync → **整个目录发布到最终名字的 rename 为线性化点** → fsync 父目录 → 持久最终 commit。不能把临时目录内的文件 replace 当最终成功，也不能简单复用普通 path-store 后移动其 history。最终 create-only 竞争、目录 rename 前后 EIO/SIGKILL 的 before/after/neither 恢复、最终名字/manifest/已固定 fd 同一性、别名/父目录替换/TOCTOU/descriptor 生命周期均需独立故障审查；未发布私有目录不能靠扫描任意残件接管。具体 primitive 与恢复布局尚待 A2 实现验证，不把以上设计描述当安全保证；在此之前 C3 snapshot 迁移阻塞。

## 生产写入口支持矩阵与迁移清单

以下为当前源码盘点，不是本切片已经实现的写能力。手工编辑/外部脚本无法穷举；仓内测试夹具、验收探针、冻结模板复制不算生产编辑器。

| 当前入口与源码证据 | 当前支持与风险 | 后续统一内核迁移面 |
|---|---|---|
| [Blender 回写](../../apps/blender-bridge/blend2json.py#L90-L193) | 视频移动/边裁/同族换轨后重排；音频 in/out/src_in 可回写，换音轨和部分 loop 移动拒收；字幕、音量、pan 不回写。重叠时**先写后 exit 2**，非事务覆盖 | 精确位置寻址、编辑计划、冲突/重叠预检、提交前拒绝；不得照抄过时文件头的“不回写音频” |
| [ASR 合并](../../apps/asr/tl_merge.py)，[调用者](../../apps/asr/asr_cli.py) | B1 经公开同 publisher：转录前 raw/base/op_id，默认替换、显式 append；legacy preserve-unmanaged、managed owned 字幕 reconcile；外部字幕先拒绝，dry/nochange 零写 | cache/reports 字幕数组备份/报告不是 timeline 事务对象；旧 helper 无 snapshot 只保障其入口后的窗口 |
| [静态 duck 烘焙](../../plugins/duck-static/bed_static_duck.py) | B1 早 snapshot、纯变换、同 publisher；managed 首段保 ID、新 IDs 固定，显式 owned marker；同参 skip/dry 零写；首次备份 A1 exact-import/create-only 独立 history | 备份后主 CAS stale 可留下备份副作用，非两文件原子事务；需支持 owned metadata 的内核，不回退覆盖 |
| [通用脚手架](../../scripts/new_project.sh#L11-L35) | heredoc 整体写 timeline，可覆盖既有文件；创建 cache/out | 新工程创建与禁止无意覆盖 |
| [口播脚手架](../../templates/tech-talk/scripts/new_project.sh#L94-L115) | 已存在则不覆盖，递归改占位路径与 _meta，再写新文件 | 保留新建语义与原始模板扩展 |
| [MG build](../../templates/mg-styles/build.py#L16-L65) | 新目录限定，dry 返回工程，生成 gfx/音频后写 timeline | 新建工程与完整源/轨数据 |
| [MG media](../../templates/mg-styles/build_media.py#L111-L153) | 调用 build 后再替换视频轨/追加源，第二次覆盖 timeline | 新建过程的整批计划与失败边界 |
| [既有只读预览页](../../vtx/preview.html#L264-L281)（非写入口） | 文件/粘贴 JSON 读取展示，不写工程；自带页面数据检查不是渲染校验 | 后续统一读模型消费面，不虚构编辑/导出能力 |
| [只读 CLI](../../vtx/cli/timeline.py)、[只读模型](../../vtx/timeline.py)、[引用模型](../../vtx/timeline_reference.py) | inspect/query 为完整解析数据；reference 提供权威原文和带版本暂态地址；均零写态 | store 从原文与 content_version 起步；不得从会展开外部字幕的 Ctx.tl 回写 |

仅 ASR 与静态 duck 已迁移 B1；Blender、通用/口播/MG 脚手架以及 snapshot（A2 pinned target 未实现）等仍未迁移，不能宣称所有 writers 或统一人/AI 编辑已完成。[fstxn](../../vtx/runtime/fstxn.py) 的 file_lock 由 store 单次复用，不用其默认损坏 JSON 回退/无 fsync 的 locked_update。此切片没有常驻服务、DSH 桥或消费 UI。

## 行为验证

[真实行为测试](../../tests/contracts/test_timeline_api.py) 覆盖完整扩展保留、缺素材/未知插件、输入字节身份、暂态地址/数组次序、交集与端点、严格 JSON、坏 shape 仍可检查、结构化诊断、外部字幕不完整标注、开发错误透出、CLI/Python 同值及无工程写副作用。[事务行为测试](../../tests/contracts/test_timeline_store.py) 在仓外临时项目覆盖双进程/线程同 base 恰一成功、重试不倒退、严格输入/ID/扩展/字幕外引、create-only、head undo、每阶段异常/SIGKILL、每次真实 write/fsync/rename/replace 调用 EIO、坏历史与源码 CLI/API 同值。[兼容回归](../../tests/contracts/test_timeline_store_compat.py) 补充相同 SHA 工程的绝对/相对 symlink/.. 寻址、跨路径双进程 CAS/恢复、坏别名拒绝，以及 legacy 读校验与 managed 严格拒绝的边界。执行：`env python3 -m unittest tests.contracts.test_timeline_api tests.contracts.test_timeline_store tests.contracts.test_timeline_store_compat -v`。[引用测试](../../tests/contracts/test_timeline_reference.py) 以真实临时文件/公开 CLI 覆盖严格原文与块 span、未知嵌套/大整数/高精度/转义、managed/legacy、ID与版本寻址、帧边界/fps、外部字幕、输入及 wire 上限与零写态；执行 `env python3 -m unittest tests.contracts.test_timeline_reference -v`。测试通过不等于渲染或整仓全量通过；发行包安装烟测另需在仓外运行。
