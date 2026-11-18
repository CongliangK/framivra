# bgm · Scoreweft 音乐/BGM 工具

音乐工具代码已并入 Framivra，由本 app 统一维护；不需要旧音乐源码仓或个人 `bgm` 包装脚本。保留 `bgm_gen` 导入名、工程 v1/v2、七引擎（synth / sample / sf2 / sfz / ai / demucs / master）、show/edit、MIDI/MusicXML 转换、分析与 synthlib。工程产出的普通音频可直接接入视频时间线；终链仍由 Framivra 内核负责。

## 入口与最小依赖

Python 3 + numpy；完整工程混音/母带/QC 还需 PATH 中的 FFmpeg。pytest 仅测试使用。音色、模型、外部服务和工具二进制均不随 Git 分发，不会自动下载。GPU/torch 只在用户选用相应外部引擎时使用，不影响普通视频或 synth。

以下命令从 Framivra 根目录执行；`--help` 和健康检查只读：

```bash
python3 apps/bgm/bgm.py --help
python3 apps/bgm/health.py
python3 apps/bgm/bgm.py engines
python3 apps/bgm/bgm.py show structure apps/bgm/examples/synth.json
```

**下面的 render 会写指定输出目录**。原创[纯合成示例](<examples/synth.json>)无需任何音色或模型；混合引擎的[原最小示例](<examples/minimal.json>)需用户配置 SF2 和 Kenney 采样。

```bash
python3 apps/bgm/bgm.py render apps/bgm/examples/synth.json -o ./out/bgm-demo
```

显式 `-o` 决定输出目录；不传时使用调用者当前目录的 `out/<工程名>/`，绝不写 app 安装目录。结果含 `mix.flac`、`stems/`、工程快照、`qc.json` 与母带中间件。渲染成功不等于 QC 全过，应检查返回 JSON 的 `qc_ok`。

## 外置运行环境与资产

默认持久数据根为 `$XDG_DATA_HOME/framivra/music`，未设 XDG 时为 `~/.local/share/framivra/music`。默认读取该根的 `runtime.json`；默认文件不存在按空配置运行，synth 仍可用。`MUSIC_RUNTIME` 可显式指定另一个用户 JSON；显式文件缺失、坏 JSON 或错误配置结构会报错，不静默换环境。不会读取包内或旧仓 runtime。

资产优先级：`MUSIC_ASSETS_ROOT` / `MUSIC_SAMPLES_ROOT` → runtime 的对应键 → 数据根的 `assets/` / `assets/samples/`。配置中的相对路径以 runtime JSON 所在目录为基准，支持 `~`、`$VAR`；裸工具名和动态库 soname 保留系统 PATH/loader 语义。工程文件内部路径保留原格式语义，不批量改写旧工程。

用户自行创建的配置示意（路径必须替换为真实本地安装）：

```json
{
  "assets_root": "assets",
  "samples_root": "assets/samples",
  "python_system": "/path/to/python3",
  "tools": {
    "fluidsynth_lib": "libfluidsynth.so.3",
    "sfizz_render": "/path/to/sfizz_render"
  },
  "sf2": {"FluidR3_GM": "assets/sf2/FluidR3_GM.sf2"},
  "demucs": {
    "python": "/path/to/demucs-env/bin/python",
    "repo": "assets/models/demucs",
    "env": {"HF_HOME": "assets/models/hf"}
  },
  "matchering": {"python": "/path/to/matchering-env/bin/python"},
  "comfyui": {"base_url": "http://127.0.0.1:8188"}
}
```

- SF2 动态加载用户安装的 libfluidsynth；显式 `tools.fluidsynth_lib` 只试载该值，错误不回退。未配置时先系统发现，macOS 再尝试 dylib 与 Apple Silicon/Intel 的 Homebrew `fluid-synth` 标准 opt 路径，无需 ABI 软链；解释器与库须同架构。SFZ 调用用户的 sfizz_render；sample 读取用户提供的音频。
- demucs 强制 `--repo` 本地模型仓（含所选模型 YAML 和其引用的 `.th` 权重；直接签名模型则需对应 `.th`）；缺本地权重立即报错。不会因 `HF_HUB_OFFLINE` 而假定 torch 不下载，明确拒绝 `offline=false`。
- ai 只提交[ComfyUI API 工作流](<workflows/README.md>)给用户已有服务；用户应先安装节点、模型并审核工作流。不安装或下载模型，不自动启动服务。
- `doctor` 是完整可选环境诊断，不是最低安装门。manifest 的健康检查仅验证基础代码接口，不要求 GPU、音源、模型、ComfyUI 全装。
- 视频宿主使用 `bgm.python`（可选解释器）和 `bgm.runtime`（可选外置 JSON，传为 `MUSIC_RUNTIME`）；不消费旧 `bgm.cli` / `bgm.repo`。

## Python 接口与操作规范

应用根加入 `PYTHONPATH` 后可直接导入：

```python
from bgm_gen.core.project import render_project
result = render_project("project.json", out_dir="out/music", runtime={})
# result: mix / master / premaster / qc / qc_ok / stems / out_dir / duration_s
```

`runtime=None` 读取生效用户配置；显式字典（包括 `{}`）不重读 JSON，资产环境变量仍可覆盖根。`engines.run_engine(spec, runtime=...)` 返回引擎 envelope；工程层把同一 runtime 传到轨道和母带引擎。路径解析唯一实现为[paths](<bgm_gen/paths.py>)，其函数支持 `environ=` 快照而不改全局环境。show/edit 连续操作需重新 show 获取索引；编辑会写工程及撤销栈。

