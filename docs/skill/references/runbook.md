# runbook · 标准修改配方集（R1-R4）与棒次协议

> **定位：接棒子智能体「只读这一份就开工」的配方文件。** 路由索引在 `../SKILL.md`，字段语义与坑表在 `edit.md`/`gfx.md`；本文只回答「接到某类任务后，按什么顺序敲哪些命令」。命令必须与 `edit.md`/各脚本 `--help` 及仓库现状一致，本文每条配方都经过 voice-ime（v3.x–v4.x 全迭代周期）实测。
>
> **R5/R6/R7 归 `templates/tech-talk/references/runbook-r5r6r7.md`**——发布链（4K 投稿+冻结）/ BGM 分轨审计 / 封面制作属产线配方（各依赖具体 app 组合），v2 文档双层拆分时迁出内核。
>
> `<项目>` = 项目目录占位（下文用真实项目 voice-ime 作示例）；`<VTX>` = `~/.agents/skills/video/vtx/vtx.py`（符号链接，权威本体 video_tools 仓 `vtx/`）；`<video_tools>` = video_tools 仓根。

## 使用规则（worker 必读）

1. **只读本文对应配方 + 被指派的棒次 prompt，不做全仓探索。** 深层语义拿不准时才查 `edit.md`（坑表）/`gfx.md`/脚本 `--help`，不要通读仓库。
2. **命令按配方原样执行，禁止凭记忆改参数**（改参数=改配方，须主控拍板）。
3. **预期 >60s 的长命令一律后台运行 + 轮询**（同步等待会被看门狗杀）。
4. 修改 timeline.json 一律走**幂等脚本 + `vtx snapshot`**（先快照后改动），不手写 .bak。
5. 每棒结束落 REPORT 到 `<video_tools>/docs/reports/`（契约见文末；原 `cache/opt20250911/` 已弃用——cache/ 不入 git 且会重组，重要文档勿放，voice-ime 发布物料灭失实案 2026-10-03）。
6. **回滚锚点惯例**：重大轨源迁移（BGM stem 迁移、资产源换路径等）前，先落 `timeline.pre-<改动>.json` 长期锚点（与 `vtx snapshot` 自动轮转快照并行不悖；冻结链回滚以 `pre-*` 为输入）——voice-ime v4.7 stem 迁移即用 `timeline.pre-stem-migration.json` 兜底。

环境变量占位（复制配方时先赋值一次）：

```bash
export VTX=~/.agents/skills/video/vtx/vtx.py
export VIDEO_TOOLS=~/文档/01_活跃项目/video_tools
export PROJ=~/文档/04_bilibili视频/voice-ime   # 示例项目，按实际棒次替换
```

---

## R1 · 纯音频变更（音量 / duck / fade 改 timeline 音轨后）

**适用场景**：只改 `audio_tracks`（volume / pan / fade / 事件窗 / 换 BGM 源），不动任何视频轨、gfx、字幕。

**前置条件**：timeline 改动已定稿；项目内 `out/` 有上一版产物（会被覆盖，VERSIONS.json 记账轮转）。

```bash
cd ~/文档/04_bilibili视频/voice-ime

# 0) 快照 + 幂等脚本改 timeline（示例：BGM -5dB，防重施标记见坑位①）
python3 $VTX -t timeline.json snapshot

# 1) 机制硬门
python3 $VTX -t timeline.json validate

# 2) 重混音频（音视频画面不动，video 段缓存全命中；v2 §1.2 起两遍增益+alimiter
#    限幅已在 audio 尾部一体完成——out/audio.wav 即「归一+限幅」终态，无独立步骤）
python3 $VTX -t timeline.json audio

# 3) 封装（正式 libfdk_aac 192k）
python3 $VTX -t timeline.json mux

# 4) QC（响度/切点/黑帧）+ 内容验证门
python3 $VTX -t timeline.json qc
python3 $VIDEO_TOOLS/apps/gates/content_gates.py -t timeline.json --media out/final.mp4

# 5) 4K 投稿版重导出 + 断言（配方见 tech-talk runbook-r5r6r7.md R5）：
env python3 $VIDEO_TOOLS/apps/publish/export_4k.py -t timeline.json
bash $VIDEO_TOOLS/apps/publish/check_4k.sh out/final_4k.mp4

# 6) 冻结：更新 out/FREEZE.json（md5 清单）+ CHANGELOG.md 变更单 + timeline.<ver>-frozen.json
```

