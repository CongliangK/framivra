# DEV · 开发者向文档

> **开发者向**：工具演进史、性能基准、坑位破案详录。使用视频产线请读 `skill/SKILL.md`，无需阅读本文。
> 条目末尾标注出处（裁自哪份文档的哪一节）。

## 一、性能基准

环境：RTX 4080 / 8 核，基准项目 = voice-ime 653s@1080p30。〔出处：skill/references/edit.md「渲染速度」〕

- 渲染全链（2026-09-08 基线）：全片 14.7min → `--gpu` 9:46。
- 拆包同批提速（2026-09-11）：qc 690s→11s（约 60×，结果与旧版逐项一致）；video 分段并行（8 段，511s→112s，约 4.6×）；冷启动并行化（seg 预编码 + prepare 抽音频线程池）。全链 21.5min → 缓存命中约 50s / 冷启动约 164s。
- 片头迭代 `--draft --frames 300`（10s 画面）9.8s；validate 3s。
- gfx 引擎：CDP 常驻浏览器多 tab 约 34ms/帧（不透明页单截直写）；透明页白黑双截解算。后续透明页快路径（`Emulation.setDefaultBackgroundColorOverride(a=0)` 直出带 alpha PNG）单帧 2400ms→91ms（约 6.6×）；页级并行 `VTX_GFX_PAGES` 后全量冷 gfx 12024 帧 192.5s（约 62fps）。tab 并发 4→16 收益递减，页级并行才是增量方向。〔出处：edit.md「gfx 渲染引擎」＋工作区 AGENTS.md D44/D44b〕
- gfx 方案 v2（2026-09-11，本机 32C64G，基准项目 voice-ime 28 页 12024 帧，配置记法=PAGES/SPLIT_FRAMES/TABS）：新增**页内分片并行**（`VTX_GFX_SPLIT_FRAMES`/`VTX_GFX_SPLIT_MAX`）与**静态跨度去重**（`__vtx_static_spans`，1474 帧≈12.4% 字节复制免渲）后，生产默认定版 **7/450/4＝全量冷 129.8-131.9s、峰值 RSS 15.0-15.2GB**（commit 2e65536；桌面活跃时段正式验收 3 轮中位 149.8s，chrome/firefox/obsidian 同跑）。矩阵实测（冷跑 WALL s / 峰值 RSS GB）：6/0/4=161.9/13.0-16.4；8/600/4=129.3（±0.5%）/16.5-17.3；12/0/4=145.6；8/450/4=126.5-143.9/16.5-17.9（破 16GB 门）；**8/450/6=112.9-122.1/19.8-20.4（RSS 破线被否）**。≤110s 硬门未达：方案主杠杆（canvas 批量导出通道）被像素门槛证伪（见 §二），四个重页（g6b_endboard 765 帧/c5a 1153/c5b 810，全部零静态跨度）只剩截图通路，g6b 串行估算 ≈80s 构成硬下限。校准纪律：当次 6/0/4 基线 160.25s vs 历史记录 192.5s 偏差 -16.8%，未破 20% 修正线，矩阵数字可比。〔出处：docs/plans-2026-09-11/方案一＋voice-ime/cache/reports/gfx_opt_20260911 审计归档〕
- blender_bridge audit_bundle：早期 json2blend+verify_blend 全链 22.2s → 优化后 8.1s。〔出处：工作区决策台账〕
- content_gates 五门合并单次 pass（G1/G2/G3/G7/G10 + 项目门一次跑完）。〔出处：README scripts 节〕
- `ffprobe -count_frames` 逐帧解码计数：1080p 全源 12-27s/个；改流头 nb_frames + count_packets 回落后 validate 2min→3s。〔出处：edit.md 坑表〕
- `-frames:v N` 触发 filter_complex 全量 drain：曾耗 4:30（300 帧）；已对每路输入加 `-t` 截短。〔出处：edit.md 坑表〕
- 字幕 subs 三命令（2026-09-12，方案四，合成 30s/900 帧项目实测）：`subs check` <1s（零渲染纯文本）；`subs preview` 单张 0.56s（图层单帧合成路径）；改 1 条字幕重渲 2 段 1.9s（6 段缓存命中）——段键含本段窗口内 ass 字节（composite._seg_content_key），「改一条字幕=全链重渲」的担忧实测不成立。〔出处：docs/plans-2026-09-11/方案四 + 方案四-验收证据/〕

迁移即测试基线（2026-09-10，全部通过）：〔原 README「深度测试基线」节，7a75655 瘦身后由本文承载〕

