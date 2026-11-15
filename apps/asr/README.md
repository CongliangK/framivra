# asr —— Qwen3-ASR + ForcedAligner 字幕产线

组件声明见 `app.json`（kind=app）；健康检查为 `asr_cli.py --help`，不加载模型。
`asr.enabled` 在所有执行入口生效；环境、模型与输入法资产隔离。HTTP 请求前核对本机进程身份及监听 socket 归属，GPU/显存准入只读 `nvidia-smi` 预算，不访问 IME 端点。准入实现唯一入口为 `independence.py`；拒绝的配置与准备边界见下文。

音频/视频 → sidecar 转写（字级时间戳）→ 文本规范化 → 锚定式断句聚合 → 帧级
`timeline.json subtitles[]` 合并（保护其他字段、写前备份）+ 独立 SRT。模块自有轻量
sidecar（stdlib http.server，默认 **127.0.0.1:18085**；18081 为语音输入法 sidecar
专用端口，本仓绝不绑定/探测/复用——issue #140/#141 铁律）。

模块协议与准备边界以本页为准；[原始设计记录](https://github.com/CongliangK/framivra/blob/c476b4f5f13687c61c9be02f4015e289a176ddc8/docs/plans/2026-09-11-三大模块整合方案.md)仅供溯源。

## 文件清单

| 文件 | 用途 |
|---|---|
| `sidecar_server.py` | 转写服务：Qwen3-ASR + ForcedAligner，`/health` `/warm` `/transcribe`；words[] 加工（text↔items 对齐）；OOM 换挡 + 空闲卸载。**运行于 qwen-asr 解释器**（通常由 client 自动拉起） |
| `client.py` | HTTP 客户端（stdlib http.client；不走环境代理、不跟随重定向）+ 音频抽取（ffmpeg 16k mono wav）+ sidecar 进程管理（pid 三因子归属）+ config 容错读取；`ensure_sidecar/transcribe/warm` 受 enabled 门禁与拉起前显存预检约束（issue #142） |
| `independence.py` | **独立性与安全准入单一接口（issue #141/#142）**：IME 锚定路径拒绝、遗留键迁移错误、base_url 18081 拒绝、解释器依赖来源验证、模型链接关系校验（`verify_dir_assets`）、PID 三因子归属、`asr.enabled` 严格门禁（`asr_enabled/require_enabled`）、通用 GPU/显存准入（`gpu_admission`+并发 `admission_lease`）、资产清单（版本/来源/SHA256）接口 |
| `textnorm.py` | 文本规范化 N1-N7（内化自 ibus-voice-ime text_postprocess 确定性子集 + ctypes libopencc 繁简软依赖） |
| `segline.py` | 字级时间戳 → 断句聚合 → 帧（锚定式；split_line/allocate 内化自 voice-ime make_subs.py，时间改字级锚定） |
| `srt_out.py` | 独立 SRT 1.0 写出（HH:MM:SS,mmm） |
| `tl_merge.py` | subtitles[] 适配器（只声明字幕范围 + 字幕备份；经公开 timeline publisher 提交） |
| `asr_cli.py` | 总入口：`sidecar` / `transcribe` / `srt` / `timeline` / `batch` |
| `init.sh` | 已有 Framivra 专属解释器/模型检查与本地 sidecar 冒烟；不补齐缺失资产，旧 `--download` 入口为 **BLOCKED**；**绝不借用输入法环境/模型** |
| `tests/` | 单测（零模型零 GPU 零网络）+ fixtures |

## 用法

以下运行命令要求已独立准备并审核环境与模型，且显式启用 ASR；准备边界见下文「开启步骤」。

```bash
bash apps/asr/init.sh                                        # 检查已有独立资产；含本地 sidecar 冒烟，非安装器
env python3 apps/asr/asr_cli.py sidecar start                # 拉起 sidecar（日志 ~/.local/state/framivra/asr/sidecar.log）
env python3 apps/asr/asr_cli.py transcribe 口播.wav --timestamps --json
env python3 apps/asr/asr_cli.py srt 口播.mp4 -o out.srt      # 音视频皆可（自动抽 16k wav）
env python3 apps/asr/asr_cli.py timeline 成片.wav -t <项目>/timeline.json --dry-run
env python3 apps/asr/asr_cli.py batch <目录|清单.txt> -o <输出目录>
```

`timeline` 子命令：读 `project.fps/duration_frames`，subtitles[] 就地合并（旧值备份进
`<项目>/cache/reports/subtitles_backup_<UTC秒>-<PID>-<唯一后缀>.json`），打印摘要（窗数/字数/最快速率/
最宽行）；默认替换，`--append` 保留旧字幕后按 in 排序。随后用 `vtx validate && audit` 复核（阅读速度 ≤9 字/s 含标点口径、行宽等）。

写入口已迁移至公开 `read_timeline_for_write` → `timeline_write_plan` → `timeline_transaction`，与 `vtx store` 共用 journal；仅结构验证，不证明素材存在、插件可渲染。转录前固定 raw snapshot/base/op_id，耗时返回后不重读升级 base；CAS 冲突 exit1，不自动 rebase，timeline 未改但耗时产物可能已生成。外部 `subtitles_src` 提前拒绝，不删除路径。legacy 保留未知字段/Unicode 源 ID、不新增身份；managed 仅 owner 字幕范围 reconcile，已有显式 ID 保内容，新字幕 ID 在请求固定，不按数组位置猜 ID。

Python 旧四参 `merge_subtitles` 仍可调用，但无早期参数只在 helper 入口取 base，不保障调用前耗时窗口；长任务必须传同一 `snapshot/expected_base/op_id`，仅有 base 无 snapshot 拒绝。`--dry-run` 纯 plan 不写 timeline/备份/history；内容相同走只读 base fence。字幕数组备份以标准库独占创建（含撞名/符号链接时重选唯一后缀），成功写完并关闭后才提交 timeline，每个返回路径保留该次旧字幕。备份 I/O 失败不提交 timeline、不返回备份路径；为避免误删被替换的外来文件，可能留下未返回的不完整文件供人工检查。字幕数组备份与审计报告不是 timeline publisher 对象，也不与主文件原子提交；备份成功后的 CAS 失败可能保留完整备份，由异常 backup 字段说明。报错 `outcome_unknown/recovery_required` 不代表未提交：保留同一 snapshot/输入/op_id，用公开 store status/recover 查/恢复同 operation 后同请求重传，不能盲造新 op；helper 异常带 receipt/request/backup。

独立 `tl_merge.py -t <项目>/timeline.json [--dry-run]` 的 stdin 是原始 UTF-8 字幕 JSON 数组；经公共 `parse_write_json` 在普通解码前拒绝重复键、非有限数和会在写回时损失十进制值的高精度 token（包括未知嵌套扩展），错误 exit1，timeline/备份/history 均不写。0.1、0.5、安全指数及大整数沿用内核写解析合同，不承诺保留数值词法。Python `merge_subtitles(new_subs=...)` 接收已有 Python 值，不能追溯调用前已发生的舍入；转录总入口与旧参数/默认替换行为不变。

## sidecar 协议（默认 127.0.0.1:18085，可配 config `asr.base_url`）

客户端仅接受 `http://127.0.0.1:<port>` 或 `http://localhost:<port>`（离线归一到
IPv4 loopback）。自有端口可配；远程主机、IPv6/其他回环别名、路径前缀、凭据、查询和
fragment 均拒绝。每次 HTTP 前要求项目 PID/启动时刻/脚本身份及 LISTEN socket inode
归属于同一进程；无可读 Linux `/proc` 证据时 fail closed，不先发 health 猜测归属。
手动常驻启动须使用专属解释器 `-I`，且没有有效归属记录时客户端不会探测或复用；建议
通过 `asr_cli sidecar start` 启动。此元数据前检不消除检查到连接之间的竞态，遵循仓库
[不新增本地 TOCTOU 加固的边界](../../CONTRIBUTING.md#信号实验隔离)，不宣称竞态证明。

| 端点 | 方法 | 请求 | 响应 |
|---|---|---|---|
| `/health` | GET | — | `{"status":"ok","model":"…","aligner":"…或null","port":18085,"version":1}` |
| `/warm` | POST | `{}` | `{"ok":true}`（模型预上 GPU） |
| `/transcribe` | POST | `{"audio":"<本地wav路径>","language":"Chinese","timestamps":true}` | `{"text":"…","words":[{"w":"字","s":0.833,"e":0.913},…],"degraded_ranges":[[起,止],…],"duration_s":12.34}` |

- 错误统一 `{"error":"中文可行动信息"}` + 4xx/5xx；音频不存在 400；`timestamps:true`
  但 aligner 未加载 409（须独立准备并配置 aligner，见「开启步骤」；init 不会下载）。
  对齐时间窗非有限数、倒退、负值、越过实际时长（仅容许 1ms 舍入误差），或无法核实时长，
  同样返回 409；不会通过排序、夹取或均分伪造有效字幕，CLI 不写 SRT/审计产物。
- **words 为 sidecar 加工格式，非 qwen-asr 原生 items**（0.0.6 原生为 token 级、无标点、秒
  制）：words[] = 单字粒度（拉丁连续串拆单字符共享 token 时间窗）、含标点（零宽锚 =
  前实字 {s,e}）、单位秒 3 位小数；`degraded_ranges` 为 text↔items 失配区段（text 字符
  索引 [起,止]，该段字时间已线性插值兜底）；全失配时 `words=[]`（客户端回退整句均分
  并显著告警）。
- **长音频直接透传**：qwen-asr 0.0.6 按有时间戳 180s / 纯文本 1200s 的目标长度，
  在 ±5s 低能量边界分块并拼回；这不是总输入时长上限，也不保证识别文字完整。
  sidecar/client 零切分逻辑，每块默认最多生成 1024 tokens；单调时间戳也不能证明无漏转。
- **端口防御（issue #141）**：`--port 18081` 直接拒绝（fail-closed，不探测）；启动
  前探测端口占用，被占则报错退出（不静默换端口）。共驻降档链已删除（探测输入法本身
  即被禁止）。
- **enabled 门禁（issue #142，实际执行非纸面默认）**：下表入口在 `asr.enabled`
  未启用（严格解析：仅 `true/1/yes/on`；**未配置/坏配置一律视为禁用**）时零请求零
  拉起零加载，直接中文诊断退出：

  | 入口 | 禁用态行为 | 豁免（语义明确，非绕过） |
  |---|---|---|
  | `asr_cli transcribe/srt/timeline/batch` | 拒绝（exit 2），零 HTTP | — |
  | `asr_cli sidecar start` / 库 `client.ensure_sidecar` | 拒绝，零探测零拉起 | — |
  | 库 `client.transcribe` / `client.warm` | 拒绝，零 POST | — |
  | 直接运行 `sidecar_server.py`（长驻） | 拒绝绑定（18081/IME 路径硬边界先行诊断） | `--once-health`=init 准备冒烟（零模型加载零 GPU） |
  | `asr_cli sidecar health/status` | **允许**（只读探测自有 base_url，零 POST） | 本行即豁免语义 |
  | `asr_cli sidecar stop` | **允许**（回收自有残留服务，安全收敛动作） | 本行即豁免语义 |
  | `vt.py doctor`（asr 段） | 不探测 sidecar（零 HTTP），报告禁用态 | — |

- **进程归属三因子（issue #142）**：pid 记录（JSON：`pid/start_time/port`，落
  `~/.local/state/framivra/asr/`）+ `/proc cmdline`（sidecar_server.py+端口）+
  `/proc stat` 启动时刻比对。stop/restart/故障回收前必须三因子全过：陈旧 PID、
  PID 重用（启动时刻失配）、伪造记录、端口被外部进程抢答 → 只报错不发信号，
  记录保留供排查；健康但无法归属的服务一律拒绝复用（零 warm/transcribe 试错）。
- **通用资源准入（issue #142，与 IME 端点零依赖）**：模型加载（client 拉起前预检
  + sidecar `_load_locked` 准入）前经 `gpu_admission` 只读 `nvidia-smi` 比对显存
  预算（`asr.vram_budget_mib` 配置优先并传入服务，缺省粗估：1.7B≈6144 / 0.6B≈3072 MiB）：
  使用驱动报告的 `memory.free`，不把 `total-used` 中的驱动保留量算作可用显存。
  预算不是硬上限或显存预留，长音频应按实测显式配置（下述 0.6B 验收使用 8192 MiB）；
  不足 → 安全拒绝（绝不停止/卸载/重配任何外部服务，含语音输入法）；`nvidia-smi`
  不可用且设备为 GPU → **fail-closed 拒绝**（不拿「先 OOM 再换小模型」当唯一保护，
  诊断给出 CPU 出路）；设备 `cpu` → 显存门 N/A 放行。并发准入经项目状态根 `flock`
  （`admission_lease`）串行「查显存→加载」临界区；运行期 OOM 只把自有模型迁回 CPU
  并清自有显存缓存（`recover_after_oom`），返回 503 中文诊断；加载 OOM 的同根
  companion 降档保留为**次级**保护。

## 文本规范化规则表（textnorm.py）

| # | 规则 | 行为 |
|---|---|---|
| N1 | 行首标点删除 | `(^|\n)[，,、。．.!?！？；;：:…]+` 删除（IME 规则增强为全标点集；断言行首绝不出标点，单测硬断言） |
| N2 | 重复标点折叠 | 取最强 `？>！>。>；>：>，`（「！！！」→「！」、「，。」→「。」） |
| N3 | 空格清理 | CJK 标点前后空白删；CJK-CJK 间空白删；**CJK↔拉丁边界空格保留**（本模块规格，与 IME 原实现不同） |
| N4 | 长无标点软切标记 | >24 字无标点段记录切点候选（只记录不改文本，断句硬切前参考） |
| N5 | 口水词清除（默认开，`--no-filler` 关） | 语气词组 嗯/呃/啊/额/哦/唔+（词内+边界感知句间）；边界感知 discourse 组 那个/这个/就是、对吧/怎么说呢/然后呢 等（仅两侧为标点/空白/边界时删，不伤正文同形词） |
| N6 | 繁简统一（默认关，`--t2s` 开） | 仅作用于聚合完成后的显示文本（不动锚定字流/帧）；ctypes libopencc 软依赖，缺库跳过并告警（Fedora：`sudo dnf install opencc`） |
| N7 | 行尾标点 | 整条转写尾无句末标点不加；行尾句中类（，、；：）截去，句末类（。？！…）保留 |

断句（segline.py，锚定式）：句末标点切句 → 句内超 limit=18 贪心回退句中标点 →
无标点先试 N4 软切再硬切（latin_safe_cut 回退拉丁/数字词边界，「Qwen3-ASR」不被拆）；
行时间 = 首字 s … 末字 e；`in=floor(s*fps)`、`out=ceil(e*fps)`；最短 18 帧（0.6s，
audit 同阈值）；相邻去重叠不破 `in+min_dur` 下限（极冲突保留重叠+告警）；速率 ≤9 字/s
（len 含标点，audit 同口径，超限有隙扩隙/无隙告警）；越界整行丢弃（保序）。

## 产物落位

- 临时 16k wav → `<项目>/cache/tmp/<runid>/`（退出即删；输入不在项目内退 /tmp 小件）；
- SRT / 审计摘要 / timeline 备份 / batch 报告 → `<项目>/cache/reports/`（无项目上下文时落输出同目录）；
- 合并后的 subtitles[] 只写回 timeline.json 本体；
- sidecar 日志/PID → `~/.local/state/framivra/asr/`（项目状态根，不进仓库不进项目，
  与输入法的任何状态目录完全独立）。

## 依赖（默认关闭）

- **app 级（manifest requires，doctor 可探测）**：ffmpeg / ffprobe（16k wav 抽取与时长探测）；系统 python3 ≥3.9 stdlib（CLI 全部子命令零第三方依赖）。
- **sidecar 级（专属 venv 环境依赖，不进 requires，README 落实）**：qwen-asr + torch
  装在 **Framivra 专属环境 `apps/asr/.venv`**（绝不借用输入法环境；依赖来源经
  `independence.verify_module_from_own_env` 验证必须来自该环境根内）；Qwen3-ASR-1.7B/0.6B +
  Qwen3-ForcedAligner-0.6B 模型装在**专属根 `~/.local/share/framivra/asr/models`**
  （同型号允许独立部署，禁止 symlink/hardlink/共享缓存借用输入法权重——
  `independence.py` 资产清单接口固定版本/来源/SHA256 记账）；`bash apps/asr/init.sh`
  仅检查已有独立资产并冒烟，不创建环境、不安装包、不下载模型。
- **旧配置迁移**：`VT_ASR_IME_ROOT` / `asr.ime_root` 出现即报迁移错误（fail-closed，
  不静默换算）；`asr.python` / `asr.model_dir` / `asr.aligner_dir` / `asr.base_url`
  显式配置同样过独立性校验，指向输入法资产/端口即拒绝。

## 健康检查

`env python3 apps/asr/asr_cli.py --help` 退出码 0（app.json health.command；CLI 面系统 python3 即可跑，不涉 sidecar/模型）。sidecar 运行态另经 `asr_cli.py sidecar status` / `GET /health` 探测。

## 开启步骤（默认关闭）

> `asr.enabled` 自 #142 起为实际执行门禁（见上表）；`asr.device_map`（默认
> `cuda:0`，接受 `"cpu"`）与 `asr.vram_budget_mib`（正数 MiB，缺省按档
> 估计）参与资源准入。CPU 参数通过显存门（N/A）不代表真实 CPU 转写已验收。

1. 先阅读 [第三方许可与模型下载边界](../../docs/third-party-licenses.md)。旧
   `bash apps/asr/init.sh --download` 在配置探测、建 venv、pip 和模型请求之前
   **BLOCKED（退出码 4）**；它不是可用的安装路线。以下 `plan <asset>` 仅离线显示
   单资产证据与限制，不下载、不安装，也不表示已获得用途许可：

   ```bash
   env python3 scripts/policy/model_license_policy.py plan qwen-asr-1.7b
   ```

   另行确认许可、固定官方快照、证据哈希和用途后，受支持的单资产下载接口才可使用；
   **固定快照下载不安装 qwen-asr/torch 运行环境，也不解除旧 init 下载门禁**。
   缺失的独立环境/依赖仍须另行审核和准备；不得借用输入法环境、模型或共享缓存。
2. 已独立准备资产后，`bash apps/asr/init.sh` 检查解释器 `config.asr.python` →
   `apps/asr/.venv`，模型 `config.asr.model_dir` → 专属根
   `~/.local/share/framivra/asr/models`（Qwen3-ASR-1.7B / 0.6B 任一档 +
   **Qwen3-ForcedAligner-0.6B**；显式 `asr.aligner_dir` 优先），并执行本地
   `--once-health` 冒烟。检查统一读取 `VTX_CONFIG`，缺失或不独立的资产先拒绝，
   不自动补齐；冒烟不加载真实模型、不验证转写质量。
3. 确认独立环境与资产就绪后，才把解释器/模型路径和显式启用项写进仓库根
   `config.local.json`（写配置是独立操作；init 不自动改）：
   `"asr": { "enabled": true, "python": "<apps/asr/.venv>/bin/python", "model_dir": "~/.local/share/framivra/asr/models" }`
   （`base_url` 默认 `http://127.0.0.1:18085`，无需写）。
4. 日常使用无需手动起 sidecar：`srt`/`timeline`/`batch` 会自动拉起并等健康。

空闲 5 分钟自动卸载到 CPU 释放显存；这与 CPU 实际推理验收不同。下述独立
CPU 冒烟不证明 GPU 支持、显存占用或输入法共驻安全；GPU 实测仍为 UNRUN。
与输入法的 GPU 竞争由 #142 通用资源准入处理，绝不停/降输入法。

## 已验证版本

### 2026-11-14 独立 CPU 验收（#140）

从空环境独立安装：Python 3.12.13、qwen-asr 0.0.6、torch 2.10.0+cpu、
transformers 4.57.6、accelerate 1.12.0、huggingface-hub 0.36.2；`pip check` 通过。
环境为本工单 checkout 的 `apps/asr/.venv`；模型、HOME、缓存和状态均在本次独立目录，
不引用输入法或共享模型缓存。95 个已安装分发包的目录均位于该 venv 内。

两个资产经许可单资产接口从官方固定快照分别下载（用途按 commercial），保留上游
README/LICENSE 和下载通知；两份模型卡证据 SHA256 均为
`5058416891bc47a2051557765997e8c42f8eb78a0e33c3e775bd17d4b0ba4d50`。
逐文件记录并通过 `verify_asset_entry` 复核；文件无 symlink、硬链接借用。

| 资产 | 官方 revision | model.safetensors SHA256 |
|---|---|---|
| Qwen/Qwen3-ASR-0.6B | `5eb144179a02acc5e5ba31e748d22b0cf3e303b0` | `79d6cbd4c98c7bbffe9db2edac07f56cd6637d0d5944b27f6c2b8353840323ea` |
| Qwen/Qwen3-ForcedAligner-0.6B | `c7cbfc2048c462b0d63a45797104fc9db3ad62b7` | `47831d0e82f96b20e9034dba01a075ee06436654719f6a68289e49f1b65ce0e7` |

真实执行 `asr_cli transcribe --timestamps --json --language English` 和 `srt`，
`device_map=cpu`、`CUDA_VISIBLE_DEVICES=''`、HF/Transformers 离线模式。
输入是本地 eSpeak NG 合成的 3.652 秒 16kHz mono PCM（不是 mock 转写结果），SHA256
`037dae442fe97cbcff724e3c714e1b50a6b9dd9ba0ae332414bdc4bbdee7b523`。
输出为 “The quick brown fox jumps over the lazy dog.”，44 个字符时间窗落在音频范围内，
起点单调、`degraded_ranges=[]`，SRT 成功生成。另一个独立合成的 7.702 秒中文输入
SHA256 `20948a3a1a2975d22ed561dfe9ad2888737f6694d1a04968fc5f307b36235cfc`，
正确输出“这是一个独立的语音识别测试。今天的天气很好。”，22 个字/标点时间窗通过同样检查，
无降级区间，中文 SRT 亦生成。真实 `init.sh` 检查通过；自有服务停止后 PID/记录已消失。

这是独立安装、模型加载、字级时间戳和字幕产物的短音频冒烟，不是识别质量基准。
1.7B、长音频、GPU 推理、真实 GPU 共驻/真实 OOM 均 UNRUN；未请求、启停或读取输入法。
资产清单由本次验收显式校验，不能据此声称每次启动会自动验证清单哈希。
旧 `init.sh --download` 仍 BLOCKED，本次没有解除许可门禁，也不构成全部传递依赖的法律放行。

### 独立 GPU 共存与长音频验收（2026-11-15）

在维护者[另行授权](https://github.com/CongliangK/framivra/issues/140#issuecomment-6886543715)后，
使用另建专属 CUDA 环境和上述同一批独立校验的权重；未读取、请求、启停或借用输入法。
RTX 4080 16GiB / 驱动 615.78.08，Python 3.12.13、torch 2.10.0+cu128、CUDA 12.8、
qwen-asr 0.0.6、transformers 4.57.6，离线推理，`device_map=cuda:0`、预算 8192 MiB。

- **受控共存通过**：另一个自有进程固定持有 2GiB CUDA 张量并低频执行矩阵计算，
  112 次计算及两端哨兵值、分配地址保持正常。整次采样最小空闲显存 5493 MiB；这不是硬隔离
  或显存预留证明，更不是输入法响应性测试。两模型初载/恢复均实测 `cuda:0/bfloat16`；
  测试将空闲卸载间隔设为 1s 后均回 CPU，分配量从约 3422MB 降至 9.57MB。
  32768 MiB 不可能预算在冷启动和恢复时均拒绝，无额外压力分配、无故意 OOM；恢复正常预算后转写成功。
- **自然长语音链路通过**：从 [LibriSpeech dev-clean](https://openslr.org/12/) 按数字 ID
  顺序取完整句段至首次超过 210s，35 段合计 216.300s（不是连续实录）；16kHz mono 输入 SHA256
  `ca0be25a0e8f7da7fb3f6d386bcb3e3fde3aac7e2ae01b1f8e55a7a638eec3e6`。
  与全部 663 个参考词比较，26 处词级编辑，样本 WER 3.92%，非逐字无误；每段至少 80.95%
  参考词匹配、末词保留。本次样本检查线为 WER ≤5%、每段匹配 ≥80%、末尾至多缺 2 词，
  不代表产品准确率承诺。3663 个字符时间窗有限、起点单调、在时长内，无降级区间；
  229 条 SRT 延伸到 216.167s，忽略格式后保留全部识别文字。
  同输入重算库分块为 175.11875s + 41.18125s，确实跨过有时间戳分块阈值。
  数据署名：LibriSpeech ©2014 Vassil Panayotov，[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)；
  本次只转为 PCM 并拼接，未将音频加入仓库。
- **失败样本保留**：214.584s 重复合成中文发生模型循环，230.301s 非重复合成英文发生严重漏转。
  前者还产生 201.28→176.19s 倒退时间窗，现已实测返回 409、CLI exit 1，零 SRT/审计写入；
  后者时间单调仍漏转，结构校验不能识别这类错误，**识别质量问题未修复**。
  自然样本通过不能抹去这两个失败，也不能视为任意长音频已全部验收。

本轮同时修复初载 aligner 未接收设备/精度、显存预算未传入服务、以及把驱动保留量算作空闲
（实测 `total-used` 多算 351MiB）的问题。实测命令为 `transcribe --timestamps --json`、`srt`；
仅自有服务被停止，PID/listener 与受控共存进程均已清理。1.7B、纯文本 >1200s 分块、
小时级音频、真实 OOM 和输入法响应性仍 UNRUN；不以样本结果承诺这些场景。

### 历史版本记录

以下当时借用 IME 环境的链路已废除，仅作 qwen-asr 库行为溯源：
- qwen-asr **0.0.6**（历史实测于 Python 3.14.7）——`from_pretrained(asr,
  forced_aligner=...)` 路线实证；forced_aligner 关键字失败时自动降级显式组装（R10）。
- Qwen3-ASR-1.7B / Qwen3-ASR-0.6B（历史实测 4.4GB / 1.8GB）；
  Qwen3-ForcedAligner-0.6B（1.8GB/10 文件；历史 ModelScope revision `master`，
  钉版指纹 model.safetensors sha256 前 12 位 `47831d0e82f9`——独立部署时按
  `independence.py` 资产清单接口重新记账）。

## 端口说明

| 端口 | 归属 |
|---|---|
| 18081 | 语音输入法 sidecar 专用端口——本仓**绝不绑定/探测/复用**（绑定即 fail-closed 报错；issue #141） |
| 18085 | 本模块 sidecar 默认端口（`--port` / config `asr.base_url` 可改；指向 18081 即拒绝） |

## 集成入口

仓级初始化使用 `vt.py init`，操作见 [快速开始](../../README.md#快速开始clone-即用零安装步骤)；[配置模板](../../config.example.json)与[编排接口](../../docs/skill/references/edit.md)分别维护配置和项目文件契约。

## 测试

```bash
cd apps/asr && env python3 -m unittest discover -s tests -t . -v   # 零真实模型、零 GPU，仅隔离 fixture/mock 服务
# 或仓根统一入口：bash scripts/run_all_tests.sh（自动发现 apps/*/tests）
```

覆盖：textnorm golden ≥8、segline 边界全套（floor/ceil、行宽 18、行首无标点硬断言、
min_dur≥18、去重叠不破下限+极冲突保留重叠、越界整行丢弃、rate≤9 含标点口径 8.5/9.5
两例）、words[] 构造 golden（数字变形、拉丁跨 -/. 吸收、标点零宽锚、失配插值
degraded_ranges、全失配回退）、srt golden、tl_merge（逐键相等/备份/dry-run/损坏保护）、
client mock sidecar（转写/4xx/超时/>200s 透传不切分、健康归属校验/拒绝复用任意健康服务）、
independence 对抗矩阵（遗留 env/键、18081 URL 矩阵、IME 路径/symlink/硬链接注入、
依赖来源注入、外部 pid 拒发信号、资产清单 SHA256 正反例、复用链成员已删除）、
init 离线回归（许可门禁不变、下载只写专属根、IME 零调用）。
