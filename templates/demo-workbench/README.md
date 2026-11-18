# Framivra 主 Demo：可编辑时间线

同一支 30 秒 / 900 帧 / 1080p30 作品，以实际时间线串起普通 MP4 剪辑、帧驱动画面、透明叠层、原生 ASS 字幕、素材底声、配乐、音效、混音与成片 QC。HTML 入口共用 CDP/CEF，SVG 序列另有入口；两种准备方式使用同一动效源与剪辑表，不在一条时间线上混用两种 gfx 规格。

## 第一次出片

需要完整源码、Python ≥3.9（含 Pillow、NumPy）、Node.js ≥18、可编码 x264/AAC 且有 libass 的 FFmpeg/ffprobe，以及相应原生渲染器。按[安装指南](../../docs/guides/installation.md)准备 CDP 和字幕字体；Node.js 另备。四个工程字体用[公开字体锁](../demo-timeline-svg/README.md#实际依赖与字体)：可复用一键安装目录中已核对字节的文件，或单独运行原字体准备命令。准备工程不联网、不安装字体；原生字幕仍需要 FFmpeg/libass 能找到 **Noto Sans CJK SC**，仅把字体复制进工程并不能保证字幕选面。

以下从源码根执行，是**写操作**（生成媒体、缓存及成片）。`RUN` 必须是仓、用户 HOME 与缓存根之外新建的物理目录；`FONTS_DIR` 指向已有锁定字体目录。

```bash
set -euo pipefail
RUN=$(python3 -c 'import pathlib,tempfile; print(pathlib.Path(tempfile.mkdtemp(prefix="framivra-demo-")).resolve())')
export VTX_GFX=cdp
python3 scripts/demo_workbench.py prepare --format html --output "$RUN/project" --fonts-dir "${FONTS_DIR:?}"
python3 -m vtx -t "$RUN/project/timeline.json" subs check
python3 -m vtx -t "$RUN/project/timeline.json" all
python3 -m vtx -t "$RUN/project/timeline.json" qc --strict
```

看 `project/out/final.mp4`。本片没有口播，字幕长度/重叠需通过检查，voice 对齐 WARN 是已知不适用，不计作通过。运行环境与人为视听结论分开记录。

| 路线 | 选择与前提 |
|---|---|
| CDP（默认） | `--format html`，`VTX_GFX=cdp`，原生 Chromium 系浏览器；Windows 用产品认可的原生 Chrome/Edge。 |
| SVG | 新目录 `--format svg`；按 [Inkscape 原生依赖](../demo-timeline-svg/README.md#实际依赖与字体)准备。不支持 Windows。 |
| CEF（实验） | `--format html`，`VTX_GFX=cef`，[`VTX_CEF_WORKER` 指向重新构建的原生 worker](../../experiments/gfx_cef_worker/README.md#生产接线实验性-alpha)。不是一键安装依赖，不回落 CDP，不支持 Windows。 |

同平台原生实测才是资格；HTML 能打开、生成工程成功或已装工具都不等于全片通过。不同渲染器的字型边缘可能不同，不用跨后端逐字节相等代替字幕、人眼和帧时钟验证。

## 编辑与验收

在 DSH 打开生成的 `timeline.json`，定位到 **462f**（15.4 秒）。把「可移动提示」图形块和音效轨上同一帧的短音一起移到 **396f**（13.2 秒），再改第三条内联字幕；在侧栏先看编辑计划并确认保存，不直接改渲染缓存。用侧栏[合成帧预览](../../dsh-sidebar/README.md#首次配置与使用)对照旧、新位置（按提示授权），用正式 `all` 重渲，不能把旧 MP4 的播放位置联动当成当前时间线的渲染证据。

也可在隔离新目录自动完成该练习，按后端各自留证：

```bash
python3 scripts/demo_workbench_acceptance.py --backend cdp --output "$RUN/accept-cdp" --fonts-dir "${FONTS_DIR:?}"
```

该入口会写入隔离工程、原版和编辑后成片、严格 QC、媒体与事务证据；**不会**给用户已有工程授权或保存。首轮验证真实后端，编辑后用同一后端身份复用未改动的图形缓存并重新合成。CEF 首次验收可先显式设 `VTX_GFX_PAGES=1 VTX_CEF_PAIR_SWAP=0 VTX_GFX_SPLIT_FRAMES=0`，把资源配置和结果一起记录。后端缺依赖时明确失败，不把另一后端当作覆盖。本入口不替换[发行冻结验收](../../docs/releases.md#冻结验收与发布)、安装的短能力验收或 [portable-v2 纯 SVG 性能基线](../demo-timeline-svg/README.md#主性能基线)；新版的编排、字幕和媒体工作量都不同，耗时不能与旧数据混比。
