# denoise 插件（缝 A · prepare 源产物钩子）

口播陷波 + Resemble 仅降噪链，以 timeline 键挂进 vtx prepare 阶段（v2 §2.2 缝 A，
ARCHITECTURE.md 设计权威）。`scripts/denoise_voice.py` / `scripts/denoise_init.sh`
旧路径留相对 symlink 兼容。

## timeline 键用法（管线内）

```json
"sources": [
  {"id": "voice01", "path": "assets/voice01.wav", "denoise": true}
]
```

- 值 `true` 或 `{...可选参数}`（值参与参数摘要：键值变 → 产物键变 → 旧产物由
  gc 回收、回执指纹失效）。
- 声明即该源的 prepare 音频走降噪链，产物落内容寻址键
  `cache/audio/{audio_key}::denoise:{version}:{digest}.wav`；**裸提取键
  `cache/audio/{audio_key}.wav` 不被污染**（未声明键的源恒直抽裸 wav）。
- 混音/区间渲染自动读降噪产物（与 prepare 同一解析，无特判）；不写键 = 无行为。
- 卸载/禁用插件后带键 timeline 在 validate 即报错（三分支语义，§2.1 不变量 ③）。

## CLI 用法（离线手动，命令面不变）

```bash
env python3 plugins/denoise/denoise_voice.py <in.wav> <out.wav>
```

## 依赖环境（requires）

- ffmpeg（系统包）
- conda denoise 环境：`~/miniconda3/envs/denoise`（python 3.10 + resemble-enhance
  + CPU torch；`DENOISE_PY` 环境变量可覆盖解释器路径）
- Resemble 权重：`~/models/resemble-enhance/enhancer_stage2`（约 681MB；
  `RESEMBLE_WEIGHTS` 环境变量可覆盖根路径）

一键探测/下载：`bash plugins/denoise/denoise_init.sh [--download]`
（权重缺省走 hf-mirror.com 直连，渠道实测记录见该脚本头注释）。

## 环境缺失的两级口径

- **渲染链 fail-hard**：prepare 期缺 conda/权重 → 打印缺什么 + exit 1，**不回落
  裸音频**（回落 = 产物标注降噪实未降噪，不可信）。
- **health 探针不崩**：`vt doctor` 报 unhealthy 并给修复指引（安装期探测口径）。

## 测试

`plugins/denoise/tests/`（run_all_tests.sh 统一发现）：默认单测 mock subprocess
断言命令序列与键语义；`VTX_TEST_DENOISE=1` 门控真跑 conda 链的集成测试。
