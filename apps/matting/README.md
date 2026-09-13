# matting —— A-roll 抠像 / 背景替换链

> **v2 结构（P2a 批次 3 起本目录迁入 `apps/matting/`）**：manifest 见 `app.json`（kind=app；健康检查 = `health_check.py` 语法探针，compile() 全部 21 个 .py 零执行零副作用）；兼容 symlink：仓库根 `matting → apps/matting`（目录级，旧路径引用仍可达）。随迁参数化收编（§3.1）：resolve_matte 8 件 `sys.path.insert` 硬编码改 HERE 相对；voice-ime 素材/微信收稿/ComfyUI 输出等项目路径改 **CLI 参数 / env（`MATTE_BASE`/`MATTE_SRC`/`MATTE_OUT`/`MATTE_DRX`/`RESOLVE_DEBUG_LOG`/`VOICE_IME`/`WORKS`/`SRC`/`COMFYUI_OUT`/`VIDEOS`）> `~` 展开默认**，无参无 env 时展开值与原硬编码逐字节同。

真人出镜视频的背景替换管线（bg_test 实测定稿路线）：**BiRefNet_HR@2048 生成首帧 mask → MatAnyone2 视频抠像 → guided filter 收边 → 与新背景合成**。另有 `resolve_matte/` 子链（DaVinci Resolve 21 Studio Magic Mask 命令行驱动，见 `resolve_matte/README.md`）。

**快照性质声明**：本链脚本为 bg_test 产线实测定稿的快照（`run_full_bg.sh` 编排九段视频、`resolve_matte/step*.py` 探针、`card_points.json` 本机标定点位等）——路径已参数化，但**步骤编排与点位仍是原工程实测快照**，跨工程复用时须按 README 各节核对改写。

## 用途

产出独立抠像素材（带 alpha 的 ProRes4444 / alpha PNG 序列 / 换背景成片），不进 timeline 管线；关停后主链产物不受影响（可卸载 app，§0.1）。

## 依赖

- **conda env `comfyui`**（torch GPU 栈 + opencv-python/numpy/Pillow/omegaconf/hydra-core/safetensors/transformers）——推理环境，系统解释器探测不了，故 `app.json` 的 `requires` 为空，依赖探测走下方 init.sh。
- **两个外部 git 克隆**（与本目录平级，runner 相对路径引入；`init.sh --download` 可重建）：`MatAnyone2/`（github + HF 权重 141MB）、`BiRefNet_HR/`（HF 快照）。
- **resolve_matte 子链系统依赖**（GUI/系统级，解释器探测不了，全部手动）：DaVinci Resolve 21 Studio（`/opt/resolve`，付费）、xdotool、`/dev/input` 读权限（record_clicks）、uinput（键盘注入验证）、NVIDIA GPU + nvidia-smi（vram_guard）；ComfyUI 服务（127.0.0.1:8188，仅背景图生成，另见 `comfyui_init.sh`）。

## 健康检查

`env python3 apps/matting/health_check.py`——compile() 纯语法检查全部 21 个 .py（零执行零副作用零 __pycache__）；`vt doctor` 经 app.json `health.command` 自动调用。

## 开启步骤（默认关闭）

1. 一键探测/补齐：`bash apps/matting/init.sh --download`（幂等；探测 conda env `comfyui`、
   两个平级克隆、torch hub ResNet18，缺失项实际下载——HF 权重走 hf-mirror，github 克隆
   失败自动换代理；旧路径 `bash matting/init.sh` 经 symlink 同样可达）。
   ComfyUI 背景图服务另跑 `bash apps/matting/comfyui_init.sh`（--install/--start）。
2. **两个外部 git 克隆，必须与本目录平级**（脚本用相对路径 `sys.path` 引入）：
   - `MatAnyone2/`（含官方代码 + `pretrained_models/hf2/model.safetensors` 权重）
   - `BiRefNet_HR/`（含 swint 权重）
   - 原工程另有 MatAnyone / BiRefNet / RobustVideoMatting 旧版克隆，仅 v1 路线用，可不装。
3. **Python 依赖**（conda env 内）：opencv-python、numpy、Pillow、torch、omegaconf、hydra-core、safetensors。
4. **ComfyUI 服务**（127.0.0.1:8188，仅生成背景图时需要）：`bash ~/AI/ComfyUI/start.sh`。
5. 以上就绪后，把仓库根 `config.local.json` 的 `comfyui.enabled` 置 `true`（当前为状态声明）。

## 文件清单（原快照清单，随迁移保留）

| 文件 | 用途 |
|---|---|
| `run_birefnet_hr.py` | 首帧 → 高分辨率 mask（BiRefNet_HR，2048 档） |
| `init.sh` | 抠像链幂等依赖探测（conda env / 两克隆 / torch hub 权重）；`--download` 补缺失项（HF 权重走 hf-mirror，github 克隆失败自动换代理） |
| `comfyui_init.sh` | ComfyUI 背景图服务（127.0.0.1:8188）依赖探测/初始化（仅生成背景图时需要） |
| `run_matanyone2.py` | 主 runner：帧目录 + 首帧 mask → alpha 序列（raw + guided filter 收边） |
| `run_matanyone.py` | v1 版 runner（MatAnyone，已被 v2 取代，留档） |
| `post_h3.sh` | H3 成片后处理（找 ComfyUI 输出→补音轨→拉伸→抽质检帧）；路径 env `SRC`/`COMFYUI_OUT`/`OUT_DIR` 覆盖 |
| `run_full_bg.sh` | voice-ime 全量背景替换编排（precrop→2048 抽帧→mask→抠像→4K 合成→NVENC）。**快照性质：步骤编排为原工程实测**；路径已参数化（参数1/`VOICE_IME` 项目根、`WORKS`，默认 ~ 展开） |
| `health_check.py` | app 健康探针（`app.json` health.command 消费；纯语法检查） |
| `app.json` | v2 应用 manifest（kind=app；requires 空——conda/GUI 依赖见上，系统解释器探测不了） |
| `resolve_matte/` | DaVinci Magic Mask 命令行驱动子链（`matte_pipeline.sh` 一键流水线 + step 探针 + 鼠标/显存基建，详见 `resolve_matte/README.md`） |
| `docs/` | 测试报告（路线拍板依据）、抠像升级调研、背景替换总调研 |

## 用法（配置完成后）

```bash
# 单镜头抠像（conda comfyui env 解释器）
python run_matanyone2.py <frames_dir> <mask.png> <out_raw_dir> <out_refined_dir>
python run_birefnet_hr.py <first_frame.png> <out_mask.png>
# 达芬奇 Magic Mask 流水线（resolve_matte/）
env bash apps/matting/resolve_matte/matte_pipeline.sh <工程名> [视频目录]
```

实测性能与坑（首帧静默下载被墙权重、绿残留、发丝收边参数等）见 `docs/测试报告-背景替换与H3重绘.md` §五。
