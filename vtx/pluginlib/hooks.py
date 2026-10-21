"""钩子总线（§2.2 两缝）+ 三不变量聚合入口（§2.1，设计权威 docs/ARCHITECTURE.md）。

扩展点（v1 音频侧 + v1.1 validate + v1.2 probe_source/finish；值域冻结）：
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
  缝 C `probe_source`（协议 v1.2 增补，2026-09-14 P6）：**虚拟源 facts** 钩子
    ——sources[] 条目可以不是媒体文件（如 bgm-render 插件的 music_tools 工程
    JSON）。插件暴露 ``probe_source(c, sid) -> dict | None``：None = 不受理该源
    （调用方回落 ffprobe）；受理则返回 ``{"has_audio": bool, "has_video": bool,
    "nb_frames": int, "fps": num|None, "w": int, "h": int}``（与 Ctx.probe 返回
    同构）。stages.validate 的源级检查与音频事件时长校验统一经本缝取 facts——
    非媒体源不再触发 ffprobe 误报（「无音频轨」/时长 0）。
  缝 D `finish`（协议 v1.2 增补，2026-09-14 P6）：**管线收尾**钩子——`vtx all`
    全链（mux/qc 完成后、进程收尾前）触发，插件暴露 ``finish(c) -> list[str]``
    （错误行；空 = 成功），cli 将错误行打印并以退出码 1 结束（产物已就绪、
    收尾件失败不静默）。qc 失败不阻断本钩（final.mp4 等产物已落盘，存量假阴性
    项目也要能收尾），退出码按 qc/finish 各自失败态汇总。用途：渲染完成后的
    自动导出类动作（blender-bridge 插件的 .blend 审片工程自动导出）。
    --draft / --dry-run 或停在 qc 之前的 --until 不触发；all --until qc 可收尾。

插件加载走**包导入** `importlib.import_module("plugins.<name>")`（§2.3 规则，
禁 spec_from_file_location——绕过包语义是双模块裂分的另一入口）；加载时做
hooks 声明↔代码注册一致性对账（声明了没注册 / 注册了没声明都报错，§2.3）。
P2b 起 plugins/ 有实体包（denoise / duck-static）。「生效」按 timeline 过滤
（active_plugins 的 c 参数）：无插件键时间线 = 空生效清单——回执指纹省略
插件段（v1 逐字节兼容）、总线空转，内核零行为变化（三不变量接线见
pluginlib/__init__.py 接线表）。
"""
from ..diagnostics import indexed

import hashlib
import importlib
import json
import sys
from pathlib import Path

from . import registry as _registry
from .manifest import load_manifest
from .compatibility import require_compatible_core, require_compatible_protocol

HOOK_NAMES = ("prepare_source", "audio_events", "validate",
              "probe_source", "finish")   # v1.1 + validate；v1.2 + 缝 C/D（§2.2）


def _plugin_hooks_declared(name: str, repo_root=None) -> list:
    """插件声明的 hooks（registry 条目 hooks 字段**非空**列表 > 实体 manifest
    extends.hooks）。registry 条目 hooks 为空列表/缺失时 fallback 实体 manifest
    声明（P6 修复 A-P1-3：registry 被手改清空 hooks 会让插件静默废掉——键照放行、
    钩子永不跑、零报错；fallback 后 manifest 仍是声明面，与 _plugin_version 的
    fallback 语义对齐）。"""
    reg = _registry.declared_plugins(repo_root).get(name)
    if reg is not None and isinstance(reg.get("hooks"), list) and reg["hooks"]:
        return [h for h in reg["hooks"] if isinstance(h, str)]
    for e in _registry.discover(repo_root):
        if e["name"] == name and e["manifest"]:
            declared = (e["manifest"].get("extends", {}) or {}).get("hooks")
            if isinstance(declared, list):
                return [h for h in declared if isinstance(h, str)]
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


def _scope_items(tl, path, c=None):
    """键声明 path → timeline 中的检索容器（_scan_plugin_keys 四 scope 同构）。
    P6 修复 A-P1-2：v1 只认 sources[]/audio_tracks[] 两 path，声明在 root/project/
    events[] 的键「validate 放行但插件永不生效」+ 参数摘要盲区——现与内核扫描
    scope 全面对齐（root=[tl] 本体、project=tl.project、events[] 拍平全部事件）。"""
    if not isinstance(tl, dict):
        return []
    if path == "root":
        return [tl]
    if path == "project":
        return [tl["project"]] if isinstance(tl.get("project"), dict) else []
    if path == "sources[]":
        return tl.get("sources", [])
    if path == "audio_tracks[]":
        return tl.get("audio_tracks", [])
    if path == "audio_tracks[].events[]":
        return [e for ti, tr in indexed(c, tl.get("audio_tracks", []), '$.audio_tracks') if isinstance(tr, dict)
                for _, e in indexed(c, (tr.get("events") or []), f'$.audio_tracks[{ti}].events')]
    return []


