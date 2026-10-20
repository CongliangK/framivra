# 达芬奇命令行驱动 AI 抠像流水线（Linux / Resolve 21 Studio）

用 CLI + 少量固定坐标鼠标操作，驱动 DaVinci Resolve 的 Magic Mask（AI 神奇遮罩）
对多轨真人视频批量抠像，并逐轨导出带阿尔法通道的 ProRes 4444。
替代 BiRefNet/MatAnyone 自研链路（质量差、慢）。

## 一键流水线

```bash
env bash matte_pipeline.sh matte_auto_v9          # 完整：清显存→建工程→抠像→导出
SKIP_GUARD=1 env bash matte_pipeline.sh proj      # 达芬奇已就绪，跳过重启
SKIP_OPS=1    env bash matte_pipeline.sh proj     # 仅跳过鼠标序列，仍建工程/导入并导出
```

视频目录按非空值优先级选择：第二个 CLI 参数 > `VIDEOS` > `MATTE_BASE` > HOME 下原 `centered9` 默认目录；空参数或空环境变量回退到下一项。

阶段明细：

| 阶段 | 脚本 | 作用 |
|---|---|---|
| 1 显存护栏 | `vram_guard.py --project NAME [--force]` | 杀达芬奇→等显存≥15G（下限13G放行）→重启→恢复工程/时间线/调色页 |
| 2 建工程 | `prep.py NAME` | 29.97fps + 2048x1152 → 导 9 视频 → grid9 时间线 9 轨对齐 → 开调色页 |
| 3 抠像 | `gen_sequence.py` + `ops_runner.py op_sequence.json` | 137 步鼠标序列：选相册→逐卡（右键模板静帧→应用→演算→等日志） |
| 4 等待 | `wait_tracking_done.py --timeout 900 --stable 20` | 监听 ResolveDebug.txt，**多段稳定判定**（见坑 3.1） |
| 5 导出 | `export_alpha.py NAME [outdir]` | 逐轨 ProRes4444 + alpha，ffprobe 帧数/透明度校验 |

辅助工具：`record_clicks.py`（内核级点击录制，Ctrl+C 结束）、`ops_runner.py --dry/--from/--to`（序列回放）、`mouse_ctl.py`（xdotool 封装，窗口相对坐标）、`resolve_ctl.py`（连接/自动启动/健壮建工程）。

## 坐标系（重要）

- 序列里的坐标是**达芬奇窗口相对坐标**；窗口位于 (0,64)，4608x2528。
- 物理截图像素 → 窗口坐标：`win = 截图px × (3840/截图宽) ÷ 1.2`。
- **xdotool 写入单位 ≠ 读回单位**（XWayland 缩放）：`mouse_ctl._pointer_write_scale()`
  开机自动标定（写 500,400 读回算比例）。若点击落点漂移，先重启标定。
- 固定卡位点 `card_points.json`（15 点，用户人工验证）。**卡片条按时长升序、
  标签乱序**，实测映射：卡1→V1 卡2→V5 卡3→V2 卡4→V3 卡5→V9 卡6→V4 卡7→V8 卡8→V6 卡9→V7。
  换时间线布局必须重新核对（点卡后看 MagicMask 日志里的 "Source frame range" 帧数）。

## 各轨片段帧数（centered9 素材）

V1=1051 V2=1396 V3=1711 V4=2161 V5=1216 V6=2746 V7=3061 V8=2536 V9=1876

## 踩坑实录（每条都真实付出过代价）

### 1. 脚本 API（v21）
- `LoadProject`（无 OpenProject）；无 `DeleteTimeline`；`CreateTimelineFromClips` 不可调用。
- `ImportMedia` 必须传列表 `[path]`；`CreateEmptyTimeline` 重名返回 None。
- `AppendToTimeline` 锚定在 V1 已有内容末尾 → **追加顺序 V2..V9 先、V1 最后**才能对齐。
- 帧率建工程后很快锁死：**CreateProject 后立即** `SetSetting("timelineFrameRate","29.97")`，
  否则 24fps 默认值会把 35s 素材压成 841 帧。
- `GetRenderCodecs("QuickTime")` 返回空，要用扩展名 `GetRenderCodecs("mov")`；
  `SetCurrentRenderFormatAndCodec("mov", "ProRes4444")`（格式字段是扩展名！）。
