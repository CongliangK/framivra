# video_tools —— 视频制作工具权威仓

**一句话**：B 站视频产线全部可复用**程序本体的权威位置**——vtx 帧精确渲染 CLI + 采集/音频/脚手架脚本 + Blender 预览桥 + 抠像/背景替换链 + A-roll 素材工具，git 管理，大模型能力默认关闭。

## 定位（2026-09-10 升级：从收编快照 → 权威本体）

本仓是视频工具的**唯一权威本体**（D40）：

- `~/.agents/skills/video/` 中的 `vtx/`、`scripts/`、`references/`、`assets/`、`SKILL.md` 全部是指向本仓的**符号链接**——skill 的说明文档与工作流（`skill/`）随本仓分发与版本化，live 位置零实体，历史调用路径（`~/.agents/skills/video/vtx/vtx.py` 等）全部继续可用。
- 修改任何程序或 skill 文档一律改本仓，live 侧零改动（链接自动跟随）。

## 目录结构

| 目录 | 内容 | 说明 |
|---|---|---|
| `skill/` | **video skill 全部文档与模板**（工作流载体，随仓分享）：SKILL.md 路由索引 + references/{edit,gfx,research}.md 三领域完整文档 + assets/（起手模板 + gfx 实例页 + 调研模板） | `~/.agents/skills/video/` 内全部内容为指向本目录（及根级 vtx/、scripts/）的符号链接 |
| `vtx/` | **vtx 渲染 CLI**：vtx/ 包：vtx.py 入口（validate/audit/prepare/gfx/video/audio/mux/qc + --draft/--gpu/seg 缓存/两遍静态增益响度）+ infra/encoders/composite/qc/stages 模块+ timeline.schema.json + preview.html + models/（rnnoise）+ 历史版本回滚锚在 git 历史 | 唯一渲染入口；自定位无硬编码路径；零外部 Python 依赖 |
| `scripts/` | 14 件脚本：new_project.sh（脚手架）、sync_audio.py（双系统 GCC-PHAT 同步）、denoise_voice.py（D25 降噪链）、bilibili.py / github_repos.py / score_topic.py（research 采集与评分）、scroll-shoot.mjs / fetch-site-assets.py / dump-computed-styles.mjs（设计研究）+ **voice-ime 收编五件（D41）**：post_loudness.sh（响度限幅兜底）、check_4k.sh（4K 投稿版 9 项断言）、bed_static_duck.py（bed 静态分段避让）、repack_voice.py（实录 take 替换 TTS 占位）、content_gates.py（内容级验证门 G1/G2/G3/G7/G10+项目门） | skill 内符号链接指到此处 |
| `blender_bridge/` | timeline.json ↔ Blender VSE 双向预览桥：json2blend / verify_blend / blend2json / subs_alpha / **audit_bundle.py**（BGM 分轨审计包五步链）+ README（VSE API 坑表） | 权威副本（`04_bilibili视频/_library/blender_bridge/` 退役为指针） |
| `matting/` | 抠像/背景替换链 runner：run_birefnet_hr.py（首帧 mask）、run_matanyone2.py（视频抠像）、run_matanyone.py（v1）、post_h3.sh、run_full_bg.sh + docs/ 报告 | GPU 推理，需 conda env 与权重（见 matting/README.md） |
| `aroll/` | A-roll 素材工具：aup3_extract.py（Audacity .aup3 解析→提 WAV）、trim_takes.py（VAD 掐头去尾）+ docs/ 全链流程手册 | 配合 `_library/reports/A-roll实拍素材处理流程` 使用 |
| `asr/` | **Qwen3-ASR + ForcedAligner 字幕产线**：sidecar（18085）转写→字级时间戳→规范化断句→timeline subtitles[] 合并/独立 SRT（asr_cli.py） | 默认关闭；`bash asr/init.sh` 后按 asr/README.md 开启 |
| `tts/` | **IndexTTS-2.5 / edge-tts 口播占位语音产线**：spec.json 驱动多窗合成 + atempo 贴预算 + 峰值归一，产物按 repack_voice.py 既有约定落位（tts_cli.py） | 默认关闭；`bash tts/init.sh` 后按 tts/README.md 开启 |
| `imgen/` | **速创 API 生图 + cover-crop 后处理**：nano/gpt-image 异步提交轮询→lanczos 精确 3840×2160/1920×1080/1080×1920，直达 matting 抠像链与口播底图（imgen_cli.py） | 默认关闭；0.1 元/张；`bash imgen/init.sh` 配 key |
| `init.sh` | 依赖自检 + 生成 config.local.json（幂等；大模型默认关闭） | |
| `config.example.json` | 本地能力开关模板（comfyui/asr/tts/imgen 默认全关） | asr/tts/imgen 已接线（读取各自模块），comfyui 仍为声明 |

