# demo-fixture —— standard-demo 冻结输入与 demo.py 入口专题（#177 / 父任务 #91）

> 事实 home：本专题是 demo 工程输入包（templates/standard-demo/）与唯一入口
> scripts/demo.py 的说明 home。包内构成与 profile 对照见
> templates/standard-demo/README.md（链接不复述）。本切片不运行 renderer/QC/CEF，
> 相关结论一律 UNRUN，交后续切片在同一入口续接。

## 三步入口

```bash
env python3 scripts/demo.py preflight --profile quick    # 只读：版本化 JSON 体检
env python3 scripts/demo.py preflight --profile standard
env python3 scripts/demo.py preflight --profile gfx-stress
```

preflight 区分 prepare 依赖（python/ffmpeg/ffprobe/随包字体）与 render 依赖
（CDP/CEF）：缺 CEF 不会把 prepare 判成同一成败状态；CEF/Electron 本仓本切片
不安装不运行，`cef` 字段恒为 null+reason。gfx-stress 为引用型 profile
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

tests/test_demo_fixture.py 分两层，执行环境边界如下：

- **合同/反例单测（ContractTests，8 例）**：无需 ffmpeg，随
  `bash scripts/run_all_tests.sh` 与 CI 各 job 全量执行。
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
