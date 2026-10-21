# asr —— Qwen3-ASR + ForcedAligner 字幕产线

> **v2 结构（P2a 批次 2 起本目录迁入 `apps/asr/`）**：manifest 见 `app.json`（kind=app；健康检查 = `asr_cli.py --help`）。
> **IME 解耦（issue #141，2026-10-17）**：ASR 与语音输入法彻底解耦——旧键
> `VT_ASR_IME_ROOT` / `asr.ime_root` 已废除（出现即迁移错误）；解释器/模型探测链
> 不再含 IME 仓；共驻降档（探测 18081）与「任意健康服务即可复用」均已删除。
> **安全准入（issue #142，2026-10-18）**：`asr.enabled` 门禁全入口实际执行；
> sidecar 进程归属三因子（cmdline+端口+启动时刻）；通用 GPU/显存准入
> （`nvidia-smi` 只读预算，不依赖 IME 端点）。单一接口见 `independence.py`。

音频/视频 → sidecar 转写（字级时间戳）→ 文本规范化 → 锚定式断句聚合 → 帧级
`timeline.json subtitles[]` 合并（保护其他字段、写前备份）+ 独立 SRT。模块自有轻量
sidecar（stdlib http.server，默认 **127.0.0.1:18085**；18081 为语音输入法 sidecar
专用端口，本仓绝不绑定/探测/复用——issue #140/#141 铁律）。

模块协议与准备边界以本页为准；[原始设计记录](https://github.com/CongliangK/framivra/blob/c476b4f5f13687c61c9be02f4015e289a176ddc8/docs/plans/2026-09-11-三大模块整合方案.md)仅供溯源。

## 文件清单

| 文件 | 用途 |
|---|---|
| `sidecar_server.py` | 转写服务：Qwen3-ASR + ForcedAligner，`/health` `/warm` `/transcribe`；words[] 加工（text↔items 对齐）；OOM 换挡 + 空闲卸载。**运行于 qwen-asr 解释器**（通常由 client 自动拉起） |
| `client.py` | HTTP 客户端（stdlib urllib）+ 音频抽取（ffmpeg 16k mono wav）+ sidecar 进程管理（pid 三因子归属）+ config 容错读取；`ensure_sidecar/transcribe/warm` 受 enabled 门禁与拉起前显存预检约束（issue #142） |
| `independence.py` | **独立性与安全准入单一接口（issue #141/#142）**：IME 锚定路径拒绝、遗留键迁移错误、base_url 18081 拒绝、解释器依赖来源验证、模型链接关系校验（`verify_dir_assets`）、PID 三因子归属、`asr.enabled` 严格门禁（`asr_enabled/require_enabled`）、通用 GPU/显存准入（`gpu_admission`+并发 `admission_lease`）、资产清单（版本/来源/SHA256）接口 |
| `textnorm.py` | 文本规范化 N1-N7（内化自 ibus-voice-ime text_postprocess 确定性子集 + ctypes libopencc 繁简软依赖） |
| `segline.py` | 字级时间戳 → 断句聚合 → 帧（锚定式；split_line/allocate 内化自 voice-ime make_subs.py，时间改字级锚定） |
| `srt_out.py` | 独立 SRT 1.0 写出（HH:MM:SS,mmm） |
| `tl_merge.py` | timeline.json subtitles[] 合并（只动 subtitles 键 + 备份 + 原子写回） |
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
`<项目>/cache/reports/subtitles_backup_<UTC>.json`），打印摘要（窗数/字数/最快速率/
最宽行）；随后用 `vtx validate && audit` 复核（阅读速度 ≤9 字/s 含标点口径、行宽等）。

## sidecar 协议（默认 127.0.0.1:18085，可配 config `asr.base_url`）

| 端点 | 方法 | 请求 | 响应 |
|---|---|---|---|
| `/health` | GET | — | `{"status":"ok","model":"…","aligner":"…或null","port":18085,"version":1}` |
| `/warm` | POST | `{}` | `{"ok":true}`（模型预上 GPU） |
| `/transcribe` | POST | `{"audio":"<本地wav路径>","language":"Chinese","timestamps":true}` | `{"text":"…","words":[{"w":"字","s":0.833,"e":0.913},…],"degraded_ranges":[[起,止],…],"duration_s":12.34}` |

- 错误统一 `{"error":"中文可行动信息"}` + 4xx/5xx；音频不存在 400；`timestamps:true`
  但 aligner 未加载 409（须独立准备并配置 aligner，见「开启步骤」；init 不会下载）。
- **words 为 sidecar 加工格式，非 qwen-asr 原生 items**（原生为 token 级、无标点、ms
  制）：words[] = 单字粒度（拉丁连续串拆单字符共享 token 时间窗）、含标点（零宽锚 =
  前实字 {s,e}）、单位秒 3 位小数；`degraded_ranges` 为 text↔items 失配区段（text 字符
  索引 [起,止]，该段字时间已线性插值兜底）；全失配时 `words=[]`（客户端回退整句均分
  并显著告警）。
- **长音频直接透传**（>180s 也一样）：qwen-asr 库内建 ±5s 低能量边界分块与无损拼回，
  sidecar/client 零切分逻辑。
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
  预算（`asr.vram_budget_mib` 配置优先，缺省按档估计：1.7B≈6144 / 0.6B≈3072 MiB）：
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
   `~/.local/share/framivra/asr/models`（Qwen3-ASR-1.7B / 0.6B /
   **Qwen3-ForcedAligner-0.6B**），并执行本地 `--once-health` 冒烟。缺失项失败汇总，
   不自动补齐；冒烟不加载真实模型、不验证转写质量。
3. 确认独立环境与资产就绪后，才把解释器/模型路径和显式启用项写进仓库根
   `config.local.json`（写配置是独立操作；init 仅打印建议片段，不自动改）：
   `"asr": { "enabled": true, "python": "<apps/asr/.venv>/bin/python", "model_dir": "~/.local/share/framivra/asr/models" }`
   （`base_url` 默认 `http://127.0.0.1:18085`，无需写）。
4. 日常使用无需手动起 sidecar：`srt`/`timeline`/`batch` 会自动拉起并等健康。

空闲 5 分钟自动卸载到 CPU 释放显存；这与 CPU 实际推理验收不同。当前独立环境的
CPU/GPU 真实转写均未验收，不据参数准入宣称硬件支持或实测显存占用。与输入法的
GPU 竞争由 #142 通用资源准入处理，绝不停/降输入法。

## 已验证版本

- **独立安装与真实转写 UNRUN（issue #140/#141）**：专属环境 `apps/asr/.venv` 与专属模型根
  `~/.local/share/framivra/asr/models` 的真实安装+转写正例尚未执行（旧 init 下载入口受 #18
  许可门禁 BLOCKED；另行批准的固定快照下载也不能替代运行环境安装与实际转写验收）。以下为历史实测版本记录
  （当时借用 IME 环境——该链路已废除，仅作 qwen-asr 库行为溯源）：
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

## 待集成事项

集成事项已于 2026-09-11 全部落地（根 README/init.py/config.example.json/edit.md 均已更新）。

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
