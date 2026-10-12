# REPORT-A4 · aroll/sync CLI 参数面与依赖缺失指引修复

> 子智能体 A4 交付（2026-10-11）。两项已实证缺陷修复：① aroll 两件本体缺 `--help`；
> ② sync 脚本缺 scipy 时裸 traceback。写域仅 `apps/aroll/` + `apps/sync/`（代码+tests+必要处 README 最小改动）。

## 缺陷1 · aup3_extract.py（及 trim_takes.py）无 --help

**实证**：`__main__` 直接把 `sys.argv[1]` 当 aup3 路径 → `aup3_extract.py --help` 对 `--help` 做只读 SQLite 打开 → `sqlite3.OperationalError` 裸 traceback。同目录 `trim_takes.py` 同病（`--help` 被当 src_dir）。

**修复**（argparse，标准库；两件同风格）：
- `aup3_extract.py`：`build_arg_parser()` = positional `aup3` + `out_dir`（nargs='?'，默认 `.`）+ `--dump`（store_true）。**位置参数顺序、缺省值、--dump 语义与旧 sys.argv 手工解析完全一致**（旧调用方式全部兼容；且修正了旧版 `--dump` 混入 argv[2] 被当 out_dir 的隐患）。
- `trim_takes.py`：positional `src_dir`（默认 `.`）+ `out_dir`（默认 `../../assets/voice_takes`），同上。
- `--help/-h` 中文描述（description/epilog/help 全中文）。
- `__main__` 包 `try/except`：`sqlite3.DatabaseError`（文件不存在 mode=ro / 非 SQLite / 缺 project 表）与 `OSError` → 一行式中文报错 + 退出码 1，无 traceback；参数错走 argparse 标准 usage+exit 2。
- `health_check.py` 探针不变（compile 语法检查），docstring 去除已失效的「无 --help 面」表述。

## 缺陷2 · sync_audio.py 缺 scipy 裸 traceback

**实证**：本机基础 python3（`~/miniconda3/bin/python3`，doctor 同款解释器）有 numpy/soundfile/soxr、**无 scipy**；旧代码 scipy 懒 import 深处 `bandpass()`，要到 ffmpeg 转码完两轨后才 `ModuleNotFoundError` 裸 traceback。

**修复**（`apps/sync/sync_audio.py`）：
- 顶部 `import numpy` 包 try/except（缺库不再炸 import 期）；`main()` 在 **argparse 之后** 调 `require_deps()` 统一预探 numpy/scipy/soundfile/soxr → 缺则 stderr 输出中文指引、`sys.exit(EXIT_MISSING_DEP=3)`（专用退出码，与 argparse=2 / 运行期=1 区分）。
- 指引内容：点名缺失包 + 推荐解释器（优先 `config.local.json` `denoise.python`（`VTX_CONFIG` 覆盖支持），路径 expanduser 后**验证存在才引用**；否则本机默认约定路径 `~/miniconda3/envs/denoise/bin/python`，存在则注明「本机已安装」；均无则通用兜底文案）+ `python3 -m pip install <缺包>` 备选。无硬编码单机路径。
- 置于 argparse 之后的关键收益：**`--help` 零第三方依赖可用** —— app.json health.command = `sync_audio.py --help` 由 doctor 用系统解释器跑，此性质保证任何解释器下 health 稳定。
- scipy 存在时行为不变（require_deps 只做 import 探测，全齐则零输出直进原逻辑）。

**同式处理**（grep 全扫 `apps/sync/`，第三方裸 import 仅此两处）：`repack_voice.py` 顶部 `import numpy` 同式加守卫（仅需 numpy），缺则中文指引 + 退出码 3。其余 import 均标准库不动。

## 验证记录（全过）

| 项 | 结果 |
|---|---|
| `env python3 apps/aroll/aup3_extract.py --help` | rc 0，usage + 中文描述 + `--dump` 在册 |
| `env python3 apps/aroll/trim_takes.py --help` | rc 0，usage + 中文描述 |
| 假 aup3（垃圾字节）/ 文件不存在 | rc 1，一行中文「无法读取工程…」，无 traceback/OperationalError |
| `env python3 apps/sync/sync_audio.py /tmp/x.wav /tmp/y.wav /tmp/z.wav`（基础 python3 无 scipy） | rc **3**，中文指引（引用本机实际 denoise 路径 + pip 备选），无 traceback |
| `VTX_CONFIG` 指向含 `denoise.python` 的配置 | 指引引用配置路径；配置路径不存在则回退本机默认（兜底验证） |
| denoise 解释器实测（合成 45s 双 wav，延迟 +1.5s） | 全链走通：a=1500.0ms、漂移 0ppm、复验残差 0.00ms、verdict **OK**、rc 0、report.json 落盘（scipy 存在时行为不变的实证） |
| `cd apps/aroll && env python3 -m unittest discover -s tests -t .` | 5 tests OK |
| `cd apps/sync && env python3 -m unittest discover -s tests -t .` | 6 tests OK |
| 仓门禁同款发现（`-s apps/<app>/tests -t apps/<app>`，自仓根） | 两套均 OK（已入 `scripts/run_all_tests.sh` 批次二发现面） |
| `env VTX_DOCTOR_OFFLINE=1 python3 vt.py doctor` | rc 0；`aroll｜health ✓`、`sync｜health ✓` |

## 新增测试

- `apps/aroll/tests/test_cli_args.py`：--help rc 0/usage/中文（两件）；无参数 = argparse 干净报错 rc 2（旧版 IndexError traceback）；假 aup3 / 缺文件干净报错。
- `apps/sync/tests/test_dep_guard.py`：--help 在 **PYTHONPATH stub 屏蔽 numpy+scipy+soundfile+soxr** 下仍 rc 0；缺 scipy / 缺 numpy（sync_audio、repack_voice）→ 中文指引 + rc 3 + 无 traceback；另设按本机真实依赖面的用例（已装 scipy 则 skip）。

## 跨平台与安全

argparse 用法跨平台；错误指引路径先验证存在再引用、均有兜底文案；测试与脚本 subprocess 一律 list 形式传参（无 shell 注入面）；未引入任何第三方 pip 依赖（argparse/importlib 均标准库）。

## 变更清单

- 修改：`apps/aroll/aup3_extract.py`、`apps/aroll/trim_takes.py`、`apps/aroll/health_check.py`（docstring）、`apps/sync/sync_audio.py`、`apps/sync/repack_voice.py`、`apps/sync/README.md`（依赖节 1 行）
- 新增：`apps/aroll/tests/{__init__,test_cli_args}.py`、`apps/sync/tests/{__init__,test_dep_guard}.py`、本报告
- 未动：版本三源、docs/、skill(s)/、git commit（无）