详细操作、音色先行审听、许可和乐谱来源分别见[CLI 速查](<docs/USAGE.md>)、[创作手册](<docs/PLAYBOOK_BGM.md>)、[NOTICE](<NOTICE.md>)、[乐谱登记](<catalog/scores/REGISTRY.md>)与[引擎契约](<bgm_gen/engines/README.md>)。编目是资源元数据，不是随仓资源安装承诺；不含音源、模型或媒体试听件。历史预览索引仅保留来源与署名资料。

## macOS 原生验收

运行前确认 `python3`、NumPy、FFmpeg/ffprobe 均为已有可用环境；`platform.machine()` 为 `arm64` 才是 Apple Silicon 原生，Intel/Rosetta 需另跑，不能继承结果。无 `/proc` 时总内存按本机页大小读取；可用内存仍未知，并发保守单发，不把总内存当空闲内存。

验收用仓外新目录、自有隔离 HOME/XDG 与显式 `MUSIC_RUNTIME`、`VTX_CONFIG`；不得读取日常音乐配置或借用私有音源。准备原创多轨 synth/sample 工程、one-shot 和视频输入，工程/运行资产路径覆盖中文、空格与 `#`。以下为**写操作**，变量替换为该隔离目录的实际绝对路径；通过官方 CLI 顺序执行，不安装或下载资产：

```bash
python3 apps/bgm/bgm.py show structure --json "$MUSIC_PROJECT"
python3 apps/bgm/bgm.py render "$MUSIC_PROJECT" -o "$MUSIC_OUT" --quiet
python3 apps/bgm/bgm.py qc "$MUSIC_OUT/mix.flac" --project "$MUSIC_PROJECT" -o "$MUSIC_OUT/independent-qc.json"
python3 vtx/vtx.py -t "$VIDEO_TIMELINE" all
python3 vtx/vtx.py -t "$VIDEO_TIMELINE" qc --strict
```

音乐须检查 `qc_ok` 与独立 QC。另用 `edit set`/`edit undo` 验证原文恢复；按[插件输出契约](<../../plugins/bgm-render/README.md>)把同工程的 master/premaster/总线分轨挂三源，逐源比对 PCM、总线增益/声像与工程全长。缓存复验显式设置 `cache.mode: aggressive`，冷/暖均跑完整 `all`，沿现有身份验证 runtime、工程、`$ref`、输出选择及缺分轨失败恢复。全片解码并核对逐帧精确 PTS、采样数、响度与真峰，不以进程成功代替 QC；启用的 finish 仍须执行，侧栏插件拒绝门不绕过。

记录当前源码/解释器/库/音源/插件/产物身份、退出码与 PASS/FAIL/SKIP/UNRUN；该音乐机制工程不是 900 帧 SVG 性能主片。母带/母前/总线分轨与成片人工听审另记，未听审为 UNRUN。无淡变 AAC 瞬态可能超冲，正常淡变输入通过不证明任意瞬态安全，不能降低 TP 门。一次性运行器与其自测已退役，原文可从[固定候选](https://github.com/CongliangK/framivra/tree/49ce3ff9a6c3cfa762033597d0192015fa3fbe02)恢复；有效产品回归继续保留。不要上传含本机路径的原始日志。

| 引擎 | macOS 前提与验收边界 |
|---|---|
| synth | NumPy；用原创多轨合成，无模型/采样库 |
| sample | 用户音频；用原创 one-shot 验证外置根与路径，不代表 Kenney 等第三方库通过 |
| sf2 | 同架构 libfluidsynth + 授权 SF2；缺任一项不能声称实际渲染通过。原创最小 SF2 仅证明通用加载/出声，不代替 FluidR3_GM/整套 GM；legacy GM/MIDI 入口消费 runtime 的 `sf2.FluidR3_GM`，未配置仍保留历史 Linux 路径 |
| sfz | 原生 sfizz_render + SFZ 及其全部采样/include；需配置 `tools.sfizz_render`，独立实跑，不能以 SF2/synth 替代 |
| ai | 用户明确配置并已启动的 ComfyUI、节点/模型/API 工作流；不自动探测默认服务或提交任务 |
| demucs | 已有解释器、包与完整本地权重仓；Mac 用显式 `device: cpu`；auto 的 NVIDIA 探测在无 NVIDIA 时走 CPU，不承诺 MPS |
| master | 基础验 FFmpeg loudnorm；matchering 另需已有独立 Python 环境与授权参考音频，不随 loudnorm 通过 |

缺可选资源时保留具体缺口与待验步骤，不缩成“七引擎通过”。基础闭环通过不满足 [#374](https://github.com/CongliangK/framivra/issues/374) 全部验收及闭单条件。

## 测试与迁移来源

```bash
python3 -m pytest apps/bgm/bgm_gen/tests -q -ra
python3 -m pytest apps/bgm/bgm_gen/tests -q -ra -m "fast or slow or service"
```

默认 fast；slow/service 需显式选择。缺用户音色、外部历史工程/冻结基线时明确 skip；保留原断言，配置到位后继续严格验证。新 fresh 测试实跑纯 synth 全链、双渲一致、配置/输出隔离和无模型下载守卫。测试不全局 chdir、不清理其他任务临时目录；运行分档见[测试说明](<bgm_gen/tests/README.md>)。

迁入源为 [Scoreweft 固定提交 804c24e](https://github.com/CongliangK/scoreweft/tree/804c24ecf2d3e0a55fbd5b2225d3b09da7e86f87)。[原 LICENSE](<LICENSE>)及版权保留；历史决策/计划从该提交追溯，不把原 51KB AGENTS 引入为 Framivra 新规则。源码、schema、descriptors、必要编目、工作流和原创示例随本仓维护；个人 runtime、音源、权重、媒体预览、out/cache、虚拟环境与 Git 历史均未复制入库。
