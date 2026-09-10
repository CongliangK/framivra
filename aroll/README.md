# aroll —— A-roll 实拍素材处理工具

真人出镜素材（相机录制 + Audacity 多轨录音）的前期处理工具。全链流程手册见 `docs/A-roll实拍素材处理流程-2026-09-07.md`（掐头去尾→ASR 配对→GCC-PHAT 双系统同步→口型后移→降噪→头尾切割→头部检测 crop）。

## 文件清单

| 文件 | 用途 | 依赖 |
|---|---|---|
| `aup3_extract.py` | Audacity 3.x `.aup3`（SQLite）解析器 → 逐轨提 WAV（格式对照官方 ProjectSerializer.cpp 逆向，删除片段可从孤儿块恢复） | python3 标准库 |
| `trim_takes.py` | take 掐头去尾（带通 RMS VAD + 气口外扩） | python3 标准库 |
| `REPORT.md` | 首次运行的解析报告样例（留档） | — |

## 大模型依赖（默认关闭）

全链中的 ASR 文本配对（Qwen3-ASR sidecar `127.0.0.1:18081`）与 Resemble 降噪属大模型能力，默认关闭；其中：

- `sync_audio.py`（GCC-PHAT 双系统音频同步）与 `denoise_voice.py`（陷波→Resemble 降噪链）**已收编在 `../skill/scripts/`**（同仓快照内），运行分别需要 conda `denoise` 环境（numpy/scipy/soundfile/soxr）与 `~/models/resemble-enhance` 权重。
- 开启状态记录在仓库根 `config.local.json`（asr / tts 项）。

## 用法

```bash
python3 aup3_extract.py <项目.aup3> <输出目录>   # 提取各轨 WAV + structure.json 报告
python3 trim_takes.py <track.wav> ...            # 按 VAD 掐头去尾
```
