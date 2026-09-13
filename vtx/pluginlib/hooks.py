"""钩子总线（§2.2 两缝）+ 三不变量聚合入口（§2.1，设计权威 docs/ARCHITECTURE.md）。

扩展点（v1 仅音频侧 + v1.1 validate；值域冻结）：
  缝 A `prepare_source`（denoise 型）：源级键插件——在源音频提取的**单一实现**
    （stages.source_audio_extraction）内统一触发，一源一产物进 cache/audio/
    （内容寻址键见不变量 ①）。契约 v1.1（P2b 细化，§2.2 缝 A 小注）：
    ``prepare_source(c, sid) -> Path | None``——插件**自算**内容寻址产物路径并
    保证产物在盘（缓存命中直返 / 缺失则产出 / 环境缺失 fail-hard——打印缺什么
    + exit 1，不回落裸音频，回落 = 产物标注降噪实未降噪）；v1 草案的
    ``(c, sid, wav) -> bool``（wav = 裸提取键路径）会让降噪产物写进裸键——
    内容寻址破坏，已废弃。无插件受理 = 直抽裸 wav。
  缝 B `audio_events`（duck-static 型）：轨级键插件——_audio_graph 构建
    filtergraph **前**对全量 tracks 的纯内存 events 变换，**不写回 timeline**
    （幂等天然成立）；区间渲染走同一缝（窗口模式自动正确，无需特判）。
  `validate`（协议 v1.1 增补，§2.2）：validate 阶段在 _scan_plugin_keys 后
    调用的插件校验扩展点——插件暴露 ``validate(c) -> list[str]``，错误行由
    stages.validate 并入 errs 阻断。用途：插件键的互斥/形状/双施防护等插件级
    契约的早失败（duck:true × duck_strategy 互斥、_meta.bed_static_duck 双施）。

插件加载走**包导入** `importlib.import_module("plugins.<name>")`（§2.3 规则，
禁 spec_from_file_location——绕过包语义是双模块裂分的另一入口）；加载时做
hooks 声明↔代码注册一致性对账（声明了没注册 / 注册了没声明都报错，§2.3）。
P2b 起 plugins/ 有实体包（denoise / duck-static）。「生效」按 timeline 过滤
（active_plugins 的 c 参数）：无插件键时间线 = 空生效清单——回执指纹省略
插件段（v1 逐字节兼容）、总线空转，内核零行为变化（三不变量接线见
pluginlib/__init__.py 接线表）。
"""
import hashlib
import importlib
import json
import sys
from pathlib import Path

from . import registry as _registry

HOOK_NAMES = ("prepare_source", "audio_events", "validate")   # v1.1 + validate（§2.2）


def _plugin_hooks_declared(name: str, repo_root=None) -> list:
    """插件声明的 hooks（registry 条目 hooks 字段 > 实体 manifest extends.hooks）。"""
    reg = _registry.declared_plugins(repo_root).get(name)
    if reg is not None and isinstance(reg.get("hooks"), list):
        return [h for h in reg["hooks"] if isinstance(h, str)]
    for e in _registry.discover(repo_root):
        if e["name"] == name and e["manifest"]:
            declared = (e["manifest"].get("extends", {}) or {}).get("hooks")
            if isinstance(declared, list):
                return declared
    return []


def active_plugins(c=None, repo_root=None) -> list:
    """生效插件清单（installed+enabled 且声明了钩子 且 **本 timeline 声明了其键**）
    → [{name, version, module}]。

    候选双源（P4）：plugins/registry.json 静态注册表（官方件）+ **已发现 external
    实体**（第三方件——不进静态注册表，manifest 即声明面；验收门 6 的注册路径）。
    P2b 起 c 参数生效（「对本 timeline 生效」过滤，§2.1 不变量 ② 的"空生效
    清单"前提）：timeline 未声明插件的任一注册键 → 该插件不计入——无插件键
    时间线的 active_plugins_digest 恒 ""（指纹省略段，与 v1 逐字节一致）、
    contribute_keys/两缝空转（v1 行为）。c=None（无上下文，list/doctor 展示
    等机器态）不过滤。
    """
    names = list(sorted(_registry.declared_plugins(repo_root)))
    for e in _registry.discover(repo_root):
        if (e["kind"] == "plugin" and e["source"] == "external"
                and e["manifest"] and e.get("dir") and e["dir"].is_dir()):
            if e["name"] not in names:
                names.append(e["name"])
    out = []
    for name in sorted(names):
        if _registry.plugin_state(name, repo_root) != "active":
            continue
        if not _plugin_hooks_declared(name, repo_root):
            continue
        if c is not None and not _timeline_declares(c, name, repo_root):
            continue
        out.append({"name": name, "version": _plugin_version(name, repo_root),
                    "module": None})
    return out


