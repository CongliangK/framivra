# DEV · 开发者向文档

> **开发者向**：工具演进史、性能基准、坑位破案详录。使用视频产线请读 `skill/SKILL.md`，无需阅读本文。
> 条目末尾标注出处（裁自哪份文档的哪一节）。

## 一、性能基准

环境：RTX 4080 / 8 核，基准项目 = voice-ime 653s@1080p30。〔出处：skill/references/edit.md「渲染速度」〕

- 渲染全链（2026-09-08 基线）：全片 14.7min → `--gpu` 9:46。
- 拆包同批提速（2026-09-11）：qc 690s→11s（约 60×，结果与旧版逐项一致）；video 分段并行（8 段，511s→112s，约 4.6×）；冷启动并行化（seg 预编码 + prepare 抽音频线程池）。全链 21.5min → 缓存命中约 50s / 冷启动约 164s。
- 片头迭代 `--draft --frames 300`（10s 画面）9.8s；validate 3s。
- gfx 引擎：CDP 常驻浏览器多 tab 约 34ms/帧（不透明页单截直写）；透明页白黑双截解算。后续透明页快路径（`Emulation.setDefaultBackgroundColorOverride(a=0)` 直出带 alpha PNG）单帧 2400ms→91ms（约 6.6×）；页级并行 `VTX_GFX_PAGES` 后全量冷 gfx 12024 帧 192.5s（约 62fps）。tab 并发 4→16 收益递减，页级并行才是增量方向。〔出处：edit.md「gfx 渲染引擎」＋工作区 AGENTS.md D44/D44b〕
- blender_bridge audit_bundle：早期 json2blend+verify_blend 全链 22.2s → 优化后 8.1s。〔出处：工作区决策台账〕
- content_gates 五门合并单次 pass（G1/G2/G3/G7/G10 + 项目门一次跑完）。〔出处：README scripts 节〕
- `ffprobe -count_frames` 逐帧解码计数：1080p 全源 12-27s/个；改流头 nb_frames + count_packets 回落后 validate 2min→3s。〔出处：edit.md 坑表〕
- `-frames:v N` 触发 filter_complex 全量 drain：曾耗 4:30（300 帧）；已对每路输入加 `-t` 截短。〔出处：edit.md 坑表〕

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

## 三、工具演进史

- **剪辑中间层自研**：ffmpeg 唯一渲染后端，vtx CLI，不引 NLE 依赖；达芬奇/Blender 作剪辑底层经调研与 POC 后否决（继续 vtx 路线）。转场仅硬切；全管线固定 30fps 精确（≠29.97，教训：时基等平台参数先实测平台事实标准）。〔出处：工作区决策台账 D9/D14/D24〕
- **timeline 契约 v2**：轨道等价（数组序=图层序）、kind 纯语义、gfx 并入 video_tracks、同轨 clips 不重叠；旧 v1（role+overlays）退役。〔出处：edit.md 契约节，D15〕
- **缓存内容寻址内建**：voice-ime 174G→25G 清理实测后收编为 gc/df/snapshot/体积预算等治理机制；「改源不重渲」与「误命中旧内容」两类历史问题从机制上消失。〔出处：edit.md「缓存卫生」〕
- **程序权威本体收编（D40）**：video_tools 仓为唯一权威，skill live 挂载点全部符号链接；双仓收编方案（D36）同日被升格取代。
- **vtx 拆包重构（2026-09-11）**：vtx.py 单文件 → 包（infra/encoders/composite/qc/stages/gfx_render/hygiene/cache_state 八模块），行为与旧版逐项一致；历史回滚锚点在 git 历史。〔出处：edit.md「工具位置」〕
- **gfx 引擎演进**：CLI 逐帧截图 → CDP 常驻浏览器多 tab（4.6×）→ 透明页 override 直出快路径（6.6×）→ 页级并行（全冷 192.5s）。`VTX_GFX=cli` 保留回退。〔出处：edit.md + AGENTS.md D42/D44/D44b〕
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
