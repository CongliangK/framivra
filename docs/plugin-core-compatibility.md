# 插件显式内核版本门

本专题记录 Issue #49 的窄实现，契约入口为 [ARCHITECTURE §2.3](ARCHITECTURE.md#23-manifest-schema-v1)。不代表 Core API × plugin protocol 完整兼容矩阵已完成。

## 判定边界

- `requires.core` 显式声明采用现有 manifest 的最低版本形式：`>=MAJOR.MINOR.PATCH`。三段为无前导零的非负 ASCII 整数，可在约束两端及 `>=` 后留空白；逐段数值比较，等于下限通过。
- 空串、非字符串、缺段、预发布、构建后缀、通配符、其他比较符和组合范围均拒绝；实现不声称支持完整 SemVer 或 PEP 440。
- 缺少 `requires.core` 保留遗留策略，不做版本门控。内置 denoise / duck-static 属此分支；缺字段不等于已证明兼容。
- 检测版本来自 `vtx.__version__`，沿用内核既有源码树/安装包版本读取链，不另读 Git tag 或插件目录旁的 VERSION。显式约束遇到无法解析的内核版本时拒绝加载。

## 执行与失败

manifest 读取先验证约束语法；插件总线在包导入前重新读取实体 manifest 并比较内核版本，随后才进行既有 hooks 声明与实现对账。已缓存模块也不能绕过此门；外部插件在修改导入路径之前完成版本检查。无有效 manifest 导入锚不允许退回任意包导入。

兼容错误显示插件名、检测内核版本、要求下限及升级入口；修复方式是升级内核至所需版本，或选择支持当前内核的插件。格式错误须修复 manifest 或安装有效插件。入口见 [安装说明](../README.md)。

已禁用或 timeline 未引用的有效插件继续空转。无键声明插件沿用全局生效规则。语法错误 manifest 沿用 discovery 的无效组件策略：doctor/registry 显示错误，外部件不进入有效发现清单；不会导入或运行钩子。本版本门不扩展应用 CLI/health 命令执行策略。

## 协议边界

已有 hook 名值域与声明/实现一致性检查继续生效，未知 hook 名仍拒绝。manifest 没有定义独立 `protocol` 版本字段；额外未知字段沿用现有 schema 策略，不能把任意 `protocol` 值当成经过兼容协商。Core API 与各协议演进版本的正式矩阵、应用执行门控、外部模型或 API 服务可用性不在本补丁承诺内。

## 验证

专用测试覆盖最低版本边界、数值排序、非法约束、非法内核版本、遗漏约束、未知 hook、禁用/未引用、外部导入及缓存模块，拒绝时断言零导入/零钩子调用。与既有插件总线及代码摘要回归一并运行：

```bash
env python3 -m unittest tests.test_plugin_compatibility tests.test_pluginlib tests.test_plugin_code_digest tests.test_protocol_v12
```