- `SetTrackEnable` **在交付页调用返回 True 但不生效**，必须先 `OpenPage("edit")` 切完轨道
  再回交付页渲染。`GetIsTrackEnabled` 读回也不可靠，以渲染结果为准。
- `pgrep -x resolve` 匹配不到进程名，探测进程要用 `pgrep -f /opt/resolve/bin/resolve`。
- **ApplyGradeFromDRX 应用含 Magic Mask 的 DRX 必崩**（Signal 11, fusionscript.so）；
  Fusion 页 MagicMask 必爆显存。所以模板应用走调色页 GUI（PowerGrade 共享静帧）。
- 脚本 API 对象会"退化"（方法变 None）——达芬奇 UI 线程半死的前兆，重启达芬奇解决。

### 2. 渲染导出
- **达芬奇只能渲染到家目录下的文件夹**，写 /tmp 会静默产出 1 帧或直接失败。
- **每次渲染必须单独设 in/out**：`MarkIn=108000`（时间线起始时码 01:00:00:00 的帧
  基准）、`MarkOut=108000+帧数-1`，且 `"SelectAllFrames": False`。否则输出长度=最长轨。
- 阿尔法三件套：`ExportAlpha: True` + `AlphaMode: 1` + 编码 ProRes4444（422HQ 无阿尔法，
  会得到 99.8% 不透明的废片）；调色页节点必须手动接**阿尔法输出**（模板里已含）。
- 校验三查：ffprobe 帧数逐轨核对、alpha 平面 YMIN/YMAX（12bit，4095 满）、
  **末尾 2s alphaMax>0**（防止轨道没切对渲染了别的轨）。

### 3. Magic Mask 演算行为（最大坑）
- **演算分正向+反向多段**：第一段 "Tracking has been completed" 之后立即可能跟着新
  "Start tracking"。`wait_tracking_done.py` 必须等"completed 后 20s 无新段"。
- **演算期间达芬奇抓住指针**：所有 XTEST 合成点击失效，指针"冻结"在演算按钮坐标。
  表现为鼠标彻底失灵、截图正常、渲染正常。**等待期间禁止任何鼠标操作**。
  （若冻结不恢复：等演算全部结束后一般自动释放；紧急情况重启达芬奇。）
- 完成信号看日志 `~/.local/share/DaVinciResolve/logs/ResolveDebug.txt`：
  `[MagicMaskTracker]: Start tracking`（含分辨率与帧范围，可核对是否选对了片段）
  / `Tracking has been completed` / `No more source frame to track`。
  日志在达芬奇重启后轮转，需要留存请提前拷贝。
- 速度：2048x1152 正常 ~30-34fps；长素材反向段可能掉速（2160 帧曾花 10 分钟），
  机制未明（显存 5GB/16GB 时也复现，不纯是爆显存）。降码率无效。

### 4. 鼠标/输入基建
- xdotool 合成点击对 Resolve（XWayland/Qt）有效，但要用 mousedown+延时+mouseup。
- 内核级 `record_clicks.py`（/dev/input + select 多路复用）录制人工操作 → my_ops.json
  → 序列化到 op_sequence.json。
- 系统级输入失效排查经验：xdotool 写坐标读回恒定不动 = 指针被抓/冻结；
  uinput 键盘注入（`~/.agents/skills/screenshot` 的 keyinject）可验证内核通路。
- 本机 quirk：ZCode AppImage 劫持 argv[0]，跑脚本用 `env` 前缀（`env python3/gcc`）。

### 5. 显存管理
- 目标：启动达芬奇前空闲 ≥15GB（下限 13GB 放行，桌面基线约 1.8GB 常驻）。
- 整个工程一轮清理即可，**不要逐轨重启**（用户明确要求）。
- 可安全清理白名单：showtime（GNOME 视频播放器）、firefox。

## 目录产物

- `out/alpha_v8/matte_V{1..9}.mov` —— matte_auto_v8 成品：prores yuva444p12le，
  帧数逐轨核对通过，alpha [256,3773]/4095，尾部有内容，逐帧 MD5 互异。
- `RUNLOG_v8.md` —— 2026-09-12/13 全流程首次跑通记录。
- `my_ops.json` / `card_points.json` —— 人工操作录制与验证过的卡位点。
- `step*.py`、`bench/` —— 历史探针与码率/分辨率基准素材。