**耗时基准**（voice-ime 653s@1080p30，缓存命中）：validate ~3s、audio 数十秒（含终链限幅，v1 时代独立 post_loudness ~2.6s 已并入）、mux ~4.4s、qc ~8.6s、4K ~66s；**全链 ≈2 分钟**。〔方案三前（2026-09-12 口径）：post ~10s、mux ~20s、qc ~11s、4K ~2.5min、全链 ≈4-5 分钟〕

**0) 先跑 `vtx diff` 定位影响面与推荐路径**（时间线语义 diff，2026-09-12 新增）：

```bash
python3 $VTX diff timeline.pre-<改动>.json timeline.json --json out/tl_diff.json
```

输出逐字段差异 + 影响面（seg/gfx 缓存键逐 clip 重算比对=真值、vseg 失效段、需重跑阶段、耗时预估）+ 推荐修改路径。R1 场景（只动音轨）预期报告：seg 失效 0、vseg 失效 0 段、需重跑 validate → audio → mux → qc、推荐路径 `patch_script`。若 diff 显示 seg/vseg 失效 >0，说明改动越界进了视频侧——按 R2 重跑，别只走本配方。原则口径：**改动走哪条路以 diff 输出的 advice.recommended_path 为准**（generator=改 make_subs.py 再生；patch_script=幂等补丁；manual_whitelist=仅 _meta 可手改）。

**快速确认命令**（全量 R1 链前先局部验证，音频改动场景；上面「0) 先跑 diff」是决策步，此处是决策后的确认步）：

```bash
python3 $VTX -t timeline.json video --draft --range 0 6   # 热改动区间窗口（时间点支持 秒/时间码/f帧号；gfx 冷缓存提示先跑 vtx gfx）；产物 cache/preview/range_*.mp4，耗时双口径：纯回看窗 ~5s，改动窗（窗内 rseg 失效重编码+窗口混音）显著更长（DEV.md「迭代回路基线」）
python3 $VTX -t timeline.json prepare                     # audio --report 前置：输入变化后依赖门要求先过 prepare（温态命中缓存 ~1.4s）
python3 $VTX -t timeline.json audio --report              # 混音后判级：LUFS/TP/人声-床乐差/duck 咬合/空洞，FAIL 退出码 1；报告 out/audio_report.json
```

**必跑验证门**：validate（机制）→ qc（-14±1.5 LUFS/切点/帧数）→ content_gates（G1/G2/**G3 解码 TP**/G7/G10）→ check_4k（4K 版 14 项断言）。

**已知坑位**：
- **响度终链语义（v2 §1.2）**：alimiter 限幅已并入 `vtx audio` 尾部，audio→mux 直连即可；v1 时代「audio → post_loudness → mux 三步链」的中步已消失（`scripts/post_loudness.sh` 留兼容薄壳，正常链路勿跑——对已终态 audio.wav 幂等直过，无收益）。**AAC 编码后真峰过冲仍在：G3 解码域 TP 终检不可省**，wav 域 TP 不算数。
- **响度语义**：归一后全片综合响度 I 恒为 ≈-14 LUFS；改某轨 volume 改变的是**轨间相对比例**，不是成片 LUFS。预期「成片更响」的验收口径要写对。
- 幂等脚本须带**防重施标记**（如改前检测目标值已是新值则跳过），防同脚本跑两遍把 -5dB 叠成 -10dB。

---

## R2 · 时间线编排小改（clip 窗口 / 轨道增删 / 字幕）

**适用场景**：改 `video_tracks` 的 clip 窗口、增删轨/事件、字幕条修订。

**前置条件**：同 R1；改动用幂等脚本实施。

