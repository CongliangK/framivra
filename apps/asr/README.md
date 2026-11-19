# asr —— Qwen3-ASR + ForcedAligner 字幕产线

音频/视频 → 独立 sidecar 转写 → 文本规范化、字级时间戳聚合 → 独立 SRT 或帧级
`timeline.json subtitles[]`。组件声明见 `app.json`；默认关闭，不借用语音输入法（IME）的环境、模型或服务。

模块协议与准备边界以本页为准；[原始设计记录](https://github.com/CongliangK/framivra/blob/c476b4f5f13687c61c9be02f4015e289a176ddc8/docs/plans/2026-09-11-三大模块整合方案.md)仅供溯源。

## 文件清单

| 文件 | 用途 |
|---|---|
| `sidecar_server.py` | Qwen3-ASR + ForcedAligner 服务，延迟加载模型，生成 `words[]` |
| `client.py` | 解释器/模型发现、sidecar 生命周期、ffmpeg 音频抽取、HTTP 客户端 |
| `independence.py` | 独立性、归属、启用与显存准入的权威实现；提供资产清单（版本/来源/SHA256）校验接口 |
| `textnorm.py` | 文本规范化 N1-N7（内化自 ibus-voice-ime text_postprocess 确定性子集 + ctypes libopencc 繁简软依赖） |
| `segline.py` | 字级时间戳 → 断句聚合 → 帧（锚定式；split_line/allocate 内化自 voice-ime make_subs.py，时间改字级锚定） |
| `srt_out.py` | 独立 SRT 1.0 写出（HH:MM:SS,mmm） |
| `tl_merge.py` | subtitles[] 适配器（只声明字幕范围 + 字幕备份；经公开 timeline publisher 提交） |
| `asr_cli.py` | 总入口：`sidecar` / `transcribe` / `srt` / `timeline` / `batch` |
| `init.sh` | 已有独立资产检查与本地 sidecar 冒烟，见「开启步骤」 |
| `tests/` | 隔离测试与 fixtures |

## 用法

从完整源码仓根运行；内核 wheel 不包含此 app，30 秒 SVG 发布基线也不验收 ASR。见[发行资格](../../docs/releases.md#冻结验收与发布)。
先按[开启步骤](#开启步骤默认关闭)审核并准备独立资产、显式启用。客户端使用 Linux `/proc` 证明端点归属；缺少该证据即拒绝，不把 macOS/Windows 的路径发现或 init 冒烟当作原生转写资格。

**运行提示**：以下命令会抽取临时 WAV、发本地请求，必要时自动启动 sidecar。`srt`/`batch` 会写输出和报告；`timeline --dry-run` 仍执行转写，只是不写 timeline、备份、history 或字幕审计报告。

```bash
env python3 apps/asr/asr_cli.py transcribe "/path/to/口播.wav" --timestamps --json
env python3 apps/asr/asr_cli.py srt "/path/to/口播.mp4" -o "/path/to/项目/out.srt"
env python3 apps/asr/asr_cli.py timeline "/path/to/成片.wav" -t "/path/to/项目/timeline.json" --dry-run
env python3 apps/asr/asr_cli.py batch "/path/to/清单.txt" -o "/path/to/输出目录"
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
fragment 均拒绝。客户端不走环境代理、不跟随重定向；每次 HTTP 前离线核对项目
PID 记录、启动时刻、脚本/端口及同进程的 LISTEN socket inode，不能靠 `/health` 反证归属。
手动常驻启动须用专属解释器 `-I`，但仅启动不建立客户端所需的归属记录；需单独启动时用
`asr_cli.py sidecar start`。元数据前检不消除检查到连接之间的竞态，遵循仓库
[不新增本地 TOCTOU 加固的边界](../../CONTRIBUTING.md#信号实验隔离)，不宣称竞态证明。

| 端点 | 方法 | 请求 | 响应 |
|---|---|---|---|
| `/health` | GET | — | `{"status":"ok","model":"…","aligner":"…或null","port":18085,"version":1}`（不加载模型） |
| `/warm` | POST | `{}` | `{"ok":true}`（尝试加载或恢复到配置设备，不做转写） |
| `/transcribe` | POST | `{"audio":"<本地wav路径>","language":"Chinese","timestamps":true}` | `{"text":"…","words":[{"w":"字","s":0.833,"e":0.913},…],"degraded_ranges":[[起,止],…],"duration_s":12.34}`；不请求时间戳时仅返回 `text` |

- 错误为 4xx/5xx + `{"error":"中文可行动信息"}`；缺 `audio` 或文件不存在返回 400。
  `timestamps:true` 但服务没有 aligner 路径时返回 409；仅转写文本可以不配置 aligner。
  非空 `words` 的时间窗非有限、倒退、为负、超出实际时长（容许 1ms 舍入误差），或时长无法核实，也返回 409，不排序、夹取或均分修复。
  `srt`/`timeline` 遇此错误退出 1，不写字幕或字幕审计；`batch` 记录该条失败、继续处理其余条目，最终写汇总并退出 1。
- **words 为 sidecar 加工格式，非 qwen-asr 原生 items**（0.0.6 原生为 token 级、无标点、秒
  制）：words[] = 单字粒度（拉丁连续串拆单字符共享 token 时间窗）、含标点（零宽锚 =
  前实字 {s,e}）、单位秒 3 位小数；`degraded_ranges` 为 text↔items 失配区段（text 字符
  索引 [起,止]，该段字时间已线性插值兜底）。全失配时 `words=[]`；字幕子命令有文本时
  告警并回退整句均分，不再具备字级锚定。此降级与无效非空时间窗返回 409 不同。
- **长音频直接透传**：qwen-asr 0.0.6 按有时间戳 180s / 纯文本 1200s 的目标长度，
  在 ±5s 低能量边界分块并拼回；这不是总输入时长上限，也不保证识别文字完整。
  sidecar/client 零切分逻辑，每块默认最多生成 1024 tokens；单调时间戳也不能证明无漏转。
- **端口防御（#141）**：18081 专属语音输入法，禁止绑定、探测或复用。默认 18085；
  自有端口被占则报错，不静默换端口。
- **enabled 门禁（#142）**：只有布尔 `true` 或字符串 `true/1/yes/on` 启用；
  未配置或无效值按禁用处理。下表为禁用态行为，诊断和准备例外仍受独立性校验：

  | 入口 | 禁用态行为 | 豁免（语义明确，非绕过） |
  |---|---|---|
  | `asr_cli transcribe/srt/timeline/batch` | 拒绝（exit 2），零 HTTP | — |
  | `asr_cli sidecar start` / 库 `client.ensure_sidecar` | 拒绝，零探测零拉起 | — |
  | 库 `client.transcribe` / `client.warm` | 拒绝，零 POST | — |
  | 直接运行 `sidecar_server.py`（长驻） | 拒绝绑定（18081/IME 路径硬边界先行诊断） | `--once-health`=init 准备冒烟（零模型加载零 GPU） |
  | `asr_cli sidecar health/status` | **允许**（只读探测自有 base_url，零 POST） | 本行即豁免语义 |
  | `asr_cli sidecar stop` | **允许**（回收自有残留服务，安全收敛动作） | 本行即豁免语义 |
  | `vt.py doctor`（asr 段） | 不探测 sidecar（零 HTTP），报告禁用态 | — |

- **停止与复用不是同一检查**：有有效 PID 记录且 `/proc` 证实脚本、端口和启动时刻后，
  `stop` 才能向自有进程发信号，即使 HTTP 已不可用；确认原进程退出才报成功。
  身份不符不发信号并保留记录；不可解析的 PID 记录会清除且不发信号。复用另须通过上文的监听归属和健康检查。
- **通用资源准入（issue #142，与 IME 端点零依赖）**：模型加载（client 拉起前预检
  + sidecar `_load_locked` 准入）前经 `gpu_admission` 只读 `nvidia-smi` 比对显存
  预算（`asr.vram_budget_mib` 配置优先并传入服务，缺省粗估：1.7B≈6144 / 0.6B≈3072 MiB）：
  使用驱动报告的 `memory.free`，不把 `total-used` 中的驱动保留量算作可用显存。
  预算不是硬上限或显存预留，长音频应按实测配置。不足，或 GPU 路线无法使用 `nvidia-smi`，
  均拒绝；`cpu` 仅跳过显存门。`admission_lease` 串行「查显存→加载」；加载 OOM 的同根
  companion 降档是次级保护。转写中 OOM 尝试迁回自有模型至 CPU、清自有缓存并返回 503；
  不停止、卸载或重配外部服务。默认空闲 5 分钟卸载到 CPU 也不等于用 CPU 推理。

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
行时间 = 首字 s … 末字 e；`in=floor(s*fps)`、`out=ceil(e*fps)`；默认最短 18 帧（30fps 时为 0.6s）；相邻去重叠不破 `in+min_dur` 下限（极冲突保留重叠并告警）；默认速率目标 ≤9 字/s（含标点，有隙扩隙、无隙告警）；越界整行丢弃（保序）。

## 产物落位

- 临时 16k wav → `<项目>/cache/tmp/<runid>/`（退出即删；输入不在项目内退 /tmp 小件）；
- `srt` 写 `-o` 指定的文件，审计摘要在同目录（摘要写失败不撤回 SRT）；`batch` 在 `-o` 指定目录写各条 SRT 和汇总。
- `timeline` 改写指定的时间线；字幕数组备份和审计报告在该时间线旁的 `cache/reports/`。
- sidecar 日志/PID → `~/.local/state/framivra/asr/`（项目状态根，不进仓库不进项目，
  与输入法的任何状态目录完全独立）。

## 依赖（默认关闭）

- **app 级（manifest requires，doctor 可探测）**：ffmpeg / ffprobe（16k wav 抽取与时长探测）；系统 python3 ≥3.9 stdlib（CLI 全部子命令零第三方依赖）。
- **sidecar 级（不随 app 或发行包安装）**：专属解释器先查 `asr.python`，再查
  `apps/asr/.venv/bin/python`；`qwen_asr` 和 `torch` 必须来自所选解释器自己的环境。
  ASR 在 `asr.model_dir`、默认专属根 `~/.local/share/framivra/asr/models` 中查找模型子目录，
  优先 1.7B，找不到才选 0.6B。运行时优先使用存在的 `asr.aligner_dir`，否则在这两个根找 `Qwen3-ForcedAligner-0.6B`；init 对显式路径不回退。
  发现不等于权重已加载、资产清单 SHA256 已验证或用途已获准；不允许通过链接或共享缓存借用 IME 资产。
- **旧配置迁移**：`VT_ASR_IME_ROOT` / `asr.ime_root` 出现即报迁移错误（fail-closed，
  不静默换算）；`asr.python` / `asr.model_dir` / `asr.aligner_dir` / `asr.base_url`
  显式配置同样过独立性校验，指向输入法资产/端口即拒绝。

## 健康检查

`env python3 apps/asr/asr_cli.py --help` 是 manifest 的命令健康检查，不启动服务、不加载模型。
`sidecar status` 则会向已证明归属的服务发本地 `/health` 请求；健康只表明服务应答，不表明推理成功。

## 开启步骤（默认关闭）

1. 先按[第三方许可与模型下载边界](../../docs/third-party-licenses.md#下载与运行政策)审核并独立准备解释器、依赖和模型。
   [`plan` 检查](../../docs/third-party-licenses.md#零写态检查)只读取证据，不下载、不安装、不授予用途许可。
   旧 `bash apps/asr/init.sh --download` 在探测和联网前 **BLOCKED（退出码 4）**；固定快照下载不安装 `qwen-asr`/`torch`，也不解除旧门禁。
2. 非默认资产路径先写入生效配置（`VTX_CONFIG` 优先，否则仓库根 `config.local.json`），保持禁用。
   `bash apps/asr/init.sh` 检查既有专属解释器、一档 ASR 权重与 ForcedAligner，成功后会临时启动本地服务并请求 `/health`。
   它不创建环境、不安装、不下载、不改配置，也不加载模型；通过不等于转写已验收。
3. 就绪后才在同一生效配置设 `"asr": {"enabled": true}`（保留已有 ASR 配置键）。
   `asr.device_map` 默认 `cuda:0`，可设 `"cpu"`；`asr.vram_budget_mib` 为正数 MiB，见上文准入规则。

下列独立 CPU 短样本与受控 GPU 样本各有验收范围。长音频漏转仍未解决，单调时间戳不能证明完整；借用 IME 环境的历史记录不适用于现行部署。

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

## 集成入口

仓级初始化使用 `vt.py init`，操作见 [快速开始](../../README.md#快速开始clone-即用零安装步骤)；[配置模板](../../config.example.json)与[编排接口](../../docs/skill/references/edit.md)分别维护配置和项目文件契约。

## 测试

局部回归使用隔离夹具，会写临时数据并启动本地测试服务，不加载真实模型或使用 GPU。全仓入口及本机限制见[贡献指南](../../CONTRIBUTING.md#测试入口)。

```bash
cd apps/asr && env python3 -m unittest discover -s tests -t . -v
```

关键反例见 `tests/test_endpoint_ownership.py`（PID 匹配但 listener 不归属，零 HTTP）、
`tests/test_alignment_validity.py`（倒退时间窗 409，不写 SRT/审计）、
`tests/test_init_independent_assets.py`（解释器与资产独立性）；独立的 `tests/test_init_offline.sh` 保留旧下载拒绝断言。
