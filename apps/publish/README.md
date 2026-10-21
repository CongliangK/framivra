# publish · 4K 投稿版导出与发布链断言

组件 manifest 见 `app.json`；命令使用本目录真实路径，回归测试见本目录 `tests/`。

## 用途

4K 发布增强链（ARCHITECTURE §3.2 定位：**发布增强**——关停本 app 退回 1080p 母带直投，不影响主链产物可信度）：

| 件 | 用途 |
|---|---|
| `export_4k.py` | 4K 并行导出（方案三阶段2）：母带按帧均分 N 段并行 h264_nvenc p4 + lanczos 放大 + concat -c copy；段缓存内容寻址（复用 `vtx.cache_state.media_stat_digest`），重跑段全命中秒级；NVENC 探测失败显式回落 x264 medium 单进程（R5 命令逐字一致） |
| `check_4k.sh` | 4K 成片规格断言（多门规格断言：分辨率/帧率双验/h264 High/yuv420p/码率 ≤20.5M/时长帧数/aac 48k copy 区间/I 帧间隔/faststart） |
| `compare_4k_quality.py` | 画质门禁：nvenc 版 vs x264 基线同位抽帧 PSNR/SSIM（≥5 帧，或语义达标；解析失败 fail-closed exit 2） |

## 输入校验

- `--maxrate` 仅接受 ASCII 十进制码率，`k/K` 规范为 `k`、`m/M` 规范为 `M`；拒绝非有限值及低于 1000 bit/s 的值。两条编码链共用该解析结果。
- 母带必须含音轨；缺失时在编码器选择、段缓存创建前以中文提示退出 2。NVENC 与 x264 回落都要求音轨直通。

## 依赖

- ffmpeg / ffprobe（含 NVENC：h264_nvenc 可用则并行链，否则 --fallback-x264 回落）
- 内核包 vtx（`from vtx.cache_state import media_stat_digest`——段缓存键指纹；requires.core >= 2.0.0）；Python 零第三方包
- check_4k.sh 需 bash + ffprobe + python3（帧数推导读 timeline.json）

## 健康检查

`env python3 apps/publish/export_4k.py --help` 退出码 0（app.json health.command）；`check_4k.sh` 无参会打印用法并非零退出，须传入待验媒体（脚本无 `--help` 面）。

## 开启步骤

以下示例从 Framivra 仓库根执行；`<video_tools>` 与 `<项目>` 分别替换为仓库、视频项目的绝对路径。方括号表示可选部分，执行前展开或省略。

```bash
cd "<video_tools>"
# vtx 主链完成、<项目>/out/final.mp4 就位后
env python3 apps/publish/export_4k.py -t "<项目>/timeline.json" [--media "<项目>/out/final.mp4"] -o "<项目>/out/final_4k.mp4"
bash apps/publish/check_4k.sh "<项目>/out/final_4k.mp4"          # 期望帧数可自动从 timeline.json 推导
env python3 apps/publish/compare_4k_quality.py "<项目>/out/final_4k.mp4" "<项目>/out/final_4k_x264_ref.mp4"
# 本 app 测试（随迁件；NVENC 集成用例无 GPU 自动 skip）
cd apps/publish && env python3 -m unittest discover -s tests -t . -v
# repo 根全量（含本 app）
cd "<video_tools>"
bash scripts/run_all_tests.sh
```
