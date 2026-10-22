# demo-fixture —— standard-demo 输入与公开运行入口（#177 / #91）

> 事实 home：本专题是 demo 工程输入包（templates/standard-demo/）与唯一入口
> scripts/demo.py 的说明 home。包内构成与 profile 对照见
> [输入包说明](../../templates/standard-demo/README.md)。demo.py 只做输入准备与核验，
> 其 realization.json 中 renderer/QC/CEF 的 UNRUN 不代表后续生产命令的结果。

新纯 MG 作品先依 [HTML 创作组织](../skill/references/gfx.md#html-创作组织) 设计；本页的冻结多页 fixture 按原覆盖用途保留，不把新作品改写成较少页面后的耗时与本 fixture 当作同输入性能对照。

## 三步入口

```bash
env python3 scripts/demo.py preflight --profile quick    # 只读：版本化 JSON 体检
env python3 scripts/demo.py preflight --profile standard
env python3 scripts/demo.py preflight --profile gfx-stress
```

preflight 区分 prepare 依赖（python/ffmpeg/ffprobe/随包字体）与 render 依赖
（CDP/CEF）：缺 CEF 不会把 prepare 判成同一成败状态；输入准备入口
不安装不运行 renderer，`cef` 字段恒为 null+reason。gfx-stress 为引用型 profile
（prepare_supported=false）。

preflight 退出码语义（issue #177）：preflight 是**只读诊断**——只要检查本身
可执行并完整输出版本化 JSON 即 rc 0；依赖缺失（如无 ffmpeg/ffprobe/字体哈希
不匹配）反映在 `checks[].ok` 与 `prepare_ready` 字段，不用退出码表达。这样
「诊断跑成」与「依赖就绪」是两个状态，不会因缺任一依赖（含 CEF）折叠成同一
成败退出码；依赖就绪与否的硬失败闭口在 `prepare`（缺失/不符时非零退出并保留
诊断证据）。

prepare（写态命令：只在可信父目录下创建**全新**工程目录，拒绝已有目录/
symlink 组件/仓内/HOME/缓存内输出，拒绝未冻结工具链与 generator 漂移）：

```bash
# 写态示范：在 /tmp 下新建目录，生成媒体+复制冻结输入+生产 validate
env python3 scripts/demo.py prepare --profile quick --output /tmp/demo-quick
```

verify（只读，以仓内冻结清单为外部锚，绝不信待验目录自证）：

```bash
env python3 scripts/demo.py verify --project /tmp/demo-quick
```

未知版本/生成器或工具链身份不符/重复 JSON 键/非有限数/绝对路径或 `..`/
symlink/缺文件/大小或哈希不符 → 非零退出。`--pixels` 附加解码原始像素
rgb24 全量指纹核验（F2 级证据，默认 verify 已含帧数/尺寸/帧率/PTS/PCM）。

## 公开 standard Demo 生产运行

从根 README 的 clone 步骤进入仓库后，在同一个 Bash 会话执行以下步骤。
这是现有生产 CLI 的操作配方，不是本提交已跑通的证明。当前源码的 CDP/CEF
公开成片及人工视听仍须实际执行；历史 CDP 验收与稳定资格见
[源码稳定基线](../releases.md)，不能移植为当前版本或 CEF 的 PASS。

前置条件：Python ≥3.9；PATH 中的 ffmpeg/ffprobe 必须与下节冻结工具链的
版本和二进制哈希同时匹配，单有同版本或通过 vt.py init 不够；随包字体完整。
当前配方限 Linux，须具备 `/proc` 进程监管、ldd、fontconfig 的 fc-list/
fc-conflist 及可读取的字体/运行库。CDP 使用已安装的本地 Chrome/Chromium
ELF 运行时，先将 CHROME 设为其绝对可执行路径。CEF 另需 Linux x86_64
锁定 worker 与同目录运行库/资源；构建要求、CPU 要求和安全边界以
[CEF worker 说明](../../experiments/gfx_cef_worker/README.md) 为准。
worker 禁用 Chromium sandbox，仅用于受信任本地 HTML；缺失环境就停止，
不修改权限、安全设置或隐式改用其他后端。

以下是**实际写操作**：只在 /tmp 下新建隔离运行目录；保留该目录供取回结果，
不要用原项目替换路径。preflight 的 rc=0 仅表示诊断完成，须先确认
prepare_ready=true；prepare/verify 失败不得继续，也不得自动重冻结来绕过失败。

```bash
set -eu
RUN=$(mktemp -d /tmp/framivra-standard.XXXXXX)
export RUN
git rev-parse HEAD > "$RUN/source.commit"
git status --porcelain > "$RUN/source.status"
printf '{}\n' > "$RUN/config.json"
env -i PATH="$PATH" HOME="$HOME" LC_ALL=C.UTF-8 PYTHONUTF8=1 VTX_CONFIG="$RUN/config.json" python3 scripts/demo.py preflight --profile standard > "$RUN/preflight.json"
env python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1]))["prepare_ready"] is True else 1)' "$RUN/preflight.json"
env -i PATH="$PATH" HOME="$HOME" LC_ALL=C.UTF-8 PYTHONUTF8=1 VTX_CONFIG="$RUN/config.json" python3 scripts/demo.py prepare --profile standard --output "$RUN/input" > "$RUN/prepare.log" 2>&1
env -i PATH="$PATH" HOME="$HOME" LC_ALL=C.UTF-8 PYTHONUTF8=1 VTX_CONFIG="$RUN/config.json" python3 scripts/demo.py verify --project "$RUN/input" --pixels > "$RUN/verify.log" 2>&1
# 两份都复制同一输入；排除 prepare 的 validate 回执及任何 cache/out。
env python3 -c 'import os,shutil; from pathlib import Path; r=Path(os.environ["RUN"]); [shutil.copytree(r/"input", r/b, ignore=shutil.ignore_patterns("cache", "out")) for b in ("cdp", "cef")]'
mkdir "$RUN/cdp-logs" "$RUN/cef-logs"
```

下面的小函数仅保存现有 CLI 的日志、退出码和 Bash 命令墙钟，不生成验收结论。
清空继承的环境并固定 CPU/并发配置，避免隐藏 VTX 参数或用户插件配置；PATH
仍须指向上述合格工具。首组采用单页、CDP 单 tab、禁用页内分片；CEF 页内请求
本来就是串行。这是请求的并发上限，实际 worker/重叠情况另从事件与原生日志记录，
不能只凭环境变量宣称两后端实际工作量相同。all 自带普通 QC，qc --strict
是另一次调用、单独计时。
事件文件必须全新，不能重复用同一 step 名；失败时日志与退出码保留，停止后续步骤。

```bash
demo_stage() {
  local backend=$1 step=$2 status
  shift 2
  local logs="$RUN/$backend-logs"
  if [ -e "$logs/$step.log" ] || [ -e "$logs/$step.events.jsonl" ]; then
    printf '保留旧证据；请使用新的 step 名：%s\n' "$step" >&2
    return 1
  fi
  TIMEFORMAT='wall_seconds=%R user_seconds=%U sys_seconds=%S'
  if { time env -i PATH="$PATH" HOME="$HOME" LC_ALL=C.UTF-8 PYTHONUTF8=1 \
      VTX_CONFIG="$RUN/config.json" VTX_NO_NVENC=1 VTX_SEG_NVENC=0 \
      VTX_PIPELINE=0 VTX_VIDEO_SEGMENTS=2 VTX_FILTER_THREADS=1 VTX_IO_WORKERS=1 \
      VTX_GFX_PAGES=1 VTX_GFX_TABS=1 VTX_GFX_SPLIT_FRAMES=0 VTX_GFX_SPLIT_MAX=1 \
      VTX_GFX_CACHE_NS= VTX_GFX="$backend" VTX_CHROME="${CHROME:-}" \
      VTX_CEF_WORKER="${CEF_WORKER:-}" VTX_CEF_MODE=a0 \
      python3 vtx/vtx.py -t "$RUN/$backend/timeline.json" \
      --events-jsonl "$logs/$step.events.jsonl" "$@" > "$logs/$step.log" 2>&1;
  } 2> "$logs/$step.time.txt"; then status=0; else status=$?; fi
  printf '%s\n' "$status" > "$logs/$step.exit.txt"
  return "$status"
}
: "${CHROME:?先设置已安装浏览器的绝对路径}"
demo_stage cdp all all
cp "$RUN/cdp/out/qc_report.json" "$RUN/cdp-logs/all.qc_report.json"
demo_stage cdp qc-strict qc --strict
# 请求 CDP 不等于实际 CDP；已有校验器拒绝 CLI 回落与缺少冷建页观察。
env python3 -c 'from scripts.release.release_github import observed_cdp; import sys; print(observed_cdp(sys.argv[1]))' "$RUN/cdp-logs/all.log"
```

CEF 显式选择 A0/Mode N/native-exact，沿用同一 standard 输入，不改成静态子集。
CEF_WORKER 应是已构建且运行库齐备的绝对路径；未设置时下面停止，路径无效或
原生执行失败时生产 CLI 非零退出，不回落 CDP/CLI。

```bash
: "${CEF_WORKER:?先设置已构建 CEF worker 的绝对路径}"
demo_stage cef all all
cp "$RUN/cef/out/qc_report.json" "$RUN/cef-logs/all.qc_report.json"
demo_stage cef qc-strict qc --strict
```

`qc --strict` 对全片解码计帧并禁用命中段免检，CEF 也使用；它仍只执行已有的
有限 QC 检查，不是完整画面内容或视听质量证明。它不是 timeline 的
`project.gfx_dependency_policy="strict"`。后者仅支持限定的 CDP 依赖检查，
CEF 会明确拒绝。standard 保持原有 legacy 策略，不通过改页面或换后端冒充通过。
默认 CDP 缓存在 cache/gfx/；CEF 缓存在 cache/gfx_exp/cef-alpha/，原生日志和
逐帧回执在 cache/reports/cef/。两份项目必须独立，不能拿已有 CDP 成片充当 CEF。

取回 `$RUN/cdp/` 与 `$RUN/cef/` 的可编辑项目及各自 out/final.mp4、
out/qc_report.json、out/receipt.json，同时保留 input/realization.json、
source.commit/source.status、config.json、preflight/prepare/verify 日志和两个 *-logs 目录。
按实际日志/回执记录后端、退出码、QC、帧数与时长；standard 的目标为 360 帧、
30 fps、12 秒，不把目标值当测量值。实际查看/播放字幕、剪辑点、动效、透明图层
并听音频后另记判断；命令退出 0、QC 和输入核验都不能代替人工视听。

### 修改、预览、恢复与性能基线

只编辑上述项目副本的 gfx/canvas.html 样式或 timeline.json 字幕，保留原始 input。
例如对 CEF 副本修改 canvas 样式后，仍用原 backend 运行；这些 CEF 步骤需要真实
运行验证，文档与合同测试不提供 CEF 编辑/恢复已通过的证据：

```bash
demo_stage cef edit-gfx gfx
demo_stage cef edit-preview video --draft --range 30f 90f
demo_stage cef edit-all all
demo_stage cef edit-qc qc --strict
```

区间预览位于 cache/preview/（具体文件名见 edit-preview.log），不覆盖正式 out/final.mp4。
改字幕时按字幕所在帧区间调整 range；修改后输入自然不再等于冻结清单，不重冻结。
CDP 的自动 HTML 修改、实际缓存复用、缺源拒绝、损坏音频缓存恢复与独立验收包
复用已有 release_acceptance；它要求清洁源码 HEAD，写入新的隔离副本：

```bash
env python3 scripts/release/release_acceptance.py "v$(cat VERSION)" --candidate --chrome "$CHROME" --output "$RUN/cdp-release-acceptance"
```

此入口固定 CPU/CDP，即使外层设置 VTX_GFX=cef 也不会改用 CEF；其包、freeze
与发行资格不接受 CEF。它不验证上面的 CEF 编辑/恢复；CEF 恢复实测仍待在隔离
项目中完成，不能拿 CDP 的 cache/gfx/ 观察或发行摘要代替。

供 #120 对照时，每次从未渲染 input 另建副本，固定同一源码、输入、配置和并发；
按 CDP/CEF、CEF/CDP、CDP/CEF 至少三轮交错配对，失败也保留。这里的冷项目不含
既有 cache/out，不等于清空宿主页缓存；暖跑单列。使用现有 events 解析器确认
事件完整后读取 run_terminal 的 CLI 观察时长和 scope=stage 的阶段时长；调用
`vtx.runtime.events.read_events` 的 complete 必须为 true，事件写失败不会让渲染失败。它们
不含全部启动/收尾，不把并发阶段相加当总墙钟，完整命令墙钟见 *.time.txt。
事件资源字段未测，不虚报 RSS；准备成本与 all/strict QC 分开。没有实际同输入
成片和配对数据时不选优化方向，不声称 CEF 提速。

## 确定性与工具链

- 媒体配方：lavfi 确定信源、整数帧、48kHz、显式编码参数与线程
  （manifest `media_generation`+`codec_args` 全量在册）。
- 同工具链（version_line+binary SHA256 双锚）两份全新输出 encoded SHA256
  必须相等；跨 FFmpeg 版本不承诺容器字节相同；未列入工具链为
  unsupported/unverified，不得静默并入同一资产版本。
- benchmark manifest（templates/standard-demo/benchmark/benchmark_manifest.json）
  是先冻结期望值；重冻结是维护操作：`env python3 scripts/demo.py
  freeze-manifest`（写态，需真实 ffmpeg，写仓内 manifest）。

## 测试与门禁

tests/contracts/test_demo_fixture.py 分两层，执行环境边界如下：

- **合同/反例单测（ContractTests）**：无需 ffmpeg，由
  `bash scripts/run_all_tests.sh` 的默认全量发现执行；普通 PR 精选集未收录本模块。
  CI 的选择范围见 [CI 回归覆盖](../DEV.md#ci-回归覆盖)，不据其他 job 通过宣称本类已执行。
- **真实生成测试（RealGenerationTests，5 例）**：本质上绑定冻结工具链
  （ffmpeg 8.1.3 version_line+binary SHA256 双锚）。CI 的 ubuntu 发行版
  ffmpeg 永远 unsupported，因此该类**不注册进 CI strict ffmpeg 零 skip
  批次**（`scripts/ci_checks.py` gate `ffmpeg`），改为独立的
  `demo-toolchain` 批次：仅当 `VTX_TEST_DEMO_TOOLCHAIN=1` 显式 opt-in 时
  注册执行，且批次内零 skip——工具链不匹配冻结清单是硬失败（错误原因
  随 demo.py stderr 一并给出），不是 skip。冻结工具链环境（本机
  ffmpeg 8.1.3 双锚或云端冻结镜像）跑法：

  ```bash
  env python3 scripts/ci_checks.py demo-toolchain   # strict 零 skip 全量
  ```

  普通本地全量跑（无该 env）该类以 skip 形式逐项列出并给出原因，不会
  静默消失：ci_checks 的 media_inventory 源码扫描以
  `VTX_TEST_DEMO_TOOLCHAIN` 为锚点强制把该类登记进 `demo-toolchain`
  批次清单（漏登记会在 static/媒体门前置检查报错）。

两道契约均未放宽：demo.py 的工具链冻结锚不变，strict 门零 skip 不变；
冲突在测试注册面解决——unsupported 环境不执行该类，而非允许它 skip。
相关回归与门禁聚合见根 README.md 与 AGENTS.md（链接不复述）。
