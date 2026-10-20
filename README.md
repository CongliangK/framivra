# Framivra —— 可复现、帧精确的视频制作工具箱（v2 插件化架构）

> **Framivra**（小写 `framivra`，读 "frame-ee-vra"）:frame + 自造后缀。仓目录与文档内路径占位沿用 `video_tools` 历史路径名,品牌名以本行与 pyproject 包名为准。**本仓是什么**:B 站视频产线的程序本体权威仓。**内核** `vtx/` = timeline.json 契约 + 帧精确渲染 CLI（validate/diff/subs/audit/prepare/gfx/video/audio/mux/qc/gc/snapshot）+ 混音响度终链（-14 LUFS + limiter）+ 内容寻址缓存——关停它主链产物不可信，故永不可关（宪法信条）。产线知识在 `templates/tech-talk/` 工作流包（科技口播六阶段 SOP + 调研/拍摄/封面/发布链配方）；机制契约文档在 `docs/skill/`（路由索引 `docs/skill/SKILL.md`）。git 管理，大模型能力默认关闭。

> **姊妹项目 Music Tools**：本仓专注「视频」产线。BGM / 配乐 / 床乐 / 音效 / 响度 QC / 分轨混音需求请移步姊妹仓 music_tools（本仓 `apps/bgm` 为路由声明）——music_tools 产出的 BGM 挂载进本仓 timeline.json 后，由 `apps/blender-bridge/audit_bundle.py` 生成分轨审计包供 Blender 逐轨裁决。

## 四层架构一页

```
vtx/            ① 内核（永不可关，宪法信条）：渲染链 + validate/qc + 响度终链 + 缓存 + vtx/api.py 对外面 + vtx/pluginlib/ 协议运行时
docs/skill/     ① 内核文档：SKILL.md 路由索引 + references/{edit,gfx,runbook}.md + assets/ 起手模板
apps/  (12 件)  ② 应用：产出独立素材不进 timeline 管线，关停不影响主链
plugins/ (4 件) ② 插件：扩展 timeline.json 词汇并钩进管线（denoise / duck-static / blender-bridge / bgm-render + registry.json）
templates/      ③ 模板集合：tech-talk / skill-demo 工作流 + hello-app / hello-plugin 开发范本
scripts/           内核工具残留：new_project.sh / gfx_golden.py / bench_cold.sh（+ 迁走件跨平台转发入口）
vt.py              统一入口：doctor / apps|plugins|templates list|check（skills 兼容别名） / version / init / --fix-links
```

分层判据（关停测试）：关停后主链产物**不可信** → 内核；**仍能可信出一期片** → 可卸载（apps/plugins）。apps/plugins 每件自包含（app.json/plugin.json + 代码 + README + tests）。

## 快速开始（clone 即用，零安装步骤）

