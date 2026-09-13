# tts/ · IndexTTS-2.5 口播占位语音产线 + edge-tts 兜底

口播语音的两段式工作流：**TTS 占位（本模块，正向）→ 实录替换（`scripts/repack_voice.py` 既有链，终版）**。
来源：voice-ime/assets/voice_tts/ 产线（gen.py / run_v2.py / normalize.py / refit_final.py，2026-08 实战 27 窗）
参数化收编——硬编码窗口表 → `spec.json` 驱动；产物按 repack_voice.py 既有消费约定落位，**零协议改动**。

## 文件清单

| 文件 | 职责 |
|---|---|
| `tts_cli.py` | 总入口（系统 python3，stdlib）：`spec` / `gen` / `normalize` / `probe` |
| `spec.py` | timeline.json voice 轨 `vt_<ID>` 事件链 → spec.json（预算=Σ链帧/30+0.02s） |
| `indextts_worker.py` | IndexTTS-2.5 重活件——**由配置解释器执行**（conda indextts py3.10 / repo venv py3.11，可 import indextts/torch），模型单次加载多窗串行 |
| `edge_worker.py` | edge-tts 兜底引擎（晓晓 zh-CN-XiaoxiaoNeural，免 GPU 免模型，subprocess 二进制 + 同 atempo/归一链） |
| `atempo.py` | atempo 参数纯函数（`atempo_chain(raw_s, budget_s)`，方向 raw/budget + 域外 √ 双级链）+ ffmpeg 应用器 |
| `levelnorm.py` | 峰值归一 -6dBFS（ffmpeg volumedetect + volume，幂等） |
| `init.sh` | 幂等资产探测（repo/解释器/音色参考/edge-tts/GPU；复用本机已有资产优先）；`--download` 补齐代码克隆 + 11GB 权重（ModelScope 国内直连，确认后再跑） |
| `tests/` | unittest（atempo golden / spec 预算 / levelnorm / manifest+final 格式；零模型零 GPU 零网络） |

## 快速使用

```bash
bash tts/init.sh                                   # 1) 资产探测（幂等，只打印）
python3 tts/tts_cli.py spec -t <项目>/timeline.json -o /tmp/spec.json
# 2) 人工校对回填 spec.json 各窗 text（timeline voice 轨不含文本）
python3 tts/tts_cli.py gen /tmp/spec.json --project <项目>            # indextts（默认）
python3 tts/tts_cli.py gen /tmp/spec.json --project <项目> --engine edge   # 免 GPU 兜底
python3 tts/tts_cli.py normalize --project <项目>   # final/ 批量峰值归一（幂等，可独立重跑）
python3 tts/tts_cli.py probe                       # 快速资产探测
```

## spec.json 格式（windows[].id 即 `vt_<ID>` 去前缀）

```json
{
  "version": 1,
  "engine": "indextts",
  "voice_ref": "~/音乐/5_G_Resemble仅降噪.wav",
  "fps": 30,
  "windows": [
    {"id": "A1", "text": "首先：它是一个输入法。……", "budget_s": 7.62, "n_events": 1}
  ]
}
```

- `budget_s = max(每个事件的 src_in + (out - in)) / fps + 0.02`——与 repack_voice.py
  「事件链帧 + 20ms 句尾保护」严格同口径（TAIL_MS 同款；run_v2 的 +0.02 防取整少 1 帧余量）。
- `text` 由 `spec` 子命令置空、**人工校对后回填**（逐字稿在脚本/字幕侧，勿让模型念窗口 ID）。
- `fps`/`n_events` 为对账辅助字段，消费方只读 `id/text/budget_s`。

## 产物落位（repack_voice.py 消费契约，静态断言见 tests）

| 产物 | 位置 | 格式 |
|---|---|---|
| final 成品 | `<项目>/assets/voice_tts/final/<ID>.wav` | 48kHz / mono / pcm_s16le，时长=budget±0.02s |
| raw 中间物（22.05k / edge 转码） | `<项目>/cache/tmp/<runid>/` | 可再生中间物（hygiene tmp 纪律） |
| manifest 记账 | `<项目>/cache/reports/tts_manifest_<runid>.json` | gc 永不触碰 |

manifest 行字段（对齐 voice-ime manifest.json + peak_db）：
`{id, chars, budget_s, raw_s, duration_factor, final_s, peak_db}`（edge 引擎 duration_factor 恒 null）。

`gen` 完成后打印 repack_voice.py 同款后续命令序列（清 prepare 缓存 → prepare → audio →
mux → qc → audit；v2 §1.2：limiter 已并入 vtx audio，无需手动 post_loudness；实录终版用
`scripts/repack_voice.py` 替换）。

## 引擎与 atempo 契约（方向坑必须钉死）

- **indextts**（默认）：克隆 `tts.voice_ref` 音色（本人真声，预览音色≈终版），需 GPU + repo；
  逐窗流程 = 自然语速 infer → 偏差>7% 时 `duration_factor=clamp(budget/raw, 0.5, 2.0)`
  重生成一次 → atempo 精确贴预算 → 峰值归一。4080 RTF≈0.2，27 窗量级 2-4 分钟（voice-ime 实测）。
  OOM/失败 → 退出码 3 + 中文降级提示 `--engine edge`。
