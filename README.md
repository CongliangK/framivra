# video_tools —— 视频制作工具权威仓
> **使用者向**：目录结构、依赖、快速开始。使用方法见 `skill/SKILL.md`；演进史与测试基线见 `docs/DEV.md`。

**一句话**：B 站视频产线全部可复用**程序本体的权威位置**——vtx 帧精确渲染 CLI + 采集/音频/脚手架脚本 + Blender 预览桥 + 抠像/背景替换链 + A-roll 素材工具，git 管理，大模型能力默认关闭。

> **姊妹项目 Music Tools**：本仓专注「视频」产线（渲染/剪辑/字幕/动效/抠像）。若你的需求是 **BGM / 配乐 / 床乐 / sting / 音效 / 响度 QC / 分轨审计**（确定性合成、公有领域经典改编、SF2/SFZ 音源、分轨混音母带），请移步姊妹仓 **`~/文档/01_活跃项目/music_tools/`**——两仓通常搭配使用：music_tools 产出的 BGM 挂载进本仓 timeline.json 后，由本仓 `blender_bridge/audit_bundle.py` 生成分轨审计包供 Blender 逐轨裁决。

## 定位
本仓是视频工具的**唯一权威本体**：`~/.agents/skills/video/` 中的 `vtx/`、`scripts/`、`references/`、`assets/`、`SKILL.md` 全部是指向本仓的**符号链接**（历史调用路径继续可用）。修改任何程序或 skill 文档一律改本仓，live 侧零改动（链接自动跟随）。

## 目录结构
| 目录 | 内容 | 说明 |
|---|---|---|
| `skill/` | **video skill 全部文档与模板**：SKILL.md 路由索引 + references/{edit,gfx,research,runbook}.md + assets/（起手模板 + gfx 实例页 + 调研模板） | `~/.agents/skills/video/` 内全部内容为指向本目录（及根级 vtx/、scripts/）的符号链接 |
| `vtx/` | **vtx 渲染 CLI**：vtx.py 入口（validate/audit/prepare/gfx/video/audio/mux/qc + --draft/--gpu/seg 缓存/两遍静态增益响度）+ infra/encoders/composite/qc/stages 模块 + timeline.schema.json + preview.html + models/（rnnoise） | 唯一渲染入口；自定位无硬编码路径；零外部 Python 依赖 |
| `scripts/` | 15 件脚本：new_project.sh（脚手架）、sync_audio.py（双系统 GCC-PHAT 同步）、denoise_voice.py（降噪链）、denoise_init.sh（降噪链依赖探测/下载）、bilibili.py / github_repos.py / score_topic.py（research 采集与评分）、scroll-shoot.mjs / fetch-site-assets.py / dump-computed-styles.mjs（设计研究）+ **五件验证门**：post_loudness.sh（响度限幅兜底）、check_4k.sh（4K 投稿版断言）、bed_static_duck.py（bed 静态分段避让）、repack_voice.py（实录 take 替换 TTS 占位）、content_gates.py（内容级验证门 G1/G2/G3/G7/G10+项目门） | skill 内符号链接指到此处 |
| `tests/` | PR#1 回归测试五件：unittest×2（test_cache_state / test_graphics_portability）+ cache 回归（run_cache_regression）+ 合成 E2E×2（run_e2e / verify_e2e） | `python3 -m unittest discover -s tests`；集成用例需 VTX_TEST_CHROME/VTX_TEST_FFMPEG |
| `blender_bridge/` | timeline.json ↔ Blender VSE 双向预览桥：json2blend / verify_blend / blend2json / subs_alpha / **audit_bundle.py**（BGM 分轨审计包五步链）+ README（VSE API 坑表） | 权威副本（`04_bilibili视频/_library/blender_bridge/` 退役为指针） |
| `matting/` | 抠像/背景替换链 runner：run_birefnet_hr.py、run_matanyone2.py、run_matanyone.py、post_h3.sh、run_full_bg.sh + init.sh / comfyui_init.sh（依赖探测/下载）+ docs/ 报告 | GPU 推理，需 conda env 与权重（见 matting/README.md） |
| `aroll/` | A-roll 素材工具：aup3_extract.py（Audacity .aup3 解析→提 WAV）、trim_takes.py（VAD 掐头去尾）+ docs/ 全链流程手册 | 配合 `_library/reports/A-roll实拍素材处理流程` 使用 |
| `asr/` | **Qwen3-ASR + ForcedAligner 字幕产线**：sidecar（18085）转写→字级时间戳→规范化断句→timeline subtitles[] 合并/独立 SRT（asr_cli.py） | 默认关闭；`bash asr/init.sh` 后按 asr/README.md 开启 |
| `tts/` | **IndexTTS-2.5 / edge-tts 口播占位语音产线**：spec.json 驱动多窗合成 + atempo 贴预算 + 峰值归一（tts_cli.py） | 默认关闭；`bash tts/init.sh` 后按 tts/README.md 开启 |
| `imgen/` | **速创 API 生图 + cover-crop 后处理**：nano/gpt-image 异步提交轮询→lanczos 精确 3840×2160/1920×1080/1080×1920（imgen_cli.py） | 默认关闭；0.1 元/张；`bash imgen/init.sh` 配 key |
| `init.py` | 跨平台初始化本体：五段依赖自检（基础/编码/可选/程序/大模型）+ 安装指引 + 生成 config.local.json（幂等；--fix 自动安装、--json 机器可读） | Linux/macOS/Windows 通用 |
| `init.sh` | init.py 的薄包装（保留旧入口；Windows 直接 `python init.py`） | |
| `config.example.json` | 本地能力开关模板（comfyui/asr/tts/imgen/denoise 默认全关） | asr/tts/imgen 已接线，comfyui 仍为声明 |
| `docs/` | 开发者文档：DEV.md（演进史、性能基准、坑位破案详录）+ cache-provenance.md（缓存与回执溯源语义）+ canvas-export.md（gfx 可移植渲染与 Canvas 导出协议） | |

