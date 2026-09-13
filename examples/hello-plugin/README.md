# hello-plugin —— 音频插件完整纪律范本

> v2 插件化架构的第三方音频插件起手模板（ARCHITECTURE §4.2 / §2 协议），
> 也是验收门 6（examples 外部注册）的对照实现。

## 用途

`sources[]` 级键 `"hello_gain": {"db": N}`——声明即该源的 prepare 音频被施加
`volume=N dB`（ffmpeg volume 滤镜，无重依赖），产物内容寻址：

```
cache/audio/{audio_key}::hello:{version}:{value_digest}.wav
```

与官方 denoise 插件同为**缝 A · prepare 源产物钩子**（§2.2）：`audio_key` 经
内核 API 面 `vtx.api.audio_key` 取得；裸提取键 `cache/audio/{audio_key}.wav`
永不被插件产物污染（契约 v1.1——插件自算产物路径）。

## 依赖

- 内核 ≥ 2.0.0；ffmpeg（PATH 可寻；缺失时渲染期 fail-hard，见下）。

## 健康检查

```
env python3 examples/hello-plugin/health_check.py   # 探 ffmpeg，exit 0 = 健康
```

`plugin.json` 的 `health.type=command` 指向本脚本（探针命令必须为**插件根内
相对路径**——绝对路径与 `..` 被 manifest 校验拒绝，§2.4 安全节）。

## 开启步骤（external 注册，三分支语义的入口）

examples/ 不在 registry 内置扫描范围（内置只扫 `apps/` + `plugins/`），走
config.local.json 的 `external_plugins`（gitignored，不入库）：

```json
"external_plugins": [
  {"path": "examples/hello-plugin", "enabled": true}
]
```

只写字符串 = 注册但**默认 disabled**（§2.4 安全节：external 显式 enable 才生效）。

## 三分支怎么测（§2.1 不变量 3：装/未装/禁用）

写一个带 `"hello_gain": {"db": 3}` 源的 timeline，分别在三种注册态下跑
`env python3 vtx/vtx.py -t <tl> validate`：

| 注册态 | validate 结果 |
|---|---|
| `{"path": ..., "enabled": true}`（分支 1：已装启用） | ✓ 放行，prepare/混音吃插件产物 |
| `{"path": ...}` 或 `{"enabled": false}`（分支 3：已装被禁用） | ✗ `键 'hello_gain' ... 已安装但被禁用——启用后重跑` |
| 条目存在但安装态判为未装（分支 2，测试经 mock `plugin_state` 驱动） | ✗ `键 'hello_gain' ... 未安装——vt plugins list 查看`（安装指引而非裸 Unknown key） |
| 条目**整个移除**（= 未注册） | ⚠ `未知键 'hello_gain'` 警告不阻断 + 指纹段消失 → 旧回执失配强制重渲裸音频（见下节） |

绝不静默忽略——「缺省关闭」指不写键无行为，不是写了键可跳过。注意 external
插件的「注册面」就是 config 条目：官方插件的 registry.json 声明是永久注册
（实体目录没了仍走分支 2 报错），external 条目移除则键直接退化为未知键警告
——两条路都不可信复用旧产物（分支 2 阻断；条目移除靠指纹失配重渲）。机器可
复现的断言见 `tests/test_hello_plugin.py`（VTX_CONFIG 指向临时 config 驱动，
不碰本机 config.local.json）。

## 缓存键怎么进白名单（§2.1 不变量 1）

插件产物若不注册键，渲染中就会被 budget_gate 自动触发的 gc 当垃圾回收（gc_plan
把 `cache/audio/` 下不在当前键集内的文件一律判垃圾）。唯一通道 =
`contribute_keys(c)`（`__init__.py` 已实现）：返回本 timeline 全部声明
`hello_gain` 源的产物键 stem，由内核 `hygiene._current_keys` 并入 ak 存留集——
`vtx gc --dry` 可见插件产物不在回收清单。

## 卸载后缓存为何失效（验收门 6 对照语义）

从 `external_plugins` 移除本插件后，两条独立通道同时失效：

1. **键出白名单 → gc 回收**：插件不生效 → `contribute_keys` 空转 →
   `cache/audio/{audio_key}::hello:...wav` 不再在存留键集内 → `vtx gc` 将其列入
   回收（`gc --dry` 可先验）。
2. **指纹段消失 → 旧回执失配**：回执指纹末尾有「生效插件清单」追加段
   （`plugins:hello@1.0.0:{参数摘要}`，§2.1 不变量 2）。卸载后该段消失——录制侧
   （带段）与重算侧（无段）指纹失配，`vtx all` 拒绝复用旧 prepare/audio 产物，
   必然重走裸提取。反向同理：无插件时代录的回执，装上插件后同样失配重渲。

## 复制本目录改名起步

```bash
cp -r examples/hello-plugin examples/my-plugin && cd examples/my-plugin
# 1) 改名：目录名与 plugin.json "name" 一致（^[a-z][a-z0-9-]*$）
# 2) 换键：plugin.json extends.schema_keys 的 key 换成你的键名（path 目前冻结为
#    sources[] / audio_tracks[] 两缝，hooks 值域 = prepare_source/audio_events/validate）
# 3) 换实现：__init__.py 的 prepare_source/contribute_keys 换成你的处理链
#    （产物路径模板 {audio_key}::<plugin>:{version}:{digest}.wav 保持不变）
# 4) 注册：config.local.json external_plugins 加 {"path": "examples/my-plugin"}
# 5) 验证：env python3 vt.py doctor && env python3 vt.py plugins list
```

纪律红线（§2 协议）：三不变量缺一不可（缓存键贡献/回执指纹掺入/三分支注册态）；
环境缺失 fail-hard 不回落；禁 `spec_from_file_location`；内核能力只经 `vtx.api`。
