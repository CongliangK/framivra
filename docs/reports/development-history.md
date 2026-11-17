# 研究结论与证据索引

当前结构、操作与限制分别见 [ARCHITECTURE](../ARCHITECTURE.md)、[DEV](../DEV.md) 和 [known-issues](../reference/known-issues.md)。本页只留影响路线选择的结论；旧轮次、参数矩阵及故障排查见[固定基准原文](https://github.com/CongliangK/framivra/blob/3d9e6622d5d06eecf55ffbe2075da1fa994f93f5/docs/reports/development-history.md)，不作当前性能或验收证明。

## 未获生产资格的路线

<a id="electron-route-status"></a>

- **Electron：性能 FAIL，封存，不采用。** 2026-10-17 用户终止开发、优化及整片验证；整片未完成、无完整成片，不要求补验。未集成组合 POC 在同机单 worker、三份真实 HTML、1080p、帧 30/60/90、两轮配对下相对新鲜 CEF 耗时比 **1.326569 > 1.25**；不外推合并源码或全片。[PR #175](https://github.com/CongliangK/framivra/pull/175) 保留裁定，[实验目录](../../experiments/gfx_electron_worker/README.md) 仅供复现。

<a id="navfree-免导航截图判负双杀方案-v2-阶段-2"></a>

- **NAVFREE：Golden FAIL 且更慢，封存、不得启用。** 单页 40/40 相同未通过八实例压力验证；真实页耗时为导航的 1.5–4.4 倍。默认导航不变；没有证明截图/动画时钟竞态，也不证明 BeginFrame 必需或足够。

<a id="beginframe"></a>

- **BeginFrame #67：exactness FAIL，正式三轮冷性能 UNRUN，拒绝生产采用。** 同锁定 shell 导航对照 1024 帧中 245 帧不等；149/153 两版反例重现，达到研究停止条件，不证明浏览器内部根因或普遍局限。实际为 SwiftShader；诊断 wall/RSS、小探针及生命周期 smoke 不替代正式验收；缺失的来源/内部归因字段仍为 null。完整协议、限制与证据见[研究原文](https://github.com/CongliangK/framivra/blob/3d9e6622d5d06eecf55ffbe2075da1fa994f93f5/docs/gfx-beginframe.md)，[冻结原型](https://github.com/CongliangK/framivra/tree/f1efe5f688b543ca11902c9538dec02a5c265961/experiments/gfx_beginframe) 可追溯；独审只核哈希，未重跑浏览器/解码。
- **foreignObject 批量导出：全零 Golden FAIL，通道撤销。** blur/渐变差异由 SVG-image 对照复现，不以视觉近似豁免；omitBackground 的历史无 alpha 失败不改判为可用。

## 一、性能基准

以下仅属历史 RTX 4080 / 32C64G，项目、冷热及验证链不同不得横比；完整矩阵、Blender 校验和迭代窗测量在固定基准原文。

| 保留的验收结论 | 边界 |
|---|---|
| 2026-09-11 voice-ime：≤110s 未达；更高并发内存门 FAIL | 增页不等于解决重页瓶颈。 |
| 2026-09-12 / 13：QC 74/75、strict exit 1；bench_p2 全链 240s 门未达 | c4_mini 探针假阴性仍保留；NVENC/x264 不逐像素等价，4K 抽检不等于全帧认证。 |
| 2026-09-19 真实 voice-ime，`6388d6e`：全冷三轮中位 1117.8s，QC76/76、exit0 | **291.4s 真实项目基线作废**；R-07b 仍按同口径三轮中位≤本基线×0.8验收，不称已达。 |

### SVG 完整冷链优化（#345 / #386）

2026-11-17，macOS arm64 / 10 CPU / 16GiB，Python3.14.3、FFmpeg9.0.2；同一 portable-v2 30秒/900帧/1080p30、锁定字体/音频/工具及默认质量。对照是测量开始时最新 main `c95ab81`，候选运行代码为 `deab99c`；收尾同步的 main `313743d` 只增加安装测试/CI/指南改动，被测渲染源码与输入字节不变，不把下面计时重标为实跑 `313743d`；两臂均无 worker、分段或质量覆盖，不再计入已合并的 Darwin 身份去重收益。完整命令、工具/输入摘要和仓外证据索引见 [PR #386](<https://github.com/CongliangK/framivra/pull/386>)。

| 顺序 | main cold all（s） | 候选 cold all（s） | 候选/main |
|---|---:|---:|---:|
| AB | 109.476 | 75.592 | 0.690487 |
| BA | 112.588 | 76.493 | 0.679411 |
| AB | 112.245 | 76.154 | 0.678465 |

配对比中位 **0.679411（减时32.06%）**；cold 仅为全新项目 cache/out，不是 OS 冷缓存。prepare+all 中位126.832→90.159s，prepare14.292→14.323s，warm all62.990→63.853s，额外 cold strict QC30.292→31.431s，均分账；本轮复用已校验字体，首次下载计时 **UNRUN**。四 shell 候选的有效负结果0.702046及所有探索/旧失败保留仓外，不挑样并入本组。

实际增量为有界独立 shell（本机默认5）、同上限完整 PNG 校验及去掉一次中间 PNG 移动；回退与隔离见[缓存语义](<../cache-provenance.md#产物身份>)。stage 任务区间并集中位：gfx59.869→23.486s、video29.880→30.057s（不含独立预编码任务）；raster48.267→12.803s；区间嵌套/并行，不相加冒充墙钟，也不把组合收益全部归给某个子改动。暖态并未提速，下一瓶颈仍有视频消费/编码与完整身份复核，不授权删检查。

冷 all 的0.5秒采样 CPU 下界中位136.76→142.94 CPU-s，进程树 summed-RSS 峰值中位1338→1365MiB（样本最高1361→1555MiB），IO读/写下界中位2638/551→2612/705MiB。共享页重复、短命进程/漏峰值及平台计数限制保留；不是精确峰值或设备流量。持久PNG均52,339,349字节，cache约56.76MB、out约47.77MB，额外字体副本/缓存仅在私有 staging。

十二次正式 cold/warm all 与十二次独立 strict QC、完整视频/PCM解码、精确900帧 PTS=n/30 与30秒、48kHz双声道1,440,000采样通过；全900 PNG冷暖及跨臂字节、解码视频/音频/连续时序相等。成片响度-14.2LUFS、解码域TP-4.4dBFS；未改任何质量门。原生 shell 失败与隔离 SIGINT 回收通过；定向回归、文档门和最终只读复审记录在 PR，不能代替其他平台或全仓验收。人工艺术/听感批准及其他平台本轮 **UNRUN**。

### HTML/CDP 工作流研究（#346）

本次独立研究使用公开 tech-talk [chart_curve.html](../../templates/tech-talk/assets/gfx-templates/chart_curve.html) 原样全长（20s / 600帧 / 1920×1080 / 30fps，DOM/SVG/文字），配确定性校准音，执行真实 `vtx all`。它不是完整口播节目：无私人素材、真人、字幕或 PIP；模板示例数据不是新的业务实测。**不替代唯一 30s/900帧 SVG 主基线，也不重启已否决路线。** 重现入口与计时边界见[实验说明](../../experiments/perf_html_workflow/README.md)。生产源码、默认配置和 CI 均未修改。

冷态指新项目缓存/输出，暖态指同项目随后重新执行 `all`；没有清 OS 页缓存，也没有保持浏览器。固定现有 CDP 导航路径、两块各300帧、CPU编码、单视频段和两 IO worker。默认 `core` 缓存保留 lossless gfx pack，收回散帧和工作媒体；所以“暖”并不意味着跳过下游编码。启动计时止于 DevTools/页目标建立；截图项包括捕获、PNG编码、传输和 JSON解析，不是浏览器内部编码器独占耗时。视频阶段合并 PNG读/解码、合成、编码和输出写入；单独读/写、纯编码、精确资源峰值均保留 `null+reason`。

所有原始尝试保留：最早一次因研究目录被用作过长 Chrome socket `TMPDIR` 而失败（包括 CDP/CLI回落失败），未计为性能成功；改短物理临时目录后，三对探索测量完整通过。探索清单误以散 PNG 为持久缓存，遗漏 `.vtxp`，因此不宣称当时完成缓存验收。修正仅作用于研究记录/校验工具，原数据不覆盖；随后固定工具版本重新开展交替顺序的三对 A/B 冷暖实验。

探索阶段测得浏览器启动覆盖约0.31s，而截图组合调用 work 约51s、覆盖约10.6s，下游 video 约23.1s；跨线程 work/覆盖与阶段不可相加。于是只试现有 `VTX_GFX_TABS=4→2`，检验截图排队/内存是否值得调节，而非预设重写渲染器或恢复旧快 PNG。A/B 前固定顺序 A1/B1/B2/A2/A3/B3，每臂独立冷暖一次，并要求全帧/音频/时间戳一致，保留所有配对比值；5% 全链中位改善只是本研究的实用信号线，不是产品 SLA 或统计显著性声明。

**实测结果：不为提速将默认 tabs 降为2。** Linux / Ryzen 9 7950X、Chrome157、Python3.12.13、固定 FFmpeg8.1 GPL构建；运行内核仍为 `d5d57c16`，A/B测量工具固定于 `67cf6d5`。完整身份、输入/字体/工具哈希、失败尝试及逐帧质量记录见[机器证据](html-workflow-evidence/index.json)，[样帧总览](html-workflow-evidence/overview.jpg)来自实际缓存，不是重绘示意。

| 配对 | A：4 tabs 冷 / 暖（s） | B：2 tabs 冷 / 暖（s） | B/A 冷 / 暖 |
|---|---:|---:|---:|
| 1 | 79.284 / 68.028 | 83.906 / 68.028 | 1.0583 / 1.0000 |
| 2 | 79.104 / 67.946 | 85.792 / 68.108 | 1.0845 / 1.0024 |
| 3 | 78.852 / 67.822 | 83.822 / 67.866 | 1.0630 / 1.0006 |

配对比值中位：**冷态慢6.30%，暖态约+0.065%（无实用改善）**；冷态采样进程树 summed-RSS 峰值中位从5.684降至4.372GiB，配对比值中位下降23.52%，属内存/耗时取舍而非提速胜出。CPU与 summed-RSS 为100ms采样下界，RSS包含共享页重复计数；Linux树级 IO累加可能重复归属已退出子进程，原始资源记录中统一“lower bounds”措辞已在证据更正说明中撤回其 IO含义，不冒充设备流量。共享工作站、小样本、无观察器开销校准，不给跨机器保证。

A臂冷态中位：启动覆盖0.289s；608次截图（600目标帧+8探针）组合 work50.58s / 覆盖10.56s，PNG原子写覆盖0.304s；video23.10s、收尾打包/核验/回收17.50s。B臂截图 work虽降至43.32s，覆盖却升至15.31s，说明少并发降低累计等待不等于全链更快。暖态零启动/截图，仍有缓存恢复/验证约12.12s、收尾核验/回收约11.15s及 video约23.07s；这些是组合边界，缓存恢复嵌于gfx阶段，**不得与阶段时长重复相加**。下一步更值得单独归因下游和缓存往返；本研究不授权省略校验、改变缓存语义或重启浏览器替代路线。

最终12次 `all` 均exit0、事件完整、无失败/回落；6个项目的冷暖pack字节及mtime均未变。逐件解码全部600帧PNG、完整视频/PTS、48k双声道音频/连续PTS；跨臂/轮次/冷暖的PNG字节与RGBA、视频帧/时间戳、音频采样/时间戳全部相等，12件无黑帧；6次额外strict QC通过。抽看f60/300/540字体/图表正常，不等于独立艺术审查或用户成片确认。主SVG、透明叠层/多页/真实整期及其他平台仍 **UNRUN**。此次给出的是明确的负收益决策与可复现取舍，不宣称产品加速。

### 资源生命周期排查（#362）

原静态审查以 `908064d` 为起点；接续在 main `fa5d28d` 的生产实现上（测量源码 `99fd603`），以已安装候选 wheel 验证唯一 portable-v2 30秒/900帧/1080p30 SVG。三档共7次正式 `all` 及另行7次strict QC通过（aggressive响度/TP复用有效回执），core连续3次；三档末次完整解码的视频、PCM及音频时序摘要相同。详细包身份、逐轮资源、实际宿主与清理证据统一见 [PR #366](<https://github.com/CongliangK/framivra/pull/366>)。有限样本不证明永久无泄漏，也不宣称整体提速。

| 链路 / 空间归属 | 已确认机制与待验风险 |
| --- | --- |
| prepare / audio | 源抽音频、插件派生及混音交接件，保留取决于[缓存档位](../cache-provenance.md#全局缓存档位)；模型服务、外部素材与共享资产不属于项目 cache，未实测其生命周期。 |
| gfx | 本机core保留约56.76MB认证PNG/证据，aggressive约66.18MB，off仅约1–2KB账本/锁；macOS未冒充Linux压缩包支持。[文件事务](../../vtx/runtime/fstxn.py)原强引用锁表为每个唯一加锁路径保留 Python 锁，[CLI 截图](../../vtx/render/gfx_cli.py)的逐帧 `locked_staging` 等调用会持续增加条目（CDP 的无锁 `atomic_write` 本身不经过该表）；改为弱引用注册表，所有持有者/等待者仍强引用同一锁。不删除磁盘锁文件、不改变跨进程锁协议。 |
| video / mux / qc | seg、逐层无损 pass、交接音视频与最终成片性质不同；不能按扩展名统一删除。`core/off` 用后回收媒体，`aggressive` 的保留属于策略而非泄漏；失败/截断链保留必要交接件。本轮采样树RSS峰值约1.00–1.37GiB；共享页重复计数、短命进程及漏峰限制保留，非独占物理内存。 |
| audit / df | [体积检查](../../vtx/cache/hygiene.py)的 quiet 路径原先仍全量排序 cache 路径、扫描孤儿并读取账本，仅丢弃打印。本轮跳过这些展示专用步骤，verbose top-20 改为有界候选选择，保留排序/同尺寸顺序及返回值。逻辑字节统计仍需遍历，不能当物理独占空间或原子快照。 |
| 缓存回收 / DSH | 历史键、预览、报告、实验缓存与事务锁各有不同保留边界；[清理约束](../cache-provenance.md#容量与保留)要求停写。本次新增[空间接口](<../reference/storage-api.md>)与侧栏采样、冻结预演及独立确认，复用原 GC 与参与者策略锁；不把文本 `gc` 输出冒充绑定确认，也不承诺排斥任意外部写入者。 |

另有未修复的静态风险：[CDP 接收器](../../vtx/render/gfx_render.py)逐 WebSocket 帧限长，但分片消息累计未限长，握手头也无总字节上限；小上限内存探针可由两个合法分片组成超限消息，不等于已观测真实浏览器内存泄漏。[媒体执行](../../vtx/pipeline/infra.py)的 `capture_output` 保留完整子进程日志，普通 FFmpeg 不具备浏览器同等的进程树监管；正常退出会回收直接子进程，不能据此断言正常路径有僵尸泄漏。分片上限应验证合法截图尺寸，媒体取消须明确等待/收尾与跨平台语义，均未在本轮改写。

[回归](../../tests/cache/test_storage_diagnostics.py)检查 quiet 不做展示遍历/账本读取、verbose top-20 与稳定全排序一致和缺失布局零写态；[锁生命周期回归](../../tests/safety/test_fstxn_lock_lifetime.py)检查闲置路径回收、活动持有者/等待者共享锁。这些是机制证明，不是 RSS 上限或整片提速证明。

用户已明确本次档位仅指既有 `off/core/aggressive`。DSH 复用原全局三档确认/CAS 保存，不新增 CPU/RAM/并发档位；[资源预算](../reference/resource-budget.md)仍是未接入渲染 CLI 的 Linux 协作记账原语，不是 OS 限制。空间管理实现与有界响应、过期/替换/失败等机制回归见[接口](<../reference/storage-api.md>)、[内核测试](<../../tests/cache/test_storage_api.py>)和[侧栏记录](<../../dsh-sidebar/DEVELOPMENT.md#project-storage-controls-362>)。最终已安装组合以完整主片副本核对显示、可见采样、三档原生保存与下一操作、取消/失败恢复及工程切换；维护者明确批准原冻结范围后，自动化通过正式界面执行一次清理，所有未删文件身份/哈希不变，源件、成片及历史/回执保留。当前对比、精确字节和有限观测限制见[PR #366](<https://github.com/CongliangK/framivra/pull/366>)；[旧合成图例](<https://github.com/CongliangK/framivra/blob/501e6a0a8b4fcd56c7a9fd1f4859989c1ed4e5fd/docs/reports/resource-management-362/README.md>)只作历史溯源，不冒充当前整片或所有平台验收。

每档新建项目经官方prepare/verify，不混旧cache/out；core三次`all`约73.46/61.73/61.07秒，off两次72.31/73.62秒，aggressive两次73.88/35.75秒，不作跨版本性能配对。均按实际探测回落x264/原生AAC，不称无回落；三档源容器900帧整数PTS乘时基逐帧等于n/30，非仅归一解码帧数通过。每次结束已观测的命令子进程均退出，观察器返回1个Python线程/3个FD；其all结束即时RSS约21–60MB、后续端点最高约81MB，不是渲染器残留。0.5秒目标采样的单轮调用成本约0.77–1.64秒，完整空间inspect约102–225ms（含启动），没有扣减计时。逻辑字节与st_blocks分账；后者含硬链接/共享extent重复，不是独占物理占用。core账本每轮约增491B，但既有4000/5000条轮转有界；out增长来自受保护历史成片，不当作缓存泄漏去删。未使用的CEF、模型/音乐服务、其他平台及长期存活仍UNRUN，既有[小型三档机制集成](<../../tests/cache/test_cache_modes_integration.py>)不替代主片。

### DSH 单帧预览研究（#363）

**结论：图像接入 DSH 已有实现；缺的是“冷 HTML 临时单帧”的公开内核入口，以及可预测的交互延迟，而不是再造一个图片上传器。** 本研究固定源码 `741f74b466605df310334461b9c74e4ec735d0ed`，只提交分析与证据，不实现下面的候选、不修改 UI、默认配置或 CI。[原始需求](<https://github.com/CongliangK/framivra/issues/363>)没有授予接口改造或视觉重设计。

#### 当前完成度与实际链路

| 用户诉求 | 当前实现 / 缺口 |
|---|---|
| 定位“10 秒第 5 帧” | [侧栏输入](<../../dsh-sidebar/src/client/index.mjs#L33-L43>)只接受规范整数**全局帧**，支持逐帧按钮、键盘和标尺；没有秒数或 timecode 输入。30fps 下，若指秒内零基偏移 5，则是 `10×30+5=305`；若指第 5 张则是 304。新输入需明确口径，显示换算后的整数，不能猜测或浮点舍入。gfx 页局部帧为全局帧减 `dst_in`，不等于任意选中片段的源时间。 |
| 临时渲染一个 HTML | 内部 [render_page](<../../vtx/render/gfx_render.py#L799-L858>) 已接受 `frames=[305]`，保持原页 `n_frames=600`；没有公开 CLI/API 把冷 HTML 单帧接入侧栏。普通 gfx 阶段仍按页面完整性准备缺帧，不能把 `n_frames` 改成 1 或为稀疏结果伪造整页完成证书。 |
| 显示当前合成帧 | 已有显式授权的 `timeline frame --frame … --render --include-png`，经 [PreviewInputs](<../../vtx/render/preview_inputs.py>) 和共享合成器生成 `current-composite`、`approximate:false`。它读取认证 gfx 帧，**不启动 HTML 生产者**；缺失/陈旧 gfx 拒绝，`off` 档可见 gfx 也拒绝。它不是从旧成片抽帧，也不是最终编码/QC 等价证明。 |
| 直接进入 DSH | [Host](<../../dsh-sidebar/src/host.mjs#L523-L601>) → 有界 JSON/base64 → [客户端](<../../dsh-sidebar/src/client/frame.mjs#L105-L155>) 校验版本、帧/FPS、PNG/IHDR、SHA、原文与完整解码 → `File`/object URL 显示。再一次明确点击才复制或追加图片与引用到已有草稿，不发送、不自动上传；模型是否支持视觉与用户发送是另一步。 |
| 平台/工程兼容 | 基线 [frame 实现](<../../vtx/timeline_frame.py#L68-L79>)仅 Linux fd-anchored/core-only，活动插件拒绝；本机 macOS 实际返回 `platform.unsupported` 且无新产物。macOS 支持已有独立 [PR #367](<https://github.com/CongliangK/framivra/pull/367>)，本研究不重复实现、不把未合入候选当基线能力。 |

渲染与时间线编辑授权分离；变帧、版本、选区、Session 或未保存编辑会使旧图失效。当前只允许一个全局渲染所有者；渲染准入、语义写/提案确认及未回收子进程有互斥，普通读不等于全程禁止。这不是可直接删掉的性能锁。Host 的 **30 秒**请求预算及独立清理边界保留：内核 [PreviewCtx.run](<../../vtx/timeline_frame.py#L124-L136>) 的 `communicate()` 没有自身总 deadline，CDP 分次超时/重试也不等于端到端预算，不能靠延长 Host 超时当作提速。所谓 streaming route 用于传递取消，**不是渐进式图片流**。参考 [现有侧栏契约](<../../dsh-sidebar/DEVELOPMENT.md#fixed-frame-png--current-draft-explicit-preview-cache-write>)；其早期“缺输入不准备”的概括不能覆盖后来新增的 native 临时编码行为。

#### 瓶颈：已证实的机制与待测项分开

1. **gfx 就绪是功能缺口，不是网络慢。** [随机读取器](<../../vtx/render/gfx_packed_reader.py>)已经能从认证裸 PNG/pack 取单帧，不必为读取整页解包；但不生产冷帧。HTML 改一字后旧缓存身份失效，需要另行授权的 gfx 准备，当前没有“一次点击只补该 HTML 帧”的通路。
2. **单帧合成不一定只做一帧工作。** [临时输入](<../../vtx/render/preview_inputs.py#L106-L124>)会为可见 native clip 调生产 `prepare_layers`；其[编码范围](<../../vtx/pipeline/composite.py#L892-L920>)是整个 clip。默认 `core` 不复用合成媒体/预览图，`aggressive` 才可能复用；因此重复点击可能重复编码整段、随后清掉临时件。不能把 `aggressive` 或暖缓存换档称为同条件冷态提速。
3. **身份/校验可能支配暖请求。** [frame current 检查](<../../vtx/timeline_frame.py#L155-L262>)反复核项目、gfx、工具/字体/库和实现身份；完整项目校验还会 probe 非可见素材。已有进程内摘要 memo，不是每次必然重新 hash 所有字节；仍需拆出目录遍历、外部探测、重复读取与新 CLI 启动，不能直接删掉重验。
4. **稀疏 HTML 的 setup 比目标截图复杂。** 默认仍建 4 tabs，初始导航、150ms 等待、透明/flatten 探针，然后再次导航目标帧。一个不透明帧在本次实测触发 **5 次 screenshot、4 次 PNG 解码**。内置目标帧计时不含这些探针，`ms_total` 又不含 Browser.start；只报 `ms_per_frame` 会漏成本。
5. **传输有成本，但尚非实测主因。** base64 增大约 1/3，整份精确 timeline 原文也随帧返回；两端还有解析、校验和图片解码。8MiB 是**整个响应**上限，PNG 有效预算小于约 6MiB 再扣元数据，并非可放 8MiB PNG；8192 边长、16,777,216 像素及 DSH 更严图片政策另算。本次 f305 PNG 85,910B、base64 114,548B，不能由此推断复杂图/4K 的耗时或容量足够。

旁证而非本次重跑：[PR #359 的公开记录](<https://github.com/CongliangK/framivra/pull/359>)在 `783778f` 候选、900 帧 SVG 主片上报告 CLI 合成帧 228 用时 **180.952s**，真实 DSH 在约 **30.039s** 取消、没有显示 PNG。它说明“小夹具通过 ≠ 主片交互可用”，不证明延迟来自哪一函数，也不能用下面的 HTML 探针替代或宣布已修复。

#### 本机机制实验：可跳帧，但不是现成的毫秒级工作流

[机器证据](<single-frame-preview-evidence.json>)保留全部 **30 次**调用、三套原样探针源码、输入/Chrome 启动二进制 hash、逐帧 PNG/RGBA hash、分项样本和失败边界。公开 [chart_curve.html](<../../templates/tech-talk/assets/gfx-templates/chart_curve.html>)原样复制到全新外部临时目录，600 帧/1080p30，仅请求 f305、再请求同帧、再请求邻帧 f306，各做三轮，并追加 `[304,305,306]` 同调用对照。它是内部 CDP 机制探针，**不替代 30 秒/900 帧 SVG 主基线，不是 DSH UI 截图或成片性能测试**。

环境：macOS 26.5.2 arm64、Python 3.9.6、NumPy 2.0.2、Chrome 157.0.8103.52；4 tabs、NAVFREE=0、STATIC=1，页面无 static/canvas 声明。先运行无 Pillow 的发现组，再补解码观测组；最后新建独立 venv，用已有 Pillow 11.3.0 对照，未修改系统安装或宿主配置。每次缺帧调用新起/停浏览器；未清 OS 页缓存。字体/完整 Chrome framework 身份、后台负载、资源峰值与观察器开销未锁定/校准，因此不授跨机精确性、统计显著性或产品 SLA。

| 组别（每格三次墙钟秒，非 p95） | 新目录 f305 | 同目录新邻帧 f306 |
|---|---|---|
| 发现：无 Pillow | 5.303 / 4.891 / 5.586 | 5.101 / 5.141 / 5.313 |
| 归因：无 Pillow | 5.350 / 5.103 / 5.146 | 5.092 / 5.296 / 5.097 |
| 现有 Pillow 快路 | 1.810 / 1.525 / 1.437 | 1.714 / 1.436 / 1.423 |

归因组 f305 中位总墙钟 **5.146s**；四次解码的两类累计中位分别约 **1.804s RGB + 1.838s RGBA**，Browser.start 中位约 0.304s、全部 screenshot 累计中位约 0.469s。两类解码不互相嵌套，但 `page_transparent` 包含其中的 RGB 解码与截图，**不能把这些项再次相加**。这在本机无 Pillow 环境中把主要成本定位到了已有 PNG 解码回落，而非笼统归咎于 Chrome 启动或图片回传。Pillow 组两类解码累计中位约 18ms/34ms，总墙钟中位 1.525s；这是[现有可选快路](<../../vtx/render/gfx_render.py#L77-L89>)的环境对照，不是新优化代码，顺序探索亦不是随机配对收益认证。

同帧裸 PNG 早退约 1.5–1.6ms，只验证已存在文件并不启动 Chrome；**它不核 HTML/依赖/环境身份，绝不能当作公开认证缓存或 DSH 暖延迟**。f305/f306 在三组及相邻帧对照中的 PNG 字节、完整解码 RGBA 均一致，且两帧彼此不同；只证明这些样帧，不证明全 600 帧。公开 `frame_timeline` 三组均拒绝 macOS，故没有“已打通冷 HTML→侧栏”的声明。

![内部 CDP 实际生成的 f305（模板示意数据，不是 DSH 界面）](<assets/single-frame-preview/chart-f305.png>)

复现是**写操作**：在上述源码仓根，将机器证据相应 `series[].probe_source` 保存为外部临时脚本，再用所选 Python 执行；脚本自建 `/tmp/f363-*` 工程并启动自有 Chrome，保留输出，不访问私人项目。Pillow 组需独立 venv、同版 NumPy 与 Pillow；不要在生产 DSH 或日常环境安装候选。脚本中的硬编码 macOS Chrome 路径是该次探针条件，不是跨平台入口；原样重跑不保证本机字体/负载相同。证据的源码 SHA 可核对提取字节，输出 `results.json` 含全部尝试。

#### 建议路线与落地边界（均未实施）

| 顺序 / 范围 | 建议交付 | 收益依据与需验证的边界 |
|---|---|---|
| P0：现有链路可诊断 | 分清平台不支持、冷 gfx、整段临时编码、渲染超时和传输/解码失败；记录请求关联的阶段墙钟、字节、缓存档位/命中，草稿追加另计。检查实际 vtx 环境是否有既有 Pillow 快路，不强制改默认依赖。 | 本次有明确 setup 解码证据；现有 capability 表示协议存在，不保证本平台/工程可渲染。可添加只读可用性信息，但不能让能力探测隐式执行项目/浏览器。macOS 跟进 #367，不重复开平台实现。 |
| P1：临时 HTML 单帧最小公开切片 | **优先新增独立的内核 HTML/layer preview 契约**，侧栏只消费认证 PNG。输入绑定受信根内 HTML/显式草稿快照、原始资源基址、局部帧、原时长、视口/FPS、依赖与环境；仅写预览命名空间，明确 `HTML 图层预览`，不伪称当前全合成。 | 复用既有稀疏 CDP 导航，不在侧栏另写 renderer。完整 timeline 并非独立页面必需；临时 timeline 也不能绕过现有冷 gfx 拒绝。不同 image kind、引用与能力协商须维护方和独立安全审查；先冻结协议再实现。 |
| P1：局部性能候选 | 先测稀疏请求只建有效 tab、同帧重复 setup 的安全去重；对身份开销仅研究同操作 pinned snapshot 的共享与最终突变重验。 | 不默认下调全片 tabs，不删字体/透明探针、不盲信跨进程旧身份。必须覆盖透明/flatten、Canvas 回落、慢字体及随机 seek；Pillow 已是现有优化，不重复开发。 |
| P1：清晰预览体验 | 独立整数帧与明确口径的秒/帧输入，显示全局/局部帧；区分“页面预览/合成预览”、等待/生成/失效/失败；候选包括适配窗口、100% 检查、透明棋盘底、键盘缩放。 | 这是待设计建议，不是现成能力或已授权改版。保留显式生成/草稿动作；不因拖动自动写缓存。有效图与选区关联可分别判定，但非命中选区不能附上虚假引用；过期图若保留必须显著标注且禁止送入当前帧草稿。 |
| P2：冷 HTML 参与完整合成 | 新增认证“局部帧生产者→合成器”缝，保留正式合成语义；native 单帧替代整段编码需独立证明源采样/滤镜/编码等价，不能悄悄降质。 | 涉及部分帧证明与既有整页完成证书的边界，不是给当前 reader 随便塞 PNG。原始源一帧快速抽取可另列近似模式，不能沿用 `approximate:false`；活动插件兼容另审。 |
| P2：仅当端到端归因支持 | 二进制 PNG + 有界元数据/短时 grant 绑定资源，减少 base64 与复制；或内核所有的有界长寿命 renderer。 | 新传输保留 SHA/版本/原文/完整解码，不能改读 `image.path` 或暴露裸文件；renderer 复用须限制内存、队列、寿命、跨项目状态和全树取消。跨请求浏览器常驻不是现有功能，也不由本研究重启历史暂停路线。 |

临时 HTML 仍可执行脚本，不能注入有权限的 DSH DOM；资产相对路径不能因移到临时目录而悄悄变义。必须明确可信来源、文件/网络访问策略、SSRF/路径逃逸、输出尺寸/字节、超时和进程回收。legacy 声明依赖不证明闭包；strict 禁 JS/SVG，不适合拿来包装普通动画 HTML 并宣称已安全。此处不恢复 NAVFREE、BeginFrame、foreignObject 或被停止的替代后端。

下一步验收应分别记录：冷页面首次图、同图合法命中、邻帧、HTML/依赖变化后重渲、取消/乱序；测从点击到解码后呈现的墙钟，核心阶段与草稿步骤另列。至少覆盖 f0/中间/末帧、非零 `dst_in`、透明叠层、重页、中文/空格路径、坏缓存、编辑中失效、30 秒预算内退出及无残留；正式性能仍走既定主基线，不能用这张图宣布全链加速。目标延迟应在真实 UI 基线后定，不预先承诺 100ms 或 30%。

**实际验证与限制：** 51 项侧栏 frame/Host/base64/owner/draft 定向测试通过，10 项可移植 frame 合同与 24 项 gfx mocks 通过；均不是 GUI 认证。初次 Node 测试因缺 React 有 2 个模块加载失败，锁定依赖安装后重跑通过。Python 原选 20 项在 macOS 初跑为 1 failure/9 errors；改物理临时目录后仍为 **1 failure/3 errors**（Linux 平台前置拒绝、fd 锚定及 `/proc` 假设），未修改/跳过原断言来报全绿。Linux 真实合成、DSH 端到端 UI、全仓 suite、900 帧主片、p95/峰值资源均 **UNRUN**。这些限制不阻止交付研究结论，但不授新功能可用性或产品性能资格。

## 已收敛的设计选择

FFmpeg+vtx 为本体，Blender 为可选交换/审计；当前数据流、静态增益响度终链、解码真峰、缓存身份与 Golden 门分别归[架构](../ARCHITECTURE.md)和[缓存语义](../cache-provenance.md)，不在此复述。overlay/framesync、NVENC、AAC、字幕量化与透明合成的旧排查见固定基准原文；历史失败不因删减改判。冷建字节非确定性与长流 sidechain 未明机制见[当前边界](../reference/known-issues.md)，不能据短探针或温态命中承诺整片安全。

<a id="色彩编码"></a>

色彩 #101 的未验收限制、禁止仅贴 BT.709 标签及探针写态要求已归[当前边界](../reference/known-issues.md#色彩编码)，历史证据由该处固定链接追溯。

## CEF voice-ime Alpha（#145）

2026-10-18 仅验收受信任本地 A0/Mode N 实验后端，默认 CDP 不变；源码 `59811757809642534591e77ee86669aeccbcdbf7`，两处 HTML selector 修正经用户批准，不能称 HTML 全部逐字节不变或后续 main 已全链重验。首轮中断无新终片、外层退出码未知；用户续跑复用该冷候选 GFX，单 IO worker/CPU 编码/12GB cgroup，all exit0、strict QC76/76、内容/黑场门及 Blender verify 通过。外层只读禁网 **不等于 Chromium sandbox：仍为 no_sandbox=true**。

原字幕 check **exit1**（3 FAIL / 16 UNKNOWN；另有27项字符预算 FAIL）；安全区通过不抵消失败。旧/新 master 真峰超过当时 −1.2 门限，旧 PASS 依赖舍入；失败 master 不在实际13条 bus 消费链，属非阻塞上游限制，不改历史 FAIL。辅助音频脚本缺 scipy 的初次失败保留，独立补验 exit0；相对音轨零偏移不证明字幕语义同步。**不授予生产、跨后端 Verified Exact、全部字幕合格、GPU/NVENC 或性能胜出。** [收尾原文](https://github.com/CongliangK/framivra/blob/3d9e6622d5d06eecf55ffbe2075da1fa994f93f5/docs/reports/cef-alpha-145-closeout.md) 与[机器证据索引](cef-alpha-145-evidence.json)保留身份、哈希及原始失败，不改冻结字节。

## 产线复审

历史 Windows 案例支持逐源导出、保留事件音量、明确 source/destination 映射及内容绑定缓存；后混音须测当前 WAV。审计通过不等于 `.blend` 播放验收（当时未安装 Blender）。[复审原文](https://github.com/CongliangK/framivra/blob/3d9e6622d5d06eecf55ffbe2075da1fa994f93f5/docs/reports/workflow-review.md) 的旧待核列表不是当前缺陷或授权；当前交付归[分轨审计](../../apps/blender-bridge/README.md#分轨审计包-audit_bundlepy人机协作交付门)。

## 安全与治理裁定

- 2026-10-14 外部红队核定只做内容审读，未跑套件/核对线上 issue；初版独审 **FAIL（2阻断/5次要）**，修稿不等于新实测。TOCTOU 裁定归[贡献指南](../../CONTRIBUTING.md#信号实验隔离)；依赖、安装及 v2 契约原则归 DEV/ARCHITECTURE。[27项原裁定](https://github.com/CongliangK/framivra/blob/3d9e6622d5d06eecf55ffbe2075da1fa994f93f5/docs/reports/%E5%A4%96%E9%83%A8%E7%BA%A2%E9%98%9F%E6%8A%A5%E5%91%8A%E7%BB%88%E5%AE%A1%E6%A0%B8%E5%AE%9A-2026-10-14.md) 保留当时 CEF 待议等历史状态。
- #103 两次组级信号杀掉宿主会话是隔离缺陷，不是无关环境抖动。动态实验硬限制归[贡献指南](../../CONTRIBUTING.md#信号实验隔离)；当时谱系防线在途、DSH harness 统一 setsid 仅 backlog 未实施，不据此推断后续部署或授予安全设置改动。[事故原文](https://github.com/CongliangK/framivra/blob/3d9e6622d5d06eecf55ffbe2075da1fa994f93f5/docs/reports/%E7%BA%A2%E9%98%9FPoC%E6%8E%80%E7%BF%BB%E6%A1%8C%E9%9D%A2%E4%BC%9A%E8%AF%9D%E4%BA%8B%E6%95%85-2026-10-15.md)保留时间线与责任取证。
