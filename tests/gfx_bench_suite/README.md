# gfx_bench_suite · S1–S8 标准基准测试套件（issue #106 · 已冻结）

> **冻结件**：`suite_manifest.json` v1.0.0 是 S1–S8 场景定义的唯一权威（页面、
> 分辨率 1920x1080、30fps、总帧数、调度帧序列、特性标签）。自冻结 commit 起
> 场景定义不再变更；消费方以 commit sha 锁定。本 README 只是操作入口，
> 规格冲突时以 manifest 为准。

## 消费方（谁在用这套件）

| issue | 用途 | 关键场景 |
|---|---|---|
| #107 | CDP 路线铁基线（吞吐/内存） | S1–S7 全部 |
| #115 / #117 | CEF 路线A / Electron 路线B 生死线压测 | S3/S4b/S5/S6 |
| #118 | 内存/泄漏审计 | S7（5,000 帧单 Worker 腿） |
| #119 | 半帧撕裂审计 | S6（PHASE C 注入 + `?delay=async`） |
| #120 | 静态帧饥饿审计 | S6 PHASE B（静止 120 帧，未声明跨度） |
| #121 | 拍平/alpha 保真审计 | S4b（body opacity 三角包络） |

## 场景速览（详见 manifest）

| 场景 | 页 | 帧数 | 要点 |
|---|---|---|---|
| S1 基础吞吐 | examples 版 gfxA + 新构纯色页 | 90+100 | 纯 IPC + PNG 编码底噪 |
| S2 CJK/SVG | data_table_grouped / nav_card | 180×2 | CJK 字重/内联 SVG/Grid/box-shadow |
| S3 光栅合成极限 | 新构 s3_heavy_blur | 300 | blur(80px) 毛玻璃 + 多层径向渐变 + drop-shadow |
| S4a 低 Alpha 羽化 | overlay 卡族 ×2 + lower-third | 150×3 | 羽化阴影低 alpha 带 |
| S4b 拍平对抗 | 新构 s4_body_opacity_fade | 120 | body opacity 0→1→0 + mask-image（g6a_title 事故形态） |
| S5 GPU 动态 | 新构 s5_webgl_particles | 300 | Mulberry32 种子 2,000 半透明粒子 + WebGL 波纹，双契约（`?f=`/`__vtx_render`） |
| S6 时序对抗 | 新构 s6_timing_adversarial | 600 | A 正常 / B 静止 120F / C 每 10 帧注 55ms / D 恢复；random_jump + reverse_100 |
| S7 高压长稳 | 14 模板交替 | 轮 1920F | 并发 4/7 两腿 + 单 Worker 5,000F 不重启；RSS ≤16GiB |
| S8 生产基线 | 外部项目 voice-ime（bench_p2 已存档作废） | Micro 12024 / Macro 16746 | gfx 阶段 / 全链路双轨 |

## 运行套件自检（Chrome CDP 实渲 + DoD 幂等门）

```bash
# 仓根执行；全套（S1–S6 全量 2320 帧实渲 + 19 页幂等 + S6 序列 + S7/S8 抽样）约 10–40 分钟
env VTX_TEST_CHROME=/usr/bin/google-chrome \
  python3 tests/gfx_bench_suite/smoke.py

# 可选：--phase manifest,render,idempotency,s6seq,s7,s8 逐段跑
#        --keep-frames 保留全部帧产物（默认 hashing 后清理，留首/中/尾样本）
#        --skip-external S8 外部项目缺席时记 SKIP
#        --page-budget/--per-frame-budget/--budget-base/--stall-s 看门狗闸参数
# 证据默认落 /tmp/framivra-evidence/issue-106/{report.json,report.md,...}
```

### 看门狗与退出码（2026-10-15 加固，issue #106 续作）

每次 `render_page`/浏览器会话在独立子进程组内执行；父进程轮询帧产物做逐帧
进度输出（≥16 帧的任务每 15s 一行），两道墙钟闸收割「单场景拖死整套」：

- **stall 闸**（`--stall-s`，默认 300s）：无任何新帧落盘 → 判停滞，整组 SIGKILL；
- **budget 闸**（默认 `120s + 10s×工作帧数/tabs`，`--page-budget` 绝对覆盖）：
  慢但在动 → 判超预算，整组 SIGKILL。

击杀后记录 `{reason, budget_s, elapsed_s, frames_done, est_ms_per_frame}` 事实并
跳过继续；**超时不是 FAIL**，退出码区分：`0`=全 PASS；`1`=门 FAIL；`2`=环境缺置；
`3`=完成但存在受控超时（部分超时）。引擎 `vtx/gfx_render.py` 与冻结规格零改动。

