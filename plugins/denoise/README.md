# denoise 插件（缝 A · prepare 源产物钩子）

口播人声降噪链 v2（R-20 P2 攻坚定案）：Resemble 仅降噪 → afftdn 谱降噪 + 回
48kHz（链内延迟补偿），以 timeline 键挂进 vtx prepare 阶段（v2 §2.2 缝 A，
ARCHITECTURE.md 设计权威）。独立命令使用本目录的 `denoise_voice.py` / `denoise_init.sh`。

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

- 已安装且启用时，在源上写 `denoise: true`（JSON 中键须加引号）；要关闭该源的降噪就删键，`false` 不会关闭。对象值可参与产物键与回执指纹计算，但当前不改变处理参数。
- **写操作**：prepare（区间冷填充同路）在裸提取缓存未命中时调用 `prepare_source`。钩子复用有完成证书且字节匹配的派生缓存，否则读取源素材，用临时文件处理，再发布 48 kHz stereo PCM WAV 和完成证书：
  `cache/audio/{audio_key}__denoise_{version}_{value_digest}_{code_digest}.wav`。
  不改源素材、timeline 或裸提取文件 `cache/audio/{audio_key}.wav`。
- 当前 prepare 提取有裸缓存快路径：同源裸 WAV 有效时可先返回，不代表已降噪。整片/区间混音及 `audio --report` 的分轨输入仍按事件的 `src` 调用 `resolve_source_wav`，由钩子解析或生成派生音频。
- 带键但插件未安装或被禁用时，validate 报错；独立 prepare 和区间冷填充也会在查裸缓存前拒绝该源。见 [管线扩展协议](../../docs/ARCHITECTURE.md#2-管线扩展协议)。

## CLI 用法（离线手动，命令面不变）

**写操作**（从仓库根运行，使用与原件不同的输出路径）：

```bash
env python3 plugins/denoise/denoise_voice.py /path/to/in.wav /path/to/out.wav
```

读取输入 WAV 和本地模型，临时 WAV 放在系统临时目录；终步直接写指定路径（会建父目录，可覆盖已有文件），产出 48 kHz mono PCM WAV。它不读取 timeline，不登记或发布钩子的缓存键、完成证书或阶段回执。输出路径不要指向输入原件或项目缓存。

## 依赖环境（requires）

- ffmpeg（系统包）
- conda denoise 环境：`~/miniconda3/envs/denoise`（python 3.10 + resemble-enhance
  + CPU torch；`DENOISE_PY` 环境变量可覆盖解释器路径）
- Resemble 权重：根目录默认为 `~/models/resemble-enhance`，可用 `RESEMBLE_WEIGHTS` 覆盖；检查该根下的 `enhancer_stage2/ds/G/default/mp_rank_00_model_states.pt`。

旧 `--download` 为 **BLOCKED**。缺权重时先读 [第三方许可](../../docs/third-party-licenses.md)；
`env python3 scripts/policy/model_license_policy.py plan resemble-enhance` 仅离线显示证据，
不下载、不安装环境、不授予许可或解除旧入口门禁。

## 环境缺失的两级口径

- **实际使用失败**：以缺 checkpoint 为例，钩子未命中有效派生缓存时，会列出缺件并以 1 退出，不发布新派生 WAV、不回落到裸音频；命中派生缓存则不测环境。独立 CLI 每次先查解释器和 checkpoint 文件，缺件时同样退出 1，且不进入处理链、不写指定输出。链中子进程报错也不是成功；CLI 终步直接写输出，不保证失败时原子保留旧文件。
- **环境探测不是验收**：`bash plugins/denoise/denoise_init.sh` 查 PATH 上的 ffmpeg、解释器能否导入 `resemble_enhance`、checkpoint 是否非空；读 `[MISS]`，正常探测即使缺件也退出 0。`env VTX_DOCTOR_OFFLINE=1 python3 vt.py doctor` 的 denoise health 只查 ffmpeg 路径及解释器、checkpoint 文件是否存在，缺件为 `unhealthy`，不单独判内置组件的 doctor 总体失败。两者均不证明权重可加载、降噪效果或使用许可。

## 测试

`plugins/denoise/tests/`（run_all_tests.sh 统一发现）：默认单测 mock subprocess
断言命令序列与键语义；`VTX_TEST_DENOISE=1` 门控真跑 conda 链的集成测试。
