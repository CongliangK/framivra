# Fixed-wheel 已核归档音画（单件）

这是**已核归档音画**，不是 live producer、`current-composite`、`input_epoch` 或正式交付回执。固定 launcher 只返一件独立管理员预批准的准确 MP4；它不渲染、不解码、不读 `cache/`、`out/`、operator grant 或任意交付路径。读取时比较批准身份与当次观察的时间线及全部声明源的原始字节，不声称合成环境仍是生成时的环境、输入在返回后不变，或取得多文件原子快照。

## 独立审批与部署

1. 由可信操作方在私有环境生成正式成片并归档精确 `[start,end)`。独立管理员核对同一已批准时间线、全部声明源、正式生成证据和归档的**完整 decoded A/V QC**：对照正式成片的解码视频和正式终态音频/相位/增益，检查所有呈现帧、绝对帧切点对应的 48 kHz 样本端点、归档起点与持续时间、H.264 1080p + AAC 48 kHz 双声道及浏览器可播。没有等价证据就不要批准。现有 [Operator 正式视频段](preview-formal-segment.md) 可取视频来源，但它没有给音频、封装或 A/V 等价背书；这里不实施生成流程或复查 decoded QC。
2. 沿用[独立可信 fixed-wheel profile](preview-window-first-frame.md) 的 Linux launcher、`kernel.sha256`、`kernel.whl` 及 `runtime/`。在同一 owner 私有目录旁置且仅置以下固定授权文件（不在 session 或项目内；普通、单链接、no-follow、可信 owner 或 root、不可由组/其他用户写）：
   - `formal-archive.mp4`：已独立认证的 MP4 原样字节，最多 5 MiB；不得直接把 `out/` 或 `cache/` 路径通给 Host。
   - `formal-archive.json`：下列精确字段的 UTF-8 JSON，最多 16 KiB。`timeline` 是规范绝对 `.json` 路径（其父目录即绝对项目根），拒绝符号链接、`cache`、`out` 或路径上与 profile 重叠；不识别底层 bind mount 别名，可信部署须保证实际隔离。`content_version` 是 `sha256:` 加时间线文件**原始字节**摘要。`sources` 是全声明路径集合（包括未引用源，同字面路径只列一次）；全部为直连 `assets/` 下的普通 `.mp4`/`.wav`，basename 为 1–96 位 ASCII 字母数字、点、下划线、连字符且以字母数字开始（不含扩展名）。时间线最多 12 项源声明（即使重复路径），每源 ≤64 MiB，总 ≤256 MiB，时间线 ≤1 MiB。禁止 symlink、硬链接、插件、扩展键、GFX、外部字幕和额外依赖。支持 1080p v2 和内联字幕，时基限 30/1 或 30000/1001，区间在工程内且视觉时长严格为 1–4 秒（以有理数比较）。只取本地支持的工程文件系统。源格式的自包含性及内容由管理员核对，消费不打开其内嵌引用。
   - `formal-archive.sha256`：独立管理员发布的 manifest **原始字节** SHA-256，一行小写 64 hex + LF。把它当作审批锚；不要由 Host 或消费端对收到的 manifest 现场补算写入。wheel pin/投影来自可信部署 `--verify`，不是生成者的自报。普通预览不依赖这三个文件。

```json
{
  "schema": "framivra.preview-formal-archive.manifest/v1",
  "scope": "operator-approved-formal-av-archive",
  "timeline": "/absolute/project/timeline.json",
  "content_version": "sha256:<64 lowercase hex>",
  "start": 0,
  "end": 60,
  "fps": {"num": 30, "den": 1},
  "sources": {
    "assets/video.mp4": {"sha256": "<64 lowercase hex>", "byte_length": 100000},
    "assets/sound.wav": {"sha256": "<64 lowercase hex>", "byte_length": 768044}
  },
  "sha256": "<64 lowercase hex of the MP4>",
  "byte_length": 200000,
  "wheel_sha256": "<64 lowercase hex>",
  "runtime_projection_sha256": "<64 lowercase hex>"
}
```

Host 侧只由受信 owner 为精确 session/timeline 放行独立路由；此放行不代替管理员的归档锚，也不能转用 operator 生产权限。两项授权分别配置，但 launcher 不证明由不同自然人批准。nonce 仅作同请求关联，不是授权。固定 profile 仍要求操作方阻止未授权同 UID 写入；受信 launcher 不是任意同 UID 代码的沙箱。取消或任一复核失败均不能消费结果。

## 固定 wire v1

```bash
"$TRUSTED_PREVIEW_LAUNCHER" --formal-archive-capabilities
"$TRUSTED_PREVIEW_LAUNCHER" -t "$ABS_TIMELINE" timeline preview-formal-archive \
  --frame "$START" --expected-content-version="$CONTENT_VERSION" \
  --read-approved-archive --nonce="$REQUEST_NONCE"
```

探测成功退出 0，stdout 是一行 JSON：`{schema:"framivra.preview-formal-archive.capabilities/v1",available,launcher,wheel_sha256,runtime_projection_sha256}`。`launcher` 固定 `framivra.preview-window.fixed-wheel/v1`。`available:true` 只说明当前 fixed wheel 能读且固定归档文件当次通过验证，**不读工程、不认证当前输入**。缺独立授权、旧 wheel 或归档不合格时 `available:false`；普通 v1 capabilities 完全不变。

消费只接受精确参数和顺序；`--frame` 必须等于批准的 `start`，不按包含关系或近邻选择，`--expected-content-version` 必须等于批准版本，`nonce` 是 32 位小写 hex 并按原样返还。先验证固定 profile、清单 pin 与归档，再对比 selector；不访问未批准 selector。随后以单链接 no-follow fd 核对精确版本与全部源字节；发布前复核 wheel、归档、同一组项目 fd 的完整字节和路径身份，写完 stdout 后再校验已持 fd 的绑定才成功退出。

消费成功要求退出 0、正常结算、恰好一行完整 stdout JSON（含 LF ≤8 MiB），精确字段如下：

| 字段 | 含义 |
|---|---|
| `schema`, `ok`, `nonce` | `framivra.preview-formal-archive/v1`, `true`, 请求 nonce |
| `content_version`, `start`, `end`, `fps` | 已核归档的精确时间线版本、绝对帧半开区间、精确 `{num,den}` |
| `manifest_sha256`, `launcher`, `wheel_sha256`, `runtime_projection_sha256` | 归档批准锚和本次固定运行投影 |
| `width`, `height`, `audio_sample_rate`, `audio_channels` | 固定 profile `1920`, `1080`, `48000`, `2`；音视频实测属于独立 QC，不是此入口新做的检测 |
| `mime`, `byte_length`, `sha256`, `base64` | 固定 `video/mp4`、原媒体字节数及 64 位小写 SHA-256、原样媒体的标准 base64 |

正常拒绝退出 1、只返 `{schema,ok:false,nonce,code}`，`code` 是 `archive.unavailable`（批准/归档/部署无法复核）、`archive.selector_mismatch` 或 `archive.inputs_mismatch`。入口、启动或传输故障可能退出 2 或无完整响应。任何非零、截断、超限、身份/nonce 不匹配都不可消费；不可用时也不回退读项目产物，或把别的渲染升级为归档资格。