想先生成公开示例？按 [standard Demo 运行指南](docs/demo-fixture.md#公开-standard-demo-生产运行) 从冻结输入准备到显式 CDP / CEF 成片、查看结果；依赖资格与未验收范围在指南中说明。

自建项目先满足下方[依赖矩阵](#依赖矩阵)；「零安装」指不必先 pip 安装本仓，不代表已具备外部工具或素材。以下克隆、init、脚手架及渲染均为写操作。把 PROJECT 替换为全新项目的绝对路径；脚手架会写入 timeline.json，不要指向已有工程。

```bash
git clone https://github.com/CongliangK/framivra
cd framivra
python3 vt.py init                     # 依赖自检 + 安装指引 + 生成 config.local.json（幂等；--fix 自动装）
env VTX_DOCTOR_OFFLINE=1 python3 vt.py doctor  # 三检：本地引用漂移 / apps+plugins manifest+health / 工作流模板完好性
PROJECT="/绝对路径/新视频项目"
bash scripts/new_project.sh "$PROJECT" 30  # 目录 + 占位 timeline.json + schema + gfx 样页
```

脚手架不生成或下载媒体。先把含音轨的 CFR 30 fps 素材放到 `$PROJECT/assets/v01.mp4`，或修改 timeline.json 的 sources 与剪辑/音频事件以匹配自己的素材；源长度须覆盖所引用的帧区间。完成编排后，在同一 Bash 会话执行：

```bash
python3 vtx/vtx.py -t "$PROJECT/timeline.json" validate  # 渲染前校验门（必跑）
python3 vtx/vtx.py -t "$PROJECT/timeline.json" all       # 全流程，更新项目 cache/、out/
```

完整 `all` 及 QC/插件结果成功后查看 `$PROJECT/out/final.mp4`；输出文件存在本身不是验收通过。截断或局部检查的边界见 [正式交付链](docs/skill/references/runbook.md#正式交付链r1-r4-共用)。

例行 doctor 使用离线形态，不 fetch；版本漂移仅相对本地已有引用。确需刷新远端引用时，联网 doctor 会执行 git fetch 写入 .git，须按写操作审查。

可选产线初始化：`bash apps/asr/init.sh` / `bash apps/tts/init.sh` / `bash apps/imgen/init.sh`（幂等）；`bash apps/matting/init.sh`（已有抠像资产探测；下载边界见 [第三方许可](docs/third-party-licenses.md)）。live skill 挂载（如 `~/.agents/skills/video/`，为指向本仓的符号链接）可用 `vt.py --fix-links` 重建。

不想手动 clone？三平台一键安装（克隆模式，等价于自动跑一遍「快速开始」）见下两节：Linux/macOS（`install.sh`）与 Windows（`install_windows.ps1`）。

<a id="installer-trust"></a>

两平台引导器的共同信任边界：脚本经 HTTPS 从 GitHub raw 下载，首次信任「HTTPS 下载通道 + 仓库当前内容」，不自称独立签名验证；可先下载审读再执行。发布者身份验证（keyless provenance，#16/#41）由资产安装通道承担，克隆引导不冒充身份验证。版本选择、固定 tag 与完整边界见 [安装选择与信任](docs/releases.md#安装选择与信任)。

**Linux/macOS 一键安装（克隆模式，install.sh）**（[共同信任边界](#installer-trust)）——与 Windows 侧同一工作流：引导脚本克隆（或安全更新）本仓库到 `~/.local/share/framivra-repo`，再在克隆内运行幂等的 `vt.py init`；从公开稳定 Release 解析 tag 与提交，不要求 gh CLI，不替代手动克隆（上文「快速开始」clone 路径始终可用），脚本绝不 sudo、也不自动安装包管理器依赖：

```bash
curl -fsSL https://raw.githubusercontent.com/CongliangK/framivra/main/scripts/install.sh | bash
```

- 自动完成：python3≥3.9 与 git 探测（缺失不自动装，给一行 apt/dnf/brew 或 python.org / git-scm.com 指引）→ 克隆/安全更新仓库 → 在克隆内跑 `vt.py init`（依赖自检 + 生成 `config.local.json`；参数原样透传，如 `bash -s -- -- --fix`；curl|bash 下有可用的 `/dev/tty` 时把交互接回终端）→ 提示进入仓库与 doctor 用法（例行检查用上方离线形态）。重复运行 = 安全更新 + 重跑幂等 init；兼容 macOS 系统自带 bash 3.2。
- 更新安全语义与 Windows 版逐条对齐：克隆干净（`git status --porcelain` 为空）时显式 refspec 浅 fetch + 全限定 ref `reset --hard origin/<ref>`；克隆变脏（含未跟踪文件）时**拒绝自动更新**、不静默丢弃改动（清理或删除克隆目录后重试，或加 `--skip-update` 跳过更新直接复用）；本地提交以 `.git` 内同步标记（marker）保护，防 `reset --hard` 丢提交；上游新版本把原被忽略路径（典型 `config.local.json`）转为跟踪文件时判脏看不见，碰撞预检同样**拒绝覆盖**（先把冲突文件备份到克隆外，删除克隆目录后重装）；符号链接目录与 origin 不匹配的克隆一律拒绝覆盖。
- 环境变量（与 Windows 侧同名同义）：`FRAMIVRA_DIR`（默认 `~/.local/share/framivra-repo`；**须为绝对路径**；**不得**用运行时数据根 `~/.local/share/video-tools`——安装运行数据会让克隆变脏、下次更新被拒；该比对为纯词法规范化（`/./`、`/../` 变体同拒，不依赖 macOS 缺失的 GNU `realpath`），但不解析符号链接——两路径经符号链接同址的极端场景不拦）、`FRAMIVRA_REPO`（默认 `https://github.com/CongliangK/framivra.git`，可信镜像可）、`FRAMIVRA_REF`（默认 `stable`；指定 `vX.Y.Z` 或 `vX.Y.Z-rc.N`；开发版显式 `main`）；空值/全空白/控制字符/短横线开头一律拒绝，空白仅 `FRAMIVRA_DIR` 放行空格（目录路径可含空格），`FRAMIVRA_REPO`/`FRAMIVRA_REF` 仍拒空白。

**Windows 一键安装（克隆模式，install_windows.ps1）**（[共同信任边界](#installer-trust)）——与上节 Linux/macOS 侧同一工作流（克隆/安全更新仓库 + 仓内幂等 init），依赖公开稳定 Release 的版本定位，不要求 gh CLI：引导脚本克隆（或安全更新）本仓库到 `%LOCALAPPDATA%\framivra-repo`，再在克隆内运行幂等的 `vt.py init`；定位是快速初始化手段，不替代手动克隆（上文「快速开始」clone 路径始终可用）：

```powershell
# Windows 原生 PowerShell 一行(无需 WSL/Git Bash;等价于自动跑一遍「快速开始」):
powershell -NoProfile -ExecutionPolicy Bypass -Command "iwr -UseBasicParsing https://raw.githubusercontent.com/CongliangK/framivra/main/scripts/install_windows.ps1 -OutFile $env:TEMP\vt-i.ps1; & $env:TEMP\vt-i.ps1"
```

- 自动完成：python≥3.9 检测（缺失时 winget 用户级安装）→ git 检测（缺失时 winget 用户级装 Git.Git；失败给一行手动指引）→ 克隆/安全更新仓库 → 在克隆内跑 `vt.py init`（依赖自检 + 生成 `config.local.json`，追加 `'--fix'` 自动装缺失基础依赖）→ 提示新开终端用法（winget 新装须新终端才进 PATH）。
- 重复运行 = 安全更新：克隆干净（`git status --porcelain` 为空）时 `git fetch` + `reset --hard origin/<ref>`；克隆变脏（含未跟踪文件）时**拒绝自动更新**、不静默丢弃改动（清理或删除克隆目录后重试，或加 `-SkipUpdate` 跳过更新直接复用）。本地提交同样拒绝自动重置（引导器以 `.git` 内同步标记识别，防 `reset --hard` 丢弃）。上游新版本把原被忽略路径（典型 `config.local.json`）转为跟踪文件时判脏看不见，碰撞预检同样**拒绝覆盖**（先把冲突文件备份到克隆外，删除克隆目录后重装）。
- 环境变量：`FRAMIVRA_DIR`（默认 `%LOCALAPPDATA%\framivra-repo`；**须为绝对路径**，相对路径与以点/空格结尾的路径段一律拒绝；**不得**用运行时数据根 `%LOCALAPPDATA%\framivra`——安装运行数据会让克隆变脏、下次更新被拒）、`FRAMIVRA_REPO`（默认 `https://github.com/CongliangK/framivra.git`，可信镜像可）、`FRAMIVRA_REF`（默认 `stable`；指定 `vX.Y.Z` 或 `vX.Y.Z-rc.N`；开发版显式 `main`）；空值/控制字符/短横线开头一律拒绝，空白仅 `FRAMIVRA_DIR` 放行（目录路径可含空格，默认值随用户名而定），`FRAMIVRA_REPO`/`FRAMIVRA_REF` 仍拒空白。
- 参数透传：脚本名后追加的参数原样交给 `vt.py init`（短横线开头须加引号），如 `& $env:TEMP\vt-i.ps1 '--fix'`；脚本先保存后执行，控制台直连不被吞。
- 企业域控无 winget 的机器需手动装 Python 与 Git；隔离内网不在支持范围。
- 稳定源码、RC、平台资格与发行冻结规则见 [源码稳定基线](docs/releases.md)。`install.py` 的资产安装保留严格身份验证；长期安装器改进跟踪 #94。

维护者发布入口（仅查看参数；真实发布需 GitHub 仓库写权限）：

```bash
bash scripts/release.sh --help
```

## 三层路由

**开始创作**：纯 MG、多场景/角色与透明叠加的源码组织，先读 [HTML 创作组织](docs/skill/references/gfx.md#html-创作组织)；再选机制或口播工作流，避免把模板清单直接当页面清单。

**内核**：`docs/skill/SKILL.md`（路由索引 + 迭代回路速查）→ `docs/skill/references/edit.md`（timeline 契约 + vtx 全阶段 + 坑表）、`gfx.md`（帧驱动协议 + D22 设计铁律）、`runbook.md`（修改配方 R1-R4 + 棒次协议）。

**apps/**（12 件，一句话）：

| app | 职责 |
|---|---|
| `apps/asr/` | Qwen3-ASR + ForcedAligner 字幕产线（口播→subtitles[] 锚定），默认关闭 |
| `apps/tts/` | IndexTTS-2.5 / edge-tts 口播占位语音（spec.json 贴预算），默认关闭 |
| `apps/imgen/` | 生图 API 生图 + cover-crop 后处理（bg4k 硬接口 3840×2160），默认关闭 |
| `apps/matting/` | 抠像/背景替换两路线（resolve_matte 达芬奇主力 + 自研链备用；专属文档 matting.md） |
| `apps/aroll/` | A-roll 素材工具（aup3 提取 / VAD 掐头去尾） |
| `apps/cover/` | B 站投稿封面产线（make_cover.py 一条龙 + QC 门，D50） |
| `apps/research/` | 选题调研 6 件（bilibili / github_repos / score_topic / 设计研究三件） |
| `apps/blender-bridge/` | timeline ↔ Blender VSE 双向预览桥 + BGM 分轨审计包 |
| `apps/publish/` | 4K 投稿版发布链（export_4k / check_4k / compare_4k_quality） |
| `apps/bgm/` | BGM 需求路由声明（本体 music_tools 姊妹仓，无可执行件） |
| `apps/gates/` | 发布前内容级复检（G1/G2/G7/G10 + 项目门；G3 TP 已上内核 qc） |
| `apps/sync/` | 双系统同步（GCC-PHAT）与实录 take 替换（管线前置素材工具） |

**plugins/**（4 件）：`plugins/denoise/`——口播降噪（`sources[].denoise`，缝 A prepare 源产物钩子）；`plugins/duck-static/`——床乐静态分段避让（`audio_tracks[].duck_strategy`，缝 B 音轨事件内存变换）；`plugins/blender-bridge/`——顶层键 `blender_bridge` 配置即 `vtx all` 收尾自动导出 Blender 审片工程（缝 D finish，协议 v1.2）；`plugins/bgm-render/`——`sources[].bgm` 音轨直接引用 music_tools 工程 JSON，自动渲染母带入内容寻址缓存（缝 A+C，协议 v1.2）。协议 v1/v1.2 与 registry.json 见 `docs/ARCHITECTURE.md` §2/§2.5。

**templates/**：集合入口见 [templates/README.md](templates/README.md)。`tech-talk/` 是完整科技口播 B 站流程（六阶段 / 音频支线 / 全链 SOP / 模板资产），与纯内核 `skill-demo/`、开发范本 `hello-app/` / `hello-plugin/` 平级；产线任务先读 tech-talk 的 SKILL.md。`python3 vt.py templates list|check` 检查工作流模板，旧 `skills` 命令兼容；doctor JSON 保留 `skills` 字段。

## 依赖矩阵

| 层级 | 依赖 | 用途 | 状态 |
|---|---|---|---|
| 基础（必需） | python3≥3.9（标准库即可）、ffmpeg/ffprobe、Chrome/Chromium/Edge | vtx 渲染与 gfx 帧截图 | vt.py init 自检 |
| 基础（强烈建议） | libfdk_aac 构建 | mux 正式封装音质 | 自检，缺则回落+告警（交付须复测解码 TP） |
| 可选 | blender / sox | blender-bridge 预览桥 / 部分音频后处理 | 自检 |
| 可选 | NVENC（NVIDIA GPU） | `video --gpu` 渲染 | 自动探测，不可用回落 x264 |
| **大模型（默认关闭）** | conda env comfyui + MatAnyone2/BiRefNet_HR 权重 | apps/matting 自研抠像链 | `bash apps/matting/init.sh`（已有资产探测） |
| **大模型（默认关闭）** | Qwen3-ASR sidecar（18085） | apps/asr 字幕产线 | `bash apps/asr/init.sh` |
| **大模型（默认关闭）** | IndexTTS-2.5 repo（11GB 权重）+ conda indextts（edge-tts 兜底） | apps/tts 占位语音 | `bash apps/tts/init.sh` |
| 可选 | ComfyUI 服务 127.0.0.1:8188 | apps/matting 背景图生成（post_h3.sh 消费其输出） | `bash apps/matting/comfyui_init.sh` |
| **大模型（默认关闭）** | 生图 API key（0.1 元/张）+ requests | apps/imgen 生图 | `bash apps/imgen/init.sh` 配 key |
| **大模型（默认关闭）** | resemble-enhance 权重 681MB + conda denoise env | plugins/denoise 口播降噪 | `bash plugins/denoise/denoise_init.sh`（已有资产探测） |

## 开发者文档与项目布局

> 智能体请循 `AGENTS.md`（行为指南/门禁速览/写态红线）；本 README 为人类面。

- 架构设计权威：`docs/ARCHITECTURE.md`（v2 四层架构 / 插件协议 / 迁移映射 / 验收门）。
- 开发者文档：`docs/DEV.md`（演进史、性能基准、坑位破案详录）、`cache-provenance.md`（缓存与回执溯源）、`canvas-export.md`（gfx Canvas 导出协议）、`audio-audit.md`（分轨审计包语义）、`post-loudness.md`（响度终链）、`demo-fixture.md`（standard-demo 冻结输入与 `scripts/demo.py` 三步入口）。
- 测试：`bash scripts/run_all_tests.sh`（`python3 -m unittest discover -s tests`；集成用例需 VTX_TEST_CHROME/VTX_TEST_FFMPEG）。
- 工作区（非 git 仓）：各期视频项目目录 + `_library/`（方法论/资产/blender_bridge 挂载，例如 `~/videos/projects/`）——**修改一律改本仓**，工作区历史副本为冻结快照。音频工具见姊妹仓 music_tools。

## License

本项目许可证与版权声明见仓根 [LICENSE](LICENSE)，不替代上游代码、模型/辅助权重、API 服务与输出权利。

第三方许可、固定官方证据与默认载荷/可选下载边界统一见 [第三方许可](docs/third-party-licenses.md)；未知或受限资产不算合规通过，未审查的自动下载明确阻断。默认源码/内核未捆绑权重或外部工具二进制，与安装器的可选下载不是同一声明。
