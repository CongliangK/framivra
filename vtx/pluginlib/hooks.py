"""钩子总线（§2.2 两缝）+ 三不变量聚合入口（§2.1，设计权威 docs/ARCHITECTURE.md）。

扩展点（v1 仅音频侧，值域冻结）：
  缝 A `prepare_source`（denoise 型）：源级键插件——在源音频提取的**单一实现**
    （stages.source_audio_extraction）内统一触发，一源一产物进 cache/audio/
    （内容寻址键见不变量 ①）；无插件注册 = 直抽裸 wav；环境缺失 fail-hard
    （权重不在 → 阶段失败退出，不回落裸音频——回落 = 产物标注降噪实未降噪）。
  缝 B `audio_events`（duck-static 型）：轨级键插件——_audio_graph 构建
    filtergraph **前**对全量 tracks 的纯内存 events 变换，**不写回 timeline**
    （幂等天然成立）；区间渲染走同一缝（窗口模式自动正确，无需特判）。

插件加载走**包导入** `importlib.import_module("plugins.<name>")`（§2.3 规则，
禁 spec_from_file_location——绕过包语义是双模块裂分的另一入口）。
P1-2 时点 plugins/ 无实体包：**总线空转**——所有入口返回空集/False/原样列表，
内核零行为变化（三不变量已接线，见 pluginlib/__init__.py 接线表）。
"""
import hashlib
import importlib
import json

from . import registry as _registry

HOOK_NAMES = ("prepare_source", "audio_events")


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
    """生效插件清单（installed+enabled 且声明了钩子）→ [{name, version, module}]。

    P1-2 恒为 []（registry 两官方插件均 installed:false，总线空转）。
    c 参数预留（P2b：按 timeline 实际声明键过滤「对本 timeline 生效」的插件）。
    """
    out = []
    for name in sorted(_registry.declared_plugins(repo_root)):
        if _registry.plugin_state(name, repo_root) != "active":
            continue
        if not _plugin_hooks_declared(name, repo_root):
            continue
        version = str(_registry.declared_plugins(repo_root)[name].get("version", "?"))
        out.append({"name": name, "version": version, "module": None})
    return out


def _load_plugin_module(name: str):
    """包导入 plugins.<name>（P2b 实体落地后可用）。

    **加载失败 fail-hard**（P1 对抗审查 F4 修正，§1.2 缝 A 同源纪律）：插件已
    生效（installed+enabled+声明钩子）却模块起不来 = 安装破损——静默回落裸产物
    等于「标注处理实未处理」，不可信。抛 RuntimeError 由阶段崩溃；缺件属安装态
    矛盾另由 doctor/registry 报告，总线不重复报。"""
    try:
        return importlib.import_module(f"plugins.{name}")
    except Exception as e:
        raise RuntimeError(
            f"插件 plugins/{name} 已生效但模块加载失败（安装破损？）：{e}") from e


def run_prepare_source(c, sid: str, wav) -> bool:
    """缝 A：源级键插件的产物钩子（stages.source_audio_extraction 内统一触发）。

    逐个询问声明了 prepare_source 的生效插件（约定：插件暴露
    ``prepare_source(c, sid, wav) -> bool``——自行产出 wav 并返回 True，环境缺失
    自行 fail-hard）。任一插件产出 → True；无插件/全部不受理 → False（调用方
    直抽裸 wav）。P1-2 恒 False（空转）。
    """
    for p in active_plugins(c):
        if "prepare_source" not in _plugin_hooks_declared(p["name"]):
            continue
        mod = _load_plugin_module(p["name"])
        fn = getattr(mod, "prepare_source", None) if mod else None
        if callable(fn) and fn(c, sid, wav):
            return True
    return False


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
    """对本 timeline 生效的插件参数摘要：registry 键声明在 timeline 中的取值
    （排序 JSON 的 sha1 前 12 位）——插件改默认参/键值变 → 摘要变 → 回执失效。"""
    picked = {}
    try:
        key_map = _registry.registry_key_map()
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
