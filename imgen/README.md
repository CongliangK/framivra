# imgen —— 速创 API 生图 + cover-crop 后处理

脚本阶段写 prompt → 速创 API（api.wuyinkeji.com，nano / gpt-image 两模型）付费生图（0.1 元/张）→ 原图即下（URL 2 小时失效）→ ffmpeg lanczos cover-crop 到**精确尺寸** → 硬对接下游：
- `assets/bg/background_4k.png`（3840×2160 8-bit RGB PNG）→ `matting/run_full_bg.sh` 抠像逐像素合成（cv2.imread 直读、无 resize，**像素契约不过即崩**）；
- `assets/bg/background_1080p.png`（1920×1080）/ `background_vertical.png`（1080×1920）→ 口播链 / gfx 底图；
- `assets/imgen/<UTC>-<n>.png` → 原图留档（API 原图不可再生，属资产非缓存，不入 cache/）。

## 文件清单

| 文件 | 用途 | 上游内化来源（行号=2026-09 快照） |
|---|---|---|
| `imgen_cli.py` | 总入口：`gen` / `batch` / `postprocess` / `key status`；编排线程池 10/10/5 | `src/slime/cli.py`（:87/:108/:137 三池；:41-80 单次生成） |
| `api_client.py` | SuchuangClient：异步提交 + 轮询 detail + 下载 + 错误分类 | `src/slime/core/api_client.py`（鉴权裸 key 头 :48-54；提交 :56-83；轮询 :92-151；result 四形态 :172-204；错误分类 :17-37） |
| `models.py` | 模型/端点/参数定义 + kind 联动矩阵 | `src/slime/core/models.py`（BASE_URL :10；nano 端点 :36；**gpt 端点 `/api/async/image_gpt` 无 `_image` 后缀** :43；参数校验 :93-108；**参考图 ≤14 张 :113-114**；估价 :120-121）+ `reference_upload.py:57`（is_data_url） |
| `secrets.py` | key 解析链 `--key > SC_API_KEY > NANOBANANA2_API_KEY > keyring > ~/.config/slime/secrets.json(0600)` | `src/slime/core/secrets.py`（resolve :119-139；env 链 :90-92；keyring :21-29；secrets.json :56-87）+ `core/config.py:19-20`（XDG 目录） |
| `reference_upload.py` | 本地参考图外链：scp jd-gitea-root → https://ruochongliang.top/sc-img/，失败回退 tmpfiles.org（SC_PROXY/本地 7890） | `src/slime/core/reference_upload.py`（scp+chmod+回验 :118-147；tmpfiles :75-115；代理链 :40-49；回退编排 :150-162） |
| `postprocess.py` | ffmpeg lanczos cover-crop → 精确 3840×2160 / 1920×1080 / 1080×1920 + ffprobe 断言 | 方案 §5.3（ffmpeg 零新依赖；无上游对应） |
| `init.sh` | 幂等初始化（秒级，无模型下载） | 方案 §6.4 |
| `tests/` | 57 用例：models/secrets/postprocess 单测 + 本地 mock http.server 全链 | 方案 §9.3 |

**内化说明**：上游仓 `/home/liangruochong/文档/01_活跃项目/图像与多媒体/suchuang-image` 只读；本模块运行时零依赖上游（搬入改写、本仓内化维护）。内化改动：api_base 可注入（mock 测试/换网关不改代码）、下载失败重试 ≤2 次后上抛、`resolve_api_key` 空白串视为未提供（上游会把 `"  "` 误当路径）、ffprobe 对无效图返回 0 尺寸的显式拦截。

## API 模型表

| 模型 | 提交端点 | size 语义 | aspectRatio | 参考图 |
|---|---|---|---|---|
| `nano`（NanoBanana2 / Gemini 底座，默认） | `POST /api/async/image_nanoBanana2`（上游 `core/models.py:36`） | `1K`/`2K`/`4K` 分辨率档 | 交（非 `auto` 时；`auto/1:1/16:9/9:16/…`） | urls ≤14 张 |
| `gpt-image`（GPT-Image-2 / OpenAI 底座） | `POST /api/async/image_gpt`（上游 `core/models.py:43`，**无 `_image` 后缀**） | **纯比例**（`auto/1:1/16:9/9:16/…`，非像素） | **不交** | urls ≤14 张（拒绝 data URL） |

