# video_tools 文档架构宪章(DOC-CHARTER)v1.0(定稿)

> 升版链:v0.9(草案)→v1.0 会签稿(作废,未吸收 harness 拦签)→v0.9.1(重送会签,吸收 harness-mgr 两文件拦签点:skills/** 三红线——消费面条款/知会义务/写入权归属,与 §7a 示例命令写态冲突 `VTX_DOCTOR_OFFLINE=1` 零写态形态,源码实位 vt.py L363-377,kernel R-12 §九禁令在案)→**v1.0(本定稿)**:kernel 会签单(§2/§1/§4 契约语义边界,含三条解释注记)+harness 会签单(§7c 三红线/§7a 独立直核/readlink BAD=0)双签齐,凭单合入。
> **7b 处置说明**(harness 随单备注):原 §7c 重排为 §7b,编号连续,§0.1/§5/§5.23/§5.24 引用同步更新;两会签单所引 7c 即本稿 7b,语义不变。
> 会签:kernel-mgr「同意」(契约语义边界 §2/§1 条款 3-7/§4 条款 16-22,2026-09-16)+harness-mgr「同意」(挂载消费面 §1.5/1.7/7/7a/7b/5.23/5.24/6.25,2026-09-16);会签单正本落公司 docs-people-mgr 桌 records/。
> 事实基础:全部现状数据采用《2026-09-16-R02-A1-文档层级现状清单.md》实测结论,本文不新增事实。

## 0. 总则

1. 本宪章划定 video_tools 产品仓文档四层(SKILL / ARCHITECTURE / README / docs)的职责、读者、写入权与命名约定;四层之外不设第五层。skills/** 文档面不另设层(仍非第五层),其挂载消费面/知会义务/写入权规则见第 7b 条,随 SKILL 层条款约束。
2. 各条为硬约束;现状与条文冲突时以条文为目标态,按第 6 节流程修订,不溯及既往归档件。

## 1. SKILL 层(操作路由层)

3. 职责:操作路由层——SKILL.md 路由索引+references 机制文档+assets 模板,回答「接到任务先读哪份 reference、用哪块模板」。
4. 读者:执行席/接棒子智能体(技能消费者),按路由「只读这一份就开工」。
5. 写入权:本层及 skills/** 文档面(见第 7b 条)修订即改写挂载消费面,任何改动落笔前须先知会 harness-mgr(挂载链辖方)。
6. 命名约定:SKILL.md 大写固定名(技能注册约定);references 单词小写(edit/gfx/runbook);assets 小写 kebab(lower-third/title-card)。
7. 挂载消费面实况(A1 实测):`~/.agents/skills/video/` 下六件符号链接,名字逐一为 SKILL.md、assets、references、scripts、skills、vtx,全部指向本仓。其中 SKILL.md、assets、references 与 skills/** 文档面直接构成执行席消费文本——本层修订即改写执行席所见内容。每次修订合入后以挂载复核命令(见第 7a 条操作示例)复核六件链接完好;docs/ 不在挂载面,治辖文档不经 live skill 通道对外暴露。
7a. 操作示例(本宪章自身文档化命令,按第 21 条以 bash 栅栏书写,属冒烟面活条目;**零写态形态**):

```bash
env VTX_DOCTOR_OFFLINE=1 python3 vt.py doctor  # 挂载/组件/版本体检(零写态:跳过 fetch,vt.py L363-377;裸 doctor 会 git fetch 写 .git,禁止示范与例行裸跑,kernel R-12 §九禁令在案)
env python3 vt.py --fix-links --dry            # 挂载链接重建预演(去 --dry 方为实际重建,属写操作,经 harness-mgr 辖域流程)
```

7b. **skills/** 文档面条款(挂载消费面维度)**:六件挂载中的 skills 链接指向本仓 `skills/`(现 tech-talk 工作流包:SKILL.md+references/**+assets/**),其文档与 skill/ 层文档同为执行席 live 消费文本,适用以下三条红线——①本条即其挂载消费面条款,与 §7 同等约束,层属判定=不作第五层、随 SKILL 层消费面条款约束;②对该面的任何文档修订,落笔前须知会 harness-mgr(与第 5 条同责);③其**写入权归 plugins-mgr**(boss 立法 2026-09-16,ledger 批①「skills/ 归 plugins-mgr」),破除无主态;其冒烟扫描面门口健康由 plugins-mgr 按 R-02 认领函口径认领(C5 外因破门接单)。本条修订本身涉挂载消费面,亦须会签 harness-mgr。

## 2. ARCHITECTURE 层(契约权威层)

8. 职责:契约权威层——设计冻结件,承载 v2 拍板方案全文;修改须过对抗审查,不接受实现过程中单方面修订。
9. 读者:开发部门(v2 迁移/实现执行席与审查席)。
10. 写入权:协议定义归 kernel-mgr;docs-people 部只做勘误落笔与叙事一致性维护,不动契约语义。
11. 命名约定:固定单文件 docs/ARCHITECTURE.md,不拆分、不增设平行件。

## 3. README 层(门面与快速开始层)

12. 职责:门面与快速开始层——「本仓是什么+clone 即用零安装」一页,附四层架构与路由。
13. 读者:clone 新接手者(人类)。
14. 写入权:docs-people-mgr 辖域;其中「每件自包含含 tests」表述关联 R-11,由 plugins-mgr 随 tests 补齐兑现,本层不改该表述。
15. 命名约定:根 README.md 固定名。

## 4. docs 层(专题与历史层)

16. 职责:专题与历史层——治辖专题文档(DEV/audio-audit/cache-provenance/canvas-export/post-loudness/workflow-review)与计划归档(plans*)。
17. 读者:开发者与审计。
18. 写入权:docs-people-mgr 辖域;plans* 为只读历史层,适用第 19 条。
19. 只读历史层条款:docs/plans、docs/plans-2026-09-11、docs/plans-v2 三目录只读,归档后不再新增独立提交;唯一活文件例外=docs/plans-v2/p5-acceptance/KNOWN-ISSUES.md(活登记表,按 R-02 授权回写),其余归档件一经归档不改写。
20. 两口径辨析:docs_smoke.sh 扫描「docs/*.md」系非递归 glob,今日恰好豁免 plans* 三目录——此为「非递归现状」而非「plans* 语义豁免」,两者不是同一件事,不得混用。docs/ 新增子目录默认应入检,确需豁免须显式登记留痕,不得依赖 glob 形态的偶然豁免。
21. 规范条款:docs 域文档化命令须写入 bash 栅栏方可获得冒烟守护;栅栏外命令属盲区,文档不得宣称盲区命令已受守护。**归属口径**(经理审计 2026-09-16):栅栏内命令须以「可冒烟形态」书写(含 python3/bash/env/node 命令词+仓路径形态),且**示范命令一律零写态**(写态命令须显式警示或以 --dry/离线旗标书写);纯系统命令(如 readlink 循环)虽入栅栏亦不属 docs_smoke 冒烟面,其正确性由 C1 校验工具与人工复核承载,文档不得宣称其已受冒烟守护。
22. 命名约定:主题件小写 kebab(audio-audit/cache-provenance/canvas-export/post-loudness/workflow-review);归档件中文+日期后缀(依赖盘点与init方案-2026-09-11.md 式);大写角色名(DEV.md/MERGE_REPORT.md)为存量,不再新增。

## 5. 层间关系

23. 路由顺序:clone 新接手者→README;执行席/接棒子智能体→SKILL 层路由→references;工作流配方→skills/tech-talk(见第 7b 条);契约争议→ARCHITECTURE;专题与历史→docs。
24. 六件挂载(`~/.agents/skills/video`,含 skills/** 文档面)是唯一 live 对外面;skills/** 文档面受第 7b 条约束;docs 层仅在产品仓内,不入挂载消费面。

## 6. 附则:解释权与修订流程

25. 本宪章解释权归 docs-people-mgr;修订经 docs-people-mgr 审核+涉契约语义会签 kernel-mgr+涉挂载消费面(含 skills/** 文档面)会签 harness-mgr,CEO 签发。
26. 定稿落地:会签通过后本稿落产品仓 docs/DOC-CHARTER.md,居 docs/ 顶层,天然处于 docs_smoke「docs/*.md」非递归 glob 扫描面内;§7a 操作示例两条命令(均零写态/预演形态)即成为冒烟面活条目,随本宪章 commit 一并受守护。

——A3 席增补落盘 2026-09-16(v0.9.1,吸收 harness 拦签两点);复核人:L3-A
