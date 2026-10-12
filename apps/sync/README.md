# sync · 双系统同步与实录替换（**管线前置素材工具**子类）

v2 应用（P2a 批次 1 自 `scripts/sync_audio.py`、`scripts/repack_voice.py` 迁入；manifest 见 `app.json`，兼容 symlink：`scripts/` 下同名件 → 本目录）。

> **子类标注（ARCHITECTURE §3.1）**：本 app 属「管线前置素材工具」——不进 timeline 管线、无插件钩子；但两件都邻接**不可逆写操作**（repack 替换 voice 轨源、sync 微重采样 conform），纪律 = **幂等 + 备份**：repack 首次运行自动备份占位到 `final_tts_backup/`，替换后必须清 `cache/audio/vt_*.wav` 重跑 prepare/audio（脚本结束打印完整序列）；sync 输出为新 out.wav，不覆写输入，conform 后复验残差 max<1.5ms 达标才判过。

## 用途

| 件 | 用途 |
|---|---|
| `sync_audio.py` | 双系统音频同步（无标记）：相机轨 × 话筒轨滑动窗 GCC-PHAT 互相关 → 稳健线性漂移拟合 → soxr 微重采样 conform（D26；视频永不动，conform 的总是音频）。异常窗=素材掉样/跳变报警 |
| `repack_voice.py` | 真人录音替换 voice 轨 TTS 占位源（D29 T4）：按窗口 take 替换 `assets/voice_tts/final/<窗口ID>.wav`，事件窗口/字幕不动；复用源特则 = 多片窗一份连续 take 按能量谷自动切分 |

## 依赖

- 硬依赖（app 级，manifest requires）：ffmpeg + numpy（repack_voice 系统 python3 即可跑）
- `sync_audio.py` 须在 **conda `denoise` 环境**运行（numpy/scipy/soundfile/soxr；scipy 仅该环境提供，doctor 的系统解释器探不到，故不进 app 级 requires）：`~/miniconda3/envs/denoise/bin/python apps/sync/sync_audio.py ...`
- 缺第三方依赖时（如系统 python3 无 scipy）不抛裸 traceback：脚本给中文指引（denoise 解释器优先读 `config.local.json` `denoise.python`，无则默认约定路径；附 pip 备选）并以专用退出码 **3** 结束；`--help` 零第三方依赖即可用（A4）
- 全链依赖探测：`bash plugins/denoise/denoise_init.sh`（ffmpeg / denoise 环境 / Resemble 权重三探；原 scripts/denoise_init.sh 经兼容 symlink 可达）

## 健康检查

`env python3 apps/sync/sync_audio.py --help` 退出码 0（app.json health.command）。

## 开启步骤

```bash
# 双系统同步（conda denoise 环境）
~/miniconda3/envs/denoise/bin/python apps/sync/sync_audio.py <camera.(wav|mp4|mov)> <mic.wav> <out.wav> --report report.json
# 实录替换（项目根）
env python3 apps/sync/repack_voice.py -t timeline.json [--takes-dir assets/voice_takes] [窗口ID ...]
# 替换后（缺一不可，脚本结束也会打印）：
rm <项目>/cache/audio/vt_*.wav && env python3 vtx/vtx.py -t timeline.json prepare && ... audio && ... mux && ... qc
```