- 合成项目 vtx 全链 QC 9/9（切点帧比对 0.03-0.04/255、-14.1 LUFS）；`--draft --frames` NVENC 草稿链验证。
- 真实项目回归（voice-ime v3.9 冻结 timeline 在 /tmp 链接树副本上，live 原位零写入）：validate 37 源/69 clips/150 字幕 5.2s 通过；qc 74/75 与 FREEZE v3.9 完全一致（唯一 ✗ = 已证伪的 c4_mini 探针假阴性）；timeline md5 `9ce6ec43…`、final.mp4 md5 `ae9389ef…` 与冻结记录相符。
- blender_bridge：json2blend+verify_blend 556/556、129 条（=D39 基线精确复现）。
- 组件矩阵：new_project 脚手架契约校验、全脚本 py_compile/bash -n/node --check、init.sh 幂等两次零差异。
- 迁移附带修复：vtx.py timeline 路径改 `os.path.abspath`（防符号链接穿透把回执写到 live 项目，已回归验证）；aroll/README 失效引用修正。

blender_bridge 历史产物校验记录：〔原 blender_bridge/README「历史校验记录」节，迁移后由本文承载〕

| 项目 | 校验 | 样帧 PSNR vs 终渲 |
|---|---|---|
| poc2（24fps 30s） | 103/103 | 35.6 dB @f120 |
| voice-ime（30fps 600s） | 410/410 | 41.2 dB @f6000 |
| voice-ime v3.3（653s） | 520/0（122 条） | — |
| voice-ime 终版（653.13s/19594f） | 524/524 | 43.6 dB @f3000 |

字幕透明轨（subs_alpha.mov）体量参考：voice-ime 1.24GB/18000 帧/2min14s 渲染。

blend2json 回写测试记录：恒等回写两项目零改动结构相等 PASS；模拟 4 视频+3 音频操作+1 故意撞轨，回写 7 项精确捕捉、1 项换通道正确告警，vtx validate 通过。

## 二、坑位破案详录

（使用文档只保留「⚠ 规则 + 后果」，排查过程与实测细节在此。）

### framesync 大 graph 丢层（v3.5 排查）〔出处：edit.md 坑表〕

现象：57 层 overlay 图中，次输入（mp4 seg 层）首帧在主帧到达时尚未产出时被 framesync 透传跳过——单帧黑/丢层，确定性复现且编码器无关（NVENC/x264 同集）；早期 setpts 修复只解决部分形态。
修复：vtx 内置 tpad 首尾克隆垫帧（真首帧 PTS 与 enable 映射不变，垫帧永不显示）。垫帧量保持 1/1——扩到 2/24 实测无效且克隆尾帧会变片尾 extra 帧。

### 大 graph 层窗口边界密集区丢帧（v3.6 排查）〔出处：edit.md 坑表〕

现象：61 层图七渲实证——多个 overlay 层 enable 端点相差 ≤20 帧的密集交叠带，主链整帧丢失（缺 3~10 帧，非确定性但病灶带 100% 复现，可伴近黑崩溃带）；与编码器/滤镜图线程数/tpad 垫帧量全部无关（病灶在次输入流传输中段而非头尾边界）。
修复（双保险）：①`-filter_complex_threads 1`（overlay 链串行依赖，线程化无收益反引入调度竞态）；②输出端 `-fps_mode cfr -r {fps}` 按帧率网格复制补帧（缺帧落在层切换点的相对静止带，1-4 帧复制视觉不可见）。
交付口径：病灶带个别帧是复制补偿帧而非原渲染帧，QC/红队遇「相邻帧逐字节相同」属预期；CFR 补 PTS 不保内容，补出来若是黑帧仍须重渲。

### loudnorm 单遍 dynamic 塌陷实测〔出处：edit.md「响度归一」〕

voice-ime 653s 成片实测单遍 loudnorm（dynamic 模式）时变增益随内容游走：前段 -11.8、后段 -21.7~-32 LUFS 的区域塌陷（同一素材两遍渲染可复现）。故现行方案 = 两遍静态增益（ebur128 实测 I → 恒定 volume 增益到 -14 LUFS）。

### sidechaincompress 长流异常实测〔出处：edit.md「音乐避让」〕

voice-ime 653s 全图实测该滤镜在长流上使无口播段的床乐反常抬高约 19dB（10s 短流隔离测试行为正常，深层机制未定位）。故新项目启用 duck 前必须先做「duck 开/关」A/B 响度对照；床乐静态电平已足够低（低于口播 ≥5dB）时建议直接不开。

### NVENC 探测坑细节〔出处：edit.md 坑表〕

探测尺寸须 ≥145 宽：128px 报「低于最小支持值」假失败，曾致草稿静默回落 x264 而无人察觉——探测失败必须显式告警而非静默回落。

