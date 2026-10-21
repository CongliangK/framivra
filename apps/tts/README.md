# tts/ · 口播占位语音与音频加工
原三模块方案的章节编号仅作历史来源：[2026-09-11 设计记录](https://github.com/CongliangK/framivra/blob/c476b4f5f13687c61c9be02f4015e289a176ddc8/docs/plans/2026-09-11-三大模块整合方案.md)。

组件声明见 `app.json`（kind=app）；健康检查为 `tts_cli.py --help`，不加载模型。`tts.repo/python/voice_ref/edge_bin` 支持 `~` 路径。**当前 IndexTTS worker 是许可拒绝入口，不执行推理**：CLI 仍可选择它，但在配置解释器可用并进入 worker 后，当前政策返回退出码 4，先于上游导入、资产读取与项目产物创建。改配置或已有权重不能解除阻断；恢复条件见[第三方许可与运行边界](../../docs/third-party-licenses.md)。

工作流：**按 `spec.json` 生成 TTS 占位 → `apps/sync/repack_voice.py` 替换为实录终版**；产物遵守下文消费契约，历史产线记录见文末。

## 用途

| 文件 | 职责 |
|---|---|
| `tts_cli.py` | 总入口（系统 python3，stdlib）：`spec` / `gen` / `normalize` / `probe` |
| `spec.py` | timeline.json voice 轨 `vt_<ID>` 事件链 → spec.json（预算口径见下方 spec.json 格式节） |
| `indextts_worker.py` | 由配置解释器调用的许可拒绝入口；当前不加载模型、不生成音频 |
| `edge_worker.py` | edge-tts 兜底引擎（晓晓 zh-CN-XiaoxiaoNeural，免 GPU 免模型，subprocess 二进制 + 同 atempo/归一链） |
| `audio_processing.py` | 共享 final 加工（volumedetect + volume）；算法见下文 atempo/峰值契约。`tempo` 只计算滤镜；`normalize` 支持单 WAV/目录、`--target`，原位写音频。参数见 `python3 apps/tts/audio_processing.py --help`。 |
| `init.sh` | 默认本地资产探测；未审查的混合下载明确阻断，许可与恢复边界见 [第三方许可](../../docs/third-party-licenses.md) |
| `tests/` | unittest（atempo golden / spec 预算 / levelnorm / manifest+final 格式；零模型零 GPU 零网络） |

## 依赖

- **app 级（manifest requires，doctor 可探测）**：ffmpeg / ffprobe（atempo 应用器、时长实测、峰值归一、volumedetect）；CLI 面（tts_cli/spec）系统 python3 stdlib 零第三方依赖。
- **indextts 路径**：配置解释器用于启动拒绝入口，不代表推理可用；`init.sh` / `probe` 的资产存在性结果不能解除许可阻断。
- **edge 兜底引擎**：`edge-tts` 二进制（默认 `~/miniconda3/bin/edge-tts`，`tts.edge_bin` 可改；pip 安装）。

## 健康检查

`env python3 apps/tts/tts_cli.py --help` 退出码 0（app.json health.command；CLI 面系统 python3 即可跑，不涉模型/解释器）。资产态另探：`tts_cli.py probe` / `bash apps/tts/init.sh`。

## 操作与写入边界

先读[第三方许可与运行边界](../../docs/third-party-licenses.md)。`gen` 缺省取 `spec.engine`，再缺省为当前阻断的 `indextts`；需要独立 edge 路径时显式传 `--engine edge`。edge 客户端许可与在线服务/输出权利须分开审查，不因客户端可安装而获得授权。

只读命令（`gen --dry-run` 只列窗口，不验证引擎可用性）：

```bash
env python3 apps/tts/tts_cli.py --help
env python3 apps/tts/tts_cli.py gen /tmp/spec.json --project <项目> --engine edge --dry-run
```

下列命令**会写文件**；edge 生成还会调用在线服务与本地 FFmpeg，运行前确认服务条件、依赖和目标项目：

```bash
env python3 apps/tts/tts_cli.py spec -t <项目>/timeline.json -o /tmp/spec.json
# 人工校对回填 spec.json 各窗 text（timeline voice 轨不含文本）
env python3 apps/tts/tts_cli.py gen /tmp/spec.json --project <项目> --engine edge
env python3 apps/tts/tts_cli.py normalize --project <项目>   # 原位修改 final/ 音频
```

`tts_cli.py probe` / `bash apps/tts/init.sh` 只作资产探测，也可能调用本地 `nvidia-smi`；不是推理验收。`tts.enabled` 不是 worker 阻断的解除开关。

## spec.json 格式（windows[].id 即 `vt_<ID>` 去前缀）

```json
{
  "version": 1,
  "engine": "edge",
  "voice_ref": "~/音乐/5_G_Resemble仅降噪.wav",
  "fps": 30,
  "windows": [
    {"id": "A1", "text": "首先：它是一个输入法。……", "budget_s": 7.62, "n_events": 1}
  ]
}
```

- `budget_s = max(每个事件的 src_in + (out - in)) / fps + 0.02`：与 repack_voice.py 的 TAIL_MS 同口径，保留 20ms 句尾保护。
- `text` 由 `spec` 子命令置空、**人工校对后回填**（逐字稿在脚本/字幕侧，勿让模型念窗口 ID）。
- `fps`/`n_events` 为对账辅助字段，消费方只读 `id/text/budget_s`。

## 产物落位（repack_voice.py 消费契约，静态断言见 tests）

| 产物 | 位置 | 格式 |
|---|---|---|
| final 成品 | `<项目>/assets/voice_tts/final/<ID>.wav` | 48kHz / mono / pcm_s16le，时长=budget±0.02s |
| raw 中间物（edge 转码） | `<项目>/cache/tmp/<runid>/` | 可再生中间物（hygiene tmp 纪律） |
| manifest 记账 | `<项目>/cache/reports/tts_manifest_<runid>.json` | gc 永不触碰 |

manifest 行字段（对齐 voice-ime manifest.json + peak_db）：
`{id, chars, budget_s, raw_s, duration_factor, final_s, peak_db}`（edge 引擎 duration_factor 恒 null）。

`gen` 完成后打印 repack_voice.py 同款完整 `all` 交付入口，会更新项目 `cache/`、`out/`；
内容寻址自动选择可复用缓存，完整链负责当前阶段回执及启用插件的 finish。局部音频诊断须先对当前输入
`prepare`，再 `audio --report`，最终仍运行完整 `all` 并检查退出码与 QC/插件结果，见 [runbook](../../docs/skill/references/edit.md)。
limiter 已并入 vtx audio，无需手动 post_loudness；实录终版用 `apps/sync/repack_voice.py` 替换。

## 引擎与 atempo 契约（方向坑必须钉死）

- **indextts**：当前仅拒绝执行，不提供旧推理流程或 OOM 降级承诺。历史模型参数、宿主版本和性能记录见下方历史入口。
- **edge**：默认晓晓 zh-CN-XiaoxiaoNeural，经在线 edge-tts 生成音频，再由 FFmpeg 转为 48k mono s16，应用同一 atempo/归一链；不使用本地模型或 GPU。
- **atempo 方向**：`atempo=X` 表示播放速度 X 倍，输出时长为输入/X；贴预算必须 **`atempo = raw / budget`**。当前唯一实现是 `audio_processing.atempo_chain`，golden 单测钉死 raw=9.14 / budget=7.62 → `atempo=1.199475`。
  单级域 [0.5, 2.0]，域外拆成若干同值级；raw≈budget（|r-1|<1e-9）返回空串，不变速。
- **落点与格式**：`apply_atempo` 用 `apad` + `-t budget` 长裁短补，并转为 48kHz/mono/pcm_s16le；预算计算中的 20ms 句尾保护不变。
- **峰值归一**：默认 -6dBFS，峰值与目标相差小于 0.5dB 时不改写；`normalize` 原位替换音频，不是只读分析。

## 音色参考要求

`tts.voice_ref` 是旧 IndexTTS 路径传入 worker 的参数，当前拒绝入口不读取它；edge 不使用此音色参考。配置此键不意味着已恢复克隆能力。

## 测试

```bash
cd apps/tts && env python3 -m unittest discover -s tests -t . -v   # 零模型零 GPU 零网络（ffmpeg 合成 KB 级 wav）
# 或仓根统一入口：bash scripts/run_all_tests.sh（自动发现 apps/*/tests）
```

覆盖：atempo golden 四例（方向/域外双级/相等无滤镜）、spec 预算（vt_A1 单片窗 228 帧→7.62、
两片窗链和、非 vt_ 跳过）、levelnorm（-20dBFS→-6±0.5、幂等）、manifest 字段 + final 格式
（ffprobe 断言 48000/mono/pcm_s16le，repack 兼容）。

## 历史依据与当前限制

2026-08 的 voice-ime 窗口实测、2026-09-11 的 atempo 落点修正，以及旧模型/解释器/音色参考记录保留在[固定历史全文](https://github.com/CongliangK/framivra/blob/f596efc5e0712d6c47c1e906f31fdd6f6c4e51f4/apps/tts/README.md)；这些记录不证明当前宿主就绪，也不解除当前 worker 阻断。历史 `duration_factor` 是模型时长倍率，与 atempo 方向相反，不是当前 edge 链的参数。

- edge-tts 依赖在线接口；本仓不保证服务可用性或输出权利。
- 纯函数、合成 WAV 和 mock 测试不等于实际在线合成、模型推理或 GPU 验收。