```bash
cd ~/文档/04_bilibili视频/voice-ime
python3 $VTX -t timeline.json snapshot
# …幂等脚本改 timeline.json…
python3 $VTX diff cache/snapshots/<最近快照>/timeline.json timeline.json --json out/tl_diff.json
```

**先看 diff 再决定动不动画布**（2026-09-12 新增，统一「改生成器 vs 补丁」口径）：上面第 3 步的语义 diff 就是决策依据——

- `seg 失效 / vseg 失效段 >0` 或 entries 落在 `video_tracks/subtitles/project` → 几何/时长/字幕类改动，**只能走生成器**（本配方 R2 全链），推荐路径应显示 `generator`；
- `seg/gfx/vseg 全 0` 且 entries 落在 `audio_tracks` → 纯音频参数改动，降级走 R1（audio+mux+qc，跳过 video）；
- `manual_whitelist` 之外的任何「手改」都不合规（timeline 由生成器再生，禁止手改）；白名单仅 `_meta` 注释类字段。

硬门槛自验：只改 1 个 clip 时长的 diff 必须报告 **seg 失效恰好 1 个 clip**（voice-ime 实测：ar_二@2059 dur 91→121，seg 重编码恰 1 任务、vseg 失效 2/8 段，实跑逐段核对一致；预估 42s vs 实测 46s，误差 +8.7%）。

```bash
python3 $VTX -t timeline.json validate

# 缓存决策（gfx=内容寻址自动换键，通常无需手工清）：
#   · 改的是 clip 时间窗但源/参数没变 → seg 键不变，直接 video（命中）
#   · 改了源切片窗口/编码参数 → 旧键自然失效自动重渲；若怀疑误命中，rm cache/seg 对应段后重跑
#   · 改了 gfx html → gfx 键自动变（见 R3）

python3 $VTX -t timeline.json video --gpu    # 正式渲染（NVENC）；迭代期用 --draft --frames N
python3 $VTX -t timeline.json audio          # 含响度终链（增益+限幅一体）
python3 $VTX -t timeline.json mux
python3 $VTX -t timeline.json qc
python3 $VIDEO_TOOLS/apps/gates/content_gates.py -t timeline.json --media out/final.mp4
# 收尾习惯：先 gc --dry 看一眼，再 gc
python3 $VTX -t timeline.json gc --dry
```

**快速确认命令**（编排改动已由上方 diff 定界为几何/字幕类后、全量 video --gpu 前，先区间看效果）：

```bash
python3 $VTX -t timeline.json video --draft --range <改动clip入点> <出点>   # 只渲改动邻域；产物 cache/preview/range_*.mp4，耗时双口径：纯回看窗 ~5s（复验 5.18-5.20s），改动窗（窗内 rseg 内容键失效需重编码，含窗口混音）显著更长（voice-ime 字幕改动窗空载实测 11.0s；DEV.md「迭代回路基线」）
python3 $VTX -t timeline.json video --draft --range <A> <B> --track <轨名>   # 或按轨取窗；--clip <ID> 按 clip 取窗
python3 $VTX -t timeline.json subs check                                     # 动了字幕再复查全绿（<1s）
```

**耗时基准**：缓存全命中全链 ~50s；video 段缓存命中、其余冷 ≈3min；video 冷（并行 8 段）~2min。

**必跑验证门**：validate → qc → content_gates。黑帧嫌疑（动过窗口边界）加 `blackdetect` 抽验（qc 内置零黑帧检查）。

**已知坑位**：framesync/CFR 补帧坑全部 vtx 内置（见 `edit.md` 坑表）；红队遇「相邻帧逐字节相同」属 CFR 复制补偿帧，是预期，但定稿前须帧数+黑帧+断点两侧抽验三重确认。**不要再手工 rm cache/seg（edit.md 明令），内容寻址已从机制上消除误命中**；只有排查疑点时允许定向清理后重渲。

---

## R2s · 纯字幕修订（只改 timeline.json 的 subtitles）

**适用场景**：只改字幕文本/开窗，不动任何 clip/音轨/源。背景：v39 事故（字幕超前口播最大 4.3s，渲染完整片才发现）——本配方的定位就是让这类漂移在渲染前被抓到。