### 原生 aac 真峰过冲实测〔出处：edit.md 坑表 / README 依赖矩阵〕

原生 `aac` 编码器对真人素材（波峰因子 23-25dB 密集瞬态）解码真峰过冲实测可达 +8.5dB（wav 域 TP 达标不代表成片达标）——正式 mux 内置 libfdk_aac 192k，终检必测编码后解码 TP；缺失时回落+告警。

### gfx 透明页合成组拍平〔出处：工作区 AGENTS.md D44b〕

body 级 opacity 的页面（如 g6a_title）会使 Chrome 合成组拍平，`setDefaultBackgroundColorOverride` 静默失效——双探针帧 flatten 守卫自动回落白黑双截。omitBackground 方案四次实测均无 alpha 输出，已证伪不再尝试。

### GPU 光栅 / tab 并发上限〔出处：工作区 AGENTS.md D44b〕

GPU 光栅对 gfx 截图无收益；Chrome 同源 tab 共用渲染进程，单浏览器加 tab 无效——提速靠 `VTX_GFX_PAGES` 页级并行。

### foreignObject 快照批量导出通道判死（方案 v2 阶段 2）〔出处：docs/plans-2026-09-11/方案一＋cache/reports/gfx_opt_20260911〕

设想：把页面 DOM 序列化进 SVG `<foreignObject>`，经 Image→canvas→toDataURL 一次导出整帧，绕过逐帧截图。实测：文本/字体/transform/drop-shadow 与截图通路**逐位一致**，但两类固有偏差过不了 golden 全零门槛——(a) 含 `filter:blur(150px)` 的页确定性 ±1/255 抖动约 1.5% 像素；(b) 径向渐变背景页（本项目四个重页全是）固有 max|Δ|=25~61、差异像素 7-14%（渐变 dithering＋低透明度文字 AA）。**对照实验判死归因**：同一帧原件直接走同一 SVG-image 管线差异逐位相同——偏差与页面重构无关，是 SVG-image 光栅上下文固有，任何"把页面写得更标准"都救不回。相关驱动与 `VTX_GFX_BATCH` env 已整体移除。附带实测：toDataURL 编码耗时强依赖内容熵（低熵页 7.7ms、高熵 blur 页 77ms）。

### navfree 免导航截图判负双杀（方案 v2 阶段 2）〔出处：同上，golden 报告 compare_p2_c4.json〕

设想：页内 `__vtx_render(f)` 置帧态，每 tab 只导航一次（`VTX_GFX_NAVFREE=1`）。单页空闲实测 40/40 帧逐字节一致，但负载下双杀——①**像素漂移**：8 实例并发对 4 个 `__vtx_render` 页自 ~f22 起全偏（llm_post 387/u3_install 127/c5a_main_path 641/c5b_hotkey 430 帧差异，golden 全帧比对）；②**反而更慢**：同页 ms/帧 1.5-4.4× 劣于逐帧导航（DOM 突变触发整页重光栅，blur 页尤甚：g6b navfree 111-163ms vs 导航 64-126ms）。处置：`VTX_GFX_NAVFREE` 默认 0 封存，代码保留（env 门控＋页内抛错哨兵回落＋页级告警齐全）——**勿开**。

### 纯页级并行扩容无效（方案 v2 阶段 1）〔出处：bench_p1 矩阵，cache/reports/gfx_opt_20260911/SUMMARY.md〕

PAGES 6→8→12 无分片时全量冷 161.9/168.8/145.6s 平坦（均值，CPU 仅 43-58%）——瓶颈是单页关键路径不是 CPU，只加页不切片买不到时间。结论：收益方向转为**页内分片**（`VTX_GFX_SPLIT_FRAMES`，8/450 起显著），见 §一 gfx 方案 v2 行。

### `-filter_complex_threads 1` 竞态证伪与真实管线解锁无收益双录（方案二阶段1，2026-09-12）〔出处：docs/plans-2026-09-11/方案二 + bench_p2 A/B 原始日志〕

