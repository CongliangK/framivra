# 调研（research）· 视频选题调研（多智能体编排）

> **使用者向**：选题调研编排协议与角色契约。索引见 `../SKILL.md`，开发者分析见 `../../docs/DEV.md`。

对任意视频选题执行多源调研，产出标准化 `01-brief.md` + 量化分数。**架构原则：主智能体只做编排与综合，不做一手调研；采集者不评分，评分者只读落盘证据；结论必须经过红队质询。** 索引见 `../SKILL.md`。

## 公平性四规则（违反即返工）

1. **采集与评分分离**：R1-R4（采集/分析）与 R5（评分）必须由不同智能体承担；R5 的派发提示词**只含文件路径，不得包含主智能体的倾向性摘要**。
2. **证据先行**：每个子智能体先把自己的原始数据和结论写入 `research/` 文件，之后才允许读其他角色的产出。原始数据文件落盘后不可修改（只可追加新文件）。
3. **红队必答**：R6 质询员的每条质疑，主编必须在 Brief §11 逐条回应（接受并修改 / 驳回并给理由），不得静默忽略。
4. **锚点约束**：一切人工评分按 `score_topic.py --rubric` 的行为锚点取值（±5），每项必附证据，可被任何角色复核。

## 角色与执行 DAG

```
R0 主编（主智能体）
 ├── R1 B站供给侧 ─┐
 ├── R2 GitHub生态 ├─ 三者并行（互不读对方产出）
 ├── R3 全网需求侧 ─┘
 │      ↓ 汇合
 ├── R4 对标拆解（输入：R1 的竞品 BV 清单）
 │      ↓
 ├── R5 独立评分（输入：仅 research/ 落盘文件）
 │      ↓
 ├── 主编综合 → 01-brief.md 草稿
 ├── R6 红队质询（输入：草稿+分数，任务：论证"为什么不该做"）
 └── 主编逐条回应 → Brief 定稿（§11 争议记录）
```

## 各角色契约（派发提示词必须自包含以下内容）

### R0 主编（你自己）
确认选题 → 设计关键词矩阵（核心词/场景词/竞品词/平台词/英文词各≥1）→ **并行派发 R1/R2/R3**（Agent 工具，run_in_background）→ 汇总后从 R1 的竞品清单选 2-3 个标杆派发 R4 → 派发 R5 → 写 Brief 草稿 → 派发 R6 → 回应质询定稿。主编不得亲自跑采集脚本或填评分。

### R1 B站供给侧采集员
```
工具：python3 <video_tools>/apps/research/bilibili.py
产出：research/bilibili_search.json / bilibili_stats.json / bilibili_comments.json + supply_digest.md
digest 必含：①供给分层表（娱乐层/工具盘点层/场景演示层/空白层，每层代表视频+播放+藏率+投币赞比）
          ②直接竞品 BV 清单（定义：同主题×同形态×同平台）③时效判断 ④相关视频总数与空关键词记录
纪律：相关性过滤规则写明（剔除同名梗/游戏/无关平台），play<500 与 pubdate=1970 剔除并计数
```
### R2 GitHub 生态调研员
```
工具：python3 <video_tools>/apps/research/github_repos.py（GITHUB_TOKEN 可选）
产出：research/github_repos.json + ecosystem.md（现有方案表：stars/最近推送/做不到什么；底座热度；差异化空间结论）
```
### R3 全网需求侧调研员
```
工具：WebSearch / brave-search skill / 联网检索
产出：research/demand.md —— 知乎/论坛痛点【原话+URL】、商业产品功能现状（阉割证据）、
     抖音/小红书信号强度（搜索引擎间接采样，不直连）、YouTube 同题材参照（标题/播放量/切入角度）、受众认知与误解点
```
### R4 对标拆解员
```
输入：R1 的竞品 BV 清单中选 2-3 个 + research/bilibili_comments.json
产出：research/benchmarks.md —— 每标杆四问：hook 怎么写/结构怎么分章/评论区高赞在说什么/它的短板=我的差异；附评论区需求清单
工具补充：python3 <video_tools>/apps/research/bilibili.py comments "BV.." 可再采
```
### R5 独立评分员
```
输入：【仅】research/ 目录下的落盘文件——提示词不得含主智能体的任何结论或情绪倾向
步骤：先 --rubric 读锚点 → 填 supplement.json（模板 <video_tools>/skills/tech-talk/assets/supplement.template.json，每项附证据）→ 跑 score_topic.py
工具：python3 <video_tools>/apps/research/score_topic.py
产出：research/supplement.json + topic_score.json + score_notes.md（评分理由，特别说明不确定项）
```
### R6 红队质询员
```
输入：01-brief.md 草稿 + topic_score.json
任务：尽最大努力论证"这个选题不该做/分数虚高"——找反例（同题材失败视频、需求伪证、竞品近期动态、
     差异化不成立的理由、评分证据链的漏洞），每条质疑附证据或明确标注为推断
产出：research/challenge.md（编号质疑列表）
```

## 产出契约

- `01-brief.md`：复制 `<video_tools>/skills/tech-talk/assets/brief-template.md`（十节+§11 争议记录，顶层章节不得增删）。
- `research/`：所有原始数据 JSON + 各角色 md（supply_digest / ecosystem / demand / benchmarks / score_notes / challenge）。
- 人工拍板项始终保留：切入角度、标题封面、时长——主编只给带推荐的选项。

Brief 定稿后的下一步（脚本 → gfx 动效 → vtx 剪辑渲染）见内核 skill `references/gfx.md` 与 `references/edit.md`（本包 SKILL.md 六阶段④⑤）。

## 已知坑

- B 站新指纹冷启动空结果——脚本已内置重试与指纹持久化（~/.cache/bili-research/）；持续空的关键词是供给空白证据，记录之。
- 子智能体提示词必须自包含（角色契约原文+绝对路径），不得引用对话历史。
- 评论 API 单次仅顶部约 20 条，样本量如实标注。
- R5 与 R6 的产出若与主编判断冲突，以证据强度裁决并在 §11 记录，不得以"主编权威"压过反证。
