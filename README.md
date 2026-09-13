# video_tools —— 视频制作工具仓（v2 插件化架构）

> **本仓是什么**：B 站视频产线的程序本体权威仓。**内核** `vtx/` = timeline.json 契约 + 帧精确渲染 CLI（validate/diff/subs/audit/prepare/gfx/video/audio/mux/qc/gc/snapshot）+ 混音响度终链（-14 LUFS + limiter）+ 内容寻址缓存——关停它主链产物不可信，故永不可关。产线知识在 `skills/tech-talk/` 工作流包（科技口播六阶段 SOP + 调研/拍摄/封面/发布链配方）；机制契约文档在 `skill/`（路由索引 `skill/SKILL.md`）。git 管理，大模型能力默认关闭。

> **姊妹项目 Music Tools**：本仓专注「视频」产线。BGM / 配乐 / 床乐 / 音效 / 响度 QC / 分轨混音需求请移步姊妹仓 `~/文档/01_活跃项目/music_tools/`（本仓 `apps/bgm` 为路由声明）——music_tools 产出的 BGM 挂载进本仓 timeline.json 后，由 `apps/blender-bridge/audit_bundle.py` 生成分轨审计包供 Blender 逐轨裁决。

## 四层架构一页

```
vtx/            ① 内核（永不可关）：渲染链 + validate/qc + 响度终链 + 缓存 + pluginlib/api.py
skill/             ① 内核文档：SKILL.md 路由索引 + references/{edit,gfx,runbook}.md + assets/ 起手模板
apps/  (12 件)  ② 应用：产出独立素材不进 timeline 管线，关停不影响主链
plugins/ (2 件) ② 插件：扩展 timeline.json 词汇并钩进管线（denoise / duck-static + registry.json）
skills/         ③ 工作流包：tech-talk（科技口播制作范本；skill.json 声明 uses_apps/optional_plugins）
scripts/           内核工具残留：new_project.sh / gfx_golden.py / bench_cold.sh（+ 迁走件兼容 symlink）
vt.py              统一入口：doctor / apps|plugins|skills list|check / version / init / --fix-links
```

分层判据（关停测试）：关停后主链产物**不可信** → 内核；**仍能可信出一期片** → 可卸载（apps/plugins）。apps/plugins 每件自包含（app.json/plugin.json + 代码 + README + tests）。

## 快速开始（clone 即用，零安装步骤）

```bash
env python3 vt.py init                     # 依赖自检 + 安装指引 + 生成 config.local.json（幂等；--fix 自动装）
env python3 vt.py doctor                   # 三检：版本漂移 / apps+plugins manifest+health / skills 完好性
bash scripts/new_project.sh <项目目录> 30  # 新项目脚手架（目录 + 模板 timeline.json + schema）
env python3 vtx/vtx.py -t <项目>/timeline.json validate    # 渲染前校验门（必跑）
env python3 vtx/vtx.py -t <项目>/timeline.json all         # 全流程 → out/final.mp4（--until video 可截断）
```

可选产线初始化：`bash apps/asr/init.sh` / `bash apps/tts/init.sh` / `bash apps/imgen/init.sh`（幂等）；`bash apps/matting/init.sh --download`（抠像链权重）。live skill 挂载（`~/.agents/skills/video/`）为指向本仓的符号链接，`vt.py --fix-links` 可重建。

## 三层路由

**内核**：`skill/SKILL.md`（路由索引 + 迭代回路速查）→ `skill/references/edit.md`（timeline 契约 + vtx 全阶段 + 坑表）、`gfx.md`（帧驱动协议 + D22 设计铁律）、`runbook.md`（修改配方 R1-R4 + 棒次协议）。