- **竞态证伪（安全性）**：61 层 ±20 帧密集端点复现图（v3.6 病灶形态）threads=1 与 4 输出 MD5 逐位一致且 4 快 1.9×（26.17s vs 13.54s；收编为 tests/test_filter_threads.py）。`-filter_complex_threads 1` 改为 `infra.filter_threads()`（env `VTX_FILTER_THREADS`）。
- **但解锁在真实管线无墙钟收益（性能证伪）**：8 段并行下 --gpu 形态 89→86s（-3%）、x264 形态 99→98s；隔离单段（15 路输入的 seg00）仅 1.35×（38→28s）。2× 只在单进程大 graph 成立——段图 9-15 层的并行度来自「各输入独立预处理链」thread 化，overlay 主脊（全帧串行混合）与解码不被该开关加速；8 段并行时 CPU 聚合已饱和。**默认维持 1**；单进程路径（`--draft --frames` 头窗、VTX_VIDEO_SEGMENTS=1 回落、gfx 底轨 qc_clean）可显式 `VTX_FILTER_THREADS=4` 提速。
- **真实项目前 3 段跨运行非确定性（重要新事实）**：voice-ime seg00/01/02（恰为输入路数最多的 15/11/13 路）同 argv（threads=1）隔离复渲 4 次得 4 个 md5——与 DEV.md v3.6 条目「病灶非确定性、与滤镜图线程数无关」自洽；差异形态为长连片（78-370 帧）CFR 补偿帧位置漂移，非结构性。含义：**「两次渲染逐字节一致」类等价门在此项目不可用**，A/B 判定须用 t1 控制组方差对照（t4 差异段 PSNR 47.7-54.2dB 与同线程方差 49.3-50.2dB 同分布=无新增差异形态）；seg03-07（≤10 路）四轮逐字节全等。

### 字幕预览/烧录等价性两个量化陷阱（方案四红队 S1/S2/S7，2026-09-12）〔出处：docs/plans-2026-09-11/方案四-验收证据/＋审查报告〕

- **对齐偏移参照点**（S1）：字幕在 voice 窗口**结束后**顺延开始是常态（句间留白），偏移若参照窗口起点会夸大一个窗口长（实测 0.5s 滞后报 +5.50s FAIL）。subs check 已改为参照最近外沿（滞后窗口末端为正/超前窗口起点为负），重叠字幕仍参照窗口起点（v39 漂移语义）。
- **活跃集判定必须过 cs 量化**（S2）：`write_global_ass` 的 Dialogue 时间经 `_fc` 的 `round(frame·100/fps)` 量化，libass 以帧 pts 精确值比较——fps=24 时帧 17 的 pts=0.70833s < 量化起点 0.71s，成片不画而帧坐标判定会画。`_active_subs` 已复刻同一量化。
- **「逐像素一致」措辞口径**（S7）：子集 ass@0s 与全量 ass@t 在**同一输入表示**下烧录逐位一致（实测零差）；跨 ffmpeg 输入路径（mp4 解码 vs png 解码）因色度上采样差异有 1px 级 AA 差（PSNR 34.5dB，视觉无差）。preview vs 成片的差异全部为 x264 有损编码噪声（无字幕帧噪声底 max|Δ|=10/0.57%，字幕帧差异全部集中于文字带，其余画面 ≤8）。

## 三、工具演进史

