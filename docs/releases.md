# 源码稳定基线

Release 冻结确切源码提交，不捆绑 Python、ffmpeg、浏览器、模型权重或二进制运行时。版本唯一声明在 VERSION，pyproject 与包内回退版本同步。发行使用 annotated tag `vX.Y.Z`；候选使用 `vX.Y.Z-rc.N`，VERSION 包含同一去前缀版本。tag 不得移动；发现缺陷发布新版本。RC 是 prerelease，绝不更新 latest。未完成冻结的候选不构成稳定承诺。

## 安装选择与信任

两平台引导器的 `FRAMIVRA_REF` 同义：默认 stable 查询本仓公开非预发行 latest，固定 annotated tag 与 peeled commit；可显式指定发行 tag，或用 main 选择开发源码。稳定解析、网络、tag 身份失败均中止，不回落 main。网络仓的显式 tag 须存在同名公开 Release，允许测试 RC。明确指定的绝对路径本地仓是离线候选验收信任边界，可使用私有 annotated tag。安装身份记录在克隆的 .git/framivra-release.json。引导脚本本身经 HTTPS 获取，克隆方式依赖 Git 来源，不声称 provenance 身份验证；下载资产安装器保留严格 provenance 验证，免 gh 的长期改进归 #94，不阻塞源码冻结门。

## 冻结验收与发布

维护者先提交源码，再在仓库外全新目录运行 `scripts/release/release_acceptance.py`（参数见下列只读帮助）。`--candidate` 为清洁 HEAD 创建仅存在于隔离副本的私有 tag，可在公开 tag 创建之前验收；证据保存在仓外，最终公开 tag 必须指向同一提交。变更源码后必须重跑，不能移植旧提交证据。

```bash
env python3 scripts/release/release_acceptance.py --help
env python3 scripts/release/release_github.py --help
```

验收入口调用真实安装引导及隔离 HOME 六件挂载验证（不代表用户既有挂载健康）、唯一 30 秒 Demo 准备/输入核验、SVG 全片渲染与 strict QC、900 帧 1080p30 完整解码，以及独立机制探针的双 HTML 各 60 帧真实 CDP、字幕和普通媒体覆盖、HTML 样式局部修改及区间 preview、无关缓存复用与新键产生、缺源拒绝和损坏缓存重建、两个独立 acceptance 包（freeze/2 的 public_demo / capability_probe，各有独立 manifest 与 source_root）校验及各自输入/产物篡改负例、源码分发许可证审计。原宣传工程 timeline 不改；独立包输入副本补全 900 SVG 帧依赖声明，并额外执行 `all --until mux` → `qc --strict`（不含 finish）；两个包均回读 live source，视觉 unknown 与依赖闭包限制不升级。完整原始日志与验收包仅保留本地。公开 public-freeze-summary.json（schema/2）分别记录 public_demo=svg-sequence 与 capability_probe=cdp 的实际后端资格，为字段白名单摘要：版本/tag/commit、核心资产完整哈希、固定门状态、工具版本、支持与未知范围、本地 freeze 摘要及本页复现指针；不含环境、命令、账户、路径或日志，不声称第三方可仅凭摘要重放完整证据。摘要随发行签名与下载校验，不加入核心校验和以避免自我哈希循环；证据固定 commit、tag、资产摘要和逐门文件摘要。发行器拒绝缺门、未知门、失败/unknown/skipped 的 required 门、日志被改、提交或资产不匹配。

发布事务依次为 prepare（完整草稿资产并下载校验）、sign-release workflow（必须 dispatch 在候选 tag ref，固定 digest，签名草稿资产）、verify（严格身份和证据验证）、publish。publish 在全部资产通过预期 ref/commit 的 keyless provenance 验证后才解除 draft；失败保留草稿，不改变旧稳定版。外部证据可证明字节和执行结果一致，不能证明生产者诚实；仍须独立审查与真实环境验收。package 的 visual_content=unknown 必须保留，不能伪装为全面视听通过。

## 稳定支持范围

首稳定资格限定经过最终候选实测的 Linux、CPU 编码、30 秒 SVG 作品、独立 CDP 机制探针与内核 CLI。具体工具版本和通过结果以该候选证据为准；本文件不预先宣称候选通过。macOS/Windows 引导器保持同义实现，但未经对应实机验证，不列为已验证平台。CEF、Electron、GPU、可选模型应用、外部素材和人工视听质量均不在首基线稳定资格内。许可证审计的来源 warnings 保留，不把完整性检查当作全面法律清关。未满足 required 门禁止发布；发布资产不把实验能力升级为稳定承诺。
