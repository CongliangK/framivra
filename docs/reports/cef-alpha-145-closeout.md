# CEF voice-ime Alpha 验收收尾（#145）

状态：完整候选运行及客观补验已完成；本报告裁决范围仅为 #145 实验性 Alpha。

## 固定范围与输入

本单仅验收受信任本地 voice-ime 的 CEF A0 / Mode N 实验后端，默认 CDP 不变。不宣称正式生产准入、跨后端 Verified Exact、GPU/NVENC 或性能胜出。Electron 已取消；#114、#115、#118、#120、#122、#123 的独立研究要求不随本单关闭。

本轮完整冷跑的源码为 `59811757809642534591e77ee86669aeccbcdbf7`；最终收尾分支基于 main `0b9e197`，仅修改文档。期间发现的 FFmpeg showinfo 预取测试问题已由 main 独立修复，收尾分支移除了重复测试补丁。此处完整运行绑定 5981175，不宣称对后续全部 main 变更重新跑过整片。worker SHA256 为 `c728509d359e2194450e7d6c440e005cebdeeec8e9441e95876a5fca7b87160c`，231 项实际运行库重新读取的摘要均与原独立准入锁相符。CEF 154.0.34 / Chromium 154.0.8037.98。实际 A0/headless 探针记录软件 compositing、WebGL SwiftShader、sRGB 与字体摘要；这是同配置独立探针，不伪称来自整片每个进程的 GPU 查询。

原项目永久只读。原 timeline 与实验输入均为 SHA256 `d555373d1f7f2289ab755c8927e56ae9e417abd4817bcff25d7b8ddc167e5c21`：1920×1080、30fps、16,746 帧 / 558.2s，28 页 / 12,024 个 GFX 帧。实验输入保留用户于 2026-10-16 单独批准的 open_source_card 两处 class selector 修正；原 HTML 未改，不能声称所有 HTML 与原件逐字节相同。未改时间轴、字幕、音轨、文案或画面需求。

内部双副本的原始校验和历史产物保全沿用 2026-10-16 记录。本轮补齐外引闭包双备：每份 987 个 canonical 文件、1,251,271,902 字节，包含实际音效、音乐工程、9 项 SFZ 的递归 include/sample、SF2 和固定音乐源码；逻辑路径及符号链接目标有映射，原件及两份副本逐文件 SHA256/type/mode/size 相同且 inode 各异。独立审查者重新读取全部三路并重新展开 SFZ 闭包，未发现 missing/unresolved/error。

## 原始失败的分层

原检查器的字幕 align/width FAIL 与源 producer master QC FAIL 均保留原报告及退出码，不改门、不降阈值、不回写历史 PASS。独立盲 ASR 与真实声学对齐得到 88 PASS、43 WARN、3 FAIL、16 UNKNOWN；3 项字幕晚声约 2.5–2.8s，经平移裁窗复核仍失败。27 项原字符预算 FAIL 保留，当前 renderer 实测实际 alpha 安全区 27/27 PASS。以上不能改写为字幕同步门整体通过。本轮新终片在全部 150 个窗口相对原后端及历史 CEF 的音频偏移均为 0；这仅排除新增相对音轨偏移，不把语义字幕同步判为 PASS。

历史音乐 master 的更精细 TP 测量：原 CDP −1.16 dBTP、历史 CEF 与本轮 CEF −1.14 dBTP，均略超工程 −1.2 门限。旧 CDP PASS 依赖一位小数舍入，不能称两份 master 相同或将 CEF FAIL 改为 PASS。视频实际消费 13 条含 gain/pan/space 的 bus stem；失败 master 在分轨之后独立生成，不在本片消费链，也不回写 bus。结合实际 bus 消费路径、原后端同一精细测量超限与最终独立验收，此项裁为非阻塞上游母带/测量限制，不以终片 TP 合格作为唯一理由。

## 证据入口

