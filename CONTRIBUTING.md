# 贡献指南

环境按 [快速开始](README.md#快速开始clone-即用零安装步骤)与[依赖矩阵](README.md#依赖矩阵)准备；Python 下限以 [`pyproject.toml`](pyproject.toml) 为准。结构与契约见 [ARCHITECTURE](docs/ARCHITECTURE.md)，开发专题见 [DEV](docs/DEV.md)。

## 测试入口

以下命令可写临时文件或启动工具；全仓运行器发现内核、app、plugin、template 测试，任一批失败即非零：

```bash
bash scripts/run_all_tests.sh
```

`VTX_TEST_CHROME` 指向 Chrome/Chromium，启用对应真实 gfx 检查；`VTX_TEST_FFMPEG` 指向 FFmpeg，部分测试显式门控，另有测试回退 PATH。因此未设置变量不等于纯单测，skip 不等于通过，也不能把这些变量当作启用所有可选集成。实际 opt-in 以测试为准，[CI 覆盖](docs/DEV.md#ci-回归覆盖)区分普通 PR、定时与手动范围。

可按领域检查；辅助模块不另设运行器，工具与平台门控仍有效：

```bash
python3 -m unittest discover -s tests/gfx -t . -v
python3 -m unittest discover -s tests/qc -t . -v
```

## 信号实验隔离

kill/killpg/信号类动态实验**禁止 in-process 直跑**，须在 `start_new_session=True` 隔离子进程内；变异演示按必然突破被测防护看待。停止新增本地 TOCTOU 加固，但不撤既有防御/在途 B08 回归；这不授予改动安全设置或部署宿主隔离的权限。历史责任与当时未实施项见[研究索引](docs/reports/development-history.md#安全与治理裁定)。

## 文档门禁

文档 / manifest 改动必须运行聚合门，任一 leaf FAIL 总非零；它不证明散文语义、真实渲染或严格成片验收：

```bash
bash scripts/doc_gates.sh
```

## 提交与评审

1. 从默认分支创建特性分支；外部贡献者先 Fork。
2. 遵守 [AGENTS 写态红线](AGENTS.md#写态红线)：CI 修改需事先明确批准，不手改生成区、不绕开历史写入边界；版本号仅随发布三源同步。manifest 的 catalog 重注见[文档宪章](docs/DOC-CHARTER.md#单一事实来源)。
3. `docs/skill/`、`templates/**` 是 live 消费面，修改前先公告并评审，详见[消费面规则](docs/DOC-CHARTER.md#消费面)。示范命令标明写操作，优先 `--dry` / 离线形态。
4. 提交 PR，说明动机、范围、实际命令及 PASS/FAIL/SKIP/UNRUN；渲染改动附最小 timeline 与前后对比。不得把未执行的检查报绿。
5. 维护者 review 后，在同一分支追加修订并重验受影响面。
