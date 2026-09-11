# 剪辑（edit）· 中间层工具站：timeline.json + vtx
> **使用者向**：timeline.json 契约、vtx CLI 用法、音频工程规则与渲染坑表。性能基准与坑位破案详录见 `../../docs/DEV.md`。不涉及 HTML 动效设计（→ `references/gfx.md`）、不涉及选题调研（→ `references/research.md`）。索引见 `../SKILL.md`。
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
程序本体在 video_tools 仓：`vtx/` 与 `scripts/` 的权威位置是 `~/文档/01_活跃项目/video_tools/`，本 skill 内为符号链接（上文路径全部继续可用）；**修改程序一律改 video_tools 仓**。`vtx/vtx.py` 是唯一入口，实现分为 `infra`（Ctx/执行/并行）、`encoders`（NVENC/x264/libfdk 探测与封装）、`composite`（滤镜图构建）、`qc`、`stages`（各阶段编排）、`gfx_render`（CDP 引擎）、`hygiene`（缓存键/gc/df/snapshot/VERSIONS）、`cache_state`（回执身份/依赖门）八个模块；历史版本回滚锚点在 git 历史。入口自定位、无硬编码主目录路径；迁移=整个 video_tools 仓拷走 + 更新符号链接（前提：目标机器有 ffmpeg + 浏览器）。
## 时间线预览（人审用）
`preview.html` 是单文件网页（无外部依赖），把 timeline.json 渲染成可视化时间线供**人类**审阅编排：视频轨/动效层/字幕/音轨分行显示，事件块带源名、时长、vol/pan/loop 标注与淡入淡出条；支持缩放、适应宽度、悬停播放头、点击事件块看全字段详情，并有**预览级校验**（整数帧、EDL 不变量、base 轨连续性、越界、同轨音频重叠等）——发现问题标红，但最终以 `vtx validate` 为准。
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
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json gc --dry    # 回收清单预览（收尾习惯）
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json gc          # 执行回收（键集外垃圾+tmp 孤儿）
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json gc --all    # 深度回收（保留 reports/VERSIONS/history/final）
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json df          # 体积体检（分项占用+top-20 大文件）
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json snapshot    # timeline+文本资产快照（自动留 2 份）
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json clean      # 清空 cache/ out/（慎用）
```
## 缓存卫生与体积治理
- **内容寻址缓存**：gfx 帧缓存键 = `sha1(html字节+声明的 cache_dependencies 内容+fps+视口+时长)[:12]`（目录 `cache/gfx/<key>/`），seg 键含源指纹+切片/窗口/编码参数（`cache/seg/<key>.mov|mp4`），音频键含源指纹。**源改了键就变**；旧键自然成为垃圾，由 gc 回收。不要手工 `rm cache/seg/...`。
- **`vtx gc`**：回收「当前 timeline 键集之外」的全部缓存条目 + tmp 孤儿（>24h）+ out 一次性中间物。`cache/reports/` 永不回收。`--keep-days N` 暂缓近 N 天条目。**收尾习惯：每轮「改 timeline→重渲」结束后先 `gc --dry` 看一眼再 `gc`**——cache 占用从此有上界（≈当前键集工作集），不再单调增长。
- **证据纪律（管线级约定）**：调试渲次（整片 mp4）一律写 `cache/tmp/<runid>/`，阶段结束即删（`hygiene.tmp_run` 上下文）；需留证一律抽帧（≤10 张）或 540p 小样 ≤10MB 存 `cache/reports/evidence/`。**报告/回执/JSON 一律 `cache/reports/`，gc 永不触碰**。**tmp 选址铁律：GB 级媒体中间物严禁写系统 /tmp**（本机 /tmp 是 tmpfs，写即占 RAM/swap 且会被定期清理，长任务文件可能中途消失）；≤数十 MB 小件可用 /tmp。
- **产物版本记账**：mux 每次成功追加 `out/VERSIONS.json`（md5/大小/timeline+audio md5）；**覆盖检测**——将覆盖的 final.mp4 与账面末条 md5 不符 → 显著告警 + 旧条目标 superseded。旧成片轮转 `out/history/`（硬链接优先），`--keep-versions N` 默认 2。
- **快照**：改 timeline 的批量脚本（retimeline 类）改调 `vtx snapshot`（timeline+全部引用文本资产，KB 级，自动留 2 份），不手写 `.bak`。
- **体积预算**：`VTX_CACHE_BUDGET_GB=80` 设预算后 gfx/video 阶段前超限自动跑 gc 清单提示（`VTX_GC_AUTO=1` 直接回收）；audit 报告含 volume 节（cache/out 占用+tmp 孤儿）。
**gfx 渲染引擎**：默认 CDP 常驻浏览器（单浏览器多 tab/页并行；不透明页单截直写、透明页双探针解算 alpha）；渲染器失败自动整页回落 CLI 逐帧路径。`VTX_GFX=cli` 强制 CLI、`VTX_GFX_TABS=N` 调 tab 并发（默认 4）、`VTX_GFX_PAGES` 调页级并行、`VTX_GFX_PNG_LEVEL` 调透明页压缩级别。不透明页输出与 CLI 逐字节一致；透明页半透明边缘有 ≤5/255 舍入级差异（亚感知）。每次阶段完成写 `out/receipt.json`（timeline 哈希、各阶段结果）；QC 报告在 `out/qc_report.json`，审计报告在 `out/audit_report.json`。**回执依赖门**：receipt.json 绑定输入身份（timeline+源指纹+gfx html+声明的 cache_dependencies）——输入变化不继承旧阶段成功；单跑某阶段前置未成功会被直接拒绝（audio←prepare、video←gfx（有 gfx 轨时）、mux←video+audio、qc←mux），编辑输入后按序重跑或直接 `all`。详见 docs/cache-provenance.md。
## audit 指标审计（建议性，不阻断渲染）
**validate 是机制硬门，audit 是内容体检**——审计出的 ⚠ 不拦截渲染，但发布前应逐条看过。检查项：画面构成占比与模块完整性（缺 broll/screen/gfx 提示）、黑场窗口、**无声窗口**（bed 轨应全程兜底）、口播覆盖率（主流观察 83-99.7%）、全片语速（**标准值 5.5 字/s（用户拍板引导值，=主流中位）；ok 区间 4.8~6.3**，明细在 `~/文档/04_bilibili视频/_library/corpus/叙事风格分析/data/speech_rate.json`）、字幕阅读速度（>7.5 字/s 偏快、>9 过快）/最短时长/相互重叠。基准值都注明在报告里，来源是本项目自己的 39 视频语料实测。
## 音频工程（build_audio 内置）
- **音乐避让（duck）**：音轨声明 `"duck": true`（bed/amb 建议）即被 `"duck_by"`（缺省 `["voice"]`）轨的混音经 `sidechaincompress`（threshold 0.03 / ratio 6 / attack 150ms / release 700ms）压低。**⚠ 新项目启用 duck 前必须先做「duck 开/关」A/B 响度对照，否则长节目下无口播段床乐可能反常抬高约 19dB**；床乐静态电平已足够低（低于口播 ≥5dB）时建议直接不开（破案详录见 DEV.md）。
- **响度归一（两遍静态增益）**：第一遍 ebur128 实测原始混音 I，第二遍施加恒定 `volume` 增益到 **-14 LUFS（EBU R128，B站标准）**；QC 阶段自动复测（±1.5 LUFS 容差）。**⚠ 不要用单遍 `loudnorm`（dynamic 模式），长节目上时变增益会随内容游走致区域塌陷**。峰值安全由项目侧 limiter 兜底（`post_loudness.sh`：alimiter limit=0.599，audio 后 mux 前必跑）。
- **⚠ audio → post_loudness → mux 三步链不可跳中步，否则 mux 后解码 TP 超标**。
- 混音链：轨内子混音（apad 补齐成片全长）→ duck → 总混音（amix normalize=0 保留音量比例）→ 静态增益 → 定长裁切。
- **BGM 分轨审计包（正式版默认必做）**：BGM 正式版本挂载进时间线后，除 vtx 渲染外必跑 `python3 <本仓>/blender_bridge/audit_bundle.py -t <项目>/timeline.json`（旧库 `~/文档/04_bilibili视频/_library/blender_bridge/` 位置为兼容指针，权威副本已收编本仓）——分轨 MP3 导出 + 审计 timeline + Blender 审计工程 `audit.blend` 一键五步，产物落 `<项目>/cache/audit_bgm/`。用户在 Blender VSE 逐轨静音/独听对比才能参与混音与编排裁决（人机协作交付门；完整规程见 music skill「交付门：分轨审计包」节）。分轨或音量改动后重跑即可（MP3 按源 mtime 自动失效重转）。
## 渲染速度：草稿 / GPU / 缓存
```bash
# 迭代预览（不碰正式产物）：540p + NVENC p4，跳过 qc_clean；改动集中片头时加 --frames
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json video --draft --frames 1800
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json mux --draft    # draft.mp4 + audio.wav → draft_final.mp4
# 正式 GPU 渲染：NVENC p7+tune hq+vbr cq17（1080p 交付透明，下游 4K 重编码无感）
python3 ~/.agents/skills/video/vtx/vtx.py -t myvideo/timeline.json video --gpu
```
量级预期：片头迭代 `--draft --frames 300` 秒级返回；全片冷渲染分钟级、缓存命中后约 1 分钟（详细基准见 DEV.md）。缓存语义：gfx=内容寻址（sha1 键，见上节）；video seg 键含源指纹与切片参数；音频键含源指纹。video 分段渲染的段缓存文件名携带段内容键（段区间+画布/模式/gpu+每层上游内容键+段字幕 ass 字节），改字幕/图层/gfx 内容后旧段不再命中。
## 坑表（vtx 已内置规避，手写 ffmpeg 时必看；⚠ 规则+后果，破案详录见 DEV.md）
- **⚠ `ffprobe -count_frames` 是逐帧解码计数，probe 一律用流头 nb_frames + count_packets 回落**，否则 validate 可能分钟级。
- **⚠ `-frames:v N` 会触发 filter_complex 全量 drain（所有输入解到 EOF）**，draft 截短须对每路输入加 `-t`。
- **⚠ NVENC 探测尺寸须 ≥145 宽**，过小报假失败，曾致草稿静默回落 x264——探测失败必须显式告警。
- **⚠ 正式 mux 用 libfdk_aac 192k**：原生 `aac` 对真人素材（密集瞬态）解码真峰过冲可达 +8.5dB——终检必测编码后解码 TP，不信 wav 域 TP；vtx mux 已自动探测 libfdk_aac 并在缺失时回落+告警。
- **⚠ qc_clean 由 video 阶段尾部生成**：渲染中断残留截断文件（moov 缺失）会使后续 qc 假失败——rm cache/qc_clean.mp4 重跑 video 即重建。
- **⚠ 大 graph 下 framesync「窗口开启首帧」配对失败＝整层丢帧/露黑**：vtx 已内置 tpad 首尾克隆垫帧（垫帧量 1/1，加大无效且产片尾 extra 帧）；手写 ffmpeg 大图时同病同方，终检必跑 `blackdetect=d=0.01:pix_th=0.10` 全片零黑帧门。
- **⚠ 大 graph「层窗口边界密集区」中段丢帧＝连续 PTS 空洞**：vtx 双保险=①`-filter_complex_threads 1`（overlay 链线程化无收益反引竞态）②输出端 `-fps_mode cfr -r {fps}` 复制补帧；交付遇「相邻帧逐字节相同」属预期，定稿前须帧数精确+零黑帧+断点两侧抽验三重确认。
- **⚠ 帧号权威抽帧一律 `select=eq(n\,F)` 过滤器（配 `-vsync 0`/`-fps_mode passthrough`）**：`-ss` 有 ±1 帧不可靠区，只用于粗定位，否则帧级断言会差 1 帧误判。
- **⚠ tpad 尾克隆帧会延后视觉切换 1 帧**：做帧级 sfx-演示联动、逐帧比对时须计入。手写 ffmpeg 其他已知坑：1. `overlay` 滤镜输入序是 `[底层][上层]`，反了会把输出缩成上层尺寸。 2. overlay 输入流（PNG 序列/ProRes 中间片）默认从成片 t=0 播放；必须 `setpts=PTS+{in帧}/{fps}/TB` 时移 + `enable='between(t,...)'` 双保险。 3. 本机 ffmpeg 8.1.2 的 VP9 (`yuva420p`) alpha 会丢，透明中间片只用 ProRes 4444（`prores_ks -profile:v 4444 -pix_fmt yuva444p10le`）或 PNG 序列。 4. 帧精确切割用 `trim=start_frame:end_frame` + 重编码；`-ss` 前置是关键帧吸附，禁止用于精确切。 5. 素材必须是 CFR 且帧率 = 项目 fps（VFR 素材先归一化再入库）。 6. 音频循环用 `-stream_loop -1` 输入参数；`aloop` 的 size 参数会预分配巨量内存。 7. 字幕经 ASS 烧录（粒度 1 厘秒 < 1 帧）；音频 adelay 毫秒量化，误差 ≤1ms（契约已声明）。
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
**v2 核心语义**：所有视频轨道等价，无 base 特权、无全覆盖要求（空洞帧＝黑场，是规划问题不是校验错误）；`kind`（aroll/broll/screen/pip/gfx）纯语义标注，渲染机制完全不区分，只服务于编排规范、预览着色与画面构成统计；clip 的 `scale/x/y/opacity` 全部可选（缺省＝全屏不透明，全屏走 transform 归一）；fade 统一为 **alpha 淡入淡出**（无下层时自然露出黑场＝视觉淡黑）。旧 v1（role+overlays）不被 vtx 接受，预览器可只读兼容显示。
**"这个素材放哪条轨"速查**：
| 素材 | 轨道 | 说明 |
|---|---|---|
| 出镜口播（含对镜头演示） | `aroll` | 通常为 tracks[0] 底层 |
| 实拍空镜/产品特写/环境 | `broll` | 全屏覆盖窗（缺省几何即全屏） |
| 录屏、软件界面实况、产品演示 | `screen` | 全屏或分区均可 |
| 小窗画面（角标人像/引用片段/双视角） | `pip` | 必须给 scale/x/y |
| **全屏流程图/表格/数据展示/原理动画** | `gfx-full`（kind=gfx） | clip 只写 dst_in+dur，缺省全屏；覆盖实拍层 |
| 标题卡/下三分之一/角标/强调字 | `gfx-overlay`（kind=gfx） | 页面自身留透明区域，叠在最上（仅低于字幕） |
gfx 的"全屏 vs 局部"由 HTML 页面设计决定（帧恒为全画布 PNG，透明区域即"局部"），timeline 层不区分——不要为全屏图形发明新 kind。动效页面本身怎么写，见 `references/gfx.md`。
不变量（validate 强制）：`src_out - src_in == dur`（gfx clip 除外）；切割范围 ≤ 素材实测帧数；**同轨 clips 不重叠**（视频与音频皆是，唯一机制约束）；clip/事件不超出 duration_frames；fade 总长 ≤ 片段长。音频事件可加 `src_in`（取源音频中段）与 `loop`（循环平铺）。**source 可以是纯音频文件**（wav/mp3：音效、BGM、环境声等资产库素材直接进 timeline；时长换算项目帧网格，只能被音频事件引用，视频 clip 引用会被拒绝）。完整字段语义读 `~/.agents/skills/video/vtx/timeline.schema.json`，里面有每个字段的注释。
## QC 检查项语义（qc 阶段自动执行）
- 成片帧数/时长/分辨率与 project 声明一致
- **底轨切点帧比对**：底轨（tracks[0]）每个切片在成片与源素材（同变换）间做像素比对——证明切点落在声明的帧上
- **上层窗口生效**：tracks[1:] 每个 clip 采样帧与底轨基准（qc_clean）的均值差或覆盖率达标——防"叠加层出现在错误时间"
- **空窗无泄漏**：无上层覆盖的帧上不应有叠加残留
- **响度归一**：整合响度 ≈ -14 LUFS（±1.5，EBU R128）
- **音频并发窗口响度**：最大并发轨数的窗口有能量
## 与其他文档/资产的衔接
- HTML 动效页面（gfx/*.html）的编写与设计 → 本 skill `references/gfx.md`（帧驱动协议、样式模板）
- 选题调研与 01-brief → 本 skill `references/research.md`
- BGM/音效/环境声的选取与授权 → `~/文档/04_bilibili视频/_library/reports/音频资产与混音惯例调查.md` §四-§六（授权红线：**商业音乐不用=唯一红线**、音效/环境声宽松获取、AI 生成积极使用）；**现成资产在工作区 `_library/assets_audio/`**（Kenney/Mixkit/Sonniss 音效库 + bgm/ AI 试听样，登记见其 LICENSE.md；各期 timeline 以 `../_library/assets_audio/...` 相对引用）；**本地 BGM 产线路由进 `music` skill（~/.agents/skills/music/）**，本体在 `~/文档/01_活跃项目/music_tools/`（CLI：bgm）
- 选题/脚本/录制规划 → 上层流程（见 `~/文档/04_bilibili视频/AGENTS.md`）
## 方法论指针
- **编排总纲**：`~/文档/04_bilibili视频/_library/reports/编排方法论-科技口播视频.md`（开场模板/正文节奏/演示对位三原则/字幕 ASR 锚定/音频避让与响度链/结尾模板/验证门清单）——编排每期 timeline 前先读。
- 用户手剪回读协议（blend2json→ASR→用户权威文本对照）→ `~/文档/01_活跃项目/video_tools/blender_bridge/README.md`「用户手剪回读协议」节。
- **待办（拍板后执行）**：voice-ime/07-shooting-guide.md 的通用实拍规范上提为 `_library/reports/` 通用手册，项目版留本期装备记录——上提后此处补指针。
## 三大生成模块（asr/tts/imgen）产线接口
三模块默认关闭。开启：`bash asr/init.sh` / `bash tts/init.sh` / `bash imgen/init.sh`（幂等，复用本机已有资产优先）→ `config.local.json` 对应条目 `enabled:true`（模板见 `config.example.json`）。三模块互不依赖、与 vtx 零耦合，唯一耦合面 = 项目目录文件契约（timeline.json / assets/**）。
- **字幕锚定工作流（asr/，口播→subtitles[]）**：口播音频/成片 → `python3 asr/asr_cli.py timeline <音频> -t <项目>/timeline.json`——sidecar 转写 + 字级时间戳 → 文本规范化断句（口水词清除/行宽 ≤18/最短 0.6s）→ subtitles[] 就地合并（旧值自动备份进 `cache/reports/`）。**合并后必须 `vtx validate` + `audit` 复核**：阅读速度 ≤9 字/s、告警逐条人工解释后才可进 `video` 阶段烧录。长音频库内自动分块（±5s 低能量边界）；`--dry-run` 只预览。端口纪律：**18081 是 IME 输入法 sidecar，本仓 ASR sidecar 默认 18085**（config `asr.base_url`）。
- **TTS 占位→实录替换衔接（tts/）**：`python3 tts/tts_cli.py spec -t <项目>/timeline.json` 从 voice 轨 `vt_<ID>` 事件链反推每窗预算（budget=Σ链帧/30+0.02s）成 spec.json（人工校对文本）→ `python3 tts/tts_cli.py gen <spec.json> --project <项目> [--engine edge]` → 产物落 **`<项目>/assets/voice_tts/final/<窗口ID>.wav`**（48kHz/mono/pcm_s16le，时长=budget±0.02s）。实录替换链同构：用户按窗录音进 `assets/voice_takes/<ID>.wav` → `python3 scripts/repack_voice.py -t <项目>/timeline.json` 替换占位（用法见 `--help`）→ 按 repack 打印的后续命令清 prepare 缓存重跑（audio 必重跑）。IndexTTS 需 repo+专用解释器；`--engine edge` 免 GPU 兜底（晓晓）。
- **背景图 bg4k 硬接口（imgen/）**：`python3 imgen/imgen_cli.py gen "<prompt>" --out <项目> --kind bg4k --yes` → 速创 API 生成 + lanczos cover-crop 到**恰好 3840×2160、8-bit RGB PNG** 落 `assets/bg/background_4k.png`——matting `run_full_bg.sh` 的 cv2.imread 直读逐像素合成，尺寸/像素格式不符即链断（模块内置 ffprobe 断言）。`bg1080p`/`vertical` 变体供口播底图；离线补裁 `imgen_cli.py postprocess <原图> --kind bg4k -o <目标>`。**付费纪律：0.1 元/张，提交前确认门（无 `--yes` 时交互 y/N，非 TTY 直接中止）；审核拒绝不退款**；生成 URL 2 小时失效，原图留档 `assets/imgen/`。
