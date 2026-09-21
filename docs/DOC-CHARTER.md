# video_tools 文档架构宪章(DOC-CHARTER)v1.1(定稿)

> 升版链:v0.9(草案)→v1.0 会签稿(作废,未吸收 harness 拦签)→v0.9.1(重送会签,吸收 harness-mgr 两文件拦签点:skills/** 三红线——消费面条款/知会义务/写入权归属,与 §7a 示例命令写态冲突 `VTX_DOCTOR_OFFLINE=1` 零写态形态,源码实位 vt.py L363-377,kernel R-12 §九禁令在案)→**v1.0(定稿)**:kernel 会签单(§2/§1/§4 契约语义边界,含三条解释注记)+harness 会签单(§7c 三红线/§7a 独立直核/readlink BAD=0)双签齐,凭单合入。
> **7b 处置说明**(harness 随单备注):原 §7c 重排为 §7b,编号连续,§0.1/§5/§5.23/§5.24 引用同步更新;两会签单所引 7c 即本稿 7b,语义不变。
> 会签:kernel-mgr「同意」(契约语义边界 §2/§1 条款 3-7/§4 条款 16-22,2026-09-16)+harness-mgr「同意」(挂载消费面 §1.5/1.7/7/7a/7b/5.23/5.24/6.25,2026-09-16);会签单正本落公司 docs-people-mgr 桌 records/。
> 事实基础:全部现状数据采用《2026-09-16-R02-A1-文档层级现状清单.md》实测结论,本文(v1.0 部分)不新增事实。
> **升版链补记(v1.1,2026-09-21)**:R-22 保鲜机制条款 27~35 增补(§7,纯增补,现行条款 1~26 一字不改);CEO 终签正本=公司仓 desks/ceo/records/2026-09-21-终签-DOC-CHARTER-v1.1.md;三会签=kernel-mgr(28/33-⑤)+qa-mgr(29/33/34)+harness-mgr(30~32);宪法余量以建议条款入 35(候 boss,未生效)。

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

## 7. 保鲜机制条款(R-22 增补,2026-09-21)

> 升版链补记(v1.1):本节条款 27~35 纯增补,现行条款 1~26 一字不改;法源=R-22 总验收+CEO 终签正本(公司仓 desks/ceo/records/2026-09-21-终签-DOC-CHARTER-v1.1.md);三会签=kernel-mgr(28/33-⑤)+qa-mgr(29/33/34)+harness-mgr(30~32);起草=R22-L3-AUTHOR,审核=docs-people-mgr。各条机检判据见条内;极限诚实声明(C4)随条载明,门不覆盖处不假装覆盖。

27. **归属唯一(one home per fact)**:每一事实只有一个家=其职责所属层;他处只准链接,链接须可解析。同一事实在两处以上重复表述=可审查缺陷。叙事时态清理:当前状态是唯一合法时态,历史演变留给 commit/台账/ERRATUM,禁 previously/now/no longer/renamed 式叙述。口诀:bug→postmortem/台账,理由→决策记录,操作→references,契约→ARCHITECTURE 与 schema,安装态事实→catalog 区,数值政策→预算源件,常备指令→根 README 与 SKILL 路由。机检:重复锚检查(leaf_3,登记词族对照);语义全量查重=review(docs-people 散文抽查)——门只保已登记词族。
28. **数值型政策只许一处 home(硬条款)**:数值型政策(字数/条数/编制/配额/版本类上限与同值约束)只允许一处 home;散文只许链接+提取命令,复述数值=可审查缺陷。预算唯一 home=公司仓 tools/doc_budget.json(单一来源,单位逐件显式 word|char 禁字节,口径命令随件);产品仓运行时锚定快照生效,快照↔源件锚定对账漂移即红;源件修订=经理核+CEO 备案,P8 换锚登记(旧锚/新锚/归因/申请人席/经理核可五字段)随修订强制,登记须载实测依据。机检:预算门 leaf_2(`python3 scripts/verify_doc_budget.py`)+锚核对(`--anchor-check tools/doc_budget.json`,公司根执行);golden 源件篡改档。
29. **冻结文书封印(分级)**:冻结类文书实行分级封印——A 类(签发令/封盘件/总验收单)整件 SHA-256;B 类 append 族(台账/回执册/批台账)以节哈希封冻结节(合法追加不触节锚),**节哈希细则与过渡态人工核归辖 qa-mgr**;C 类机制件不入封印面,走普通 review+负例交叉;A 类封印面清单闭合程序=封印席申报+qa 复核双载。封印程序:签发令生效后 T+1 工作日内,封印席对 A 类逐件双录(收档哈希=宣称哈希),侧车 manifest 自身内容哈希载入签发令追注行作外部锚;换锚=换锚登记五字段+合法追注重录;封印哈希对原样字节流零规范化。三角色不同席:封印席(文书产出方)/verifier 归辖 qa-mgr/外部锚(签发令)。机检:封印 verifier(归辖 qa-mgr,候 M4 机制件落地;落地前过渡态=双录留痕+qa 人工核,本条以纪律条款效力先行——极限诚实声明)。
30. **挂载消费面清单表(唯一 home)**:挂载消费面清单表=消费面构成事实的唯一 home:六件挂载+skill/ 全件+skills/** 文档面逐件钉名(路径+字节+锚定日),登记件落 docs-people-mgr 桌;件增减与表不符即红。**「字节」列显式标注为落表时点快照列,非对账维度(harness 会签意见采纳)——对账维度=路径与存在性。**消费面语义事实仍归各写入权方(§5/§7b),本表只保构成完整。机检:清单表对账(候 M4 机制件落地;过渡=readlink 六件 BAD=0 手动锚,§7a 同款)。
31. **变更联动判据**:①挂载消费面(skill/ 或 skills/** 文档面)任何 commit 必携:挂载复核输出(六件 BAD=0)+知会 harness-mgr 登记行;②组件 manifest(app.json/plugin.json)或 plugins/registry.json 任何变更 commit 必携 catalog 重注(`python3 scripts/gen_manifest_catalog.py --inject docs/catalog.md`),否则文档门红=变更联动失职;③登记行与重注随 commit 三证,commit 面抽检归 qa-mgr 段一。机检:②已落地(catalog_check leaf_5;先例=M2 卡批 a 案,denoise 2.0.0 在途改被 leaf_5 逮住,2026-09-20);①=抽检+本地纪律。
32. **挂载通道极限声明**:挂载消费面保鲜通道保:链路完好(六件 BAD=0)+构成完整(清单表对账)+知会留痕;不保:消费文本语义质量(=harness-mgr 会签 review)与版本缓冲(G5 结构缺口,harness-mgr 辖)。
33. **机器不覆盖的判断清单**:以下五项为机器不覆盖的判断,配 review 分工,门禁不得宣示覆盖:①散文语义准确性(与源码行为一致性)=docs-people-mgr 散文抽查;②该不该写/该不该归档=经理审核挑刺制;③教程前置顺序与读者起点适配=docs-people-mgr;④挂载消费文本语义质量=harness-mgr 会签;⑤契约语义正确性(schema 应然值、判据语义)=kernel-mgr。清单变更随第 25 条修订流程。机检:清单存在性(「机器不覆盖」字样在册)+内容质量=review(双载,防字样门被读作语义门)。
34. **gate 极限诚实声明**:每个文档门 verifier 落地 commit 必须同 commit 携极限声明行,以「保什么/不保什么」**对偶形态为基准**(qa 会签意见采纳),声明=存在性〔门〕+内容〔review〕双载;聚合门 leaf 清单变更(增 leaf/扩容)须同 commit 同步头注计数与注册面,三数对账红=门自伤即回炉。机检:声明行 grep≥1(M1~M3 落件逐一在案);三数对账=doc_gates 尾行。
35. **纲领件余量(建议条款,候 boss,未生效)**:宪法、路线图、各 AGENTS.md 一律 ≤2000 字,且常备保留 ≥10% 余量(警戒线=上限×0.9);触警戒=常鸣态登记(计数不扰,缘由入册),超上限=立法修正事项。双态制:boss 批准前本条不生效,以常鸣态登记运行(现势宪法 1996 字符/路线图 1955 字符,均常鸣);批准后转红线态入字数门 blocking。本条系立法面条款,DOC-CHARTER 仅登记机制与状态,不创设宪法义务。

——v1.0 条款 1~26:A3 席增补落盘 2026-09-16(v0.9.1),v1.0 定稿会签 2026-09-16,复核人:L3-A。
——v1.1 条款 27~35 增补落盘 2026-09-21(R-22 M4;CEO 终签正本=公司仓 desks/ceo/records/2026-09-21-终签-DOC-CHARTER-v1.1.md;三会签 kernel/qa/harness;起草 R22-L3-AUTHOR,审核 docs-people-mgr)。
