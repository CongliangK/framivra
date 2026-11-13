# bgm-render · 内置音乐工程音源插件

通过 `validate`、`probe_source`、`prepare_source` 三个 hooks，把 [仓内音乐组件](../../apps/bgm/README.md)的工程作为视频音源。音乐本体随 Framivra 维护；不查找旧音乐仓、外部 music skill 或本机 `bgm` 包装脚本。音源、模型和第三方素材由用户自行提供。

## 用法

以下为时间线片段，不会自行执行或下载资源：

```json
{
  "sources": [
    {"id": "music", "path": "music/project.json", "bgm": {"output": "master"}}
  ],
  "audio_tracks": [
    {"name": "bed", "duck": true, "events": [
      {"src": "music", "in": 0, "out": 120, "volume": 0.2, "loop": true}
    ]}
  ]
}
```

| 参数 | 默认 | 语义 |
|---|---|---|
| `output` | `master` | 音乐母带；必须 `qc_ok: true` 才接收 |
| `output: premaster` | — | 母前混音，供视频侧终混，不套用音乐母带响度要求 |
| `output: stem:<轨名>` | — | `stems/<轨名>.bus.wav` 总线分轨，含该轨 gain/pan/space；不是引擎裸分轨，也不含母带 IR 尾 |
| `extra_deps` | `[]` | 相对音乐工程目录的外部依赖文件/目录，参与产物及回执身份；非标准 SFZ 外链、工具的额外动态库等须显式声明 |

`bgm: true` 等于默认参数；没有 `bgm` 键则不调用音乐组件。多个源可以引用同一工程的不同分轨，仍只渲染一次。轨名须符合 `^[A-Za-z0-9][A-Za-z0-9_.-]*$`（整串匹配）。只读查看轨名：

```bash
python3 apps/bgm/bgm.py show structure --json /绝对路径/音乐工程.json
```

音乐内部节拍/采样率保持原工程语义；probe 的帧数为 `ceil(duration_s * fps)`，来自与渲染同口径的工程时长。prepare 将所选产物转成 48 kHz、stereo、s16 音源；不会因内置迁移修改既有混音增益。最终音频仍须通过视频 `audio/mux/qc`，音乐 QC 不替代成片验收。

## 运行配置

```json
{"bgm": {"enabled": true, "python": null, "runtime": null}}
```

固定调用仓内 `apps/bgm/bgm.py`；`bgm.python` 可指定已有 Python 解释器（不是命令串），缺省从系统环境解析。`bgm.runtime` 可指定用户运行配置；相对路径以生效的 VTX_CONFIG 文件目录为基准，子进程通过 `MUSIC_RUNTIME` 接收。否则遵循音乐组件的环境变量/默认数据根规则：`$XDG_DATA_HOME/framivra/music`，缺省 `~/.local/share/framivra/music`，其中 `runtime.json` 为可选运行配置、`assets/` 为用户资产。旧 `bgm.repo` / `bgm.cli` 已退役，不再改变调用目标。

未安装音乐组件或配置的解释器不可执行会明确失败；没有音乐源的普通视频不要求音乐环境。基础音乐依赖、可选引擎和本机资产搬迁见 [应用 README](../../apps/bgm/README.md)。health 的帮助探针仅证明 CLI 可启动，不等于实际渲染通过。

## 身份、缓存与失败

- 整工程共享缓存为 `cache/bgm/<render_digest>/`；render 身份不含 output/stem，包含工程字节、`$ref`、extra_deps、插件代码和仓内音乐执行身份。
- 音乐执行身份包含业务 Python/JSON 描述符/schema/工作流内容、有效运行配置摘要、解释器/FFmpeg/配置工具文件身份、NumPy 安装记录。配置原文与凭据不写入回执。工具为 stat 身份、NumPy 为安装记录，不是完整动态库或任意外部服务的闭包证明。
- 标准 sample 根、SF2 引用文件、SFZ 根与绝对 SFZ 的所在目录自动参与；超出这些目录的 SFZ include/sample 等仍须 `extra_deps` 声明。显式依赖会同时影响缓存和回执，不必另手工重复填写 project.cache_dependencies。
- 目录依赖保留有界策略：排序的相对路径与文件内容签名；排除 `__pycache__`、`.lock`、`.tmp`。超过 5000 文件或 2 GiB 时使用 `(相对路径,size,mtime_ns)` 清单，`DIRL:` 区分 `DIRC:`，不承诺超限库恢复 mtime 后的中间字节变动检测。
- `input_identity(c)` 是只读不变量贡献函数，不新增生命周期 hook。按工程计算、按时间线聚合到阶段回执；同一 Ctx 复核重新读取身份，不永久 memo。输入在渲染期间变化拒绝发布共享完成标记。
- 共享渲染使用跨进程锁与原子 `_complete.json`；该标记证明本轮渲染完成，不替代音乐/成片 QC。母带 QC 未通过拒收 master；premaster/stem 仍作为待终混素材处理。
- 派生 WAV 使用 `cache/audio/<audio_key>__bgm_<version>_<digest>.wav`；命中无需重渲。完整标记在而目标 WAV 缺失/空时重渲一次；未知轨名明确失败，不通过重试掩盖工程错误。
- `contribute_keys` 保活派生 WAV；registry `cache_dirs` 与 `cache_dir_keys` 按 render digest 回收陈旧共享目录，锁文件不回收。卸载/禁用插件后，仍声明 bgm 的时间线明确失败；换成已生成 WAV 可继续纯内核链。

CLI 机器输出必须是 UTF-8。非零退出、非法输出不会被当作成功；模型/素材缺失不自动下载，也不回落未处理音频。

## 验证

在插件目录执行（测试会写独立临时产物）：

```bash
python3 -m unittest discover -s tests -t . -v
```

两项真实内置合成集成默认执行，无外部音源或模型依赖；其余回归使用受控渲染桩及 FFmpeg。检查范围与跳过必须逐项报告，不把帮助探针、桩测试或音源 prepare 当成全片验收。
