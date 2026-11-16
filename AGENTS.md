# Framivra 智能体指南

## 项目与入口

- 素材与 timeline.json → 帧精确视频、混音、QC、缓存；[内核](<vtx/>)永不可关。
- [apps](<apps/>)产出素材或辅助结果；[plugins](<plugins/>)扩展时间线管线；启用后不得忽略依赖或失败。
- [DSH 侧栏](<dsh-sidebar/README.md>)独立发行；配置走内核事务，保留帧率/预览限制；[空间管理](<docs/reference/storage-api.md>)的删除权归内核。
- 制作从[技能](<docs/skill/SKILL.md>)进入；科技口播与开发范本见[工作流](<templates/README.md>)；组件查[catalog](<docs/catalog.md>)。
- 开发先读[架构契约](<docs/ARCHITECTURE.md>)与[DEV](<docs/DEV.md>)；时间线 API/写事务见[接口边界](<docs/reference/timeline-api.md>)。
- 依赖见[README](<README.md#依赖矩阵>)：Python ≥3.9，媒体路径显配；模型默认关闭，空依赖声明≠只需标准库。

## 常用命令

- 离线体检：`env VTX_DOCTOR_OFFLINE=1 python3 vt.py doctor`；联网会 fetch 写入。
- 修链预演：`python3 vt.py --fix-links --dry`；去掉 `--dry` 前核对目标与权限。
- 渲染：`python3 vtx/vtx.py -t <timeline.json> all`（写缓存/产物）；前提见[交付链](<docs/skill/references/edit.md#正式交付链r1-r4-共用>)。
- 全仓测试：`bash scripts/run_all_tests.sh`；局部/依赖见[贡献指南](<CONTRIBUTING.md#测试入口>)。
- 文档必跑：`bash scripts/doc_gates.sh`；如实报 PASS/FAIL/SKIP/UNRUN，冒烟/语法≠业务/全量验收。

## 写态红线

- CI 含工作流、门禁、测试选择/分片、通过判据与检查设置；改前须说明必要性、影响及验证/回滚方案，获用户明确批准后仅按批准范围调整。
- 未获批准不动 CI；不得删减/跳过测试、放宽门禁、绕过必需检查以修失败、提速或刷绿，先修根因。
- 项目/素材库置仓外；项目 cache/out 是产物非源件；示例默认零写态，写操作须警示。
- 不手改 catalog 生成区；manifest/registry 改动须[重注目录并过文档门](<docs/DOC-CHARTER.md#单一事实来源>)。
- [VERSION](<VERSION>)、[pyproject](<pyproject.toml>)、[内核版本](<vtx/__init__.py>)改一必三；发布遵循[冻结规则](<docs/releases.md>)。
- 技能与工作流文档修改前须公告影响并经[消费面评审](<docs/DOC-CHARTER.md#消费面>)，修改后核对真实消费入口。
- 退役先审引用/恢复路径；禁篡改冻结证据、删除在用契约/证据/许可证及未解决问题。

## 编辑纪律

- 代码变更须同 PR 同步本指南的结构、入口或约束。
- 仅跨目录常驻要点，每点一行；禁流水账/平行指南/本地叠加层。
- 一事实一家，细节链权威文档；目录变动修活引用，不留旧入口壳。
- 实现后独立复审并交维护者评审；契约/API 变更须维护方同意与独立对抗审查。
- [文档规则](<docs/DOC-CHARTER.md>)管职责/写入权；预算见[快照](<scripts/doc_budget.snapshot.json>)，现字数另测，不改门禁/快照掩盖超限。

## 基线与性能

- 唯一主基线为 30秒/900帧/1080p30 SVG；禁用退役12秒基线，机制探针不能代替主片。
- 准备/核验仅用 `scripts/demo.py prepare/verify`；portable-v2 用公开锁定字体/标准库音频，不混旧输入；获取/恢复见[Demo](<templates/demo-timeline-svg/README.md#主性能基线>)。
- 固定输入/质量/环境/后端/配置测冷暖全链；留身份/哈希/证据；未跑记 UNRUN，探针不冒充整体提速。
- 正式/区间视频拼接共用内核帧端点生成的 concat 时长；不得以容器推断时长或仅总帧数通过替代帧时钟正确性。
