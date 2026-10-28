# 时间线只读 API

本页是只读接口与写入口迁移清单的唯一 home。结构权威仍为 [timeline schema](../../vtx/timeline.schema.json)，渲染校验仍归 [validate](../../vtx/pipeline/stages.py)。本接口的 `ok` **仅表示可安全查询的数据形状**，不表示 schema 合规、素材可用、插件已安装或可渲染。

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
| [新只读 CLI](../../vtx/cli/timeline.py)、[新公共 API](../../vtx/timeline.py) | 本切片新增，完整原文和暂态地址；无写入/ID/服务 | 未来写层从原文与 content_version 起步；不得从会展开外部字幕的 Ctx.tl 回写 |

已有 [fstxn](../../vtx/runtime/fstxn.py) 是文件发布/锁/恢复设施，不是语义编辑 API，不应在只读层调用。上述入口当前**未迁移**；本切片不新增写接口、稳定 ID、常驻服务或 DSH 桥。

## 行为验证

[真实行为测试](../../tests/contracts/test_timeline_api.py) 覆盖完整扩展保留、缺素材/未知插件、输入字节身份、暂态地址/数组次序、交集与端点、严格 JSON、坏 shape 仍可检查、结构化诊断、外部字幕不完整标注、开发错误透出、CLI/Python 同值及无工程写副作用。执行：`env python3 -m unittest tests.contracts.test_timeline_api -v`。测试通过不等于渲染或整仓全量通过；发行包安装烟测另需在仓外运行。
