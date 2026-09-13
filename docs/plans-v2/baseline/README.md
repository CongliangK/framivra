# vtx P0 基线（voice-ime 影子演练，2026-09-13）

> **2026-09-13 P1 重锚注记**：audio 全部输出与 qc 的 TP 项已被 `../baseline-r2/`
> 取代（v2 响度终链落地，ARCHITECTURE §9 门 2 两段口径）；本基线（r1）仍是
> validate/diff/subs 及 qc 其余项的对比权威。复验命令：
> `compare.sh <新目录> ../baseline-r2`。

video_tools v2 重构的 P0 基线：在 live voice-ime 的 **cp -rs 影子副本**上跑 5 条
vtx 只读/近只读命令，归档原始输出 + 归一化产物，冻结对比口径。v2 实现（P1/P5）
复验时按「演练程序」重跑 + `compare.sh` 对比即可。

- vtx 代码：主工作区 v1 `main` @ `c2f849c`（`/home/liangruochong/文档/01_活跃项目/video_tools/vtx/vtx.py`）
- 影子区：`/home/liangruochong/文档/04_bilibili视频/_shadow_v2/`（可整目录删除重建）
- live 项目零写入目标**未完全达成**：除「红线事件留档」所述 3 个 stem 文件被
  写穿（内容确定性等价）外，其余零写入（`find -newermt` 全量核验）
- 环境留档：`env.txt`（ffmpeg 8.1.2 / Python 3.13.11 / Chrome 152 / 32C）

## 五命令结果（退出码均为 v1 语义预期值，不是演练失败）

| # | 命令（cwd=影子，`-t timeline.json`） | exit | 关键结论 |
| - | --- | --- | --- |
| a | `validate` | 0 | PASS：45 源 / 5 视频轨 69 clips / 150 字幕 / 17 音轨 73 事件 |
| b | `diff timeline.v4.2-frozen.json timeline.json` | 1 | DIFF（语义 diff 有差异=1 属预期）：2 条差异（`_meta` 注释、`audio_tracks[bgm-intro]` +1 事件）；seg/gfx/vseg 失效 0，需重跑 validate→audio→mux→qc |
| c | `audio --report` | 0 | **WARN**（PASS 4 / WARN 1 / FAIL 0）：整片 I −14.0 LUFS ✓、人声-床乐差 22.0dB ✓、duck 咬合 0.05s/回升 2.41s ✓；WARN=片尾静音空洞 555.0–558.2s(3.2s) |
| d | `subs check` | 1 | **FAIL**：150 条中 align FAIL 145 / 行宽 FAIL 27 / CPS WARN 17 / overlap FAIL 0（对齐阈值 ±1.5s） |
| e | `qc` | 1 | **74/75 通过**：唯一 ✗ = `gfx 窗口 c4_mini 生效`（已知假阴性，bench_p2 实锤探针问题非内容问题）；免检 69 项；响度回执命中 −15.5 LUFS 免测 |

辅助：`raw/prepare.log`（影子内冷抽 45 个 stem 的前置步骤，exit 0，非五命令之一）。

## 基线清单

```
baseline/
├── README.md            本文件
├── NORMALIZATION.md     归一化对比口径（冻结）
├── normalize.py         归一化唯一权威实现（text/json 两模式）
├── compare.sh           复验对比器：compare.sh <新基线目录> → 逐条 PASS/FAIL
├── run-baseline.sh      演练执行器（冻结顺序：prepare→重播种→5 命令）
├── env.txt              工具链版本留档（测量值依赖工具链）
├── raw/                 原始输出：首行 `# exit=N` + 完整 stdout/stderr
│   ├── validate.log  diff.log  subs.log  qc.log  audio.log  prepare.log
└── norm/                归一化产物（P1/P5 对比主体）
    ├── validate.norm.txt  diff.norm.txt  subs.norm.txt  qc.norm.txt  audio.norm.txt
    └── subs.norm.json  qc.norm.json  audio.norm.json（落盘报告的归一化副本）
