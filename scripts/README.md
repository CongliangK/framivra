# scripts：仓库工具与入口

从仓库根目录执行。产品命令直接使用 apps/plugins 的实现，不提供旧路径转发层。

## 按职责查找

| 用途 | 入口与边界 |
|---|---|
| 受管制作环境 | [`bootstrap.sh`](<bootstrap.sh>) / [`bootstrap.ps1`](<bootstrap.ps1>) 获取同提交 bundle，调用 [`install_source.py`](<install_source.py>)；默认国内优先、固定身份缓存与重跑恢复。参数/写态/平台资格以[安装指南](<../docs/guides/installation.md#受管非-ai-制作环境>)为准，ready 仍需制作验收。 |
| 安装内部模块 | [`install_downloads.py`](<install_downloads.py>) 管理校验/Range 缓存；[`install_native_posix.py`](<install_native_posix.py>) / [`install_native_windows.py`](<install_native_windows.py>) 准备原生工具；[`install_python_packages.py`](<install_python_packages.py>) 固定 wheel 计划；[`install_runtime_fonts.py`](<install_runtime_fonts.py>) 复用冻结 [`demo_fonts.py`](<demo_fonts.py>) 提取器，不改 Demo 锁。不是独立发行认证。 |
| 旧式/资产安装 | [`install.sh`](<install.sh>) / [`install_windows.ps1`](<install_windows.ps1>) 只做克隆与 init；[`install.py`](<install.py>) 是独立资产安装器，不混同受管环境。先看 `--help`；实际安装会写目标目录。 |
| 新建项目 | `new_project.sh DIR [SECS]`（默认 30，ASCII 十进制 ≥7 秒）使用 `templates/gfx-starter.html`；新建/完整替换 unmanaged timeline 经公共 plan/CAS/history，拒绝 managed/坏输入；不是无损初始化或目录级原子事务，详见[新项目写态](../docs/skill/references/edit.md)。 |
| 主 Demo | `demo_workbench.py` 准备可编辑工程，`demo_workbench_acceptance.py` 按实际后端验收原版和编辑版；见[操作与依赖](../templates/demo-workbench/README.md)。 |
| SVG 性能源 | `demo.py` 准备/核验锁定的 30 秒 portable-v2 输入，`fonts` 诊断公开字体来源；见[源基线](../templates/demo-timeline-svg/README.md#主性能基线)。 |
| 发布 | `release/release_github.py` 执行发布事务；`release/verify_release.py` 核验身份；`release/release_acceptance.py` 在隔离冻结源码上验收。真实发布需要授权。 |
| 图形基线与证据 | `gfx/gfx_golden.py` 是命令入口，`gfx/gfx_evidence.py` 是验证库；使用说明见 [图形证据](../docs/cache-provenance.md#golden-evidence)。 |
| CEF 配对性能 | `gfx/bench_cef.py plan/run/report`；只读计划、新目录冷暖全链、JSON/Markdown 报告；[输入与 Mac 交接](../experiments/gfx_cef_worker/README.md#有界调度与-mac-性能交接)。 |
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
