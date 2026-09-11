# asr —— Qwen3-ASR + ForcedAligner 字幕产线

音频/视频 → sidecar 转写（字级时间戳）→ 文本规范化 → 锚定式断句聚合 → 帧级
`timeline.json subtitles[]` 合并（保护其他字段、写前备份）+ 独立 SRT。模块自有轻量
sidecar（stdlib http.server，默认 **127.0.0.1:18085**，与 IME 输入法 sidecar 18081
完全解耦）。

设计文档：`docs/plans/2026-09-11-三大模块整合方案.md` v2.1 §3（模块设计）、§6.2（init）。

## 文件清单

| 文件 | 用途 |
|---|---|
| `sidecar_server.py` | 转写服务：Qwen3-ASR + ForcedAligner，`/health` `/warm` `/transcribe`；words[] 加工（text↔items 对齐）；OOM 换挡 + 空闲卸载。**运行于 qwen-asr 解释器**（通常由 client 自动拉起） |
| `client.py` | HTTP 客户端（stdlib urllib）+ 音频抽取（ffmpeg 16k mono wav）+ sidecar 进程管理 + config 容错读取 |
| `textnorm.py` | 文本规范化 N1-N7（内化自 ibus-voice-ime text_postprocess 确定性子集 + ctypes libopencc 繁简软依赖） |
| `segline.py` | 字级时间戳 → 断句聚合 → 帧（锚定式；split_line/allocate 内化自 voice-ime make_subs.py，时间改字级锚定） |
| `srt_out.py` | 独立 SRT 1.0 写出（HH:MM:SS,mmm） |
| `tl_merge.py` | timeline.json subtitles[] 合并（只动 subtitles 键 + 备份 + 原子写回） |
| `asr_cli.py` | 总入口：`sidecar` / `transcribe` / `srt` / `timeline` / `batch` |
| `init.sh` | 幂等初始化：解释器/模型探测（复用 IME venv 与模型优先）、缺才下载（ModelScope 国内直连）、冒烟 |
| `tests/` | 单测（零模型零 GPU 零网络）+ fixtures |

## 用法

```bash
bash asr/init.sh                                  # 一次性初始化（幂等）
python3 asr/asr_cli.py sidecar start              # 拉起 sidecar（日志 ~/.local/state/video_tools/asr_sidecar.log）
python3 asr/asr_cli.py transcribe 口播.wav --timestamps --json
python3 asr/asr_cli.py srt 口播.mp4 -o out.srt    # 音视频皆可（自动抽 16k wav）
python3 asr/asr_cli.py timeline 成片.wav -t <项目>/timeline.json --dry-run
python3 asr/asr_cli.py batch <目录|清单.txt> -o <输出目录>
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
  但 aligner 未加载 409（先跑 `bash asr/init.sh`）。
- **words 为 sidecar 加工格式，非 qwen-asr 原生 items**（原生为 token 级、无标点、ms
  制）：words[] = 单字粒度（拉丁连续串拆单字符共享 token 时间窗）、含标点（零宽锚 =
  前实字 {s,e}）、单位秒 3 位小数；`degraded_ranges` 为 text↔items 失配区段（text 字符
  索引 [起,止]，该段字时间已线性插值兜底）；全失配时 `words=[]`（客户端回退整句均分
  并显著告警）。
- **长音频直接透传**（>180s 也一样）：qwen-asr 库内建 ±5s 低能量边界分块与无损拼回，
  sidecar/client 零切分逻辑。
- **18081 是 IME 输入法 sidecar 既有端口，绝不复用**；本服务默认 18085；启动前探测
  端口占用，被占则报错退出（不静默换端口）。**同卡共驻防护**：启动探测 18081 存活
  （=IME 在跑）→ 默认自动降 0.6B 档并打印提示（`--model 1.7b` 显式覆盖自担风险）。

## 文本规范化规则表（textnorm.py，方案 §3.6）

| # | 规则 | 行为 |
|---|---|---|
| N1 | 行首标点删除 | `(^|\n)[，,、。．.!?！？；;：:…]+` 删除（IME 规则增强为全标点集；断言行首绝不出标点，单测硬断言） |
| N2 | 重复标点折叠 | 取最强 `？>！>。>；>：>，`（「！！！」→「！」、「，。」→「。」） |
| N3 | 空格清理 | CJK 标点前后空白删；CJK-CJK 间空白删；**CJK↔拉丁边界空格保留**（方案规格，与 IME 原实现不同） |
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
- sidecar 日志 → `~/.local/state/video_tools/asr_sidecar.log`（不进仓库不进项目）。

## 依赖与开启步骤（默认关闭）

1. `bash asr/init.sh`：探测链复用本机资产——解释器 `config.asr.python` → IME
   `.venv-qwen-asr`（本机 Python 3.14.7 + qwen-asr 0.0.6 + torch 2.12.0）→
   `asr/.venv`（都没有则自建并走清华 PyPI）；模型 `config.asr.model_dir` → IME
   `vendor/models/qwen3-asr` → `~/models/qwen3-asr`（Qwen3-ASR-1.7B / 0.6B /
   **Qwen3-ForcedAligner-0.6B**，缺才下载，ModelScope 国内直连，ForcedAligner 约 2GB）；
   完成后 `--once-health` 冒烟。
2. 把解释器/模型路径写进仓库根 `config.local.json`（init 结束会打印建议片段，不自动改）：
   `"asr": { "enabled": true, "python": "<venv>/bin/python", "model_dir": "~/models/qwen3-asr" }`
   （`base_url` 默认 `http://127.0.0.1:18085`，无需写）。
