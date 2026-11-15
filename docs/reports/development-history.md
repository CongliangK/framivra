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
