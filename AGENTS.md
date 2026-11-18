# Framivra 智能体指南

## 项目与入口

- timeline.json/素材→帧精确视频/混音/QC/缓存；[内核](<vtx/>)永不可关
- [应用](<apps/>)产素材/辅助结果；[插件](<plugins/>)扩时间线，启用不忽略依赖/失败；已知不可用声明先于 probe 拒绝，源声明在 prepare/range 裸缓存前同拒。
- [侧栏](<dsh-sidebar/README.md>)独立发行：完整合成非单页，预览共用整数播放头、成片联动非合成证书；单行工具栏，帧步进与轨道高度在时间线；轨高/专注视图只改显示；宽屏文件列表独立滚动；单帧拒绝其他插件，官方 BGM 仅作无声画面引用、不执行；HTML信任≠沙箱；启用直接剪辑后松手走内核预检/CAS 保存，非法保留原位；吸附/闭合同轨空隙不改时基或他轨，侧栏以结构化视图审阅。
- 侧栏按宿主与内核能力分开报告读、整帧与事务；Windows 不把静态协议声明或成片播放当成内核帧合成、写入资格。
- [空间管理](<docs/reference/storage-api.md>)删除权归内核；清理须独立确认停写，不向模型开放。
- macOS授权见接口；目录fd≠proc-fd Path；单帧须全输入前后复核；环境文件批查仅复用本次目录前缀并前后核验，仍重枚举/哈希/查链接；Linux缺RECORD只认元数据所属RPM清单。
- 制作读[技能](<docs/skill/SKILL.md>)；范本[工作流](<templates/README.md>)、组件[目录](<docs/catalog.md>)。
- 开发读[架构](<docs/ARCHITECTURE.md>)/[开发](<docs/DEV.md>)；API/事务见[接口](<docs/reference/timeline-api.md>)。
- 环境见[依赖](<README.md#依赖矩阵>)：Python≥3.9，工具按路径配置；模型默认关；空依赖≠仅需标准库。
- [安装](<docs/guides/installation.md>)：独立venv/固定身份缓存/内核锁，验收后ready；macOS venv 链接解释器、先核验已装完整 FFmpeg keg；Win主进程不提权，厂商包UAC；DSH显式连接；登记≠验收。
- 安装验收：BGM发现不可依赖POSIX，目录发布对部分Windows绝对路径兼容混合斜杠、不改运行数据；GPU用原生锁；Windows并发内存门须实测且探测失败时拒派；hue素材先规范RGB色域，不松QC。
- BGM v2 轨 `$ref` 装载/merge 仅接受工程内相对路径：Windows 根相对、带盘符及设备路径先拒绝，仍须 realpath 越界检查；POSIX 反斜杠为普通文件名字符。插件签名与 asset_dependencies 共用标准库筛选及静态路径检查，仅展开 v2 直接纯非空字符串引用；以工程词法父目录为基准拒绝越界及非普通文件，按 loader 先 normpath 后 realpath 取文件，缺失记 MISSING；沿用 loader 根目录 base 的严格前缀限制；不证明原始文本逐组件不越界，检查与打开非原子，不能防 TOCTOU 或工程内硬链接指向外部内容。
- 侧栏沿用[宿主主题](<dsh-sidebar/DESIGN.md>)，切换不重建输入或触发写态；保留黑舞台/轨道语义色，失效不留可用成功提示；高级定位浮层展开时工具栏暂停吸顶，折叠恢复。
- [音乐原生验收](<apps/bgm/README.md#macos-原生验收>)用隔离配置与原创输入走完整链；基础、可选引擎、人工听审分别记账，缺资产不算通过。
- IndexTTS 的 `probe` 和 `gen` 共用原生解释器发现；找到解释器不代表许可允许推理。
- [CDP 启动诊断](<vtx/render/gfx_render.py>)区分 DevTools HTTP 就绪前进程退出与页面 CDP 握手后初始化 `Page.enable` 超时；退出只报告有界状态，不泄露 argv/profile，超时不证明页面域状态或 renderer/token 根因，不改 strict/CLI 回退。
- [Windows 浏览器监管](<vtx/runtime/windows_browser.py>)固定绝对 `.exe`，创建即进私有 Job（Win10+/Server 2016+），仅继承指定 stdio；双管合计有界，先终止本方 Job，期限内确认活跃进程归零才算收尾成功；根退出、双 EOF 或关句柄兜底均不作证明。CDP 收尾未证时保留主错误及 profile，不回落 CLI。

## 常用命令

- 离线体检：`env VTX_DOCTOR_OFFLINE=1 python3 vt.py doctor`；联网会fetch 写入。
- 链接预演：`python3 vt.py --fix-links --dry`；实改前核对目标/权限。
- 渲染：`python3 vtx/vtx.py -t <timeline.json> all`（写缓存/产物）；前置见[交付链](<docs/skill/references/edit.md>)。
- 测试：本机 Linux / DSH 全量、侧栏 DOM 与真实工具回归一律用[本机限额入口](<CONTRIBUTING.md#本机-linux先隔离再运行>)；组 OOM 后停止重跑，由人工排查并确认。其他平台、局部选测见[贡献指南](<CONTRIBUTING.md#测试入口>)。
- 文档必跑：`bash scripts/doc_gates.sh`；报告 PASS/FAIL/SKIP/UNRUN；冒烟、语法检查不等于业务或全量验证。

## 写态红线

- CI（工作流、门禁、选测/分片、判据、检查设置）改前说明必要性/影响及验证/回滚，获用户明确批准后限范围修改。
- 未授权不动CI；禁删减/跳测、松门禁或绕必需检查刷绿，先修根因。
- 视频项目/素材放仓外；cache/out为产物非源件；示例默认零写态，写操作警示。
- 不手改 catalog 生成区；manifest/registry 改动须[重注并过门](<docs/DOC-CHARTER.md#单一事实来源>)。
- [VERSION](<VERSION>)、[pyproject](<pyproject.toml>)、[内核版本](<vtx/__init__.py>)改一必三；遵循[冻结规则](<docs/releases.md>)。
- 技能/工作流改前公告并经[消费面评审](<docs/DOC-CHARTER.md#消费面>)，改后核对入口。
- 退役先审计引用/恢复路径；不篡改冻结证据，不删活契约、证据、许可或问题。

## 编辑纪律

- 代码 PR 须同步本指南，记录结构、入口或跨目录约束；[QC](<vtx/inspection/qc.py>) 先统一可表示 RGB 色域，再按统一矩阵/范围采样，变更后重验 QC。
- 仅留跨目录常驻要点，一点一行；无流水账/平行指南。
- PR 只提交必要业务改动及少量高价值回归，优先扩展已有测试；避免重复 mock、机械源码字符串断言或为数量/覆盖率堆测试。
- 一次性复现/诊断脚本、截图、原始日志及大体积验收产物默认放仓外，PR 用简短结果与引用说明；仅有明确长期消费者、且经维护者同意的最小夹具或证据可入仓，不借精简删除有效回归、冻结证据或许可证。
- 一事实一家；迁移修活引用、链权威文档，不留旧壳。
- 实现后独立复审并交维护者；契约/API 变更须维护方同意及独立对抗审查。
- 遵守[文档规则](<docs/DOC-CHARTER.md>)；预算数字仅由[预算快照](<scripts/doc_budget.snapshot.json>)维护，调整须维护者明确授权并保留真实度量；现文另测，不改门禁或伪造快照掩盖超限。

## 基线与性能

- 唯一主基线：30秒/900帧/1080p30 SVG；禁用退役12秒基线，探针不替主片。
- Windows SVG 先校验原始项目相对声明，再用 `/` 序列化依赖并集并复核解析路径；不放行 Windows SVG 运行时。
- [Windows Fontconfig 检查](<vtx/render/gfx_svg_win_fontconfig.py>)只在独立进程用固定官方 DLL 快照查询单个已知字体 face；仅供内部验证，不证明闭包、渲染或产物资格。
- [Windows SVG 快照](<vtx/render/gfx_svg_win_snapshot.py>)先用目录句柄拒绝重解析和物理重叠，再核验独占包树；核验入口不启动进程。[内部验收](<vtx/render/gfx_svg_win_shell.py>)仅以固定官方 `.com` 验内置几何或四行文本单帧；锁字体私有暂存并核验文本像素，期限内未证 Job 结算则不读 PNG、不删操作目录；不证明加载域、缓存或发布资格。
- [SVG shell](<vtx/render/gfx_svg_stage.py>) 即使退出成功也拒收明确的 Fontconfig error / Pango-CRITICAL，不发布完成凭据；该诊断只报告固定短错误，不回显日志，普通警告不拒收。
- portable-v2 基线仅用 `scripts/demo.py prepare/verify`；锁字体与标准库音频，禁混旧输入；预检不授渲染资格，见[SVG 基线](<templates/demo-timeline-svg/README.md>)。
- [可编辑主 Demo](<templates/demo-workbench/README.md>)同源生成 SVG/HTML，默认由主时间线直引乐谱 JSON，不提交音频；CDP、CEF 与 Inkscape 验收不代替安装、冻结与性能基线。
- [portable-v2 锁定运行时源](<templates/demo-timeline-svg/benchmark/portable-v2.json>)须以 LF 检出（含 Windows）；[属性规则](<.gitattributes>)仅补齐既有 Python 规则未覆盖的锁成员，核验仍按原始字节。
- 同输入/质量/环境/后端/配置分冷暖测全链；留身份/哈希/证据；未测UNRUN，探针非整体提速；SVG有界调度/串行回退见[缓存](<docs/cache-provenance.md#产物身份>)。
- [事件](<docs/reference/execution-events.md>)跨阶段回执绑定实际嵌套任务，区间不相加冒充墙钟。
- 帧率由[constraints](<vtx/pipeline/constraints.py>)统一；FFmpeg用精确时基，音频按绝对帧端点取采样；正式/区间concat共用端点，不以容器或总帧数替代时钟判据。
- [CEF 帧证明](<experiments/gfx_cef_worker/README.md>)的拒收/非推进回调只能计账，不得推进 signed 阶段、冻结候选或写输出；推进须 owned 快照及对应绘制后 JS 围栏，保留完整见证/字节 veto；修复须重编并绑定 worker 二进制身份。
