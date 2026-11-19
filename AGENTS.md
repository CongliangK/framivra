# Framivra 智能体指南

Framivra 把时间线和素材变成帧精确的音视频，并负责缓存与 QC。先看下面的权限边界，再按任务选一个入口；不用通读所有专题。

## 写态红线

- **CI**（工作流、门禁、选测/分片、判据、检查设置）：改前说明必要性、影响、验证和回滚，取得用户明确批准才可限范围修改。不删减或跳测、不松门禁或绕必需检查刷绿。
- **工程与缓存**：视频项目/素材放仓外；cache/out 是产物，不是源件。示例默认零写态，写操作须警示。[空间清理](docs/reference/storage-api.md)归内核，须独立确认停写，不向模型开放删除。
- **契约与消费面**：契约/API 变更须维护方同意及独立对抗审查；[技能/工作流](docs/DOC-CHARTER.md#消费面)改前公告并评审，改后核对入口。
- **生成与冻结**：不手改 catalog 生成区；manifest/registry 改动须[重注并过门](docs/DOC-CHARTER.md#单一事实来源)。[VERSION](VERSION)、[pyproject](pyproject.toml)、[内核版本](vtx/__init__.py)改一必三，遵守[冻结规则](docs/releases.md)。退役先审计引用与恢复路径，不篡改冻结证据，不删活契约、证据、许可或未结问题。

## 项目与入口

| 你要判断什么？ | 从哪里读 |
|---|---|
| 这个行为由谁决定，谁依赖它，怎么验证？ | 从[开发入口](docs/DEV.md#从一个行为开始)按行为定位，再查[架构职责表](docs/ARCHITECTURE.md#11-执行与状态)。`vtx/` 内核永不可关；应用产素材，插件扩时间线；已启用的依赖或失败不能忽略。已知不可用声明先于 probe 拒绝，源声明在 prepare/range 裸缓存前同拒。 |
| 侧栏操作会不会写盘？模型工具能不能代写？ | [用户操作与平台资格](dsh-sidebar/README.md)、[手动编辑与回执](dsh-sidebar/DEVELOPMENT.md#manual-editing-writes-require-explicit-authorization)、[界面设计](dsh-sidebar/DESIGN.md)。侧栏独立发行；模型工具只读，人工写入走[内核事务](docs/reference/timeline-api.md)。预览、成片播放不授写入或合成资格。 |
| 改音画、缓存或平台实现会影响什么？ | [区间渲染](docs/ARCHITECTURE.md#区间渲染)、[帧率与时钟](docs/ARCHITECTURE.md#帧率与时钟)、[混音增益](docs/ARCHITECTURE.md#12-响度终链)、[正式阶段回执](docs/cache-provenance.md#正式阶段身份与增益)、[BGM 引用与身份](docs/cache-provenance.md#音乐组件输入身份)。浏览器排错看[启动与收尾](docs/reference/known-issues.md#浏览器启动与收尾)。 |
| 安装成功是否等于可以使用？ | [安装/更新](docs/guides/installation.md)和[依赖矩阵](README.md#依赖矩阵)：Python ≥3.9、模型默认关，空 Python 依赖不等于只需标准库。安装登记不替代验收，Windows 主进程不提权；IndexTTS 的 probe/gen 共用解释器发现，发现不等于获许可推理。 |
| 制作而不是改代码？ | 先查[制作技能](docs/skill/SKILL.md)，再按需选[工作流](templates/README.md)或[组件](docs/catalog.md)。音乐用[原生验收](apps/bgm/README.md#macos-原生验收)：隔离配置与原创输入走完全链，基础、可选引擎、人工听审分别记账。 |

## 常用命令

- 离线体检：`env VTX_DOCTOR_OFFLINE=1 python3 vt.py doctor`；联网会 fetch 并写入。链接预演：`python3 vt.py --fix-links --dry`；实际修复前核对目标与权限。
- 渲染：`python3 vtx/vtx.py -t <timeline.json> all` 会写缓存和产物，前置见[交付链](docs/skill/references/edit.md)。
- 本机 Linux / DSH 全量、侧栏 DOM 与真实工具回归必须走[本机限额入口](CONTRIBUTING.md#本机-linux先隔离再运行)。组 OOM 后停止重跑，由人工排查并确认；复验显式使用事故时的限额。其他平台、局部选测见[贡献指南](CONTRIBUTING.md#测试入口)。
- 文档必跑 `bash scripts/doc_gates.sh`。报告 PASS/FAIL/SKIP/UNRUN，冒烟与语法检查不等于业务或全量验收。

## 编辑纪律

- 入口或跨目录常驻约束改变才改本指南。其余按[写入与评审](docs/DOC-CHARTER.md#写入与评审)归位；一事实一家，迁移修活链接，不留旧壳。现文字数另测，不改门禁或未经维护者授权调整[预算快照](scripts/doc_budget.snapshot.json)。
- 只提交必要改动，优先扩展已有高价值回归，不堆重复 mock 或机械源码字符串断言。实现后独立复审并交维护者。
- 一次性脚本、截图、日志和大体积验收产物放仓外，PR 引用结果；仅有长期消费者且经维护者同意的最小夹具或证据可入仓。

## 基线与性能

- 主基线仅用[30 秒 / 900 帧 / 1080p30 SVG](templates/demo-timeline-svg/README.md)：`scripts/demo.py prepare/verify`，禁混旧输入或使用退役 12 秒基线。[锁定运行时源](templates/demo-timeline-svg/benchmark/portable-v2.json)按[属性规则](.gitattributes)以 LF 检出、验原始字节。预检、探针或[可编辑 Demo](templates/demo-workbench/README.md)不替代主片、安装或发布验收。
- 同输入、质量、环境、后端和配置分冷暖测全链，保留身份、哈希和证据。[事件区间](docs/reference/execution-events.md)不相加冒充墙钟。[QC 采样](docs/cache-provenance.md#qc-段像素免检)先统一可表示 RGB 色域，按统一矩阵/范围采样，变更后重验 QC。
- [Windows SVG](docs/reference/windows-svg.md) 生产身份未证即在 PATH/缓存查找前拒绝，不回退内部验收；[SVG 缓存与调度](docs/cache-provenance.md#产物身份)和 [CEF 帧证明](experiments/gfx_cef_worker/README.md)各有独立验证边界，不以探针授权交付。
