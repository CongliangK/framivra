# CONTRIBUTING.md —— 外部贡献指南

> 感谢关注 vtx（B 站视频产线程序）。本文面向准备提交 issue / PR 的外部贡献者；仓内开发纪律与演进史的完整叙述见 `docs/DEV.md`，文档层规则见 `docs/DOC-CHARTER.md`。

## 环境准备

按 [快速开始](README.md#快速开始clone-即用零安装步骤) 初始化与离线自检，先读其中的写操作提示与 [依赖矩阵](README.md#依赖矩阵)。Python 支持下限以 [`pyproject.toml`](pyproject.toml) 的 `requires-python` 为准；测试运行器使用 `env python3`。

## 测试入口

统一测试运行器（默认全量发现，任一批次失败即非零退出）：

```bash
bash scripts/run_all_tests.sh
```

该入口发现内核、app、plugin 和 template 的测试；执行可写临时文件或启动工具。部分媒体检查需显式启用，另有检查在 PATH 存在 FFmpeg 时自动运行，不能把未设置环境变量的运行称为纯单测。skip 表示未执行：

- `VTX_TEST_CHROME` —— 指向 Chrome/Chromium 可执行文件，启用以它为门控的 gfx 真渲染检查。
- `VTX_TEST_FFMPEG` —— 指向 ffmpeg 可执行文件；部分测试要求显式设置，另有测试在未设置时回退 PATH。

例：`env VTX_TEST_FFMPEG=$(which ffmpeg) VTX_TEST_CHROME=$(which chromium) bash scripts/run_all_tests.sh`。这组变量不启用所有可选集成；额外 opt-in 与依赖以各测试为准。普通 PR、定时与手动 CI 的选择范围见 [CI 回归覆盖](docs/DEV.md#ci-回归覆盖)。

GFX 与 QC 分别按领域发现，目录内辅助模块不另设运行器：

```bash
python3 -m unittest discover -s tests/gfx -t . -v
python3 -m unittest discover -s tests/qc -t . -v
```

这两个入口保留各用例原有工具与平台门控，skip 不替代真实浏览器、原生后端或严格成片验收；全仓入口仍是上面的 `run_all_tests.sh`。

## 文档门禁

涉及文档 / manifest 的改动必须过文档门聚合器（任一 leaf FAIL 总非零）：

```bash
bash scripts/doc_gates.sh
```

## 提交前纪律

常驻规则统一见 [AGENTS 写态红线与编辑纪律](AGENTS.md#写态红线)：检查 CI 修改授权、版本同步、生成区与历史层写入边界、manifest 的 [catalog 重注联动](docs/DOC-CHARTER.md#7-保鲜机制条款v11-增补)；版本号只随发布提交，日常 PR 不改动。涉及生成区的写命令由该 home 指向，不要手改 catalog。

`docs/skill/` 与 `templates/**` 文档的消费面公告和写入权须遵循 [文档宪章](docs/DOC-CHARTER.md#1-skill-层操作路由层)；非明确指派不改挂载消费面。编码智能体先读 [AGENTS](AGENTS.md)，人类使用入口仍为 [README](README.md)。

## PR 流程

1. Fork 仓库，从默认分支切特性分支。
2. 按上文跑测试与文档门禁，确保全绿；命令尽量零写态书写（`--dry` / 离线旗标优先）。
3. 提交 PR，正文说明动机、改动面与已跑过的门禁命令；涉及渲染行为的改动请附 timeline 最小复现或前后对比。
4. 等待维护者 review；要求修改时在同一分支追加提交即可。
