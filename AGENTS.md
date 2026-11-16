# Framivra 智能体指南

## 项目与入口

- B 站视频制作工具箱：素材与 timeline.json → 帧精确视频、混音、QC 与缓存；[内核](<vtx/>)永不可关。
- [apps](<apps/>)产出素材或辅助结果；[plugins](<plugins/>)扩展时间线管线；启用后不得忽略依赖或失败。
- [DSH 侧栏](<dsh-sidebar/README.md>)独立于 Python 内核发行，提供预览、引用、显式剪辑与工程配置；配置仍走内核事务，不解除帧率/预览限制。
- 制作从[技能](<docs/skill/SKILL.md>)进入；科技口播与开发范本见[工作流](<templates/README.md>)；组件查[catalog](<docs/catalog.md>)。
- 开发先读[架构契约](<docs/ARCHITECTURE.md>)与[DEV](<docs/DEV.md>)；时间线 API/写事务见[接口边界](<docs/reference/timeline-api.md>)。
- 环境按[README](<README.md#依赖矩阵>)准备：Python ≥3.9，媒体工具按路径配置；模型默认关闭，依赖声明为空不等于仅需标准库。

## 常用命令

- 仓根离线体检：`env VTX_DOCTOR_OFFLINE=1 python3 vt.py doctor`；联网形态会 fetch 写入。
- 链接修复预演：`python3 vt.py --fix-links --dry`；去掉 `--dry` 前核对目标与权限。
- 源码渲染：`python3 vtx/vtx.py -t <timeline.json> all`（写缓存与产物）；前置条件见[交付链](<docs/skill/references/edit.md#正式交付链r1-r4-共用>)。
- 全仓测试：`bash scripts/run_all_tests.sh`；依赖与局部入口见[贡献指南](<CONTRIBUTING.md#测试入口>)。
- 文档改动必跑：`bash scripts/doc_gates.sh`；如实报告 PASS/FAIL/SKIP/UNRUN，冒烟与语法检查不等于业务或全量验证。

## 写态红线

- CI 包括工作流、门禁、测试选择/分片、通过判据与检查设置；修改前须充分确认必要性、影响、验证/回滚方案，说明并取得用户明确批准，仅在批准范围内调整。
- 未获批准不动 CI；不得删减/跳过测试、放宽门禁或绕过必需检查来修失败、提速或刷绿，先修业务根因。
- 视频项目与素材库放仓外；项目 cache/out 是运行产物，不作源件维护；示例默认零写态，写操作须警示。
- 不手改 catalog 生成区；manifest/registry 改动须[重注目录并过文档门](<docs/DOC-CHARTER.md#单一事实来源>)。
- [VERSION](<VERSION>)、[pyproject](<pyproject.toml>)、[内核版本](<vtx/__init__.py>)改一必三；发布遵循[冻结规则](<docs/releases.md>)。
- 技能与工作流文档修改前须公告影响并经[消费面评审](<docs/DOC-CHARTER.md#消费面>)，修改后核对真实消费入口。
- 历史退役先审计引用与恢复路径；不得篡改冻结证据或删除仍在使用的契约、证据、许可证及未解决问题。

## 编辑纪律

- 每次代码变更必须在同一 PR 同步更新本指南，反映当前结构、入口或约束。
- 本指南只留跨目录常驻要点，每点独占一行；不写流水账，不建平行指南或本地叠加层。
- 一事实一家，细节链接已有权威文档；目录调整须修复活引用，不留旧入口壳。
- 实现后独立复审并提交维护者评审；契约/API 变更须维护方同意与独立对抗审查。
- [文档规则](<docs/DOC-CHARTER.md>)管职责与写入权；预算只见[快照](<scripts/doc_budget.snapshot.json>)，现文字数另测，不改门禁/快照掩盖超限。

## 基线与性能

- 唯一主基线为 30秒/900帧/1080p30 SVG；禁用退役12秒基线，机制探针不能代替主片。
- 准备/核验仅用 `scripts/demo.py prepare/verify`；portable-v2 用公开锁定字体和标准库音频，旧输入不混用，获取与恢复见[Demo](<templates/demo-timeline-svg/README.md#主性能基线>)。
- 固定输入、同质量/环境/后端/配置分冷暖测全链；留身份、哈希与证据，未跑写 UNRUN，小探针不冒充整体提速。
- 正式/区间视频拼接共用内核帧端点生成的 concat 时长；不得以容器推断时长或仅总帧数通过替代帧时钟正确性。
- [PNG 批量校验](<vtx/render/gfx_cli.py>)每调用最多两线程，外层页并发另计；并行不得省略完整字节校验或沿用旧摘要。
