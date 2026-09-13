# aroll —— A-roll 实拍素材处理工具

> **v2 结构（P2a 批次 1 起本目录迁入 `apps/aroll/`）**：manifest 见 `app.json`（kind=app；健康检查 = `health_check.py` 语法探针）；兼容 symlink：仓库根 `aroll → apps/aroll`（目录级，旧路径引用仍可达）。

真人出镜素材（相机录制 + Audacity 多轨录音）的前期处理工具。全链流程手册见 `docs/A-roll实拍素材处理流程-2026-09-07.md`（掐头去尾→ASR 配对→GCC-PHAT 双系统同步→口型后移→降噪→头尾切割→头部检测 crop）。

## 文件清单

| 文件 | 用途 | 依赖 |
|---|---|---|
| `aup3_extract.py` | Audacity 3.x `.aup3`（SQLite）解析器 → 逐轨提 WAV（格式对照官方 ProjectSerializer.cpp 逆向，删除片段可从孤儿块恢复） | python3 + numpy |
| `trim_takes.py` | take 掐头去尾（带通 RMS VAD + 气口外扩） | python3 + numpy |
| `health_check.py` | app 健康探针（`app.json` health.command 消费；纯语法检查） | python3 标准库 |
| `REPORT.md` | 首次运行的解析报告样例（留档） | — |

## 大模型依赖（默认关闭）

全链中的 ASR 文本配对（Qwen3-ASR sidecar `127.0.0.1:18085`）与 Resemble 降噪属大模型能力，默认关闭；其中：

- `sync_audio.py`（GCC-PHAT 双系统音频同步）已随 P2a 迁入 **`apps/sync/`**（本 app 的姊妹应用）；`denoise_voice.py`（陷波→Resemble 降噪链）仍在 `../../scripts/`（P2b 迁 plugins/denoise）。运行分别需要 conda `denoise` 环境（numpy/scipy/soundfile/soxr）与 `~/models/resemble-enhance` 权重。
- **迁移/新机配置**：运行 `bash ../../plugins/denoise/denoise_init.sh` 探测三项依赖——ffmpeg / denoise 环境 / 681MB 权重（原 scripts/denoise_init.sh 经兼容 symlink 可达），缺权重时 `--download` 走 hf-mirror 直连下载（国内实测 2.0 MB/s；HF 走代理 3.9 MB/s 备选，HF 直连不可达，ModelScope 无此模型）。模型分发不走自建 Gitea LFS——服务器公网带宽仅 ~0.5 MB/s，无分发价值（2026-09-11 实测定案）。
- 开启状态记录在仓库根 `config.local.json`（asr / tts 项）。

## 用法

```bash
env python3 apps/aroll/aup3_extract.py <项目.aup3> <输出目录>   # 提取各轨 WAV + structure.json 报告
env python3 apps/aroll/trim_takes.py <提取目录> <take输出目录>  # 按 VAD 掐头去尾
```