3. 日常使用无需手动起 sidecar：`srt`/`timeline`/`batch` 会自动拉起并等健康。

GPU：4080 16GB 下 1.7B+aligner 同驻约 6-8GB；IME sidecar 同跑时本 sidecar 自动降
0.6B；空闲 5 分钟自动卸载到 CPU 释放显存；CPU 推理不支持（qwen-asr 结论同 IME）。

## 已验证版本

- qwen-asr **0.0.6**（Python 3.14.7，IME `.venv-qwen-asr`）——`from_pretrained(asr,
  forced_aligner=...)` 路线实证；forced_aligner 关键字失败时自动降级显式组装（R10）。
  注意：IME venv 历史上整体移动过（`~/文档/type/ibus-ime` → 现路径），其 python 解析
  不到自身 site-packages——本模块 init.sh/client/sidecar 三处均已内建 PYTHONPATH
  自愈，无需手工修复 venv。
- Qwen3-ASR-1.7B / Qwen3-ASR-0.6B：IME `vendor/models/qwen3-asr/`（4.4GB / 1.8GB）。
- Qwen3-ForcedAligner-0.6B：`~/models/qwen3-asr/Qwen3-ForcedAligner-0.6B`（2026-09-11
  init 下载，1.8GB/10 文件；ModelScope revision 分支 `master`——API 未暴露 commit
  sha，钉版指纹 model.safetensors sha256 前 12 位 `47831d0e82f9`）。

## 端口说明

| 端口 | 归属 |
|---|---|
| 18081 | IME 输入法 sidecar（`ibus-voice-ime`，本仓**绝不复用**；仅启动时探测它做共驻降档） |
| 18085 | 本模块 sidecar 默认端口（`--port` / config `asr.base_url` 可改） |

## 待集成事项

集成事项已于 2026-09-11 全部落地（根 README/init.py/config.example.json/edit.md 均已更新）。

## 测试

```bash
python3 -m unittest discover -s asr/tests -v   # 59 个用例，零模型零 GPU 零网络
```

覆盖：textnorm golden ≥8、segline 边界全套（floor/ceil、行宽 18、行首无标点硬断言、
min_dur≥18、去重叠不破下限+极冲突保留重叠、越界整行丢弃、rate≤9 含标点口径 8.5/9.5
两例）、words[] 构造 golden（数字变形、拉丁跨 -/. 吸收、标点零宽锚、失配插值
degraded_ranges、全失配回退）、srt golden、tl_merge（逐键相等/备份/dry-run/损坏保护）、
client mock sidecar（转写/4xx/超时/>200s 透传不切分）。
