# skill-demo —— 虚拟歌手搞笑视频工作流范本（零依赖纯内核包）

> 与 tech-talk 平级的纯内核工作流示例骨架（v2 ARCHITECTURE §4.1 包形态；
> 无 frontmatter——挂载层负责元数据，本体纯文档）。**本包只用内核**：不依赖
> 任何 apps/ 与 plugins/（skill.json 的 uses_apps / uses_plugins /
> optional_plugins 全空）——虚拟歌手的歌声素材、字幕、图文均按「独立素材」
> 预备，直接进 timeline 由 vtx 内核渲染。

这里的「独立素材」指输入资产；纯 MG 创作的页面与模块边界另读 [HTML 创作组织](../../docs/skill/references/gfx.md#html-创作组织)，不从素材种类或角色数推导 HTML 数。

## 三阶段极简流程

以下命令在本包目录执行（仓内为 `<video_tools>/templates/skill-demo/`）；
`<video_tools>` 指实际仓库根。复制本包到仓外后，验收命令仍用 `VTX_REPO`
指向该仓库根。现有验收脚本要求仓根路径不含空白，给 `VTX_REPO` 赋值加引号
也不能解除此限制。生成与验收会写入或替换本包的 `v01.mp4`、`cache/` 与 `out/`，
包括成片；执行前确认目录与已有产物。

1. **素材预备（包外，任意工具）**：歌声/口播音轨（wav，48kHz CFR 对齐）、画面
   素材（mp4，1920x1080 CFR30）、字幕文本。演示用 `bash gen_fixture.sh` 一键
   生成最小合法素材（testsrc2 + sine 的 v01.mp4，90 帧）。
2. **编排**：把素材登记进 `timeline.json`（sources → 视频轨/音频轨/gfx 轨/
   字幕；gfx 用帧驱动透明页如 `gfxA.html`），
   `env python3 "<video_tools>/vtx/vtx.py" -t timeline.json validate` 过契约校验。
3. **演示渲染与验收**：`env VTX_REPO="<video_tools>" bash expected_check.sh`
   自行执行 validate、完整 all 与 strict QC，生成 `out/final.mp4` 后断言产物存在、
   帧数 90、QC 全 PASS（含 TP 项）；无需先另跑 all。脚本的固定帧数断言仅用于
   本演示，自有作品的正式交付用内核 [完整链](../../docs/skill/references/runbook.md#正式交付链r1-r4-共用)。

## 声明

本包只用内核，零 apps/plugins 依赖——vtx 的 validate/渲染/混音响度链/QC（含
TP 判定）即产出可信成片的全部所需。需要生成能力（歌声合成、自动字幕）时按
apps 清单另接（见 ../../apps/ 与 ../tech-talk/ 的组合方式）。

本包根目录的 `gen_fixture.sh`、`gfxA.html`、`timeline.json`、`expected_check.sh`
是 v2 验收门 4（关停测试：apps+plugins 全禁仍能出片）的现成道具；
生成素材后按上方验收命令运行。它会实际渲染，并非只读检查；所需运行环境见
[README](../../README.md#快速开始clone-即用零安装步骤)，不据演示帧数承诺耗时。
