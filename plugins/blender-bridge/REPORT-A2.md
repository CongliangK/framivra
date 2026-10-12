# REPORT-A2 —— blender-bridge 缺陷1/缺陷2 修复报告

> 子智能体 A2 · 2026-10-11 · 写域：`apps/blender-bridge/` + `plugins/blender-bridge/`
> （代码+tests+README）。未触碰版本三源 / docs/ / skill/ / 其他目录，未 git commit，
> 未引入第三方 pip 依赖。

## 1. 改动清单

| 文件 | 类型 | 内容 |
|---|---|---|
| `plugins/blender-bridge/__init__.py` | 改 | ① 新增 `_ensure_subs_alpha(c)`：字幕透明轨前置保障（缺陷1）；② 新增 `_filter_deprecation(lines)` + `_error_tail(stderr, stdout)` 并改写 `_run_blender_script` 失败文本组装（缺陷2）；③ `finish()` 在 json2blend 导出前接入 ensure；④ 模块 docstring 补两修复说明；⑤ 新增常量 `_SUBS_ALPHA`、`import sys` |
| `plugins/blender-bridge/tests/test_blender_bridge_plugin.py` | 改 | 新增两组单测：`DeprecationFilter`（6 例，样本含 Blender 5.2.1 实测告警块+traceback+真实错误）与 `SubsAlphaAutogen`（7 例，mock subprocess） |
| `apps/blender-bridge/tests/test_subs_alpha.py` | 新 | subs_alpha.py 契约锁：缺 subs.ass → exit 非零+中文指引；成功路径产出 ProRes 4444 alpha 轨（ffmpeg 存在时） |
| `plugins/blender-bridge/README.md` | 改 | 用法节补两条修复行为说明；测试节更新 |

apps 侧代码（json2blend.py / subs_alpha.py / verify_blend.py）**零改动**——两缺陷根因都在插件收尾钩子的编排与错误呈现层。

## 2. 根因

**缺陷1（收尾钩子首跑必失败）**：timeline 顶层 `"blender_bridge": true` 时 `vtx all`
的缝 D finish 钩子直接调 json2blend.py 导出 preview.blend；而 json2blend 对有字幕的
timeline 硬依赖 `<项目>/cache/subs_alpha.mov`（L267-275：缺失记 error → L286
`sys.exit(1)`）。该文件只由 `apps/blender-bridge/subs_alpha.py`（纯 python3，手动跑）
产出，**不在 `vtx all` 管线产物集内**（管线只生成 cache/subs.ass）——首跑必缺 →
json2blend exit 1 → 收尾钩子失败、`vtx all` 退出码 1。

**缺陷2（告警混入错误通道）**：`_run_blender_script` 失败时取
`(r.stderr or r.stdout).strip().splitlines()[-3:]`。Blender 5.2.1 把 VSE 弃用点告警
（`'ImageStrip.frame_final_end' is expected to be removed in Blender 6.0` 等，
json2blend 设 `frame_final_*` 时触发）打到 **stderr**，而 json2blend 的真实 `!` 错误行
打到 **stdout**——stderr 非空则永不回落 stdout，尾 3 行恰是告警块（告警行+缩进源码
上下文行），真实错误被完全淹没（实测复现见 §4 修复前输出：整条错误信息全是
DeprecationWarning，无一行真实原因）。

## 3. 方案

**缺陷1** —— `finish()` 在 json2blend 导出前调 `_ensure_subs_alpha(c)`：

- 门控与 json2blend 同构：仅 `c.tl["subtitles"]` 非空时需要透明轨；无字幕零行为。
- `cache/subs_alpha.mov` 已在盘 → 幂等跳过（不重复生成，二次跑零成本）。
- 缺失但 `cache/subs.ass` 在盘 → 自动执行
  `[sys.executable, <仓>/apps/blender-bridge/subs_alpha.py, <timeline.json 绝对路径>]`
  （纯 python3 脚本、无 bpy，用当前解释器调用；`_SUBS_ALPHA` 由 `__file__` 推导，
  独立于 cwd；subprocess 列表参数 + pathlib，Windows 路径安全）。exit 0 且产物落盘
  才算成功；失败 → 错误行含完整命令 + 尾 3 行原因（不吞、不静默）。
- `cache/subs.ass` 也缺 → 不起子进程，直接报中文指引：
  「先跑 vtx 字幕阶段：`vtx -t <timeline> subs render` 生成 subs.ass，再重跑 vtx all」。
- blender 不在 PATH 的 fail-hard 语义保持在其原有检查位（ensure 排在其后，不吞不抢）。

**缺陷2** —— `_run_blender_script` 失败文本组装改为：

1. `_filter_deprecation(lines)` 行级状态机：剔除含 `DeprecationWarning` 的告警行**及
   其后缩进的上下文行**（吞到非缩进/空行为止；真实 traceback 恒有非缩进的
   `Traceback (most recent call last):` 头隔开，不会误吞）；返回 (保留行, 滤除行数)。
2. `_error_tail`：stderr/stdout 两路都先滤告警；滤后 stderr 仍有实质行取 stderr，
   否则回落 stdout（真实 `!` 错误行所在），回落时剔除 Blender 版本横幅/`Blender quit`
   噪声行；取尾 3 行 ` | ` 拼接。
3. 滤除计数聚合为一行提示追加在错误文本尾：`（已滤除 N 行 Blender
   DeprecationWarning）`；**子进程退出码原样进消息（`exit {rc}`），语义不变**。

## 4. 验证证据（全部实际执行，本机 blender 5.2.1 = /home/liangruochong/.local/bin/blender）