def _timeline_declares(c, name: str, repo_root=None) -> bool:
    """timeline 是否声明了插件注册键中的任一键（「对本 timeline 生效」判定）。

    键位检索用全量键声明（registry 静态注册表 + external 实体 manifest，P4 起经
    registry.key_map 双源），path 同 _plugin_params_digest（_scope_items 四 scope，
    P6 对齐）。无法判定（c 无 tl / 插件无键声明）时保守返回 True——宁多生效
    不静默漏（fail-hard 可见性优先）。
    """
    tl = getattr(c, "tl", None) if c is not None else None
    if not isinstance(tl, dict):
        return True
    key_map = _registry.key_map(repo_root)
    has_keys = False
    for key, info in sorted(key_map.items()):
        if info.get("plugin") != name:
            continue
        has_keys = True
        for _, it in indexed(c, _scope_items(tl, info.get("path"), c),
                             '$.' + (info.get("path") or '').removesuffix('[]')):
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
        if d is None:
            raise RuntimeError(f"插件 {name} 缺少有效 manifest 导入锚，拒绝加载")
        manifest, errors = load_manifest(d)
        if errors:
            from vtx import __version__
            raise RuntimeError(f"插件 {name}: 检测内核版本 {__version__!r}; "
                               f"manifest 无效：{'; '.join(errors)}")
        require_compatible_core(name, manifest)
        require_compatible_protocol(name, manifest, _plugin_hooks_declared(name))
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


def _declared_hook(plugin, hook_name):
    """Resolve one declared callable without changing caller iteration or exits."""
    if hook_name not in _plugin_hooks_declared(plugin["name"]):
        return None
    mod = _load_plugin_module(plugin["name"])
    fn = getattr(mod, hook_name, None) if mod else None
    return fn if callable(fn) else None


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
        fn = _declared_hook(p, "prepare_source")
        if fn is not None:
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
        fn = _declared_hook(p, "audio_events")
        if fn is not None:
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
        fn = _declared_hook(p, "validate")
        if fn is not None:
            got = fn(c)
            if isinstance(got, (list, tuple)):
                # Structured string subclasses retain source evidence; third-party
                # opaque values remain strings and receive an honest unknown rule.
                errs.extend(x if isinstance(x, str) else str(x) for x in got)
    return errs


def run_probe_source(c, sid: str):
    """缝 C（协议 v1.2 增补，§2.2）：虚拟源 facts 钩子。

    逐个应用声明了 probe_source 的生效插件（约定：插件暴露
    ``probe_source(c, sid) -> dict | None``——None = 不受理该源，调用方回落
    ffprobe；受理返回与 Ctx.probe 同构的 facts dict）。stages.validate 的源级
    检查（has_audio/fps 契约）与音频事件时长校验（nb_frames）统一经本缝——
    sources[] 引用非媒体文件（music_tools 工程 JSON 等）时由插件供给 facts。
    无插件受理 → None（调用方照旧 ffprobe，v1 行为）。"""
    for p in active_plugins(c):
        fn = _declared_hook(p, "probe_source")
        if fn is not None:
            got = fn(c, sid)
            if isinstance(got, dict):
                return got
    return None


def run_finish(c) -> list:
    """缝 D（协议 v1.2 增补，§2.2）：管线收尾钩子——`vtx all` 全链（mux/qc 完成后）
    由 cli 触发（--draft / --dry-run 或停在 qc 之前的 --until 不触发；
    all --until qc 可收尾）。qc 失败不阻断本钩（产物已就绪），cli 按
    qc/finish 各自失败态汇总退出码。

    逐个应用声明了 finish 的生效插件（约定：插件暴露 ``finish(c) -> list[str]``
    ——错误行列表，空 = 成功）。cli 汇总错误行打印并以退出码 1 结束——正式
    产物（final.mp4）已就绪，收尾件失败不吞（如 blender-bridge 的 .blend
    自动导出失败须可见）。插件自行打印进度行。"""
    errs = []
    for p in active_plugins(c):
        fn = _declared_hook(p, "finish")
        if fn is not None:
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


# Algorithm namespace, independent of plugin/package release versions (#86).
_PLUGIN_CODE_DIGEST_VERSION = b"vtx-plugin-code-v2\0"


