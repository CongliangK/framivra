# bgm —— BGM/配乐产线**路由声明型** app（无代码本体）

> **v2 定位（ARCHITECTURE §3.1/§3.2）**：本 app 是 12 件应用中唯一的**纯路由声明**——`entry.cli` 为空数组（manifest 合法形态），不含任何可执行代码。职责：把本仓视频产线里的 BGM/配乐/床乐/sting/音效/混音/响度 QC 需求**路由到姊妹仓 music_tools**，并声明「分轨审计包交付门」的衔接知识链接。关停后果＝BGM 需求自行路由（可卸载，非可信度构成）。

## 用途（路由声明）

| 需求 | 归属（本仓不含本体） |
|---|---|
| BGM/配乐/床乐/卡点音乐/音效生成（bgm_gen / bgm CLI / ACE-Step / MiniMax Music3 / synthlib） | **music_tools 姊妹仓**：`~/文档/01_活跃项目/music_tools/`（独立仓库）；具体项目在姊妹仓 `music_project/`。入口索引：**music skill**（挂载 `~/.agents/skills/music/`，本体 music_tools 仓；触发词 bgm/配乐/分轨审计/LUFS 等） |
| 选曲 / 公有领域乐谱 / 确定性合成 / SF2/SFZ 音源 | 同上 music_tools |
| 混音 / 响度 QC（LUFS） | 同上 music_tools（`bgm qc`） |
| **分轨审计包交付门**（BGM 正式版挂载进 timeline 后必做，用户逐轨裁决） | 本仓 `apps/blender-bridge/audit_bundle.py`：MP3 导出→审计 timeline→vtx validate→json2blend→verify_blend 五步一键，产物 `<项目>/cache/audit_bgm/` |

两仓衔接方式：music_tools 产出的 BGM 以普通音频源挂载进本仓 `timeline.json` 音轨（`bed`/`bgm` 轨），后续走本仓渲染链（audio/mux/qc）与上述交付门。

## 依赖

**无**（manifest `requires` 为空——无代码本体即无依赖）。BGM 生成侧依赖（GPU/音源/模型）全部在 music_tools 仓自理，见其 skill 与 README；审计包侧依赖见 `apps/blender-bridge/README.md`（ffmpeg/ffprobe + blender 解释器）。

## 健康检查

manifest health 为 **config_key 型**（`bgm.enabled`）：读 config 判断路由声明是否已配置（`vt doctor` / `vt apps check` 消费）。`config.local.json` 含 `bgm` 节（`vt init` 从 `config.example.json` 生成的模板自带）即「已配置」；enabled 布尔仅作人工开关标记，不影响本 app 行为（无代码可开关）。

## 路由配置

1. 确认姊妹仓就位：`~/文档/01_活跃项目/music_tools/`（缺则按 music skill 指引拉起）。
2. 按 `config.example.json` 的 `bgm` 节配置路由；启用标记以模板为准，不是本 app 的模型执行或下载开关。
3. BGM 需求直接说给 **music skill**（触发词见 skill 描述）；产出挂载 timeline 后跑交付门：
   `env python3 apps/blender-bridge/audit_bundle.py -t <项目>/timeline.json [--prefix bgm-]`。
