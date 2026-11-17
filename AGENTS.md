# Framivra 智能体指南

## 项目与入口

- timeline.json/素材→帧精确视频/混音/QC/缓存；[内核](<vtx/>)永不可关
- [应用](<apps/>)产素材/辅助结果；[插件](<plugins/>)扩时间线，启用不忽略依赖/失败。
- [侧栏](<dsh-sidebar/README.md>)独立发行：预览/引用/显式剪辑/配置；写态走内核事务，帧率/预览限制不变。
- macOS授权见接口；目录fd≠proc-fd Path；单帧复用须操作级全输入前后复核，运行库优化须复查全成员/链接。
- 制作读[技能](<docs/skill/SKILL.md>)；范本[工作流](<templates/README.md>)、组件[目录](<docs/catalog.md>)。
- 开发读[架构](<docs/ARCHITECTURE.md>)/[开发](<docs/DEV.md>)；API/事务见[接口](<docs/reference/timeline-api.md>)。
- 环境见[依赖](<README.md#依赖矩阵>)：Python≥3.9，工具按路径配置；模型默认关；空依赖≠仅需标准库。
- [安装](<docs/guides/installation.md#受管非-ai-制作环境>)：独立venv/固定身份缓存/内核锁，验收后ready；Win主进程不提权，厂商系统包UAC。DSH显式连接；登记≠平台验收。
- 安装验收会运行 BGM 合成；其引擎发现不得依赖 POSIX 专用模块，GPU 互斥须使用平台原生锁。

## 常用命令

- 仓根离线体检：`env VTX_DOCTOR_OFFLINE=1 python3 vt.py doctor`；联网形态会 fetch 写入。
- 链接修复预演：`python3 vt.py --fix-links --dry`；去掉 `--dry` 前核对目标与权限。
- 渲染：`python3 vtx/vtx.py -t <timeline.json> all`（写缓存/产物）；前置见[交付链](<docs/skill/references/edit.md#正式交付链r1-r4-共用>)。
- 全仓测试：`bash scripts/run_all_tests.sh`；局部入口见[贡献指南](<CONTRIBUTING.md#测试入口>)。
- 文档必跑：`bash scripts/doc_gates.sh`；如实报告 PASS/FAIL/SKIP/UNRUN；冒烟、语法检查不等于业务或全量验证。

## 写态红线

- CI（工作流、门禁、选测/分片、判据、检查设置）改前说明必要性/影响及验证/回滚，获用户明确批准后限范围修改。
- 未授权不动 CI；不删减/跳测、放宽门禁或绕必需检查刷绿，先修根因。
- 视频项目/素材放仓外；cache/out 是产物非源件；示例默认零写态，写操作警示。
- 不手改 catalog 生成区；manifest/registry 改动须[重注并过门](<docs/DOC-CHARTER.md#单一事实来源>)。
- [VERSION](<VERSION>)、[pyproject](<pyproject.toml>)、[内核版本](<vtx/__init__.py>)改一必三；遵循[冻结规则](<docs/releases.md>)。
- 技能/工作流文档改前公告影响并经[消费面评审](<docs/DOC-CHARTER.md#消费面>)，改后核对真实入口。
- 退役先审计引用/恢复路径；不篡改冻结证据，不删活契约、证据、许可或问题。

## 编辑纪律

- 代码 PR 同步本指南。
- 仅留跨目录常驻要点，一点一行；无流水账/平行指南。
- 一事实一家；链接权威文档；迁移修活引用，不留旧入口壳。
- 实现后独立复审并交维护者；契约/API 变更须维护方同意及独立对抗审查。
- 遵守[文档规则](<docs/DOC-CHARTER.md>)；[预算快照](<scripts/doc_budget.snapshot.json>)不改，另测现文，不改门禁藏超限。

## 基线与性能

- 唯一主基线：30秒/900帧/1080p30 SVG；禁用退役12秒基线，探针不替主片。
- 准备/核验只用 `scripts/demo.py prepare/verify`；portable-v2 锁字体/标准库音频，旧输入不混用，见[Demo](<templates/demo-timeline-svg/README.md#主性能基线>)。
- 固定输入、同质量/环境/后端/配置分冷暖测全链；留身份、哈希、证据；未跑写 UNRUN，探针不冒充整体提速。
- 正式/区间拼接共用内核帧端点 concat 时长；不以容器或仅总帧数代替帧时钟判据。
