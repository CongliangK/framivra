# docs：文档导航

这里收录 Framivra 的操作指南、接口契约、开发说明与研究证据。首次使用先看[项目 README](<../README.md>)；本页只帮助选择入口，不另建一套用法或契约。

## 按任务开始

| 你要做什么 | 从哪里读 |
|---|---|
| 安装、更新或排查环境 | [安装指南](<guides/installation.md>)；已安装后的问题看[代理诊断](<guides/agent-diagnostics.md>) |
| 制作、修改或交付视频 | [操作技能](<skill/SKILL.md>)；完整工作流与工程范本看[模板集合](<../templates/README.md>) |
| 找素材工具或管线插件 | [组件目录](<catalog.md>)，再进入具体组件 README |
| 开发、测试或接入宿主 | [开发者入口](<DEV.md>)；系统边界与扩展契约看[架构](<ARCHITECTURE.md>) |
| 理解缓存或定位重复渲染 | [缓存与回执](<cache-provenance.md>) |
| 验收项目、维护发行或核对许可 | [项目验收](<guides/project-acceptance.md>)、[发布](<releases.md>)、[第三方许可](<third-party-licenses.md>) |
| 查当前限制或历史结论 | [已知问题](<reference/known-issues.md>)、[研究索引](<reports/README.md>)；历史通过不代表当前环境已验收 |

## 子目录分工

- [guides](<guides/>)：面向任务的操作指南，先检查各页的环境与输入前提。
- [reference](<reference/>)：接口、结果语义与支持边界；具体专题由 [DEV](<DEV.md#专题入口>) 路由。
- [skill](<skill/SKILL.md>)：视频执行者使用的技能正文、机制说明和资产，与普通开发文档用途不同。
- [reports](<reports/README.md>)：保留的研究结论与证据，不是日常操作清单。

## 修改文档

先按[文档规则](<DOC-CHARTER.md>)找事实的唯一维护位置：本页维护导航，具体命令、契约与依赖留在对应主题。组件目录的生成区不能手改，历史冻结证据不能重录；技能与模板的修改另有消费面评审要求。

在仓库根目录运行文档检查（可能生成 Python 缓存；只冒烟脚本帮助或检查语法，不执行完整教程）：

```bash
bash scripts/doc_gates.sh
```

还需人工核对链接、命令前置条件与实际语义；报告通过、跳过及未运行范围，不能用门禁通过代替业务验收。
