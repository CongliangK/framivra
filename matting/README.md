# matting —— A-roll 抠像 / 背景替换链

真人出镜视频的背景替换管线（bg_test 实测定稿路线）：**BiRefNet_HR@2048 生成首帧 mask → MatAnyone2 视频抠像 → guided filter 收边 → 与新背景合成**。

## 文件清单

| 文件 | 用途 |
|---|---|
| `run_birefnet_hr.py` | 首帧 → 高分辨率 mask（BiRefNet_HR，2048 档） |
| `run_matanyone2.py` | 主 runner：帧目录 + 首帧 mask → alpha 序列（raw + guided filter 收边） |
| `run_matanyone.py` | v1 版 runner（MatAnyone，已被 v2 取代，留档） |
| `post_h3.sh` | H3 成片后处理（找 ComfyUI 输出→补音轨→拉伸→抽质检帧） |
| `run_full_bg.sh` | voice-ime 全量背景替换编排（precrop→2048 抽帧→mask→抠像→4K 合成→NVENC）。**快照性质：内部为原工程绝对路径**，复用时按本机路径改写 |
| `docs/` | 测试报告（路线拍板依据）、抠像升级调研、背景替换总调研 |

## 依赖与开启步骤（默认关闭）

本链属大模型类能力，**默认关闭，需手动配置**：

1. **conda 环境 `comfyui`**（torch 等 GPU 栈）。
2. **两个外部 git 克隆，必须与 runner 脚本同目录平级**（脚本用相对路径 `sys.path` 引入）：
   - `MatAnyone2/`（含官方代码 + `pretrained_models/hf2/model.safetensors` 权重）
   - `BiRefNet_HR/`（含 swint 权重）
   - 原工程另有 MatAnyone / BiRefNet / RobustVideoMatting 旧版克隆，仅 v1 路线用，可不装。
3. **Python 依赖**（conda env 内）：opencv-python、numpy、Pillow、torch、omegaconf、hydra-core、safetensors。
4. **ComfyUI 服务**（127.0.0.1:8188，仅生成背景图时需要）：`bash ~/AI/ComfyUI/start.sh`。
5. 以上就绪后，把仓库根 `config.local.json` 的 `comfyui.enabled` 置 `true`（当前为状态声明）。

## 用法（配置完成后）

```bash
# 单镜头抠像
python run_matanyone2.py <frames_dir> <mask.png> <out_raw_dir> <out_refined_dir>
python run_birefnet_hr.py <first_frame.png> <out_mask.png>
```

实测性能与坑（首帧静默下载被墙权重、绿残留、发丝收边参数等）见 `docs/测试报告-背景替换与H3重绘.md` §五。
