# matting —— A-roll 抠像 / 背景替换链

组件声明见 `app.json`（kind=app）；`health_check.py` 以 `compile()` 检查 Python 文件，不执行抠像。项目路径优先使用 CLI 参数或环境变量（`MATTE_BASE`/`MATTE_SRC`/`MATTE_OUT`/`MATTE_DRX`/`RESOLVE_DEBUG_LOG`/`VOICE_IME`/`WORKS`/`SRC`/`COMFYUI_OUT`/`VIDEOS`），缺省路径展开 `~`。

真人出镜视频的背景替换管线（bg_test 实测定稿路线）：**BiRefNet_HR@2048 生成首帧 mask → MatAnyone2 视频抠像 → guided filter 收边 → 与新背景合成**。另有 `resolve_matte/` 子链（DaVinci Resolve 21 Studio Magic Mask 命令行驱动，见 `resolve_matte/README.md`）。

> ⚠️ **许可证提示**：代码与权重分别审查；许可限制、未知项与下载边界见 [第三方许可](../../docs/third-party-licenses.md)。已有资产或探测就绪不代表已获用途许可。

**快照性质声明**：本链脚本为 bg_test 产线实测定稿的快照（`run_full_bg.sh` 编排九段视频、`card_points.json` 本机标定点位等）——路径已参数化，但**步骤编排与点位仍是原工程实测快照**，跨工程复用时须按 README 各节核对改写。

## 用途

产出独立抠像素材（带 alpha 的 ProRes4444 / alpha PNG 序列 / 换背景成片），不进 timeline 管线；关停后主链产物不受影响（可卸载 app，§0.1）。

## 依赖

- **conda env `comfyui`**（支持 CUDA 的 PyTorch、提供 `cv2.ximgproc.guidedFilter` 的 OpenCV、NumPy、Pillow、omegaconf、hydra-core、safetensors、transformers）。`app.json` 的 `requires` 为空不代表没有运行时依赖；已有资产用 `init.sh` 探测。
- **resolve_matte 子链系统依赖**（GUI/系统级，解释器探测不了，全部手动）：DaVinci Resolve 21 Studio（`/opt/resolve`，付费）、xdotool、`/dev/input` 读权限（record_clicks）、uinput（键盘注入验证）、NVIDIA GPU + nvidia-smi（vram_guard）。

## 健康检查

从仓库根运行 `env python3 apps/matting/health_check.py`；离线 doctor 的 matting 组件项也经 `app.json` 的 `health.command` 调用同一脚本。它只用 `compile()` 检查本目录与 `resolve_matte/` 下的 Python 语法，不执行被检脚本、不写 `__pycache__`，也不验证依赖、权重或 GPU。

## 开启步骤

本节用于准备本地 BiRefNet_HR → MatAnyone2 路线。`comfyui.enabled` 默认为 `false`，是状态声明；改为 `true` 不会启动服务，也不是直接运行抠像脚本的前置条件。

1. 从仓库根用 `bash apps/matting/init.sh` 探测已有资产。它检查 `COMFYUI_PY`（默认 `~/miniconda3/envs/comfyui/bin/python`）能否导入指定模块，以及脚本同目录内的 `MatAnyone2/`（代码和 `pretrained_models/hf2/model.safetensors`）、`BiRefNet_HR/`（只查 `config.json`）和预置的 ResNet18 文件。**看汇总的 `✗` 缺件数，不要用退出码判定就绪**：探测到缺件也会退出 0。旧 `--download` 为 **BLOCKED**，不会补齐缺件。
2. 缺依赖或缺少已获许可的权重时，不要进入推理。离线证据可用 `env python3 scripts/policy/model_license_policy.py plan matanyone2-code matanyone2-weights birefnet-hr resnet18-weights` 查看；`plan` 不下载、不安装环境、不授予许可，也不解除未知项或旧入口门禁。
3. 区分能力：发现 FFmpeg 或在 `-encoders` 列表看到 `h264_nvenc`，不等于 GPU 可用。两个本地 runner 都直接使用 CUDA、无 CPU 回退；例如 `torch` 能导入但没有可用 CUDA 时，语法和已有资产探测仍可能通过，首帧 runner 会在 `.cuda()` 处失败。即使 CUDA 可用，单镜头命令也只写 mask 和 alpha；全量快照还需匹配的前景帧、alpha 与背景图，并实际完成合成和 NVENC 编码，才能据产物验收。

ComfyUI HTTP 服务（127.0.0.1:8188）仅用于生成背景图，与抠像解释器分开；另用 `bash apps/matting/comfyui_init.sh` 探测。`--start` 会启动已配置服务并写日志（已在线则不启动）；旧 `--install` 为 **BLOCKED**。

## 文件清单（原快照清单，随迁移保留）

| 文件 | 用途 |
|---|---|
| `run_birefnet_hr.py` | 首帧 → 高分辨率 mask（BiRefNet_HR，2048 档） |
| `init.sh` | 已有抠像依赖探测（conda env / 本地模型目录 / ResNet18 文件）；旧 `--download` 为 BLOCKED |
| `comfyui_init.sh` | 探测或启动已配置的 ComfyUI 背景图服务（127.0.0.1:8188） |
| `run_matanyone2.py` | 主 runner：帧目录 + 首帧 mask → alpha 序列（raw + guided filter 收边） |
| `run_matanyone.py` | v1 版 runner（MatAnyone，已被 v2 取代，留档） |
| `post_h3.sh` | H3 成片后处理（找 ComfyUI 输出→补音轨→拉伸→抽质检帧）；路径 env `SRC`/`COMFYUI_OUT`/`OUT_DIR` 覆盖 |
| `run_full_bg.sh` | voice-ime 全量背景替换编排（precrop→2048 抽帧→mask→抠像→4K 合成→NVENC）。**快照性质：步骤编排为原工程实测**；路径已参数化（参数1/`VOICE_IME` 项目根、`WORKS`，默认 ~ 展开） |
| `health_check.py` | app 健康探针（`app.json` health.command 消费；纯语法检查） |
| `app.json` | v2 应用 manifest（kind=app；requires 空——conda/GUI 依赖见上，系统解释器探测不了） |
| `resolve_matte/` | DaVinci Magic Mask 命令行驱动子链（`matte_pipeline.sh` 一键流水线 + 鼠标/显存基建，详见 `resolve_matte/README.md`） |
| `docs/` | 测试报告（路线拍板依据）、抠像升级调研、背景替换总调研 |

## 用法（依赖就绪后）

单镜头抠像会写首帧 mask 与两组 alpha PNG。用 `comfyui` 环境的 `python`，从仓库根进入 `apps/matting/`（模型加载使用相对路径）；先将下列变量设为仓外绝对路径。`FIRST_FRAME` 是 `FRAMES_DIR` 按文件名排序的首个 `.png`，`MASK` 的上级目录须已存在。

```bash
cd apps/matting
python run_birefnet_hr.py "$FIRST_FRAME" "$MASK"
python run_matanyone2.py "$FRAMES_DIR" "$MASK" "$OUT_RAW_DIR" "$OUT_REFINED_DIR"
```

达芬奇 Magic Mask 是独立子链，会启动或重启 Resolve、创建工程并写导出；前置条件与执行命令见 [Resolve 抠像说明](resolve_matte/README.md)。

路线、历史性能与失败记录（包括 MatAnyone2 光流启动时的 ResNet18 静默联网、绿残留、发丝收边）见[实测报告](docs/测试报告-背景替换与H3重绘.md) §五；不作为当前机器的验收结果或下载指引。
