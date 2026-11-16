# SVG 完整冷暖链观测

这是唯一 portable-v2 30 秒 / 900 帧 / 1080p30 SVG 的单臂观测入口，复用 [browser benchmark 的资源与完整解码工具](../../scripts/gfx/_bench_runtime.py)，不创建另一种输入、渲染器或质量资格。结论归[开发记录](../../docs/reports/development-history.md#一性能基准)，不能把单臂运行当作配对提速证明。

## 执行

需要完整源码、Node、含 jsonschema 的 Python、FFmpeg/ffprobe、受支持的 Inkscape/Fontconfig 和[四份锁定公开字体](../../templates/demo-timeline-svg/README.md#实际依赖与字体)。先冻结两份源码/工具/机器身份；同机所有媒体与 GUI 诊断必须串行。以下是**写操作**：在新的仓外目录准备完整输入，生成 cache/out 和两份成片快照，不清理旧目录或 OS 缓存。

```bash
python3 experiments/perf_svg_workflow/run.py --help
# 将以下路径换成已核验的工具、不可变源码与新的物理输出目录。
python3 experiments/perf_svg_workflow/run.py \
  --repo /path/to/frozen-source --output /path/to/new-external-trial \
  --python /path/to/venv/bin/python3 --ffmpeg /path/to/ffmpeg \
  --inkscape /path/to/native-inkscape --fonts-dir /path/to/locked-fonts
```

每臂依次运行官方 `prepare`、`verify`、cold `all`、独立 strict QC、完整解码/PTS 检查，再在同一项目 warm 重跑。`all` 外层墙钟不含额外 strict/解码/输入制作；prepare+all 只将输入制作与 cold all 相加，首次字体联网获取另计。冷仅指全新项目 cache/out；warm 前会进行额外验证，不声称 OS 冷或固定热缓存驻留。

不继承 `VTX_*` 调参，使用任务级空配置和实际正式默认（pipeline、分段、编码质量、缓存均不改）。工具 PATH 是显式工具目录加调用者 PATH，须冻结其解析结果。调度实验不能偷换成单段/单 worker 分母。两臂分别在全新输出运行，至少按 AB、BA、AB 顺序做三组，并在聚合时重新核对全部输入哈希、900 PNG 字节序列、完整解码视频/音频/采样时钟相等及事件完整性；逐对 `new/base` 的中位数才是目标指标。保留失败和受扰轮次，不能挑最快轮。

`--helper-directory` 只用于显式选择同一份已有 `_bench_runtime.py`，会导入并执行该目录的受信任 Python 代码；两臂必须固定同一字节版本，摘要进入结果。默认使用被测仓库的工具。若需要尚未合入的共享测量补丁，应在报告中标明来源和哈希，不把不同 helper 的资源口径混算。

## 证据与边界

`summary.json` 留逐命令 argv 日志、退出与资源观察、运行叶源码/工具/输入哈希、cold/warm 质量结果、PNG 清单与逻辑字节；同目录保存完整事件、回执、QC、逐帧 PTS、解码摘要及各状态的成片快照。默认 core 会清理中间 WAV，因此额外采样核验针对真实成片完整解码的 PCM（本固定工具链要求精确 1,440,000 采样），不伪称保留了混音 WAV；产品 post-loudness 回执单列。成片快照先于 strict，PNG/凭据快照在各状态 strict 后，不能把后者说成独立的 pre-strict 截图。资源采样保留 helper 的平台/频率/漏峰值/共享页重复计数等限制；逻辑文件字节不是物理独占空间，进程 IO 不是设备流量或等待时间。

原始包含本机路径，不直接上传 GitHub；公开只导出脱敏逐轮数值、哈希索引和限制，媒体、字体、凭证与原始主机日志不上传。PNG 哈希相等与产品 QC 通过不等于艺术/听感批准；人工全片视听须另列。输入或工具身份变动须重做两臂，不能拼接旧新成绩。

本次 #345 的三对完整控制器布局可用 `summarize.py` 离线投影；它只读取已停止写入的 JSON/原始事件，不重新渲染、不打开媒体，沿用内核严格事件解析器。以下重定向是**写操作**，目标须为新的仓外文件：

```bash
python3 experiments/perf_svg_workflow/summarize.py --help
python3 experiments/perf_svg_workflow/summarize.py \
  --evidence-root /path/to/completed-evidence --formal-directory formal-v2 \
  --failed-formal-directory formal > /path/to/new-public-summary.json
```

投影记录逐对比值、阶段区间并集、资源限制及相对文件哈希索引；受扰探索、串行消融与失败首试单列，不进入正式分母。退出0只代表这些机器证据符合本轮配对资格，不等于 PR 已完成复审或人工视听批准。源/输入/工具不稳定、原始事件不完整、质量不符或观察到外部媒体进程时拒绝资格。
