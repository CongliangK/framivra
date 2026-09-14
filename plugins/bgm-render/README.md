# bgm-render（插件）——视频层直接引用 music_tools 工程 JSON

> 缝 A（prepare 源产物）+ 缝 C（probe 虚拟源 facts），协议 v1.2（2026-09-14 P6）。
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

| 参数 | 默认 | 说明 |
|---|---|---|
| `output` | `master` | `master` = 母带终态（loudnorm -14 LUFS / -1 dBTP，B 站口径，交付即用）；`premaster` = 母前混音 |
| `extra_deps` | `[]` | 深度依赖（相对音乐工程目录的路径数组，如采样/参考件）——内容进产物键摘要，改了自动重渲 |

`"bgm": true` 等价于全默认。不写键的源照旧按媒体文件处理（零行为）。

## 行为

1. **validate**（缝 C）：`bgm show structure --json` 取工程时长（与 render 的
   build_render_plan 同口径；mix 被 atrim 定长到该时长）→ 事件时长校验拿真实
   帧数，工程 JSON 不触发 ffprobe「无音频轨」误报。
2. **prepare**（缝 A）：`bgm render <工程> -o <临时目录> --quiet` → 取母带 wav →
   转 48kHz stereo s16（与内核裸提取同格式）落内容寻址键
   `cache/audio/{audio_key}::bgm:{version}:{digest}.wav`——digest = bgm 键值 +
   插件代码摘要 + **$ref 拆分件内容** + extra_deps 内容。
   - 缓存命中（同工程同参数渲染过）→ 秒回零渲染；
   - 工程/参数/$ref/extra_deps/插件代码任一变化 → 新键自动重渲；
   - `vtx gc` 常规回收旧键（contribute_keys 白名单保活当前键）。
3. **下游无感**：音轨事件、混音响度终链、audio_report、区间预览全部把它当普通
   音频 wav 源（resolve_source_wav 统一解析）。

## 深度依赖与回执失效

- 工程 JSON 本身：stat 身份天然在回执指纹（receipt_fingerprint 的源成分）——
  改工程必失效重渲。
- `$ref` 拆分件（music_tools schema v2）：内容在**产物键**摘要（缓存侧自动失效）；
  如需回执级（timeline 字节不变也翻转 receipt），把 $ref 件路径一并列入
  `project.cache_dependencies`。
- 采样库/SF2/SFZ（immutable 资产库）默认不追踪——动了库请 bump 工程或用
  `extra_deps` 显式声明。

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

`bgm.cli` 优先（本机包装脚本钉 miniconda python + PYTHONPATH）；留空则按
`~/.local/bin/bgm` 在盘与否回落 `python3 -m bgm_gen`（PYTHONPATH=`bgm.repo`）。
三通道全败 → validate/prepare fail-hard（打印缺什么 + exit 1，不静默回落裸提取）。

## 关停测试（§0.2）

卸载本插件：写了 `bgm` 键的 timeline validate FAIL（三分支报「未安装——vt plugins
list 查看」），删键换预渲染 wav 即恢复——主链不受影响。

## 测试

`env python3 -m unittest discover -s tests`（本目录，伪 bgm CLI 桩验证双缝/缓存
身份/摘要翻转/三分支）。
