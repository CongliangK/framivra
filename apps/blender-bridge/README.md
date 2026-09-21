# blender_bridge — timeline.json → Blender VSE 预览前端

> **v2 结构（P2a 批次 2 起本目录迁入 `apps/blender-bridge/`）**：manifest 见 `app.json`（kind=app；健康检查 = `audit_bundle.py --help`）；兼容 symlink：仓库根 `blender_bridge → apps/blender-bridge`（目录级，旧路径引用仍可达——music skill 跨仓引用的保命线，§6.1/§6.3）。随迁定位改写：audit_bundle.py 的 vtx 解析链重算为 **repo 根锚定**（自本文件向上找含 `vt.py`+`vtx/vtx.py` 的祖先；原「上级目录」在迁移后是 `apps/` 而非仓根，§6.2）；`tests/test_audio_audit.py` 随迁并清除 spec_from_file_location（app 根注入 + 普通 import，§1.4）。

> **使用者向**：Blender 预览桥用法与 VSE 坑表。历史校验记录与实测数据见 `../../docs/DEV.md`。

> 路线：不用 MCP（CLI/bpy 直连更自由）、不用外部直写 .blend（无格式规范），用 `blender --background --python` 生成。回写（Blender→json）由 blend2json.py 提供（见下）。

## 文件清单（用途）

| 件 | 用途 | 解释器 |
|---|---|---|
| `audit_bundle.py` | BGM 分轨审计包五步链（MP3 导出→审计 timeline→vtx validate→json2blend→verify_blend） | 系统 python3 |
| `json2blend.py` / `verify_blend.py` | timeline.json → preview.blend 生成 / 结构校验 | **blender**（`--python` 载入，python3 直跑 ModuleNotFoundError: bpy） |
| `blend2json.py` | 用户手剪回写桥（Blender→json，保护规则拒收危险改动） | **blender** |
| `subs_alpha.py` | 字幕透明轨（cache/subs.ass → ProRes 4444 alpha mov） | 系统 python3 |
| `bundle_session.py` | audit_bundle 的单次 blender 会话合并执行器（runpy 依次跑 json2blend/verify） | **blender** |
| `tests/` | audit 真 ffmpeg 回归（`test_audio_audit.py`，10 用例） | 系统 python3 |

## 依赖

- **app 级（manifest requires，doctor 可探测）**：ffmpeg / ffprobe（audit_bundle 逐源导出与内容检测，系统 python3 即可跑）。
- **blender 解释器（app.json `requires.binaries` 含 blender）**：json2blend / verify_blend / blend2json / bundle_session 须由 **blender** 运行（`blender --background --python <脚本>`）——依赖 bpy，系统 python3 无法 import。本机 Blender 5.2.1 LTS（`~/.local/opt/blender-5.2.1-linux-x64`，符号链接 `~/.local/bin/blender`）。

## 健康检查

`env python3 apps/blender-bridge/audit_bundle.py --help` 退出码 0（app.json health.command；系统 python3 面不涉 blender）。blend 件另走语法门（vt init 的 bridge_* 编译检查）与实际 blender 会话（audit 全链）。

## 用法（对一个项目生成预览工程）

```bash
cd apps/blender-bridge   # （旧路径 blender_bridge/ 经 symlink 同样可达；外部 `_library` 资产一律以工作区绝对路径引用，见文末编排方法论条目）
env python3 subs_alpha.py <项目>/timeline.json      # ① 字幕透明轨（需先有 cache/subs.ass，vtx 字幕阶段产物）
blender --background --factory-startup --python json2blend.py -- <项目>/timeline.json <项目>/preview.blend   # ② 生成
blender --background --python verify_blend.py -- <项目>/timeline.json <项目>/preview.blend                   # ③ 结构校验（退出码 0=全过）
blender <项目>/preview.blend                        # ④ 打开预览（空格播放，拖拽刮擦）
env python3 audit_bundle.py -t <项目>/timeline.json [--prefix bgm-]   # 分轨审计包五步链
```

**打开即视频编辑工作区**：json2blend 生成的 .blend 默认活动工作区=「Video Editing」——打开即是时间线+视频预览面板，无需手动切换工作区/场景。实现：以 Blender 官方 `Video_Editing` app template 的 startup.blend 为底（出厂启动文件不含该工作区，headless 下唯一可靠路径），清掉模板示例物件后在模板场景上建 VSE；找不到模板时回落原 Layout（不影响条目生成）。

依赖（app 级与 blender 解释器约束见顶部「依赖」节）。

## 历史校验记录

各项目 preview.blend 的 verify 校验通过数与样帧 PSNR 记录迁移至 `../../docs/DEV.md`（迁移即测试基线一节）。

字幕透明轨产物：`<项目>/cache/subs_alpha.mov`（ProRes 4444 alpha）。

## 语义映射（与 vtx 包源码对齐）

- video_tracks 数组序＝图层序：音频轨占通道 1..A（每轨一条），视频轨 [0] 最底从 A+1 起，字幕轨最顶
- clip：`frame_final_start=dst_in`，`frame_final_end=dst_in+dur`（右开区间语义一致）；源偏移＝src_in
- 全屏 clip：crop169（16:9 中心裁切）+ 等比拉满（vtx L313）
- 窗口 clip（scale/x/y 任一存在）：scale 相对**源原始尺寸**（vtx L306-311，非画布 fit）、x/y=左上角像素
- gfx：读 vtx 帧缓存 `cache/gfx/<内容寻址键>/f*.png` 图像序列（旧版 `cache/<stem>/` 布局作回落）
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

保护规则（拒收或告警，均保留原值）：
- 视频：跨族换轨（movie↔gfx）、同轨重叠、素材 src 越界、越过画布尾、dst<0 → 拒收
- 音频：**换通道不回写**（轨名 voice/bed 驱动 ducking 与 audit 口播统计，串轨改语义）
- 音频 loop 组被部分拖动（组内缝隙）→ 不回写
- 音量/声像改动 → 告警不回写（带淡变事件的静态属性不可靠，已跳过比对）
- 字幕条（subs|ASS）移动 → 告警（字幕回写需改 subtitles 数组，未做）

注意：Blender 不允许同通道重叠，拖动相撞会把条自动顶到上层通道——表现为换轨
（视频侧同族照收，音频/跨族会被上述规则拦下并提示）。

## 分轨审计包 audit_bundle.py（人机协作交付门）

事件映射、逐源导出、内容缓存、整段回退及验收边界见 [音频审计说明](../../docs/audio-audit.md)。审计是 duck/总混处理前的 MP3 试听；`--whole` 保留每个原事件，默认切分不会重启循环或淡变。

用法：`env python3 apps/blender-bridge/audit_bundle.py -t <项目>/timeline.json [--prefix bgm-] [--force]`
BGM 正式版默认必做（见 music skill「交付门：分轨审计包」节）：分轨 MP3 导出 →
审计 timeline（逐轨一列、生产音量原样、静音切分=只保留实际发声段、无 voice 轨）→
vtx validate → json2blend →
verify_blend 五步一键，产物落 `<项目>/cache/audit_bgm/{mp3/,timeline.json,audit.blend}`。
MP3 按源 mtime 自动失效重转（分轨重渲后重跑即可），`--force` 强制全量。

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
