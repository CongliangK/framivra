# duck-static 插件（缝 B · 音轨事件内存变换钩子）

床乐全局增益 + 口播窗静态避让（voice-ime D32/D34/D35 决策：静态分段替代
sidechaincompress，规避长流异常），以 timeline 键挂进 vtx 混音 graph（v2 §2.2
缝 B，ARCHITECTURE.md 设计权威）。`scripts/bed_static_duck.py` 旧路径留相对
symlink 兼容。

## timeline 键用法（管线内，推荐）

```json
"audio_tracks": [
  {"name": "voice", "events": [{"src": "v", "in": 0, "out": 100, "volume": 0.8}, ...]},
  {"name": "bed", "duck_strategy": {"mode": "static"}, "events": [...]}
]
```

- 对象参数缺省化（缺省对齐 bed_static_duck CLI 默认）：
  `global_db: -8`｜`duck_db: -4`｜`voice_track: "voice"`｜`merge_gap: 20`｜
  `attack: 5`｜`release: 8`（帧）。
- 纯内存变换：混音 graph 构建前对声明轨做「口播窗边界切段 + 音量
  ×10^(global_db/20)（窗内再 ×10^(duck_db/20)）+ src_in 连续 + 首末段保留原
  淡入出 + 避让段微淡入出（attack/release）」，**不写回 timeline**（幂等天然
  成立）；区间渲染走同一缝，窗口模式自动正确。
- validate 钩子：`duck:true` 与 `duck_strategy` 同轨互斥报错；`_meta.
  bed_static_duck` 存在即报错（防已烘焙 timeline 双施——先从
  timeline.pre-duck.json 备份恢复）；键值形状（mode/数值/voice_track）早失败。
- 不写键 = 无行为；卸载/禁用后带键 timeline 在 validate 即报错（三分支语义）。

## CLI 用法（离线手动烘焙，命令面与 _meta 标记语义不变）

```bash
env python3 plugins/duck-static/bed_static_duck.py -t timeline.json \
    [--track bed] [--voice-track voice] [--global-db -8] [--duck-db -4] \
    [--merge-gap 20] [--attack 5] [--release 8] [--no-backup]
```

CLI 直接改写 timeline 并写 `_meta.bed_static_duck` 标记（幂等：同参数跳过）；
插件键与 CLI 烘焙互斥（见上）。变换数学两者同一实现
（bed_static_duck.transform_events），脚本烘焙与管线渲染永不分叉。

## 依赖环境

无外部依赖（纯帧域数学 + 内核 ffmpeg 混音链）；health 探针 =
`bed_static_duck.py --help`。

## 测试

`plugins/duck-static/tests/`（run_all_tests.sh 统一发现）：窗口合并/边界切段/
音量数学/微淡入出/不改性入参/互斥与双施报错/多轨声明/参数缺省；
真实 ffmpeg 混音集成 `VTX_TEST_DUCK_INTEG=1` 门控。
