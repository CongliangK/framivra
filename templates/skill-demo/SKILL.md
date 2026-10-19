# skill-demo —— 虚拟歌手搞笑视频工作流范本（零依赖纯内核包）

> 与 tech-talk 平级的纯内核工作流示例骨架（v2 ARCHITECTURE §4.1 包形态；
> 无 frontmatter——挂载层负责元数据，本体纯文档）。**本包只用内核**：不依赖
> 任何 apps/ 与 plugins/（skill.json 的 uses_apps / uses_plugins /
> optional_plugins 全空）——虚拟歌手的歌声素材、字幕、图文均按「独立素材」
> 预备，直接进 timeline 由 vtx 内核渲染。

这里的「独立素材」指输入资产；纯 MG 创作的页面与模块边界另读 [HTML 创作组织](../../docs/skill/references/gfx.md#html-创作组织)，不从素材种类或角色数推导 HTML 数。

## 三阶段极简流程

1. **素材预备（包外，任意工具）**：歌声/口播音轨（wav，48kHz CFR 对齐）、画面
   素材（mp4，1920x1080 CFR30）、字幕文本。演示用 `bash gen_fixture.sh` 一键
   生成最小合法素材（testsrc2 + sine 的 v01.mp4，90 帧）。
2. **编排**：把素材登记进 `timeline.json`（sources → 视频轨/音频轨/gfx 轨/
   字幕；gfx 用帧驱动透明页如 `gfxA.html`），`env python3 vtx/vtx.py -t
   timeline.json validate` 过契约校验。
3. **渲染与验收**：`env python3 vtx/vtx.py -t timeline.json all` 一条链出
   `out/final.mp4`（含响度归一+限幅+烧字幕+QC）；`bash expected_check.sh`
   断言产物存在、帧数 90、QC 全 PASS（含 TP 项）。

## 声明

本包只用内核，零 apps/plugins 依赖——vtx 的 validate/渲染/混音响度链/QC（含
TP 判定）即产出可信成片的全部所需。需要生成能力（歌声合成、自动字幕）时按
apps 清单另接（见 ../../apps/ 与 ../tech-talk/ 的组合方式）。

`fixtures/` 四件是 v2 验收门 4（关停测试：apps+plugins 全禁仍能出片）的现成
道具——`gen_fixture.sh → validate → all → expected_check.sh` 在本目录实测可
复现（~秒级，产物落本目录 out/ 与 cache/，gitignored）。
