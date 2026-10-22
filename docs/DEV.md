# DEV · 开发者入口

> 先读 [系统结构与扩展契约](ARCHITECTURE.md)，按 [贡献指南](../CONTRIBUTING.md) 验证与提交。视频制作从 [Skill](skill/SKILL.md) 开始；本页区分当前依赖、实际回归覆盖和未验收边界，不把研究记录当作能力证明。

## 专题入口

| 需要解决的问题 | 当前入口 |
|---|---|
| 安装与项目验收 | [安装/更新](guides/installation.md)、[公开 Demo](guides/demo-fixture.md)、[代理诊断](guides/agent-diagnostics.md)、[验收包](guides/project-acceptance.md)、[Blender 分轨审计](../apps/blender-bridge/README.md#分轨审计包-audit_bundlepy人机协作交付门) |
| 身份、缓存与执行结果 | [缓存/回执](cache-provenance.md)、[执行事件](reference/execution-events.md)、[资源预算](reference/resource-budget.md)、[插件兼容](reference/plugin-core-compatibility.md) |
| 媒体实现 | [Canvas 导出](skill/references/gfx.md#canvas-导出与静态跨度)、[响度终链](skill/references/edit.md#音频工程build_audio-内置)、[编码色彩边界](reference/known-issues.md#色彩编码)、[Golden 证据](cache-provenance.md#golden-evidence) |
| 组件与未解决问题 | [组件目录](catalog.md)、[已知问题](reference/known-issues.md)；具体命令和输入限制归各组件 README |
| 决策与实验依据 | [历史基准](reports/development-history.md#一性能基准)、[路线资格与证据](reports/development-history.md)、[MG 验收边界](../templates/mg-styles/README.md#验收边界)、[NAVFREE](reports/development-history.md#navfree-免导航截图判负双杀方案-v2-阶段-2) / [BeginFrame 判负](reports/development-history.md#beginframe) |

## 依赖与发布原则

默认使用 Python 标准库；真实需求可引入成熟第三方库，但须评估维护成本、平台兼容、性能收益、回落与测试语义，保全 CDP 协议、校验镜像和文件事务确定性。显式启用组件后的失败语义不随依赖选择弱化。具体依赖只由 [README 依赖矩阵](../README.md#依赖矩阵) 维护；Python 包声明与 ffmpeg/ffprobe/浏览器/运行后端需求分别核对，`dependencies = []` 不代表所有路径仅需标准库。

版本同步、冻结门与支持范围见 [源码稳定基线](releases.md)；doctor 的“无 tag 不告警”是 [管理入口](ARCHITECTURE.md#51-vtpy-统一入口) 的独立语义，不免除发行冻结要求。CI/检查设置、消费面、生成区及文档写入授权遵守 [AGENTS](../AGENTS.md) 与 [文档宪章](DOC-CHARTER.md)。

## CI 回归覆盖

| 执行面 | 覆盖与失败语义 |
|---|---|
| 普通 PR / main push 的 Python 检查 | [显式精选模块](../scripts/ci_pr.py)，覆盖 CLI、契约、真实文件系统/进程安全与 CI 反例；各完整模块分组，每组只在一个选定解释器运行一次，include 矩阵不作解释器×分组笛卡尔积；具体配对以 [workflow](../.github/workflows/ci.yml) 为准 |
| 定时 / 手动 Python 检查 | 全部受支持 Python 版本的默认全量发现；本地无参统一入口也保持全量，新模块自动进入全量，是否纳入普通 PR 精选需评审 |
| FFmpeg / Chrome 严格清单 | 按完整模块并行分组；无分组参数仍执行全清单，新模块确定性归默认组，原断言、工具预检、零 skip 约束不变；含 FrameSource 的组及无分组命令显式预检 Chrome，无环境凭证旧 PNG 必须真实重渲，不补签 |
| Windows / 文档与静态门 | 各 job 只证明自己实际执行的检查；Windows 安装 CI 不替代本地原生渲染验收 |

精选检查拒绝缺失模块、空集合、发现错误及任何 skip；各媒体严格 job 任一失败/skip 也非零退出，不用单组冒充汇总、不串行拼接媒体组。默认全量 runner 的可选工具/平台 skip 保持其报告语义，**skip 不是成功执行**。

普通事件不覆盖每个模块×每个解释器，大规模 CLI 参数组合及其余 app/plugin/example 回归也不全在精选集；这是明确接受的跨版本/范围覆盖降级，不得把精选通过说成全量或未运行解释器通过。分支保护须独立核对全部实际执行检查；退役名称或未选组合不能伪造为已通过，迁移设置须另行授权，读取受限时状态就是未知。workflow 不修改仓库保护或权限。

性能以实际 hosted 结果拆分报告排队、安装、测试与总耗时；不承诺消除排队/机器波动，不靠取消任务、降低超时或旧报告宣称提速完成。测试执行入口和 opt-in 变量只在 [CONTRIBUTING](../CONTRIBUTING.md#测试入口) 维护。

## 支持与验证边界

- **Windows 原生内核**：x64 Python 与 Chrome/Edge 覆盖安装自检、CDP/CLI、FFmpeg 全链、缓存与 gc/clean。环境指纹绑定浏览器发行目录、系统库/驱动、系统/用户字体及注册表、主机与启动边界；未知输入拒绝复用，不承诺跨机器像素等价，保守扫描成本高于 Linux。Chrome 由 Job Object 在宿主强杀后回收整树，事务使用字节范围锁，删除以拒绝写入/改名共享的祖先句柄与按句柄删除并拒绝 junction 祖先。原生安全由本地 Windows 测试、中文/空格/# 路径单双分段/冷温缓存/严格 QC/解码/黑场/真峰值由本地 [合成 E2E](../tests/runners/run_e2e.py) 验收；默认 CI windows-install 只验安装。**Windows CEF 与模型侧车生命周期不在该验收内**，平台专用测试必须分别报告。
- **模型与素材限制**：ASR 兼容探测 `.venv/Scripts/`，不代表 Windows qwen-asr 解释器已验收，手动路线见 [ASR](../apps/asr/README.md)。`apps/aroll/trim_takes.py` 按 `raw[44:]` 读取既定 float32 WAV，不是通用 WAV 解析器。
- **桥接 fps 身份**：constraints 接受数值等值的 `30.0`，Ctx 归一为 int；json2blend 的只读上下文保留原文 fps，而 gfx_key 纳入 fps 文本。不能以“非 int 已拒绝”排除差异；该输入的缓存复用须单独核对。
- **声明依赖**：未列入 cache_dependencies 的外链变化不保证触发重渲，必须按工程检查声明，见 [缓存语义](cache-provenance.md)。

实现存在、实验结论、实际验收是不同事实：线性调度与可选 Electron 复现仍有实现；路线资格、性能、帧数/PTS 正确性必须以对应输入和环境的实际证据判断，不能由文档整理升级。历史数据、裁定与停用实验从[研究记录](reports/development-history.md)追溯，不构成当前吞吐 SLA。
