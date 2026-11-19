# DEV · 开发者入口

> 视频制作从 [Skill](skill/SKILL.md) 开始；开发按 [贡献指南](../CONTRIBUTING.md) 验证与提交。

## 从一个行为开始

从 Issue 的验收条件或失败用例出发，在下表找专题，或从[架构职责表](ARCHITECTURE.md#11-执行与状态)找实现。按符号、命令名或协议字段定位定义、调用方和相关测试，先读命中的函数、夹具与断言，不必通读整个目录。能说明「谁决定这个行为、谁依赖它、怎样区分改前与改后」，就可以动手；答不出哪一项，就沿那条线继续查。

例如，侧栏只改显示时，先看对应视图和显示测试；若改的是“松手后显示哪个版本”，就要沿[编辑事务](reference/timeline-api.md)从宿主请求查到内核回执，再查宿主如何采用结果。跨进程消费者不一定出现在导入关系里。缩小阅读范围不等于缩减必需验证。

怀疑职责混杂时，在 Issue 或 PR 留一个可核对的例子：原以为只需改哪里、实际为何必须联动另一处、哪条调用链或回归能说明问题。若只是入口难找，补指路即可；不凭文件长度或引用数决定拆分。

## 专题入口

| 需要解决的问题 | 当前入口 |
|---|---|
| 安装与项目验收 | [安装/更新](guides/installation.md)、[公开 Demo](../templates/demo-timeline-svg/README.md#主性能基线)、[代理诊断](guides/agent-diagnostics.md)、[验收包](guides/project-acceptance.md)、[Blender 分轨审计](../apps/blender-bridge/README.md#分轨审计包-audit_bundlepy人机协作交付门) |
| 时间线数据与编辑迁移 | [只读 API、显式事务首切片与未迁移写入口](reference/timeline-api.md) |
| 身份、缓存与执行结果 | [缓存/回执](cache-provenance.md)、[执行事件](reference/execution-events.md)、[资源预算](reference/resource-budget.md)、[插件兼容](reference/plugin-core-compatibility.md) |
| 媒体实现 | [Canvas 导出](skill/references/gfx.md#canvas-导出与静态跨度)、[响度终链](skill/references/edit.md#音频工程build_audio-内置)、[编码色彩边界](reference/known-issues.md#色彩编码)、[Golden 证据](cache-provenance.md#golden-evidence) |
| 组件与未解决问题 | [组件目录](catalog.md)、[已知问题](reference/known-issues.md)；具体命令和输入限制归各组件 README |
| DSH 外宿主适配器开发 | [侧栏适配器开发](../dsh-sidebar/DEVELOPMENT.md)；其 README 维护接线与验证边界 |
| 决策与实验依据 | [DSH 单帧预览研究](reports/development-history.md#dsh-单帧预览研究363)、[HTML/CDP 冷暖研究](reports/development-history.md#htmlcdp-工作流研究346)、[历史基准](reports/development-history.md#一性能基准)、[路线资格与证据](reports/development-history.md)、[MG 验收边界](../templates/mg-styles/README.md#验收边界)、[NAVFREE](reports/development-history.md#navfree-免导航截图判负双杀方案-v2-阶段-2) / [BeginFrame 判负](reports/development-history.md#beginframe) |

## 依赖与发布原则

默认使用 Python 标准库；真实需求可引入成熟第三方库，但须评估维护成本、平台兼容、性能收益、回落与测试语义，保全 CDP 协议、校验镜像和文件事务确定性。显式启用组件后的失败语义不随依赖选择弱化。具体依赖只由 [README 依赖矩阵](../README.md#依赖矩阵) 维护；Python 包声明与 ffmpeg/ffprobe/浏览器/运行后端需求分别核对，`dependencies = []` 不代表所有路径仅需标准库。

版本同步、冻结门与支持范围见 [源码稳定基线](releases.md)；doctor 的“无 tag 不告警”是 [管理入口](ARCHITECTURE.md#51-vtpy-统一入口) 的独立语义，不免除发行冻结要求。CI/检查设置、消费面、生成区及文档写入授权遵守 [AGENTS](../AGENTS.md) 与 [文档宪章](DOC-CHARTER.md)。

## CI 回归覆盖

| 执行面 | 覆盖与失败语义 |
|---|---|
| 普通 PR / main push | [CI](../.github/workflows/ci.yml) 在 Python 3.9 / 3.13 运行[显式冒烟模块](../scripts/ci_pr.py)，并保留文档与静态门；冒烟只需标准库，不安装 FFmpeg、浏览器或第三方 Python 包，不执行真实媒体集成 |
| 定时 / 手动 Python 检查 | 独立的 [Integration regression](../.github/workflows/integration.yml) 保留全部受支持 Python 版本的默认全量 unittest 发现，并追加音乐 pytest 独立子进程（安装测试用 pytest/NumPy，分档由组件管理）；本地无参统一入口一致，新模块进入对应发现，是否纳入冒烟需评审 |
| 定时 / 手动 FFmpeg / Chrome 严格清单 | 按完整模块并行分组；无分组参数仍执行全清单，新模块确定性归默认组，原断言、工具预检、零 skip 约束不变；含 FrameSource 的组及无分组命令显式预检 Chrome，无环境凭证旧 PNG 必须真实重渲，不补签 |
| 定时 / 手动 Windows 安装检查 | 同属 Integration regression，保留安装安全、安装端到端与定向原生清理；不替代本地原生渲染验收 |

冒烟检查拒绝缺失模块、空集合、发现错误及任何 skip；各媒体严格 job 任一失败/skip 也非零退出，不用单组冒充汇总、不串行拼接媒体组。默认全量 runner 的可选工具/平台 skip 保持其报告语义，**skip 不是成功执行**。

普通 CI 不证明真实渲染、媒体工具链、Windows 安装或全量跨版本回归通过；大规模 CLI 参数组合及其余 app/plugin/example 回归也不全在冒烟集。这是明确接受的覆盖降级，不得把冒烟通过说成全量或未运行路径通过。两个 workflow 使用独立并发组，普通事件不创建集成检查的 skip 占位；专用验收 workflow 仍按各自触发条件运行。分支保护须独立核对实际所需检查；退役名称或未运行检查不能伪造为已通过，迁移设置须另行授权，读取受限时状态就是未知。workflow 不修改仓库保护或权限。

性能以实际 hosted 结果拆分报告排队、安装、测试与总耗时；不承诺消除排队/机器波动，不靠取消任务、降低超时或旧报告宣称提速完成。测试执行入口和 opt-in 变量只在 [CONTRIBUTING](../CONTRIBUTING.md#测试入口) 维护。

## 支持与验证边界

- **Windows 原生内核**：x64 Python 与 Chrome/Edge 覆盖安装自检、CDP/CLI、FFmpeg 全链、缓存与 gc/clean。环境指纹绑定浏览器发行目录、系统库/驱动、系统/用户字体及注册表、主机与启动边界；未知输入拒绝复用，不承诺跨机器像素等价，保守扫描成本高于 Linux。Chrome 由 Job Object 在宿主强杀后回收整树，事务使用字节范围锁，删除以拒绝写入/改名共享的祖先句柄与按句柄删除并拒绝 junction 祖先。原生安全由本地 Windows 测试、中文/空格/# 路径单双分段/冷温缓存/严格 QC/解码/黑场/真峰值由本地 [E2E 运行器](../tests/runners/run_e2e.py) 的显式 `--scope capability` 及[独立验证器](../tests/runners/verify_e2e.py) 的同一 scope 验收；定时 / 手动 CI windows-install 覆盖安装安全/安装端到端及两项定向原生清理，并非 Windows 全套。**Windows CEF 与模型侧车生命周期不在该验收内**，平台专用测试必须分别报告。
  当前 Windows 不支持主片 SVG；[Windows SVG 内部验收](reference/windows-svg.md)不授生产资格。capability 只验独立 HTML/字幕/普通媒体机制，不授完整 30 秒或发布资格，也不自动 skip 主片后标完整通过。默认 `--scope full` 仍要求主片 900 帧与机制 180 帧两者全链；run/verify scope 必须一致，当前候选是否通过只以实际证据判断。

  以下为写操作（生成媒体与缓存，调用本地 FFmpeg/浏览器）。从仓根在 PowerShell 执行，替换为已有工具路径；全新 RUN 不得复用：

  ```powershell
  $RUN = Join-Path $env:TEMP ("framivra-capability-" + [guid]::NewGuid().ToString("N"))
  $FFMPEG = "C:\path\to\ffmpeg.exe"; $CHROME = "C:\path\to\chrome.exe"
  python tests/runners/run_e2e.py --repo "$PWD" --output "$RUN" --ffmpeg "$FFMPEG" --chrome "$CHROME" --scope capability
  if ($LASTEXITCODE -ne 0) { throw "capability run failed" }
  python tests/runners/verify_e2e.py "$RUN" --ffmpeg "$FFMPEG" --scope capability
  if ($LASTEXITCODE -ne 0) { throw "capability verification failed" }
  ```
- **模型与素材限制**：ASR 兼容探测 `.venv/Scripts/`，不代表 Windows qwen-asr 解释器已验收，手动路线见 [ASR](../apps/asr/README.md)。`apps/aroll/trim_takes.py` 按 `raw[44:]` 读取既定 float32 WAV，不是通用 WAV 解析器。
- **帧率时钟**：允许值和别名只由 `vtx/pipeline/constraints.py` 定义；FFmpeg、帧元数据与缓存使用精确有理数身份，音频用绝对帧端点到 48kHz 采样的映射，不以四舍五入的每帧采样数累乘。
- **声明依赖**：未列入 cache_dependencies 的外链变化不保证触发重渲，必须按工程检查声明，见 [缓存语义](cache-provenance.md)。

实现存在、实验结论、实际验收是不同事实：线性调度与可选 Electron 复现仍有实现；路线资格、性能、帧数/PTS 正确性必须以对应输入和环境的实际证据判断，不能由文档整理升级。历史数据、裁定与停用实验从[研究记录](reports/development-history.md)追溯，不构成当前吞吐 SLA。
