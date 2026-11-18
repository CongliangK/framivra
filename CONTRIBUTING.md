# 贡献指南

环境按 [快速开始](README.md#快速开始clone-即用零安装步骤)与[依赖矩阵](README.md#依赖矩阵)准备；Python 下限以 [`pyproject.toml`](pyproject.toml) 为准。结构与契约见 [ARCHITECTURE](docs/ARCHITECTURE.md)，开发专题见 [DEV](docs/DEV.md)。

## 测试入口

### 本机 Linux：先隔离再运行

在 systemd 用户会话、cgroup v2 环境中，从仓库根使用本机保护入口（可启动工具并写临时测试数据）：

```bash
python3 scripts/run_local_tests.py -- bash scripts/run_all_tests.sh
python3 scripts/run_local_tests.py -- node --test dsh-sidebar/tests/timeline-dom.test.mjs
```

侧栏全套在 `dsh-sidebar/` 使用 `npm run test:local`。保护入口默认限制本 UID 的单个测试进程树为 2 GiB、禁用该组 swap，启动前保留 2 GiB 主机可用内存余量；可用 `--memory-mib` 显式设定合适预算。仅一组受保护测试可并行运行；属性校验失败、余量不足或上次同一 systemd 用户管理器的组 OOM 未确认时不启动，不回落裸跑。组 OOM 返回 137；先修失败原因，再由人工单独运行 `python3 scripts/run_local_tests.py --ack-oom` 确认（不运行测试）。此保护不包含另行启动的外部服务，也不能限制其他应用耗尽系统内存。

### 全仓测试与跨平台直接入口

无 systemd 用户会话的平台仍使用原运行器（不提供上述硬隔离）。全仓运行器发现内核、app、plugin、template 测试，任一批失败即非零：

```bash
bash scripts/run_all_tests.sh
```

统一入口保留全部 unittest 发现，并以独立子进程运行 `apps/bgm/bgm_gen/tests` 的 pytest 默认档；开发环境需安装 pytest（`>=7,<9`）及音乐基础 NumPy + FFmpeg。pytest 输出收集/通过/失败/skip/deselected 计数与 skip 原因，缺 pytest、收集错误或空集合均非零；可选环境 skip/deselected 不代表已执行或验收通过。音乐分档与显式扩展测试命令见 [bgm](apps/bgm/README.md)。带参数的 `ci_partition.py` 测量入口仍仅覆盖 unittest，不是包含音乐 pytest 的全量证明。

macOS 默认 `/var`、`/tmp` 是符号链接，遇到仓库的物理路径检查时可用已存在的物理 `TMPDIR=/private/var/tmp` 运行同一全套（`export TMPDIR=/private/var/tmp`）；这只是改用真实物理路径，不忽略、不放宽任何 symlink 检查。

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