演示项目按协议自 `/tmp/framivra_demo` 重建：`/tmp/a2_demo`（timeline.json +
timeline.schema.json + gfx/ + assets/，timeline 顶层 `"blender_bridge": true`，字幕
`[{"in":12,"out":88,"text":"示例字幕"}]`，900 帧 1920x1080）。

**(0) 修复前冷跑复现**（`rm -f cache/subs_alpha.mov && env python3 vtx/vtx.py -t
/tmp/a2_demo/timeline.json all`，日志 `/tmp/a2_cold_run.log`）：

```
QC：9/9 通过
  ✗ json2blend 导出 失败（exit 1）：  strip.frame_final_start = clip["dst_in"] | …json2blend.py:174:
    DeprecationWarning: 'ImageStrip.frame_final_end' is expected to be removed in Blender 6.0 |
    strip.frame_final_end = clip["dst_in"] + clip["dur"]（完整命令：…）
✗ finish 钩子失败（正式产物已就绪；修复后重跑对应插件动作或 `vtx all`）
EXIT=1        ← 两缺陷同框：首跑失败 + 错误文本全是告警、真实错误（缺少字幕透明轨）不可见
```

**(1) 修复后冷跑**（同命令，subs_alpha.mov 已删，日志 `/tmp/a2_cold_fixed.log`）：

```
  · blender-bridge: cache/subs_alpha.mov 缺失，自动生成（subs_alpha.py）
✓ blender-bridge：已生成 cache/subs_alpha.mov（28886970 B，字幕透明轨）
  · blender-bridge: 导出 preview.blend（json2blend，blender --background）
✓ blender-bridge：已导出 preview.blend（93732 B，审片工程）
完成： /tmp/a2_demo/out/final.mp4      EXIT=0   ← 首次即成功
```

**(2) 二次跑**（subs_alpha.mov 已在盘，日志 `/tmp/a2_second_run.log`）：EXIT=0；
subs_alpha.mov mtime/size 逐字节不变（stat 前后 diff 为空）；日志中「自动生成」出现
0 次（幂等跳过）；preview.blend 正常重导出。

**(3) 篡改场景**（均非零退出、无告警噪声）：

- 删 `cache/subs.ass` + `subs_alpha.mov` 后 `vtx all`：video 合成步自愈重造 subs.ass
  → 收尾自动生成成功，EXIT=0（管线韧性的正向旁证，`/tmp/a2_tamper_ass.log`）。
- 直接调 finish 钩子且两者皆缺 → `缺少 /tmp/a2_demo/cache/subs.ass（字幕透明轨
  subs_alpha.mov 的源）——先跑 vtx 字幕阶段：vtx -t /tmp/a2_demo/timeline.json subs
  render 生成 subs.ass，再重跑 vtx all`，exit 1（指引明确，不含任何告警行）。
- 隐走视频源（json2blend 真实错误 + stderr 满载弃用告警，真 blender 子进程）→
  `json2blend 导出 失败（exit 1）：! 音频源缺失 v01: … | ! 视频源缺失 aroll|0: …
  （已滤除 8 行 Blender DeprecationWarning）（完整命令：…）`，exit 1——真实错误
  可见、告警仅存计数。
- timeline.json 改名后 `vtx all`：`timeline 不存在: …`，exit 1（干净早失败）。

**(4) 单测**：

```
cd apps/blender-bridge     && env python3 -m unittest discover -s tests -t .   → Ran 16 tests OK
cd plugins/blender-bridge  && env python3 -m unittest discover -s tests -t .   → Ran 25 tests OK
```

新增用例覆盖：告警块+traceback+真实错误三态过滤断言（含多行上下文、无告警恒等）、
stdout 回落/横幅剔除、`_run_blender_script` 集成（退出码语义断言）；ensure 的无字幕/
已存在幂等/缺 ass 指引/脚本缺失/成功命令组装（`sys.executable` + `__file__` 推导路径 +
绝对化 timeline）/失败含完整命令与原因/exit 0 未落盘不吞。另跑内核相关面
`tests.test_protocol_v12 + tests.test_pluginlib`（54 tests OK）无回归。

## 5. 风险与边界

- **跳过式幂等**：subs_alpha.mov 已在盘即跳过，不比对 subs.ass 新旧——字幕内容变更
  但透明轨残留旧版时 .blend 带旧字幕（任务允许「不重复生成或幂等重生成」二选一；
  删 cache/subs_alpha.mov 或 `vtx clean` 后自动重生）。后续如需精确失效可升级为
  ass→mov 内容键联动（超出本缺陷范围）。
- **gc 视角**：subs_alpha.mov 是固定名文件、非内容寻址键，`vtx gc` 可能回收——本修复
  的自动生成链路恰好兜底（回收后下次 `vtx all` 重生），不构成失败态。
- **过滤面**：`_filter_deprecation` 按 `DeprecationWarning` 子串匹配——若未来某真实
  错误行本身含该词（如用户以 `-W error` 把告警升格为异常），异常行会被滤除、仅剩
  计数提示与 traceback 头；Blender 5.2.1 默认配置无此形态（实测），升格场景属
  json2blend 显式改造，届时需同步调整。
- **版本号未 bump**：`VERSION`/`plugin.json`/`registry.json` 维持 1.0.0——registry 与
  版本三源均在本任务写域外，且 plugin_code_digest 已随代码变更自动翻转回执指纹；
  若消费面要求升版需另走 registry 变更流程。
- **跨平台**：路径全部 pathlib；python3 解释器取 `sys.executable`（回落
  `shutil.which("python3")`/`"python3"`）；subprocess 列表参数由标准库处理引号，
  Windows 空格路径安全。blender 缺失时原报错原位保留（未动）。
