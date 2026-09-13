# runbook R5/R6/R7 · 发布链 / BGM 分轨审计 / 封面（tech-talk 产线配方）

> **tech-talk 产线知识**：自内核 `skill/references/runbook.md` R5/R6/R7 三节原文迁入——发布链/BGM/封面属产线配方（各依赖具体 app 组合：publish / blender-bridge / cover+imgen）。机制侧配方 R1/R2/R2s/R3/R4 与棒次协议仍在内核 runbook。
> `<项目>` = 项目目录占位（下文用真实项目 voice-ime 作示例）；`<VTX>` = `~/.agents/skills/video/vtx/vtx.py`（符号链接，权威本体 video_tools 仓 `vtx/`）；`<video_tools>` = video_tools 仓根。

## R5 · 发布链（4K 投稿版 + 冻结）

**适用场景**：母带 out/final.mp4 已过全部门，出投稿版并冻结。

```bash
cd ~/文档/04_bilibili视频/voice-ime

# 1) 4K 投稿版导出（方案三：8 段并行 nvenc p4，段内 lanczos 放大，concat -c copy 拼接 + aac 直通）
env python3 $VIDEO_TOOLS/apps/publish/export_4k.py -t timeline.json

# 2) 4K 断言（14 项：分辨率/帧率/h264 High/yuv420p/码率 cap/时长+帧数/aac/I 间隔/faststart）
bash $VIDEO_TOOLS/apps/publish/check_4k.sh out/final_4k.mp4

#    x264 软件链（lanczos + libx264 medium 单进程，--fallback-x264）为回退路径与画质对照基线
#    （NVENC 探测失败时 export_4k.py 显式告警后自动整链回落，不静默）；画质对照门
#    compare_4k_quality.py：PSNR ≥42dB / SSIM ≥0.99（2026-09-13 定版实测 10 帧 PSNR min 49.45dB / SSIM min 0.993247 全达标）
env python3 $VIDEO_TOOLS/apps/publish/export_4k.py -t timeline.json --fallback-x264 -o out/final_4k_x264_ref.mp4
env python3 $VIDEO_TOOLS/apps/publish/compare_4k_quality.py out/final_4k.mp4 out/final_4k_x264_ref.mp4

# 3) 冻结三件套：
#    out/FREEZE.json（md5 清单，final.mp4+final_4k.mp4+timeline）
#    CHANGELOG.md 变更单（版本号/改动/验证门结果）
#    timeline.<版本号>-frozen.json（冻结时间线快照）
# 4) 发布物料：PUBLISH-PACK.md（参数+简介+备答）
```

**耗时基准**：4K 导出 ~66s（方案三 2026-09-13 实测 65.68s = 段渲染 62.8s + concat 2.1s，8 段并行 nvenc p4；段缓存全命中重跑时秒级）；check_4k 秒级。〔方案三前：x264 软件链单进程 ~2.5min〕

**必跑验证门**：check_4k 全 PASS（任一 FAIL 退出码 1）。码率门覆盖用 `BITRATE_CAP_MBPS=20.5 bash check_4k.sh …`。

**已知坑位**：
- **冻结纪律**：FREEZE 记录的 md5 必须与当轮产物一致；后续任何改动（哪怕只重导 4K）都要更新 FREEZE 对应条目（final_4k md5 会变、final.mp4 不变时只改一处并注明）。
- AI 不手写 ffmpeg **剪辑**命令；此处的 4K 重编码是发布转码不是剪辑，属白名单用法（剪辑永远走 timeline+vtx）。

---

## R6 · BGM 挂载 / 音量改动后（分轨审计包）

**适用场景**：BGM 正式版挂载进 timeline、或已挂载 BGM 的音量/分轨有改动（用户裁定：正式版默认必做）。

**前置条件**：R1（或 R2）渲染链已跑完、验证门 PASS。

```bash
cd ~/文档/04_bilibili视频/voice-ime
python3 $VIDEO_TOOLS/apps/blender-bridge/audit_bundle.py -t timeline.json --prefix bgm2-
# 产物：<项目>/cache/audit_bgm/  ├ mp3/（逐源 320k 试听件，内容校验缓存）
#                               ├ timeline.json（逐轨一列审计时间线）
#                               └ audit.blend（Blender 审计工程，交用户逐轨静音/独听）
```

**重跑规则**：分轨或音量改动后**重跑即可**——MP3 按源内容和转码规格校验缓存；`--force` 强制全量重转。草稿/内部迭代轮不要求，正式版必做（人机协作交付门）。`--whole` 保留所有原事件；默认切分的 loop/fade 回退和审听边界见 `docs/audio-audit.md`。

**耗时基准**：分钟级（逐轨 ffmpeg 转码 + json2blend + verify_blend）。

**必跑验证门**：audit_bundle 内置五步（转码→审计 timeline→vtx validate→audit.blend→verify_blend 结构校验）。

**已知坑位**：给用户的 Blender 用法话术与交付规程见 music skill「交付门：分轨审计包」节与 `apps/blender-bridge/README.md`。

---

## R7 · 封面制作/修改（B 站投稿封面，D50）

**适用场景**：新视频出投稿封面，或改封面文案/背景/排布重铺。规格依据（显示比例/安全区/文案法）与完整坑表 → `cover.md`（本包 references/）。

**前置条件**：背景图已生成并验收（imgen GPT 系端点，付费动作人工授权；提示词原则与选型见 `cover.md` §五）；大标题+小字两行文案已拍板。

```bash
# 一条龙：prep-bg(lanczos 精确画布) → render(HTML+Chrome 截图 PNG/JPG≤2MB) → previews(四端裁切) → qc(验证门)
env python3 $VIDEO_TOOLS/apps/cover/make_cover.py all --out <项目>/cover \
    --bg-src <项目>/assets/imgen/<UTC>-<n>.png \
    --title "说话 就是 打字" --sub "Linux 开源 · 语音键盘同引擎"

# 只改文案/排布重铺（bg_1920x1200.png 已在 <项目>/cover/ 内）：
env python3 $VIDEO_TOOLS/apps/cover/make_cover.py render --out <项目>/cover --title "…" --sub "…"
env python3 $VIDEO_TOOLS/apps/cover/make_cover.py previews --dir <项目>/cover
env python3 $VIDEO_TOOLS/apps/cover/make_cover.py qc --dir <项目>/cover
```

**产物契约**：`<项目>/cover/` = bg png + cover.html + cover.meta.json（参数自述）+ cover png/jpg（上传用 JPG）+ previews/{pc_16x9,thumb_384,app_138,app_43}.png + cover_qc.json。

**耗时基准**：全链 ~10s（voice-ime 实测：prep-bg ~2s + Chrome 截图 ~3s + 预览 ~3s + qc ~2s）。

**必跑验证门**：`qc` PASS（尺寸/安全区几何/YAVG≥115/JPG≤2MB，FAIL 退出码 1）+ thumb_384 缩略图大标题 1 秒可读（人审）+ 独立视觉子智能体 OCR 复检。

**已知坑位**：生图勿单独依赖 flare（对无害抽象画提示词有审核误杀两连拒记录）；sunburst 忽略 aspectRatio 出 1K 方图，prep-bg 兜底裁切（放大倍数自动警告）；Chrome headless 同输入渲染字节一致，可当幂等重铺工具；投稿时创作中心裁切预览框再自查一次。
