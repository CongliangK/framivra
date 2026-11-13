# USAGE.md —— CLI 速查 + 署名义务（工具级常备内容）

> 当前入口为 `python3 apps/bgm/bgm.py`（Framivra 根目录）；下文 `bgm` 均指该命令，不需要个人包装脚本。运行环境/输出位置见 [app README](../README.md)。历史案例引用仅作经验溯源，不要求旧源码或案例仓在位。render/stem/master/edit 会写用户指定产物或工程。

## 一、CLI 用法速查（源=REPORT §⑤）

```bash
bgm doctor                          # 环境体检（runtime.json 一致性）
bgm engines                         # 引擎清单+确定性分级
bgm render-engine --engine ai --spec /tmp/ai_spec.json   # 单引擎（AI 素材生成）
bgm stem <audio> --out-dir <dir> --sr 44100              # demucs 分轨
bgm render <project.json> -o out/<name>                  # 工程→stems→混音→母带→QC
bgm render <project.json> --incremental                  # 增量渲染（默认关闭；BGM_NO_INCREMENTAL=1 硬闸）
bgm master <in.wav> --lufs -14 --tp -1 -o <out.wav>      # 单独母带
bgm qc <audio> --project <project.json>                  # 独立 QC 复测
bgm catalog                         # 音色目录
```

**v3 操作面速查**（BG-D11/D12；`--help` 逐级可查，target 寻址=show 规范排序视图序号——连续编辑必须每次 edit 后重新 show 取索引）：

```bash
bgm show {engines,structure,notes,cc,events,diff} [--json]   # 检视层：结构/音符窗/CC 车道/事件流全序/undo diff
bgm edit {insert,set,move,delete} <project> --target <路径>   # 编辑动词四件
        [--window beat:a-b|sec:a-b] [--payload '<json>']      # 窗口谓词（[a,b) 半开）；对象=深度合并
        [--copy-of <源>] [--transpose <半音>] [--quantize <拍格>] [--scale <比>]
bgm edit {split,merge} <project>                              # 内联 ↔ $ref 双布局互转
bgm edit undo <project> [--list] [--force]                    # 撤销（漂移检测；--force 覆盖；--list 列栈）
```

target 路径族：`tracks/<name>[/notes[/<i>]|/events[/<i>]|/clip|/params[/<key>]]`、`automation/<i>`、`sections/<i>`、`meta/<field>`、`master/…`。

**v3.4 资源感知并行开关**（BG-D17；并发缺省=min(⌈(可用内存−地板)/per_task⌉, cpu×0.5, 硬帽 8)，DispatchGate 派发实时内存门控；GPU 引擎排除池外；**确定性=并行≡顺序逐位相等**）：

```bash
BGM_NO_PARALLEL=1 bgm render <project.json>    # 硬关并行：完全走旧顺序路径（BGM_NO_INCREMENTAL 同款硬闸语义）
BGM_MAX_PARALLEL=N bgm render <project.json>   # 并发上限覆盖（与硬帽 8 取 min 只能更小；非法值/门控超时 exit 1 干净报错）
BGM_MEM_FLOOR_GB=X bgm render <project.json>   # 内存地板覆盖（GB；缺省 max(6, 总内存×20%)——地板不足时派发等待而非冻机）
```

## 二、署名义务（源=REPORT §7.6/§8.5；红线①）

**发布使用 SalamanderGrand / VPO 音色的成品时，视频简介模板（直接复制）**：

```
BGM 钢琴音源：Salamander Grand Piano V3 by Alexander Holm (CC-BY 3.0) — https://archive.org/details/SalamanderGrandPianoV3
BGM 弦乐音源：Virtual Playing Orchestra by Paul Battersby (free music use; layered sample licenses) — https://virtualplaying.com
```

- Salamander Grand Piano V3：CC-BY 3.0，作者 Alexander Holm（SFZ 化 kinwie、retune Markus Fiedler 的衍生层随库声明）。
- VPO（Virtual Playing Orchestra 3.2.1.1）：用库制音乐（含商用）完全自由，**再分发采样**需署名 Paul Battersby——本产线只在视频中使用不再分发采样，署名为道义+协议双保险；复合许可原文=库内相对路径 `Documentation/license.htm`（Sonatina Symphonic Orchestra CC-BY + NoBudgetOrchestra CC-BY + Iowa 等分层来源）。
- VSCO2 CE：CC0（Ed Garnero 等），无署名义务——如实记录。
- Sonniss GDC 系（鼓 one-shot）与 Kenney：royalty-free/CC0 免署名，无义务。
- ACE-Step 1.5（AI 素材冻结件）：Apache-2.0 系，无署名义务。
- 底稿溯源（如命运 Mutopia `Symphony5_1.mid`，Public Domain）：四要素封存见 `music_project/examples/fate/SOURCE.md`（2026-09-09 分家随迁）。
