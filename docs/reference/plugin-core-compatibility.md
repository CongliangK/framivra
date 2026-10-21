# Core API × 插件协议兼容矩阵

契约唯一入口为 [ARCHITECTURE §2.3](../ARCHITECTURE.md#23-manifest-schema-v1) 的显式兼容门；钩子签名见该文 §2.2 / §2.5。本专题发布当前实现的矩阵与验证边界，不承诺外部模型或 API 服务可用。

## 当前实现矩阵

Core API 发行版本取自 `vtx.__version__`（源码版本声明见 [VERSION](../../VERSION)），不在本表复制版本号。下表描述本仓 [兼容门实现](../../vtx/pluginlib/compatibility.py)，不外推未来版本。插件自己的 `version` 与协议标识独立。

显式 `requires.core` 必须先通过：低于下限或内核版本不可解析即拒绝，不再检查协议；不能用协议版本绕过内核门。core 门通过后的协议结果如下：

| requires.plugin_protocol | 允许的 hooks | 运行时结果 |
| --- | --- | --- |
| `"1.1"` | prepare_source、audio_events、validate | 支持；仍须通过声明/实现对账 |
| `"1.2"` | 上行 + probe_source、finish | 支持；仍须通过声明/实现对账 |
| `"1.0"` 或未知版本 | 无 | 拒绝；旧 prepare 草案签名不支持 |
| 空串、带空白/前缀、非字符串等 | 无 | 拒绝；不做版本猜测/范围协商 |
| 缺字段 | 现有已知 hooks | 未版本化路径，非矩阵认证 |

内置 denoise、duck-static、bgm-render、blender-bridge 均未声明协议字段，保留现有行为；前两者还未声明 core 下限。缺少单个约束字段只跳过对应门，不跳过另一个显式约束；整个 `requires` 对象缺失仍是 schema 错误。顶层未知 `protocol` 字段继续按额外字段处理，不参与兼容判断。

## 内核最低要求

`requires.core` 采用最低版本形式。三段为无前导零的非负 ASCII 整数，可在约束两端及 `>=` 后留空白；逐段数值比较，等于下限通过。空串、非字符串、缺段、预发布、构建后缀、通配符、其他比较符和组合范围均拒绝；实现不支持完整 SemVer 或 PEP 440。

检测版本沿用内核既有源码树/安装包版本读取链，不另读 Git tag 或插件目录旁的 VERSION。缺少约束不等于已证明兼容。

## 执行与失败

每次插件加载在包导入前重新读取实体 manifest，依次检查 core、协议及协议允许的 hooks，再进行导入后的声明/实现对账。协议检查同时覆盖实体 manifest 与有效 registry 声明，不能借 registry 扩大协议能力。所有执行入口（五类 hooks、源解析、缓存键贡献、目录键贡献）共用此门。已缓存模块也重新检查；external 件在修改导入路径之前完成检查。摘要/缓存目录等纯静态读取不执行插件代码，不承诺在这些读取时进行运行时门控。

显式未知或格式错误的 `requires.plugin_protocol` 保留在 discovery 的有效 manifest 中，在运行时拒绝，避免 external 件被静默遗漏。现有 core 语法错误仍沿用 discovery 的无效组件策略：doctor/registry 显示错误，外部件不进入有效发现清单；有效但过高的 core 下限在执行时拒绝。已禁用或 timeline 未引用的插件继续空转，无键声明插件沿用全局生效规则。

协议错误显示插件名、检测内核版本、声明协议、允许协议及升级入口；core 错误显示检测版本与要求下限。按错误修复 manifest，升级内核或安装支持当前内核/协议的插件；不应仅改版本标签来掩盖签名不兼容。入口见 [安装说明](../../README.md)。应用 CLI/health 命令、用户直接绕过插件总线的 Python 导入，不属于本运行时门的执行范围。

## 验证

临时插件 fixture 覆盖允许/拒绝矩阵、协议/核心错误优先级、坏类型、遗留字段、内置清单、registry 漂移、所有执行入口、external 真实发现及导入、缓存模块改 manifest 后重新拒绝。拒绝时检查导入路径不变、零导入/零钩子副作用。CPU 回归命令：

```bash
env python3 -m unittest tests.test_plugin_protocol_matrix tests.test_plugin_compatibility tests.test_pluginlib tests.test_plugin_code_digest tests.test_protocol_v12
```

这些检查保协议门和加载时序，不证明第三方实现语义正确、第三方代码可信或外部服务可用。
