# NORMALIZATION —— vtx P0 基线归一化对比口径（冻结）

v2 重构 P1/P5 复验对基线与新跑结果做逐条对比前，须先按本口径归一化。
归一化的唯一权威实现是同目录 `normalize.py`（改口径须同步改本文件）。
工具链版本依赖见 `env.txt`——测量类数值（LUFS/TP/像素差）随工具链变化，
复验前先核对环境一致。

## 原始产物格式（raw/）

每条命令一份 `raw/<命令名>.log`：**首行 `# exit=N`（退出码）**，其余为该命令
完整 stdout+stderr（合并捕获）。落盘报告另存于影子区 `out/`（qc_report.json /
audio_report.json / subs_check.json），归档时一并归一化。

- `<命令名>` ∈ {validate, diff, subs, qc, audio}（audio = `audio --report`）。

## 剔除（时间/环境敏感，不进对比）

| 类别 | 具体项 |
| --- | --- |
| 耗时行 | 含 `[timing]` 的行（audio --report 末行）、`预估耗时` 行（diff） |
| gfx 帧统计 | 含 `ms/帧` / `ms@p50` 的行（gfx 阶段输出，本 5 命令不出现，防御性保留规则） |
| 耗时/日期字段 | JSON 键：`estimated_seconds`、`estimate_basis`、`elapsed`、`duration_s`、`updated`、`when`、行首日期时间戳 |
| stat 指纹 | JSON 键：`mtime_ns`、`mtime`、`ctime`、`atime`、`size`、`digest`、`sha1`、`sha256`、`md5`、`stat`（receipt 的 stat 指纹段） |
| 绝对路径 | `/home/liangruochong/文档/04_bilibili视频/_shadow_v2/voice-ime-shadow` → `<SHADOW>`；`/home/liangruochong/文档/04_bilibili视频/voice-ime` → `<PROJECT>`（替换顺序敏感：影子前缀更长，先替换） |
| tmp run 目录 | `cache/tmp/<UTC时间戳>-<label>` 中的时间戳名 → `<TMPRUN>` |

## 保留（对比主体）

- raw 日志首行 `# exit=N`（退出码是判定信号：diff DIFF=1、subs/qc FAIL=1 属预期）；
- checks[] 的 `name` / `level`（或 `pass`）/ `detail` 及其中数值；
- 汇总计数与 PASS/FAIL/WARN 判定序列（summary/fail/warn/total 等）；
- 报告结构性内容：thresholds、skipped_probes、subs results[] 的 in_s/out_s/text
  （**内容时间坐标，非墙钟时间，保留**）。

## 实现约束

- JSON 一律 `json.load` + 递归遍历剔除（normalize.py `_normalize_obj`），
  **禁止手写正则硬抠 JSON**；输出 `sort_keys=True` 规范序。
- 文本走逐行过滤 + 子串替换（normalize.py `normalize_text`）。

## norm/ 产物清单

| 命令 | 文本归一化 | JSON 归一化（来源） |
| --- | --- | --- |
| validate | norm/validate.norm.txt | ——（无落盘报告） |
| diff | norm/diff.norm.txt | ——（未用 --json，冻结为人读摘要） |
| subs | norm/subs.norm.txt | norm/subs.norm.json（影子 out/subs_check.json） |
| qc | norm/qc.norm.txt | norm/qc.norm.json（影子 out/qc_report.json） |
| audio | norm/audio.norm.txt | norm/audio.norm.json（影子 out/audio_report.json） |

## 预期差异白名单（known-diffs.conf，P0 复审 P0-1/P1-4 裁定）

compare.sh 支持白名单豁免与数值容差，配置为同目录 `known-diffs.conf`：

- `json-drop-key <norm文件名> <点路径>`：双方归一化 JSON 剔除该键路径后比较（预期改写项）；
- `json-drop-items <norm文件名> <数组路径> <字段> <正则>`：剔除数组中字段匹配正则的条目（预期新增项，如 P1 后 qc 新增的 TP 判定项）；
- `num-tol <norm文件名> <容差>`：该 JSON 文件浮点字段按容差比较（仅 audio.norm.json，0.15——实证 0.1 LUFS 级测量抖动）。

白名单只承载 ARCHITECTURE.md §1.2/§1.3 **已拍板的设计内变化**（P1 响度终链合并：
qc 新增 TP 判定项、audio 的 TP 阈值/实测/判级改写）；**白名单外任何差异 = FAIL**。
P1 落地并建立重锚基线（baseline-r2/）后，audio 部分改为对重锚基线逐项比较，届时
移除本 conf 的 audio 相关行。

注记：audio.norm.txt 的「音频报告：WARN（…）」判级汇总行因与 `[timing]` 同行被
整行剔除——已知口径损失（计数在 audio.norm.json 有副本），接受不改（P0 复审 P2-5）。

## 复验程序（冻结）

1. 按基线 README「演练程序」重建影子区（cp -rs + `_library` 兄弟链接 + 去链接化）；
2. 执行 `run-baseline.sh`——脚本内含完整顺序：prepare（影子键冷抽 stem）→
   重播种回执（恢复被 prepare 撤销的 mux 门）→ validate → diff → subs →
   **qc → audio**（qc 必须先于 audio：`begin_stage("audio")` 会撤销 mux/qc 回执
   使 qc 依赖门拒绝）；
3. 用 normalize.py 产出新基线 norm/；
4. `compare.sh <新基线目录>` 逐条 diff，全一致为 PASS。

### 已知温态效应（对比时须同态，否则视为程序偏差）

- **qc 免检**：影子 `cache/qc_pass_segs.json`（符号链接→live）绑定 final.mp4 的
  stat；stat 一致时探测类检查走「免检」旁路（pass=True，detail 前缀「免检」）。
  复验影子从同一 live 建立即自然同态。
- **响度回执**：`cache/audio/loudness_receipts.json` 键含 `path.resolve()`（穿透
  符号链接解析为 live 路径）+ stat——同 live 建立的影子键相同，命中/未命中状态
  在两次运行间一致（命中即免测，数值与实测确定性相等）。
- diff 的退出码语义：PASS=0 / **DIFF=1**（有差异是预期，不是失败）；
  subs check：FAIL=1；qc：有 ✗ 项=1；audio --report：报告 FAIL=1。
