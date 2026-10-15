# imgen —— 生图 API 生图 + cover-crop 后处理

> **v2 结构（P2a 批次 2 起本目录迁入 `apps/imgen/`）**：manifest 见 `app.json`（kind=app；健康检查 = `imgen_cli.py --help`）。随迁清除 `spec_from_file_location`（v2 §1.4 冻结禁令）：imgen_cli.py 加载 secrets.py 与 tests/_loader.py、init.sh 探测内嵌加载统一改为 **app 根 sys.path 注入 + 普通 import**（app 目录注入不涉内核双模块，允许；保留解析落点校验哨兵防 stdlib 同名 secrets 串味）。

脚本阶段写 prompt → 生图 API（api.wuyinkeji.com，nano / gpt-image / GPT-Image-2.5 三系五模型）付费生图（0.1 或 0.3 元/张按模型）→ 原图即下（URL 2 小时失效）→ ffmpeg lanczos cover-crop 到**精确尺寸** → 硬对接下游：
- `assets/bg/background_4k.png`（3840×2160 8-bit RGB PNG）→ `apps/matting/run_full_bg.sh` 抠像逐像素合成（cv2.imread 直读、无 resize，**像素契约不过即崩**）；
- `assets/bg/background_1080p.png`（1920×1080）/ `background_vertical.png`（1080×1920）→ 口播链 / gfx 底图；
- `assets/imgen/<UTC>-<n>.png` → 原图留档（API 原图不可再生，属资产非缓存，不入 cache/）。

## 文件清单

| 文件 | 用途 |
|---|---|
| `imgen_cli.py` | 总入口：`gen` / `batch` / `postprocess` / `key status`；编排线程池 10/10/5 |
| `api_client.py` | ImgenClient：异步提交 + 轮询 detail + 下载 + 错误分类 |
| `models.py` | 模型/端点/参数定义 + kind 联动矩阵 |
| `secrets.py` | key 解析链 `--key > SC_API_KEY > NANOBANANA2_API_KEY > keyring > ~/.config/vtx-imgen/secrets.json(0600)` |
| `reference_upload.py` | 本地参考图外链：配置自有服务器通道时 scp 优先，默认 tmpfiles.org（SC_PROXY/本地 7890 兜底）；详见下方「参考图外链通道」 |
| `postprocess.py` | ffmpeg lanczos cover-crop → 精确 3840×2160 / 1920×1080 / 1080×1920 + ffprobe 断言 |
| `init.sh` | 幂等初始化（秒级，无模型下载） |
| `tests/` | 71 用例：models/secrets/postprocess 单测 + 本地 mock http.server 全链 |

**实现说明**：本模块由独立参考实现改写内化维护，运行时零外部仓依赖。相对参考实现的改动：api_base 可注入（mock 测试/换网关不改代码）、下载失败重试 ≤2 次后上抛、`resolve_api_key` 空白串视为未提供、ffprobe 对无效图返回 0 尺寸的显式拦截。

## API 模型表

