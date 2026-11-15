# vtx：时间线与渲染内核

本目录把时间线与素材转换为帧精确视频，负责校验、图形渲染、音视频合成、响度终链、QC 与缓存。它也提供时间线查询/编辑 API；独立素材工具在 [apps](<../apps/README.md>)，管线扩展在 [plugins](<../plugins/README.md>)。

## 从源码使用

在**仓库根目录**执行，不要进入本目录后直接导入同名模块。安装和工具要求见[依赖矩阵](<../README.md#依赖矩阵>)；以下仅查看帮助与接口能力，不需要媒体，也不创建项目产物：

```bash
python3 -m vtx --help
python3 -m vtx capabilities
```

源码启动器 `python3 vtx/vtx.py`、模块入口 `python3 -m vtx` 与安装后的 `vtx` 使用同一 CLI。仓库安装、组件检查与链接管理使用的是根目录的 [vt.py](<../vt.py>)，不是渲染命令。

- **开始创作**：按[快速开始](<../README.md#快速开始clone-即用零安装步骤>)准备外置项目与素材，再按[编辑/交付流程](<../docs/skill/references/edit.md>)校验和执行完整 `all`。渲染会写项目缓存与产物；本源码目录不是项目输出目录。
- **只查时间线**：使用[时间线 API](<../docs/reference/timeline-api.md>)中的 inspect/query；查询成功不代表素材存在或项目可渲染。
- **开发集成**：从 [api.py](<api.py>) 与[公共面说明](<../docs/reference/timeline-api.md#公共面与零写态>)开始；组件不要绕过公共 API 导入内部实现。

## 定位实现

| 目录 / 文件 | 主要职责 |
|---|---|
| [cli](<cli/>) | 参数解析与命令调度 |
| [pipeline](<pipeline/>) | 项目上下文、约束、阶段执行与合成 |
| [render](<render/>) | 图形后端与帧源 |
| [media](<media/>) | 编码、音频与字幕 |
| [cache](<cache/>) | 缓存身份、回执、恢复与回收 |
| [runtime](<runtime/>) | 文件事务、进程、诊断、事件与资源原语 |
| [inspection](<inspection/>) | QC、diff、影响分析与验收 |
| [pluginlib](<pluginlib/>) | 组件发现、配置、加载与钩子 |
| [timeline.schema.json](<timeline.schema.json>)、[timeline.py](<timeline.py>) 及同前缀模块 | 时间线结构、查询、引用、编辑和事务实现 |

详细数据流和模块边界以[架构](<../docs/ARCHITECTURE.md#1-内核数据流与模块>)为准；缓存行为以[缓存与回执](<../docs/cache-provenance.md>)为准。回归测试在 [tests](<../tests/README.md>)，运行前阅读[贡献指南](<../CONTRIBUTING.md#测试入口>)的依赖与写态说明。
