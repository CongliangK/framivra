# video_tools —— 视频制作工具权威仓

**一句话**：B 站视频产线全部可复用**程序本体的权威位置**——vtx 帧精确渲染 CLI + 采集/音频/脚手架脚本 + Blender 预览桥 + 抠像/背景替换链 + A-roll 素材工具，git 管理，大模型能力默认关闭。

## 定位（2026-09-10 升级：从收编快照 → 权威本体）

本仓是视频工具的**唯一权威本体**（D40）：

- `~/.agents/skills/video/` 中的 `vtx/` 与 `scripts/` 是指向本仓的**符号链接**——skill 瘦身为「纯文本说明（SKILL.md + references/）+ 模板资产（assets/）」，历史调用路径（`~/.agents/skills/video/vtx/vtx.py`）全部继续可用。
- 修改任何程序一律改本仓，skill 侧零改动（链接自动跟随）。
- skill 的文档（references/edit.md·gfx.md·research.md）权威位置仍在 skill 内；本仓不再保留文档副本，避免双源漂移。

## 目录结构

| 目录 | 内容 | 说明 |
|---|---|---|
| `vtx/` | **vtx 渲染 CLI**：vtx.py（validate/audit/prepare/gfx/video/audio/mux/qc + --draft/--gpu/seg 缓存/两遍静态增益响度）+ timeline.schema.json + preview.html + models/（rnnoise）+ 历史回滚锚（vtx.py.bak_v35/36） | 唯一渲染入口；自定位无硬编码路径；零外部 Python 依赖 |
| `scripts/` | 9 件脚本：new_project.sh（脚手架）、sync_audio.py（双系统 GCC-PHAT 同步）、denoise_voice.py（D25 降噪链）、bilibili.py / github_repos.py / score_topic.py（research 采集与评分）、scroll-shoot.mjs / fetch-site-assets.py / dump-computed-styles.mjs（设计研究） | skill 内符号链接指到此处 |
| `blender_bridge/` | timeline.json ↔ Blender VSE 双向预览桥：json2blend / verify_blend / blend2json / subs_alpha / **audit_bundle.py**（BGM 分轨审计包五步链）+ README（VSE API 坑表） | 权威副本（`04_bilibili视频/_library/blender_bridge/` 退役为指针） |
| `matting/` | 抠像/背景替换链 runner：run_birefnet_hr.py（首帧 mask）、run_matanyone2.py（视频抠像）、run_matanyone.py（v1）、post_h3.sh、run_full_bg.sh + docs/ 报告 | GPU 推理，需 conda env 与权重（见 matting/README.md） |
| `aroll/` | A-roll 素材工具：aup3_extract.py（Audacity .aup3 解析→提 WAV）、trim_takes.py（VAD 掐头去尾）+ docs/ 全链流程手册 | 配合 `_library/reports/A-roll实拍素材处理流程` 使用 |
| `init.sh` | 依赖自检 + 生成 config.local.json（幂等；大模型默认关闭） | |
| `config.example.json` | 本地能力开关模板（comfyui/asr/tts 默认全关） | 声明式预留，脚本尚未读取 |

## 快速开始

```bash
bash init.sh                                    # 依赖自检 + 生成 config.local.json（幂等）
python3 vtx/vtx.py --help                       # 渲染 CLI
bash scripts/new_project.sh <项目目录> [时长秒]   # 新项目脚手架
python3 blender_bridge/json2blend.py -t <项目>/timeline.json   # Blender 预览工程
```

## 依赖矩阵

| 层级 | 依赖 | 用途 | 状态 |
|---|---|---|---|
| 基础（必需） | python3（标准库即可）、ffmpeg/ffprobe、google-chrome | vtx 渲染与 gfx 帧截图 | init.sh 自检 |
| 可选 | blender | blender_bridge/ 预览桥 | init.sh 自检 |
| 可选 | NVENC（NVIDIA GPU） | vtx --gpu 渲染 | 自动探测，不可用回落 x264 |
| 可选 | sox | 部分音频后处理 | init.sh 自检 |
| **大模型（默认关闭）** | conda env comfyui + MatAnyone2/BiRefNet_HR 克隆与权重 | matting/ 抠像链 | **需手动配置**，见 `matting/README.md` |
| **大模型（默认关闭）** | ComfyUI 服务 127.0.0.1:8188 | 背景图生成（post_h3.sh） | **需手动配置** |
| **大模型（默认关闭）** | Qwen3-ASR sidecar 127.0.0.1:18081 | A-roll 文本配对 / 听写 | **需手动配置** |

## 与其他位置的关系

- `~/.agents/skills/video/`：**瘦身后的 skill**（SKILL.md 索引 + references/ 三文档 + assets/ 模板），其 `vtx/`、`scripts/` 为指向本仓的符号链接。
- `~/文档/04_bilibili视频/`：视频工作区（各期项目、素材、缓存、报告），不是 git 仓库；项目绑定脚本（make_subs.py、repack_voice.py、retimeline_v3x 等）留在工作区各项目内，未收编。
- `_library/blender_bridge/`：已由本仓 `blender_bridge/` 取代为权威；原位置保留为兼容入口（工作区历史脚本仍引用其路径）。
- 音频工具见姊妹仓：`~/文档/01_活跃项目/music_tools/`。
- 推送到服务器 Gitea（私有）时：

```bash
git remote add origin <你的 Gitea 地址>/video_tools.git
git push -u origin main
```

## 深度测试基线（2026-09-10，迁移即测试，全部通过）

- 合成项目 vtx 全链：validate→prepare→video→audio→mux→qc **QC 9/9**（切点帧比对 0.03-0.04/255、-14.1 LUFS）；`--draft --frames` NVENC 草稿链验证。
- 真实项目回归（voice-ime v3.9 冻结 timeline 在 `/tmp` 链接树副本上，**live 原位零写入**）：validate 37 源/69 clips/150 字幕 5.2s 通过；qc **74/75 与 FREEZE v3.9 完全一致**（唯一 ✗ = 已证伪的 c4_mini 探针假阴性）；timeline md5 `9ce6ec43…`、final.mp4 md5 `ae9389ef…` 与冻结记录相符。
- blender_bridge：json2blend+verify_blend **556/556、129 条**（=D39 基线精确复现）。
- 组件矩阵：new_project 脚手架契约校验、全脚本 py_compile/bash -n/node --check、init.sh 幂等两次零差异。
- 迁移附带修复：vtx.py timeline 路径改 `os.path.abspath`（防符号链接穿透把回执写到 live 项目，已回归验证）；aroll/README 失效引用修正。

## 历史副本纪律

- `_library/bg_test/works/`（matting 四件）与 `voice-ime/cache/aup3_extract/`（aroll 两件）与本体**当前逐字节一致**，但属冻结历史快照：**修改一律改本仓**，勿直接改工作区副本（不进 git、不影响生效位置）。
- `matting/run_full_bg.sh` 与 `post_h3.sh` 为 bg_test 历史运行器，内含本机绝对路径（未参数化），换机/换项目需手改——属收编原样保留，非通用脚本。
