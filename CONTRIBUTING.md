# CONTRIBUTING.md —— 外部贡献指南

> 感谢关注 vtx（B 站视频产线程序）。本文面向准备提交 issue / PR 的外部贡献者；仓内开发纪律与演进史的完整叙述见 `docs/DEV.md`，文档层规则见 `docs/DOC-CHARTER.md`。

## 环境要求

- Python 3.9+（仓内开发纪律建议 3.10+，与 `pyproject.toml` 的 `requires-python` 一致；仓内一律用 `env python3`，避免系统 Python 劫持问题）。
- ffmpeg（音视频链必需）；渲染集成检查需 Chrome/Chromium（gfx 帧驱动协议）。
- clone 后先跑自检与初始化：

```bash
env python3 vt.py init      # 依赖自检 + 生成 config.local.json（幂等）
env python3 vt.py doctor    # 三检：版本漂移 / apps+plugins manifest+health / skills 完好性
```

## 测试入口

统一测试运行器（CI 语义，任一批次失败即非零退出）：

```bash
bash scripts/run_all_tests.sh
```

默认批次为纯单测；涉及真实渲染/混音的集成检查由环境变量门控，未设置时自动跳过：

- `VTX_TEST_CHROME` —— 指向 Chrome/Chromium 可执行文件，启用 gfx 真渲染集成检查。
- `VTX_TEST_FFMPEG` —— 指向 ffmpeg 可执行文件（未设时回退 PATH 上的 ffmpeg），启用混音响度链集成检查。

例：`env VTX_TEST_FFMPEG=$(which ffmpeg) VTX_TEST_CHROME=$(which chromium) bash scripts/run_all_tests.sh`。

## 文档门禁

涉及文档 / manifest 的改动必须过文档门聚合器（任一 leaf FAIL 总非零）：

```bash
bash scripts/doc_gates.sh
```

## 硬性纪律（PR 前自查）

1. **版本三源同步**：`VERSION` / `pyproject.toml` / `vtx/__init__.py` 三处版本号改一必三，`doc_gates` 的 version_sync leaf 守护。版本号变更只随发布提交，日常 PR 请勿改动。
2. **catalog 生成区禁手改**：`docs/catalog.md` 的生成区由脚本重注，任何 manifest（`app.json` / `plugin.json`）变更后必须执行：

   ```bash
   python3 scripts/gen_manifest_catalog.py
   ```

   再跑 `bash scripts/doc_gates.sh` 确认全绿。
3. **docs/plans\* 三目录为只读历史层**，请勿改动其中内容。
4. **`skill/` 与 `skills/**` 属挂载消费面**，其文档修订影响下游技能挂载，非明确指派请勿触碰。

## AGENTS.md 与 AI 协作智能体

仓根 `AGENTS.md` 是面向编码与执行智能体（AI 协作智能体）的常驻指令文件：约定常用命令入口、写态红线、编辑纪律与「home 指针制」（一事实一家，禁止复制）。若你以智能体身份参与本仓，请先读它并遵守；人类入口是 `README.md`。

## PR 流程

1. Fork 仓库，从默认分支切特性分支。
2. 按上文跑测试与文档门禁，确保全绿；命令尽量零写态书写（`--dry` / 离线旗标优先）。
3. 提交 PR，正文说明动机、改动面与已跑过的门禁命令；涉及渲染行为的改动请附 timeline 最小复现或前后对比。
4. 等待维护者 review；要求修改时在同一分支追加提交即可。