# 插件代码摘要的公开权威实现；内部参数摘要也直接复用，不设兼容别名。
def plugin_code_digest(name: str, repo_root=None) -> str:
    """插件代码内容摘要（红队终审 P1-1 裁定 2026-09-14；P6 公共化经 vtx.api 供
    插件产物键复用——A-P1-1：产物键侧同样要掺代码摘要，否则改代码不 bump
    version 时回执指纹翻转但旧产物被缓存命中「洗白」）：插件目录全部 .py 文件
    （rglob 递归含子目录/子包，排除 __pycache__ 与 **tests/ 子目录**——R1-F F5：
    改一行测试 ≠ 插件行为变化，掺入会使产物键/回执无谓翻转触发真工程整轮重渲；
    口径统一对全部插件生效，见 ARCHITECTURE §2.5 条 9；按插件根相对 POSIX
    路径排序，路径与内容以长度前缀分界，独立算法版本入摘要；迁移说明见
    docs/cache-provenance.md）的
    sha1 前 12 位——改默认参/改代码不 bump version 的场景一律翻转。模块加载
    成功后文件必在；读不到按 MISSING 计（fail-方向：摘要必变）。"""
    d, _origin = _registry.plugin_module_dir(name, repo_root)
    h = hashlib.sha1(_PLUGIN_CODE_DIGEST_VERSION)
    if d is None or not d.is_dir():
        h.update(b"NO-DIR\0")
        return h.hexdigest()[:12]
    h.update(b"DIR\0")
    for f in sorted(d.rglob("*.py"), key=lambda x: x.relative_to(d).as_posix()):
        relative = f.relative_to(d)
        if any(part in {"tests", "__pycache__"} for part in relative.parts):
            continue       # tests/ 不进代码摘要（R1-F F5，docstring 见口径）
        path_bytes = relative.as_posix().encode("utf-8")
        h.update(len(path_bytes).to_bytes(8, "big"))
        h.update(path_bytes)
        try:
            content = f.read_bytes()
        except OSError:
            h.update(b"MISSING\0")
        else:
            h.update(b"CONTENT\0")
            h.update(len(content).to_bytes(8, "big"))
            h.update(content)
    return h.hexdigest()[:12]


def _plugin_params_digest(c, name: str) -> str:
    """对本 timeline 生效的插件参数摘要 = 显式键值 + **插件代码内容摘要**：
    键声明（registry 静态注册表 + external manifest，P4 起经 registry.key_map
    双源）在 timeline 中的取值（收集全部实例——P1 审查 F2），叠加
    `plugin_code_digest`（红队终审 P1-1：官方插件改 DEFAULTS 不 bump version、
    无键 external 改代码两类盲区实测会使 digest 不变 → 旧回执放行陈旧产物，
    与 §2.1 宣称冲突——掺代码摘要后宣称成立）。"""
    picked = {}
    try:
        key_map = _registry.key_map()
        for key, info in sorted(key_map.items()):
            if info.get("plugin") != name:
                continue
            for it in _scope_items(getattr(c, "tl", None), info.get("path")):
                if isinstance(it, dict) and key in it:
                    # 收集全部实例（P1 对抗审查 F2 修正）：只取首例时，多实例
                    # 场景改第二例参数 digest 不变——旧回执放行陈旧产物。
                    picked.setdefault(f"{info.get('path')}:{key}", []).append(it[key])
    except Exception:
        pass   # 摘要失败不阻断指纹——退化为无参数成分（宁弱不崩）
    blob = json.dumps({"params": picked, "code": plugin_code_digest(name)},
                      ensure_ascii=False, sort_keys=True)
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


def get_cache_dir_keys(c=None, repo_root=None) -> dict:
    """不变量 ① 的键级扩展（v1.2.0，R1-F F2）：插件 cache 子目录的**内容寻址
    键级条目**白名单——插件暴露可选 ``cache_dir_keys(c) -> dict[str, set[str]]``
    （{目录名: 键集}，与 contribute_keys 同为不变量入口而非钩子，不参与对账）。

    声明了键集的 cache_dirs 目录，gc 把其一级子目录按键对待：在键集内保活，
    不在 = 陈旧可回收（gc_plan 的 plugin_cache 类别）——修复 bgm-render 共享
    渲染缓存陈旧 render_digest 目录（每个 ≈整工程渲染产物）无界累积。仅对
    同时经 registry cache_dirs 注册的目录生效（目录注册 + 键级条目两层声明的
    层叠语义）；未暴露本函数的插件 = 整目录白名单（原语义不变）。P1-2 恒空
    dict（空转）。"""
    out = {}
    for p in active_plugins(c, repo_root):
        mod = _load_plugin_module(p["name"])
        fn = getattr(mod, "cache_dir_keys", None) if mod else None
        if callable(fn):
            got = fn(c)
            if isinstance(got, dict):
                for dname, keys in got.items():
                    if not (isinstance(dname, str) and
                            isinstance(keys, (set, frozenset, list, tuple))):
                        continue
                    merged = out.setdefault(dname, set())
                    merged |= {str(k) for k in keys}
    return out
