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

tests/test_demo_fixture.py（合同+反例；真实生成测试挂 VTX_TEST_FFMPEG，进
scripts/ci_checks.py strict ffmpeg inventory）。相关回归与门禁聚合见根
README.md 与 AGENTS.md（链接不复述）。
