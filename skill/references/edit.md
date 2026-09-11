# 剪辑（edit）· 中间层工具站：timeline.json + vtx

本文档是视频合成的**底层工具站**：提供受校验保护的时间线契约（timeline.json）和唯一渲染入口（vtx CLI）。不涉及 HTML 动效设计（→ 同 skill `references/gfx.md`）、不涉及选题调研（→ `references/research.md`）。索引见 `../SKILL.md`。

## 铁律

1. **AI 永远不手写 ffmpeg 剪辑命令**。剪辑 = 编写/修改 timeline.json + 调用 vtx。失误率控制靠三道闸：渲染前校验（validate）、确定性构建（vtx 生成全部 ffmpeg）、渲染后 QC。
2. **修改 timeline.json 后必须重跑 validate**，校验失败时修数据、不绕过。
3. 所有时间量用**整数帧**；区间 `[in, out)` 左闭右开；项目时基 30fps（D24 换基，≠29.97）、48kHz 音频（每帧整 1600 采样）。

## 工具位置

```
vtx CLI：        ~/.agents/skills/video/vtx/vtx.py            （符号链接；权威本体=~/文档/01_活跃项目/video_tools/vtx/，D40；仅依赖 python3 标准库 + ffmpeg/ffprobe/google-chrome）
契约 schema：    ~/.agents/skills/video/vtx/timeline.schema.json
时间线预览器：    ~/.agents/skills/video/vtx/preview.html     （人审编排用，双击打开）
参考实现（POC）： ~/文档/04_bilibili视频/_archive/poc2/   （外部示例项目：30s 多轨 demo，QC 全过；不随 skill 分发）
```

程序本体在 video_tools 仓（D40）：`vtx/` 与 `scripts/` 的权威位置是 `~/文档/01_活跃项目/video_tools/`，本 skill 内为符号链接（上文路径全部继续可用）；**修改程序一律改 video_tools 仓**。vtx.py 用 `Path(__file__)` 自定位、无任何硬编码主目录路径；迁移=整个 video_tools 仓拷走 + 更新符号链接（前提：目标机器有 ffmpeg + google-chrome）。

## 时间线预览（人审用）

`preview.html` 是单文件网页（无外部依赖），把 timeline.json 渲染成可视化时间线供**人类**审阅编排：视频轨/动效层/字幕/音轨分行显示，事件块带源名、时长、vol/pan/loop 标注与淡入淡出条；支持缩放（滑杆或 Ctrl+滚轮）、适应宽度、悬停播放头（帧号+秒数）、点击事件块看全字段详情（帧/秒双单位），并有**预览级校验**（整数帧、EDL 不变量 src_out-src_in==dur、base 轨连续性、越界、同轨音频重叠等）——发现问题标红，但最终以 `vtx validate` 为准。

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
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json all        # 全流程 → myvideo/out/final.mp4

