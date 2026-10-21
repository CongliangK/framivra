# Framivra(video_tools 仓)智能体指南

> 读者=编码与执行智能体;人类入口=README.md。本文件只收跨目录常驻指令,事实一律指针(home 指针制,见 H 节);无叠加层,禁建 AGENTS.local.md。

## 仓身份
B 站视频产线程序本体权威仓:内核 `vtx/`(timeline.json 契约+帧精确渲染 CLI+混音响度终链+内容寻址缓存),永不可关(项目信条)。文档四层 SKILL/ARCHITECTURE/README/docs,层规则唯一 home=docs/DOC-CHARTER.md。

## 常用命令
- 统一入口:`python3 vt.py doctor|init|--fix-links`。doctor 例行跑用 `env VTX_DOCTOR_OFFLINE=1`(零写态);`--fix-links` 去 --dry 即实写(写操作,DOC-CHARTER §7a)。
- 渲染:`vtx -t <timeline.json> all`(分阶段 validate/prepare/gfx/video/audio/mux/qc;子命令全集=docs/skill/references/runbook.md「命令正字法」节)。
- 门禁:`bash scripts/run_all_tests.sh`(集成需 VTX_TEST_CHROME/VTX_TEST_FFMPEG);`bash scripts/doc_gates.sh`(文档门聚合器,leaf 清单见脚本头注册面,任一 FAIL 总非零)。
- 安装:一键安装=克隆模式引导(Linux/macOS=scripts/install.sh,Windows=scripts/install_windows.ps1;默认稳定tag/显式版本或main+安全更新+幂等 init);人面安装行→README.md。源码冻结规则 home=docs/releases.md;长期资产安装改进跟踪=#94,参数 home=scripts/install.py --help。

## 写态红线
- **CI 默认禁止擅改**:工作流、门禁脚本、测试选择/分片、通过判据及仓库检查设置均属 CI。只有对必要性、影响与验证/回滚方案有十足把握，且**修改前向用户说明并申请、获得明确批准**后，方可在批准范围内调整；未获批准一律不动。不得以修复失败、提速或让检查变绿为由，删减/跳过测试、放宽门禁或绕过必需检查；应先定位并修复业务代码，确需改 CI 则停下申请。
- 生成区禁手改:docs/catalog.md 生成区(变更走 `python3 scripts/gen_manifest_catalog.py` 重注);项目 cache/ 与 out/ 运行时产物(.gitignore 在册)。
- docs/plans* 三目录只读历史层(唯一活文件例外见 DOC-CHARTER §19)。
- docs/skill/ 与 templates/** 文档修订即改写挂载消费面,落笔前先经消费面评审公告(DOC-CHARTER §5/§7b)。
- 版本三源(VERSION/pyproject.toml/vtx/__init__.py)改一必三,doc_gates version_sync leaf 守护。
- 文档化命令零写态书写(--dry/离线旗标);冒烟扫描面与盲区见 DOC-CHARTER §20/§21。

## 编辑纪律
- 新建文档先读 DOC-CHARTER 定层,四层之外不设第五层。
- manifest(app.json/plugin.json)变更后必跑 catalog 重注并过 doc_gates 全绿。
- 文档预算与字数门槛数值唯一 home=仓内 scripts/doc_budget.snapshot.json 快照;本文件与任何散文不复述预算数字。

## H home 指针(一事实一家,禁复制)
- 文档层规则与写入权→docs/DOC-CHARTER.md
- 契约权威(v2 冻结件)→docs/ARCHITECTURE.md
- 当前开发纪律/依赖原则→docs/DEV.md;演进史与历史基准→docs/reports/development-history.md
- 组件清单→docs/catalog.md;缓存与回执语义→docs/cache-provenance.md
- 创作拆页与分工→docs/skill/references/gfx.md「HTML 创作组织」。
- 操作路由→docs/skill/SKILL.md;工作流包→templates/tech-talk/;人类面→README.md
