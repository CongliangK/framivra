# 开发决策与历史基准

本页解释保留或否决路线的理由，不维护软件结构、当前参数或待办。操作与契约从 [DEV](../DEV.md) 路由；下列测量只属于所列版本、机器和项目。逐轮数据见 [原始基准与故障记录](https://github.com/CongliangK/framivra/blob/3d9e6622d5d06eecf55ffbe2075da1fa994f93f5/docs/reports/development-history.md)，旧plans来源见[固定历史](https://github.com/CongliangK/framivra/tree/3d9e6622d5d06eecf55ffbe2075da1fa994f93f5/docs/plans-2026-09-11)。

## 一、性能基准

历史机器为 RTX 4080 / 32C64G。项目名、冷热与验证链边界不同的数值不得横比；尤其不能把 bench_p2 的结果当真实 voice-ime 全链基线。

| 日期 / 标的 | 保留的测量与结论 |
|---|---|
| 2026-09-08，voice-ime 653s@1080p30 | 早期全链 14.7min，GPU 9:46；仅作起点。 |
| 2026-09-11，voice-ime 28页/12024 GFX帧，`2e65536` | 页内分片+1474帧静态去重：7/450/4 冷跑129.8–131.9s、RSS15.0–15.2GB；桌面活跃三轮中位149.8s。≤110s目标 **未达**；8/450/6虽112.9–122.1s但RSS19.8–20.4GB，**内存门FAIL**。只增页6→8→12无分片为161.9/168.8/145.6s，关键瓶颈是重页。 |
| 2026-09-12，方案二三轮全冷 | 281/302/299s，中位299s，对照串行375s；gfx关键路径180s、video尾98s。QC每轮74/75、strict **exit 1**，保留已证伪c4_mini探针假阴性，不改成全绿。NVENC与x264抽31帧：19帧相同，其余PSNR40.4–50.1dB，不是逐像素等价。 |
| 2026-09-13，bench_p2 16746f/558.2s | 验证链三轮289.57/291.41/310.01s，中位291.4s；QC仍74/75、exit1，内容门通过。qc+gates16.7s与单列4K65.7s达标，**全链240s门未达**。4K14断言及10帧PSNR/SSIM抽检通过，不是全帧认证。 |
| 2026-09-19，真实voice-ime 16746f@30fps，`6388d6e` | 全冷三轮1119.19/1117.83/1116.81s，中位1117.8s；QC76/76、总exit0。**291.4s作为真实项目基线作废**；R-07b继承同口径三轮中位≤本基线×0.8的验收要求。 |
| 2026-09-12，voice-ime v4.3迭代 | 全链draft温态100.8s；纯回看窗5.2s；字幕改动窗空载11.0s、并行负载/冷探测59.4s。不能把回看耗时承诺给修改窗。diff单clip预估42s、实测46s，仅该例误差+8.7%。 |
| 2026-09-12，合成30s/900f字幕项目 | check<1s、单张preview0.56s、改一条字幕重渲两段1.9s（六段命中）；独立全片字幕pass增加整片解码/重编码，未采用。 |

旧分阶段估时、GFX全矩阵、迁移/Blender校验表及原始来源保存在上述固定原文；不是当前运行时预算。图像等价、声音、资源与性能各门分别裁定。

<a id="electron-route-status"></a>

### Electron：性能不合格，实验封存

2026-10-17 用户终止该路线：拒绝生产采用，停止开发、优化及整片验证；整片未完成、未生成完整成片，不要求补验。未集成decoder+encode ackyield组合POC在同机单worker、三份真实HTML、1080p、帧30/60/90、两轮配对下耗时8.430915s / 新鲜CEF6.355430s = **1.326569 > 1.25，FAIL**。不能外推最终合并源码或全片性能。[实验入口](../../experiments/gfx_electron_worker/README.md)与[PR #175](https://github.com/CongliangK/framivra/pull/175)保留封存记录；原报告位于 `_alpha-evidence-20261016/electron-perf-combined-decoder-yield-20261017-codex/REPORT.md`，SHA-256 `012eef5a9a05b04a8ca33ff0360f5ddb9f0ebe1dffa49c2448d41893c0aab9c5`。

## 二、坑位破案详录

| 问题 | 留下的判断，不作为现行操作配方 |
|---|---|
| 大overlay图首尾丢层、密集窗口丢帧 | 首尾垫帧解决一类边界；CFR补帧只修PTS、不保证内容。后来61层线程对照否决了“线程必然引发竞态”的归因，但真实8段并行解锁几乎无墙钟收益。前三重段同参数四次MD5各异，不能用逐位一致门掩盖原管线方差；完整控制组与PSNR见原文。 |
| 单遍dynamic loudnorm | 653s真人片前后响度塌陷可复现（后段约−21.7至−32LUFS），选择测量后静态增益，而非时变归一；WAV合格不替代编码后解码真峰检查。 |
| sidechaincompress | 长流无口播床乐反升约19dB，短流正常，深层机制 **未定位**；不能由短探针推断整片安全。 |
| NVENC / AAC | 128px探针曾误判硬件失败；原生AAC真人瞬态解码真峰可过冲8.5dB。编码器身份必须进段键，探测与缓存消费者须同口径，失败不可静默。 |
| 透明截图 | body opacity可使Chrome合成组拍平，透明override不保证alpha；守卫需要实际探针。omitBackground四次无alpha的失败不改写为可用。 |
| foreignObject批量导出 | blur约1.5%像素±1/255；径向渐变max差25–61、7–14%像素不同，同SVG-image对照复现。**全零Golden FAIL**，与页面重构无关；通道撤销，不由视觉近似豁免。 |
| 字幕对齐与preview | 延后字幕应参照voice窗口外沿；ASS百分秒量化必须进入活动集判定。同输入表示下可逐位比较；MP4/PNG色度上采样及有损编码差异不能误报成字幕逻辑漂移。 |

<a id="navfree-免导航截图判负双杀方案-v2-阶段-2"></a>

### NAVFREE：并发像素漂移且更慢

2026-09方案v2中，空闲单页40/40帧相同不代表压力安全：8实例下四个真实页自约f22开始全帧Golden不等；同页每帧耗时反而为逐帧导航的1.5–4.4倍。路线判负封存、不得启用，默认导航不变。这些观察**没有证明截图/动画时钟竞态**，也不证明BeginFrame必需或足够。

<a id="beginframe"></a>

### BeginFrame：正确性判负，正式性能 UNRUN

#67冻结原型在同一锁定shell的导航对照下比较1024帧，245帧不等；资源夹具两副本均零差，c5a为46/29、c5b为6/4、llm_post为64/96。没有缺帧、来源或环境失配，但这仍是 **exactness FAIL，拒绝生产采用**。小探针、Canary角标、DOM相同及最终四帧生命周期smoke均不能抵消内部像素错误。

c5a完整历史前缀到f22的反例在149与153两版、各两次独立运行中重现；每次三个重复f22均726像素不同、最大通道差9。满足跨版本仍失败的研究停止分支，不证明全局最短前缀、浏览器内部根因或普遍BeginFrame局限。实际为SwiftShader；下载URL/原始收据未知为null。**正式三轮冷性能UNRUN**（并发开发失控而取消），既有wall/RSS仅诊断；RSS会重复计共享页，早期压力轮保留另一闲置浏览器。内部时序归因接口缺失，字段null，不用外部耗时填补。

[完整原型/锁与复现入口](https://github.com/CongliangK/framivra/tree/f1efe5f688b543ca11902c9538dec02a5c265961/experiments/gfx_beginframe)和 [研究原文](https://github.com/CongliangK/framivra/blob/3d9e6622d5d06eecf55ffbe2075da1fa994f93f5/docs/gfx-beginframe.md) 保留协议及全部证据。仓外证据根为 `/tmp/framivra-orchestration-20261018/issue67/`；独审核验留存文件哈希，未重跑浏览器或解码。研究结案不授予生产、性能或内部归因通过。

<a id="色彩编码"></a>

### 色彩编码：不能只补 BT.709 标签

#101的2026-10-18测量绑定 `59811757809642534591e77ee86669aeccbcdbf7`、Linux FFmpeg8.1.3/RTX4080、1080p RGB24八色块单帧：默认YUV420P与显式BT.601 limited逐字节相同，与BT.709 limited不同。x264 medium/veryfast及NVENC p4/p7四档均真实编码解码成功，SPS无video_signal_type、ffprobe无color_*；AAC+copy mux前后各自解码字节不变。只证明该RGB入口，不覆盖任意YUV输入、完整图或原色/传递函数。

据此**不能直接贴bt709/tv标签**。须先设计实际矩阵/范围转换与原色/传递函数来源，保留既有YUV解释；实际改像素需独立评审及重建Golden，不能称“仅元数据零差”。新标签前后零差、完整Golden/QC、音频响度及多帧全链帧数均 **UNRUN**，原报告据此保持#101开放，不构成当前线上状态查询。现行探针帮助见[测量工具](../../tests/runners/probe_color_encoding.py)；默认不入套件、要求四档真实可用、不用CPU替代NVENC；零写态入口为 `python3 tests/runners/probe_color_encoding.py --help`；实际测量必须用 `--output` 指向不存在的新目录，拒绝覆盖，且会写入合成素材和结果。[原始样本与测量记录](https://github.com/CongliangK/framivra/blob/3d9e6622d5d06eecf55ffbe2075da1fa994f93f5/docs/reference/color-encoding.md)及仓外 `/tmp/framivra-orchestration-20261018/issue101/` 保存命令、失败、摘要；临时目录不等于持久仓库归档。

## 三、工具演进史

- NLE底层方案经调研/POC否决，选FFmpeg+vtx为渲染本体；Blender仅作可选交换/审计，采用CLI/bpy，拒绝无格式规范的外部直写与不必要MCP。现行契约归[架构](../ARCHITECTURE.md)，历史校验与迁移细节见原文。
- CDP常驻、透明快路径、页并行、页内分片/静态去重均以Golden与原子落盘为采用条件，不由提速替代正确性。pip安全区与自带底色源于真实返工/黑底深字事故；外部旧工程副本是冻结快照，不证明与本体同步。

## 产线复审

历史Windows 54秒1080p30制作发现音频审计取错来源、丢事件音量、混淆source/destination时间。修后23/23测试通过（14项audit），真实两源/两轨/十事件审计通过；Blender未安装，**未验.blend播放**。选择逐源导出、保留事件、明确时间映射及内容绑定缓存；后混音响度必须测当前WAV。[原复审及当时建议](https://github.com/CongliangK/framivra/blob/3d9e6622d5d06eecf55ffbe2075da1fa994f93f5/docs/reports/workflow-review.md)保留完整证据与后续优先级；其中loop非零src_in、失败后旧bundle、字幕阅读、浏览器清理等是当时待核边界，不把旧列表当当前缺陷或授权。

## CEF voice-ime Alpha（#145）

2026-10-18仅验收受信任本地A0/Mode N实验后端，默认CDP不变。完整冷候选源码 `59811757809642534591e77ee86669aeccbcdbf7`，231项运行库锁一致；不把后续main变更说成全链重验。原工程只读，timeline 16746帧/558.2s@1080p30，28页/12024GFX帧；实验HTML保留用户批准的两处selector修正，非所有HTML逐字节相同。内外引闭包双备的独立核验、worker身份和环境详见[原收尾报告](https://github.com/CongliangK/framivra/blob/3d9e6622d5d06eecf55ffbe2075da1fa994f93f5/docs/reports/cef-alpha-145-closeout.md)；实际输出和原失败文件哈希仍在[机器索引](cef-alpha-145-evidence.json)。

首轮GFX完成，预编码时会话中断，无新终片且外层退出码未知。按用户高内存/显存任务自行执行要求，用户续跑采用单IO worker、CPU编码、12GB cgroup；复用该冷候选GFX而非历史产物，all退出0。外层bwrap只读挂载并禁网，但Chromium仍为既有 `no_sandbox=true`，**未启用Chromium sandbox**。最终SHA `3817cdf4698a74e3cb357844493142515a58044a9e812222eb59778bc97bb695`，135696311字节；全GFX回执审计、strict QC76/76、内容/黑场门通过，Blender独立verify588 PASS/0 FAIL；帧时钟/ASS集合匹配不是全像素字幕认证。

保留失败：原字幕check **exit1**，盲ASR/声学对齐88 PASS、43 WARN、3 FAIL、16 UNKNOWN，三项晚声约2.5–2.8s；27项字符预算FAIL不因实际安全区27/27通过而消失。原CDP master −1.16dBTP、历史/新CEF −1.14dBTP均超−1.2门限；旧PASS依赖一位小数舍入。该失败master不在实际13条bus消费链，裁为非阻塞上游母带/测量限制，不改历史FAIL。

音频相关辅助脚本初因缺scipy退出1，原聚合失败保留；独立venv、CPU单线程/1GiB补验退出0。新片相对原CDP和历史CEF的150窗均0 sample lag，只排除新增相对音轨偏移，不证明字幕语义同步。#145实验Alpha可结案，**不证明生产准入、跨后端Verified Exact、全部字幕合格、GPU/NVENC或性能胜出**；其他研究要求不随之关闭。

## 外部红队核定（2026-10-14）

核定基准 `06e9621`，外部报告快照元数据不自洽；完整27项裁定见[固定原文](https://github.com/CongliangK/framivra/blob/3d9e6622d5d06eecf55ffbe2075da1fa994f93f5/docs/reports/%E5%A4%96%E9%83%A8%E7%BA%A2%E9%98%9F%E6%8A%A5%E5%91%8A%E7%BB%88%E5%AE%A1%E6%A0%B8%E5%AE%9A-2026-10-14.md)。保留的决定：停止新增本地TOCTOU加固但不撤既有防御/在途B08回归；移除仓根组件符号链接但不误删Windows安装物化；默认标准库但允许经评估采用成熟库；保留处理非pip资产的克隆安装器；优先agent诊断流，不放宽v2契约。CEF当日待议，不冒充后续状态。

报告重提已有透明单截、两遍响度与DAG，不能据其叙事认定能力缺失；外部HTML未声明依赖仍是静态扫描边界。核定仅内容审读，**未跑测试套件、未访问线上issue核对**；初版独立红队 **FAIL（2阻断/5次要）**，原稿记载修正不等于新实测。旧line/commit表留在原文，不重复维护。

## 进程杀灭 PoC 事故（2026-10-15）

#103复审两次杀掉宿主桌面会话。scope启动未setsid，与systemd用户管理器同组；r2b把缺隔离的变异代码in-process执行，组级SIGKILL击中宿主。真实缺陷包括伪造pgid=0及未拒自身/祖先组；工作树/证据未损坏，不把事故归为无关环境抖动。[原始时间线和责任复盘](https://github.com/CongliangK/framivra/blob/3d9e6622d5d06eecf55ffbe2075da1fa994f93f5/docs/reports/%E7%BA%A2%E9%98%9FPoC%E6%8E%80%E7%BF%BB%E6%A1%8C%E9%9D%A2%E4%BC%9A%E8%AF%9D%E4%BA%8B%E6%95%85-2026-10-15.md)保留取证。

安全结论：kill/killpg/信号类动态实验**禁止in-process直跑**，须在 `start_new_session=True` 隔离子进程内；变异演示按必然突破被测防护看待。当次复审改用静态分析、安全测试及既有隔离证据。报告时谱系白名单防线在途，DSH harness统一setsid经用户裁决仅记backlog、**未实施**；本页不推断后续部署状态或授权改安全设置。
