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
# 仓根执行；全套（S1–S6 全量 2320 帧实渲 + 19 页幂等 + S6 序列 + S7/S8 抽样）约 10 分钟
env VTX_TEST_CHROME=/usr/bin/google-chrome \
  python3 tests/gfx_bench_suite/smoke.py

# 可选：--phase manifest,render,idempotency,s6seq,s7,s8 逐段跑
#        --keep-frames 保留全部帧产物（默认 hashing 后清理，留首/中/尾样本）
#        --skip-external S8 外部项目缺席时记 SKIP
# 证据默认落 /tmp/framivra-evidence/issue-106/{report.json,report.md,...}
```

快速完整性门（无 Chrome，秒级，`bash scripts/run_all_tests.sh` 自动包含）：

```bash
env python3 -m unittest tests.test_gfx_bench_suite
```

## 比对档位（Tier）

- **Tier-1**（本 issue DoD 门）：`max_abs_diff == 0`——RGBA 四通道逐像素最大
  绝对差为零（解码口径与 `scripts/gfx_golden.py` 同域）。用于同帧异序幂等
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
tests/test_gfx_bench_suite.py  # 快速完整性门（无 Chrome，unittest 自动发现）
```
