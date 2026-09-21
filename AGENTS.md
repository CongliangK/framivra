# AGENTS.md(video_tools 智能体指南)

> 读者=编码与执行智能体;人类入口=README.md。本文件只收跨目录常驻指令,事实一律指针(home 指针制,见 H 节);无叠加层,禁建 AGENTS.local.md。

## 仓身份
B 站视频产线程序本体权威仓:内核 `vtx/`(timeline.json 契约+帧精确渲染 CLI+混音响度终链+内容寻址缓存),永不可关(宪法信条)。文档四层 SKILL/ARCHITECTURE/README/docs,层规则唯一 home=docs/DOC-CHARTER.md。

## 常用命令
- 统一入口:`python3 vt.py doctor|init|--fix-links`。doctor 例行跑用 `env VTX_DOCTOR_OFFLINE=1`(零写态);`--fix-links` 去 --dry 即实写,经 harness-mgr 辖域流程(DOC-CHARTER §7a)。
- 渲染:`vtx -t <timeline.json> all`(分阶段 validate/prepare/gfx/video/audio/mux/qc;子命令全集=skill/references/runbook.md「命令正字法」节)。
- 门禁:`bash scripts/run_all_tests.sh`(集成需 VTX_TEST_CHROME/VTX_TEST_FFMPEG);`bash scripts/doc_gates.sh`(文档门聚合器,leaf 清单见脚本头注册面,任一 FAIL 总非零)。
- 安装:`curl -fsSL https://ruochongliang.top/dl/video-tools/install.sh | bash -s --`(参数唯一 home=scripts/install.sh --help;人面安装行=README.md)。

## 写态红线
- 生成区禁手改:docs/catalog.md 生成区(变更走 `python3 scripts/gen_manifest_catalog.py` 重注);项目 cache/ 与 out/ 运行时产物(.gitignore 在册)。
- docs/plans* 三目录只读历史层(唯一活文件例外见 DOC-CHARTER §19)。
- skill/ 与 skills/** 文档修订即改写挂载消费面,落笔前知会 harness-mgr(DOC-CHARTER §5/§7b)。
- 版本三源(VERSION/pyproject.toml/vtx/__init__.py)改一必三,doc_gates version_sync leaf 守护。
- 文档化命令零写态书写(--dry/离线旗标;DOC-CHARTER §21);docs 域命令须入 bash 栅栏方受冒烟守护。

## 编辑纪律
- 新建文档先读 DOC-CHARTER 定层,四层之外不设第五层。
- manifest(app.json/plugin.json)变更后必跑 catalog 重注并过 doc_gates 全绿。
- 文档预算与字数门槛数值唯一 home=doc_budget 源件(公司仓 tools/doc_budget.json);本文件与任何散文不复述预算数字。

## H home 指针(一事实一家,禁复制)
- 文档层规则与写入权→docs/DOC-CHARTER.md
- 契约权威(v2 冻结件)→docs/ARCHITECTURE.md
- 开发纪律/演进史/基准→docs/DEV.md
- 组件清单→docs/catalog.md;缓存与回执语义→docs/cache-provenance.md
- 操作路由→skill/SKILL.md;工作流包→skills/tech-talk/;人类面→README.md