- **edge**：晓晓 zh-CN-XiaoxiaoNeural，免鉴权免 GPU，审听级占位；默认输出 24kHz mp3 →
  ffmpeg 重采样 48k mono s16 → 同 atempo/归一链。
- **atempo 方向**（gen.py 前车之鉴，R8）：`atempo=X` = 播放速度 X 倍 → 输出时长 = 输入/X，
  贴预算必须 **`atempo = raw / budget`**。voice-ime gen.py 首版写反成 budget/raw，27 窗 final
  全部反向偏离，2026-08-30 refit_final.py 实测定向修正（:19-24）。本仓以 run_v2.py 修正版为
  唯一实现，golden 单测钉死：raw=9.14 / budget=7.62 → `atempo=1.199475`。
  单级域 [0.5, 2.0]，域外双级 `atempo=√r,atempo=√r`（更极端依次再拆）；raw≈budget（|r-1|<1e-9）
  返回空串不加滤镜。
- **精确落点**：atempo 滤镜内部按帧重采样，输出有 ~0.3% 样本量化漂移（2026-09-11 集成验收
  实测 7.5989s vs 7.62 预算，占位态少 1 帧被 vtx validate 拦）——`apply_atempo` 统一以
  `apad`（无限尾静音）+ `-t budget` 收口：输出样本数恰为 round(budget×48000)，长裁短补。
  尾部 ≤20ms 静音与「链帧+20ms 句尾保护」同语义；峰值归一按 max 计不受尾静音影响。
- **duration_factor 方向**（勿与 atempo 混淆，run_v2 修正注记照录）：是 IndexTTS2 的模型参数、
  语义=时长倍率（A7 实证：自然 5.5s 传 1.21 → 6.8s），过长的句传 budget/实测（<1）说短——
  方向恰与 atempo 相反。
- **峰值归一 -6dBFS**（normalize.py 实证）：IndexTTS 克隆低电平参考致 raw 批次 mean -44~-51dB，
  直接混音会被 bed 淹没且 duck 失效；-6dBFS 明显高于 duck 阈值（-30.4dBFS）且不削波。幂等
  （已在 -6±0.5dB 内不动）。

## 音色参考要求

config `tts.voice_ref`（默认 `~/音乐/5_G_Resemble仅降噪.wav`，9.9s 本人真声）：5-10s 干声、
单一说话人、电平正常；换人声改此键即可。仅 indextts 引擎需要。

## 测试

```bash
python3 -m unittest discover -s tts/tests -v   # 零模型零 GPU 零网络（ffmpeg 合成 KB 级 wav）
```

覆盖：atempo golden 四例（方向/域外双级/相等无滤镜）、spec 预算（vt_A1 单片窗 228 帧→7.62、
两片窗链和、非 vt_ 跳过）、levelnorm（-20dBFS→-6±0.5、幂等）、manifest 字段 + final 格式
（ffprobe 断言 48000/mono/pcm_s16le，repack 兼容）。

## 已验证版本（本机资产，init.sh 探测即用）

- 模型：IndexTeam/IndexTTS-2.5 @ `~/AI/IndexTTS/repo/checkpoints`（11 文件五件套齐，
  ModelScope revision 见方案 §6.1 表 #4）；代码仓 indextts（infer_v2_5.IndexTTS2）。
- 解释器：conda indextts py3.10.19 / repo `.venv` py3.11.13（官方支持区间 3.10-3.11）。
- edge-tts @ `~/miniconda3/bin/edge-tts`（py3.13 入口脚本）；音色参考 9.9s 已核实。

## 开启步骤

1. `bash tts/init.sh`（幂等探测，缺 repo 打印两条路线；`bash tts/init.sh --download`
   补齐代码克隆 + 11GB 权重下载——权重走 ModelScope 国内直连，耗时数十分钟，确认后再跑）。
2. `config.local.json` 的 `tts.enabled` 置 `true`（键缺省时全走默认探测链，旧配置无需重生成）。
3. GPU 排程建议：TTS 批跑与 vtx video 阶段（NVENC 同卡）、ASR sidecar 错峰；同卡共驻显存
   紧张时 indextts 会 OOM 退出并提示降级 edge（预期路径）。

## 待集成事项

集成事项已于 2026-09-11 全部落地（根 README/init.py/config.example.json/edit.md 均已更新）。

## 风险

- edge-tts 依赖微软非官方公开接口（无 SLA），仅作兜底不作主力（方案 §11）。
- atempo 方向坑复发风险由 golden 单测钉死（R8）；`duration_factor` 与 atempo 方向相反，
  代码注释已照录 run_v2.py 修正注记。
- 同卡共驻（IME sidecar / ASR sidecar / NVENC）时 indextts 可能 OOM——属预期降级路径，
  退出码 3 + 提示 `--engine edge`。
