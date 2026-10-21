# Framivra 智能体指南

本文件只收跨目录常驻指令；人类入口是 [README](README.md)，文档职责与写入权见 [文档规则](docs/DOC-CHARTER.md)。不设叠加层，不建 AGENTS.local.md。

## 仓身份

B 站视频产线程序本体权威仓；内核 `vtx/` 永不可关。系统边界与契约见 [ARCHITECTURE](docs/ARCHITECTURE.md)，当前开发与验证入口见 [DEV](docs/DEV.md)。

## 常用命令

- 只读体检：`env VTX_DOCTOR_OFFLINE=1 python3 vt.py doctor`。链接修复先预演：`env python3 vt.py --fix-links --dry`；去掉 `--dry` 是实际写操作。
- 渲染入口：`vtx -t <timeline.json> all`（会写项目产物）；命令与前置条件见 [edit](docs/skill/references/edit.md)。
- 验证：`bash scripts/run_all_tests.sh`；文档门：`bash scripts/doc_gates.sh`。实际执行范围、环境依赖与跳过项必须如实报告，局部通过不等于全量验证。
- 安装见 [README](README.md)；发布与源码冻结见 [releases](docs/releases.md)。

## 写态红线

- **CI 默认禁止擅改**:工作流、门禁脚本、测试选择/分片、通过判据及仓库检查设置均属 CI。只有对必要性、影响与验证/回滚方案有十足把握，且**修改前向用户说明并申请、获得明确批准**后，方可在批准范围内调整；未获批准一律不动。不得以修复失败、提速或让检查变绿为由，删减/跳过测试、放宽门禁或绕过必需检查；应先定位并修复业务代码，确需改 CI 则停下申请。
- 不手改 `docs/catalog.md` 生成区；项目 `cache/`、`out/` 是运行时产物，不作源件维护。
- 历史可从当前树退役，但不得篡改冻结证据或删除仍被消费的契约、证据、许可证、未解决问题；审计与恢复边界见 [历史与证据](docs/DOC-CHARTER.md#历史与证据)。
- `docs/skill/` 与 `templates/**` 文档修改前须公告影响并经消费面评审；修改后核对真实消费入口，见 [消费面](docs/DOC-CHARTER.md#消费面)。
- 版本三源 `VERSION`、`pyproject.toml`、`vtx/__init__.py` 改一必三。
- 示例默认零写态；确需写操作须明确警示。不得把 `--help`、语法检查或预演冒充业务执行。

## 编辑纪律

- 一事实一家，新内容先找已有 home；目录调整必须修复活引用，不留平行说明或旧入口壳。
- `app.json`、`plugin.json` 或 `plugins/registry.json` 改动必须重注 catalog 并过文档门，命令见 [单一事实来源](docs/DOC-CHARTER.md#单一事实来源)。
- 文档预算数字只在 `scripts/doc_budget.snapshot.json`；不得改快照或门禁来掩盖超限，当前字数须另行实测。
- 操作路由：[SKILL](docs/skill/SKILL.md)；工作流：[templates](templates/README.md)；组件：[catalog](docs/catalog.md)；缓存语义：[cache-provenance](docs/cache-provenance.md)。
