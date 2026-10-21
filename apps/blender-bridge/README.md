# blender_bridge — timeline.json → Blender VSE 预览前端

组件声明见 `app.json`（kind=app）；健康检查为 `audit_bundle.py --help`。内核入口从含 `vt.py` 与 `vtx/vtx.py` 的仓根定位；本 app 是预览与审计前端，不替代内核渲染/QC。

> **使用者向**：Blender 预览桥用法与 VSE 坑表。历史校验记录与实测数据见[开发记录](../../docs/reports/development-history.md)。

> 路线：不用 MCP（CLI/bpy 直连更自由）、不用外部直写 .blend（无格式规范），用 `blender --background --python` 生成。回写（Blender→json）由 blend2json.py 提供（见下）。

## 文件清单（用途）

| 件 | 用途 | 解释器 |
|---|---|---|
| `audit_bundle.py` | BGM 分轨审计包五步链（MP3 导出→审计 timeline→vtx validate→json2blend→verify_blend） | 系统 python3 |
| `json2blend.py` / `verify_blend.py` | timeline.json → preview.blend 生成 / 结构校验 | **blender**（`--python` 载入，python3 直跑 ModuleNotFoundError: bpy） |
| `blend2json.py` | 用户手剪回写桥（Blender→json，保护规则拒收危险改动） | **blender** |
| `subs_alpha.py` | 字幕透明轨（cache/subs.ass → ProRes 4444 alpha mov） | 系统 python3 |
| `bundle_session.py` | audit_bundle 的单次 blender 会话合并执行器（runpy 依次跑 json2blend/verify） | **blender** |
| `tests/` | audit 真 ffmpeg 回归（`test_audio_audit.py`） | 系统 python3 |

## 依赖

- **app 级（manifest requires，doctor 可探测）**：ffmpeg / ffprobe（audit_bundle 逐源导出与内容检测，系统 python3 即可跑）。
- **blender 解释器（app.json `requires.binaries` 含 blender）**：json2blend / verify_blend / blend2json / bundle_session 须由 **blender** 运行（`blender --background --python <脚本>`）——依赖 bpy，系统 python3 无法 import。本机 Blender 5.2.1 LTS（`~/.local/opt/blender-5.2.1-linux-x64`，符号链接 `~/.local/bin/blender`）。

## 健康检查

`env python3 apps/blender-bridge/audit_bundle.py --help` 退出码 0（app.json health.command；系统 python3 面不涉 blender）。blend 件另走语法门（vt init 的 bridge_* 编译检查）与实际 blender 会话（audit 全链）。

## 用法（对一个项目生成预览工程）

```bash
cd apps/blender-bridge   # （外部 `_library` 资产一律以工作区绝对路径引用，见文末编排方法论条目）
env python3 subs_alpha.py <项目>/timeline.json      # ① 字幕透明轨（需先有 cache/subs.ass，vtx 字幕阶段产物）
blender --background --factory-startup --python json2blend.py -- <项目>/timeline.json <项目>/preview.blend   # ② 生成
blender --background --python verify_blend.py -- <项目>/timeline.json <项目>/preview.blend                   # ③ 结构校验（退出码 0=全过）
blender <项目>/preview.blend                        # ④ 打开预览（空格播放，拖拽刮擦）
env python3 audit_bundle.py -t <项目>/timeline.json [--prefix bgm-]   # 分轨审计包五步链
```

**打开即视频编辑工作区**：json2blend 生成的 .blend 默认活动工作区=「Video Editing」——打开即是时间线+视频预览面板，无需手动切换工作区/场景。实现：以 Blender 官方 `Video_Editing` app template 的 startup.blend 为底（出厂启动文件不含该工作区，headless 下唯一可靠路径），清掉模板示例物件后在模板场景上建 VSE；找不到模板时回落原 Layout（不影响条目生成）。

依赖（app 级与 blender 解释器约束见顶部「依赖」节）。

## 历史校验记录