# 分阶段（gfx 帧缓存可复用，改哪段重跑哪段；缓存按源 html mtime 自动失效）：
#   validate → audit（指标体检）→ prepare（提取音频）→ gfx（渲染动效）→ video → audio → mux → qc
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json clean      # 清空 cache/ out/
```

**gfx 渲染引擎（D42）**：默认 CDP 常驻浏览器（单浏览器多 tab，不透明页单截直写/透明页白黑双截解算 alpha，实测 4.6×，约 34ms/帧）；渲染器失败自动整页回落 CLI 逐帧路径。`VTX_GFX=cli` 强制 CLI、`VTX_GFX_TABS=N` 调并发（默认 4）、`VTX_GFX_PNG_LEVEL` 调透明页压缩级别。不透明页输出与 CLI 逐字节一致；透明页半透明边缘有 ≤5/255 舍入级差异（亚感知）。

每次阶段完成写 `out/receipt.json`（timeline 哈希、各阶段结果）；QC 报告在 `out/qc_report.json`，审计报告在 `out/audit_report.json`。

## audit 指标审计（建议性，不阻断渲染）

对照主流 UP主 实测基准（叙事风格分析 + 画面构成与节奏分析）自动体检编排合理性：**validate 是机制硬门，audit 是内容体检**——审计出的 ⚠ 不拦截渲染，但发布前应逐条看过。检查项：画面构成占比与模块完整性（缺 broll/screen/gfx 提示）、黑场窗口（无视频层的帧段）、**无声窗口**（无任何音频事件的帧段——主流视频口播间隙 100% 有音乐铺底，bed 轨应全程兜底）、口播覆盖率（voice 轨，主流观察 83-99.7%）、全片语速（**标准值 5.5 字/s（用户拍板引导值，＝主流中位 5.56）；ok 区间 4.8~6.3**，实测基准 39 视频均值 5.42/P25-P75 4.95~5.76，逐 UP主 TheCW 4.77 ~ CodeSheep 6.39，明细在 `~/文档/04_bilibili视频/_library/corpus/叙事风格分析/data/speech_rate.json`）、字幕阅读速度（逐行实测 P50=5.68 / P95=8.02 / P99=9.37，>7.5 偏快 >9 过快）/最短时长/相互重叠。基准值都注明在报告里，来源是本项目自己的 39 视频语料实测。

## 音频工程（build_audio 内置）

- **音乐避让（duck）**：音轨声明 `"duck": true`（bed/amb 建议）即被 `"duck_by"`（缺省 `["voice"]`）轨的混音经 `sidechaincompress`（threshold 0.03 / ratio 6 / attack 150ms / release 700ms）压低——口播出现时音乐自动让路，经典剪辑思路的程序化实现。**⚠ 长流警告（2026-09-08）**：voice-ime 653s 全图实测该滤镜在长流上使无口播段的床乐反常抬高 ~19dB（10s 短流隔离测试行为正常，深层机制未定位）——新项目启用 duck 前必须先做「duck 开/关」A/B 响度对照；床乐静态电平已足够低（低于口播 ≥5dB）时建议直接不开。
- **响度归一（两遍静态增益，2026-09-08 起）**：第一遍 ebur128 实测原始混音 I，第二遍施加恒定 `volume` 增益到 **-14 LUFS（EBU R128，B站标准）**；QC 阶段自动复测（±1.5 LUFS 容差）。**不要退回单遍 `loudnorm`（dynamic 模式）**：长节目上其时变增益随内容游走——voice-ime 653s 成片实测前段 -11.8、后段 -21.7~-32 LUFS 的区域塌陷（同一素材两遍渲染可复现）。峰值安全由项目侧 limiter 兜底（voice-ime 的 post_loudness.sh：alimiter limit=0.599，audio 后 mux 前必跑，见 D30/D31）。
- 混音链：轨内子混音（apad 补齐成片全长）→ duck → 总混音（amix normalize=0 保留音量比例）→ 静态增益 → 定长裁切。
- **BGM 分轨审计包（2026-09-10 用户裁定：正式版默认必做）**：BGM 正式版本挂载进时间线后，除 vtx 渲染外必跑 `python3 <本仓>/blender_bridge/audit_bundle.py -t <项目>/timeline.json`（旧库 `~/文档/04_bilibili视频/_library/blender_bridge/` 位置为兼容指针，权威副本已收编本仓）——分轨 MP3 导出 + 审计 timeline + Blender 审计工程 `audit.blend` 一键五步，产物落 `<项目>/cache/audit_bgm/`。用户在 Blender VSE 逐轨静音/独听对比才能参与混音与编排裁决（人机协作交付门；完整规程与给用户的使用话术见 music skill「交付门：分轨审计包」节）。分轨或音量改动后重跑即可（MP3 按源 mtime 自动失效重转）。

## 渲染速度：草稿 / GPU / 缓存（2026-09-08，voice-ime 653s@1080p30 实测基准）

```bash
# 迭代预览（不碰正式产物）：540p + NVENC p4，跳过 qc_clean；改动集中片头时加 --frames
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json video --draft --frames 1800
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json mux --draft    # draft.mp4 + audio.wav → draft_final.mp4

