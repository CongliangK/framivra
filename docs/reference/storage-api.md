# 项目空间与缓存清理接口

本接口只增加空间观察与普通 GC 确认入口，不改变时间线、渲染质量或现有 `gc` 规则。实现归属：[内核](<../../vtx/storage.py>)、[CLI 适配](<../../vtx/cli/storage.py>)、[既有清理计划](<../../vtx/cache/hygiene.py>)；DSH 只是消费者，不能自行选择或删除路径。缓存档位仍只有 `off/core/aggressive`，语义见[缓存策略](<../cache-provenance.md#全局缓存档位>)。

## 命令与协议

以下两项只读；不会新建 cache/out、锁文件或持久计划。`inspect` 不加载插件或构造渲染上下文；`plan` 按既有 GC 构造上下文，依赖、配置或插件失败会明确拒绝。

```bash
python3 -m vtx -t /physical/project/timeline.json storage inspect
python3 -m vtx -t /physical/project/timeline.json storage plan
```

**写操作**：只有在所有渲染、预览及其他写入者已停止，人工检查预演之后才能执行。`clean` 必须使用 `--request-stdin`，输入一个严格 JSON 对象，恰好包含 `plan_id`、`content_version` 和 `confirm:"stopped-writers-and-cleanup"`。前两项取自刚才的计划；不接受候选路径、替换计划、deep/nuke 或其他 CLI 开关。命令形态为 `python3 -m vtx -t /physical/project/timeline.json storage clean --request-stdin`；不自动重试。

stdout 是单个 JSON；诊断日志走 stderr。所有响应包含：

- `schema:"framivra.storage/v1"`、`operation:"inspect"|"plan"|"clean"`、`ok`、`content_version`、`diagnostics`。
- 成功时 `content_version` 是原始时间线字节的 `sha256:…`；无法读取版本的失败响应为 `null`，不得当作有效快照。
- `inspect` 增加 `usage:{project_bytes,cache_bytes,out_bytes,other_bytes,file_count,symlinks_skipped}` 与有效 `cache_mode`。
- `plan` 增加 `plan:{id,entries:[{path,bytes,category,reason}],bytes,count}`。路径仅供预览，`id` 为冻结内容及身份摘要。
- `clean` 正常返回计划与实测 `removed:{bytes,count}`、`remaining:{bytes,count}`。部分未删为 `ok:false` / `clean.incomplete`；删后无法核算为 `outcome_unknown:true` / `clean.outcome_unknown`，不编造已回收数量。失败不等于零写入。
- 退出码：成功 `0`，业务拒绝/执行失败 `1`，不支持的参数组合 `2`。旧内核没有本能力时，消费者只禁用空间功能，不禁用原有读接口。

`timeline capabilities` 新增可选 `capabilities.storage`，包含同一 schema、`operations:["inspect","plan","clean"]` 与 `request_stdin:true`；其他能力字段不变。

## 统计与安全边界

- 项目根是时间线父目录；只累计其内普通文件逻辑字节，`cache` 与 `out` 分组，剩余为素材及其他。硬链接按路径计数；不承诺独占物理占用、去重字节、全盘容量或外部素材大小。
- 不跟随符号链接/reparse 点；项目祖先和时间线链接会拒绝。扫描发现的链接计入跳过数。为复用既有 GC 的安全边界，`plan/clean` 在 cache/out 下发现任何链接即拒绝，而不是猜测可删除的子集。
- 单次扫描最多 100000 个条目、15 秒协作截止；阻塞的系统调用不能靠此期限抢占。超限或不可读就失败，不把部分总量显示为完整结果。计划最多 10000 项；时间线和响应各 8 MiB；stdin 4096 字节。清理前预留响应核算空间。
- `clean` 在既有 CacheSession 的逻辑 `.cache-policy.lock` 上互斥，再重建计划、比对时间线版本与候选全量身份/后代状态后调用原 `CleanupPlan.execute`。实际 sidecar 仍沿用 `fstxn.file_lock` 的追加后缀规则；没有第二套删除器。
- 既有源件、成片、证据及锁 sidecar 保护仍生效；不存在“清空整个缓存”按钮。计划中目录可能保留保护项，实际回收量不能拿预演量代替。
- 该锁只协调参与 CacheSession 的调用，不保证所有外部/API 写入者都参与。**停止所有写入者仍是人工前置条件**；不要把本入口理解为可边渲染边清理的全局调度器。进程被取消、断连或回收失败可能留下部分删除，需要观察并重新预演，不得重复旧确认。

## DSH 消费

[侧栏说明](<../../dsh-sidebar/README.md>)与[开发边界](<../../dsh-sidebar/DEVELOPMENT.md>)描述安装与宿主 API。工作台属性侧栏的“空间与缓存”在面板/文档可见时每 10 秒采样，不重叠扫描；预演期间停止自动采样，并显示完整候选清单。每次清理须勾选独立的停止写入与删除确认；语义编辑授权不能替代它。

宿主复用 owner-only `/api/framivra/control`：`storage-inspect`、`storage-plan`、`storage-clean`、`storage-cancel`。所有请求沿用 Session/grant；只有 `clean` 另收 `planToken` 与固定 `confirm`。宿主冻结内核计划，仅返回有效期 60 秒的一次性 opaque token，绑定项目、Session、宿主配置选择、可执行文件与时间线版本。浏览器不提交文件路径或内核计划。模型工具不暴露这些操作；取消也不授予编辑/删除权限。

宿主沿用有界 JSON 与托管子进程回收；本地 frame/语义事务/提案与空间操作互斥，取消和失败后的清理不能提前释放仍活跃的进程所有权。宿主配置选择、项目或保存版本变化会使预演失效；清理未知结果不会自动重试。统计采样不是文件系统原子快照，也不是模型/浏览器内存监控。

档位切换复用现有“插件 → Framivra → 配置文件 → 缓存档位”表单：审核并确认 CAS 保存全局 `cache.mode`，下次操作生效，不中断运行任务，也不立即删除历史缓存。本次不增加 CPU、内存、并发或编码质量档位。