| 模型 | 提交端点 | size 语义 | aspectRatio | 参考图 | 单价 |
|---|---|---|---|---|---|
| `nano`（NanoBanana2 / Gemini 底座，默认） | `POST /api/async/image_nanoBanana2`（上游 `core/models.py:36`） | `1K`/`2K`/`4K` 分辨率档 | 交（非 `auto` 时；`auto/1:1/16:9/9:16/…`） | urls ≤14 张 | 0.1 元 |
| `gpt-image`（GPT-Image-2 / OpenAI 底座） | `POST /api/async/image_gpt`（上游 `core/models.py:43`，**无 `_image` 后缀**） | **纯比例**（`auto/1:1/16:9/9:16/…`，非像素） | **不交** | urls ≤14 张（拒绝 data URL） | 0.1 元 |
| `gpt-2.5`（GPT-Image-2.5 基础版，[/doc/78](https://api.wuyinkeji.com/doc/78)） | `POST /api/async/image_gpt_2.5` | —（比例走 aspectRatio，官方仅 1K 档尺寸） | 交（比例或显式 `WxH`） | urls ≤14 张 | 0.1 元 |
| `gpt-2.5-sunburst`（[/doc/79](https://api.wuyinkeji.com/doc/79)，慢速高精、适合编辑图） | `POST /api/async/image_gpt_2.5_sunburst` | —（1K/2K/4K，比例或 `WxH`） | 交 | urls ≤14 张 | 0.3 元 |
| `gpt-2.5-flare`（[/doc/80](https://api.wuyinkeji.com/doc/80)，快速、质量好） | `POST /api/async/image_gpt_2.5_flare` | —（1K/2K/4K，比例或 `WxH`） | 交 | urls ≤14 张 | 0.3 元 |

2.5 系参数差异（提交前 `build_payload` 集中校验）：

- `quality`：仅 sunburst（`low/medium/high/xhigh/max`）与 flare（`low/medium/high`），默认 `medium`；**基础版无此参数**。
- `background`（`auto`/`transparent`，auto 省略不交）与 `mask`（遮罩图公网 URL，局部重绘）：仅 sunburst/flare。
- 质量差异（来自官方文案，未实测横评）：基础版=标准档最便宜；flare=生成速度快、质量好；sunburst=生成质量好、速度慢、精度很高、适合编辑图片。选型建议：批量出图/背景用基础版或 flare，参考图精修/局部重绘用 sunburst（可配 `mask`+`quality high`）。
- 要 2K/4K：`--ratio 3840x2160` 这类显式像素串（sunburst/flare 支持）；基础版只有 1K 档。

轮询 `GET /api/async/detail?id=<task_id>`：status `0/1`=排队/生成中、`2`=成功、`3`=**审核拒绝（不退款不重试）**、`-1`=失败。`result` 字段四形态防御解析（str / list[str] / list[dict] / wrapper dict）。错误分类：429/5xx 可重试，4xx 立即上抛。鉴权头 `Authorization: <裸key>`（无 Bearer）。

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
bash apps/imgen/init.sh          # 加 --net 可选开启 GET api.wuyinkeji.com 连通探测（不扣费）

# key 本地探测（不触网不扣费；展示仅尾 4 位）
env python3 apps/imgen/imgen_cli.py key status

# 4K 背景图（提交前有付费确认门：无 --yes 时交互 y/N，非 TTY 必须 --yes）
env python3 apps/imgen/imgen_cli.py gen "深色科技感演播室背景，无人物，景深虚化" \
    --out <项目目录> --kind bg4k --yes
# → <项目>/assets/bg/background_4k.png（3840×2160 rgb24，ffprobe 断言通过）

# 1080p 横版 / 竖版
env python3 apps/imgen/imgen_cli.py gen "..." --out <项目> --kind bg1080p --yes
env python3 apps/imgen/imgen_cli.py gen "..." --out <项目> --kind vertical --yes

# 带参考图（本地文件自动外链：默认 tmpfiles.org，可配自有服务器通道；URL 直传；≤14 张）
env python3 apps/imgen/imgen_cli.py gen "保留构图，改成水彩" --out <项目> --kind raw \
    --ref ./ref1.png https://example.com/ref2.jpg --yes

# GPT-Image-2.5 三端点
env python3 apps/imgen/imgen_cli.py gen "..." --out <项目> --model gpt-2.5 --ratio 16:9 --yes          # 基础版 0.1 元
env python3 apps/imgen/imgen_cli.py gen "..." --out <项目> --model gpt-2.5-flare --quality high --yes  # 快速 0.3 元
env python3 apps/imgen/imgen_cli.py gen "编辑这张图" --out <项目> --model gpt-2.5-sunburst \
    --quality xhigh --background transparent --mask https://example.com/mask.png \
    --ref ./in.png --yes                                                                       # 精修 0.3 元
env python3 apps/imgen/imgen_cli.py gen "..." --out <项目> --model gpt-2.5-sunburst \
    --ratio 3840x2160 --yes   # 显式 4K（WxH 像素串）

# 批量（[{prompt,kind,model,size,ratio,ref,quality,background,mask}, ...]；>50 张二次确认；
# 混合模型按模型单价逐行列价）
env python3 apps/imgen/imgen_cli.py batch prompts.json --out <项目> --yes

# 离线后处理（免 API、不扣费；源<目标时打印插值倍数警告后照做）
env python3 apps/imgen/imgen_cli.py postprocess <原图.png> --kind bg4k -o /tmp/out.png

# 断线恢复（URL 2h 失效内：只重轮询+下载+后处理，零新提交零扣费；task_id 见记账文件）
env python3 apps/imgen/imgen_cli.py gen "<原prompt>" --out <项目> --kind bg4k --task-id <task_id>
```

环境变量（一般无需设置）：`IMGEN_API_BASE`（覆盖 API 地址，测试/mock 用）、`IMGEN_CONFIG`（覆盖 config.local.json 路径）、`IMGEN_POLL_INTERVAL`/`IMGEN_POLL_TIMEOUT`（轮询节奏）、`SC_PROXY` 等参考图上传代理、`VT_OWN_UPLOAD_SSH_TARGET`/`VT_OWN_UPLOAD_BASE_URL`（自有服务器通道，见下节）。

## 参考图外链通道（reference_upload.py）

- **默认：tmpfiles.org**——零配置即用，属第三方临时文件服务。**隐私提示：上传的参考图会被传到第三方公网服务且短期内可被任意人访问（文件 1 小时左右过期），请勿上传含敏感/私密内容的图片**；常被墙，支持 `SC_PROXY`/`HTTPS_PROXY`/`HTTP_PROXY` 代理与本地 7890 兜底重试。
- **可选：自有服务器通道**——设置 `VT_OWN_UPLOAD_SSH_TARGET`（ssh 目标，如 `my-host`）与 `VT_OWN_UPLOAD_BASE_URL`（如 `https://example.com/sc-img`）后启用：scp 上传 → 公网 URL 回验。两个变量任一未设置即整体跳过、只走 tmpfiles.org；上传失败也自动回退 tmpfiles.org（`SC_REF_UPLOAD_TARGET=tmpfiles` 可强制只走 tmpfiles）。可选 `VT_OWN_UPLOAD_REMOTE_DIR` 覆盖远端目录（默认 `/var/www/sc-img`）。

## 付费纪律（硬门）

- 提交前按模型单价打印费用（混合模型 batch 逐行列价）+「**审核拒绝（status=3）不退款、不重试**」提示；无 `--yes` 时交互 `y/N`；**非 TTY 且无 --yes 直接中止（0 次提交，退出码 2）**。
- batch 单次 >50 张：即使 `--yes` 也需 TTY 二次确认。
- config `imgen.confirm_before_submit: false` 可关闭交互（相当于自动 yes，自担风险）。
- job 记账 → `<项目>/cache/reports/imgen_jobs_<runid>.json`（task_id/payload/状态/产物/费用，原子写）；下载临时件 → `<项目>/cache/tmp/<runid>/`（后处理完成即删）。
- 真实 API 冒烟为**独立人工步骤**（--yes 单张 0.1 元，用户在场授权），不在自动测试内——本模块全部测试走本地 mock http.server。

## key 管理

解析链优先级：`--key`（值或文件路径）> `SC_API_KEY` > `NANOBANANA2_API_KEY` > 系统 keyring（service=`vtx-imgen`, account=`api-key`）> `~/.config/vtx-imgen/secrets.json`（0600，`{"api_key": "..."}`）。key 永不入仓不入日志（打印仅尾 4 位）。推荐 `export SC_API_KEY=...` 入 shell rc。

## 后处理（postprocess.py）

```
ffmpeg -y -i <原始下载.png> \
  -vf "scale=W:H:force_original_aspect_ratio=increase:flags=lanczos,crop=W:H,format=rgb24" \
  -frames:v 1 <out.png>
```

cover 语义：等比缩放至**完全覆盖** W×H（lanczos）→ 中心 crop 精确 W×H → `format=rgb24` 压平 alpha/防 16-bit 与调色板 PNG（cv2.imread 要求 8-bit RGB）。产物 ffprobe 断言 W×H+rgb24，**不符即删产物报错**，绝不让错尺寸图进 assets/bg/。常态链路只降采样（联动矩阵保证）；`postprocess` 子命令遇源<目标打印插值倍数警告后照做（离线补救场景）。

## 测试

```bash
cd apps/imgen && env python3 -m unittest discover -s tests -t . -v   # 71 用例，零网络零扣费（mock http.server）
# 或仓根统一入口：bash scripts/run_all_tests.sh（自动发现 apps/*/tests）
```

覆盖（方案 §9.3）：postprocess 三分支 golden+实跑断言；mock 全链（提交→轮询 0→1→2→下载→落位）；status=3 中文报错不重试不重复提交；4xx 立即上抛；URL 失效重试 ≤2 上抛；key 链优先级；付费门 0/1 次提交计数；--kind 载荷联动（bg4k=4K/16:9 等）；--ref 15 拒 14 过；空目录自建；--task-id 恢复零提交；batch 总价文案与 >50 中止。测试输入 PNG 由 ffmpeg 合成（KB 级，不落仓）；断言用 ffprobe（不依赖 PIL/numpy）。

## 依赖

**app 级（manifest requires，doctor 可探测）**：`requests`（系统 python3 已装 2.32.5；仅 gen/batch 触网链需要，postprocess/key status 离线可跑）+ `ffmpeg`/`ffprobe`（8.x 已验证）+ stdlib。与 vtx 零依赖纪律不冲突（vtx 不 import imgen；imgen 不 import vtx，仅经 vtx.pluginlib.config 统一加载 config）。

## 健康检查

`env python3 apps/imgen/imgen_cli.py --help` 退出码 0（app.json health.command；不触网不扣费）。key 链配置态另探：`imgen_cli.py key status`（只读本地）。

## 待集成事项

集成事项已于 2026-09-11 全部落地（根 README/init.py/config.example.json/edit.md 均已更新）。

## 已验证版本

- 2026-09-11：requests 2.32.5 / ffmpeg 8.1.2 / Python 3.13（仓基线 ≥3.9 兼容语法）。端点与参数规则以本 README「API 模型表」为准。
- 2026-09-13（封面产线实测，voice-ime/cover/COVER-REPORT.md）：**flare 端点对无害纯抽象画中文提示词两连 status=3 审核拒绝**（不退款、排队数分钟后转拒，判供应商审核误杀；同 prompt 在 sunburst 通过）——重要生成勿单独依赖 flare；**sunburst 端点忽略 payload 中的 `aspectRatio`（含 2048x1280 显式像素串）返回 1K 方图**（记账 JSON 可证载荷正确）——非 1:1 用途需下游 cover-crop（放大须标注倍数）。