**前置条件**：同 R2；字幕改动用幂等脚本实施（`vtx snapshot` 先留快照）。

```bash
cd ~/文档/04_bilibili视频/<项目>
python3 $VTX -t timeline.json subs check      # ① 秒级：对齐漂移/CPS/重叠/行宽，FAIL 先改完再往下
# …幂等脚本改 timeline.json subtitles…
python3 $VTX -t timeline.json subs check      # ② 复查全绿（报告 out/subs_check.json）
python3 $VTX -t timeline.json subs preview 12.5 f360   # ③ 抽查可疑条目（秒或 f帧号，单张 ≤5s，支持多点）
python3 $VTX -t timeline.json video --gpu     # ④ 段级缓存：改一条字幕只重渲其覆盖的 1-2 段（~10-25s）
python3 $VTX -t timeline.json audio           # 字幕改动不动音频键，命中即过（响度终链在内）
python3 $VTX -t timeline.json mux
python3 $VTX -t timeline.json qc
python3 $VTX -t timeline.json diff <上一版报告>.json out/qc_report.json   # ⑤ 与上一版对比（可选；支持 qc_report/audit_report/subs_check，字幕项逐条对比）
```

**耗时基准**（2026-09-12 合成 30s/900 帧项目实测）：subs check <1s、subs preview 单张 ~0.6s、改 1 条字幕重渲 2 段 ~2s。

**快速确认命令**（③ 步 preview 抽查后、④ 步全量段重渲前，用区间渲染带字幕看动态效果）：

```bash
python3 $VTX -t timeline.json video --draft --range <字幕窗入点> <出点>   # 带字幕烧录的区间预览（含音频），核对对齐/样式随时间的变化；产物 cache/preview/range_*.mp4，耗时双口径：纯回看窗 ~5s，改动窗（字幕改动使窗内 rseg 内容键失效需重编码，含窗口混音）显著更长（DEV.md「迭代回路基线」）
```

**已知坑位**：
- **subs check FAIL 先分辨存量与新增**（不要求新代码）：用 `vtx diff out/subs_check_prev.json out/subs_check.json`（若留存上一版报告）逐项对比状态翻转；无上一版报告时对改前 timeline 基线复跑一次 subs check 作对照——只修本次改动引入的新增 FAIL，存量问题单独立项，不混入本轮验收口径。
- **subs check 对齐检查依赖 name="voice" 音轨**：没有 voice 轨或字幕距最近口播窗 >5s 时该项出 WARN（不静默跳过），此时对齐靠 preview 人审兜底。
- 阈值语义：对齐 FAIL ±1.5s / WARN ±0.5s（`--align-threshold` 可调）；CPS >9 FAIL / >7.5 WARN（与 audit 同基准）；行宽 28 字/行 × 2 行（PlayResX 1920 / Fontsize 58 / MarginL/R 60 推算）。
- subs preview 三级背景回退（qc_clean 提帧 → 图层单帧合成 → 黑场兜底并 WARN）；背景为黑场时只审字幕位置/样式，画面构图回 R2 走 draft。
- 字幕 ass 以字节进段内容键（composite._seg_content_key）：改一条字幕只失效其覆盖的 1-2 段，**无需也不许手工清段缓存**。
- subs render 写的 cache/subs.ass 与 video 阶段正式渲染同路径（draft 用 subs_draft.ass 不受影响）；--draft 分支不会读它，正常接力无竞态。

---

## R3 · gfx 页改动（html 修订 / 新页）

**适用场景**：修改 `gfx/*.html`（数据、布局、动效纯函数）。