轮询 `GET /api/async/detail?id=<task_id>`：status `0/1`=排队/生成中、`2`=成功、`3`=**审核拒绝（不退款不重试）**、`-1`=失败。`result` 字段四形态防御解析（str / list[str] / list[dict] / wrapper dict）。错误分类：429/5xx 可重试，4xx 立即上抛。鉴权头 `Authorization: <裸key>`（无 Bearer）。价格 0.1 元/张。

## --kind 联动矩阵（方案 §5.2 强制）

`--kind` 不但决定落位，还**联动生成档位**，保证 cover-crop 前源图 ≥ 目标（只降采样不放大）：

| --kind | 模型 | 生成档位 | 比例 | 落位（目录自动创建） | 像素契约 |
|---|---|---|---|---|---|
| `bg4k` | nano | **4K**（强制） | 16:9 | `assets/bg/background_4k.png` | 3840×2160 rgb24 |
| `bg1080p` | nano | 2K | 16:9 | `assets/bg/background_1080p.png` | 1920×1080 rgb24 |
| `vertical` | nano | **4K** | 9:16 | `assets/bg/background_vertical.png` | 1080×1920 rgb24 |
| `raw`（默认） | 用户指定 | 用户指定 | 用户指定 | `assets/imgen/<UTC>-<n>.png` | 仅尺寸校验（pix_fmt 原样） |

