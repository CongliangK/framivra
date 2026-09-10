# video_tools —— 视频制作工具收编仓

**一句话**：B 站视频产线的可复用工具统一收纳——video skill（含 vtx 帧精确渲染 CLI）快照备份 + Blender 预览桥 + 抠像/背景替换链 + A-roll 素材处理工具，git 管理，大模型能力默认关闭。

## 定位（重要）

本仓是**收编快照**：各工具从 `~/文档/04_bilibili视频/`（167G 视频工作区）与 `~/.agents/skills/video/` 复制而来，**原位置一律未动、未删除**——正在生产使用的仍是原位置那套（`~/.agents/skills/video/` 的 skill 与工作区脚本）。将来切换生效位置（符号链接等）另行拍板。

快照收编日期：2026-09-09（video skill 对应 vtx v3.6 之后的版本，含 D35 修复）。

## 目录结构

| 目录 | 内容 | 来源（原位置，仍在使用） |
|---|---|---|
| `skill/` | video skill 全量备份：`vtx/`（vtx.py 渲染 CLI + timeline.schema.json + preview.html）、`references/`（edit/gfx/research 三文档）、`scripts/`（bilibili.py 采集、sync_audio.py 双系统同步、denoise_voice.py 降噪、new_project.sh 脚手架等 9 件）、`assets/`（模板 + gfx 实例页） | `~/.agents/skills/video/` |
| `blender_bridge/` | timeline.json ↔ Blender VSE 双向预览桥：json2blend / verify_blend / blend2json / subs_alpha + README（含 VSE API 坑表） | `04_bilibili视频/_library/blender_bridge/` |
| `matting/` | 抠像/背景替换链 runner：run_birefnet_hr.py（首帧 mask）、run_matanyone2.py（视频抠像）、run_matanyone.py（v1）、post_h3.sh、run_full_bg.sh（全链编排）+ docs/ 三份调研与实测报告 | `04_bilibili视频/_library/bg_test/works/`、`voice-ime/cache/bg_gen/` |
| `aroll/` | A-roll 素材工具：aup3_extract.py（Audacity .aup3 SQLite 解析→提 WAV）、trim_takes.py（VAD 掐头去尾）+ docs/ 全链流程手册 | `voice-ime/cache/aup3_extract/`、`_library/reports/` |
| `init.sh` | 初始化：依赖自检 + 生成 config.local.json（大模型默认关闭） | 本仓新建 |
| `config.example.json` | 本地能力开关模板（comfyui/asr/tts 默认全关） | 本仓新建 |

## 快速开始

```bash
bash init.sh          # 依赖自检 + 生成 config.local.json（幂等）
```

 vtx 渲染最小链（skill 快照内，零外部 Python 依赖）：

```bash
python3 skill/vtx/vtx.py --help          # validate/audit/prepare/gfx/video/audio/mux/qc
bash skill/scripts/new_project.sh <项目名>  # 新项目脚手架
```

## 依赖矩阵

| 层级 | 依赖 | 用途 | 状态 |
|---|---|---|---|
| 基础（必需） | python3（标准库即可）、ffmpeg/ffprobe、google-chrome | vtx 渲染与 gfx 帧截图 | init.sh 自检 |
| 可选 | blender | blender_bridge/ 预览桥 | init.sh 自检 |
| 可选 | sox | 部分音频后处理 | init.sh 自检 |
| **大模型（默认关闭）** | conda env comfyui + MatAnyone2/BiRefNet_HR 克隆与权重 | matting/ 抠像链 | **需手动配置**，见 `matting/README.md` |
| **大模型（默认关闭）** | ComfyUI 服务 127.0.0.1:8188 | 背景图生成（post_h3.sh） | **需手动配置** |
| **大模型（默认关闭）** | Qwen3-ASR sidecar 127.0.0.1:18081 | A-roll 文本配对 / 听写 | **需手动配置** |

`config.local.json`（init.sh 生成，gitignored）记录开关状态；**当前阶段它是能力声明与接线预留，各工具脚本尚未读取该文件**——实际开启以各目录 README 为准。

## 与其他位置的关系

- `~/文档/04_bilibili视频/`：视频工作区（各期项目、素材、缓存、报告），**不是 git 仓库，原样保留**。项目绑定脚本（make_subs.py、repack_voice.py、retimeline_v3x 系列等）留在工作区，未收编。
- `~/.agents/skills/video/`：生效中的 skill，**未改造未删除**，本仓 `skill/` 是其备份。
- 音频工具见姊妹仓：`~/文档/01_活跃项目/music_tools/`。
- 后续：两仓推服务器 Gitea（私有），仓库即代码托管与备份，运行仍在本机（渲染需 NVENC/GPU 与本地模型）。

## 提交与推送

本仓已 `git init` 并完成暂存（`git add -A`），**下一步直接 `git commit` 即可**。推送到服务器 Gitea 时：

```bash
git remote add origin <你的 Gitea 地址>/video_tools.git
git push -u origin main
```
