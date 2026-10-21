# hello-plugin —— 音频插件完整纪律范本

> v2 插件化架构的第三方音频插件起手模板（ARCHITECTURE §4.2 / §2 协议），
> 也是验收门 6（examples 外部注册）的对照实现。

## 用途

`sources[]` 级键 `"hello_gain": {"db": N}`——声明即该源的 prepare 音频被施加
`volume=N dB`（ffmpeg volume 滤镜，无重依赖），产物内容寻址：

```
cache/audio/{audio_key}__hello_{version}_{value_digest}_{code_digest}.wav
```

与官方 denoise 插件同为**缝 A · prepare 源产物钩子**（§2.2）：`audio_key` 经
内核 API 面 `vtx.api.audio_key` 取得；裸提取键 `cache/audio/{audio_key}.wav`
永不被插件产物污染（契约 v1.1——插件自算产物路径）。代码摘要经
`vtx.api.plugin_code_digest` 取得，实际命名以 [`product_path`](./__init__.py) 为准。

## 依赖

- 内核 ≥ 2.0.0；ffmpeg（PATH 可寻；缺失时渲染期 fail-hard，见下）。

## 健康检查

```bash
# 在仓库根运行；只探测 PATH，不执行 ffmpeg。
env python3 templates/hello-plugin/health_check.py   # 探 ffmpeg，exit 0 = 健康
```

`plugin.json` 的 `health.type=command` 指向本脚本（探针命令必须为**插件根内
相对路径**——绝对路径与 `..` 被 manifest 校验拒绝，§2.4 安全节）。

## 开启步骤（external 注册，三分支语义的入口）

通用的发现、注册、路径安全与 doctor 步骤见[第三方接入](../README.md#第三方接入)。本范本在仓根 `config.local.json` 中使用：

```json
"external_plugins": [
  {"path": "templates/hello-plugin", "enabled": true}
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
| 条目**整个移除**（= 未注册） | ⚠ `未知键 'hello_gain'` 警告不阻断 + 指纹段消失 → 旧回执失配（不保证重提取，见下节） |

绝不静默忽略——「缺省关闭」指不写键无行为，不是写了键可跳过。注意 external
插件的「注册面」就是 config 条目：官方插件的 registry.json 声明是永久注册
（实体目录没了仍走分支 2 报错），external 条目移除则键直接退化为未知键警告
——两条路都不能沿用旧成功回执（分支 2 阻断；条目移除使指纹失配）。机器可
复现的断言见 `tests/test_hello_plugin.py`（VTX_CONFIG 指向临时 config 驱动，
不碰本机 config.local.json）。

## 缓存键怎么进白名单（§2.1 不变量 1）

插件产物若不注册键，渲染中就会被 budget_gate 自动触发的 gc 当垃圾回收（gc_plan
把 `cache/audio/` 下不在当前键集内的文件一律判垃圾）。唯一通道 =
`contribute_keys(c)`（`__init__.py` 已实现）：返回本 timeline 全部声明
`hello_gain` 源的产物键 stem，由内核 `hygiene._current_keys` 并入 ak 存留集——
`vtx gc --dry-run` 可见插件产物不在回收清单。

## 卸载后缓存为何失效（验收门 6 对照语义）

从 `external_plugins` 移除本插件后，两条独立通道同时失效：

1. **键出白名单 → gc 回收**：插件不生效 → `contribute_keys` 空转 →
   `cache/audio/{audio_key}__hello_...wav` 不再在存留键集内 → `vtx gc` 将其列入
   回收（`gc --dry-run` 可先验）。
2. **指纹段消失 → 旧回执失配**：回执指纹末尾有「生效插件清单」追加段
   （`plugins:hello-plugin@1.0.0:{参数摘要}`，§2.1 不变量 2）。卸载后该段消失——录制侧
   （带段）与重算侧（无段）指纹失配，旧 prepare/audio 成功回执不能沿用。
   重跑时，当前裸 WAV 若通过完整缓存凭据校验仍可复用，不保证再次提取或编码。
   反向安装插件同样使无插件时代的旧回执失配。

## 复制本目录改名起步

扩展键作用域与 hooks 值域以 [manifest schema](../../docs/ARCHITECTURE.md#23-manifest-schema-v1) 为准。

```bash
# 写操作：创建新范本目录；以下命令均在仓根运行。
cp -r templates/hello-plugin templates/my-plugin
# 1) 改名：目录名与 plugin.json "name" 一致（^[a-z][a-z0-9-]*$）
# 2) 换键：plugin.json extends.schema_keys 与 __init__.py KEY 保持一致
# 3) 换实现：__init__.py 的 prepare_source/contribute_keys 换成你的处理链；
#    product_path 统一生成带输入、版本、参数及代码摘要的产物路径
# 4) 注册：config.local.json external_plugins 加 {"path": "templates/my-plugin", "enabled": true}
# 5) 验证：env VTX_DOCTOR_OFFLINE=1 python3 vt.py doctor && env python3 vt.py plugins list
```

纪律红线（§2 协议）：三不变量缺一不可（缓存键贡献/回执指纹掺入/三分支注册态）；
环境缺失 fail-hard 不回落；禁 `spec_from_file_location`；内核能力只经 `vtx.api`。
