# resolve_matte 全流程跑通记录（matte_auto_v8，2026-09-12/13）

## 流程
1. `vram_guard.py --project <名>` —— 关达芬奇/清显存（目标15G，下限13G放行）→ 启动 → 开工程
2. `prep.py <工程名>` —— 29.97fps/2048x1152 → 导9视频 → grid9 时间线 9 轨对齐 → 调色页
3. `ops_runner.py op_sequence.json` —— 137 步鼠标序列（9 轨 Magic Mask）
4. `export_alpha.py <工程名>` —— 逐轨 ProRes4444 带阿尔法导出 + ffprobe 校验

## 导出（v21 实测要点）
- **轨道切换必须在剪辑页**：交付页调 `SetTrackEnable` 返回 True 但不生效；
  正确姿势：`OpenPage("edit")` → 切轨道 → `OpenPage("deliver")` → 渲染。
- **in/out 必须每次单独设置**：`MarkIn=108000, MarkOut=108000+帧数-1`
  （108000 = 时间线起始时码 01:00:00:00 的帧基准），配 `"SelectAllFrames": False`。
- 渲染目标目录必须在家目录下（/tmp 会静默产出 1 帧或失败）。
- `SetRenderFormatAndCodec` 格式字段用扩展名 "mov"，codec 用值 "ProRes4444"；
  `GetRenderCodecs("QuickTime")` 返回空，要用 `GetRenderCodecs("mov")`。
- 校验：ffprobe 帧数 + alpha 平面 YMIN/YMAX（12bit，4095 满）；尾部 2s alphaMax>0 防止切轨失败。

## Magic Mask 演算关键机制（本次最大坑）
- **演算期间达芬奇抓住指针**：正向段完成后反向段还会继续跑几分钟，
  期间所有合成点击（XTEST）全部失效且指针"冻结"在演算按钮位置。
- `wait_tracking_done.py` 只等第一次 "completed" 会过早放行 ——
  需改为：见到 completed 后再等 15~20s 且无新 "Start tracking" 才算完成（待实现）。
- 卡片条按时长升序排列，标签乱序。**实测映射**：
  卡1→V1, 卡2→V5, 卡3→V2, 卡4→V3, 卡5→V9, 卡6→V4, 卡7→V8, 卡8→V6, 卡9→V7
  （卡 k 的片段时长递增）。任何"按轨道号点卡"的假设都不成立。

## 输出
`out/alpha_v8/matte_V{1..9}.mov`：prores yuva444p12le，帧数与各轨片段一致，
alpha 范围 [256,3773]/4095，尾部均有内容，逐帧 MD5 互不相同。
