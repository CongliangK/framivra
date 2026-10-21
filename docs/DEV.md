# DEV · 开发者向文档

> **开发者入口**：当前依赖原则、回归覆盖、验证边界与专题路由。视频制作从 [内核 Skill](skill/SKILL.md) 开始；历史证据与当前能力分开阅读。

## 专题入口

- 操作：[安装与更新](guides/installation.md)、[公开 Demo](guides/demo-fixture.md)、[代理诊断](guides/agent-diagnostics.md)、[项目验收包](guides/project-acceptance.md)、[分轨审计](../apps/blender-bridge/README.md#分轨审计包-audit_bundlepy人机协作交付门)。
- 接口与结果语义：[缓存/回执](cache-provenance.md)、[Canvas 导出](reference/canvas-export.md)、[执行事件](reference/execution-events.md)、[资源预算](reference/resource-budget.md)、[插件兼容](reference/plugin-core-compatibility.md)、[响度终链](skill/references/edit.md#音频工程build_audio-内置)、[编码色彩](reference/color-encoding.md)。
- 研究与复审：[MG 当前状态与证据](reports/mg-styles/status.md)、[产线复审](reports/workflow-review.md)；[BeginFrame 判负记录](gfx-beginframe.md)与 [Golden 证据协议](gfx-evidence.md)保留固定位置。组件从 [catalog](catalog.md) 找入口，操作机制从 [内核 Skill](skill/SKILL.md) 路由；plans* 是原位只读历史。

## 依赖原则

默认使用 Python 标准库；真实需求出现时可按需引入成熟第三方库，须充分评估维护成本、平台兼容、性能收益以及回落与测试语义。外部 ffmpeg/ffprobe/Chrome 的工具依赖不因此改变。

**决策记录（2026-10-14，[#99](https://github.com/CongliangK/framivra/issues/99)）**：将「零第三方依赖」铁律调整为上述原则，仅定调，不立即引入或替换任何库；`pyproject.toml` 的 `dependencies = []` 保持不变。克隆引导不增加可选依赖硬检查；doctor 对可选能力缺失仅提示的分级不变，渲染核心硬门及显式启用 external 的失败语义不变。

未来候选仅登记备查，不构成本次实施范围：
- `vtx/gfx_render.py` 的 `WSConn`：若替换 WebSocket 实现，保住 `tests/gfx/test_graphics_portability.py` 假 CDP 分支及既有协议测试语义。
- `vtx/stages.py` 的 `validate()`：若改用校验库，保住 `tests/test_fps_constraints.py` 的镜像漂移守护语义。
- `vtx/fstxn.py`：深度绑定确定性与对抗测试语义，不建议替换。
- gfx CDP 后端已使用 numpy，Pillow 为可选 PNG 编解码快路径（具体路径见 `vtx/gfx_render.py` 模块头）；可在 [#94](https://github.com/CongliangK/framivra/issues/94) 发版准备中评估 optional-dependencies 声明，不把它们加入克隆安装硬门。

## CI 回归覆盖

普通 PR 与 main push 的 Python 检查执行 [显式精选模块](../scripts/ci_pr.py)，覆盖 CLI、契约、真实文件系统与进程安全，以及 CI 门禁反例。完整模块在独立 job 中分组执行，每组只在一个选定解释器上运行一次；普通事件使用 include 矩阵，不展开解释器与分组的笛卡尔积，具体配对以 [workflow](../.github/workflows/ci.yml) 为准。每个精选模块仍被执行，但普通事件不覆盖该模块与其他解释器的组合；这是明确接受的跨版本覆盖降级。该检查拒绝缺失模块、空集合、发现错误和任何 skip；成功仅代表精选回归通过，不能宣称全量回归或未运行解释器通过。

既有定时与手动 workflow 执行全部受支持 Python 版本的默认全量发现；本地无参统一入口也保持全量。新增测试模块自动进入全量发现，是否加入普通 PR 精选集需评审。大规模 CLI 参数组合、其余 app/plugin/example 回归等移出普通 PR Python 检查属于明确接受的覆盖降级。FFmpeg、Chrome、Windows 安装和文档/静态门保留各自实际执行；可选平台/工具 skip 仍按默认全量 runner 的原语义报告，不计作执行成功。

FFmpeg 与 Chrome 严格清单分别按完整模块分成并行组，原断言、工具预检与零 skip 约束保持；无分组参数仍执行完整清单。新增模块确定性归入其默认组。包含 FrameSource 的组及无分组命令显式预检 Chrome：无环境凭证的旧 PNG 必须真实重渲，不能补签。每个实际执行 job 直接报告结果，任一测试失败或 skip 仍使其非零退出；不串行拼接媒体组，不用单组结果冒充汇总成功。

检查名迁移：workflow 不创建纯汇总 job `Core regression (all selected versions and groups)`、`FFmpeg CPU integration (no skips)`、`Chrome and pipeline CPU integration (no skips)`。普通事件也不创建未选中的解释器/精选组组合。实际必需检查配置必须独立核实：若分支保护仍要求退役名称，应在另行授权后迁移到全部实际执行检查，不能将其改名伪装、绕过或宣称已同步；读取受限时配置状态就是未知。workflow 本身不修改仓库保护或权限。

CI 提速以实际 hosted 结果为准，分别报告排队、安装、测试与总耗时；精选集不承诺消除排队或媒体任务的机器波动。普通 PR 的目标时限不是强行取消任务或降低超时后声称成功。

## 历史证据

[性能与迭代基准](reports/development-history.md#一性能基准)、[故障分析](reports/development-history.md#二坑位破案详录)与[工具演进](reports/development-history.md#三工具演进史)保存在指定日期、机器、项目与版本的研究记录中，不据旧数字承诺当前性能。路线裁决参见 [Electron 封存](reports/development-history.md#electron-route-status)与 [NAVFREE 判负](reports/development-history.md#navfree-免导航截图判负双杀方案-v2-阶段-2)。

## 四、体检遗留与验证边界

2026-09-11 体检修复记录见 commit 92609f3 / 898870e；以下保留仍可定位的问题与未完成的外部验证，不把旧清单整体当成当前缺陷：

- **可定位 NIT**：`vt.py` 的 imgen_requests 检查仍两次调用 `importlib.util.find_spec("requests")`（零子进程，本地重复探测）；`apps/aroll/trim_takes.py` 仍按 `raw[44:]` 读取固定头部的 float32 WAV，仅适配其既定输入形态，不能当通用 WAV 解析器。原体检的 infra 恒真分支与 imgen 死变量未在本轮逐项复核，不据旧行号断言仍未修。
- **Windows 原生内核验收**：原生 x64 Python 与 Chrome/Edge 支持安装自检、CDP/CLI 图形、FFmpeg 全链、缓存命中及 gc/clean。图形环境指纹绑定浏览器发行目录、系统库/驱动文件、系统与用户字体及注册表、主机与启动边界；未知输入保持拒绝复用，不宣称跨机器像素等价。该保守闭包的扫描成本高于 Linux 口径。Chrome 使用 Job Object，宿主强杀时关闭句柄回收整棵浏览器树；文件事务使用 Windows 字节范围锁；删除使用拒绝写入与改名共享的祖先句柄及按句柄删除，拒绝 junction 祖先。原生安全回归由本地 Windows 原生测试验收，中文/空格/# 路径单双分段、冷温缓存、严格 QC、解码、黑场和真峰值门由本地 [合成 E2E](../tests/run_e2e.py) 验收；默认 CI 的 `windows-install` job 验收安装自检，原生渲染由上述本地验收覆盖。Windows CEF 与模型侧车进程生命周期不由此验收，平台专用测试仍按其实际执行报告。
- **asr 模块 Windows 深度移植未做**：venv 布局（.venv/Scripts/）已兼容探测，但 qwen-asr 解释器在 Windows 的可用性未验证；apps/asr/README.md 手动路线兜底。
- **json2blend fps 类型微差**（实际不可达）：桥侧键函数取 timeline 原文 fps 类型，若手写 30.0 会与 vtx 键分叉——vtx Ctx 已拒非 int fps，故仅理论风险。
- **cache_dependencies 未声明依赖的外链变更不触发重渲**：是否声明须按具体工程核对；这是声明式契约的已知边界，见 [缓存依赖语义](cache-provenance.md)。

### 版本与发布纪律（R-04，2026-09-17）〔出处：R-03/04/05 合并任务书〕

- **版本、冻结门与支持范围**：唯一规则见 [源码稳定基线](releases.md)。VERSION、pyproject 与包内回退版本须同步；annotated tag 固定同一提交。
- **兼容**：doctor「无 tag 不告警」为 §5.1 设计语义；发行冻结门独立执行，不改变该行为。