def _plugin_version(name: str, repo_root=None) -> str:
    """插件版本（registry 条目 > 实体 manifest——external 件无 registry 条目）。"""
    entry = _registry.declared_plugins(repo_root).get(name)
    if entry is not None:
        return str(entry.get("version", "?"))
    for e in _registry.discover(repo_root):
        if e["name"] == name and e["manifest"]:
            return str(e["manifest"].get("version", "?"))
    return "?"


def _timeline_declares(c, name: str, repo_root=None) -> bool:
    """timeline 是否声明了插件注册键中的任一键（「对本 timeline 生效」判定）。

    键位检索用全量键声明（registry 静态注册表 + external 实体 manifest，P4 起经
    registry.key_map 双源），path 同 _plugin_params_digest。无法判定（c 无 tl /
    插件无键声明）时保守返回 True——宁多生效不静默漏（fail-hard 可见性优先）。
    """
    tl = getattr(c, "tl", None) if c is not None else None
    if not isinstance(tl, dict):
        return True
    key_map = _registry.key_map(repo_root)
    scopes = {"sources[]": tl.get("sources", []),
              "audio_tracks[]": tl.get("audio_tracks", [])}
    has_keys = False
    for key, info in sorted(key_map.items()):
        if info.get("plugin") != name:
            continue
        has_keys = True
        items = scopes.get(info.get("path"))
        for it in items if isinstance(items, list) else []:
            if isinstance(it, dict) and key in it:
                return True
    return not has_keys


def _load_plugin_module(name: str):
    """包导入 + hooks 声明↔代码注册一致性对账（§2.3：hooks 由代码注册为单一
    事实源，manifest/registry 声明为对账面——声明了没注册 / 注册了没声明都报错）。

    导入锚双源（P4）：官方件/已注入模块 = ``importlib.import_module("plugins.<name>")``
    （repo 根已在 path；tests 亦经 sys.modules 预置假模块走同一路径）；external 件 =
    实体父目录 **append** 进 sys.path 按目录名包导入、导入后即摘除、并断言
    mod.__file__ 落在该插件目录内（manifest 校验保证 name == 目录名，连字符
    目录名不碍 import_module）——仍是真包导入，**禁 spec_from_file_location**
    （绕过包语义是双模块裂分的另一入口）不破例；append+摘除+落点断言防
    external 父目录下的同名模块顶替官方插件/stdlib（P4 审查 P1-1）。

    **加载失败 fail-hard**（P1 对抗审查 F4 修正，§1.2 缝 A 同源纪律）：插件已
    生效（installed+enabled+声明钩子）却模块起不来 / 对账不齐 = 安装破损——
    静默回落裸产物等于「标注处理实未处理」，不可信。抛 RuntimeError 由阶段
    崩溃；缺件属安装态矛盾另由 doctor/registry 报告，总线不重复报。

    注：contribute_keys 是不变量 ① 入口而非钩子（不在 HOOK_NAMES 值域），
    不参与对账。"""
    try:
        d, origin = _registry.plugin_module_dir(name)
        if origin == "external":
            # P4 审查 P1-1 修正：append（官方包/stdlib 优先）+ 导入后即摘除 +
            # mod.__file__ 落点断言——insert(0) 且常驻会让 external 父目录下的
            # 同名模块（假 plugins/denoise、同名 stdlib）优先解析，包身份纪律
            # 与 import 卫生（§9 门 7(c)）双破。
            parent = str(d.parent)
            added = parent not in sys.path
            if added:
                sys.path.append(parent)
            try:
                mod = importlib.import_module(d.name)
            finally:
                if added and parent in sys.path:
                    sys.path.remove(parent)
            mf = getattr(mod, "__file__", None)
            if not mf or not Path(mf).resolve().is_relative_to(d.resolve()):
                raise RuntimeError(
                    f"external 插件 {name} 的模块落点异常（{mf} 不在 {d} 内）"
                    "——疑似同名模块顶替，拒绝加载")
        else:
            mod = importlib.import_module(f"plugins.{name}")
    except Exception as e:
        raise RuntimeError(
            f"插件 plugins/{name} 已生效但模块加载失败（安装破损？）：{e}") from e
    declared = set(_plugin_hooks_declared(name))
    exposed = {h for h in HOOK_NAMES if callable(getattr(mod, h, None))}
    unregistered, undeclared = declared - exposed, exposed - declared
    if unregistered or undeclared:
        parts = []
        if unregistered:
            parts.append(f"声明了没注册 {sorted(unregistered)}")
        if undeclared:
            parts.append(f"注册了没声明 {sorted(undeclared)}")
        raise RuntimeError(f"插件 plugins/{name} hooks 声明与代码注册不一致"
                           f"（§2.3 单一事实源对账）：{'；'.join(parts)}")
    return mod