快速完整性门（无 Chrome，秒级，`bash scripts/run_all_tests.sh` 自动包含）：

```bash
env python3 -m unittest tests.gfx.test_gfx_bench_suite
```

## 比对档位（Tier）

- **Tier-1**（本 issue DoD 门）：`max_abs_diff == 0`——RGBA 四通道逐像素最大
  绝对差为零（解码口径与 `scripts/gfx/gfx_golden.py` 同域）。用于同帧异序幂等
  （`[0,1,2,3]` vs `[99,2]` 渲 `f0002`）与 S6 非顺序调度对账。
- Tier-2+（容差档）由 #107 按路线实测噪声定义；跨引擎（SwiftShader vs 硬件 GL）
  的 S5 像素对比必须走容差档，禁止套 Tier-1。

## 引擎口径（跑套件时的环境）

- 引擎：`vtx/gfx_render.py render_page`（Chrome CDP 常驻浏览器，
  `--headless --disable-gpu`；S5 WebGL 走 SwiftShader）。
- 建议环境：`VTX_GFX_TABS=4`（生产定版）、`VTX_GFX_STATIC=0`（防御性：套件
  页面全部刻意不声明静态跨度/canvas 导出——声明会绕过被测代码路径）、
  `VTX_GFX_NAVFREE=0`（navfree 已封存）。
- S6 压测变体：`?delay=async`（55ms 宏任务延迟落定，未完成帧诱饵，**非确定态**，
  只供 #115/#117，禁用于 golden/幂等比对）；默认 sync 为黄金确定态。

## 已登记的引擎现状发现（冻结期实测，2026-10-15）

### S3 停摆终裁（2026-10-14 23:54 事故 · 2026-10-15 复盘）

**结论：争用依赖的引擎级重试风暴（假挂死），非 S3 页面病态慢、非死锁。**

- **物证**：当晚 S1（39.1ms/帧）/S2（57.3、39.3ms/帧）正常跑完，23:54:32
  S3 进入后零输出，产物目录 **0 帧落地**（`issue-106/smoke/S3__heavy_blur/`
  空目录；S1/S2 目录各留样本帧）；
- **基线**（受控探针，`/tmp/framivra-evidence/issue-106b/`）：干净环境单截
  0.29–0.55s、`render_page` tabs=4 单帧 1.19s、**全量 300 帧 98.97s
  （317ms/帧，304 截 p50=0.95s/p99=1.45s/max=1.49s，0 异常）**；28 核合成
  饱和争用下 6.9s/帧仍 300/300 完成——S3 本身不慢；
- **机制推证**（`vtx/gfx_render.py`）：重度争用（#102 全量测试等并发 Chrome
  舰队）把 SwiftShader 重 blur 单帧光栅推过引擎 WS socket **20s 硬超时** →
  `socket.timeout` → `heal()`=整浏览器冷重启 → 每帧重试≤2 败→帧弃**不落盘**
  → 下一帧重复，≈75 帧/tab × ~100s/帧 ≈ **2h+ 零帧**；旧 harness 逐页完成
  才打一行 → 长静默即「停摆」观感。「单帧>20s」未在受控探针直接复现（28 核
  合成争用只到 ~7s/帧），该环节属引擎结构推证+零帧物证收敛的裁决；
- **处置**：不动引擎（#107 铁基线消费方自行决定放宽口径）；冒烟 harness
  加看门狗（见上节）把此形态收割为有界受控超时（exit 3）并继续套件。

### S4b 拍平偏差（既有登记）

S4b 在 fractional body-opacity 帧上，CDP 引擎 override 快路径与白黑双截金标准
`max_abs_diff=128`（预乘/拍平族偏差；f=0/f=119 全透明帧两者全等）。现行双探针
flatten 守卫（探针=work 首帧+中帧、判据=alpha 全 255）对「首帧全透明」的 S4b
不触发回落。该盲区属引擎现状，交 #121 审计处置；套件只负责把场景钉死。
（复现：smoke 的 render 阶段 S4b 帧走 override 路径，path_counts 可见。）

## 目录

```
tests/gfx_bench_suite/
├── suite_manifest.json   # 冻结件（唯一权威）
├── pages/                # 新构页 ×5（S1/S3/S4b/S5/S6）
├── smoke.py              # 套件自检（Chrome CDP 实渲 + DoD 幂等门）
└── README.md             # 本文件
tests/gfx/test_gfx_bench_suite.py  # 快速完整性门（无 Chrome，unittest 自动发现）
```
