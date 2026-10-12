# A3：ASR init 默认模式离线修复报告

## 结论与范围

默认探测不再隐式安装依赖或下载模型；只有显式 `--download` 才允许联网。真实本机最终探测 **10 秒退出 1**（缺少 0.6B 模型，未达到 90 秒超时），六组隔离回归通过，离线 doctor 的 asr health 保持 ✓。

修改/新增仅限：
- [init.sh](init.sh)：参数解析、默认离线分支、网络命令展示/门禁、pip 超时与重试限制、失败汇总、头注释和帮助。
- [test_init_offline.sh](tests/test_init_offline.sh)：可直接由 bash 执行的隔离 shell 回归。
- [本报告](REPORT-A3.md)：修复和验证记录。

未修改 [asr_cli.py](asr_cli.py)、[app.json](app.json) health 命令、版本三源或 docs；没有 git commit。共享工作区其他智能体的变更不属于 A3。

## 根因与方案

原 [init.sh](init.sh) 没有参数解析：解释器探测无结果时，会直接新建 venv 并执行无超时参数的 pip 联网安装；模型缺失时也会无条件执行 ModelScope download，成功后 curl 请求 revision。用户无法选择“只探测”，慢网下安装缺乏脚本级前置输出，表现为挂起。

修复：
1. 默认只执行本地解释器/包/模型探测与原有 localhost sidecar 冒烟。缺解释器不创建 venv，缺包/模型给出中文告警与 `bash apps/asr/init.sh --download` 指引，失败遵循原 `FAIL=1` 汇总/退出约定。
2. `--download` 显式授权创建环境、补依赖、下载缺失模型；`--help`/`-h` 零探测退出 0，未知参数退出 2。
3. pip、两种 ModelScope 调用分支、curl 都经统一 `run_network` 防御门禁，执行前用 `printf %q` 打印完整可复制命令。日志写 stderr，避免混入 curl JSON 管道。
4. pip 使用选定解释器的 `-m pip install --timeout 60 --retries 2`，避免搬家后 pip shebang 失效；无需依赖 macOS 缺省不存在的 `timeout`。curl 显示进度，连接与总时间均限制为 10 秒。
5. 保留 config → IME → 本模块解释器优先级、模型优先链、已就位资产复用及 sidecar 冒烟。不因 `--download` 重下已有模型；已具备依赖时不调用 pip。安装/环境创建失败仍进入汇总；未就绪的下载通道不继续尝试下载。

## 验证证据

### 语法与隔离单测

```bash
bash -n apps/asr/init.sh
bash -n apps/asr/tests/test_init_offline.sh
bash apps/asr/tests/test_init_offline.sh
```

语法均 exit 0；单测 exit 0，输出：

```text
PASS: default missing interpreter is local-only with summary
PASS: explicit download prints commands and bounds pip timeout/retries
PASS: ready interpreter/models are reused in both modes
PASS: existing interpreter missing packages requires explicit download
PASS: pip failure has summary and skips model network commands
PASS: help and invalid arguments do not perform initialization
All 6 ASR init offline regression cases passed.
```

单测将脚本复制到临时仓库，临时覆盖 HOME/PATH/IME 根；PATH 内的假 pip 记录调用参数，假解释器将 `-m pip` 转交假 pip，假 ModelScope 只建立空模型标记，假 curl 只输出 JSON。断言普通模式无 pip/ModelScope/curl 调用，`--download` 调用含 `--timeout 60 --retries 2`，且三类命令均前置可见。全部就绪后两种模式均无联网工具调用。临时环境不写真实配置/venv/模型，退出自动还原环境并核验绝对路径后清理临时目录。未进行真实依赖安装/模型下载。

### 真实 90 秒边界验证

最终版本运行：

```bash
start=$SECONDS
timeout 90 bash apps/asr/init.sh
rc=$?
printf 'PROBE_EXIT=%s ELAPSED_SECONDS=%s TIMEOUT_LIMIT=90\n' "$rc" "$((SECONDS-start))"
```

关键输出：

```text
== asr 模块 init（幂等；复用本机已有资产优先）==
本地探测模式：不联网；需要安装/下载时请运行 bash apps/asr/init.sh --download。
✓ qwen-asr 可导入：unknown（Python 3.13.11）
✓ modelscope 可导入（模型下载通道）
✓ 模型 Qwen3-ASR-1.7B（复用）
✗ 缺少模型 Qwen3-ASR-0.6B；本地探测不联网。请运行 bash apps/asr/init.sh --download，或配置已有 asr.model_dir。
✓ 模型 Qwen3-ForcedAligner-0.6B（复用）
✓ sidecar 冒烟通过（127.0.0.1:18085 /health，模型未加载、秒级）
== init 汇总 ==
✗ 存在失败项（见上）
PROBE_EXIT=1 ELAPSED_SECONDS=10 TIMEOUT_LIMIT=90
```

脚本退出 1 而非 timeout 的 124/137；有中文指引和完整汇总，没有执行网络命令行。先前一轮也在 16 秒退出 1；最终优化默认探测不重复 import 后为上述 10 秒。

**现场差异如实说明**：本次机器已有可复用的本模块 venv，qwen_asr/modelscope 均可导入，1.7B 与 Aligner 已就位，缺 0.6B。因此实际告警来自缺模型，并非任务预设的缺包；缺解释器和缺包场景由上述隔离单测覆盖，没有破坏真实资产来制造失败。

### doctor

```bash
env VTX_DOCTOR_OFFLINE=1 python3 vt.py doctor
```

exit 0，关键输出：

```text
✓ [app:builtin] asr｜health ✓（exit 0｜usage: asr_cli.py [-h] [--base-url BASE_URL]）｜requires ✓
doctor 结论：✅ 通过
```

## 风险与边界

- 默认模式现在可能因缺资产返回 1，这是保留原失败约定的主动告警；用户需要自行选择 `--download`，不能继续依赖裸 init 隐式安装。
- pip 的超时/重试是每次网络请求限制，不是整次安装总耗时上限；显式下载大模型仍可能较久，但完整命令和工具进度可见。未为 ModelScope 大模型下载设置过短总时限。
- 默认仍保留本地包 import 与 localhost sidecar 冒烟；损坏/异常的用户解释器或占用端口可能导致失败，本次 90 秒内证明不等于任意环境的硬总时限。
- 本次没有真实网络下载，也没有在 macOS 原机运行；兼容性依据是标准 pip 参数与不引入 GNU timeout 运行依赖，现有 Windows 路径处理保持不变。
