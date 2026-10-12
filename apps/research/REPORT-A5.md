# A5：B 站调研静默失败修复回执

## 结论与方案

采用 **b（诚实失败）+ c（保持有限重试、超时并补中文提示）**，不做自动更换指纹、验证码求解或 WBI 对抗。原请求已经有 UA、Referer、buvid cookie 与 WBI 签名；本机问题不是「忘了签名」，而是 **HTTP 200、code=0 的响应中只有 `data.v_voucher` 风控挑战，没有结果列表**。原代码把缺失列表与 B 站非零错误码都折叠成空列表，最终写空结果且退出 0，这是确定的程序根因。

指纹/时序可能影响挑战，但无法从少量请求确定 B 站内部风控规则。现有签名能够在诊断中返回真实结果，不能据此保证换 cookie 即可稳定恢复。本回执属于指定任务验收记录，不新增操作权威文档层。

## 端点与本机诊断证据

诊断在本机通过标准库 urllib 完成（单次超时 15 秒），直连显式 `ProxyHandler({})`；代理仅用于比较，显式设为 `http://127.0.0.1:7890`。cookie/签名均只在诊断进程内使用，未轮换或改写缓存；不记录 cookie 秘密。日期上下文：2026-10-11，Asia/Shanghai。

原代码：[请求层与验证](bilibili.py#L40-L88)、[搜索参数](bilibili.py#L123-L133)、[WBI 签名](bilibili.py#L114-L119)。

- 密钥：[nav API](https://api.bilibili.com/x/web-interface/nav)，无参数。未登录响应 `code=-101` 仍含可用 `wbi_img`，不是搜索失败原因。
- 搜索：[WBI search/type API](https://api.bilibili.com/x/web-interface/wbi/search/type)，参数为 `search_type=video`、`keyword=人工智能`、`page=1`、`page_size=30`、`order=`、`tids=0`，再加入 `wts`、`w_rid`。
- 请求已有桌面 Chrome UA、`Referer: https://www.bilibili.com` 和缓存 buvid3/buvid4。`-n 3` 原本仅限制评论数，本次不改变它的语义。

| 实测请求 | HTTP / body 摘要 | 判断 |
|---|---|---|
| 直连 nav，原缓存 cookie | 200；`{"code":-101,"message":"账号未登录",..."wbi_img":{...},"ip_region":"CN"}` | 密钥正常 |
| 直连签名搜索，原缓存 cookie | 200；`{"code":0,"message":"OK","ttl":1,"data":{"v_voucher":"voucher_36fd94f3-6b85-44e1-87d3-e70cf29a03a5"}}` | 风控挑战，不是真空列表 |
| 代理 nav / 原缓存签名搜索 | nav 同样有密钥；搜索 200、code=0、仅 `v_voucher` | 本机代理未解除挑战；不代表已经切换出口 |
| 直连旧的未签名 search/type | 200；HTML 标题 `出错啦! - aba.bilibili.com` | 旧接口不能作为可靠回退 |
| 临时随机 buvid，签名搜索（仅诊断） | 200、code=0、`numResults=1000`、30 条；首条 `BV1rnHQ6KEuV`，标题「已知的最早的人工智能」 | 签名/参数可工作，但 cookie 更换不构成稳定修复证据 |
| [官方 buvid API](https://api.bilibili.com/x/frontend/finger/spi) 签发指纹后的连续两次签名搜索（仅诊断） | 官方签发 200、code=0、含 b_3/b_4；首次搜索仅 `v_voucher`，第二次搜索 30 条 | 同一官方指纹仍出现挑战，不引入脆弱自动换指纹 |
| 原缓存 cookie 再次直连 | 200、code=0、仅 `v_voucher` | 缓存路径稳定复现 |

本次真实网络没有观测到 `-352/-412`；这些常见风控码由离线 mock 测试覆盖，不冒充实测。

## 改动清单与六件覆盖

- [bilibili.py](bilibili.py)：统一检测 HTTP 异常、非 JSON、非零 B 站错误码、`v_voucher` 与缺失结果列表；nav 的合法未登录密钥响应例外。搜索阻断退出 1；正常空列表重试耗尽退出 2，并打印「真无结果」。不写伪成功空文件；保留成功条目结构、页进度、合并去重格式。stats/comments 共用该层，同样不再吞风控；正常无评论仍允许空列表。
- [bilibili.py](bilibili.py#L159-L190)：`--retry` 保持旧实现的总尝试次数语义（含首次），至少 1；异常响应也纳入搜索有限重试。单次 HTTP 15 秒，连接错误的网络层最多 3 次。重试日志带端点、关键词/页号（搜索阶段）、中文超时/代理建议；不强制设置代理。
- [bilibili.py](bilibili.py#L90-L102)：支持 `BILIBILI_COOKIE` 提供合法用户 cookie，优先于缓存；不自动轮换缓存指纹，也不回写环境提供的 cookie。
- [github_repos.py](github_repos.py)：没有共用 B 站请求层；发现自身捕获异常后仍写空数组并退出 0。改为 HTTP/网络中文诊断、缺失 items 视为异常、失败非零退出，全失败不写空文件，部分成功明确标注；正常 `items=[]` 明示真无结果。
- [fetch-site-assets.py](fetch-site-assets.py)：独立请求层，原主页面失败已有异常，但 CSS/字体失败被捕获后仍「完成」并退出 0。保留已采集部分资产，失败时明确「部分完成」且退出 1，网络异常增加上下文、超时与代理建议；保持原 30 秒超时，不加重试。
- [score_topic.py](score_topic.py)：仅本地数据读取，不联网、不涉及该问题，未改。
- [dump-computed-styles.mjs](dump-computed-styles.mjs)、[scroll-shoot.mjs](scroll-shoot.mjs)：不共用 HTTP API 层，fetch 仅轮询本机 CDP（最多 30 次后抛错），目标页面由浏览器导航；未发现同款「API 风控码当空列表」路径，未改。浏览器导航失败/加载验证不在此次最小修复范围。
- [README.md](README.md)：补充失败退出语义、合法 cookie 与离线测试入口。
- [tests/test_requests.py](tests/test_requests.py)、[tests/__init__.py](tests/__init__.py)：新增 21 项纯 mock 回归，无网络与文件写入。

所有编辑均在本应用目录；未修改 docs、版本三源或 manifest，未提交 git，未安装第三方依赖。开工时仓根已有未跟踪的采集输出，未触碰。

## 验证证据

### 帮助与原命令实测

```bash
env python3 apps/research/bilibili.py --help
# HELP_EXIT=0

env python3 apps/research/bilibili.py search "人工智能" --pages 1 -n 3
# SEARCH_EXIT=1
```

真实 stderr（下面展示首次；同样诊断分别打印了 2/3、3/3，耗尽后退出 1）：

```text
[人工智能] 第1页 尝试 1/3：B 站请求异常 [https://api.bilibili.com/x/web-interface/wbi/search/type]：HTTP 200，B 站错误码 0；请求被风控挑战拦截（v_voucher），并非真无结果。可能原因：风控、WBI 校验、cookie 或接口变更；建议换网络、用 BILIBILI_COOKIE 提供合法 cookie、稍后重试；网络不可达可检查代理并设置 HTTPS_PROXY（如 http://127.0.0.1:7890）。
```

该运行没有「第1页 0 条」「✓ 0 条」或新采集结果文件。

### 离线 unittest

在应用目录运行：

```bash
env PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v
```

```text
Ran 21 tests in 0.009s
OK
```

覆盖：`-352/-412 → main 返回 1 + HTTP/错误码/中文建议`；`code=0 + v_voucher`；HTTP 412；HTML 200；缺失列表；正常空列表返回 2；保持 retry 次数；合法 nav 未登录密钥；超时最多三次且有代理建议；网络不可达；正常成功格式；重试后恢复；stats/comments 共用风控检测；合法无评论；cookie 环境变量不写缓存；GitHub 失败/空列表区别；资产主页面/子资源失败非零。

### doctor 与差异检查

```bash
env PYTHONDONTWRITEBYTECODE=1 VTX_DOCTOR_OFFLINE=1 python3 vt.py doctor
git diff --check -- apps/research
node --check apps/research/dump-computed-styles.mjs
node --check apps/research/scroll-shoot.mjs
```

```text
✓ [app:builtin] research｜health ✓（exit 0｜usage: bilibili.py [-h] [-o OUT] [--pages PAGES] [--retry RETRY] [-n N]）｜requires ✓
doctor 结论：✅ 通过
```

doctor、diff 与两件 mjs 语法检查均退出 0。

## 风险与边界

1. 本方案保证诚实失败，不承诺解除 B 站风控；合法 cookie/不同网络仍可能被挑战。没有删除指纹缓存，没有代理服务配置修改。
2. 搜索遇到一页最终失败或真空页会立即非零返回，不写本轮结果；前面页已采集但未落盘的数据会丢弃，已有输出文件不覆写。选择失败闭合，避免不完整数据被误当成功。
3. 默认搜索单页连接超时最坏可触发 `3 × --retry` 次 HTTP 尝试（另有 nav），每次 15 秒加有限退避；用户可以把 retry 显式调大，但不存在无限循环。超时是 urllib 单次网络操作超时，不是整批采集绝对墙钟时限。
4. 已实测 Linux；代码使用 Python 标准库，无平台专属超时命令。Windows/macOS 未真机实测，超时和不可达分支以离线 mock 覆盖。
5. GitHub 和资产工具失败退出码由 0 改成非零，依赖旧「部分失败也算成功」行为的调用者应处理失败状态；样式工具仍保留部分产物，GitHub 正常空结果仍退出 0，但有明确提示。
6. cookie 属敏感信息，应只通过安全环境变量或原缓存私下提供，不写仓库、报告或日志；本次新增诊断不输出请求 cookie、签名或 voucher 值。
