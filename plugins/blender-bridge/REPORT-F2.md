# REPORT-F2 — Blender bridge 红队修复与真机验收

## 结论

M2（陈旧/损坏缓存复用）、M3（timeline symlink 跨项目缓存错位）、M5（过滤吞真实异常、stderr 噪声遮蔽 stdout 错误）均已修复。插件 **38/38**、应用 **17/17** 单测全过；Blender 5.2.1 真机四场景通过；离线 doctor 全绿、全仓 `git diff --check` 干净。未 commit、未引入第三方依赖、未改版本三源或 docs 域。

## 改动

| 文件 | 内容 |
|---|---|
| [插件实现](<plugins/blender-bridge/__init__.py#L97-L235>) | 告警块识别、双路错误提取、ASS+project sidecar 键、ffprobe 有效性校验；生成成功且校验通过后才写键 |
| [subs_alpha](<apps/blender-bridge/subs_alpha.py#L13-L19>) | 唯一实现改动为 timeline 使用 `absolute()`，不跟随文件 symlink |
| [json2blend](<apps/blender-bridge/json2blend.py#L33-L46>) | 同款 timeline 路径单点修复，相对媒体/cache 均归链接所在项目 |
| [插件测试](<plugins/blender-bridge/tests/test_blender_bridge_plugin.py>) | 更新旧存在性跳过测试；增加文本/样式、四个 project 参数、坏 mov、缺/坏 sidecar、生成后坏 mov、symlink 与告警/真实错误新样本 |
| [应用测试](<apps/blender-bridge/tests/test_subs_alpha.py>) | 真实 ffmpeg 创建 B 字幕轨；B/timeline → A/timeline 时只读取 B 的 ASS、只写 B/cache，并让真实 Blender 从 B 导入字幕轨 |
| [README](<plugins/blender-bridge/README.md>) | 同步缓存复用与错误呈现的新语义 |

### M2

- 键保存于 [subs_alpha.key.json](</tmp/framivra_demo/cache/subs_alpha.key.json>)：ASS 原始字节 SHA1（文本、字体、样式都覆盖）+ project 的 width/height/fps/duration_frames。
- 无键、坏键、键不匹配都重建；匹配时仍须 ffprobe 成功，有视频流及有限正时长。ffprobe 失败、缺工具、超时、元数据错误均不视为有效缓存。
- 新生成的 mov 也必须通过校验；exit 0 但缺产物/坏产物不会写成功键。无字幕保留零行为。
- 不引入跨模块缓存框架；应用仍是纯 python3/ffmpeg 脚本。

### M3

插件、subs_alpha、json2blend 统一以 timeline 文件自身绝对路径的父目录作为项目根，不 resolve timeline 文件链接。应用单测中 A 无 ASS、B 有 ASS：若穿透即会失败；实际 B 生成有效 ProRes alpha 视频且 Blender 导入字幕成功，A/cache 始终不存在。

### M5

- 只把非缩进行 `path:line: DeprecationWarning:` 识别为警告头，再过滤其连续缩进上下文；不再按裸子串删行。
- traceback 的 `raise RuntimeError("…DeprecationWarning…")` 和异常原因完整保留。
- 两路输出均过滤和计数。优先收集 `[json2blend] 错误` / `[json2blend] !` 标记及错误块内 `!` 行，再附两路各三行尾部；其他警告如 UserWarning 保留，但不会遮蔽首展真实错误。
- 非零退出码仍原样展示；没有改变 Blender 命令或成功/失败退出码判定。

## 验证证据

所有证据在 `/tmp/framivra_demo/cache/reports/evidence/f2/`。复用已有 1920×1080、30fps、900 帧项目，结束时已恢复原字幕并重新全链渲染。

### 单测与门禁

实际执行（两侧分别在自身目录运行）：

```bash
python3 -m unittest discover -s tests -t . -v
# plugins: Ran 38 tests / OK / exit 0
# apps:    Ran 17 tests / OK / exit 0

env VTX_DOCTOR_OFFLINE=1 python3 vt.py doctor
# doctor 结论：✅ 通过 / exit 0

git diff --check
# 无输出 / exit 0
```

证据：[插件单测](</tmp/framivra_demo/cache/reports/evidence/f2/plugins-unittest-final.log>)、[应用单测](</tmp/framivra_demo/cache/reports/evidence/f2/apps-unittest-final.log>)、[离线 doctor](</tmp/framivra_demo/cache/reports/evidence/f2/offline-doctor-final.log>)、[diff 检查](</tmp/framivra_demo/cache/reports/evidence/f2/diff-check-final.log>)。

尾随空格：接棒时全仓 diff 检查实际只剩插件测试第 13 行（其他并行棒已有修复）；本棒修掉该行，插件域尾空格扫描无匹配，全仓检查干净。没有为了追齐“13 行”去修改域外文件。

### a. 缺 mov 冷启动

确认绝对删除目标后删透明轨，再执行 `python3 vtx/vtx.py -t /tmp/framivra_demo/timeline.json all`：exit 0、QC 9/9、自动生成 28,886,970 B mov、成功导出审片工程并写键。

- [冷跑日志](</tmp/framivra_demo/cache/reports/evidence/f2/a-cold.log>)
- [冷跑键/mtime/大小](</tmp/framivra_demo/cache/reports/evidence/f2/a-cold-state.json>)
- ASS SHA1：`01947ecc34246c7f2dc11534e9202050ff008299`
- mov mtime_ns：`1791733645133171894`

### b. 改字幕自动失效

字幕改为“F2 字幕键更新验证”；先运行 diff、validate、subs check、f30 字幕预览、0–4 秒 draft 区间，再 all。validate/字幕检查/预览/区间/all 均 exit 0。diff 正常报告一条文字差异、按自身契约返回 1；首个验证脚本误把该返回值当失败而停，纠正判断后继续，非程序回归。

- [字幕重建日志](</tmp/framivra_demo/cache/reports/evidence/f2/b-stale.log>)、[新键/mtime/大小](</tmp/framivra_demo/cache/reports/evidence/f2/b-stale-state.json>)
- ASS SHA1 变为 `60fcdab93f344a593769b77125cb24ac5b244c34`
- mov 增至 **30,167,350 B**，mtime_ns 前进至 `1791733732419894208`
- all exit 0、QC 9/9；日志明确再次自动生成。

### c. 截断垃圾自动修复

同名 mov 覆盖为 1024 B `X`；真实 ffprobe 失败。键仍匹配，重跑 all 仍识别损坏并自动重建：exit 0、QC 9/9，重建到 30,167,350 B，mtime_ns 前进至 `1791733802508671233`，键保持 b 的新值。随后直接再跑 ensure，mtime 不变（有效缓存幂等复用）。

证据：[损坏 probe](</tmp/framivra_demo/cache/reports/evidence/f2/c-corrupt-probe.log>)、[自动修复](</tmp/framivra_demo/cache/reports/evidence/f2/c-repaired.log>)、[修复状态](</tmp/framivra_demo/cache/reports/evidence/f2/c-repaired-state.json>)。

### d. 真实 Blender 错误/traceback

1. 临时 timeline 填非法 source 路径，调用真实 json2blend。返回 **exit 1**，首展 `[json2blend] 错误 3 条`，保留音频/视频源缺失具体原因，附 **已滤除 8 行**；无裸弃用警告块。[错误证据](</tmp/framivra_demo/cache/reports/evidence/f2/d-invalid.log>)。
2. 真机 wrapper 先执行 json2blend，再抛 `RuntimeError("F2 真实原因 DeprecationWarning 不得被滤")`，打印 traceback 并显式 `sys.exit(37)`。返回 **exit 37**，完整保留 `raise RuntimeError(...)` 源码行及 `RuntimeError: …` 原因，附 **已滤除 24 行**。[traceback 证据](</tmp/framivra_demo/cache/reports/evidence/f2/d-traceback-final.log>)、[注入脚本](</tmp/framivra_demo/cache/reports/evidence/f2/d-traceback.py>)。

边界（按任务要求未改退出码语义）：Blender 默认未配置 `--python-exit-code` 时，未捕获 RuntimeError 本身会打印 traceback 但 **进程 exit 0**；已用真实 `--python-expr` 证实，[证据](</tmp/framivra_demo/cache/reports/evidence/f2/d-default-exit.log>)。本棒测试用明确非零退出验证失败信息，不声称修复此独立既有成功/失败判定问题。若后续要求“任意 Python 异常都 fail-hard”，需另审是否增加该 Blender 参数。

### 现场恢复

[恢复全链日志](</tmp/framivra_demo/cache/reports/evidence/f2/restore-all.log>)：原 timeline 字节恢复一致，原字幕 validate/check/preview/range/all 全过，QC 9/9；透明轨键恢复为 a 的原始键，项目正式产物与原配置一致。最终实现上又跑一次[温态全链](</tmp/framivra_demo/cache/reports/evidence/f2/final-warm-all.log>)：exit 0、QC 9/9、成功导出，字幕透明轨没有重复生成，mtime_ns 保持 `1791733855669502111`。
