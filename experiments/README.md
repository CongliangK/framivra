# experiments · 可选后端与有界研究

这里保留后端实现、复现工具和离线实验，不是默认生产或新项目入口。当前图形路径与资格见[架构](<../docs/ARCHITECTURE.md#1-内核数据流与模块>)，研究结论及证据归[研究索引](<../docs/reports/development-history.md>)；目录存在、探针通过或离线模型测试通过均不代表生产准入或性能提升。

## 按问题选择入口

| 入口 | 用途与前置边界 |
| --- | --- |
| [gfx_cef_worker](<gfx_cef_worker/README.md>) | CEF 原生 worker 构建及实验性 Alpha 接线；按平台准备锁定分发包、编译工具与运行环境，只处理受信任本地 HTML。 |
| [gfx_electron_worker](<gfx_electron_worker/README.md>) | Electron OSR 实现与复现；路线已封存、不采用，状态依据见[唯一记录](<../docs/reports/development-history.md#electron-route-status>)，不是推荐优化方案。 |
| [perf_html_workflow](<perf_html_workflow/README.md>) | opt-in HTML/CDP 全链观测；需指定 Python、浏览器、FFmpeg、字体与 Python 包，按子文档固定环境和独立输出目录。 |
| [perf_cdp_cost](<perf_cdp_cost/README.md>) | 用 Python 标准库读取显式指定的既有证据并记账；不启动浏览器、不重新测量，不认证来源或授予验收。 |
| [perf_cdp_navigation](<perf_cdp_navigation/README.md>) | 标准库离线导航事件模型；没有浏览器连接、渲染器接线或生产开关，测试仅证明模型内状态转换。 |

## 复现纪律

- 先读目标 README 的完整依赖、输入、输出和已知限制；Python 模块命令从仓根执行，原生/Node 构建遵循各自目录说明，不统一安装全部实验依赖。
- **构建/安装可能联网下载并写本地产物；真实后端和全链观测会启动进程、写媒体、日志及缓存。** 只在获得授权的新项目副本/新证据目录运行，确认磁盘容量与输出路径，不覆盖原项目或冻结证据。离线记账默认 stdout，只有显式输出选项才创建报告；勿用 shell 重定向截断输入。
- 依赖、配置、工具或输入改变后是新观测。冷暖、质量与环境身份要求按各子 README 执行；单帧、短夹具、离线模型和分项工作时间不能冒充全片墙钟或质量验收。
- 唯一主性能基线仍由 [30 秒 SVG Demo](<../templates/demo-timeline-svg/README.md#主性能基线>)定义；实验不替代它。未执行的真实后端、像素核验或整片验证标记 UNRUN。

维护及评审遵循[文档规则](<../docs/DOC-CHARTER.md>)与[贡献指南](<../CONTRIBUTING.md>)；研究结果更新已有权威入口，不复制冻结协议或制造平行状态记录。