## 快速开始

```bash
python3 init.py          # 跨平台初始化：依赖自检 + 安装指引 + 生成 config.local.json（幂等）
                         # Linux 也可 bash init.sh（薄包装）；加 --fix 自动安装缺失基础依赖；--json 机器可读
python3 vtx/vtx.py --help                       # 渲染 CLI
bash scripts/new_project.sh <项目目录> [时长秒]   # 新项目脚手架
blender --background --factory-startup --python blender_bridge/json2blend.py -- <项目>/timeline.json <项目>/preview.blend   # Blender 预览工程（json2blend/verify_blend 须由 blender 解释器运行，python3 直跑会 ModuleNotFoundError）
bash asr/init.sh                                # 字幕产线初始化（模型/解释器探测，幂等）
bash tts/init.sh                                # TTS 产线初始化（IndexTTS repo/edge-tts 探测，幂等）
bash imgen/init.sh                              # 生图产线初始化（key/requests 探测，秒级幂等）
```

## 跨平台初始化（Linux / macOS / Windows）

`init.py` 按五段自检：基础依赖（python≥3.9、ffmpeg/ffprobe、浏览器）→ 编码能力（libfdk_aac、NVENC，缺则 vtx 自动回落并告警）→ 可选工具（blender/sox）→ 程序自检 → 大模型能力（默认关闭）。缺失项打印**当前 OS 的安装指引**（winget/brew/dnf/apt/pacman），`--fix` 直接尝试安装。

- **浏览器**：vtx 的 gfx 帧截图跨平台定位 Chrome/Chromium/Edge（PATH → 标准安装路径 → 环境变量 `VTX_CHROME` 指定可执行文件兜底）。
- **libfdk_aac**：正式封装用它（D31 坑位：原生 aac 对真人素材真峰过冲 +8.5dB）。Linux 装 fdk 构建的 ffmpeg（RPM Fusion/deb-multimedia）；Windows 用 gyan.dev **full+** 或 BtbN 构建；缺时 vtx 回落原生 aac 并打印告警，交付须复测编码后解码 TP。
- **Windows 注意**：`new_project.sh` 等 bash 脚本需 Git Bash 或 WSL；vtx/blender_bridge 的 Python 部分原生可用。

## 依赖矩阵

| 层级 | 依赖 | 用途 | 状态 |
|---|---|---|---|
| 基础（必需） | python3≥3.9（标准库即可）、ffmpeg/ffprobe、Chrome/Chromium/Edge | vtx 渲染与 gfx 帧截图 | init.py 自检 |
| 基础（强烈建议） | libfdk_aac 构建 | mux 正式封装音质（D31） | init.py 自检，缺则回落+告警 |
| 可选 | blender | blender_bridge/ 预览桥 | init.py 自检 |
| 可选 | NVENC（NVIDIA GPU） | vtx --gpu 渲染 | 自动探测，不可用回落 x264 |
| 可选 | sox | 部分音频后处理 | init.py 自检 |
| **大模型（默认关闭）** | conda env comfyui + MatAnyone2/BiRefNet_HR 克隆与权重 | matting/ 抠像链 | **需手动配置**，见 `matting/README.md` |
| **大模型（默认关闭）** | ComfyUI 服务 127.0.0.1:8188 | 背景图生成（post_h3.sh） | **需手动配置** |
| **大模型（默认关闭）** | Qwen3-ASR sidecar 127.0.0.1:18085（qwen-asr 解释器 + Qwen3-ASR/ForcedAligner 模型） | asr/ 字幕产线：听写→字级时间戳→subtitles[] | **需手动配置**，见 `asr/README.md` |
| **大模型（默认关闭）** | IndexTTS-2.5 repo（11GB）+ conda indextts / edge-tts 兜底 | tts/ 口播占位语音产线 | **需手动配置**，见 `tts/README.md` |
| **大模型（默认关闭）** | 速创 API key（api.wuyinkeji.com，0.1 元/张）+ requests | imgen/ 背景图生成（bg4k/bg1080p/竖版） | **需手动配置**，见 `imgen/README.md` |

## 与其他位置的关系

- `~/.agents/skills/video/`：**live skill 挂载点**——目录内五项（SKILL.md、references/、assets/、vtx/、scripts/）全部为指向本仓的符号链接，本仓 `skill/` 即 skill 本体。
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
