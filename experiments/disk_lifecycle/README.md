# 只读磁盘盘点与交付后审阅预览（S1 实验）

本实验只观察调用者明确给出的普通目录，输出 JSON。它不接入生产渲染、GC 或 CI，不验证成片、QC、通知或使用者身份。当前 b287、fastPNG、性能对比及待独立复核材料继续保留；空 hold 列表和“可重建”声明均不能解除其用途保留。

## 两个显式入口

从源码检出根运行。必须使用 `-B` 或 `PYTHONDONTWRITEBYTECODE=1`，避免 Python 写入字节码。请求和需要保存的报告应放在获准、受观察 roots 之外的位置；工具没有输出文件选项。

```bash
python3 -B -m experiments.disk_lifecycle inventory --request /absolute/path/request.json
python3 -B -m experiments.disk_lifecycle delivery-cleanup-plan --request /absolute/path/request.json
```

两者都进行当次扫描；第二个入口包含当次 inventory 及其摘要。stdout 只有一个 JSON 对象；stderr 仅简短错误诊断。无默认目录、apply、execute、删除、移动、Trash、压缩、重渲染、进程终止、锁写入、网络、后台扫描或生产模块导入。

- exit 0：本次报告计算完整；不等于可删、QC 合格或已交付
- exit 2：参数或请求不合法，未开始扫描
- exit 3：部分观察或关键 UNKNOWN，仍返回可用 JSON

任何报告均 `execution_eligible=false`、`planned_operations=[]`。没有可执行删除能力，也没有清理授权。

## 请求

版本为 `framivra.disk-lifecycle-request/1`。最小结构：

```json
{
  "schema": "framivra.disk-lifecycle-request/1",
  "project_id": "caller-project-label",
  "run_id": "caller-run-label",
  "roots": [{"id": "gfx", "path": "/absolute/new-isolated/gfx", "role": "gfx"}],
  "retain_paths": [],
  "hold_reasons": []
}
```

最小请求可做 inventory；计划中的缺失声明保持 UNKNOWN。拒绝未知字段、重复 JSON 键、布尔冒充整数、NaN/Infinity、未配对 Unicode 代理码点、非法枚举和未声明 root。字符串检查在扫描前完成；合法中文与非 BMP 字符保留。每个 ObjectRef 使用 `{ "root_id": "gfx", "relative_path": "frame.png" }`；根本身用 `.`，普通相对路径用 POSIX 分隔符。

`roots` 各项必填 `id/path/role`。角色限 input、music、sdk、download、gfx、encode、audio、final、evidence、history、shared、unknown。相同目录的重复声明及普通嵌套 roots 去重，角色和来源全部保留。不同 roots 中同名文件不共享声明。

可选字段：

- `code_ref`: `{commit, tree}`，非空字符串，均 caller_supplied，不执行 Git 验真
- `max_entries`：正整数；默认值见 inventory.py 常量。root 目录也计条目，边界到达即停止枚举
- `max_scan_seconds`：有限正数；默认值见 inventory.py 常量。这是循环/文件系统调用边界的尽力预算，阻塞中的一次调用可能超时，不是硬时限
- `ownership`：对象记录列表，字段为 `object, owner_run_id, purpose, rebuildable, activity, references, evidence_refs`
- `delivery_context`：字段为 `run_id, final, verification, finish, notification, evidence_refs`
- `reserves`：下述新增逻辑字节预算声明

ownership 的 purpose 限 NEW_ISOLATED_DERIVED_PNG、BASELINE、EXPERIMENT、PERFORMANCE_COMPARISON、FAILURE_DIAGNOSTIC、PENDING_INDEPENDENT_REVIEW、SHARED、OTHER、UNKNOWN。前述基准、实验、性能、失败及待复核用途强制 HELD；SHARED 强制 PROTECTED。只有明确 NEW_ISOLATED_DERIVED_PNG 可进入审阅门。`rebuildable` 是布尔值。

`activity={status,evidence_refs}`，status 为 DECLARED_INACTIVE / ACTIVE / UNKNOWN。`references={status,evidence_refs}`，status 为 DECLARED_NONE / PRESENT / UNKNOWN。每个 evidence_refs 都是 ObjectRef 列表。

delivery_context：

- `final` 必须指显式 final root 下本次已观察且未见变化的普通文件
- `verification={status,evidence_refs}`，status 为 PASS / FAIL / CANCELLED / PENDING / UNKNOWN
- `finish={required,status,evidence_refs,reason}`；required=true 只接受 PASS 并要求证据；required=false 只接受 NOT_REQUIRED 且非空 reason。其他 status 为 FAIL / CANCELLED / PENDING / UNKNOWN
- `notification={status,reference}`，status 为 SENT_WITH_REFERENCE / FAILED / CANCELLED / PENDING / UNKNOWN；成功必须有非空 reference

