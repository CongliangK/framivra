# 剪辑（edit）· 中间层工具站：timeline.json + vtx
> **使用者向**：timeline.json 契约、vtx CLI 用法、音频工程规则与渲染坑表。`<video_tools>` 为本仓真实根目录；性能基准见 `<video_tools>/docs/DEV.md`，HTML 动效设计见 [gfx](gfx.md)，选题调研见 `<video_tools>/templates/tech-talk/references/research.md`，索引见本 skill 的 SKILL.md。
## 铁律
1. **AI 永远不手写 ffmpeg 剪辑命令**。剪辑 = 编写/修改 timeline.json + 调用 vtx。失误率控制靠三道闸：渲染前校验（validate）、确定性构建（vtx 生成全部 ffmpeg）、渲染后 QC。
2. **修改 timeline.json 后必须重跑 validate**，校验失败时修数据、不绕过。
3. 所有时间量用**整数帧**；区间 `[in, out)` 左闭右开；项目时基 30fps（≠29.97）、48kHz 音频（每帧整 1600 采样）。
## 工具位置
```
vtx CLI：        ~/.agents/skills/video/vtx/vtx.py            （符号链接；权威本体=~/文档/01_活跃项目/video_tools/vtx/；仅依赖 python3 标准库 + ffmpeg/ffprobe/google-chrome）
契约 schema：    ~/.agents/skills/video/vtx/timeline.schema.json
时间线预览器：    ~/.agents/skills/video/vtx/preview.html     （人审编排用，双击打开）
参考实现（POC）： ~/文档/04_bilibili视频/_archive/poc2/   （外部示例项目：30s 多轨 demo，QC 全过；不随 skill 分发）
```
程序本体在 video_tools 仓：`vtx/`、`apps/`、`plugins/` 的权威位置（命令归属见 `<video_tools>/docs/ARCHITECTURE.md` §6.1）以 `<video_tools>` 为准；live skill 通过六件链接引用本仓（见 `<video_tools>/docs/DOC-CHARTER.md` §7）；**修改程序一律改 video_tools 仓**。入口三通并存（分发见 `<video_tools>/docs/ARCHITECTURE.md` §5.2）：仓根 `vtx/vtx.py` 薄启动器、`python3 -m vtx`、pip console `vtx`/`vt`；实现分为 `infra`（Ctx/执行/并行）、`encoders`（NVENC/x264/libfdk 探测与封装）、`composite`（滤镜图构建）、`qc`、`stages`（各阶段编排）、`gfx_render`（CDP 引擎）、`hygiene`（缓存键/gc/df/snapshot/VERSIONS）、`cache_state`（回执身份/依赖门）八个模块；历史版本回滚锚点在 git 历史。入口自定位、无硬编码主目录路径；迁移=整个 video_tools 仓拷走 + 更新符号链接（前提：目标机器有 ffmpeg + 浏览器）。
## 时间线预览（人审用）
`preview.html` 是单文件网页（无外部依赖），把 timeline.json 渲染成可视化时间线供**人类**审阅编排：视频轨/动效层/字幕/音轨分行显示，事件块带源名、时长、vol/pan/loop 与淡入淡出帧数标注；支持缩放、适应宽度、悬停播放头、点击事件块看全字段详情，并有**预览级校验**（整数帧、EDL 不变量、同轨视频片段重叠、越界、同轨音频重叠等）——发现问题标红，但最终以 `vtx validate` 为准。
```bash
xdg-open ~/.agents/skills/video/vtx/preview.html
# 打开后：内置 poc2 示例自动载入；「打开 timeline.json」选文件、拖拽文件到页面、或「粘贴 JSON」均可替换
```
使用时机：**编排完 timeline.json → 先开预览器人工过一遍 → 再跑 validate/渲染**；用户反馈修改意见时，预览器是双方看同一份编排的共同语言。
## 典型流程
```bash
# 新建视频项目（生成目录 + 模板 timeline.json）
bash ~/.agents/skills/video/scripts/new_project.sh ~/文档/04_bilibili视频/myvideo
# 编排：编辑 myvideo/timeline.json（契约见下），素材放 myvideo/assets/，动效页放 myvideo/gfx/
cd ~/文档/04_bilibili视频
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json validate   # 渲染前校验门（必跑）
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json --dry-run video  # 只打印将执行的 ffmpeg
#   `--dry-run` 现已全阶段安全（gfx/qc/audit 均只预览不落副作用；cache 侧段字幕/清单为键保真的有意豁免）
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json all        # 全流程 → myvideo/out/final.mp4
# 分阶段（gfx/seg/audio 缓存全部内容寻址，源或参数一变自动换键重渲，无需手工清缓存）：
#   validate → audit（指标体检）→ prepare（提取音频）→ gfx（渲染动效）→ video → audio → mux → qc
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json subs check    # 字幕渲染前检查（秒级，零渲染）：对齐漂移/CPS/重叠/行宽；FAIL 退出码 1
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json subs preview 12.5 f360  # 字幕单帧预览图（秒级，改字幕抽查；支持多时间点）
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json subs render   # 独立产出 cache/subs.ass（字节一致自动跳过）
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json diff 旧.json out/qc_report.json  # 报告对比（qc/audit/subs_check；状态翻转显著标出，DIFF 退出码 1）
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json gc --dry    # 回收清单预览（收尾习惯）
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json gc          # 执行回收（键集外垃圾+tmp 孤儿）
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json gc --all    # 深度回收（保留 reports/VERSIONS/history/final）
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json df          # 体积体检（分项占用+top-20 大文件）
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json snapshot    # timeline+文本资产快照（自动留 2 份）
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json clean      # 清空 cache/ out/（慎用）
```
## 缓存卫生与体积治理
- **内容寻址缓存**：gfx、seg、音频与合成段分别按当前输入身份选键；声明依赖、渲染环境及各产物的具体键语义统一见 [缓存与回执](../../cache-provenance.md)。旧键由 gc 回收，不例行手工删除 `cache/seg/`。
- **`vtx gc`**：回收「当前 timeline 键集之外」的全部缓存条目 + tmp 孤儿（>24h）+ out 一次性中间物。`cache/reports/` 永不回收。`--keep-days N` 暂缓近 N 天条目。**收尾习惯：每轮「改 timeline→重渲」结束后先 `gc --dry` 看一眼再 `gc`**——cache 占用从此有上界（≈当前键集工作集），不再单调增长。
- **证据纪律（管线级约定）**：调试渲次（整片 mp4）一律写 `cache/tmp/<runid>/`，阶段结束即删（`hygiene.tmp_run` 上下文）；需留证一律抽帧（≤10 张）或 540p 小样 ≤10MB 存 `cache/reports/evidence/`。**报告/回执/JSON 一律 `cache/reports/`，gc 永不触碰**。**tmp 选址铁律：GB 级媒体中间物严禁写系统 /tmp**（本机 /tmp 是 tmpfs，写即占 RAM/swap 且会被定期清理，长任务文件可能中途消失）；≤数十 MB 小件可用 /tmp。
- **产物版本记账**：mux 每次成功追加 `out/VERSIONS.json`（md5/大小/timeline+audio md5）；**覆盖检测**——将覆盖的 final.mp4 与账面末条 md5 不符 → 显著告警 + 旧条目标 superseded。旧成片轮转 `out/history/`（硬链接优先），`--keep-versions N` 默认 2。
- **快照**：改 timeline 的批量脚本（retimeline 类）改调 `vtx snapshot`（timeline+全部引用文本资产，KB 级，自动留 2 份），不手写 `.bak`。
- **体积预算**：`VTX_CACHE_BUDGET_GB=80` 设预算后 gfx/video 阶段前超限自动跑 gc 清单提示（`VTX_GC_AUTO=1` 直接回收）；audit 报告含 volume 节（cache/out 占用+tmp 孤儿）。
**gfx 渲染引擎**：默认 CDP 常驻浏览器；页面协议、Canvas 与静态跨度见 [gfx](gfx.md)。CDP/CLI 各有独立环境身份，不将跨引擎像素或 PNG 字节相同当作普遍保证。CDP 自动回落 CLI 的页不能发布完成凭证；显式 `VTX_GFX=cli` 重跑使用独立身份，strict 依赖策略不支持 CLI。详细边界见 [渲染环境身份](../../cache-provenance.md#gfx-environment)。

**回执依赖门**：`out/receipt.json` 绑定当前输入；timeline、素材、HTML、声明依赖或生效渲染身份变化，不继承旧阶段成功。单阶段前置为 audio←prepare、video←gfx（有 gfx 轨时）、mux←video+audio、qc←mux。缓存仍可按内容键复用，但不能据此跳过阶段回执。正式交付用完整 `all`，包含启用插件的 finish；操作顺序见 [runbook](runbook.md)。QC 与 audit 报告分别在 `out/qc_report.json`、`out/audit_report.json`。
## audit 指标审计（建议性，不阻断渲染）
**validate 是机制硬门，audit 是内容体检**——审计出的 ⚠ 不拦截渲染，但发布前应逐条看过。检查项：画面构成占比与模块完整性（缺 broll/screen/gfx 提示）、黑场窗口、**无声窗口**（bed 轨应全程兜底）、口播覆盖率（主流观察 83-99.7%）、全片语速（**标准值 5.5 字/s（用户拍板引导值，=主流中位）；ok 区间 4.8~6.3**，明细在 `~/文档/04_bilibili视频/_library/corpus/叙事风格分析/data/speech_rate.json`）、字幕阅读速度（>7.5 字/s 偏快、>9 过快）/最短时长/相互重叠。基准值都注明在报告里，来源是本项目自己的 39 视频语料实测。
## 字幕预检与预览（subs check / preview，2026-09-12 方案四）
- **`subs check`（零渲染，timeline 层面可跑）**：①字幕窗 vs voice 轨口播窗对齐（重叠取最大重叠窗算起点偏移，无重叠取最近外沿——滞后窗口末端为正/超前窗口起点为负；FAIL ±1.5s / WARN ±0.5s，`--align-threshold` 可调；最近窗距离 >5s 或无 voice 轨 → WARN 不静默，报告 coverage 如实扣减）；②阅读速度 CPS >9 FAIL / >7.5 WARN / <0.6s WARN（与 audit 同基准）；③字幕时间重叠双标 FAIL；④行宽 28 字×2 行 FAIL（PlayResX 1920/Fontsize 58/MarginL-R 60 推算）。报告 `out/subs_check.json` + 人类可读表格，末行 PASS/FAIL（FAIL 退出码 1，可做 CI 门）。
- **`subs preview <秒|f帧号>...`**：单帧预览图 `out/subs_preview_f<N>.png`，单张 ≤5s。背景三级回退：qc_clean 提帧 → 图层单帧合成（素材不全自动跳过）→ 黑场兜底（WARN，仅审字幕位置/样式）。活跃集判定与真实烧录的 cs 量化逐位一致（`_fc` 同式），烧录用 `ass_head` 同源样式——预览与成片差异仅为成片有损编码噪声（红队 S7 复核口径：跨 ffmpeg 输入路径有 1px 级 AA 差，视觉无差）。
- **迭代回路**：改字幕 → `subs check` → `subs preview` → 准备当前 gfx 缓存并做区间音画预览 → 完整 `all`。字幕字节参与段键，失效范围由实际覆盖与切段边界决定；单跑正式 video 仍需当前 gfx 成功。配方见 [runbook R2s](runbook.md)。

## 音频工程（build_audio 内置）
- **音乐避让（duck）**：音轨声明 `"duck": true`（bed/amb 建议）即被 `"duck_by"`（缺省 `["voice"]`）轨的混音经 `sidechaincompress`（缺省 threshold=0.0126(-38dBFS) / ratio=4 / attack=20ms / release=1500ms / makeup=1，轨级 `duck_params` 可覆盖；2026-09-11 D47 voice-ime 实测校准）压低。**⚠ 新项目启用 duck 前必须先做「duck 开/关」A/B 响度对照，否则长节目下无口播段床乐可能反常抬高约 19dB**；床乐静态电平已足够低（低于口播 ≥5dB）时建议直接不开（破案详录见 DEV.md）。
- **响度归一（两遍静态增益）**：第一遍 ebur128 实测原始混音 I，第二遍施加恒定 `volume` 增益到 **-14 LUFS（EBU R128，B站标准）**；QC 阶段自动复测，按下方响度终链的同一分级判定。**⚠ 不要用单遍 `loudnorm`（dynamic 模式），长节目上时变增益会随内容游走致区域塌陷**。
- **响度终链（v2 §1.2，2026-09-13）**：alimiter 限幅（`limit=0.599:level=false:latency=true`）已并入 `vtx audio`（build_audio 尾部，与区间预览统一过 limiter）——out/audio.wav 恒为「归一+限幅」终态，**audio → mux 直连，无独立 post_loudness 步骤**（[既有 WAV 维护边界](#既有-wav-维护)见下方）。终态校验分级：硬 FAIL=TP>-1.0 dBFS/时长漂移 ±1 采样/I 偏离 >3 LU；WARN=I 偏离 ∈(1.5,3]（内容级，发布侧 G2 拦截），≤1.5 静默通过。**编码后解码 TP 终检纪律不变**（G3/qc）。
- 混音链：轨内子混音（apad 补齐成片全长）→ duck → 总混音（amix normalize=0 保留音量比例）→ 两遍静态增益 -14 LUFS + alimiter 限幅（v2 §1.2 响度终链已并入 vtx audio，无独立 post 步）→ 定长裁切。
- **BGM 分轨审计包（正式版默认必做）**：正式版本入时间线后，除 vtx 渲染外还须执行 [分轨审计](../../../apps/blender-bridge/README.md#分轨审计包-audit_bundlepy人机协作交付门)；分轨或音量改动后重跑。用户在 Blender VSE 逐轨静音/独听对比后参与混音与编排裁决（人机协作交付门；完整规程见 music skill「交付门：分轨审计包」节）。命令、产物、缓存与验证边界只在组件说明维护。

### 既有 WAV 维护

`scripts/audio/normalize_project_audio.py` 独立归一已有项目 `out/audio.wav`，不是 timeline 重渲，也不是正式链的追加步骤。参数为 `project`（默认当前目录）、`--target`、`--ffmpeg`，输出 JSON；源码函数为 `vtx.stages.normalize_project_audio`。只读查看：

```bash
env python3 scripts/audio/normalize_project_audio.py --help
```

实际执行是**写操作**：测量、限幅与时长检查后原子替换 WAV，不更新或撤销 audio/mux/qc 回执；重复施加增益不保证字节或响度不变。它不是交付、恢复或补签回执流程，不能用旧成功回执验收新 WAV。需要调整声音时修改 timeline，重跑完整 `vtx all` 并核对成片 QC 与项目内容门；阶段顺序见 [runbook](runbook.md)，终链合同见 [ARCHITECTURE §1.2](../../ARCHITECTURE.md)。

## 渲染速度：草稿 / GPU / 缓存
```bash
# 迭代预览（不碰正式产物）：540p + NVENC p4，跳过 qc_clean；改动集中片头时加 --frames
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json video --draft --frames 1800
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json mux --draft    # draft.mp4 + audio.wav → draft_final.mp4
# 正式 GPU 渲染：NVENC p7+tune hq+vbr cq17（1080p 交付透明，下游 4K 重编码无感）
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json video --gpu
```
头窗 draft 与正式 video 都需要当前 gfx 前置（有 gfx 轨时）；完整示例见 [runbook](runbook.md)。缓存命中与改动窗耗时分开记录，历史测量见 [DEV](../../DEV.md)，不承诺固定渲染时长。
## 坑表（vtx 已内置规避，手写 ffmpeg 时必看；⚠ 规则+后果，破案详录见 DEV.md）
- **⚠ `ffprobe -count_frames` 是逐帧解码计数，probe 一律用流头 nb_frames + count_packets 回落**，否则 validate 可能分钟级。
- **⚠ `-frames:v N` 会触发 filter_complex 全量 drain（所有输入解到 EOF）**，draft 截短须对每路输入加 `-t`。
- **⚠ NVENC 探测尺寸须 ≥145 宽**，过小报假失败，曾致草稿静默回落 x264——探测失败必须显式告警。
- **⚠ 正式 mux 用 libfdk_aac 192k**：原生 `aac` 对真人素材（密集瞬态）解码真峰过冲可达 +8.5dB——终检必测编码后解码 TP，不信 wav 域 TP；vtx mux 已自动探测 libfdk_aac 并在缺失时回落+告警。
- **⚠ qc_clean 由 video 阶段尾部生成**：渲染中断残留截断文件（moov 缺失）会使后续 qc 假失败——rm cache/qc_clean.mp4 重跑 video 即重建。
- **⚠ 大 graph 下 framesync「窗口开启首帧」配对失败＝整层丢帧/露黑**：vtx 已内置 tpad 首尾克隆垫帧（垫帧量 1/1，加大无效且产片尾 extra 帧）；手写 ffmpeg 大图时同病同方，终检必跑 `blackdetect=d=0.01:pix_th=0.10` 全片零黑帧门。
- **⚠ 大 graph「层窗口边界密集区」中段丢帧＝连续 PTS 空洞**：vtx 双保险=①`-filter_complex_threads 1`（overlay 链线程化无收益反引竞态）②输出端 `-fps_mode cfr -r {fps}` 复制补帧；交付遇「相邻帧逐字节相同」属预期，定稿前须帧数精确+零黑帧+断点两侧抽验三重确认。
- **⚠ 帧号权威抽帧一律 `select=eq(n\,F)` 过滤器（配 `-vsync 0`/`-fps_mode passthrough`）**：`-ss` 有 ±1 帧不可靠区，只用于粗定位，否则帧级断言会差 1 帧误判。
- **⚠ tpad 尾克隆帧会延后视觉切换 1 帧**：做帧级 sfx-演示联动、逐帧比对时须计入。手写 ffmpeg 其他已知坑：1. `overlay` 滤镜输入序是 `[底层][上层]`，反了会把输出缩成上层尺寸；**同尺寸输入时更隐蔽**——不报错不缩放，但 alpha 前景被背景整帧盖住（输出「没有人」，2026-09-13 matte×背景合成预览实锤）。写合成命令先问：哪个输入带 alpha？带 alpha 的一律放第二位。 2. overlay 输入流（PNG 序列/ProRes 中间片）默认从成片 t=0 播放；必须 `setpts=PTS+{in帧}/{fps}/TB` 时移 + `enable='between(t,...)'` 双保险。 3. 本机 ffmpeg 8.1.2 的 VP9 (`yuva420p`) alpha 会丢，透明中间片只用 ProRes 4444（`prores_ks -profile:v 4444 -pix_fmt yuva444p10le`）或 PNG 序列。 4. 帧精确切割用 `trim=start_frame:end_frame` + 重编码；`-ss` 前置是关键帧吸附，禁止用于精确切。 5. 素材必须是 CFR 且帧率 = 项目 fps（VFR 素材先归一化再入库）。 6. 音频循环用 `-stream_loop -1` 输入参数；`aloop` 的 size 参数会预分配巨量内存。 7. 字幕经 ASS 烧录（粒度 1 厘秒 < 1 帧）；音频 adelay 毫秒量化，误差 ≤1ms（契约已声明）。
## timeline.json 契约（速览，v2 轨道等价模型）
```jsonc
{
  "version": 2,
  "project": { "fps": 30, "width": 1920, "height": 1080, "duration_frames": 720 },
  "sources":  [ { "id": "v01", "path": "assets/v01.mp4", "transform": "crop_center_scale" } ],
  "video_tracks": [   // 全部轨道等价：数组顺序＝图层顺序（[0] 最底，末位最上）；画布为黑场
    { "name": "aroll", "kind": "aroll", "clips": [   // 出镜口播（底层主画面）
        { "src": "v01", "src_in": 0, "src_out": 96, "dst_in": 0, "dur": 96, "fade_in_frames": 12 } ] },
    { "name": "broll", "kind": "broll", "clips": [   // 实拍 B-roll 全屏覆盖窗（scale/x/y 缺省＝全屏）
        { "src": "v02", "src_in": 0, "src_out": 48, "dst_in": 96, "dur": 48 } ] },
    { "name": "pip", "kind": "pip", "clips": [        // 画中画小窗
        { "src": "v07", "src_in": 0, "src_out": 96, "dst_in": 48, "dur": 96,
          "scale": 0.26, "x": 1460, "y": 90, "opacity": 0.92,
          "fade_in_frames": 12, "fade_out_frames": 12 } ] },
    { "name": "gfx", "kind": "gfx", "clips": [        // HTML 动效层（src 指向 html，帧序列按需渲染）
        { "src": "gfx/gfxA.html", "dst_in": 0, "dur": 192 } ] }
  ],
  "subtitles": [ { "in": 12, "out": 88, "text": "示例字幕" } ],   // ASS 烧录于全部图层之上
  "audio_tracks": [
    { "name": "bed",   "events": [ { "src": "v05", "in": 0, "out": 720, "volume": 0.22, "loop": true } ] },
    { "name": "voice", "events": [ { "src": "v03", "in": 60, "out": 252, "volume": 0.85,
        "fade_in_frames": 12, "fade_out_frames": 20 } ] },
    { "name": "amb",   "events": [ { "src": "v08", "in": 400, "out": 500, "volume": 0.3,
        "pan": -0.6, "fade_in_frames": 24, "fade_out_frames": 24 } ] }
  ]
}
```
**v2 核心语义**：所有视频轨道等价，无 base 特权、无全覆盖要求（空洞帧＝黑场，是规划问题不是校验错误）；`kind`（aroll/broll/screen/pip/gfx）纯语义标注，渲染机制完全不区分，只服务于编排规范、预览着色与画面构成统计；clip 的 `scale/x/y/opacity` 全部可选（缺省＝全屏不透明，全屏走 transform 归一）；fade 统一为 **alpha 淡入淡出**（无下层时自然露出黑场＝视觉淡黑）。内核与预览器均只接受 `version: 2`，不接受或自动转换旧 v1（role+overlays）及缺失版本的时间线。
**"这个素材放哪条轨"速查**：
| 素材 | 轨道 | 说明 |
|---|---|---|
| 出镜口播（含对镜头演示） | `aroll` | 通常为 tracks[0] 底层 |
| 实拍空镜/产品特写/环境 | `broll` | 全屏覆盖窗（缺省几何即全屏） |
| 录屏、软件界面实况、产品演示 | `screen` | 全屏或分区均可 |
| 小窗画面（角标人像/引用片段/双视角） | `pip` | 必须给 scale/x/y |
| **全屏流程图/表格/数据展示/原理动画** | `gfx-full`（kind=gfx） | clip 只写 dst_in+dur，缺省全屏；覆盖实拍层 |
| 标题卡/下三分之一/角标/强调字 | `gfx-overlay`（kind=gfx） | 页面自身留透明区域，叠在最上（仅低于字幕） |
gfx 的"全屏 vs 局部"由 HTML 页面设计决定（帧恒为全画布 PNG，透明区域即"局部"），timeline 层不区分——不要为全屏图形发明新 kind。表中轨型是独立素材的放置规则，不是角色/组件的拆页清单；页面边界先按 [HTML 创作组织](gfx.md#html-创作组织) 决定，再按本文挂载。
不变量（validate 强制）：`src_out - src_in == dur`（gfx clip 除外）；切割范围 ≤ 素材实测帧数；**同轨 clips 不重叠**（视频与音频皆是，唯一机制约束）；clip/事件不超出 duration_frames；fade 总长 ≤ 片段长。音频事件可加 `src_in`（取源音频中段）与 `loop`（循环平铺）。**source 可以是纯音频文件**（wav/mp3：音效、BGM、环境声等资产库素材直接进 timeline；时长换算项目帧网格，只能被音频事件引用，视频 clip 引用会被拒绝）。完整字段语义读 `~/.agents/skills/video/vtx/timeline.schema.json`，里面有每个字段的注释。

**字幕来源 `subtitles_src`（v2 内核键）**：根级声明 `"subtitles_src": "subs/ep.srt"`（相对 timeline 路径），与根级 `subtitles[]` 互斥——内核只负责引用与烧录（.srt 时间戳按项目 fps 换算整帧 / .json 帧值直用），谁产出该文件（asr/手写/其他工具）内核不感知；文件内容参与回执指纹（改字幕文件即失效重渲）。

**插件键（v2 §2.1 三分支：已装启用=生效 / 未装=报错+安装提示 / 禁用=报错+启用提示；`_meta` 永久自由命名空间）**：
| 键 | 位置 | 插件 | 行为 |
|---|---|---|---|
| `"denoise": true\|{...}` | sources[] | plugins/denoise | 该源 prepare 音频走「陷波→Resemble 仅降噪→48k」链，产物内容寻址 |
| `"duck_strategy": {"mode":"static",...}` | audio_tracks[] | plugins/duck-static | 床乐静态分段避让（与 `duck:true` 互斥；纯内存变换不写回 timeline） |
| `"bgm": true\|{"output","extra_deps"}` | sources[] | plugins/bgm-render | **音轨直接引用 music_tools 工程 JSON**：validate 期供时长 facts，prepare 期自动 `bgm render` → 48k stereo s16 落内容寻址键（`output`：`master` 母带 / `premaster` 母前 / `stem:<轨名>` 该轨总线分轨 `stems/<轨>.bus.wav`，video 侧自行混音，轨名 `^[A-Za-z0-9][A-Za-z0-9_.-]*$`）；`extra_deps` 文件/目录均可（目录递归内容签名）；一工程共享渲染缓存 `cache/bgm/` 只渲一次多源派生，陈旧渲染目录由 `vtx gc` 键级回收（在用保活）；缓存命中零渲染/工程·$ref·参数变更自动重渲；详见 plugins/bgm-render/README.md |
| `"blender_bridge": true\|{"out","verify"}` | 根级 | plugins/blender-bridge | `vtx all` 全链（qc 后）自动导出 Blender 审片工程（默认 `<项目>/preview.blend`），配置即导出免手敲命令 |
未知键警告不阻断；插件未装/禁用时写键会 validate FAIL 并给出安装/启用指引。协议权威见 `docs/ARCHITECTURE.md` §2。
## QC 检查项语义（qc 阶段自动执行）
- 成片帧数/时长/分辨率与 project 声明一致
- **底轨切点帧比对**：底轨（tracks[0]）每个切片在成片与源素材（同变换）间做像素比对——证明切点落在声明的帧上
- **上层窗口生效**：tracks[1:] 每个 clip 采样帧与底轨基准（qc_clean）的均值差或覆盖率达标——防"叠加层出现在错误时间"
- **空窗无泄漏**：无上层覆盖的帧上不应有叠加残留
- **响度归一**：整合响度 ≈ -14 LUFS（±1.5，EBU R128）
- **音频并发窗口响度**：最大并发轨数的窗口有能量
## 与其他文档/资产的衔接
- HTML 动效页面（gfx/*.html）的编写与设计 → [gfx](gfx.md)（帧驱动协议、样式模板）。
- 科技口播选题、脚本、拍摄与编排 → `<video_tools>/templates/tech-talk/references/production-workflow.md`；编排前读同目录 `narrative.md`，音频口径见同目录 `audio-mixing.md`。历史源件出处保留在各文档。
- 现成音频资产在工作区 `_library/assets_audio/`（Kenney/Mixkit/Sonniss 音效库 + bgm/ AI 试听样，许可登记见其 LICENSE.md；各期 timeline 以 `../_library/assets_audio/...` 相对引用）；本地 BGM 产线路由进 `music` skill（`~/.agents/skills/music/`）。
- 用户手剪回读协议（blend2json→ASR→用户权威文本对照）→ `<video_tools>/apps/blender-bridge/README.md`「用户手剪回读协议」。
- 拍摄规范 → `<video_tools>/templates/tech-talk/references/shooting.md`（速查）/ 同目录 `shooting-guide.md`（通用手册）；项目装备记录与通用操作指引分开。
## 三大生成模块（asr/tts/imgen）产线接口
三模块默认关闭（v2 迁至 `apps/asr|tts|imgen`）。开启：`bash apps/asr/init.sh` / `bash apps/tts/init.sh` / `bash apps/imgen/init.sh`（幂等，复用本机已有资产优先）→ `config.local.json` 对应条目 `enabled:true`（模板见 `config.example.json`）。三模块互不依赖、与 vtx 零耦合，唯一耦合面 = 项目目录文件契约（timeline.json / assets/**）。
- **字幕锚定工作流（apps/asr/，口播→subtitles[]）**：口播音频/成片 → `python3 apps/asr/asr_cli.py timeline <音频> -t <项目>/timeline.json`——sidecar 转写 + 字级时间戳 → 文本规范化断句（口水词清除/行宽 ≤18/最短 0.6s）→ subtitles[] 就地合并（旧值自动备份进 `cache/reports/`）。**合并后必须 `vtx validate` + `audit` 复核**：阅读速度 ≤9 字/s、告警逐条人工解释后才可进 `video` 阶段烧录。长音频库内自动分块（±5s 低能量边界）；`--dry-run` 只预览。端口纪律：**18081 是 IME 输入法 sidecar，本仓 ASR sidecar 默认 18085**（config `asr.base_url`）。
- **TTS 占位→实录替换衔接（apps/tts/）**：`python3 apps/tts/tts_cli.py spec -t <项目>/timeline.json` 从 voice 轨 `vt_<ID>` 事件链反推每窗预算（budget=Σ链帧/30+0.02s）成 spec.json（人工校对文本）→ `python3 apps/tts/tts_cli.py gen <spec.json> --project <项目> [--engine edge]` → 产物落 **`<项目>/assets/voice_tts/final/<窗口ID>.wav`**（48kHz/mono/pcm_s16le，时长=budget±0.02s）。实录替换链同构：用户按窗录音进 `assets/voice_takes/<ID>.wav` → `python3 apps/sync/repack_voice.py -t <项目>/timeline.json` 替换占位（用法见 `--help`）→ 运行完整 `all` 正式交付，局部诊断前置见 [runbook](runbook.md)。IndexTTS 需 repo+专用解释器；`--engine edge` 免 GPU 兜底（晓晓）。
- **背景图 bg4k 硬接口（apps/imgen/）**：`python3 apps/imgen/imgen_cli.py gen "<prompt>" --out <项目> --kind bg4k --yes` → 生图 API 生成 + lanczos cover-crop 到**恰好 3840×2160、8-bit RGB PNG** 落 `assets/bg/background_4k.png`——matting `apps/matting/run_full_bg.sh` 的 cv2.imread 直读逐像素合成，尺寸/像素格式不符即链断（模块内置 ffprobe 断言）。`bg1080p`/`vertical` 变体供口播底图；离线补裁 `apps/imgen/imgen_cli.py postprocess <原图> --kind bg4k -o <目标>`。**付费纪律：0.1 元/张，提交前确认门（无 `--yes` 时交互 y/N，非 TTY 直接中止）；审核拒绝不退款**；生成 URL 2 小时失效，原图留档 `assets/imgen/`。
- **抠像与背景合成（apps/matting/）**：真人实拍抠像 → 带 alpha ProRes4444 → 与背景合成，全流程见 `apps/matting/matting.md`（v2 自本 skill 迁入 app 目录；resolve_matte 达芬奇 Magic Mask 为主力，`run_full_bg.sh` 自研链为历史备用）。