- bg4k 禁止 2K 源插值放大进 4K 抠像合成链：nano 2K 16:9 实出 ~2752×1536，放大 3840 需 ~1.41×（=2160/1536）插值，cv2 逐像素合成 + NVENC 会把软化固化为成片画质损失。
- 显式 `--model/--size/--ratio` 覆盖联动档位时，若估算源 < 目标，**打印画质警告（含插值倍数）后照做**（用户主权）；倍数估算锚点=方案实证 2K 16:9≈2752×1536 等比外推，仅提示性质。
- `new_project.sh` 只建 `assets/ gfx/ cache/ out/` 四目录，**不含 assets/bg/、assets/imgen/**——本模块写产物前自行 `mkdir -p`（含 batch）。

## 用法

```bash
# 初始化（幂等，秒级；requests/key 链/ffmpeg 探测，默认不触网）
bash imgen/init.sh            # 加 --net 可选开启 GET api.wuyinkeji.com 连通探测（不扣费）

# key 本地探测（不触网不扣费；展示仅尾 4 位）
python3 imgen/imgen_cli.py key status

# 4K 背景图（提交前有付费确认门：无 --yes 时交互 y/N，非 TTY 必须 --yes）
python3 imgen/imgen_cli.py gen "深色科技感演播室背景，无人物，景深虚化" \
    --out <项目目录> --kind bg4k --yes
# → <项目>/assets/bg/background_4k.png（3840×2160 rgb24，ffprobe 断言通过）

# 1080p 横版 / 竖版
python3 imgen/imgen_cli.py gen "..." --out <项目> --kind bg1080p --yes
python3 imgen/imgen_cli.py gen "..." --out <项目> --kind vertical --yes

# 带参考图（本地文件自动外链：scp 自有服务器 → 回退 tmpfiles.org；URL 直传；≤14 张）
python3 imgen/imgen_cli.py gen "保留构图，改成水彩" --out <项目> --kind raw \
    --ref ./ref1.png https://example.com/ref2.jpg --yes

# 批量（[{prompt,kind,model,size,ratio,ref}, ...]；线程池 10/10/5；>50 张二次确认）
python3 imgen/imgen_cli.py batch prompts.json --out <项目> --yes

# 离线后处理（免 API、不扣费；源<目标时打印插值倍数警告后照做）
python3 imgen/imgen_cli.py postprocess <原图.png> --kind bg4k -o /tmp/out.png

# 断线恢复（URL 2h 失效内：只重轮询+下载+后处理，零新提交零扣费；task_id 见记账文件）
python3 imgen/imgen_cli.py gen "<原prompt>" --out <项目> --kind bg4k --task-id <task_id>
```

环境变量（一般无需设置）：`IMGEN_API_BASE`（覆盖 API 地址，测试/mock 用）、`IMGEN_CONFIG`（覆盖 config.local.json 路径）、`IMGEN_POLL_INTERVAL`/`IMGEN_POLL_TIMEOUT`（轮询节奏）、`SC_PROXY` 等参考图上传代理（见 reference_upload.py docstring）。

## 付费纪律（硬门）

- 提交前打印「将提交 N 张 × 0.1 元/张 = X 元」+「**审核拒绝（status=3）不退款、不重试**」提示；无 `--yes` 时交互 `y/N`；**非 TTY 且无 --yes 直接中止（0 次提交，退出码 2）**。
- batch 单次 >50 张：即使 `--yes` 也需 TTY 二次确认。
- config `imgen.confirm_before_submit: false` 可关闭交互（相当于自动 yes，自担风险）。
- job 记账 → `<项目>/cache/reports/imgen_jobs_<runid>.json`（task_id/payload/状态/产物/费用，原子写）；下载临时件 → `<项目>/cache/tmp/<runid>/`（后处理完成即删）。
- 真实 API 冒烟为**独立人工步骤**（--yes 单张 0.1 元，用户在场授权），不在自动测试内——本模块全部测试走本地 mock http.server。

## key 管理

解析链优先级：`--key`（值或文件路径）> `SC_API_KEY` > `NANOBANANA2_API_KEY` > 系统 keyring（service=`slime-suchuang`, account=`api-key`）> `~/.config/slime/secrets.json`（0600，`{"api_key": "..."}`）。key 永不入仓不入日志（打印仅尾 4 位）。推荐 `export SC_API_KEY=...` 入 shell rc。

## 后处理（postprocess.py）

```
ffmpeg -y -i <原始下载.png> \
  -vf "scale=W:H:force_original_aspect_ratio=increase:flags=lanczos,crop=W:H,format=rgb24" \
  -frames:v 1 <out.png>
```

cover 语义：等比缩放至**完全覆盖** W×H（lanczos）→ 中心 crop 精确 W×H → `format=rgb24` 压平 alpha/防 16-bit 与调色板 PNG（cv2.imread 要求 8-bit RGB）。产物 ffprobe 断言 W×H+rgb24，**不符即删产物报错**，绝不让错尺寸图进 assets/bg/。常态链路只降采样（联动矩阵保证）；`postprocess` 子命令遇源<目标打印插值倍数警告后照做（离线补救场景）。

## 测试

```bash
python3 -m unittest discover -s imgen/tests -v   # 57 用例，零网络零扣费（mock http.server）
```

覆盖（方案 §9.3）：postprocess 三分支 golden+实跑断言；mock 全链（提交→轮询 0→1→2→下载→落位）；status=3 中文报错不重试不重复提交；4xx 立即上抛；URL 失效重试 ≤2 上抛；key 链优先级；付费门 0/1 次提交计数；--kind 载荷联动（bg4k=4K/16:9 等）；--ref 15 拒 14 过；空目录自建；--task-id 恢复零提交；batch 总价文案与 >50 中止。测试输入 PNG 由 ffmpeg 合成（KB 级，不落仓）；断言用 ffprobe（不依赖 PIL/numpy）。

## 依赖

`requests`（系统 python3 已装 2.32.5；仅 gen/batch 触网链需要，postprocess/key status 离线可跑）+ `ffmpeg`/`ffprobe`（8.x 已验证）+ stdlib。与 vtx 零依赖纪律不冲突（vtx 不 import imgen；imgen 不 import vtx）。

## 待集成事项

集成事项已于 2026-09-11 全部落地（根 README/init.py/config.example.json/edit.md 均已更新）。

## 已验证版本

- 2026-09-11：requests 2.32.5 / ffmpeg 8.1.2 / Python 3.13（仓基线 ≥3.9 兼容语法）。上游内化自 suchuang-image 2026-09 工作区快照（src/slime/），端点与参数规则以 README 表中行号为准。
