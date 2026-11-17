# Framivra 智能体指南

## 项目与入口

- timeline.json/素材→帧精确视频/混音/QC/缓存；[内核](<vtx/>)永不可关
- [应用](<apps/>)产素材/辅助结果；[插件](<plugins/>)扩时间线，启用不忽略依赖/失败。
- [侧栏](<dsh-sidebar/README.md>)独立发行：完整合成非单页；生效插件在身份/钩子前拒绝；HTML信任≠沙箱；写态走内核事务，帧率/预览限制不变。
- [空间管理](<docs/reference/storage-api.md>)删除权归内核；清理须独立确认停写，不向模型开放。
- macOS授权见接口；目录fd≠proc-fd Path；单帧复用须全输入前后复核；运行库须复查全成员/链接，Linux缺RECORD仅凭元数据所属RPM清单识别，仍哈希文件。
- 制作读[技能](<docs/skill/SKILL.md>)；范本[工作流](<templates/README.md>)、组件[目录](<docs/catalog.md>)。
- 开发读[架构](<docs/ARCHITECTURE.md>)/[开发](<docs/DEV.md>)；API/事务见[接口](<docs/reference/timeline-api.md>)。
- 环境见[依赖](<README.md#依赖矩阵>)：Python≥3.9，工具按路径配置；模型默认关；空依赖≠仅需标准库。
- [安装](<docs/guides/installation.md>)：独立venv/固定身份缓存/内核锁，验收后ready；Win主进程不提权，厂商包UAC；DSH显式连接；登记≠验收。
- 安装验收：BGM发现不可依赖POSIX，GPU用原生锁；Windows并发内存门须实测且探测失败时拒派；hue素材先规范RGB色域，不松QC。

## 常用命令

- 离线体检：`env VTX_DOCTOR_OFFLINE=1 python3 vt.py doctor`；联网会fetch 写入。
- 链接预演：`python3 vt.py --fix-links --dry`；实改前核对目标/权限。
- 渲染：`python3 vtx/vtx.py -t <timeline.json> all`（写缓存/产物）；前置见[交付链](<docs/skill/references/edit.md>)。
- 测试：`bash scripts/run_all_tests.sh`；局部见[贡献指南](<CONTRIBUTING.md#测试入口>)。
- 文档必跑：`bash scripts/doc_gates.sh`；报告 PASS/FAIL/SKIP/UNRUN；冒烟、语法检查不等于业务或全量验证。

## 写态红线

- CI（工作流、门禁、选测/分片、判据、检查设置）改前说明必要性/影响及验证/回滚，获用户明确批准后限范围修改。
- 未授权不动CI；禁删减/跳测、松门禁或绕必需检查刷绿，先修根因。
- 视频项目/素材放仓外；cache/out为产物非源件；示例默认零写态，写操作警示。
- 不手改 catalog 生成区；manifest/registry 改动须[重注并过门](<docs/DOC-CHARTER.md#单一事实来源>)。
- [VERSION](<VERSION>)、[pyproject](<pyproject.toml>)、[内核版本](<vtx/__init__.py>)改一必三；遵循[冻结规则](<docs/releases.md>)。
- 技能/工作流改前公告并经[消费面评审](<docs/DOC-CHARTER.md#消费面>)，改后核对入口。
- 退役先审计引用/恢复路径；不篡改冻结证据，不删活契约、证据、许可或问题。

## 编辑纪律

- 代码 PR 须同步本指南，记录结构、入口或跨目录约束；QC 采样须统一矩阵与范围，变更后重新验证证书。
- 仅留跨目录常驻要点，一点一行；无流水账/平行指南。
- PR 只提交必要业务改动及少量高价值回归，优先扩展已有测试；避免重复 mock、机械源码字符串断言或为数量/覆盖率堆测试。
- 一次性复现/诊断脚本、截图、原始日志及大体积验收产物默认放仓外，PR 用简短结果与引用说明；仅有明确长期消费者、且经维护者同意的最小夹具或证据可入仓，不借精简删除有效回归、冻结证据或许可证。
- 一事实一家；迁移修活引用、链权威文档，不留旧壳。
- 实现后独立复审并交维护者；契约/API 变更须维护方同意及独立对抗审查。
- 遵守[文档规则](<docs/DOC-CHARTER.md>)；预算数字仅由[预算快照](<scripts/doc_budget.snapshot.json>)维护，调整须维护者明确授权并保留真实度量；现文另测，不改门禁或伪造快照掩盖超限。

## 基线与性能

- 唯一主基线：30秒/900帧/1080p30 SVG；禁用退役12秒基线，探针不替主片。
- 准备/核验仅用 `scripts/demo.py prepare/verify`；portable-v2锁字体/标准库音频，禁混旧输入，见[Demo](<templates/demo-timeline-svg/README.md>)。
- 同输入/质量/环境/后端/配置分冷暖测全链；留身份/哈希/证据；未测UNRUN，探针非整体提速；SVG有界调度/串行回退见[缓存](<docs/cache-provenance.md#产物身份>)。
- [事件](<docs/reference/execution-events.md>)跨阶段回执绑定实际嵌套任务，区间不相加冒充墙钟。
- 帧率由[constraints](<vtx/pipeline/constraints.py>)统一；FFmpeg用精确时基，音频按绝对帧端点取采样；正式/区间concat共用端点，不以容器或总帧数替代时钟判据。
