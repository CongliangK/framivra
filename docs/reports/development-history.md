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
