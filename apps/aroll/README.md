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

- 双系统音频同步由 [sync](../sync/README.md) 提供；降噪由 [denoise 插件](../../plugins/denoise/README.md) 提供。命令、处理链和环境要求以各组件说明为准。
- **迁移/新机配置**：在仓库根运行 `bash plugins/denoise/denoise_init.sh` 探测已有 ffmpeg / denoise 环境 / 权重；旧 `--download` 为 BLOCKED。缺件准备见 [denoise 依赖环境](../../plugins/denoise/README.md#依赖环境requires)，许可边界见 [第三方许可](../../docs/third-party-licenses.md)。
- ASR 的配置与开启步骤见 [ASR 说明](../asr/README.md#开启步骤默认关闭)；denoise 的 timeline 键用法见其组件说明。

## 用法

以下命令在仓库根运行，会向指定输出目录写入 WAV 与报告；运行前确认输入与输出路径。

```bash
env python3 apps/aroll/aup3_extract.py <项目.aup3> <输出目录>   # 提取各轨 WAV + structure.json 报告
env python3 apps/aroll/trim_takes.py "<提取目录>" "<take输出目录>"  # 按 VAD 掐头去尾
```

`trim_takes.py` 只扫描提取目录顶层的 `track*.wav`，输入须与提取器的输出格式一致：固定 44 字节头的单声道小端 IEEE float32 WAV。裁剪保持输入采样率（例如 48 kHz）。整数 PCM WAV 不兼容；若首个匹配文件就是这种格式，会报错，可能已建输出目录，但不会写出裁剪 WAV 或索引。

裁剪不是原位操作。请使用仓外新建的独立 take 输出目录，确保输出路径不指向任何输入文件。裁剪会在该目录写入 `takeNN_*.wav` 和 `takes_index.json`，覆盖同名产物。索引中的 `*_samples` 是采样数，`*_s` 是按输入采样率换算并舍入到毫秒的秒数。
