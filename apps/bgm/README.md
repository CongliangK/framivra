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

- SF2 动态加载用户安装的 libfluidsynth；SFZ 调用用户的 sfizz_render；sample 读取用户提供的音频。
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

## 测试与迁移来源

```bash
python3 -m pytest apps/bgm/bgm_gen/tests -q -ra
python3 -m pytest apps/bgm/bgm_gen/tests -q -ra -m "fast or slow or service"
```

默认 fast；slow/service 需显式选择。缺用户音色、外部历史工程/冻结基线时明确 skip；保留原断言，配置到位后继续严格验证。新 fresh 测试实跑纯 synth 全链、双渲一致、配置/输出隔离和无模型下载守卫。测试不全局 chdir、不清理其他任务临时目录；运行分档见[测试说明](<bgm_gen/tests/README.md>)。

迁入源为 [Scoreweft 固定提交 804c24e](https://github.com/CongliangK/scoreweft/tree/804c24ecf2d3e0a55fbd5b2225d3b09da7e86f87)。[原 LICENSE](<LICENSE>)及版权保留；历史决策/计划从该提交追溯，不把原 51KB AGENTS 引入为 Framivra 新规则。源码、schema、descriptors、必要编目、工作流和原创示例随本仓维护；个人 runtime、音源、权重、媒体预览、out/cache、虚拟环境与 Git 历史均未复制入库。
