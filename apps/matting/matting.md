# 抠像与背景合成（matting）· 真人实拍场景
> **使用者向**：把实拍真人从原背景中抠出（带 alpha 通道），再与背景图/背景视频合成。本文为 app 专属全流程文档（v2 自内核 docs/skill/references/ 迁入本目录）；合成侧规则见内核 `docs/skill/references/edit.md`，路由索引见内核 `docs/skill/SKILL.md`。
## 场景与路线选型
| 路线 | 位置 | 适用 | 状态 |
|---|---|---|---|
| **达芬奇 Magic Mask（主力）** | `apps/matting/resolve_matte/`（本目录） | 真人实拍抠像 → 带 alpha ProRes4444 导出，质量高 | 已验收（matte_auto_v8/v9 全流程跑通） |
| 自研 BiRefNet_HR + MatAnyone2（备用） | `apps/matting/`（本目录根） | 无达芬奇 Studio 时的兜底 | 可用，官方标注质量差、慢 |

**选型结论：默认走 resolve_matte**；旧链仅在无达芬奇环境时考虑。
## 路线一：resolve_matte（达芬奇 Magic Mask，默认）
前置：DaVinci Resolve 21 **Studio**（Linux）、空闲显存 ≥15GB（下限 13G 放行）。完整阶段表/坐标系标定/踩坑实录读本目录 `resolve_matte/README.md`（动手前必读）。
```bash
cd <video_tools>/apps/matting/resolve_matte
env bash matte_pipeline.sh matte_auto_v9    # 一键：清显存→建工程→9轨抠像→等演算→导出
SKIP_GUARD=1 env bash matte_pipeline.sh proj   # 达芬奇已就绪，跳过重启
SKIP_OPS=1    env bash matte_pipeline.sh proj   # 仅跳过鼠标序列，仍建工程/导入并导出，不保证无改动重导出
```
产物：`out/alpha_v*/matte_V{1..9}.mov`——**ProRes 4444 yuva444p12le 带 alpha**，每轨一段真人前景。
关键约束（细节全在 README 踩坑实录，此处只列影响排期的）：
- 当前序列按 **9 轨 grid 布局 + 固定卡位点**（`card_points.json`）组织，换时间线布局必须重新核对映射。
- Magic Mask 演算期间达芬奇**抓住指针**，等待期间禁止任何鼠标操作。
- 渲染只能写家目录；每轨单独 MarkIn/MarkOut；阿尔法三件套（ExportAlpha + AlphaMode=1 + ProRes4444）缺一即出废片。
## 路线二：自研链（备用）
BiRefNet_HR@2048 首帧 mask → MatAnyone2 视频抠像 → guided filter 收边。依赖默认关闭；`bash apps/matting/init.sh` 仅探测已有环境、克隆与权重，旧 `--download` 为 BLOCKED。准备与许可边界见本目录 [README](README.md)，实测记录见 `docs/测试报告-背景替换与H3重绘.md`。
## 合成侧：把带 alpha 前景放进成片
1. **背景图**：`python3 <video_tools>/apps/imgen/imgen_cli.py gen "<prompt>" --out <项目> --kind bg4k --yes` → 恰好 3840×2160 RGB PNG 落 `assets/bg/`（硬接口契约见内核 `docs/skill/references/edit.md`「三大生成模块」节）。
2. **首选机制：timeline.json 多轨叠加**（内核 `docs/skill/references/edit.md` 契约）——背景素材放底层轨（aroll/broll），matte_V*.mov 带 alpha 前景放**上层轨**（数组顺序＝图层顺序，末位最上），几何/透明度用 clip 的 scale/x/y/opacity。
3. **手写 ffmpeg 合成时守内核 `docs/skill/references/edit.md` 坑表三条**（2026-09-13 matte×背景实锤）：
   - `overlay` 输入序 `[底层][上层]`，**带 alpha 的一律放第二位**——同尺寸输入放反不报错，但前景被背景整帧盖住（输出「没有人」）。
   - 上层流要 `setpts=PTS+{in帧}/{fps}/TB` 时移 + `enable='between(t,...)'` 双保险，否则从 t=0 播放。
   - 透明中间片只用 ProRes 4444 或 PNG 序列；本机 VP9 (`yuva420p`) alpha 会丢。
4. 验收抠像产物：ffprobe 帧数逐轨核对 + alpha 平面 YMIN/YMAX（12bit 满程 4095）+ 末尾 2s alphaMax>0（防渲染错轨）。
## 与产线的衔接
- 抠像发生在 **③ 实拍与素材**阶段之后、⑤ timeline 编排之前：matte 产物作为普通视频源进 `assets/`，由 timeline 引用。
- 原理动画/数据展示不需要抠像（直接 gfx 页透明底叠加）；抠像只服务「真人出现在新背景/新场景前」的镜头。