- **剪辑中间层自研**：ffmpeg 唯一渲染后端，vtx CLI，不引 NLE 依赖；达芬奇/Blender 作剪辑底层经调研与 POC 后否决（继续 vtx 路线）。转场仅硬切；全管线固定 30fps 精确（≠29.97，教训：时基等平台参数先实测平台事实标准）。〔出处：工作区决策台账 D9/D14/D24〕
- **timeline 契约 v2**：轨道等价（数组序=图层序）、kind 纯语义、gfx 并入 video_tracks、同轨 clips 不重叠；旧 v1（role+overlays）退役。〔出处：edit.md 契约节，D15〕
- **缓存内容寻址内建**：voice-ime 174G→25G 清理实测后收编为 gc/df/snapshot/体积预算等治理机制；「改源不重渲」与「误命中旧内容」两类历史问题从机制上消失。〔出处：edit.md「缓存卫生」〕
- **字幕独立预检与预览（2026-09-12，方案四）**：`vtx subs check / preview / render` + `vtx diff` 报告对比入编（vtx/subs.py、vtx/diffcmd.py）；runbook 新增 R2s 纯字幕修订配方。动机 = v39 式「渲染完整片才发现字幕漂移 4.3s」事故循环（12 条字幕重开窗触发全链修订轮）；v1 的「字幕解耦独立烧录 pass」设计被红队 F4 证伪撤销（解耦多一整遍全片解码+重编码，且段键本就只含本段字幕）。〔出处：docs/plans-2026-09-11/方案四〕
- **程序权威本体收编（D40）**：video_tools 仓为唯一权威，skill live 挂载点全部符号链接；双仓收编方案（D36）同日被升格取代。
- **vtx 拆包重构（2026-09-11）**：vtx.py 单文件 → 包（infra/encoders/composite/qc/stages/gfx_render/hygiene/cache_state 八模块），行为与旧版逐项一致；历史回滚锚点在 git 历史。〔出处：edit.md「工具位置」〕
- **gfx 引擎演进**：CLI 逐帧截图 → CDP 常驻浏览器多 tab（4.6×）→ 透明页 override 直出快路径（6.6×）→ 页级并行（全冷 192.5s）→ 页内分片＋静态跨度去重（2026-09-11 方案 v2：默认 7/450/4 定版，全冷 129.8-131.9s）。方案 v2 配套两件基建：`scripts/gfx_golden.py` 像素基线（全零门槛，成为导出通道改造硬门——FO 快照、navfree 两通道均由它判死封存，详录 §二）；帧落盘原子化（tmp+os.replace，修 split×static 头帧并发双写竞态）。`VTX_GFX=cli` 保留回退。〔出处：edit.md + AGENTS.md D42/D44/D44b + docs/plans-2026-09-11/方案一〕
- **blender_bridge 路线拍板**：不用 MCP（CLI/bpy 直连更自由）、不用外部直写 .blend（无格式规范），用 `blender --background --python` 生成；json2blend 建成后逐步补 blend2json 回写桥与 audit_bundle 分轨审计包。〔出处：blender_bridge/README 头注〕
- **「打开即视频编辑工作区」实现注记**：headless 下 append 法/`win.workspace=` 赋值均不生效（实测 5.2.1 唯一可靠路径）＝以官方 Video_Editing app template 的 startup.blend 为底。〔出处：blender_bridge/README〕
- **design tokens 与模板库**：gfx-templates 七件源自 voice-ime v3.1-v3.9 九轮迭代已验收页参数化抽取；毛玻璃深底卡样式已废弃（违反白底暖色设计铁律），替代 = 白底暖色卡。〔出处：gfx.md〕
- **pip 安全区红线来历**：voice-ime v3.7 五页（c5b/c4b/c4t/c4f/c2）因未预留 pip 安全区整版返工+全量重渲，遂成设计期硬约束。〔出处：gfx.md「pip 安全区」〕
- **自包含底色原则来历**：透明底+深色字页面在演示层被剪短/落黑场后变黑底深字不可读（g6a_title 实案），确立「需要浅底就自带」。〔出处：gfx.md〕
- **历史副本纪律**：`_library/bg_test/works/`（matting 四件）与 `voice-ime/cache/aup3_extract/`（aroll 两件）与本体当前逐字节一致，但属冻结历史快照——修改一律改本仓。`matting/run_full_bg.sh` 与 `post_h3.sh` 为 bg_test 历史运行器，内含本机绝对路径未参数化，属收编原样保留。〔出处：README「历史副本纪律」〕
- **时间线预览器 preview.html**：单文件网页，内置 poc2 示例自动载入；预览级校验发现问题标红，最终以 `vtx validate` 为准。〔出处：edit.md〕

## 四、2026-09-11 全面体检遗留登记（未修项，勿无声消失）

体检当日修复清单见 commit 92609f3 / 898870e；以下为**有意未修/待外部条件**项：

- **NIT 清单**（健壮性审查判 NIT，修不动代码不影响正确性）：qc.py dry 早退后 176-177 死代码、infra.py:71 恒真分支、imgen_cli.py 死变量、vtx.py render_via 字符串状态机、init.py requests 双探测、bilibili.py --retry 0 NameError、aroll/trim_takes.py 44 字节 WAV 头假定（与自家 aup3_extract 配对成立）。
- **Windows 实机终验待办**：本仓跨平台修复（.gitattributes LF / init.py UTF-8/sys.executable / Edge x86 / conda 钩子 / heredoc cygpath / vtx Windows 三件套）均为 Linux 侧理论推演+Linux 回归，**尚无 Windows 实机验证**——朋友首次在 Windows 落地时按 README 跨平台节 + docs/依赖盘点 §三 清单逐项过一遍，重点复跑 tests/run_e2e.py（VTX_TEST_PYTHON/CHROME/FFMPEG 显式指定）。
- **asr 模块 Windows 深度移植未做**：venv 布局（.venv/Scripts/）已兼容探测，但 qwen-asr 解释器在 Windows 的可用性未验证；asr/README 手动路线兜底。
- **json2blend fps 类型微差**（实际不可达）：桥侧键函数取 timeline 原文 fps 类型，若手写 30.0 会与 vtx 键分叉——vtx Ctx 已拒非 int fps，故仅理论风险。
- **cache_dependencies 未声明依赖的外链变更不触发重渲**：当前全部工程未声明，属声明式契约的已知边界（docs/cache-provenance.md 有口径）。
