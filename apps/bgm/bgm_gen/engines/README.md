# engines/ —— 引擎插件契约与开发指南

引擎层是本工具的"插件系统"：现役 7 引擎（diffrhythm 只是历史候选，未实现），全部经同一契约接入——CLI 与编排层（`core/project.py`）不感知引擎内部实现。契约事实源 = `base.py` 与 `__init__.py` 的 docstring，本文照抄整理。

## 契约（BG-D6：envelope + payload 两层）

**输入**（`run_engine(spec)` 的 spec，envelope 全引擎统一、统一版本号 `"1"`）：

```json
{"engine": "synth", "spec_version": "1",
 "sr": 44100, "channels": 2, "out": "可选，输出路径",
 "payload": {"…引擎自有字段…"}}
```

- `sr`/`channels` 可选（默认 44100 / 2）；`payload` 各引擎自有、各自版本化（字段文档见各引擎模块 docstring）。
- **`spec["sr"]` 若声明，结果必须相等**（BG-D5：引擎内部负责重采样归一；`run_engine` 强制校验，违例抛 `EngineError`）。

**输出**（`render()` 返回 dict，必含）：`path / sr / channels / duration_s / rms_db`——缺任一键 `run_engine` 拒收；另自动补 `engine` 与 `determinism`。

**determinism 三选一声明**：`deterministic`（同 spec 逐位复现）/ `tolerant`（GPU 归约非逐位，容差带断言）/ `nondeterministic`（GPU 扩散，seed 固定亦不保证）。

**缺省输出**：`default_out(*rel)` = `<调用者 cwd>/out/<rel>`，不写 app 安装目录；显式输出优先。

## 如何新增一个引擎

1. 在本目录加模块（如 `my_engine.py`），**不以下划线开头**——包导入时 `pkgutil` 自动扫描本目录并 import（下划线模块与 `base` 除外），无需改任何共享文件。
2. 类继承 `BaseEngine`，打 `@register_engine("my_engine")` 装饰器——自注册进 `ENGINES`，CLI（`bgm engines` / `render-engine`）与编排层即刻可用。worker 并行开发 = 只增文件、不改共享文件。
3. 类属性声明 `determinism`（三选一）与 `summary`（一句话，`bgm engines` 展示）。
4. 实现 `render(self, spec)`：校验 payload → 渲染 → 返回契约出参。
5. **模块级 import 只允许标准库 + numpy**（系统 python3 可用）；torch 等重依赖一律放函数体内，或经 `runtime.json` 指定的解释器以子进程调起（解释器由用户自备，统一外置配置见 [app README](../../README.md)；doctor 和引擎均只读，不自动安装或下载）。
6. **配 `descriptors/<engine>.json`**（BG-D13）：能力面声明式事实源，`validate_payload` 与 params 透传据此驱动——缺失则新引擎在描述符校验体系内不可见。

辅助模块（在引擎目录但不是引擎）：`midi_util.py`（SMF 写入器，BG-D7 记谱 `[pitch, beat, dur, vel]` → MIDI tick，无装饰器仅当库导入；v3 另承载 `event_sort_key()`/`events_to_smf()` 事件流编码，旧路径 `write_smf`/`write_notes_smf` 字节冻结）、`gpu_guard.py`（BG-D8 显存闸 + 文件锁 `/tmp/bgm_gpu.lock`）、`_wavio.py`（wav 读写小工具，下划线名 = 不被自动发现）、`descriptor_check.py`（描述符结构检查，doctor 与 base 共享）、`harvest.py`（描述符收割器，手动运行）。

## 描述符注册表（v3，BG-D13）

- **事实源**：`descriptors/<engine>.json`（7 份，git 版本化）——参数清单（稳定字符串 id + 类型/量纲/枚举）、`events_supported` 事件消费声明、`invariants` 跨字段约束、`binary_pin{version, sha256}`（sha=advisory 黄灯，doctor 交叉核验不拉红 exit code）、`coverage`（三档来源 A 收割/B 起草+探针/C 手写降级，gaps 强制标注）。
- **运行时**：`base.validate_payload(engine, payload)` 单字段约束描述符驱动 + invariants 断言，`run_engine` 唯一入口调用（7 引擎内硬编码校验已退役）；`content_hash_key()` 组装增量渲染 hash 键。schema（`schema/engine_descriptor_v1.json`）为文档性契约——运行时手写驱动，零新增 pip 依赖。
- **收割与防漂移**：`harvest.py` 从引擎源码 payload 读取行为收割（第一事实源），证据日志落 `descriptors/harvest_log/`；防漂移四件套=pin+行为探针（`tests/test_descriptors.py`）+doctor 增查+升级先 diff 再重收割。
- **呈现**：`bgm show engines [--json]`；`bgm catalog --write` 重落盘保持 timbres.md payload 段同步（test_catalog 同步锁）。
- 设计全文：[迁入前设计记录](https://github.com/CongliangK/scoreweft/blob/804c24ecf2d3e0a55fbd5b2225d3b09da7e86f87/docs/PLAN_v3.md) §3.1。

## 现役引擎一览（= `bgm engines` 输出）

| 引擎 | determinism | 一句话功能 |
|---|---|---|
| synth | deterministic | synthlib 程序化音色（kick/snare/hat/fm_ep/bass/lead/nylon，纯 numpy） |
| sf2 | deterministic | 任意 .sf2 + bank/program（libfluidsynth ctypes 文件渲染） |
| sfz | deterministic | SFZ 乐器（VSCO2 CE / Karoryfer，sfizz_render 子进程） |
| sample | deterministic | one-shot 采样触发（Kenney 等，numpy 时间线混合） |
| master | deterministic | 母带：loudnorm 两遍法（-14LUFS/-1dBTP）/ matchering 参考匹配 |
| demucs | tolerant | htdemucs 分轨（comfyui env 子进程；四分轨默认，GPU 串行保护 BG-D8） |
| ai | nondeterministic | ComfyUI HTTP 工作流提交（ACE/Music3 整段素材生成；service 档依赖 8188） |
| （diffrhythm） | — | 可选档未装（BG-D10 用户裁定；venv-dr 建立后才实现） |

确定性分级的证据锚点：`tests/baselines_w2a.json`（确定性引擎 RMS 基线）、`tests/baselines_w2b.json`（demucs/master 分级证据）；`tests/test_eden_repro.py` 另以完整成品 mix.flac sha256 做端到端位级回归。AI 素材走**冻结复现协议**：生成物落盘冻结后由工程按绝对路径引用，整轨渲染因此逐位可复现（docs/REPORT.md §③）。
