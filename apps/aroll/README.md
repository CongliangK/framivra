# aroll —— A-roll 实拍素材处理工具

组件声明见 `app.json`（kind=app）；`health_check.py` 只做语法探测，不执行素材处理。

真人出镜素材（相机录制 + Audacity 多轨录音）的前期处理工具。现行全链流程见 [A-roll 通用手册](../../templates/tech-talk/references/aroll-pipeline.md)；[2026-09-07 首跑实录](https://github.com/CongliangK/framivra/blob/f1efe5f688b543ca11902c9538dec02a5c265961/apps/aroll/docs/A-roll实拍素材处理流程-2026-09-07.md) 保留原项目数据与排障过程。

## 文件清单

| 文件 | 用途 | 依赖 |
|---|---|---|
| `aup3_extract.py` | Audacity 3.x `.aup3`（SQLite）解析器 → 逐轨提 WAV（格式对照官方 ProjectSerializer.cpp 逆向，删除片段可从孤儿块恢复） | python3 + numpy |
| `trim_takes.py` | take 掐头去尾（带通 RMS VAD + 气口外扩） | python3 + numpy |
| `health_check.py` | app 健康探针（`app.json` health.command 消费；纯语法检查） | python3 标准库 |
| `REPORT.md` | 首次运行的解析报告样例（留档） | — |

## 大模型依赖（默认关闭）

全链中的 ASR 文本配对（Qwen3-ASR sidecar `127.0.0.1:18085`）与 Resemble 降噪属大模型能力，默认关闭；其中：

- `sync_audio.py`（GCC-PHAT 双系统音频同步）已随 P2a 迁入 **`apps/sync/`**（本 app 的姊妹应用）；`denoise_voice.py` 真身在 **`plugins/denoise/`**（P2b 迁入，`../../scripts/` 仅为兼容转发壳），链为 v2：规范化 → Resemble 仅降噪（conda）→ afftdn 谱降噪 + 回 48kHz（v1 陷波级已弃用，R-20 P2 定案）。运行分别需要 conda `denoise` 环境（numpy/scipy/soundfile/soxr）与 `~/models/resemble-enhance` 权重。
- **迁移/新机配置**：在仓库根运行 `bash plugins/denoise/denoise_init.sh` 探测已有 ffmpeg / denoise 环境 / 权重；旧 `--download` 为 BLOCKED。缺件准备见 [denoise 依赖环境](../../plugins/denoise/README.md#依赖环境requires)，许可边界见 [第三方许可](../../docs/third-party-licenses.md)。
- 开启状态记录在仓库根 `config.local.json`（asr / tts 项）。

## 用法

```bash
env python3 apps/aroll/aup3_extract.py <项目.aup3> <输出目录>   # 提取各轨 WAV + structure.json 报告
env python3 apps/aroll/trim_takes.py <提取目录> <take输出目录>  # 按 VAD 掐头去尾
```
