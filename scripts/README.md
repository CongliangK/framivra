# scripts：仓库工具与入口

从仓库根目录执行。产品命令直接使用 apps/plugins 的实现，不提供旧路径转发层。

## 按职责查找

| 用途 | 入口与边界 |
|---|---|
| 安装 | `install.sh`、`install_windows.ps1` 是克隆引导；`install.py` 是独立资产安装器。参数先看 `--help`；安装会写入目标目录。 |
| 新建项目 | `new_project.sh` 使用 `templates/gfx-starter.html`；仅用于全新项目。 |
| 主宣传 Demo / 性能源 | `demo.py` 准备/核验唯一 30 秒输入，内部复用原 `build_demo.js`，渲染直接使用产品 `all`；[唯一源基线与用法](../templates/demo-timeline-svg/README.md#主性能基线)。 |
| 发布 | `release/release_github.py` 执行发布事务；`release/verify_release.py` 核验身份；`release/release_acceptance.py` 在隔离冻结源码上验收。真实发布需要授权。 |
| 图形基线与证据 | `gfx/gfx_golden.py` 是命令入口，`gfx/gfx_evidence.py` 是验证库；使用说明见 [图形证据](../docs/cache-provenance.md#golden-evidence)。 |
| 许可政策 | `policy/model_license_policy.py` 被 app/worker 消费；`policy/distribution_license_audit.py` 审计分发归档，不属于测试夹具。 |
| 生成源件 | `gen_manifest_catalog.py` 管理 [组件目录](../docs/catalog.md) 的生成区；`generate_placeholder_media.py` 默认显示计划，`--write` 会重建媒体，须复核许可清单中的生成器与媒体哈希。 |
| 文档门 | `doc_gates.sh` 聚合 `docs/` 下七个叶；`doc_budget.snapshot.json` 是预算事实 home。门绿不代表完整命令或媒体效果已验证。 |
| 测试与 CI | `run_all_tests.sh` 是聚合入口；`ci_checks.py`、`ci_pr.py`、`ci_partition.py` 与策略文件管理既定检查。归组不改变选测、skip 或退出语义。 |

## 产品命令的唯一实现

| 职责 | 目录 |
|---|---|
| 音频同步与重打包 | [apps/sync](../apps/sync/) |
| 降噪与静态闪避 | [plugins/denoise](../plugins/denoise/)、[plugins/duck-static](../plugins/duck-static/) |
| 调研与网页采集 | [apps/research](../apps/research/) |
| 封面与 4K 发布 | [apps/cover](../apps/cover/)、[apps/publish](../apps/publish/) |
| 内容检查 | [apps/gates](../apps/gates/) |

`audio/normalize_project_audio.py` 是既有 WAV 维护工具，不是正式链的追加步骤；参数、写态与回执风险统一见 [音频操作说明](../docs/skill/references/edit.md#既有-wav-维护)。

新增文件先确定 owner：产品能力进入内核或所属组件；工作流参数、配方、资产进入模板；正式回归及验收夹具进入 [tests](../tests/)。仓根不设置独立 examples；模板内部 examples 是参数资产，不凭名称删除。临时实验须说明状态、输入、产物和复现责任。

目录迁移同步脚本互调、仓根定位、测试布局、CI 和活文档。生成区标记与历史证据不做全仓字符串替换；媒体生成器和 catalog 生成器保持其身份路径。安装引导、脚手架与聚合门是实际入口，不是转发兼容层。