## 快速开始
```bash
python3 init.py          # 跨平台初始化：依赖自检 + 安装指引 + 生成 config.local.json（幂等）
                         # Linux 也可 bash init.sh（薄包装）；加 --fix 自动安装缺失基础依赖；--json 机器可读
python3 vtx/vtx.py --help                       # 渲染 CLI
bash scripts/new_project.sh <项目目录> [时长秒]   # 新项目脚手架
blender --background --factory-startup --python blender_bridge/json2blend.py -- <项目>/timeline.json <项目>/preview.blend   # Blender 预览工程（json2blend/verify_blend 须由 blender 解释器运行，python3 直跑会 ModuleNotFoundError）
bash asr/init.sh                                # 字幕产线初始化（幂等）
bash tts/init.sh                                # TTS 产线初始化（幂等）
bash imgen/init.sh                              # 生图产线初始化（幂等）
bash matting/init.sh --download                 # 可选抠像链（权重/环境探测，缺则按指引下载）
```
`init.py` 按五段自检：基础依赖（python≥3.9、ffmpeg/ffprobe、浏览器）→ 编码能力（libfdk_aac、NVENC，缺则 vtx 自动回落并告警）→ 可选工具（blender/sox）→ 程序自检 → 大模型能力（默认关闭）。缺失项打印**当前 OS 的安装指引**，`--fix` 直接尝试安装。

- **浏览器**：gfx 帧截图跨平台定位 Chrome/Chromium/Edge（PATH → 标准安装路径 → `VTX_CHROME` 环境变量兜底）。
- **libfdk_aac**：正式封装必用它（**原生 aac 对真人素材解码真峰会过冲，缺 libfdk 时回落后交付须复测编码后解码 TP**）。Linux 装 fdk 构建的 ffmpeg（RPM Fusion/deb-multimedia）。Windows 注意：gyan.dev / BtbN / winget 的 GPL 发行包**均不含 libfdk_aac**（fdk 为 nonfree 不可随 GPL 分发），需自编译或用 nonfree 自动构建（如 tytan650 autobuild 系列），否则接受原生 aac 回落 + 交付前复测 TP。
- **Windows 注意**：bash 脚本需 Git Bash 或 WSL；vtx/blender_bridge 的 Python 部分原生可用。

## 依赖矩阵
| 层级 | 依赖 | 用途 | 状态 |
|---|---|---|---|
| 基础（必需） | python3≥3.9（标准库即可）、ffmpeg/ffprobe、Chrome/Chromium/Edge | vtx 渲染与 gfx 帧截图 | init.py 自检 |
| 基础（强烈建议） | libfdk_aac 构建 | mux 正式封装音质 | init.py 自检，缺则回落+告警 |
| 可选 | blender | blender_bridge/ 预览桥 | init.py 自检 |
| 可选 | NVENC（NVIDIA GPU） | vtx --gpu 渲染 | 自动探测，不可用回落 x264 |
| 可选 | sox | 部分音频后处理 | init.py 自检 |
| **大模型（默认关闭）** | conda env comfyui + MatAnyone2/BiRefNet_HR 克隆与权重 | matting/ 抠像链 | **需手动配置**，见 `matting/README.md` |
| **大模型（默认关闭）** | ComfyUI 服务 127.0.0.1:8188 | 背景图生成（post_h3.sh） | **需手动配置** |
| **大模型（默认关闭）** | Qwen3-ASR sidecar 127.0.0.1:18085（qwen-asr 解释器 + 模型） | asr/ 字幕产线 | **需手动配置**，见 `asr/README.md` |
| **大模型（默认关闭）** | IndexTTS-2.5 repo（11GB）+ conda indextts / edge-tts 兜底 | tts/ 口播占位语音产线 | **需手动配置**，见 `tts/README.md` |
| **大模型（默认关闭）** | 速创 API key（0.1 元/张）+ requests | imgen/ 背景图生成 | **需手动配置**，见 `imgen/README.md` |
| **大模型（默认关闭）** | resemble-enhance 权重 681MB + conda denoise env | 口播降噪链 scripts/denoise_voice.py | **需手动配置**，`bash scripts/denoise_init.sh --download` |

## 与其他位置的关系
- `~/.agents/skills/video/`：**live skill 挂载点**（五项全部符号链接 → 本仓，本仓 `skill/` 即 skill 本体）。
- `~/文档/04_bilibili视频/`：视频工作区（各期项目、素材、缓存、报告），非 git 仓；项目绑定脚本留在工作区各项目内。
- `_library/blender_bridge/`：已由本仓 `blender_bridge/` 取代为权威；原位置保留为兼容入口。
- 工作区内历史快照副本（`_library/bg_test/works/`、`voice-ime/cache/aup3_extract/`）**修改一律改本仓**，勿直接改工作区副本（详见 `docs/DEV.md`）。
- 音频工具见姊妹仓：`~/文档/01_活跃项目/music_tools/`。
- 推送到服务器 Gitea（私有）时：`git remote add origin <你的 Gitea 地址>/video_tools.git && git push -u origin main`。
