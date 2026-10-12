# F1 修复报告：4K 导出码率解析与无音轨前置门

## 结论

R1-M1、R2-F1、R2-F2 已修复，要求的单测、双链拒绝实测、正常 NVENC 回归、规格断言和静态检查均通过。仅修改发布应用范围；保留接棒前已有质量模式与双时基修复，未改版本三源或 docs，未 commit，未引入第三方依赖。

## 问题 → 修复

| 问题 | 修复 |
| --- | --- |
| R1-M1：`20m` 被 FFmpeg 当 milli；Unicode 数字通过正则但 FFmpeg 拒绝；极小值使 VBV 归零 | [export_4k.py](<apps/publish/export_4k.py>) 的 `parse_maxrate` 改为 ASCII `[0-9]` 十进制语法，`k/K→k`、`m/M→M`；按倍率换算后拒绝非有限值和小于 1000 bit/s 的值，过小报中文 `ArgumentTypeError`；返回后缀规范化字符串。两链 `maxrate/bufsize` 共用此解析结果。 |
| R2-F1：x264 对无音轨母带只导视频仍 rc0 | `export()` 在母带存在性检查之后、帧数/帧率探测和编码器选择之前，统一调用 `ffprobe -select_streams a -show_entries stream=codec_type -of csv=p=0`。无音轨即 stderr 中文路径/原因/建议并 exit 2；回落命令额外使用非可选 `-map 0:v:0 -map 0:a`，与 NVENC 链同样强制音轨直通。 |
| R2-F2：NVENC 无音轨直到 concat 才出现英文错误 | 同一入口门提前拦截，不创建段缓存、不启动编码器、不输出假成功。 |

统一错误文案：

```text
✗ 母带无音轨，4K 投稿版需音轨直通：<母带路径>；请检查 vtx mux 阶段产物
```

## 单测证据

[测试文件](<apps/publish/tests/test_export_4k.py>) 新增四个用例方法：码率合法表、非法表、两链无音轨入口拒绝、音轨存在探测。原 fps 前置门合成素材补音轨，避免新门抢先拒绝而削弱既有 fps 测试；旧回落 mock 补音轨返回，断言回落流映射。

必验参数表：

| 输入 | 结果 |
| --- | --- |
| `20m` | `20M`，两链 `maxrate/bufsize` 一致 |
| `20M` | `20M` |
| `1000k` | `1000k` |
| `２０M`、`-5M`、`20M;rm` | `ArgumentTypeError` |
| `0.00001` | `ArgumentTypeError`，含「过小」 |

另覆盖 `K→k`、1000 bit/s 边界、低于边界、零、科学记数法、NaN/inf 和数值溢出。无音轨 mock 断言非零 `SystemExit`、路径与完整中文文案、编码器探测未调用、缓存/输出不存在。

执行：

```bash
(cd apps/publish && env python3 -m unittest discover -s tests -t .)
```

两次全套均 rc0；留证轮 **23 tests / 5.335s / OK，无 skip**，包含真实 NVENC 小规模全链、缓存命中和符号链接隔离集成。[原始单测日志](<apps/publish/cache/reports/f1/unittest.log>)（预期负例 stderr 不代表测试失败）。

## 实测证据

所有新增现场产物写入发布应用的缓存证据区；该区被仓库 `cache/` 忽略规则排除，不入源码提交。以下是已执行的写态测试命令，不是零写态操作示范。

### 1. 无音轨母带，两条链统一拒绝

在应用证据区自造 3 秒 / 25fps / 75 帧无音轨素材，timeline 内容参考既有 R2 夹具。未覆盖 R2 现场。

```bash
E="$PWD/apps/publish/cache/reports/f1"
ffmpeg -v error -y -f lavfi -i 'testsrc2=s=320x180:r=25:d=3' \
  -c:v libx264 -crf 18 -pix_fmt yuv420p -an "$E/noaudio/master.mp4"
env VTX_NO_NVENC=1 python3 apps/publish/export_4k.py \
  -t "$E/noaudio/timeline.json" --media "$E/noaudio/master.mp4" \
  -o "$E/noaudio/disabled_4k.mp4"
env -u VTX_NO_NVENC python3 apps/publish/export_4k.py \
  -t "$E/noaudio/timeline.json" --media "$E/noaudio/master.mp4" \
  -o "$E/noaudio/default_4k.mp4"
```

两入口均 **rc=2**，各只有一行统一中文错误；均无输出文件。证据：[禁 NVENC 日志](<apps/publish/cache/reports/f1/noaudio/disabled.log>)、[默认入口日志](<apps/publish/cache/reports/f1/noaudio/default.log>)、[退出码](<apps/publish/cache/reports/f1/noaudio/result.log>)。

### 2. 正常母带 NVENC 真编与规格回归

复用 [demo 母带](</tmp/framivra_demo/out/final.mp4>)，探测确认 900f、30fps、AAC 48kHz、192102 bit/s；隔离 timeline 和所有段/输出位于应用缓存证据区，不写 demo 项目。

```bash
E="$PWD/apps/publish/cache/reports/f1"
env -u VTX_NO_NVENC python3 apps/publish/export_4k.py \
  -t "$E/normal/timeline.json" --media /tmp/framivra_demo/out/final.mp4 \
  -o "$E/normal/final_4k.mp4"
bash apps/publish/check_4k.sh "$E/normal/final_4k.mp4" 900
```

导出 **rc0，NVENC p4 ×8 段，冷编命中 0，900 帧自检通过，墙钟 5.6s**。[导出日志](<apps/publish/cache/reports/f1/normal/export.log>)。

[规格断言日志](<apps/publish/cache/reports/f1/normal/check_4k.log>)：**rc0，PASS=14 FAIL=0**；3840×2160、r/avg fps 均 30/1、H.264 High、yuv420p、文件总码率 19.47Mbps、时长 30.000000s、900 帧、AAC 48k、音频 192102bps、最大 I 帧间隔 113、faststart 全通过。

### 3. CLI 与静态检查

```bash
env python3 apps/publish/export_4k.py --help
bash -n apps/publish/check_4k.sh
env python3 -m py_compile apps/publish/export_4k.py \
  apps/publish/compare_4k_quality.py apps/publish/tests/test_export_4k.py
git diff --check
```

全部 **rc0**；[帮助日志](<apps/publish/cache/reports/f1/help.log>)、[静态检查退出码日志](<apps/publish/cache/reports/f1/static.log>)。`bash -n`、`py_compile`、`git diff --check` 均无诊断。

## 边界

本棒解决音轨存在性与 maxrate 参数安全，不更改投稿规格、音频编码/响度门或既有 CQ18/CRF18、VBV 与 `fps*512` 双时基策略。正常 x264 回落参数/映射由单测覆盖；要求的正常实编回归使用真实 NVENC，未用 mock 替代。
