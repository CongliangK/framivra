# bgm-render（插件）——视频层直接引用 music_tools 工程 JSON

> 缝 A（prepare 源产物）+ 缝 C（probe 虚拟源 facts），协议 v1.2（2026-09-14 P6；
> v1.1.0 stem 分轨 + 共享渲染缓存；v1.2.0 R1-F 修复批：目录型 extra_deps 递归
> 签名 / gc 键级回收陈旧渲染缓存 / 产物残缺自愈 / CLI 通道存在性门，§2.5 条 8/9）。
> 需求：视频 timeline 的音轨直接引用 MusicTools 的工程/时间线 JSON（类比 gfx 轨
> 直接引用 HTML 文件）——工具自动调 music_tools 渲染音频；**有缓存零渲染命中直用，
> 无缓存自动重渲**。BGM 路由声明本体在应用件 `apps/bgm/`（姊妹仓 music_tools）。

## 用法

timeline.json 的 sources[] 里把音乐工程 JSON 当源引用，加 `bgm` 键：

```json
{
  "sources": [
    {"id": "bgm_main",
     "path": "../../music_project/works/ep42/project.json",
     "bgm": {"output": "master"}}
  ],
  "audio_tracks": [
    {"name": "bed", "duck": true, "events": [
      {"src": "bgm_main", "in": 0, "out": 9000, "volume": 0.8, "loop": true}
    ]}
  ]
}
```

### 分轨直引（v1.1.0，`stem:<轨名>`）

视频侧自己混音的场景（如 voice-ime：13 条 stem 各挂独立音轨 volume 0.08~0.12
+ duck，对位旧片分轨 flac）——每源取一轨的**总线分轨**
`stems/<轨名>.bus.wav`（含该轨 gain/pan/space 的 float32 工程全长 bus，
music_tools mixer 产出），避开母带 loudnorm(-14 LUFS) 与母带 IR 尾：

```json
{
  "sources": [
    {"id": "bgm_piano_sal", "path": "../../music_project/out/voiceime_bgm_v2/project.json",
     "bgm": {"output": "stem:bgm-piano-sal"}},
    {"id": "bgm_drums",    "path": "../../music_project/out/voiceime_bgm_v2/project.json",
     "bgm": {"output": "stem:bgm-drums"}}
  ],
  "audio_tracks": [
    {"name": "piano_sal", "duck": true, "events": [
      {"src": "bgm_piano_sal", "in": 0, "out": 16746, "volume": 0.1, "loop": true}]},
    {"name": "drums", "duck": true, "events": [
      {"src": "bgm_drums", "in": 0, "out": 16746, "volume": 0.12, "loop": true}]}
  ]
}
```

轨名即工程 `tracks[].name`（可用 `bgm show structure --json <工程>` 查）；
值域 `^[A-Za-z0-9][A-Za-z0-9_.-]*$`（validate 拒绝路径分隔符/`..` 穿透）。
分轨时长 = 工程时长（stem 与成片等长），事件时长校验口径与 master 相同。

| 参数 | 默认 | 说明 |
|---|---|---|
| `output` | `master` | `master` = 母带终态（loudnorm -14 LUFS / -1 dBTP，B 站口径，交付即用）；`premaster` = 母前混音；`stem:<轨名>` = 该轨总线分轨（video 侧自行混音） |
| `extra_deps` | `[]` | 深度依赖（相对音乐工程目录的路径数组，**文件或目录均可**，如采样库）——内容进产物键摘要，改了自动重渲 |

`"bgm": true` 等价于全默认。不写键的源照旧按媒体文件处理（零行为）。

**extra_deps 目录的签名口径（v1.2.0）**：目录按**递归内容签名**（每个文件的
相对路径 + sha256 排序后整体哈希）——目录内改文件/增/删文件都会翻转产物键；
`__pycache__`、`.lock`、`.tmp` 伴生件豁免（不计入，防无意义抖动）。护栏：
文件数 > 5000 或累计 > 2GiB 时降级为 (相对路径, size, mtime_ns) 清单签名
（GB 级采样库全量哈希会把 validate/prepare 拖到分钟级），降级事实经签名前缀
`DIRL:`（内容签名为 `DIRC:`）进 digest JSON，可观测。

## 行为

1. **validate**（缝 C）：`bgm show structure --json` 取工程时长（与 render 的
   build_render_plan 同口径；mix 被 atrim 定长到该时长）→ 事件时长校验拿真实
   帧数，工程 JSON 不触发 ffprobe「无音频轨」误报。分轨输出同口径。