所有必要证据引用必须非空，指本次稳定普通文件，属于 evidence 角色或显式 retain。只检查位置与元数据，不读取或认证证据内容。成功值始终标记 SUPPLIED_NOT_REVERIFIED。声明冲突、对象无法闭合、跨 run、活跃、引用存在、失败/取消/待办或未知均不能被 rebuildable=true 覆盖。

## 分类与单源保护

每项只有 PROTECTED、HELD、UNKNOWN、REVIEW_ONLY_PNG 四种结论。REVIEW_ONLY_PNG 最多表示值得人工审阅：完整观察、稳定非空普通 .png 文件、明确 gfx 角色、全部归属/交付/证据门通过且无保留。PNG 仅按后缀和用途声明识别，不解码或认证图像质量。

生产保护名只从本检出固定 `vtx/hygiene.py` 以标准库 AST 读取：三个 OUT_PROTECTED 表必须为唯一顶层字符串 tuple，out_protected 必须保持已审判据结构。源码不执行、不导入；错误或变化使保护源 UNKNOWN，并全局阻断 review-only。报告记录源码哈希，这不是可信签名或远端提交证明。

明确 input/music/sdk/download/final/evidence/history/shared 角色和 retain 总是保护。保护表判据保守应用于声明路径的名称分量。额外保留 .lock、JSON/JSONL 账本/回执格式、complete/.complete 元数据名称分量及其后代，可能多保留；这不是生产 GC 规则改动。目录本身永不成为操作候选。相同已知 inode 的保护、hold 和声明矛盾传播到全部观察名称；可能存在未观察硬链接时保留待核。

## 计量与预算口径

inventory 的一致性是 BEST_EFFORT_NON_ATOMIC。complete 只表示请求枚举无已知缺口，不表示整个目录同时静止。读取前后变化给 CHANGED_DURING_SCAN；部分扫描的完整总数为 null，并单列 partial_known_subtotal，未知遗漏数为 null。

文件 logical_path_bytes 逐目录项计量；logical_unique_inode_bytes 按 (dev, ino) 去重。allocated_unique_inode_bytes 使用平台 st_blocks × 512，目录逻辑/块元数据另列。缺失计数、分配块或卷 free 均明确 UNKNOWN，不把未知当 0。重复/嵌套 roots 不重算同一普通目录项，每卷 free 只观察一次。

allocated 不是独占物理 extent 或实际可回收量；APFS/CoW、压缩、快照、打开句柄、元数据、未观察链接等都可能影响释放。estimated_reclaimable_physical_bytes 固定 null，原因为 NOT_MEASURED_AND_NO_DELETION。不同 st_dev 也不证明 APFS 容量物理独立。

每条 reserve 为 `{reserve_id,scope_root_id,kind,bytes,rationale}`，kind 限 LOGS / DIAGNOSTICS / SUMMARY / SAFETY_MARGIN。bytes 是未落盘的调用者声明新增逻辑字节非负整数，不是 allocated；显式 0 与缺失不同。

同 id 经重叠 roots 映射同卷且内容一致才合并计一次；冲突或卷未知为 UNKNOWN。同卷同 kind 多个不同 id 同样冲突。每个观察卷四类明确预算齐全才计算完整 reserve total；否则仅列已知小计。observed_plus_reserve_logical_bytes 只在该卷观察及声明完整时求和。observed_plus_reserve_allocated_bytes 永远 null。

与即时 free 的 LT/EQ/GT 仅为 LOGICAL_BUDGET_VS_INSTANT_FREE_NUMERIC_ONLY 数值比较，不叫准入、足够空间或安全余额。future_growth、peak_disk、可回收物理量均未知，hard_limit_enforced=false，无峰值采样、全机封顶或执行动作。

## 手动普通验证

测试要求调用者先准备新的获准普通 fixture 目录，环境变量 DISK_LIFECYCLE_TEST_ROOT 指向它；测试保留 fixture，不清除。仅显式运行两个实验测试模块，不做全套 discovery、不改 CI。

```bash
env PYTHONDONTWRITEBYTECODE=1 DISK_LIFECYCLE_TEST_ROOT=/absolute/approved/new-fixtures python3 -B -m unittest experiments.disk_lifecycle.test_inventory experiments.disk_lifecycle.test_plan -v
```

覆盖普通文件/目录、合成元数据、声明真值表、静态 AST 文本、预算和只读哨兵；不创建或运行 symlink/alias/traversal/ancestor/重定向安全负例。真实 Mac、APFS、真实媒体、b287/fastPNG 材料、整片生命周期、峰值和实际回收均未验证。