**apps/**（12 件，一句话）：

| app | 职责 |
|---|---|
| `apps/asr/` | Qwen3-ASR + ForcedAligner 字幕产线（口播→subtitles[] 锚定），默认关闭 |
| `apps/tts/` | IndexTTS-2.5 / edge-tts 口播占位语音（spec.json 贴预算），默认关闭 |
| `apps/imgen/` | 速创 API 生图 + cover-crop 后处理（bg4k 硬接口 3840×2160），默认关闭 |
| `apps/matting/` | 抠像/背景替换两路线（resolve_matte 达芬奇主力 + 自研链备用；专属文档 matting.md） |
| `apps/aroll/` | A-roll 素材工具（aup3 提取 / VAD 掐头去尾） |
| `apps/cover/` | B 站投稿封面产线（make_cover.py 一条龙 + QC 门，D50） |
| `apps/research/` | 选题调研 6 件（bilibili / github_repos / score_topic / 设计研究三件） |
| `apps/blender-bridge/` | timeline ↔ Blender VSE 双向预览桥 + BGM 分轨审计包 |
| `apps/publish/` | 4K 投稿版发布链（export_4k / check_4k / compare_4k_quality） |
| `apps/bgm/` | BGM 需求路由声明（本体 music_tools 姊妹仓，无可执行件） |
| `apps/gates/` | 发布前内容级复检（G1/G2/G7/G10 + 项目门；G3 TP 已上内核 qc） |
| `apps/sync/` | 双系统同步（GCC-PHAT）与实录 take 替换（管线前置素材工具） |

**plugins/**（2 件）：`plugins/denoise/`——口播降噪（`sources[]` 级键，prepare 源产物钩子）；`plugins/duck-static/`——床乐静态分段避让（`duck_strategy` 轨级键，音轨事件内存变换）。协议 v1 与 registry.json 见 `docs/ARCHITECTURE.md` §2。

**skills/**：`skills/tech-talk/`——科技口播 B 站视频制作范本（六阶段流程 / 音频支线 / 全链 SOP / 已验收模板资产），产线任务先读其 SKILL.md。

## 依赖矩阵

| 层级 | 依赖 | 用途 | 状态 |
|---|---|---|---|
| 基础（必需） | python3≥3.9（标准库即可）、ffmpeg/ffprobe、Chrome/Chromium/Edge | vtx 渲染与 gfx 帧截图 | vt.py init 自检 |
| 基础（强烈建议） | libfdk_aac 构建 | mux 正式封装音质 | 自检，缺则回落+告警（交付须复测解码 TP） |
| 可选 | blender / sox | blender-bridge 预览桥 / 部分音频后处理 | 自检 |
| 可选 | NVENC（NVIDIA GPU） | `video --gpu` 渲染 | 自动探测，不可用回落 x264 |
| **大模型（默认关闭）** | conda env comfyui + MatAnyone2/BiRefNet_HR 权重 | apps/matting 自研抠像链 | `bash apps/matting/init.sh --download` |
| **大模型（默认关闭）** | Qwen3-ASR sidecar（18085） | apps/asr 字幕产线 | `bash apps/asr/init.sh` |
| **大模型（默认关闭）** | IndexTTS-2.5 repo（11GB 权重）+ conda indextts（edge-tts 兜底） | apps/tts 占位语音 | `bash apps/tts/init.sh` |
| 可选 | ComfyUI 服务 127.0.0.1:8188 | apps/matting 背景图生成（post_h3.sh 消费其输出） | `bash apps/matting/comfyui_init.sh` |
| **大模型（默认关闭）** | 速创 API key（0.1 元/张）+ requests | apps/imgen 生图 | `bash apps/imgen/init.sh` 配 key |
| **大模型（默认关闭）** | resemble-enhance 权重 681MB + conda denoise env | plugins/denoise 口播降噪 | `bash plugins/denoise/denoise_init.sh --download` |

## 开发者文档与项目布局

- 架构设计权威：`docs/ARCHITECTURE.md`（v2 四层架构 / 插件协议 / 迁移映射 / 验收门）。
- 开发者文档：`docs/DEV.md`（演进史、性能基准、坑位破案详录）、`cache-provenance.md`（缓存与回执溯源）、`canvas-export.md`（gfx Canvas 导出协议）、`audio-audit.md`（分轨审计包语义）、`post-loudness.md`（响度终链）。
- 测试：`bash scripts/run_all_tests.sh`（`python3 -m unittest discover -s tests`；集成用例需 VTX_TEST_CHROME/VTX_TEST_FFMPEG）。
- 工作区（非 git 仓）：`~/文档/04_bilibili视频/` 各期项目 + `_library/`（方法论/资产/blender_bridge 挂载）——**修改一律改本仓**，工作区历史副本为冻结快照。音频工具见姊妹仓 `~/文档/01_活跃项目/music_tools/`。推送 Gitea（私有）：`git remote add origin <你的 Gitea 地址>/video_tools.git && git push -u origin main`。
