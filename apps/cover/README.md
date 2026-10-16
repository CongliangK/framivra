# cover · B 站投稿封面产线

v2 应用（P2a 批次 1 自 `scripts/make_cover.py` 迁入；manifest 见 `app.json`，兼容 symlink：`scripts/make_cover.py` → 本目录）。来源决策 D50（voice-ime 首期封面收编）。

## 用途

把「背景图 + 大标题/小字」合成为 B 站投稿封面（1920×1200 16:10），并产出四端裁切预览（pc_16x9 / thumb_384 / app_138 / app_43）与验证门（尺寸断言 + 安全区几何守卫 + 标题区背景亮度 YAVG≥115 + JPG ≤2MB）。子命令链 `all = prep-bg → render → previews → qc`，确定性优先可复现。规格依据与背景图生成（imgen）知识见 `templates/tech-talk/references/cover.md`。

## 依赖

- ffmpeg / ffprobe（裁切、JPG 压制、亮度测量、尺寸探测）
- Chrome/Chromium headless（render 截图：`google-chrome`/`google-chrome-stable`/`chromium-browser`/`chromium` 任一，或 `VTX_CHROME` 环境变量指定）
- Python 零第三方依赖（stdlib）
- 背景图生成不在本 app 内（付费动作人工授权，走 imgen app）

## 健康检查

`env python3 apps/cover/make_cover.py --help` 退出码 0（app.json health.command；等价经兼容旧路径 `scripts/make_cover.py --help`）。

## 开启步骤

```bash
# 项目根（或任意含 --out 目录权限的位置）
env python3 apps/cover/make_cover.py all --out <项目>/cover --bg-src <imgen原图.png> \
    --title "说话 就是 打字" --sub "Linux 开源 · 语音键盘同引擎"
# 分步：prep-bg / render / previews / qc 各自可单跑（qc 出 cover_qc.json 机器可读回执）
env python3 apps/cover/make_cover.py qc --dir <项目>/cover
```
