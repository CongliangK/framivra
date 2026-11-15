# .github：GitHub 自动化入口

本目录保存 GitHub Actions 工作流，不是本地运行配置或视频项目目录。理解结果先读 [CI 覆盖与限制](<../docs/DEV.md#ci-回归覆盖>)；本地执行入口见[贡献指南](<../CONTRIBUTING.md#测试入口>)。

## 找到对应工作流

| 工作流 | 用途 |
|---|---|
| [CI](<workflows/ci.yml>) | 普通 PR / main push 的标准库冒烟、静态语法和文档门 |
| [Integration regression](<workflows/integration.yml>) | 定时或手动的全量 Python、严格媒体及 Windows 安装回归 |
| [HTML 依赖验收](<workflows/gfx-dependency-acceptance.yml>) | 限定 PR 路径和分支的真实 HTML 依赖闭包验收 |
| [HTML 影响验收](<workflows/impact-html-acceptance.yml>) | 限定 PR 或手动触发的 HTML/媒体影响面验收 |
| [发行证明](<workflows/release-attest.yml>) | 手动生成与核验 attestation；模式与授权要求见工作流及[发布说明](<../docs/releases.md>) |

在 GitHub 的 Actions 页面选择工作流与运行记录查看日志、失败步骤和证据附件；具体触发条件、权限与参数以各 YAML 为准。普通 PR 的绿灯不代表媒体全链、全部平台或发布资格已通过，未触发的专用工作流也不是验收通过。

## 维护边界

工作流、门禁脚本、选测策略和通过判据都属于 CI。调整前必须按 [AGENTS 写态红线](<../AGENTS.md#写态红线>)说明必要性、影响及验证/回滚方案并取得明确批准；不能为消除失败删测或放宽门。发行签名不等于发布，也不能将手动触发入口当作已获得正式发版授权。
