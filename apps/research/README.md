# research · 选题调研采集/评分/样式研究工具（6 件）

v2 应用（P2a 批次 1 自 `scripts/` 六件迁入；manifest 见 `app.json`，兼容 symlink：`scripts/` 下同名件 → 本目录）。R0-R6 调研编排方法论见 `skills/tech-talk/references/research.md`。

## 用途

选题调研期的独立素材产出工具，六件互不依赖：

| 件 | 用途 |
|---|---|
| `bilibili.py` | B 站调研采集器：搜索 / 视频统计 / 热评（WBI 签名、buvid 指纹持久化 `~/.cache/bili-research/`） |
| `github_repos.py` | GitHub 仓库采集：star/活跃度/语言等结构化数据（`GITHUB_TOKEN` 可提升限流） |
| `score_topic.py` | 选题量化评分（0-100 四维模型；`--rubric` 打印人工评分锚点表） |
| `fetch-site-assets.py` | 抓取网站样式资产：外链 CSS 递归 @import + 内联 style 块 + 字体文件 |
| `dump-computed-styles.mjs` | CDP 计算样式采集：滚动触发懒加载后聚合全部可见元素样式统计 |
| `scroll-shoot.mjs` | CDP 滚动截图：逐步滚动触发懒加载逐段截图 |

## 依赖

- Python 3（stdlib urllib，零第三方包）—— 4 个 .py 件
- node（>=18，内置 fetch/WebSocket）+ google-chrome —— 2 个 .mjs 件（CDP 端口 9333/9334，`/tmp/chrome-cdp*` 独立 user-data-dir）
- 网络（bilibili/GitHub/目标站点；本机代理纪律见全局 AGENTS.md）

## 健康检查

`env python3 apps/research/bilibili.py --help` 退出码 0（app.json health.command；mjs 件无 --help 面，冒烟用 `node --check`）。

## 开启步骤

```bash
env python3 apps/research/bilibili.py search "关键词1,关键词2" -o out.json
env python3 apps/research/github_repos.py "query1,query2" --top 10
env python3 apps/research/score_topic.py <research目录>       # 读 supplement.json + 采集件 → topic_score.json
env python3 apps/research/score_topic.py --rubric             # 评分前必读锚点表
env python3 apps/research/fetch-site-assets.py <URL> <输出目录>
node apps/research/dump-computed-styles.mjs <url> <输出.json>
node apps/research/scroll-shoot.mjs <url> <输出前缀>
```
