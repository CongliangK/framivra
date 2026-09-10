# blender_bridge — timeline.json → Blender VSE 预览前端

> 建立于 2026-08-29。路线拍板：不用 MCP（CLI/bpy 直连更自由）、不用外部直写 .blend（无格式规范），
> 用 `blender --background --python` 生成。回写（Blender→json）未做，属下一步。

## 用法（对一个项目生成预览工程）

```bash
cd ~/文档/04_bilibili视频/_library/blender_bridge
python3 subs_alpha.py <项目>/timeline.json          # ① 字幕透明轨（需先有 cache/subs.ass，vtx 字幕阶段产物）
blender --background --factory-startup --python json2blend.py -- <项目>/timeline.json <项目>/preview.blend   # ② 生成
blender --background --python verify_blend.py -- <项目>/timeline.json <项目>/preview.blend                   # ③ 结构校验（退出码 0=全过）
blender <项目>/preview.blend                        # ④ 打开预览（空格播放，拖拽刮擦）
```

依赖：Blender 5.2.1 LTS（`~/.local/opt/blender-5.2.1-linux-x64`，符号链接 `~/.local/bin/blender`）、ffmpeg/ffprobe。

## 产物状态（2026-08-29）

| 项目 | blend | 校验 | 样帧 PSNR vs 终渲 |
|---|---|---|---|
| poc2（24fps 30s 测试） | `_archive/poc2/preview.blend` | 103/103 | 35.6 dB @f120 |
| voice-ime（30fps 600s） | `voice-ime/preview.blend` | 410/410 | 41.2 dB @f6000 |
| voice-ime v3.3（30fps 653s，2026-09-08） | `voice-ime/preview.blend` | 520/0（122 条） | — |
| voice-ime 终版（30fps 653.13s/19594f，D31 2026-09-08） | `voice-ime/preview.blend`（重生成） | 524/524 | 43.6 dB @f3000 |

字幕透明轨：`<项目>/cache/subs_alpha.mov`（ProRes 4444 alpha；voice-ime 1.24GB/18000帧/2min14s 渲染）。

## 语义映射（与 vtx.py 源码对齐）

- video_tracks 数组序＝图层序：音频轨占通道 1..A（每轨一条），视频轨 [0] 最底从 A+1 起，字幕轨最顶
- clip：`frame_final_start=dst_in`，`frame_final_end=dst_in+dur`（右开区间语义一致）；源偏移＝src_in
- 全屏 clip：crop169（16:9 中心裁切）+ 等比拉满（vtx L313）
- 窗口 clip（scale/x/y 任一存在）：scale 相对**源原始尺寸**（vtx L306-311，非画布 fit）、x/y=左上角像素
- gfx：读 vtx 帧缓存 `cache/<stem>/f*.png` 图像序列
- 字幕：**不经 Blender 文字渲染**——vtx 的 subs.ass 经 `alphamerge 透明画布 + ass:alpha=1` 渲成
  透明视频轨叠加，与终渲像素同源（libass 同库同字体）
- fade_in/out：blend_alpha/volume 关键帧（插值取默认，预览近似）
- 色彩：view_transform=Standard、FRAME_DROP 同步；已知与终渲有 ~2% 整体亮度差（色彩管理路径），预览可接受

## 本轮踩坑记录（Blender 5.2.1 VSE API）

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

## 回写桥 blend2json.py（2026-08-29 建成并测试通过）

用法：`blender --background --python blend2json.py -- <preview.blend> [out.json]`
（json 源取 scene["vtx_timeline"] 钩子；缺省输出 `<项目>/timeline.blendback.json`，不覆盖原文件）
回写面：视频条与音频条的**时间平移 / 左右缘裁剪（左缘=入点后移）/ 同族换轨**；
音频**整组**平移（loop 组无缝时）。产物需过 `python3 vtx.py -t <out> validate`。

保护规则（拒收或告警，均保留原值）：
- 视频：跨族换轨（movie↔gfx）、同轨重叠、素材 src 越界、越过画布尾、dst<0 → 拒收
- 音频：**换通道不回写**（轨名 voice/bed 驱动 ducking 与 audit 口播统计，串轨改语义）
- 音频 loop 组被部分拖动（组内缝隙）→ 不回写
- 音量/声像改动 → 告警不回写（带淡变事件的静态属性不可靠，已跳过比对）
- 字幕条（subs|ASS）移动 → 告警（字幕回写需改 subtitles 数组，未做）

测试记录：恒等回写两项目零改动结构相等 PASS；模拟 4 视频+3 音频操作+1 故意撞轨，
回写 7 项精确捕捉、1 项换通道正确告警，vtx validate 通过。
注意：Blender 不允许同通道重叠，拖动相撞会把条自动顶到上层通道——表现为换轨
（视频侧同族照收，音频/跨族会被上述规则拦下并提示）。

## 分轨审计包 audit_bundle.py（2026-09-10 建成，人机协作交付门）

用法：`python3 audit_bundle.py -t <项目>/timeline.json [--prefix bgm-] [--force]`
BGM 正式版默认必做（用户裁定，见 music skill「交付门：分轨审计包」节）：分轨 MP3 导出 →
审计 timeline（逐轨一列、生产音量原样、静音切分=只保留实际发声段、无 voice 轨）→
vtx validate → json2blend →
verify_blend 五步一键，产物落 `<项目>/cache/audit_bgm/{mp3/,timeline.json,audit.blend}`。
MP3 按源 mtime 自动失效重转（分轨重渲后重跑即可），`--force` 强制全量。首例=voice-ime
BGM v2 13 轨（`voice-ime/cache/bgm_audit/`，--mp3-dir 指到其 assets/bgm/v2/mp3）。