```

## 演练程序（冻结，重跑须逐字遵守）

1. 影子重建：`rm -rf <影子>/voice-ime-shadow && cp -rs <live 绝对路径> <影子>/voice-ime-shadow`
   （**必须绝对源路径**：GNU cp 对相对源路径在子目录建链接报
   「只能在当前目录中创建相对的符号链接」）；
2. 补兄弟引用：`ln -s ../_library <影子区>/_library`（timeline sources 引用
   `../_library/...`，影子深一层，缺此链接则源全 MISSING → 回执指纹失配 →
   依赖门全灭）；
3. 去链接化写目标（防穿透 live）：`rm -f out/audio.wav out/audio_report.json
   out/qc_report.json out/subs_check.json cache/report_audio/stem_voice.wav
   cache/report_audio/stem_bed_raw.wav cache/report_audio/stem_bed_duck.wav`
   （后四项：subs 报告防御性删除 + **三个 report stem 必删**——首跑曾因漏摘
   写穿 live，红线事故见下文留档）；receipt.json 与 cache/manifest.json 从
   live `cp -L` 成实体（依赖门/记账要读旧状态）。run-baseline.sh 对
   receipt/manifest 实体化与 stem 链接态有**机器防呆**（不满足直接 exit 9），
   本步骤的 rm 仅作前置冗余；
4. `run-baseline.sh [raw输出目录]`（内含 prepare 冷抽 stem + 回执重播种 + 5 命令
   顺序；不传参时 raw 落影子区时间戳目录，**拒绝写入基线目录**防覆盖首跑证据）；
5. `normalize.py` 产 norm/（8 份，命令见 NORMALIZATION.md）。

## 五命令写盘说明（哪些进影子、哪些不落盘）

| 命令 | 写入（全部落在影子内） | 不落盘 |
| --- | --- | --- |
| validate | out/receipt.json（begin_stage 撤销自身 + 成功回执重写） | 无报告文件 |
| diff | 无（纯读，不依赖 -t/receipt） | —— |
| subs check | out/subs_check.json | 不动 receipt/cache |
| qc | out/qc_report.json、out/receipt.json、cache/qc_pass_segs.json（tmp+replace，只换影子目录项）、cache/qc_probe_*.raw（用后即删） | loudness 命中时不动 cache/audio/loudness_receipts.json |
| audio --report | out/audio.wav（107M 重混）、out/audio_report.json、out/receipt.json、**cache/report_audio/stem_{voice,bed_raw,bed_duck}.wav（3×107M，固定名目录，非 tmp_run）**、cache/tmp/<runid>/（测量中间物，结束即删）、cache/audio/*.wav（prepare 前置写入，~1.7G）、cache/manifest.json 记账 | —— |
| （prepare 前置） | cache/audio/<新键>.wav ×45 | —— |

**穿透防线**（为什么影子安全）：vtx cache/out 定位不 resolve 符号链接（vtx.py:539），
写操作仅三种形态——①ffmpeg/write_text 直写（目标若为符号链接会穿透 → 演练前已
rm 或实体化）；②tmp+`os.replace`（只替换影子目录项，不穿透）；③新键新文件。
loudness/免检回执走 ②，receipt/manifest/三报告走 ①（已去链接化）。

## 红线事件留档（本基线首跑事故，复验前必读）

**事故**：首跑 `audio --report` 时，live 的 `cache/report_audio/stem_voice.wav /
stem_bed_raw.wav / stem_bed_duck.wav`（3×107M）经影子符号链接被写穿覆盖
（19:20:50–59）。根因：audio_report 的 stem 落在**固定名目录** `cache/report_audio/`
（audio_report.py:356），演练前写目标枚举误判其为 tmp_run 临时目录、未摘链接。

**损害评估（功能性损害≈0，但 mtime/ctime 变更不可逆）**：
- 回执指纹（266f63b2d790，9月13 13:55 起未变）证明 timeline+全部源输入在覆盖
  前后完全一致 → 新 stem = 同一 filtergraph 对同一输入的确定性重渲；
- 尺寸逐字节同为 107174478B；衍生测量全部对上锚点（人声-床乐差 22.0dB、duck
  咬合 0.05s/回升 2.41s、新旧 audio_report summary/thresholds 全等）；
- 与 live 旧报告（9月12）仅有的数值差：整片 I −14.1 → −14.0 LUFS（0.1 LUFS
  级测量抖动，两者同判 PASS），及 duck detail 文案差（live 旧报告由 9月12 16:00
  改版前的 vtx 生成，属代码版本差非内容差）；
- 该目录不参与任何缓存键（固定名，非内容寻址），无下游 stat 绑定；live 下次
  `audio --report` 本就会原位重渲这 3 个文件。

**处置**：已固化影子侧 3 文件为实体（防重放再穿透）；run-baseline.sh 对三 stem
链接态内置**机器防呆**（`[ -L ]` 检测，命中即 exit 9）+ `out/subs_check.json`
防御性删除（另一条同形态直写穿透路径，live 现无该文件但不可依赖偶发现状）；
`find live -newermt <演练起点>` 复核改动范围**恰好为该 3 文件**，其余零写入
（含 out/ 全部产物、receipt、manifest）。

## live 输入冻结失效条件（基线有效期，P0 复审 P1-7）

首跑基线绑定 live 的下述状态，任一变化即基线失效（须在 live 新状态上重跑首跑
基线并归档为新版本，或显式声明基线过期后再对比）：

1. **live timeline.json 字节变更** → 重播种的 receipt `input_fingerprint` 失配 →
   load_receipt 整体丢弃 → qc 依赖门**直接拒绝**（不止「不可复现项 1」说的免检
   退化——那是 stat 绑定失配的轻路径，指纹失配是重路径）；
2. **live 重渲染/重封装（out/final.mp4 或 audio.wav 变）** → 免检/响度回执 stat
   绑定失配 → qc 输出形态变化（属基线漂移，非 v2 回归）；
3. **live 跑过 subs check** → 影子重建带 out/subs_check.json 符号链接 →
   subs.py write_text 直写穿透 live（run-baseline.sh 已内置防御性 rm 兜底）；
4. **timeline.v4.2-frozen.json 被清理** → diff 对照件缺失。

## 已知不可复现项 / 行为注记

1. **温态效应（复验须同态）**：qc 的 69 项免检 + 响度回执命中依赖 live 的
   `cache/qc_pass_segs.json`、`cache/audio/loudness_receipts.json`（stat 绑定一致
   才命中）。同 live 重建的影子必然同态；若 live 后续变动（重渲染/重封装），
   免检退化为全检、响度改为实测，qc 输出形态会变——这不是 v2 回归。
2. **缓存键路径敏感**：stem/seg/gfx 键含绝对路径（hygiene._fp），影子路径 ≠ live
   → 影子缓存全 miss，必须先 prepare（run-baseline.sh 已内置）；loudness/免检键
   用 `path.resolve()`（穿透符号链接=live 路径）→ 反而命中。两套键口径不一致
   本身是 v1 行为，基线如实记录。
3. **subs check 的 145 条对齐 FAIL 是 v1 对该 timeline 的真实判定**（字幕与 voice
   窗口系统性偏移数秒；live 项目从未留过 subs_check.json）。v2 复现同一输出即为
   等价，是否「应該 FAIL」不属基线议题。
4. **qc 的 c4_mini ✗ 为已知假阴性**（免检机制注释与 bench_p2 实证均确认），
   74/75 是 live 末次 qc 的既有状态。
5. diff 退出码语义：DIFF=1（本基线即 1）；subs/qc 的 FAIL 退出码 1 同理——
   compare.sh 比较的是归一化产物与首行 `# exit=N`，不把 1 当演练失败。
6. **~0.1 LUFS 级浮点抖动（已实证）**：同一 timeline 的两次 audio --report，
   整片 I 实测 −14.1（live 9月12）vs −14.0（影子首跑）LUFS——同判 PASS 不影响
   判级，但 detail 文本含该数值，逐字节 compare 时可能出现该单点差异。复验遇
   此差异且判级/其余项一致时，按「测量抖动放行」处理并核对 env.txt 工具链。