原始完整命令、环境、输入清单、运行阶段、stdout/stderr、退出码和实际输出保存在视频工作区 `_alpha-evidence-20261018/issue145/`；外引双备及核验在同级 `issue145-external-backups/`。实验工程为 `voice-ime-alpha-cef-closeout-20261018/`，初始 cache/out 均不存在，未消费历史 CEF/CDP 产物。外层 bwrap 将源工程、素材与源码只读挂载并禁网；Chromium 自身仍为既有 `no_sandbox=true`，不宣称启用了 Chromium sandbox。

历史验收与 [PR #166](https://github.com/CongliangK/framivra/pull/166) 保留其原绑定提交和原失败，不冒充本轮结果。本轮最终结果及来源边界见下节；摘要索引见 [机器证据索引](cef-alpha-145-evidence.json)。

## 中断与用户执行边界

本轮冷态 GFX 已于 2026-10-18 09:34:36（Asia/Shanghai）完成：28 页、12,024 帧，阶段退出 0。视频预编码开始后会话中断，外层最终退出码不可取得，该次尝试未生成新终片。不将中断记作通过或伪造退出码。

用户要求高内存/显存任务交本人执行。已准备独立终端续跑脚本，以单 IO worker、CPU 编码、12GB cgroup 上限运行；重核外引与内部输入、运行库及生产代码身份后，原子无覆盖保全旧输出，复用本次冷候选完整 GFX，只读挂载这些帧及原生回执。新续跑目录单独保留日志、阶段退出码及后检查结果。用户独立启动该脚本，2026-10-18 10:35:48（Asia/Shanghai）结束，all 退出 0。


## 最终运行与客观结果

续跑证据目录为 `continuation-20261018T015154Z-32c099`，在上述证据入口下。成片 SHA256：`3817cdf4698a74e3cb357844493142515a58044a9e812222eb59778bc97bb695`（135,696,311 字节）。实测 1920×1080、30fps、16,746 帧、558.2s，与冻结 timeline 一致；音视频流完整参数保存在 `ffprobe.log`。

| 检查 | 实际结果及边界 |
| --- | --- |
| validate/audit/prepare/gfx/video/audio/mux/qc/finish | all 退出 0；阶段日志保留真实时序 |
| 全 GFX 逐帧审计 | 28 页 / 12,024 帧 PASS；使用 #62 hash_frame，检查全部 PNG 完整性、几何及 native generation/request/canary/baseline-equality 绑定；不等于逐帧 golden 视觉认证 |
| 严格 QC / 内容门 / blackdetect | 退出 0；严格 QC 76/76；没有删门或降阈值 |
| Blender bridge | 导出成功；独立 verify 588 PASS / 0 FAIL，137 条 |
| 实际 PTS 与 ASS | 16,746 帧时钟、150 条文本/量化活动集合 PASS，零 mismatch；不是全像素字幕或人工听审认证 |
| 新成片相对音频时序 | 相对原 CDP、历史 CEF 的 150 窗均为 0 sample lag；最小零偏移相关系数分别 0.9998120923942605 / 0.9999841904788426 |
| 原字幕检查 | 退出 1 保留；上述声学 FAIL/WARN/UNKNOWN 与字符预算限制未撤销 |

初次音频相对时序辅助脚本使用 base Python，因缺少 scipy 退出 1，未实际执行比较；原 `summary.json`、失败日志保持原样。使用已有独立 venv 数值依赖补验，CPU 单线程、无模型/GPU、1 GiB cgroup 上限，退出 0；其逐窗结果和输入成片/WAV 摘要保存在 `audio-correlation/new-audio-correlation.json`。不能把最初聚合退出状态改写为全绿。

原生真实能力、帧同步、JS 失败门与隔离 Kill-9 的独立实际测试报告及初次失败日志均保留。fresh 独立审查结果随证据归档。对 5981175 至最终分支的变化逐项核对：实际 CEF worker、渲染/混音/字幕/Blender 路径保持原运行身份；后续 main 的 acceptance 工具、ASR、版本与其他研究变更不能借本报告声称已全链复验。收尾提交自身不修改生产默认或运行代码。

据此可关闭 #145 的原项目实验性 Alpha 交付；不升级为生产、跨后端像素等价、全部字幕语义合格或性能通过。性能耗时问题需独立分析，不由本次功能验收结案豁免。