2. **prepare**（缝 A，v1.1.0 共享渲染缓存）：整工程渲染**一工程一次**，落
   `cache/bgm/<render_digest>/`（render_digest = 工程字节 + $ref 件内容 +
   extra_deps 内容 + 插件代码摘要，**不含输出选择与轨名**——13 个 stem 源指向
   同一工程也只渲 1 次，各源从共享产物派生）；渲染成功原子写 `_complete.json`
   标记，`<digest>.lock` flock 防多进程双写，缓存命中零渲染直派生。各源取
   master/premaster/`stems/<轨名>.bus.wav` 转 48kHz stereo s16（与内核裸提取
   同格式；bus stem 本就 48k，同采样率直通零重采样损失）落内容寻址键
   `cache/audio/{audio_key}::bgm:{version}:{digest}.wav`——digest = bgm 键值 +
   插件代码摘要 + **$ref 拆分件内容** + extra_deps 内容。
   - 产物键缓存命中（同工程同参数派生过）→ 秒回零渲染；
   - 工程/参数/$ref/extra_deps/插件代码任一变化 → 新键自动重渲
     （轨名变更只翻转产物键、不翻转 render_digest → 零重渲直接派生新 stem）；
   - **产物残缺自愈（v1.2.0）**：`_complete.json` 完好但目标 wav 缺失/空文件
     （被外部删/截断）→ 自动判共享缓存不完整，覆盖重渲一次——不再 fail-hard
     要人手删目录；轨名不在工程轨集属工程侧问题（重渲治不了），仍 fail-hard
     列出可用轨名；
   - **gc 键级回收（v1.2.0）**：`vtx gc` 常规回收旧产物键（contribute_keys
     白名单保活当前键）；共享渲染目录 `cache/bgm/` 按 **render_digest 键级**
     回收——timeline 在用的 digest 目录保活（cache_dir_keys 白名单），陈旧
     digest 目录（工程/代码演进换键后遗留，每个 ≈整工程渲染产物）`--dry-run`
     列出、`gc` 实删，不再无界累积；`<digest>.lock` 伴生件豁免（防删锁破
     跨进程互斥）；全项目弃用 bgm 键后 cache/bgm/ 整体作 legacy 回收。
3. **下游无感**：音轨事件、混音响度终链、audio_report、区间预览全部把它当普通
   音频 wav 源（resolve_source_wav 统一解析）。

## 深度依赖与回执失效

- 工程 JSON 本身：stat 身份天然在回执指纹（receipt_fingerprint 的源成分）——
  改工程必失效重渲。
- `$ref` 拆分件（music_tools schema v2）：内容在**产物键**摘要（缓存侧自动失效）；
  如需回执级（timeline 字节不变也翻转 receipt），把 $ref 件路径一并列入
  `project.cache_dependencies`。
- 采样库/SF2/SFZ（immutable 资产库）默认不追踪——动了库请 bump 工程或用
  `extra_deps` 显式声明（文件/目录均可；目录走递归内容签名，口径见上文）。

## config

```json
{
  "bgm": {
    "enabled": true,
    "repo": "~/文档/01_活跃项目/music_tools",
    "cli": "~/.local/bin/bgm"
  }
}
```

`bgm.cli` 优先（本机包装脚本钉 miniconda python + PYTHONPATH）；指向不存在/
不可执行路径时该通道视为不可用，落 `~/.local/bin/bgm`，再落
`python3 -m bgm_gen`（PYTHONPATH=`bgm.repo`）——三通道全败才 fail-hard
（打印逐通道缺什么，含 config 通道的具体坏路径 + exit 1，不静默回落裸提取）。

`bgm render ... --quiet` 与 `bgm show structure --json` 的 stdout（包括前置日志和尾部 JSON）须为 UTF-8，成功退出但含非法字节时插件会在解析及写入 `_complete.json` 前拒收。

## 关停测试（§0.2）

卸载本插件：写了 `bgm` 键的 timeline validate FAIL（三分支报「未安装——vt plugins
list 查看」），删键换预渲染 wav 即恢复——主链不受影响。

## 测试

`env python3 -m unittest discover -s tests`（本目录，伪 bgm CLI 桩验证双缝/缓存
身份/摘要翻转/三分支/共享渲染缓存恰好一次渲染/分轨 fail-hard；集成例
`VTX_TEST_BGM_INTEG=1` 门控跑真链）。