def run_prepare_source(c, sid: str):
    """缝 A：源级键插件的产物钩子（stages.source_audio_extraction 内统一触发）。

    契约 v1.1（P2b 细化，§2.2 缝 A 小注）：插件暴露
    ``prepare_source(c, sid) -> Path | None``——**自算**内容寻址产物路径（形如
    ``cache/audio/{audio_key}::<plugin>:{version}:{params_digest}.wav``，audio_key
    经内核 API 面 vtx.api 取得）并保证产物在盘后返回：缓存命中直接返回；缺失
    则产出（环境缺失打印缺什么 + exit 1 fail-hard，不回落裸音频）；未声明键/
    不受理该源返回 None（调用方直抽裸 wav）。任一插件返回 Path → 该源用插件
    产物；prepare 提取与 _audio_graph 读取经 resolve_source_wav 同一解析。
    """
    for p in active_plugins(c):
        if "prepare_source" not in _plugin_hooks_declared(p["name"]):
            continue
        mod = _load_plugin_module(p["name"])
        fn = getattr(mod, "prepare_source", None) if mod else None
        if callable(fn):
            got = fn(c, sid)
            if got is not None:
                return Path(got)
    return None


def resolve_source_wav(c, sid: str):
    """缝 A 读取点统一解析：prepare 产物与 _audio_graph/区间渲染的 wav 读取走
    同一入口（P2b 契约细化——两处各自为政时 graph 会读裸提取键而 prepare 产出
    插件键，降噪静默失效；或反之污染裸键）。返回插件产物 Path；无插件受理 →
    None（调用方回落裸提取键 cache/audio/{audio_key}.wav）。"""
    return run_prepare_source(c, sid)


def run_audio_events(c, tracks) -> list:
    """缝 B：轨级键插件的 events 内存变换（_audio_graph 构建前统一触发）。

    逐个应用声明了 audio_events 的生效插件（约定：插件暴露
    ``audio_events(c, tracks) -> list``——纯内存变换不写回 timeline）。无插件 =
    原样返回（P1-2 恒等）。"""
    for p in active_plugins(c):
        if "audio_events" not in _plugin_hooks_declared(p["name"]):
            continue
        mod = _load_plugin_module(p["name"])
        fn = getattr(mod, "audio_events", None) if mod else None
        if callable(fn):
            tracks = fn(c, tracks)
    return tracks


def run_validate(c) -> list:
    """validate 钩子（协议 v1.1 增补，§2.2）：validate 阶段在 _scan_plugin_keys
    后调用的插件校验扩展点。

    逐个应用声明了 validate 的生效插件（约定：插件暴露
    ``validate(c) -> list[str]``——返回错误行列表，空/非列表 = 通过），错误行由
    stages.validate 并入 errs 阻断。仅对「本 timeline 生效」的插件调用（三分支
    已在 _scan_plugin_keys 把未装/禁用挡在前面，此处不重复报）。用途：插件键
    互斥（duck:true × duck_strategy 同轨）、_meta 双施防护（bed_static_duck
    标记）、键值形状（mode 值域/数值参数类型）等插件级契约的早失败。"""
    errs = []
    for p in active_plugins(c):
        if "validate" not in _plugin_hooks_declared(p["name"]):
            continue
        mod = _load_plugin_module(p["name"])
        fn = getattr(mod, "validate", None) if mod else None
        if callable(fn):
            got = fn(c)
            if isinstance(got, (list, tuple)):
                errs.extend(str(x) for x in got)
    return errs


