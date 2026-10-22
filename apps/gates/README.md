# gates · 成片内容级验证门（发布前复检）

组件 manifest 见 `app.json`；命令使用本目录真实路径，回归测试见本目录 `tests/`。

## 用途

发布前对成片做与具体项目零耦合的硬门断言（ARCHITECTURE §3.2 定位：**复检非构成**——内核 qc/TP 门恒在，本 app 关停不影响出片可信度）：

- **G1** 口播窗−间隙窗中位响度差 + 口播窗均匀极差（批量 ebur128，失败回落逐窗）
- **G2** 全片综合响度 ∈ [-15.5, -14] LUFS（解码域）
- **G3** 编码后解码真峰 ≤ -1.0 dBFS（**与内核 qc 同阈值 -1.0、消费同一 `vtx/media/loudness_cache` 回执不重测**——TP 单项判定权威已上内核，此处保留为发布侧复检，§1.3）
- **G7** blackdetect 零黑帧
- **G10** 帧数/时长/帧率断言（-count_packets 默认口径）+ 打包编码器报告
- `--project-gates` 用户自定义字段相等断言（frames/first_voice_frame/*_events/duration_frames）
- `--out out/content_gates.json` 本轮结论留档 JSON（各门 pass/detail/skip + 实测数值 + media/timeline md5 指纹 + 时间戳；默认不写）——「音频链改动后必须重跑 gates」的证据链落盘

报告的 `pass` 与退出码只代表**实际执行范围**：至少一项门已执行且全部通过才是 `pass=true` / exit 0；零项检查或任一失败均为 exit 1。`complete` 表示没有跳过项；`status` 为 `pass`、`partial` 或 `fail`，成功但含显式 `--skip` / 缺少可选输入的运行打印 `PARTIAL`，不能当作五门完整验收。`skipped` 保留每项跳过原因。解码失败或缺少测量不是跳过，也不是零黑帧；失败输出不能写入响度回执。

## 依赖

- ffmpeg / ffprobe（解码域测量、帧计数、封装探测）
- numpy（系统 python3）
- 内核包 vtx（`from vtx.media import loudness_cache`——响度回执共享；requires.core >= 2.0.0）

## 健康检查

`env python3 apps/gates/content_gates.py --help` 退出码 0（app.json health.command）。

## 开启步骤

以下示例从 Framivra 仓库根执行；`<video_tools>` 与 `<项目>` 分别替换为仓库、视频项目的绝对路径。方括号表示可选部分，执行前展开或省略。

```bash
cd "<video_tools>"
env python3 apps/gates/content_gates.py -t "<项目>/timeline.json" --media "<项目>/out/final.mp4" \
    --audio-wav "<项目>/out/audio.wav" [--project-gates "<项目>/gates.json"] [--skip G1,G7] [--count-frames] \
    [--out "<项目>/out/content_gates.json"]
# 本 app 测试（随迁件）
cd apps/gates && env python3 -m unittest discover -s tests -t . -v
# repo 根全量（含本 app）
cd "<video_tools>"
bash scripts/run_all_tests.sh
```