**前置条件**：先按 [HTML 创作组织](gfx.md#html-创作组织) 确认是页内模块修改还是确需新页，再遵守帧驱动协议（动画=帧号 f 的纯函数；细节读 `gfx.md`）。

```bash
cd ~/文档/04_bilibili视频/voice-ime
python3 $VTX -t timeline.json snapshot
# …改 gfx/xxx.html…

python3 $VTX -t timeline.json validate
python3 $VTX -t timeline.json gfx            # 内容寻址：html 字节变 → 键自动变 → 只重渲该页
python3 $VTX -t timeline.json video --gpu
python3 $VTX -t timeline.json audio          # 若音频无关可省（audio 键未变则命中）
python3 $VTX -t timeline.json mux
python3 $VTX -t timeline.json qc
```

**耗时基准**：单页重渲秒级（CDP 常驻浏览器 ~34ms/帧；透明快路径 ~15ms/帧）；全量冷 gfx 129.8-131.9s（12024 帧，默认 7/450/4，2026-09-11 方案 v2 定版；桌面活跃时段约 150s）；gfx 全冷+全链+4K ≈6 分钟（方案三 2026-09-13 口径：验证链 ≈291s + 4K ≈66s；〔方案三前 ≈5.5 分钟，方案一口径〕）。

**gfx 基准表**（2026-09-11 方案 v2 矩阵，voice-ime 28 页 12024 帧、本机 32C64G，冷跑 WALL / 峰值 Chrome RSS，配置=PAGES/SPLIT_FRAMES/TABS）。**单机校准口径：32C64G / voice-ime 28 页——换机（核数/内存/页数任一变化）先按下方「重测方法」节复测，勿直接引用本表数字**：

| 配置 | WALL s | 峰值 RSS GB | 判定 |
|---|---|---|---|
| 6/0/4（旧默认） | 161.9（当次校准基线 160.25） | 13.0-16.4 | 基准 |
| 8/600/4 | 129.3（±0.5%） | 16.5-17.3 | 破 16GB 门 |
| 12/0/4 | 145.6 | — | 只加页无效（CPU 43-58%） |
| 8/450/4 | 126.5-143.9 | 16.5-17.9 | 破 16GB 门 |
| 8/450/6 | 112.9-122.1 | 19.8-20.4 | RSS 破线**被否** |
| **7/450/4** | **129.8-131.9** | **15.0-15.2** | ✅ **生产默认**（commit 2e65536） |

≤110s 硬门未达，归因＝四个重页（g6b_endboard 765 帧/c5a 1153/c5b 810，零静态跨度）只剩截图通路（canvas 批量导出通道已证伪，DEV.md §二），g6b 串行估算 ≈80s 为硬下限。

**gfx 重测方法**（改引擎配置/代码后按同一口径复测，像素门槛不过=白测）：

```bash
cd ~/文档/04_bilibili视频/voice-ime
rm -rf cache/gfx                                   # 冷跑口径；当次 6/0/4 基线偏差 >20% 先查机器再比数字
time env python3 $VIDEO_TOOLS/vtx/vtx.py -t timeline.json gfx
env python3 $VIDEO_TOOLS/scripts/gfx_golden.py compare -t timeline.json \
    --golden cache/gfx_golden.json --strict --report cache/compare_retest.json
# compare 全零才通过（golden=28 页 3536 帧、5 页全帧；--strict 防缓存未重渲假一致）
```

**gfx env 速查**（默认即定版配置，改前先看基准表）：

| env | 默认 | 说明 |
|---|---|---|
| `VTX_GFX_PAGES` | 7 | 页级并行浏览器实例数 |
| `VTX_GFX_SPLIT_FRAMES` | 450 | 页内分片阈值（帧，0=关）；主要收益来源 |
| `VTX_GFX_SPLIT_MAX` | 3 | 单页最大分片数 |
| `VTX_GFX_TABS` | 4 | 每实例 tab 数（6 有收益但 RSS 破 16GB 门） |
| `VTX_GFX_NAVFREE` | 0 | 免导航截图，**勿开 1**（负载下像素漂移且更慢） |
| `VTX_GFX_STATIC` | 1 | 静态跨度字节复制（`__vtx_static_spans`） |
| `GFX_GOLDEN_WORKERS` | min(8,CPU) | golden 比对解码并行度 |

**必跑验证门**：validate → qc（上层窗口生效/空窗无泄漏）→ 视觉抽帧人审（DOM/像素证据才是权威）。

**已知坑位**：
- **body 级 opacity 页**（如 g6a_title）会导致 Chrome 合成组拍平、透明 override 静默失效——flatten 守卫双探针帧**自动回落双截**，日志出现回落属正常现象，不是错误。
- 页级并行 `VTX_GFX_PAGES` + 页内分片 `VTX_GFX_SPLIT_FRAMES` 双轮驱动（Chrome 同源 tab 共用渲染进程，单浏览器只加 tab 无效；只加页不切片也无效）；配置改动前先看上方基准表与 DEV.md §二证伪记录。
- 回退开关 `VTX_GFX=cli`（引擎异常时强制旧 CLI 逐帧路径）。

---

## R4 · 全冷重渲（新机 / 缓存清空 / 大版本回归）

**适用场景**：cache 为空、换机、或需从头验证全链。

**v2 链（§1.2 响度终链合并后）**：限幅在 `vtx audio` 内部完成，全冷链简化为 **`all --until video` → mux → qc**——`all` 一条龙会把 mux/qc 白跑一遍（音频终态已在波内就绪），截断后再接力封装与质检：

```bash
cd ~/文档/04_bilibili视频/voice-ime
python3 $VTX -t timeline.json validate
python3 $VTX -t timeline.json all --until video   # 串行头+并行波（gfx‖audio‖seg 预编码）→ video 截止；audio 已含增益+alimiter 终链
python3 $VTX -t timeline.json mux                 # 正式封装（libfdk_aac 192k）
python3 $VTX -t timeline.json qc
python3 $VIDEO_TOOLS/apps/gates/content_gates.py -t timeline.json --media out/final.mp4
```

**耗时基准**（bench_p2 16746 帧/558.2s@1080p30（voice-ime 同型），RTX 4080/32C64G）：**方案三后（2026-09-13 定版，3 轮冷跑中位）验证链冷跑（validate→gates）≈291s（289.6/291.4/310.0，`bash $VIDEO_TOOLS/scripts/bench_cold.sh 3` 实测）**——并行波（gfx 关键路径）165s（163.1-183.6，波动 12%）+ video 尾 97s + qc 8.6s（rc=1 = 74/75，c4_mini 已知探针假阴性的预期形态）+ gates 8.1s（五门全绿）+ mux 4.4s + 串行头（validate→prepare）5.6s；〔v1 时代另有独立 post_loudness 2.6s 步骤，v2 §1.2 已并入 audio 波内，不再单列〕；4K 发布链单列 ≈66s（8 段并行 nvenc p4 + concat，`bench_cold.sh … --with-4k`）。〔方案三前：≈299s（方案二 2026-09-12 定版，voice-ime 口径）〕〔方案二前：全链冷启动 ≈5.5 分钟（拆包提速后，2026-09-11）〕gfx 全冷 ~130-180s；缓存命中全链 ~50s。

**流水线/编码器 env 速查**（默认即定版，回退开关独立 A/B）：

| env | 默认 | 说明 |
|---|---|---|
| `VTX_PIPELINE` | 1 | =0 回退线性序列（validate→prepare→gfx→video→audio→mux→qc 串行） |
| `VTX_SEG_NVENC` | 1 | =0 seg 预编码回 x264（探测失败显式告警回落，不静默） |
| `VTX_FILTER_THREADS` | 1 | 滤镜图线程数；单进程路径（draft 头窗/单段）可显式 4 |
| `VTX_NVENC_BUDGET` | 8 | 进程内 NVENC 会话预算（=并发拐点，勿上调；4K 导出并行时下调） |

**冷跑基准**：`bash $VIDEO_TOOLS/scripts/bench_cold.sh [轮数] [项目目录]`——清 cache 逐行时间戳跑 `vtx all`，轮后自动提取各阶段摘要（基准数字归档 DEV.md §一；不触碰 baseline_threads1/）。**警告**：该脚本会在项目本目录清 cache 并全量渲染直写 `out/final.mp4`——对已冻结/已交付项目跑基准必须先复制项目到独立目录（voice-ime 投稿母带曾于 2026-09-21 被冷跑基准覆盖，2026-10-03 才经 md5 比对发现）。

**必跑验证门**：qc 全项 + content_gates + check_4k（若出 4K 版）。

**已知坑位**：qc_clean 由 video 阶段尾部生成，渲染中断会残留截断文件使 qc 假失败——`rm cache/qc_clean.mp4` 重跑 video 重建。长命令后台+轮询。

---

## 主控-子智能体棒次协议

创作分工的页面/模块边界先依 [HTML 创作组织](gfx.md#html-创作组织) 写入任务书；本协议的「一棒」不定义 HTML、clip 或轨道数量。

1. **棒次 prompt 必须自包含**：权威仓路径、任务目标、报告落盘路径（`<video_tools>/docs/reports/`）、**严禁 git commit**（改完留工作区由主控提交）。
2. **worker 只读 runbook 对应配方**，不读全仓；配方外需求回报主控拍板，不自行发挥。
3. **命令原样执行**；环境变量占位先赋值，项目路径按棒次替换。
4. **REPORT 契约**：每棒结束写 `<video_tools>/docs/reports/REPORT-<棒次>.md`，四节=①结论 ②改动/证据清单（关键行 cat 摘录）③逐步耗时 ④遗留问题。
5. 长命令（>60s 预期）后台运行+轮询；调试中间物写 `cache/tmp/<runid>/` 阶段结束即删（**严禁 GB 级中间物写系统 /tmp**，本机 /tmp 是 tmpfs）。

## 命令正字法速查（易抄错项）

- **vtx 子命令全集**：`all | validate | audit | prepare | gfx | video | audio | mux | qc | clean | gc | df | snapshot | subs | diff`（subs=字幕校验，diff=时间线差异审计；全集对照 `vtx/cli.py` STAGES 表）。常用开关：`-t <timeline.json>`、`--draft [--frames N]`（仅 video/mux）、`--gpu`（仅 video）、`all --until <stage>`（截断，如 `all --until video`）、`gc --dry/--all`。入口：`python3 $VTX …`。
- **json2blend**（⚠ **必须由 blender 解释器运行，python3 直跑会 ModuleNotFoundError**）：
  ```bash
  blender --background --factory-startup --python $VIDEO_TOOLS/apps/blender-bridge/json2blend.py -- \
      <项目>/timeline.json <项目>/preview.blend
  ```
  参数在 `--` 之后：`<timeline.json> <out.blend>`，无 `-t` 开关。
- **verify_blend**（同理 blender 运行，参数序=timeline 在前 blend 在后）：
  ```bash
  blender --background --python $VIDEO_TOOLS/apps/blender-bridge/verify_blend.py -- \
      <项目>/timeline.json <preview.blend>
  ```
- **audit_bundle**（普通 python3 可运行）：`python3 $VIDEO_TOOLS/apps/blender-bridge/audit_bundle.py -t <项目>/timeline.json [--prefix bgm2- …]`，产物 `<项目>/cache/audit_bgm/`（产线配方 R6 见 tech-talk runbook-r5r6r7.md）。
- **content_gates**：`python3 $VIDEO_TOOLS/apps/gates/content_gates.py -t <项目>/timeline.json --media <项目>/out/final.mp4 [--project-gates 门.json] [--out <项目>/out/content_gates.json]`——`--out` 落本轮结论留档 JSON（各门 pass/实测值/media+timeline md5 指纹/时间戳），音频链改动后重跑 gates 的证据链用。
- **post_loudness（v2 兼容薄壳，正常链路无需）**：`bash $VIDEO_TOOLS/scripts/post_loudness.sh <项目目录>`——限幅已在 `vtx audio` 尾部一体完成，本命令仅对 v1 遗留产物/排查场景做兼容重跑（对已终态 audio.wav 幂等直过）。
- **check_4k**：`bash $VIDEO_TOOLS/apps/publish/check_4k.sh <4k文件> [期望帧数]`（帧数缺省时从 timeline.json 自动推导；4K 发布链配方见 tech-talk runbook-r5r6r7.md R5）。
