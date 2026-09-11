# runbook · 标准修改配方集（R1-R6）与棒次协议

> **定位：接棒子智能体「只读这一份就开工」的配方文件。** 路由索引在 `../SKILL.md`，字段语义与坑表在 `edit.md`/`gfx.md`；本文只回答「接到某类任务后，按什么顺序敲哪些命令」。命令必须与 `edit.md`/各脚本 `--help` 及仓库现状一致，本文每条配方都经过 voice-ime（v3.x–v4.1）实测。
>
> `<项目>` = 项目目录占位（下文用真实项目 voice-ime 作示例）；`<VTX>` = `~/.agents/skills/video/vtx/vtx.py`（符号链接，权威本体 `~/文档/01_活跃项目/video_tools/vtx/`）；`<video_tools>` = `~/文档/01_活跃项目/video_tools`。

## 使用规则（worker 必读）

1. **只读本文对应配方 + 被指派的棒次 prompt，不做全仓探索。** 深层语义拿不准时才查 `edit.md`（坑表）/`gfx.md`/脚本 `--help`，不要通读仓库。
2. **命令按配方原样执行，禁止凭记忆改参数**（改参数=改配方，须主控拍板）。
3. **预期 >60s 的长命令一律后台运行 + 轮询**（同步等待会被看门狗杀）。
4. 修改 timeline.json 一律走**幂等脚本 + `vtx snapshot`**（先快照后改动），不手写 .bak。
5. 每棒结束落 REPORT 到 `<video_tools>/cache/opt20250911/`（契约见文末）。

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

# 2) 重混音频（音视频画面不动，video 段缓存全命中）
python3 $VTX -t timeline.json audio

# 3) 响度限幅兜底 —— 不可跳过！
bash $VIDEO_TOOLS/scripts/post_loudness.sh ~/文档/04_bilibili视频/voice-ime

# 4) 封装（正式 libfdk_aac 192k）
python3 $VTX -t timeline.json mux

# 5) QC（响度/切点/黑帧）+ 内容验证门
python3 $VTX -t timeline.json qc
python3 $VIDEO_TOOLS/scripts/content_gates.py -t timeline.json --media out/final.mp4

# 6) 4K 投稿版重导出 + 断言
ffmpeg -i out/final.mp4 -vf scale=3840:2160:flags=lanczos \
  -c:v libx264 -profile:v high -preset medium \
  -b:v 19.5M -maxrate 55M -bufsize 80M -pix_fmt yuv420p \
  -c:a copy -movflags +faststart out/final_4k.mp4
bash $VIDEO_TOOLS/scripts/check_4k.sh out/final_4k.mp4

# 7) 冻结：更新 out/FREEZE.json（md5 清单）+ CHANGELOG.md 变更单 + timeline.<ver>-frozen.json
```

**耗时基准**（voice-ime 653s@1080p30，缓存命中）：validate ~3s、audio 数十秒、post_loudness ~10s、mux ~20s、qc ~11s、4K ~2.5min；**全链 ≈4-5 分钟**。

**必跑验证门**：validate（机制）→ qc（-14±1.5 LUFS/切点/帧数）→ content_gates（G1/G2/**G3 解码 TP**/G7/G10）→ check_4k（4K 版 14 项断言）。

**已知坑位**：
- **post_loudness 不可跳过**：audio→post_loudness→mux 三步链漏中步，G3 解码 TP 必超标（v4.0 并入时 G3 实测抓出）。alimiter 兜底段不可省——AAC 编码后真峰过冲，wav 域 TP 不算数。
- **响度语义**：post_loudness 归一后全片综合响度 I 恒为 ≈-14 LUFS；改某轨 volume 改变的是**轨间相对比例**，不是成片 LUFS。预期「成片更响」的验收口径要写对。
- 幂等脚本须带**防重施标记**（如改前检测目标值已是新值则跳过），防同脚本跑两遍把 -5dB 叠成 -10dB。

---

## R2 · 时间线编排小改（clip 窗口 / 轨道增删 / 字幕）

**适用场景**：改 `video_tracks` 的 clip 窗口、增删轨/事件、字幕条修订。

**前置条件**：同 R1；改动用幂等脚本实施。

```bash
cd ~/文档/04_bilibili视频/voice-ime
python3 $VTX -t timeline.json snapshot
# …幂等脚本改 timeline.json…

python3 $VTX -t timeline.json validate

# 缓存决策（gfx=内容寻址自动换键，通常无需手工清）：
#   · 改的是 clip 时间窗但源/参数没变 → seg 键不变，直接 video（命中）
#   · 改了源切片窗口/编码参数 → 旧键自然失效自动重渲；若怀疑误命中，rm cache/seg 对应段后重跑
#   · 改了 gfx html → gfx 键自动变（见 R3）

