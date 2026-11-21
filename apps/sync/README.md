# sync · 双系统同步与实录替换（**管线前置素材工具**子类）

组件 manifest 见 `app.json`；命令使用本目录真实路径，回归测试见本目录 `tests/`。

> **子类标注（ARCHITECTURE §3.1）**：本 app 属「管线前置素材工具」——不进 timeline 管线、无插件钩子；但两件都邻接**不可逆写操作**（repack 替换 voice 轨源、sync 微重采样 conform），纪律 = **幂等 + 备份**：repack 首次运行自动备份占位到 `final_tts_backup/`，替换后正式交付运行完整 `all`（脚本结束打印入口），当前回执与局部诊断前置见 [runbook](../../docs/skill/references/edit.md)；sync 的写入警告与验收规则见[开启步骤](#开启步骤)。

## 用途

| 件 | 用途 |
|---|---|
| `sync_audio.py` | 双系统音频同步（无标记）：相机轨 × 话筒轨滑动窗 GCC-PHAT 互相关 → 稳健线性漂移拟合 → soxr 微重采样 conform（D26；视频永不动，conform 的总是音频）。异常窗=素材掉样/跳变报警 |
| `repack_voice.py` | 真人录音替换 voice 轨 TTS 占位源（D29 T4）：按窗口 take 替换 `assets/voice_tts/final/<窗口ID>.wav`，事件窗口/字幕不动；复用源特则 = 多片窗一份连续 take 按能量谷自动切分 |

## 依赖

- 硬依赖（app 级，manifest requires）：ffmpeg + numpy（repack_voice 系统 python3 即可跑）
- `sync_audio.py` 须在 **conda `denoise` 环境**运行（numpy/scipy/soundfile/soxr；scipy 仅该环境提供，doctor 的系统解释器探不到，故不进 app 级 requires）：解释器用 `denoise.python` 指定；默认约定为 Linux/macOS 的 `~/miniconda3/envs/denoise/bin/python` 或 Windows 的 `~/miniconda3/envs/denoise/python.exe`
- 缺第三方依赖时（如系统 python3 无 scipy）不抛裸 traceback：脚本给中文指引（denoise 解释器优先读 `config.local.json` `denoise.python`，无则默认约定路径；附 pip 备选）并以专用退出码 **3** 结束；`--help` 零第三方依赖即可用
- 全链依赖探测：`bash plugins/denoise/denoise_init.sh`（ffmpeg / denoise 环境 / Resemble 权重三探）

## 健康检查

`env python3 apps/sync/sync_audio.py --help` 退出码 0（app.json health.command）。

## 开启步骤

以下示例从 Framivra 仓库根执行；`<video_tools>` 替换为仓库绝对路径，`<项目>` 与音视频路径占位符替换为对应绝对路径。方括号表示可选部分，执行前展开或省略。

双系统同步读取 FFmpeg 能解码的相机参考音轨和话筒轨，用 soxr 微重采样并平移话筒轨。示例写出与参考音轨等长的 48 kHz 单声道 PCM16 WAV，不重封装视频。`--max-offset`、`--win`、`--hop`、`--search` 均以秒计；报告的 `a_offset_s` 用秒（终端显示毫秒），`b_drift` 用秒/秒，`drift_ppm` 用 ppm，累计漂移和拟合/复验残差用毫秒。

**写入警告：**为 WAV 和可选 JSON 分别选未存在且互不指向同一文件、也不指向任何输入的路径；脚本不防同路径或覆盖。WAV 在复验前写入；`CHECK` 也会留下 WAV、可写报告且不触发非零退出。验收看 `verdict` 或终端复验结论：有复验结果、最大绝对残差 < 1.5 ms、有效拟合窗至少 3 个，且拟合最大绝对残差 ≤ 10 ms（超过即标记疑似阶跃），才是 `OK`。

```bash
cd "<video_tools>"
# 双系统同步（conda denoise 环境）
~/miniconda3/envs/denoise/bin/python apps/sync/sync_audio.py "<camera.mp4>" "<mic.wav>" "<新输出.wav>" --report "<项目>/新报告.json"
# 实录替换（路径指向同一视频项目）
env python3 apps/sync/repack_voice.py -t "<项目>/timeline.json" [--takes-dir "<项目>/assets/voice_takes"] [窗口ID ...]
# 替换后正式交付（从仓库根执行，会更新项目 cache/、out/）：
env python3 vtx/vtx.py -t "<项目>/timeline.json" all
```

局部音频诊断须先对当前输入执行 `prepare`，再执行 `audio --report`；局部通过后仍运行完整 `all`，检查退出码与 QC/插件结果。内容寻址自动选择可复用缓存，完整链负责当前阶段回执及启用插件的 finish；完整配方见 [runbook](../../docs/skill/references/edit.md)。
