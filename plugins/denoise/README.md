# denoise 插件（缝 A · prepare 源产物钩子）

口播人声降噪链 v2（R-20 P2 攻坚定案）：Resemble 仅降噪 → afftdn 谱降噪 + 回
48kHz（链内延迟补偿），以 timeline 键挂进 vtx prepare 阶段（v2 §2.2 缝 A，
ARCHITECTURE.md 设计权威）。`scripts/denoise_voice.py` / `scripts/denoise_init.sh`
旧路径留跨平台转发壳兼容。

## 链版本与升级记录

| 版本 | 链 | 备注 |
|---|---|---|
| 1.0.0（2026-08-26 定案） | 陷波(ffmpeg highpass+anequalizer 三点) → Resemble 仅降噪 → 回 48k | P1 基线链（R-20 质量基线档案在档） |
| **2.0.0（R-20 P2 攻坚）** | mono s16 规范化 → Resemble 仅降噪(CPU) → afftdn(nr=12:nf=-25)@48k + aresample=48000 + atrim 链内延迟补偿 | v1 陷波级弃用（IIR 群延迟=分数相位失真，对照实测 segSNR −9.5dB 且漂移嫌疑；去嗡由 Resemble+afftdn 承接）；afftdn 25ms 算法延迟经 TRIM_48K=1200 精确扣除。升级实证=测量区对比报告（R-20 P2 报告在档） |

**结论适用域声明（修订二）**：本链升级结论适用域=与 R-20 样本库同谱系的口播
素材（单一说话人/单一录音链）；跨说话人/跨麦克风/跨环境泛化未验证，不得外推。

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

已有资产探测：`bash plugins/denoise/denoise_init.sh`；旧 `--download` 为 **BLOCKED**。
缺权重时先读 [第三方许可](../../docs/third-party-licenses.md)；
`env python3 scripts/model_license_policy.py plan resemble-enhance` 仅离线显示证据，
不下载、不安装环境、不授予许可或解除旧入口门禁。

## 环境缺失的两级口径

- **渲染链 fail-hard**：prepare 期缺 conda/权重 → 打印缺什么 + exit 1，**不回落
  裸音频**（回落 = 产物标注降噪实未降噪，不可信）。
- **health 探针不崩**：`vt doctor` 报 unhealthy 并给修复指引（安装期探测口径）。

## 测试

`plugins/denoise/tests/`（run_all_tests.sh 统一发现）：默认单测 mock subprocess
断言命令序列与键语义；`VTX_TEST_DENOISE=1` 门控真跑 conda 链的集成测试。