# 正式 GPU 渲染：NVENC p7+tune hq+vbr cq17（1080p 交付透明，下游 4K 重编码无感）
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json video --gpu
```

实测（RTX 4080 / 8 核，voice-ime 653s@1080p30，2026-09-08）：全片 CPU 渲染 14.7min（含重复 seg 编码）→ **--gpu 9:46**（瓶颈已移到 52 层 overlay 滤镜链，编码器近零耗时；seg 缓存命中后纯合成为主）；片头迭代 `--draft --frames 300`（10s 画面）**9.8s**；validate 2min→**3s**。三层缓存语义（变更后需手动失效）：prepare=cache/audio 存在性；gfx=cache/<stem> 存在+帧数；**video seg=cache/seg/tXXcYY 存在+流头帧数（2026-09-08 新增，此前每次 video 全量重编 ≈4.5min）**。源/clip 参数变更 → `rm -rf cache/seg`（或对应文件）后重跑。

坑表（全部踩过、已内置规避）：
- **`ffprobe -count_frames` 是逐帧解码计数**（1080p 全源 12-27s/个；probe 已改流头 nb_frames + count_packets 回落，validate 2min→3s）
- **`-frames:v N` 触发 filter_complex 的全量 drain**：所有输入解到 EOF（300 帧曾耗 4:30）——`--draft --frames` 已对每路输入加 `-t` 截短
- **NVENC 探测尺寸须 ≥145 宽**（128px 报「低于最小支持值」假失败，曾致草稿静默回落 x264）
- **正式 mux 内置 libfdk_aac 192k（2026-09-08 D33）**：原生 `aac` 对真人素材（波峰因子 23-25dB 密集瞬态）解码真峰过冲实测可达 +8.5dB——终检必测编码后解码 TP，不信 wav 域 TP；vtx mux 已自动探测 libfdk_aac 并在缺失时回落+告警
- **qc_clean 由 video 阶段尾部生成**：渲染中断会残留截断文件（moov 缺失）使后续 qc 假失败——rm cache/qc_clean.mp4 重跑 video 即重建
- **大 graph 下 framesync「窗口开启首帧」配对失败＝整层丢帧/露黑（2026-09-08 v3.5）**：57 层 overlay 图中次输入（mp4 seg 层）首帧在主帧到达时尚未产出时被 framesync 透传跳过——单帧黑/丢层，确定性复现且**编码器无关**（NVENC/x264 同集）；D21 的 setpts 修复只解决了部分形态。vtx 已内置 tpad 首尾克隆垫帧（真首帧 PTS 与 enable 映射不变，垫帧永不显示；垫帧量保持 1/1，v3.6 实测扩到 2/24 无效且克隆尾帧会变片尾 extra 帧）；手写 ffmpeg 大图时同病同方，终检必跑 `blackdetect=d=0.01:pix_th=0.10` 全片零黑帧门
- **大 graph「层窗口边界密集区」中段主链丢帧＝连续 PTS 空洞（2026-09-08 v3.6，61 层图七渲实证）**：多个 overlay 层 enable 端点相差 ≤20 帧的密集交叠带，主链整帧丢失（缺 3~10 帧，非确定性但病灶带 100% 复现，可伴近黑崩溃带）；**与编码器/滤镜图线程数/tpad 垫帧量全部无关**（病灶在次输入流传输中段而非头尾边界）。vtx 双保险内置：①`-filter_complex_threads 1`（overlay 链串行依赖，线程化无收益反引入调度竞态）；②输出端 `-fps_mode cfr -r {fps}` 按帧率网格复制补帧（缺帧落在层切换点的相对静止带，1-4 帧复制视觉不可见）。交付口径：病灶带个别帧是**复制补偿帧**而非原渲染帧，QC/红队遇「相邻帧逐字节相同」属预期；定稿前须三重确认（帧数精确+blackdetect 零黑帧+断点两侧内容抽验），CFR 补 PTS 不保内容，补出来若是黑帧仍须重渲
- **`-ss` 定位抽帧有 ±1 帧不可靠区（PTS µs 舍入，2026-09-08 v3.6 红队实测）**：帧号权威抽帧一律 `select=eq(n\,F)` 过滤器（配 `-vsync 0`/`-fps_mode passthrough`），`-ss 秒` 定位只用于粗定位；帧级断言（切点/同步/黑帧带）用 select 法，否则差 1 帧会误判
- **tpad 尾克隆帧会延后视觉切换 1 帧（2026-09-08 v3.6 红队实测）**：层出点处 D34 尾克隆帧被拾取（u3→d6/c4b→d9a/ar_八→d10 三处实际切换比名义窗晚 1 帧，逐字节同帧零视觉差异）——未来做帧级 sfx-演示联动、逐帧比对时须计入这 1 帧

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

**v2 核心语义（D15）**：所有视频轨道等价，无 base 特权、无全覆盖要求（空洞帧＝黑场，是规划问题不是校验错误）；`kind`（aroll/broll/screen/pip/gfx）纯语义标注，渲染机制完全不区分，只服务于编排规范（一条轨一个 kind、按内容分队列）、预览着色与画面构成统计；clip 的 `scale/x/y/opacity` 全部可选（缺省＝全屏不透明，全屏走 transform 归一）；fade 统一为 **alpha 淡入淡出**（无下层时自然露出黑场＝视觉淡黑）。旧 v1（role+overlays）不再被 vtx 接受，预览器可只读兼容显示。

**"这个素材放哪条轨"速查**：

| 素材 | 轨道 | 说明 |
|---|---|---|
| 出镜口播（含对镜头演示） | `aroll` | 通常为 tracks[0] 底层 |
| 实拍空镜/产品特写/环境 | `broll` | 全屏覆盖窗（缺省几何即全屏） |
| 录屏、软件界面实况、产品演示 | `screen` | 全屏或分区均可 |
| 小窗画面（角标人像/引用片段/双视角） | `pip` | 必须给 scale/x/y |
| **全屏流程图/表格/数据展示/原理动画** | `gfx-full`（kind=gfx） | clip 只写 dst_in+dur，缺省全屏；覆盖实拍层 |
| 标题卡/下三分之一/角标/强调字 | `gfx-overlay`（kind=gfx） | 页面自身留透明区域，叠在最上（仅低于字幕） |

gfx 的"全屏 vs 局部"由 HTML 页面设计决定（帧恒为全画布 PNG，透明区域即"局部"），timeline 层不区分——不要为全屏图形发明新 kind。动效页面本身怎么写，见同 skill `references/gfx.md`。

不变量（validate 强制）：`src_out - src_in == dur`（gfx clip 除外）；切割范围 ≤ 素材实测帧数；**同轨 clips 不重叠**（视频与音频皆是，唯一机制约束）；clip/事件不超出 duration_frames；fade 总长 ≤ 片段长。音频事件可加 `src_in`（取源音频中段）与 `loop`（循环平铺）。**source 可以是纯音频文件**（wav/mp3：音效、BGM、环境声等资产库素材直接进 timeline；时长换算项目帧网格，只能被音频事件引用，视频 clip 引用会被拒绝）。

完整字段语义读 `~/.agents/skills/video/vtx/timeline.schema.json`，里面有每个字段的注释。

## QC 检查项语义（qc 阶段自动执行）

- 成片帧数/时长/分辨率与 project 声明一致
- **底轨切点帧比对**：底轨（tracks[0]）每个切片在成片与源素材（同变换）间做像素比对——证明切点落在声明的帧上
- **上层窗口生效**：tracks[1:] 每个 clip（gfx/窗口/全屏）采样帧与底轨基准（qc_clean）的均值差或覆盖率达标——防"叠加层出现在错误时间"（曾真实发生，见坑 2）
- **空窗无泄漏**：无上层覆盖的帧上不应有叠加残留
- **响度归一**：整合响度 ≈ -14 LUFS（±1.5，EBU R128 实测）
- **音频并发窗口响度**：最大并发轨数的窗口有能量

## 已知坑（ vtx 已内置规避，手写 ffmpeg 时必看）

1. `overlay` 滤镜输入序是 `[底层][上层]`，反了会把输出缩成上层尺寸。
2. overlay 输入流（PNG 序列/ProRes 中间片）默认从成片 t=0 播放；必须 `setpts=PTS+{in帧}/{fps}/TB` 时移 + `enable='between(t,...)'` 双保险。
3. 本机 ffmpeg 8.1.2 的 VP9 (`yuva420p`) alpha 会丢，透明中间片只用 ProRes 4444（`prores_ks -profile:v 4444 -pix_fmt yuva444p10le`）或 PNG 序列。
4. 帧精确切割用 `trim=start_frame:end_frame` + 重编码；`-ss` 前置是关键帧吸附，禁止用于精确切。
5. 素材必须是 CFR 且帧率 = 项目 fps（VFR 素材先归一化再入库）。
6. 音频循环用 `-stream_loop -1` 输入参数；`aloop` 的 size 参数会预分配巨量内存。
7. 字幕经 ASS 烧录（粒度 1 厘秒 < 1 帧）；音频 adelay 毫秒量化，误差 ≤1ms（契约已声明）。

## 与其他文档/资产的衔接

- HTML 动效页面（gfx/*.html）的编写与设计 → 本 skill `references/gfx.md`（帧驱动协议、样式模板）
- 选题调研与 01-brief → 本 skill `references/research.md`
- BGM/音效/环境声的选取与授权 → `~/文档/04_bilibili视频/_library/reports/音频资产与混音惯例调查.md` §四-§六（三层资产库 + 混音默认值 + 开源音乐生成；授权态度按 AGENTS.md D17：商业音乐不用=唯一红线、音效/环境声宽松获取、AI 生成积极使用）；**现成资产在工作区 `_library/assets_audio/`**（Kenney/Mixkit/Sonniss 音效库 + bgm/ AI 试听样，登记见其 LICENSE.md；跨期共用，不在单期项目内；各期 timeline 以 `../_library/assets_audio/...` 相对引用）；**本地 BGM 产线路由进 `music` skill（~/.agents/skills/music/）**，本体在 `~/文档/01_活跃项目/music_tools/`（ACE-Step 1.5 = 时长精确 BGM 主力，MiniMax Music3 = 整曲备选，ComfyUI 8188 端口跑；CLI：bgm）
- 选题/脚本/录制规划 → 上层流程（见 `~/文档/04_bilibili视频/AGENTS.md`）

## 方法论指针

- **编排总纲**：`~/文档/04_bilibili视频/_library/reports/编排方法论-科技口播视频.md`（开场模板/正文节奏/演示对位三原则/字幕 ASR 锚定/音频避让与响度链/结尾模板/验证门清单，溯源 D31-D39）——编排每期 timeline 前先读。
- 用户手剪回读协议（blend2json→ASR→用户权威文本对照）→ `~/文档/01_活跃项目/video_tools/blender_bridge/README.md`「用户手剪回读协议」节。
- **待办（拍板后执行）**：voice-ime/07-shooting-guide.md 的通用实拍规范（参数/机位/打光/同步/降噪结论）上提为 `_library/reports/` 通用手册，项目版留本期装备记录——上提后此处补指针。