python3 $VTX -t timeline.json video --gpu    # 正式渲染（NVENC）；迭代期用 --draft --frames N
python3 $VTX -t timeline.json audio
bash $VIDEO_TOOLS/scripts/post_loudness.sh ~/文档/04_bilibili视频/voice-ime
python3 $VTX -t timeline.json mux
python3 $VTX -t timeline.json qc
python3 $VIDEO_TOOLS/scripts/content_gates.py -t timeline.json --media out/final.mp4
# 收尾习惯：先 gc --dry 看一眼，再 gc
python3 $VTX -t timeline.json gc --dry
```

**耗时基准**：缓存全命中全链 ~50s；video 段缓存命中、其余冷 ≈3min；video 冷（并行 8 段）~2min。

**必跑验证门**：validate → qc → content_gates。黑帧嫌疑（动过窗口边界）加 `blackdetect` 抽验（qc 内置零黑帧检查）。

**已知坑位**：framesync/CFR 补帧坑全部 vtx 内置（见 `edit.md` 坑表）；红队遇「相邻帧逐字节相同」属 CFR 复制补偿帧，是预期，但定稿前须帧数+黑帧+断点两侧抽验三重确认。**不要再手工 rm cache/seg（edit.md 明令），内容寻址已从机制上消除误命中**；只有排查疑点时允许定向清理后重渲。

---

## R3 · gfx 页改动（html 修订 / 新页）

**适用场景**：修改 `gfx/*.html`（数据、布局、动效纯函数）。

**前置条件**：页面符合帧驱动协议（动画=帧号 f 的纯函数；细节读 `gfx.md`）。

```bash
cd ~/文档/04_bilibili视频/voice-ime
python3 $VTX -t timeline.json snapshot
# …改 gfx/xxx.html…

python3 $VTX -t timeline.json validate
python3 $VTX -t timeline.json gfx            # 内容寻址：html 字节变 → 键自动变 → 只重渲该页
python3 $VTX -t timeline.json video --gpu
python3 $VTX -t timeline.json audio          # 若音频无关可省（audio 键未变则命中）
bash $VIDEO_TOOLS/scripts/post_loudness.sh ~/文档/04_bilibili视频/voice-ime
python3 $VTX -t timeline.json mux
python3 $VTX -t timeline.json qc
```

**耗时基准**：单页重渲秒级（CDP 常驻浏览器 ~34ms/帧；透明快路径 ~15ms/帧）；全量冷 gfx 129.8-131.9s（12024 帧，默认 7/450/4，2026-09-11 方案 v2 定版；桌面活跃时段约 150s）；gfx 全冷+全链+4K ≈5.5 分钟。

**gfx 基准表**（2026-09-11 方案 v2 矩阵，voice-ime 28 页 12024 帧、本机 32C64G，冷跑 WALL / 峰值 Chrome RSS，配置=PAGES/SPLIT_FRAMES/TABS）：

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

```bash
cd ~/文档/04_bilibili视频/voice-ime
python3 $VTX -t timeline.json validate
python3 $VTX -t timeline.json all            # prepare→gfx→video→audio→mux→qc 一条龙（不含 post_loudness）
bash $VIDEO_TOOLS/scripts/post_loudness.sh ~/文档/04_bilibili视频/voice-ime
python3 $VTX -t timeline.json mux            # all 里已含 mux 时此步幂等确认即可
python3 $VTX -t timeline.json qc
python3 $VIDEO_TOOLS/scripts/content_gates.py -t timeline.json --media out/final.mp4
```

**耗时基准**（voice-ime 653s@1080p30，RTX 4080/8 核，2026-09-11 拆包提速后；gfx 为同日方案 v2 定版数字）：gfx 全冷 ~130s、video 并行 ~2min、**全链冷启动 ≈5.5 分钟**、4K 另加 ~2.5min；缓存命中全链 ~50s。

**必跑验证门**：qc 全项 + content_gates + check_4k（若出 4K 版）。

**已知坑位**：qc_clean 由 video 阶段尾部生成，渲染中断会残留截断文件使 qc 假失败——`rm cache/qc_clean.mp4` 重跑 video 重建。长命令后台+轮询。

---

## R5 · 发布链（4K 投稿版 + 冻结）

**适用场景**：母带 out/final.mp4 已过全部门，出投稿版并冻结。

```bash
cd ~/文档/04_bilibili视频/voice-ime

# 1) 4K lanczos 放大（音频 copy 不重编码）
ffmpeg -i out/final.mp4 -vf scale=3840:2160:flags=lanczos \
  -c:v libx264 -profile:v high -preset medium \
  -b:v 19.5M -maxrate 55M -bufsize 80M -pix_fmt yuv420p \
  -c:a copy -movflags +faststart out/final_4k.mp4

# 2) 4K 断言（9 项：分辨率/帧率/h264 High/yuv420p/码率 cap/帧数/aac/I 间隔/faststart）
bash $VIDEO_TOOLS/scripts/check_4k.sh out/final_4k.mp4

# 3) 冻结三件套：
#    out/FREEZE.json（md5 清单，final.mp4+final_4k.mp4+timeline）
#    CHANGELOG.md 变更单（版本号/改动/验证门结果）
#    timeline.<版本号>-frozen.json（冻结时间线快照）
# 4) 发布物料：PUBLISH-PACK.md（参数+简介+备答）
```

**耗时基准**：4K 转码 ~2.5min；check_4k 秒级。

**必跑验证门**：check_4k 全 PASS（任一 FAIL 退出码 1）。码率门覆盖用 `BITRATE_CAP_MBPS=20.5 bash check_4k.sh …`。

**已知坑位**：
- **冻结纪律**：FREEZE 记录的 md5 必须与当轮产物一致；后续任何改动（哪怕只重导 4K）都要更新 FREEZE 对应条目（final_4k md5 会变、final.mp4 不变时只改一处并注明）。
- AI 不手写 ffmpeg **剪辑**命令；此处的 4K 重编码是发布转码不是剪辑，属白名单用法（剪辑永远走 timeline+vtx）。

---

## R6 · BGM 挂载 / 音量改动后（分轨审计包）

**适用场景**：BGM 正式版挂载进 timeline、或已挂载 BGM 的音量/分轨有改动（用户裁定：正式版默认必做）。

**前置条件**：R1（或 R2）渲染链已跑完、验证门 PASS。

```bash
cd ~/文档/04_bilibili视频/voice-ime
python3 $VIDEO_TOOLS/blender_bridge/audit_bundle.py -t timeline.json --prefix bgm2-
# 产物：<项目>/cache/audit_bgm/  ├ mp3/（逐源 320k 试听件，内容校验缓存）
#                               ├ timeline.json（逐轨一列审计时间线）
#                               └ audit.blend（Blender 审计工程，交用户逐轨静音/独听）
```

**重跑规则**：分轨或音量改动后**重跑即可**——MP3 按源内容和转码规格校验缓存；`--force` 强制全量重转。草稿/内部迭代轮不要求，正式版必做（人机协作交付门）。`--whole` 保留所有原事件；默认切分的 loop/fade 回退和审听边界见 `docs/audio-audit.md`。

**耗时基准**：分钟级（逐轨 ffmpeg 转码 + json2blend + verify_blend）。

**必跑验证门**：audit_bundle 内置五步（转码→审计 timeline→vtx validate→audit.blend→verify_blend 结构校验）。

**已知坑位**：给用户的 Blender 用法话术与交付规程见 music skill「交付门：分轨审计包」节与 `blender_bridge/README.md`。

---

## 主控-子智能体棒次协议

1. **棒次 prompt 必须自包含**：权威仓路径、任务目标、报告落盘路径（`<video_tools>/cache/opt20250911/`）、**严禁 git commit**（改完留工作区由主控提交）。
2. **worker 只读 runbook 对应配方**，不读全仓；配方外需求回报主控拍板，不自行发挥。
3. **命令原样执行**；环境变量占位先赋值，项目路径按棒次替换。
4. **REPORT 契约**：每棒结束写 `<video_tools>/cache/opt20250911/REPORT-<棒次>.md`，四节=①结论 ②改动/证据清单（关键行 cat 摘录）③逐步耗时 ④遗留问题。
5. 长命令（>60s 预期）后台运行+轮询；调试中间物写 `cache/tmp/<runid>/` 阶段结束即删（**严禁 GB 级中间物写系统 /tmp**，本机 /tmp 是 tmpfs）。

## 命令正字法速查（易抄错项）

- **vtx 子命令全集**：`all | validate | audit | prepare | gfx | video | audio | mux | qc | clean | gc | df | snapshot`。常用开关：`-t <timeline.json>`、`--draft [--frames N]`（仅 video/mux）、`--gpu`（仅 video）、`gc --dry/--all`。入口：`python3 $VTX …`。
- **json2blend**（⚠ **必须由 blender 解释器运行，python3 直跑会 ModuleNotFoundError**）：
  ```bash
  blender --background --factory-startup --python $VIDEO_TOOLS/blender_bridge/json2blend.py -- \
      <项目>/timeline.json <项目>/preview.blend
  ```
  参数在 `--` 之后：`<timeline.json> <out.blend>`，无 `-t` 开关。
- **verify_blend**（同理 blender 运行，参数序=timeline 在前 blend 在后）：
  ```bash
  blender --background --python $VIDEO_TOOLS/blender_bridge/verify_blend.py -- \
      <timeline.json> <preview.blend>
  ```
- **audit_bundle**（普通 python3 可运行）：`python3 $VIDEO_TOOLS/blender_bridge/audit_bundle.py -t <项目>/timeline.json [--prefix bgm2- …]`，产物 `<项目>/cache/audit_bgm/`。
- **content_gates**：`python3 $VIDEO_TOOLS/scripts/content_gates.py -t <项目>/timeline.json --media <项目>/out/final.mp4 [--project-gates 门.json]`。
- **post_loudness**：`bash $VIDEO_TOOLS/scripts/post_loudness.sh <项目目录>`（项目目录须含 `out/audio.wav`，即在 vtx audio 之后）。
- **check_4k**：`bash $VIDEO_TOOLS/scripts/check_4k.sh <4k文件> [期望帧数]`（帧数缺省时从 timeline.json 自动推导）。