各项目 preview.blend 的 verify 校验通过数与样帧 PSNR 历史记录由[开发记录](../../docs/reports/development-history.md#一性能基准)及其固定原文保留，不代表当前版本已复验。

字幕透明轨产物：`<项目>/cache/subs_alpha.mov`（ProRes 4444 alpha）。

## 语义映射（与 vtx 包源码对齐）

- video_tracks 数组序＝图层序：音频轨占通道 1..A（每轨一条），视频轨 [0] 最底从 A+1 起，字幕轨最顶
- clip：`frame_final_start=dst_in`，`frame_final_end=dst_in+dur`（右开区间语义一致）；源偏移＝src_in
- 全屏 clip：crop169（16:9 中心裁切）+ 等比拉满（vtx L313）
- 窗口 clip（scale/x/y 任一存在）：scale 相对**源原始尺寸**（vtx L306-311，非画布 fit）、x/y=左上角像素
- gfx：复用内核只读解析器 `vtx/gfx_cache_reader.py`，只从生产 `gfx_dir` 按当前 `VTX_GFX`、worker 身份、`VTX_GFX_CACHE_NS` 与内容键定位，逐帧校验连续编号/PNG/完成摘要。Electron 复用生产 validator 验证真实 adapter/worker 回执与当前 binary/JS/settings 身份；HTML 缓存必须已有与当前环境、key、宽高、帧数、连续帧覆盖及逐 PNG SHA256 完整匹配的 `complete.json`，不接受无标记 PNG，不搜索其它 namespace、旧键或 `cache/<stem>/`。缺失/过期/损坏证书一律拒绝、不补签；请重新运行 `vtx gfx` 生成当前目录（会写入项目缓存），再导出。Blender 进程继承同一渲染环境，独立调用须使用生成缓存时的环境。
- 字幕：**不经 Blender 文字渲染**——vtx 的 subs.ass 经 `alphamerge 透明画布 + ass:alpha=1` 渲成
  透明视频轨叠加，与终渲像素同源（libass 同库同字体）
- fade_in/out：blend_alpha/volume 关键帧（插值取默认，预览近似）
- 色彩：view_transform=Standard、FRAME_DROP 同步；已知与终渲有 ~2% 整体亮度差（色彩管理路径），预览可接受

## 本工具踩坑记录（Blender 5.2.1 VSE API）

1. `Scene.use_sequencer` → `scene.render.use_sequencer`；`sequences` 集合改名 `strips`
2. **先 trim 再设 channel**：未 trim 的条（源全长）与同通道条重叠时，channel 赋值会被自动顶到上层
3. `StripTransform` 无 `fit_method`（5.x 移除）——缩放必须按源尺寸自算
4. `new_effect` 签名：`(name, type, channel, frame_start, length)`（无 frame_end）
5. 剥离无 `animation_data` 可寻（keyframe_insert 可用，插值默认非线性的贝塞尔）
6. 关键字参数部分方法静默失败（如 `AddItemListToMediaPool(file1=...)`），**全用位置参数**
7. ffmpeg `ass` 滤镜默认**不处理 alpha 平面**：需 `alpha=1`；且 `color=black@0.0` 的 alpha 过不了
   format 转换（被填 255）——透明画布用双输入 `alphamerge` 技巧
8. ffmpeg 滤镜参数里文件名后接 `:opt=v` 会被并进文件名——文件名必须内层引号 `ass=filename='路径':alpha=1`

## 已知边界（与调研报告一致）

字幕样式/侧链 ducking/LUFS/精确 fade 曲线是**渲染语义**，不进预览（预览是结构+视觉近似）；
终渲永远走 vtx。

## 回写桥 blend2json.py

用法：`blender --background --python blend2json.py -- <preview.blend> [out.json]`
（json 源取 scene["vtx_timeline"] 钩子；缺省输出 `<项目>/timeline.blendback.json`，不覆盖原文件）
回写面：视频条与音频条的**时间平移 / 左右缘裁剪（左缘=入点后移）/ 同族换轨**；
音频**整组**平移（loop 组无缝时）。产物需过 `python3 -m vtx -t <out> validate`。

保护规则（按字段拒收或告警，不是整件回滚）：
- 视频：跨族换轨（movie↔gfx）不回写换轨；素材 src 越界、越过画布尾、dst<0 拒收该条时间修改。
- 视频或音频同轨重叠在终检发现：**候选 JSON 已写出，脚本退出 2，不回滚候选**；不得当成交付件，须修正后重跑并通过 `vtx validate`。
- 音频：**换通道不回写**（轨名 voice/bed 驱动 ducking 与 audit 口播统计，串轨改语义）
- 音频 loop 组被部分拖动（组内缝隙）→ 不回写
- 音量/声像改动 → 告警不回写（带淡变事件的静态属性不可靠，已跳过比对）
- 字幕条（subs|ASS）移动 → 告警（字幕回写需改 subtitles 数组，未做）

注意：Blender 不允许同通道重叠，拖动相撞会把条自动顶到上层通道——表现为换轨
（视频侧同族照收，音频/跨族会被上述规则拦下并提示）。

## 分轨审计包 audit_bundle.py（人机协作交付门）

从仓库根运行下列命令会写审计目录与 MP3 缓存；默认产物为 `<项目>/cache/audit_bgm/{mp3/,timeline.json,audit.blend}`。`--skip-blend` 只产出 MP3 与审计 timeline，`--force` 强制重转全部 MP3。BGM 正式版的交付要求见 music skill「交付门：分轨审计包」节。

`apps/blender-bridge/audit_bundle.py` exports the selected tracks for listening before ducking and master loudness processing. Production media is never modified. MP3 is a lossy preview; a validated audit timeline is not proof that its mix equals the delivered film.

```bash
python apps/blender-bridge/audit_bundle.py -t /project/timeline.json --prefix bed --prefix sfx --skip-blend
# Preserve every event without silence detection:
python apps/blender-bridge/audit_bundle.py -t /project/timeline.json --prefix bed --whole --skip-blend
```

Python, FFmpeg and ffprobe must be on PATH. These commands work with quoted Windows paths; omit `--skip-blend` only when Blender is installed. Cross-drive media directories use absolute paths and therefore require rebasing if moved to another computer.

### Event fidelity

Every referenced source is exported and retains its source ID. A track can use several files; repeated references share an export. `--whole` copies every event including its volume, pan, source offset, loop and fade fields. Track-level ducking is deliberately excluded and listed in `_meta.excluded_duck_tracks`.

Default silence detection runs on the original first audio stream, not MP3 padding. Source content `[a,b)` is intersected with an event's `[src_in, src_in + out - in)`; intersection `[u,v)` becomes `src_in=u, in=event.in+u-src_in, out=event.in+v-src_in`. This preserves late placements and reused source excerpts. Seconds are rounded outward to frames, then overlapping/adjacent frame spans are merged. Detection failure exits nonzero.

Loop and fade events remain whole: naively splitting them would restart their cycle or envelope. Events with no detected content also remain whole, retaining quiet/silent intentional material and a valid nonempty track. Each such fallback is printed and recorded in `_meta.whole_fallbacks`. Detection thresholds are approximate; use `--whole` for conservative review.

An audio-only audit does not inherit `project.cache_dependencies` from a relocated visual project. Removed declarations are listed in its metadata. The exported media itself is tracked by vtx as ordinary sources.

### Cache and failure handling

MP3 names are based on source bytes, conversion settings and FFmpeg version, with output SHA256 checked before reuse. Same-time/same-size replacement, a different source and a damaged MP3 cannot silently reuse old audio. The first audio stream is exported at 48 kHz stereo, 320k libmp3lame. Equal source bytes share a single transcode job. Old track-name/mtime files are not trusted or deleted automatically.

Encoding and strict candidate decoding finish before atomic MP3 replacement. Failures before replacement preserve any valid cached MP3 and its receipt, and clean the candidate. A missing/corrupt receipt causes regeneration. The directory as a whole is not transactional: use the command exit status and successful validate output, not the presence of a prior timeline or `.blend`, to determine success. Do not edit sources or run multiple writers against the same audit directory during a build. This change does not introduce automatic cache garbage collection.

### Regression coverage and remaining limits

```bash
cd apps/blender-bridge && env PYTHONDONTWRITEBYTECODE=1 env python3 -m unittest discover -s tests -t . -v
```

Tests use synthetic audio and real FFmpeg/vtx validation for relocated dependencies, multiple sources and volumes, delayed nonzero source offsets, actual trimming, whole/fallback behavior, all-silent audio, content replacement with preserved timestamps, corrupt-cache repair, forced-encoder failure preservation and production timeline overwrite rejection. Additional boundary tests cover detector failure, subframe merging and cross-drive fallback. Set `VTX_AUDIT_TEST_OUTPUT` to keep fixtures/logs; otherwise temporary fixtures are cleaned. FFmpeg-dependent tests explicitly skip when unavailable; a skipped run is not acceptance.

Blender playback is a separate boundary. In particular, `json2blend.py` loop expansion with nonzero source offsets and `verify_blend.py`'s limited audio-field checks still require focused Blender tests. Retaining fields in JSON does not certify Blender playback equivalence. Linux/macOS, multiwriter behavior and long-form projects have not been established by the Windows regression.

## 用户手剪回读协议

用户在 preview.blend 里手剪（平移/裁剪/换轨）后，回读**不得直接采信 Blender 读数**，必须走实证链：

1. `blend2json.py` 重读手剪结果 → 产物过 `vtx validate`；
2. 按新切点**音频拼接**（导出对应窗口 wav）；
3. 送 **Qwen3-ASR**（sidecar 127.0.0.1:18085，language 全称 Chinese）转写；
4. 与**用户权威文本**逐句对照——用户说了算的句子清单优先于任何代理回读推断；
5. 切点按**能量谷检测微调**（ASR 句边界附近的静音谷，样本级定位）。

两处已实证的系统性偏差（须防）：

- **吸附手放值当精确切点**：strip 边缘读数是用户粗放位置，非精确切点——须 ASR 句首/能量谷校正后再定切；
- **头段语义误判**：seg 头填充词/重复废读（「一个」说两遍）代理回读易误判语义，须以用户权威文本裁定（净读 vs 废读）。

另：**用户手放值须做 D-A 恒等修正**——aroll 画面 src 与 voice src 是映射关系（dst−src 恒等），手剪只动了画面侧时须换算回 voice 侧，否则口型漂移。总纲见工作区 `~/文档/04_bilibili视频/_library/reports/编排方法论-科技口播视频.md` §六。
