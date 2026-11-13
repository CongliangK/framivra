# workflows/ —— ComfyUI 工作流库（14 件）

ComfyUI **API 格式**工作流（直接 POST /prompt 的 JSON，非 UI 导出图）。提交目标 `http://127.0.0.1:8188`（按 ComfyUI 官方说明启动本地服务）；模型权重在 ComfyUI 侧（用户自备 `<ComfyUI 安装目录>/models/`，不分发或隐式下载；节点/权重名称由用户核对）。

两类骨架：

- **ACE-Step 1.5 系**（13 件）：`UNETLoader[104] → DualCLIPLoader[105]（双文本编码器）→ TextEncodeAceStepAudio1.5[94]（tags/lyrics/seed/duration）+ ConditioningZeroOut[47]（负条件置空）→ ModelSamplingAuraFlow[78] → KSampler[3]（seed/steps）→ VAEDecodeAudio[18] → SaveAudio[104s]`。时长是**双节点声明**：`94.inputs.duration` 与 `98.inputs.seconds`（EmptyAceStep1.5LatentAudio）必须同步改。
- **MiniMax Music3**（1 件）：DiT int8 + 剪枝文本编码器 + dav VAE。

## 逐件说明

**wf_ace15.json —— 主力锚点（lo-fi）**
ACE turbo 模型（`acestep_v1.5_turbo`），lo-fi hip hop tags（vinyl crackle / boom bap / study music，instrumental 无声乐），180s，8 步，seed 20260821，MP3 输出（`audio/ace15_lofi_test`）。本仓库引用最多的一份：service 档测试的基座（tests 用它改 duration=8 跑 8s 冒烟）、步数 A/B 实验的基底（`ab_steps.py` 以它为模板）、`assets_audio/bgm/` 首批试听样的来源。

**wf_ace15_ambient.json —— 风格变体：ambient**
同 turbo 骨架，tags 换 ambient 暖垫（evolving synth pad / spacious reverb / no drums, no vocals），180s，8 步，seed 7，MP3 输出。

**wf_ace15_synthwave.json —— 风格变体：synthwave**
同 turbo 骨架，tags 换 synthwave/retrowave（gated reverb drums / arpeggiated synth lead，80s 怀旧），180s，8 步，seed 88，MP3 输出。demucs 冒烟的第二组输入（含人声 AI 生成 → 四分轨全非静音的证据样，见 `bgm_gen/assets.json` demucs 条）。

**wf_ace15_xl_official.json —— XL 修复版（钢琴）**
XL 模型（`acestep_v1.5_xl_sft_int8`）+ **qwen_0.6b + qwen_4b** 双编码器组合，solo classical piano tags，50 步，seed 99，SaveAudioAdvanced 无损 flac（产物前缀 `piano_xl_4b`）。v2 调性实验结论「XL+4B 断层领先（r=0.91）」的载体；Eden Circuit 的 AI 质感层即由它派生（docs/REPORT.md §⑥：duration=40、A minor、seed=527 → demucs 分轨取 other 冻结入工程）。

**wf_music3.json —— MiniMax Music3 整曲**
Music3 全家桶（`minimax_music3_dit_int8_convrot` + 文本编码器 pruned int8 + `dav` VAE），lyrics 为段落标记（`[Intro][Instrumental]…[Outro]`），KSampler 30 步，seed 20260821。时长由模型自主（实测 24-75s/首），定位=整曲/人声曲备选产线（时长精确的 BGM 主力路线是 ACE，见视频项目 AGENTS.md T5）。

**wf_piano_base.json —— 钢琴修复迭代①：base 模型**
`acestep_v1.5_base_int8` + qwen_1.7b，古典钢琴 tags，50 步，seed 99，flac 输出。钢琴三连修复的基线对照（取证：base 模型钢琴音质不合格）。

**wf_piano_xl.json —— 钢琴修复迭代②：XL + 1.7b**
`acestep_v1.5_xl_sft_int8` + qwen_1.7b（相对 ① 只换模型），验证 XL 对钢琴音质的改善幅度。

**wf_piano_xl4b.json —— 钢琴修复迭代③：XL + 4b（终点）**
`acestep_v1.5_xl_sft_int8` + qwen_4b（再换副编码器），flac 输出（`piano_xl_4b`）——与 `wf_ace15_xl_official.json` 同配置（前者为实验副本、后者为官方示例整理版），调性/音质双达标的最终组合。

**wf_ab_*.json —— 步数×模型 A/B 实验组（6 件）**
`bgm_gen/legacy/ab_steps.py` 从 wf_ace15.json 程序化派生（clean8/16/32/lofi32 四件；base50/xl50 为同法的模型对照组扩展）。统一「clean 制作」tags（显式排除 vinyl crackle / tape hiss / lo-fi texture——把模型伪影与风格噪声分开），seed 1234（lofi32 除外），SaveAudioAdvanced flac：

| 文件 | 模型 | KSampler | 实验变量 |
|---|---|---|---|
| wf_ab_clean8 / clean16 / clean32.json | turbo | 8 / 16 / 32 步 | 步数主变量（同 seed 同 prompt） |
| wf_ab_lofi32.json | turbo | 32 步 | lo-fi tags × 32 步（风格纹理随步数的变化） |
| wf_ab_base50.json | base_int8 | 50 步 | 模型对照：base |
| wf_ab_xl50.json | xl_sft_int8 | 50 步 | 模型对照：XL |

> **2026-09-09 分家**：voice-ime 项目床乐四件（wf_ace15_vime_{a,b,b_turbo,c}.json 及其 README 段落）迁 `<私有项目仓>/workflows/`——标签漂移教训（克制词堆叠→无鼓点 drone、律动词前置正面词）与 turbo shimmer 对照证据随迁可查。

## 提交方式（会计算并写产物）

以下从本 app 根目录执行，`bgm` 表示 `python3 bgm.py`，无需旧仓包装脚本。

```bash
# ① 旧脚本直提（bgm_gen/legacy/gen.py，argv 契约冻结 BG-D4）
cd bgm_gen/legacy && python3 gen.py ../../workflows/wf_ace15.json

# ② ai 引擎（推荐，入工程管线）：payload.workflow 指文件 + params 点路径覆盖
bgm render-engine --engine ai --spec /tmp/ai_spec.json
# /tmp/ai_spec.json 示例：
# {"engine": "ai", "spec_version": "1",
#  "payload": {"workflow": "workflows/wf_ace15.json",
#              "params": {"98.inputs.seconds": 16, "94.inputs.duration": 16,
#                         "3.inputs.seed": 42,
#                         "104s.inputs.filename_prefix": "w2b/x"}}}
```

`params` 点路径 = `节点id.inputs.字段名`（值限数值/字符串/布尔，路径不存在即报错）；产物自动从 `/view` 下载落盘（缺省 `<调用者 cwd>/out/smoke/ai/`）。GPU 提交前过 BG-D8 轻量显存闸（显存不足照常提交——ComfyUI 自带排队，但与 demucs 等本地 GPU 任务互斥）。

## 权重获取与许可

本仓仅分发工作流配置，不含模型权重。MiniMax Music3 权重许可未完成登记，需用户自行获取并遵守其条款；本仓 MIT 许可不授予权重或生成内容的额外权利，未核实商用授权前不得进入商用产线。