def contribute_keys(c) -> set:
    """不变量 ①（§2.1）：插件缓存键贡献聚合（hygiene._current_keys 的 ak 并集）。

    插件产物若不注册键，渲染中就会被 budget_gate 触发的 gc 自动回收——本入口
    是插件进 gc 存留白名单的唯一通道（约定：插件暴露
    ``contribute_keys(c) -> set[str]``）。P1-2 恒空集（空转）。
    """
    keys = set()
    for p in active_plugins(c):
        mod = _load_plugin_module(p["name"])
        fn = getattr(mod, "contribute_keys", None) if mod else None
        if callable(fn):
            got = fn(c)
            if isinstance(got, (set, frozenset, list, tuple)):
                keys |= set(got)
    return keys


def _plugin_params_digest(c, name: str) -> str:
    """对本 timeline 生效的插件参数摘要：插件键声明（registry 静态注册表 +
    external manifest，P4 起经 registry.key_map 双源）在 timeline 中的取值
    （排序 JSON 的 sha1 前 12 位）——插件改默认参/键值变 → 摘要变 → 回执失效。"""
    picked = {}
    try:
        key_map = _registry.key_map()
        for key, info in sorted(key_map.items()):
            if info.get("plugin") != name:
                continue
            path = info.get("path")
            items = []
            if path == "sources[]":
                items = c.tl.get("sources", []) if hasattr(c, "tl") else []
            elif path == "audio_tracks[]":
                items = c.tl.get("audio_tracks", []) if hasattr(c, "tl") else []
            for it in items:
                if isinstance(it, dict) and key in it:
                    # 收集全部实例（P1 对抗审查 F2 修正）：只取首例时，多实例
                    # 场景改第二例参数 digest 不变——旧回执放行陈旧产物。
                    picked.setdefault(f"{path}:{key}", []).append(it[key])
    except Exception:
        pass   # 摘要失败不阻断指纹——退化为无参数成分（宁弱不崩）
    blob = json.dumps(picked, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def active_plugins_digest(c) -> str:
    """不变量 ②（§2.1）：生效插件名+版本+参数摘要 → 回执指纹的追加段。

    **空生效清单返回 ""（指纹侧省略段）**——无插件时间线的回执指纹与 v1 逐字节
    一致，v1 回执在 v2 下继续有效（影子基线温态复验依赖此兼容）。不变量 ② 由
    "段的出现/内容变化"保证：插件安装/卸载/升级/改默认参/键值变 → 返回值出现
    或变化 → 录制侧指纹与重算侧失配 → 旧回执拒绝（含卸载后回落 v1 形态时，
    带段录制的旧回执对无段重算同样失配）。
    """
    act = active_plugins(c)
    if not act:
        return ""
    parts = []
    for p in act:
        params = _plugin_params_digest(c, p["name"])
        parts.append(f"{p['name']}@{p['version']}:{params}")
    return "plugins:" + ";".join(parts)


def get_cache_dirs(c=None, repo_root=None) -> set:
    """不变量 ①（§2.1）：插件声明的 cache 子目录名白名单（gc_plan 的 cache 根
    known 并集；registry 条目可选 "cache_dirs": [...]，仅生效插件计入）。

    缝 A 型插件产物多为 cache/audio/ 内的键文件（走 contribute_keys 的 ak 集），
    自立目录型（未来 gfx 侧等）走本白名单。P1-2 恒空集（空转）。
    """
    dirs = set()
    for p in active_plugins(c, repo_root):
        entry = _registry.declared_plugins(repo_root).get(p["name"], {})
        for d in entry.get("cache_dirs", []) or []:
            if isinstance(d, str):
                dirs.add(d)
    return dirs
