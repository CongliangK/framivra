"""操作层编辑引擎（PLAN_v3 §3.4 机制 D / BG-D11 草案 / 判据②R3-W1）。

五动词口径（PLAN_v3 §3.0）：show 属检视层（core/show.py，R3-W2）；本模块 =
编辑动词 insert/set/move/delete + 复合命令 split/merge/undo。

target 语法（PLAN_v3 §3.4）：
    tracks                      （append 轨容器）
    tracks/<name>[/notes[/<i>] | /events[/<i>] | /clip | /params[/<key>]]
    automation[/<i>]  sections[/<i>]  meta[/<field>]  master 段子路径（qc 子键可寻）
索引语义 = show 规范排序视图序号：notes 按 (beat, pitch, 原序) 排序、events 按
midi_util.event_sort_key 排序（单一实现，禁自写，C3）。automation/sections 索引 =
原始数组下标。
**索引漂移纪律（C4/BG-D11）**：每次 edit 后索引会变——连续编辑必须每次 edit 后
重新 show 取索引（CLI 摘要与 help 均明示）。

语义细则（R3-P20）：
    insert 带索引 = 在该索引**之前**插入（splice）；无索引 = append。
    set --payload 对对象 target = 深度合并（未知键拒绝、报字段路径；整替换须
    delete+insert）；--window（beat:a-b | sec:a-b，sec 按 meta.bpm 换算落 beat）
    作为选择谓词（[a, b) 半开区间）。
    跨形式折叠：--copy-of 跨 notes↔events 载体先转换；有损语义（vel=0 源音符、
    交叠复音、控制事件转 notes、窗口截断致配对悬空）报字段路径拒绝。
    修饰器 --transpose（pitch 平移）→ --scale（beat/dur 或 t 时间轴缩放）→
    --quantize（beat/t 吸附），固定顺序；越界/非法值由再校验报字段路径。

undo 契约（C6 / R2-m5 / R2-m6）——栈格式契约（R3-P2，跨 worker 接口，
show diff 读 manifest 以此为准）：
    栈 = 工程旁 .bgm_undo/：
      - 快照目录 <seq>.<ts>/：本次编辑将改写/删除的文件的**原始字节副本**
        （存储名 = 相对路径 '/'→'__'；主文件 = 工程文件 basename）；
      - manifest.json = [{seq, desc, files: [{path, sha256}], ts}]
        path = 相对工程目录路径（POSIX 风格，主文件 = basename）；
        sha256 = **编辑后**内容哈希（undo 漂移检测基准）；
      - 上限 50 条 FIFO 淘汰（UNDO_LIMIT）。
    执行序：先全量写快照（成功才入栈=manifest 原子追加）→ 再逐文件 tmp+rename
    （两段式；ops 顺序=外部轨写（路径升序）→ 主文件 → 旧文件删除）；
    写失败（如只读目录）→ 从刚写入的快照自动恢复（仅逆回已完成操作）并弹出该条。
    undo 前置漂移检测：当前外部轨文件 hash 与栈顶 manifest 记录不一致 → 报错列
    漂移文件，须 --force。undo 恢复 = 快照文件写回 + 编辑期新建文件删除
    （两段式）；undo 不做 redo（未设反向条目）。
    BGM_CRASH_AFTER=N（env 门控，仅测试用例使用）：apply 阶段第 N 次文件操作
    （原子替换或删除）完成后 os._exit(137)；未设即零开销（一次 env 读取）。

rename 引用重写（C9）：轨改名重写四处引用——automation[].target_track、
sidechain.trigger_track、sidechain.targets、$ref 外部轨文件名（slug 派生）；
stems 目录旧 stem 文件属产物，不重命名——仅注记。实现两段式（全部先写 tmp →
全部 rename）。

管线：load_project → 校验 → 变换 → 再校验（同一校验器 load_project，经工程
目录内临时文件装载）→ 原子写（save_project 同族 tmp+rename）→ 结构化 diff
（stdout 摘要 + --json {changed: [{path, before, after}]}）。
CLI 与 core/edit.py 双入口同校验路径（BG-D11）；零新增依赖。
"""

import copy
import hashlib
import json
import math
import os
import shutil
import tempfile
import time

from ..engines.base import EngineError, get_descriptor
from ..engines.midi_util import NOTE_EVENT_TYPES, event_sort_key, round_half_up
from .project import (AUTOMATION_KEYS, EVENT_TYPES, META_KEYS,
                      PROJECTION_LOCKED_PARAMS, TRACK_KEYS, ProjectError,
                      _atomic_write_bytes, _serializable_project,
                      _serializable_track, _slugify, events_to_notes,
                      load_project, merge_layout, save_project, split_layout)


class EditError(Exception):
    """编辑操作失败。消息含字段路径或 target（与 ProjectError 同口径）。"""


UNDO_DIR = ".bgm_undo"
UNDO_MANIFEST = "manifest.json"
UNDO_LIMIT = 50

# 工程母带段名（BG-D7 数据节，非引擎分支——DoD3 grep 豁免注记见 PLAN_v3 §7-3：
# 引擎名白名单判据区分"引擎分发语境"与"工程 section 键"，后者合法）。
_MASTER_SEG = "master"

NOTE_KEYS = ("pitch", "beat", "dur", "vel")
_NOTE_FIELDS = set(NOTE_KEYS)
_CLIP_KEYS = {"start_beat", "end_beat"}
_SECTION_KEYS = {"name", "start_beat", "length_beat"}
_MASTER_KEYS = {"target_lufs", "true_peak_db", "chain", "ref", "lufs_tol", "qc"}
_MASTER_QC_KEYS = {"silent_window_max_s", "bpm_tol_pct", "key_top2",
                   "hf_ratio_max", "duration_min_s"}
# 事件逐类型字段闭集（镜像 project._EVENT_FIELDS；源头与校验口径在 project.py）
_EVENT_FIELDS = {
    "cc": {"ctrl", "value"},
    "pitch_bend": {"semitones"},
    "keyswitch": {"pitch"},
    "program_change": {"program", "bank"},
    "note_on": {"pitch", "vel"},
    "note_off": {"pitch", "vel"},
}
# 深度合并 schema 图（target 路径元组 → 允许键）；不在图内的嵌套 dict 视作
# 标量整体替换（如 track.params / track.notes / track.events）。
_MERGE_SCHEMAS = {
    ("track",): TRACK_KEYS,
    ("track", "clip"): _CLIP_KEYS,
    ("section",): _SECTION_KEYS,
    ("automation",): AUTOMATION_KEYS,
    ("meta",): META_KEYS,
    (_MASTER_SEG,): _MASTER_KEYS,
    (_MASTER_SEG, "qc"): _MASTER_QC_KEYS,
}


# ---------------------------------------------------------------- target 解析

def _parse_target(target):
    """target 字符串 → 地址元组。语法错误抛 EditError（含 target 原串）。

    地址形态：
        ("tracks",)                      轨容器（append）
        ("track", name)                  单轨对象
        ("notes", name, idx|None)        notes 容器 / 规范视图元素
        ("events", name, idx|None)       events 容器 / 规范视图元素
        ("clip", name)                   clip 对象
        ("params", name, key|None)       params 对象 / 单键
        ("automation", idx|None)         automation 条目（原始下标）
        ("sections", idx|None)           sections 条目（原始下标）
        ("meta", field|None)             meta 字段
        (_MASTER_SEG, subpath 元组)      工程母带段（qc 子键可寻）
    注：轨名不得含 "/"（target 语法以 "/" 分段）。
    """
    if not isinstance(target, str) or not target.strip():
        raise EditError("--target 必须是非空路径字符串")
    parts = [p for p in target.split("/") if p != ""]
    if not parts:
        raise EditError(f"--target 语法错误：{target!r}")
    head = parts[0]
    if head == "tracks":
        if len(parts) == 1:
            return ("tracks",)
        name = parts[1]
        if len(parts) == 2:
            return ("track", name)
        sub, rest = parts[2], parts[3:]
        if sub in ("notes", "events"):
            if not rest:
                return (sub, name, None)
            if len(rest) == 1:
                return (sub, name, _parse_index(rest[0], target))
            raise EditError(f"--target 语法错误：{target!r}（{sub} 至多一个索引）")
        if sub == "clip" and not rest:
            return ("clip", name)
        if sub == "params" and len(rest) <= 1:
            return ("params", name, rest[0] if rest else None)
        raise EditError(
            f"--target 语法错误：{target!r}"
            f"（tracks/<name> 下支持 notes/events[/<i>]|clip|params[/<key>]）")
    if head == "automation":
        if len(parts) == 1:
            return ("automation", None)
        if len(parts) == 2:
            return ("automation", _parse_index(parts[1], target))
        raise EditError(f"--target 语法错误：{target!r}（automation 至多一个索引）")
    if head == "sections":
        if len(parts) == 1:
            return ("sections", None)
        if len(parts) == 2:
            return ("sections", _parse_index(parts[1], target))
        raise EditError(f"--target 语法错误：{target!r}（sections 至多一个索引）")
    if head == "meta":
        if len(parts) == 1:
            return ("meta", None)
        if len(parts) == 2:
            return ("meta", parts[1])
        raise EditError(f"--target 语法错误：{target!r}（meta 至多一个字段名）")
    if head == _MASTER_SEG:
        return (_MASTER_SEG, tuple(parts[1:]))
    raise EditError(
        f"--target 语法错误：{target!r}（合法：tracks/<name>[/notes[/<i>]|"
        f"/events[/<i>]|/clip|/params[/<key>]]、automation[/<i>]、sections[/<i>]、"
        f"meta[/<field>]、master/…）")


def _parse_index(s, target):
    try:
        i = int(s)
    except ValueError:
        raise EditError(f"--target 索引必须是非负整数：{s!r}（{target}）") from None
    if i < 0:
        raise EditError(f"--target 索引必须是非负整数：{s!r}（{target}）")
    return i


def _check_idx(i, n, target):
    if i >= n:
        raise EditError(f"{target}：索引 {i} 越界（视图长度 {n}；"
                        f"append 请用无索引形式）")


def _find_track(proj, name):
    for i, t in enumerate(proj["tracks"]):
        if t.get("name") == name:
            return i, t
    names = sorted((t.get("name") for t in proj["tracks"]), key=str)
    raise EditError(f"tracks/{name}：轨不存在（现有：{names}）")


# ------------------------------------------------- 规范排序视图（show 同口径）

def _notes_view(track):
    """notes 规范排序视图 [(raw_idx, note)]：按 (beat, pitch, raw_idx)。"""
    return sorted(enumerate(track.get("notes") or []),
                  key=lambda p: (p[1][1], p[1][0], p[0]))


def _events_view(track):
    """events 规范排序视图 [(raw_idx, event)]：event_sort_key 单一实现（C3）。"""
    return sorted(enumerate(track.get("events") or []),
                  key=lambda p: event_sort_key(p[1], p[0]))


# ---------------------------------------------------------------- 窗口与修饰器

def _parse_window(window, bpm):
    """'beat:a-b'|'sec:a-b' → (a, b)（beat 口径；sec 按 meta.bpm 换算）。
    选择谓词 = [a, b) 半开区间。解析错误报 --window 原串（handler 手写报错）。"""
    if window is None:
        return (None, None)
    if not isinstance(window, str) or ":" not in window:
        raise EditError(f"--window 语法错误：{window!r}（须为 beat:a-b 或 sec:a-b）")
    kind, _, rng = window.partition(":")
    try:
        a_s, _, b_s = rng.partition("-")
        a, b = float(a_s), float(b_s)
    except ValueError:
        raise EditError(f"--window 语法错误：{window!r}（须为 beat:a-b 或 sec:a-b）"
                        ) from None
    if kind not in ("beat", "sec"):
        raise EditError(f"--window 语法错误：{window!r}（前缀须为 beat 或 sec）")
    if not (a >= 0 and b > a):
        raise EditError(f"--window 区间非法：{window!r}（须 0 ≤ a < b）")
    if kind == "sec":
        spb = 60.0 / float(bpm)
        a, b = a / spb, b / spb
    return (a, b)


def _in_window(el, key, lo, hi):
    return lo <= (el[1] if key == "notes" else el["t"]) < hi


def _parse_num(s, flag, positive=False):
    try:
        v = float(s)
    except (TypeError, ValueError):
        raise EditError(f"--{flag} 必须是数值：{s!r}") from None
    if not math.isfinite(v):
        raise EditError(f"--{flag} 必须是有限数值：{s!r}（NaN/Infinity 拒绝，R3 审查 M-2）")
    if positive and v <= 0:
        raise EditError(f"--{flag} 必须为正数：{s!r}")
    return v


def _apply_modifiers_notes(items, transpose, quantize, scale):
    """修饰器（notes 四元组）：transpose（pitch）→ scale（beat+dur）→
    quantize（beat 吸附，round-half-up——C3b 同口径，防 banker's rounding）。
    固定顺序。"""
    for n in items:
        if transpose is not None:
            n[0] = float(n[0]) + transpose
        if scale is not None:
            n[1] = float(n[1]) * scale
            n[2] = float(n[2]) * scale
        if quantize is not None:
            n[1] = round_half_up(n[1] / quantize) * quantize
    return items


def _apply_modifiers_events(items, transpose, quantize, scale):
    """修饰器（事件）：transpose 作用于 pitch 字段（keyswitch/note_on/note_off），
    scale/quantize 作用于 t（quantize=round-half-up）。固定顺序同 notes。"""
    for ev in items:
        if transpose is not None and "pitch" in ev:
            ev["pitch"] = float(ev["pitch"]) + transpose
        if scale is not None:
            ev["t"] = float(ev["t"]) * scale
        if quantize is not None:
            ev["t"] = round_half_up(ev["t"] / quantize) * quantize
    return items


# ---------------------------------------------------------------- 深度合并

def _deep_merge(target, patch, path, schema):
    """对象深度合并（R3-P20）：未知键拒绝报字段路径；嵌套对象按 schema 图
    递归，其余值整体替换。"""
    allowed = _MERGE_SCHEMAS[schema]
    for k, v in patch.items():
        if k not in allowed:
            raise EditError(f"{path}.{k}：未知字段（允许：{sorted(allowed)}）")
        nested = schema + (k,)
        if (isinstance(target.get(k), dict) and isinstance(v, dict)
                and nested in _MERGE_SCHEMAS):
            _deep_merge(target[k], v, f"{path}.{k}", nested)
        else:
            target[k] = copy.deepcopy(v)
    return target


def _merge_note(note, payload, path):
    """note 四元组字段合并；未知键拒绝。"""
    if payload is None:
        return
    if not isinstance(payload, dict):
        raise EditError(f"{path}：--payload 必须是 object（键 {list(NOTE_KEYS)}）")
    for k in payload:
        if k not in _NOTE_FIELDS:
            raise EditError(f"{path}.{k}：未知字段（note 允许 "
                            f"{sorted(_NOTE_FIELDS)}）")
    for k in NOTE_KEYS:
        if k in payload:
            note[NOTE_KEYS.index(k)] = copy.deepcopy(payload[k])


def _merge_event(ev, payload, path):
    """事件 dict 字段合并（按最终 type 的字段闭集拒绝未知键）。"""
    if payload is None:
        return
    if not isinstance(payload, dict):
        raise EditError(f"{path}：--payload 必须是 object")
    merged = dict(ev)
    merged.update(payload)
    typ = merged.get("type")
    if typ not in EVENT_TYPES:
        raise EditError(f"{path}.type：{typ!r} 不在事件类型闭集 {list(EVENT_TYPES)}"
                        f"（未知类型拒绝，C11）")
    allowed = {"type", "t"} | _EVENT_FIELDS[typ]
    for k in payload:
        if k not in allowed:
            raise EditError(f"{path}.{k}：未知字段（{typ} 事件允许 {sorted(allowed)}）")
    ev.update({k: copy.deepcopy(v) for k, v in payload.items()})


def _merge_params(track, patch, path):
    """track.params 浅合并：键 ∈ 该轨引擎描述符 parameters ids − 投影锁定集
    （与 project._validate_track_params 同口径）。"""
    if not isinstance(patch, dict):
        raise EditError(f"{path}：--payload 必须是 object")
    eng = track["engine"]
    try:
        ids = {pa["id"] for pa in (get_descriptor(eng).get("parameters") or [])}
    except EngineError as e:
        raise EditError(f"{path}：引擎描述符不可用（{e}）") from e
    allowed = ids - PROJECTION_LOCKED_PARAMS
    target = track.setdefault("params", {})
    for k, v in patch.items():
        if k not in allowed:
            raise EditError(f"{path}.{k}：未知参数（合法={sorted(allowed)}）")
        target[k] = copy.deepcopy(v)


# ------------------------------------------------------- copy-of 跨形式折叠

def _window_filter(items, carrier, lo, hi):
    return [it for it in items if _in_window(it, carrier, lo, hi)]


def _copy_source(proj, source, lo, hi):
    """--copy-of 源 → (items, carrier)；carrier ∈ {"notes", "events"}。
    源可为同轨/跨轨的 notes/events 载体（元素或容器）；--window 作源选择谓词。"""
    src = _parse_target(source)
    if src[0] == "track":
        _i, tr = _find_track(proj, src[1])
        notes = tr.get("notes") or []
        evs = tr.get("events") or []
        if notes:
            items, carrier = [list(n) for n in notes], "notes"
        elif evs:
            items, carrier = [dict(e) for e in evs], "events"
        else:
            raise EditError(f"--copy-of 源 {source}：轨 {src[1]!r} 无 notes/events"
                            f"（XOR 下必有其一）")
        if lo is not None:
            items = _window_filter(items, carrier, lo, hi)
        _check_copy_nonempty(items, source, lo, hi)
        return items, carrier
    if src[0] in ("notes", "events"):
        _i, tr = _find_track(proj, src[1])
        key = "notes" if src[0] == "notes" else "events"
        lst = tr.get(key) or []
        view = _notes_view(tr) if key == "notes" else _events_view(tr)
        if src[2] is not None:
            if lo is not None:
                raise EditError("--window 与索引源互斥（窗口仅用于容器源）")
            _check_idx(src[2], len(view), source)
            items = [copy.deepcopy(lst[view[src[2]][0]])]
        else:
            items = [copy.deepcopy(lst[raw]) for raw, _ in view]
            if lo is not None:
                items = _window_filter(items, src[0], lo, hi)
        _check_copy_nonempty(items, source, lo, hi)
        return items, src[0]
    raise EditError(f"--copy-of 源语法错误：{source!r}（仅 tracks/<n>、notes、"
                    f"events 载体）")


def _check_copy_nonempty(items, source, lo, hi):
    if not items:
        w = f"（--window {lo}-{hi}）" if lo is not None else ""
        raise EditError(f"--copy-of 源 {source} 无匹配元素{w}")


def _convert_carrier(items, carrier, dest, source):
    """跨形式载体转换（notes 四元组 ↔ note_on/off 事件对）；同载体原样返回。
    有损语义报字段路径拒绝（PLAN_v3 §3.4 跨形式折叠）。"""
    if carrier == dest:
        return items
    if carrier == "notes" and dest == "events":
        return _notes_to_note_events(items, source)
    if carrier == "events" and dest == "notes":
        return _events_to_note_rows(items, source)
    raise EditError(f"--copy-of 载体转换失败：{carrier} → {dest}")  # pragma: no cover


def _notes_to_note_events(items, source):
    """notes → note_on/note_off 对。有损前置检查（报源字段路径）：
    vel=0 源音符（note_on 合法 1..127）、同 pitch 交叠复音（事件流校验会拒）。"""
    order = sorted(enumerate(items), key=lambda p: (p[1][1], p[0]))
    active = {}
    for raw_i, n in order:
        pitch, beat, dur, vel = n
        q = f"{source}（源第 {raw_i + 1} 个元素）"
        if float(vel) == 0:
            raise EditError(f"{q}.vel：vel=0 无法转换为 note_on"
                            f"（合法 1..127，PLAN_v3 §3.2）")
        if pitch in active:
            pb, pd = active[pitch]
            if beat < pb + pd:
                raise EditError(
                    f"{q}：同 pitch {int(pitch)} 交叠（前一音符 beat {pb}+dur {pd}"
                    f" 未结束；note 事件复音校验会拒绝）")
        active[pitch] = (beat, dur)
    out = []
    for n in items:
        pitch, beat, dur, vel = n
        out.append({"type": "note_on", "t": float(beat), "pitch": float(pitch),
                    "vel": float(vel)})
        out.append({"type": "note_off", "t": float(beat) + float(dur),
                    "pitch": float(pitch)})
    return out


def _events_to_note_rows(items, source):
    """note 事件对 → notes 四元组（复用 project.events_to_notes 投影）。
    有损前置检查：控制事件存在、单事件、窗口截断致配对悬空 → 报源字段路径。"""
    if len(items) == 1:
        raise EditError(f"{source}：单个事件无法转换为 note"
                        f"（需 note_on/off 对；请用容器源整体转换）")
    controls = [e for e in items if e.get("type") not in NOTE_EVENT_TYPES]
    if controls:
        raise EditError(f"{source}：含控制事件（如 {controls[0]['type']}），"
                        f"无法无损转为 notes（控制事件无四元组载体）")
    pending = {}
    for j, e in enumerate(items):
        pitch = int(e.get("pitch"))
        if e.get("type") == "note_on":
            if pitch in pending:
                raise EditError(f"{source}：同 pitch {pitch} 交叠（note 事件复音"
                                f"校验会拒绝）")
            pending[pitch] = j
        elif pitch not in pending:
            raise EditError(f"{source}：无对应 note_on 的 note_off（悬空 off——"
                            f"窗口截断会破坏配对，请调整 --window）")
        else:
            del pending[pitch]
    if pending:
        raise EditError(f"{source}：悬空 note_on（窗口截断会破坏配对，"
                        f"请调整 --window）")
    try:
        return events_to_notes(items)
    except Exception as e:                                  # noqa: BLE001
        raise EditError(f"{source}：事件对投影失败（{e}）") from e


def _payload_items(payload, kind, path):
    """insert --payload → (items, carrier)（payload 恒与目标同载体，无需转换）。"""
    if kind == "notes":
        if (isinstance(payload, list) and len(payload) == 4
                and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                        for x in payload)):
            return [[float(x) for x in payload]], "notes"
        if (isinstance(payload, list)
                and all(isinstance(r, (list, tuple)) and len(r) == 4
                        for r in payload)):
            return [[float(x) for x in r] for r in payload], "notes"
        raise EditError(f"{path}：notes 插入须为 [pitch, beat, dur, vel]"
                        f" 或四元数组数组")
    if isinstance(payload, dict):
        return [copy.deepcopy(payload)], "events"
    if isinstance(payload, list) and all(isinstance(e, dict) for e in payload):
        return [copy.deepcopy(e) for e in payload], "events"
    raise EditError(f"{path}：events 插入须为事件 object 或事件数组")


def _unique_track_name(proj, base):
    """insert --copy-of 轨副本的名字唯一化（C7 折叠：duplicate 由 copy-of 承载）。"""
    names = {t.get("name") for t in proj["tracks"]}
    cand = f"{base} copy"
    i = 2
    while cand in names:
        cand = f"{base} copy {i}"
        i += 1
    return cand


# ---------------------------------------------------------------- 四编辑动词

def _insert(proj, addr, payload, copy_of, window, transpose, quantize, scale,
            meta):
    kind = addr[0]
    lo, hi = _parse_window(window, proj["meta"]["bpm"])
    if window is not None and copy_of is None:
        raise EditError("insert 的 --window 仅与 --copy-of 配合（源选择谓词）")
    if kind in ("track", "tracks"):
        if copy_of is not None:
            if payload is not None:
                raise EditError("--copy-of 与 --payload 互斥")
            src = _parse_target(copy_of)
            if src[0] != "track":
                raise EditError(f"--copy-of 源 {copy_of!r} 不是轨"
                                f"（轨插入仅接受轨源）")
            _i, tr = _find_track(proj, src[1])
            new = copy.deepcopy(tr)
            new["name"] = _unique_track_name(proj, tr.get("name"))
        elif payload is not None:
            if not isinstance(payload, dict):
                raise EditError(f"insert 轨 --payload 必须是完整轨 object："
                                f"{target_of(addr)!r}")
            new = copy.deepcopy(payload)
        else:
            raise EditError("insert 轨需要 --payload（完整轨对象）或 --copy-of")
        if kind == "tracks":
            proj["tracks"].append(new)
        else:
            pos = _find_track(proj, addr[1])[0]
            proj["tracks"].insert(pos, new)
        return
    if kind in ("notes", "events"):
        _i, tr = _find_track(proj, addr[1])
        key = "notes" if kind == "notes" else "events"
        if copy_of is not None:
            if payload is not None:
                raise EditError("--copy-of 与 --payload 互斥")
            items, carrier = _copy_source(proj, copy_of, lo, hi)
            if carrier != kind:
                items = _convert_carrier(items, carrier, kind, copy_of)
        else:
            if payload is None:
                raise EditError("insert 需要 --payload 或 --copy-of")
            items, carrier = _payload_items(payload, kind, target_of(addr))
            assert carrier == kind
        if kind == "notes":
            _apply_modifiers_notes(items, transpose, quantize, scale)
        else:
            _apply_modifiers_events(items, transpose, quantize, scale)
        view = _notes_view(tr) if key == "notes" else _events_view(tr)
        lst = tr.setdefault(key, [])
        if addr[2] is None:
            lst.extend(items)
        else:
            _check_idx(addr[2], len(view), target_of(addr))
            lst[view[addr[2]][0]:view[addr[2]][0]] = items
        return
    if kind in ("automation", "sections"):
        if copy_of is not None or window is not None:
            raise EditError(f"insert {kind} 不支持 --copy-of/--window")
        if payload is None or not isinstance(payload, dict):
            raise EditError(f"insert {kind} 需要 --payload（完整 object）")
        key = "automation" if kind == "automation" else "sections"
        item = copy.deepcopy(payload)
        if addr[1] is None:
            proj[key].append(item)
        else:
            _check_idx(addr[1], len(proj[key]), target_of(addr))
            proj[key].insert(addr[1], item)
        return
    raise EditError(f"insert 不支持 target：{target_of(addr)}"
                    f"（可插入：tracks、tracks/<name>、notes、events、"
                    f"automation、sections）")


def _set(proj, addr, payload, window, transpose, quantize, scale, meta):
    kind = addr[0]
    if payload is None and not any(
            x is not None for x in (transpose, quantize, scale)):
        raise EditError("set 需要 --payload 或 --transpose/--quantize/--scale")
    lo, hi = _parse_window(window, proj["meta"]["bpm"])
    if kind in ("notes", "events"):
        _i, tr = _find_track(proj, addr[1])
        key = "notes" if kind == "notes" else "events"
        view = _notes_view(tr) if key == "notes" else _events_view(tr)
        if addr[2] is not None:
            if window is not None:
                raise EditError("--window 仅适用于容器 target")
            _check_idx(addr[2], len(view), target_of(addr))
            raw = view[addr[2]][0]
            el = tr[key][raw]
            if key == "notes":
                _merge_note(el, payload, f"tracks/{addr[1]}/notes[{raw}]")
                _apply_modifiers_notes([el], transpose, quantize, scale)
            else:
                _merge_event(el, payload, f"tracks/{addr[1]}/events[{raw}]")
                _apply_modifiers_events([el], transpose, quantize, scale)
            return
        if payload is not None and lo is None:
            raise EditError("set 容器 target 需要 --window 选择谓词"
                            f"（整替换须 delete+insert；target={target_of(addr)}）")
        if lo is None:
            if not any(x is not None for x in (transpose, quantize, scale)):
                raise EditError("set 容器 target 需要 --window 或修饰器")
        for raw, el in view:
            if lo is not None and not _in_window(el, key, lo, hi):
                continue
            if key == "notes":
                _merge_note(el, payload, f"tracks/{addr[1]}/notes[{raw}]")
                _apply_modifiers_notes([el], transpose, quantize, scale)
            else:
                _merge_event(el, payload, f"tracks/{addr[1]}/events[{raw}]")
                _apply_modifiers_events([el], transpose, quantize, scale)
        return
    if kind == "track":
        if window is not None:
            raise EditError("--window 不适用于对象 target")
        if not isinstance(payload, dict):
            raise EditError(f"tracks/{addr[1]}：--payload 必须是 object（深度合并）")
        _i, tr = _find_track(proj, addr[1])
        old = tr.get("name")
        _deep_merge(tr, payload, f"tracks/{addr[1]}", ("track",))
        if payload.get("name") is not None and payload["name"] != old:
            meta.setdefault("renames", {})[old] = payload["name"]
        return
    if kind == "clip":
        if window is not None:
            raise EditError("--window 不适用于对象 target")
        if not isinstance(payload, dict):
            raise EditError(f"tracks/{addr[1]}/clip：--payload 必须是 object")
        _i, tr = _find_track(proj, addr[1])
        _deep_merge(tr["clip"], payload, f"tracks/{addr[1]}/clip",
                    ("track", "clip"))
        return
    if kind == "params":
        _i, tr = _find_track(proj, addr[1])
        if payload is None:
            raise EditError("set params 需要 --payload（对象深度合并或单键值）")
        if window is not None:
            raise EditError("--window 不适用于 params target")
        if addr[2] is None:
            _merge_params(tr, payload, f"tracks/{addr[1]}/params")
        else:
            tr.setdefault("params", {})[addr[2]] = copy.deepcopy(payload)
        return
    if kind == "automation":
        if addr[1] is None:
            raise EditError("set automation 需要索引 target（automation/<i>）")
        if not isinstance(payload, dict):
            raise EditError(f"automation[{addr[1]}]：--payload 必须是 object")
        _check_idx(addr[1], len(proj["automation"]), target_of(addr))
        _deep_merge(proj["automation"][addr[1]], payload,
                    f"automation[{addr[1]}]", ("automation",))
        return
    if kind == "sections":
        if addr[1] is None:
            raise EditError("set sections 需要索引 target（sections/<i>）")
        if not isinstance(payload, dict):
            raise EditError(f"sections[{addr[1]}]：--payload 必须是 object")
        _check_idx(addr[1], len(proj["sections"]), target_of(addr))
        _deep_merge(proj["sections"][addr[1]], payload, f"sections[{addr[1]}]",
                    ("section",))
        return
    if kind == "meta":
        if window is not None:
            raise EditError("--window 不适用于 meta target")
        if addr[1] is None:
            if not isinstance(payload, dict):
                raise EditError("meta：--payload 必须是 object")
            _deep_merge(proj["meta"], payload, "meta", ("meta",))
        else:
            field = addr[1]
            if field not in META_KEYS:
                raise EditError(f"meta/{field}：未知字段（允许 {sorted(META_KEYS)}）")
            proj["meta"][field] = copy.deepcopy(payload)
        return
    if kind == _MASTER_SEG:
        if window is not None:
            raise EditError("--window 不适用于 master 段 target")
        mst = proj.setdefault(_MASTER_SEG, {})
        seg = addr[1]
        if not seg:
            if not isinstance(payload, dict):
                raise EditError(f"{_MASTER_SEG}：--payload 必须是 object")
            _deep_merge(mst, payload, _MASTER_SEG, (_MASTER_SEG,))
        elif seg[0] == "qc":
            qc = mst.setdefault("qc", {})
            if len(seg) == 1:
                if not isinstance(payload, dict):
                    raise EditError(f"{_MASTER_SEG}/qc：--payload 必须是 object")
                _deep_merge(qc, payload, f"{_MASTER_SEG}/qc", (_MASTER_SEG, "qc"))
            else:
                key = seg[1]
                if key not in _MASTER_QC_KEYS:
                    raise EditError(f"{_MASTER_SEG}/qc/{key}：未知键"
                                    f"（允许 {sorted(_MASTER_QC_KEYS)}）")
                qc[key] = copy.deepcopy(payload)
        else:
            key = seg[0]
            if key not in _MASTER_KEYS:
                raise EditError(f"{_MASTER_SEG}/{key}：未知字段"
                                f"（允许 {sorted(_MASTER_KEYS)}）")
            mst[key] = copy.deepcopy(payload)
        return
    raise EditError(f"set 不支持 target：{target_of(addr)}")


def _move(proj, addr, window, dest, meta):
    if not dest:
        raise EditError("move 需要 --to <目标 target>")
    kind = addr[0]
    if kind == "track":
        if window is not None:
            raise EditError("--window 不适用于轨 move")
        src_i, _tr = _find_track(proj, addr[1])
        track = proj["tracks"].pop(src_i)
        d = _parse_target(dest)
        if d[0] == "tracks":
            proj["tracks"].append(track)
        elif d[0] == "track":
            dst_i = _find_track(proj, d[1])[0]
            proj["tracks"].insert(dst_i, track)
        else:
            raise EditError(f"move 轨目标必须是 tracks 或 tracks/<name>：{dest!r}")
        return
    if kind not in ("notes", "events"):
        raise EditError(f"move 不支持 target：{target_of(addr)}")
    _i, src_tr = _find_track(proj, addr[1])
    key = "notes" if kind == "notes" else "events"
    view = _notes_view(src_tr) if key == "notes" else _events_view(src_tr)
    lo, hi = _parse_window(window, proj["meta"]["bpm"])
    if addr[2] is not None:
        if window is not None:
            raise EditError("--window 仅适用于容器 target")
        _check_idx(addr[2], len(view), target_of(addr))
        items = [src_tr[key].pop(view[addr[2]][0])]
    else:
        if lo is None:
            raise EditError("move 容器 target 需要 --window")
        match = [raw for raw, el in view if _in_window(el, key, lo, hi)]
        if not match:
            raise EditError(f"窗口无匹配元素：{window}")
        items = [src_tr[key].pop(raw) for raw in sorted(match, reverse=True)]
        items.reverse()
    d = _parse_target(dest)
    if d[0] != kind:
        raise EditError(f"move 载体必须一致：源 {kind}、目标 {d[0]}"
                        f"（跨形式请 delete+insert --copy-of）")
    _j, dst_tr = _find_track(proj, d[1])
    if d[2] is None:
        dst_tr[key].extend(items)
    else:
        dst_view = _notes_view(dst_tr) if key == "notes" else _events_view(dst_tr)
        _check_idx(d[2], len(dst_view), dest)
        dst_tr[key][dst_view[d[2]][0]:dst_view[d[2]][0]] = items


def _delete(proj, addr, window, meta):
    kind = addr[0]
    lo, hi = _parse_window(window, proj["meta"]["bpm"])
    if kind == "track":
        if window is not None:
            raise EditError("--window 不适用于轨 delete")
        idx = _find_track(proj, addr[1])[0]
        del proj["tracks"][idx]
        return
    if kind in ("notes", "events"):
        _i, tr = _find_track(proj, addr[1])
        key = "notes" if kind == "notes" else "events"
        view = _notes_view(tr) if key == "notes" else _events_view(tr)
        if addr[2] is not None:
            if window is not None:
                raise EditError("--window 仅适用于容器 target")
            _check_idx(addr[2], len(view), target_of(addr))
            del tr[key][view[addr[2]][0]]
            return
        if lo is None:
            raise EditError("delete 容器 target 需要 --window")
        match = [raw for raw, el in view if _in_window(el, key, lo, hi)]
        if not match:
            raise EditError(f"窗口无匹配元素：{window}")
        for raw in sorted(match, reverse=True):
            del tr[key][raw]
        return
    if kind == "clip":
        _i, tr = _find_track(proj, addr[1])
        tr.pop("clip", None)          # 显式 clip 移除 → 装载层按 notes 推导
        return
    if kind == "params":
        _i, tr = _find_track(proj, addr[1])
        key = addr[2]
        if key is None:
            raise EditError("delete params 需要指定键：tracks/<name>/params/<key>")
        if not tr.get("params") or key not in tr["params"]:
            raise EditError(f"tracks/{addr[1]}/params/{key}：键不存在")
        del tr["params"][key]
        return
    if kind == "automation":
        if addr[1] is None:
            raise EditError("delete automation 需要索引")
        _check_idx(addr[1], len(proj["automation"]), target_of(addr))
        del proj["automation"][addr[1]]
        return
    if kind == "sections":
        if addr[1] is None:
            raise EditError("delete sections 需要索引")
        _check_idx(addr[1], len(proj["sections"]), target_of(addr))
        del proj["sections"][addr[1]]
        return
    if kind == "meta":
        field = addr[1]
        if field is None:
            raise EditError("delete meta 需要字段名：meta/<field>")
        if field not in proj["meta"]:
            raise EditError(f"meta/{field}：字段不存在")
        del proj["meta"][field]
        return
    if kind == _MASTER_SEG:
        seg = addr[1]
        if not seg:
            raise EditError(f"delete {_MASTER_SEG} 需要子路径"
                            f"（如 {_MASTER_SEG}/qc/<key>）")
        mst = proj.setdefault(_MASTER_SEG, {})
        if seg[0] == "qc":
            if len(seg) != 2:
                raise EditError(f"delete {_MASTER_SEG}/qc 需要键")
            key = seg[1]
            qc = mst.get("qc") or {}
            if key not in qc:
                raise EditError(f"{_MASTER_SEG}/qc/{key}：键不存在")
            del qc[key]
        else:
            key = seg[0]
            if key not in mst:
                raise EditError(f"{_MASTER_SEG}/{key}：字段不存在")
            del mst[key]
        return
    raise EditError(f"delete 不支持 target：{target_of(addr)}")


def target_of(addr):
    """地址元组 → 可读 target 串（报错回显用）。"""
    kind = addr[0]
    if kind == "tracks":
        return "tracks"
    if kind == "track":
        return f"tracks/{addr[1]}"
    if kind in ("notes", "events"):
        return (f"tracks/{addr[1]}/{kind}"
                + ("" if addr[2] is None else f"/{addr[2]}"))
    if kind == "clip":
        return f"tracks/{addr[1]}/clip"
    if kind == "params":
        return f"tracks/{addr[1]}/params" + ("" if addr[2] is None
                                             else f"/{addr[2]}")
    if kind == "automation":
        return "automation" + ("" if addr[1] is None else f"/{addr[1]}")
    if kind == "sections":
        return "sections" + ("" if addr[1] is None else f"/{addr[1]}")
    if kind == "meta":
        return "meta" + ("" if addr[1] is None else f"/{addr[1]}")
    return _MASTER_SEG + ("".join(f"/{s}" for s in addr[1]))


# ------------------------------------------------------- rename 引用重写（C9）

def _rewrite_renames(proj, meta):
    """轨改名引用重写四处：automation[].target_track、sidechain.trigger_track、
    sidechain.targets、$ref 外部轨文件名（slug 派生，两段式由写盘层保证）。
    stems 目录旧 stem 文件属产物不重命名——由调用方注记。
    返回注记字符串列表。"""
    renames = meta.get("renames") or {}
    if not renames:
        return []
    notes = []
    for a in proj.get("automation") or []:
        if a.get("target_track") in renames:
            a["target_track"] = renames[a["target_track"]]
    sc = proj.get("sidechain")
    if sc:
        if sc.get("trigger_track") in renames:
            sc["trigger_track"] = renames[sc["trigger_track"]]
        if isinstance(sc.get("targets"), list):
            sc["targets"] = [renames.get(t, t) for t in sc["targets"]]
    for old, new in renames.items():
        for t in proj.get("tracks") or []:
            if t.get("name") != new:
                continue
            ref = t.get("_ref_src")
            if not ref:
                continue
            parent = ref.rsplit("/", 1)[0] if "/" in ref else ""
            new_rel = (parent + "/" if parent else "") + _slugify(new) + ".json"
            if new_rel == ref:
                continue
            others = {o.get("_ref_src") for o in proj["tracks"]
                      if o is not t and o.get("_ref_src")}
            if new_rel in others:
                raise EditError(f"tracks/{old}：改名后外部文件名 {new_rel} 与其它轨"
                                f"冲突（外部轨文件名必须唯一；请先处置占用轨）")
            t["_ref_src"] = new_rel
            meta.setdefault("ref_renames", {})[ref] = new_rel
        notes.append(f"轨 {old!r} → {new!r}（automation/sidechain 引用已重写）")
    return notes


# ------------------------------------------------------- 校验 / 布局 / diff

def _revalidate(proj, proj_path):
    """再校验（同一校验器 load_project，PLAN_v3 §3.4 管线）。

    $ref 布局工程按**镜像布局**再校验：工程目录内建临时目录，外部轨写编辑后
    序列化内容、主文件 $ref 改指镜像文件——错误路径的来源标注得以保留，
    消息中镜像路径回填为原始相对路径（test_edit 的 $ref 来源标注口径）。
    内联工程 = 工程目录内临时单文件装载。错误消息把临时路径还原为工程路径。"""
    proj_dir = os.path.dirname(proj_path)
    tmp_dir = None
    tmp_main = None
    backmap = {}                        # 镜像相对路径 → 原始 rel（消息回填）
    try:
        work = _serialize_main(proj)
        ext_tracks = [t for t in proj.get("tracks") or [] if t.get("_ref_src")]
        if ext_tracks and any(isinstance(s, dict) and set(s) == {"$ref"}
                              for s in work.get("tracks", [])):
            tmp_dir = tempfile.mkdtemp(prefix=".bgm_val_", dir=proj_dir)
            tmp_name = os.path.basename(tmp_dir)
            new_tracks = []
            for slot, t in zip(work["tracks"], proj.get("tracks") or []):
                if isinstance(slot, dict) and set(slot) == {"$ref"} and t.get("_ref_src"):
                    rel = t["_ref_src"]
                    mirror = f"{tmp_name}/{_snap_name(rel)}"
                    _atomic_write_bytes(os.path.join(proj_dir, mirror),
                                        _json_bytes(_serializable_track(t)))
                    backmap[mirror] = rel
                    new_tracks.append({"$ref": mirror})
                else:
                    new_tracks.append(slot)
            work = dict(work)
            work["tracks"] = new_tracks
        fd, tmp_main = tempfile.mkstemp(prefix=".bgm_val_main_", suffix=".json",
                                        dir=proj_dir)
        with os.fdopen(fd, "wb") as f:
            f.write(_json_bytes(work))
        load_project(tmp_main)
    except ProjectError as e:
        msg = str(e).replace(tmp_main, proj_path, 1)
        for mirror, rel in backmap.items():
            msg = msg.replace(mirror, rel)
        raise EditError(msg) from e
    finally:
        for p in (tmp_main, tmp_dir):
            if p is None:
                continue
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    os.unlink(p)
            except OSError:
                pass


def _serialize_main(proj):
    """主文件序列化：外部轨还原为 $ref 槽位（布局保全），内联轨原样。
    全程深拷贝——before/after 状态不得与活工程共享可变子对象。"""
    tracks = []
    for t in proj.get("tracks") or []:
        if t.get("_ref_src"):
            tracks.append({"$ref": t["_ref_src"]})
        else:
            tracks.append(copy.deepcopy(t))
    work = dict(proj)
    work["tracks"] = tracks
    return _serializable_project(work)


def _layout_state(proj, proj_path):
    """工程 → 规范化布局形态（变更检测/结构化 diff 用；字节口径与
    _atomic_write_json 一致）：{main_rel, main: dict, ext: {rel: track dict}}。"""
    return {
        "main_rel": os.path.basename(proj_path),
        "main": _serialize_main(proj),
        "ext": {t["_ref_src"]: copy.deepcopy(_serializable_track(t))
                for t in proj.get("tracks") or [] if t.get("_ref_src")},
    }


def _diff_dicts(a, b, prefix=""):
    """结构化 diff：[{path, before, after}]。dict 按键递归；list 逐位（长度差
    的尾部以 before/after=None 表达；中段插入表现为位置级差异）。"""
    out = []
    if type(a) is not type(b) or not isinstance(a, (dict, list)):
        if a != b:
            out.append({"path": prefix.rstrip("."), "before": a, "after": b})
        return out
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b), key=str):
            p = prefix + str(k)
            if k not in a:
                out.append({"path": p, "before": None, "after": b[k]})
            elif k not in b:
                out.append({"path": p, "before": a[k], "after": None})
            else:
                out += _diff_dicts(a[k], b[k], p + ".")
    else:
        for i in range(max(len(a), len(b))):
            p = prefix.rstrip(".") + f"[{i}]"
            if i >= len(a):
                out.append({"path": p, "before": None, "after": b[i]})
            elif i >= len(b):
                out.append({"path": p, "before": a[i], "after": None})
            else:
                out += _diff_dicts(a[i], b[i], p + ".")
    return out


def _diff_layout(before, after):
    """布局状态 diff：主文件裸路径 + 外部轨文件以 '<rel>:' 前缀标注。"""
    changed = _diff_dicts(before["main"], after["main"], prefix="")
    for rel in sorted(set(before["ext"]) | set(after["ext"])):
        b, a = before["ext"].get(rel), after["ext"].get(rel)
        if rel in before["ext"] and rel in after["ext"]:
            changed += _diff_dicts(b, a, prefix=f"{rel}:")
        elif rel in after["ext"]:
            changed.append({"path": rel, "before": None, "after": a})
        else:
            changed.append({"path": rel, "before": b, "after": None})
    return changed


def _json_bytes(obj):
    return (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _sha256(blob):
    return hashlib.sha256(blob).hexdigest()


# ------------------------------------------------------- 写盘 / 快照 / undo 栈

def _snap_name(rel):
    return rel.replace("/", "__")


def _read_manifest(proj_dir):
    path = os.path.join(proj_dir, UNDO_DIR, UNDO_MANIFEST)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as e:
        raise EditError(f"{path}：manifest 读取失败（{e}）——栈已损坏，请人工处置"
                        ) from e
    if not isinstance(data, list):
        raise EditError(f"{path}：manifest 必须是数组")
    return data


def _write_manifest(proj_dir, entries):
    _atomic_write_bytes(os.path.join(proj_dir, UNDO_DIR, UNDO_MANIFEST),
                        _json_bytes(entries))


def _push_entry(proj_dir, main_rel, snap_files, written, desc):
    """先全量写快照（成功才入栈）：写 <seq>.<ts>/ 副本 → manifest 原子追加 →
    上限 50 FIFO 淘汰（R2-m6 执行序：快照先行，apply 在后）。"""
    undo_dir = os.path.join(proj_dir, UNDO_DIR)
    os.makedirs(undo_dir, exist_ok=True)
    entries = _read_manifest(proj_dir)
    seq = max((int(e.get("seq", 0)) for e in entries), default=0) + 1
    ts = int(time.time())
    snap_dir = os.path.join(undo_dir, f"{seq}.{ts}")
    os.makedirs(snap_dir, exist_ok=True)
    for rel, blob in sorted(snap_files.items()):
        with open(os.path.join(snap_dir, _snap_name(rel)), "wb") as f:
            f.write(blob)
    entry = {"seq": seq, "desc": desc, "ts": ts,
             "files": [{"path": rel, "sha256": _sha256(blob)}
                       for rel, blob in sorted(written.items())]}
    entries.append(entry)
    while len(entries) > UNDO_LIMIT:
        old = entries.pop(0)
        shutil.rmtree(os.path.join(undo_dir, f"{old['seq']}.{old['ts']}"),
                      ignore_errors=True)
    _write_manifest(proj_dir, entries)
    return entry


def _pop_entry(proj_dir, entry):
    undo_dir = os.path.join(proj_dir, UNDO_DIR)
    entries = [e for e in _read_manifest(proj_dir) if e.get("seq") != entry["seq"]]
    _write_manifest(proj_dir, entries)
    shutil.rmtree(os.path.join(undo_dir, f"{entry['seq']}.{entry['ts']}"),
                  ignore_errors=True)


def _apply_op(op, rel, payload, proj_dir):
    """单文件操作：write = 同目录 tmp + os.replace（两段式）；unlink = 删除。

    unlink 对已消失文件视为成功（幂等，R3 审查 m-1：undo 末位 op 崩溃重试时
    旧文件可能已被前次执行删除，FileNotFoundError 逃逸会令栈顶永久卡死）。"""
    path = os.path.join(proj_dir, rel)
    if op == "write":
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _atomic_write_bytes(path, payload)
    else:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def _read_crash_env():
    """BGM_CRASH_AFTER=N（env 门控，仅测试）：apply 阶段第 N 次文件操作后
    os._exit(137)。未设/非法 → None（生产路径零开销：仅此一次 env 读取）。"""
    v = os.environ.get("BGM_CRASH_AFTER")
    if v is None:
        return None
    try:
        n = int(v)
    except ValueError:
        return None
    return n if n >= 1 else None


def _crash_hook(n, count):
    if n is not None and count >= n:
        os._exit(137)


def _rollback(proj_dir, entry, done, snap_files):
    """写失败自动恢复：仅逆回已完成的操作（两段式写回）；新建文件删除、
    被删文件恢复。"""
    for op, rel in reversed(done):
        path = os.path.join(proj_dir, rel)
        if op == "write":
            if rel in snap_files:
                _atomic_write_bytes(path, snap_files[rel])
            else:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
        else:
            _atomic_write_bytes(path, snap_files[rel])


def _commit(proj_path, before_state, after_state, desc, meta):
    """快照入栈 → 两段式 apply（含 BGM_CRASH_AFTER 钩子）→ 失败自动回滚。
    返回 manifest 条目；无写盘操作返回 None。"""
    proj_dir = os.path.dirname(proj_path)
    main_rel = before_state["main_rel"]
    before_main = _json_bytes(before_state["main"])
    after_main = _json_bytes(after_state["main"])
    ext_before = {rel: _json_bytes(v) for rel, v in before_state["ext"].items()}
    ext_after = {rel: _json_bytes(v) for rel, v in after_state["ext"].items()}
    ref_renames = meta.get("ref_renames") or {}

    # 写盘计划（顺序：外部轨写（路径升序）→ 主文件 → 旧文件删除；
    # 该顺序是 BGM_CRASH_AFTER=N 原子写窗口测试的契约）
    ops = []                                   # (op, rel, payload)
    for rel in sorted(ext_after):
        if rel not in ext_before or ext_before[rel] != ext_after[rel]:
            ops.append(("write", rel, ext_after[rel]))
    if before_main != after_main:
        ops.append(("write", main_rel, after_main))
    for rel in sorted(ext_before):
        if rel not in ext_after and rel in ref_renames:
            ops.append(("unlink", rel, None))
    if not ops:
        return None

    # 快照：将被改写/删除文件的原始字节副本（新建文件无副本）
    snap_files = {}
    for op, rel, _p in ops:
        blob = _read_bytes(os.path.join(proj_dir, rel))
        if op == "unlink":
            if blob is None:
                raise EditError(f"快照失败：{rel} 不存在（不能删除不存在的文件）")
            snap_files[rel] = blob
        elif blob is not None:
            snap_files[rel] = blob
    written = {rel: payload for op, rel, payload in ops if op == "write"}
    try:
        entry = _push_entry(proj_dir, main_rel, snap_files, written, desc)
    except OSError as e:
        raise EditError(f"undo 快照写入失败（{UNDO_DIR}）：{e}；"
                        f"工程未做任何修改") from e

    crash_after = _read_crash_env()
    done = []
    try:
        for op, rel, payload in ops:
            _apply_op(op, rel, payload, proj_dir)
            done.append((op, rel))
            _crash_hook(crash_after, len(done))
    except Exception as e:                       # noqa: BLE001 — 逆回后再报
        # R3 审查 M-1：回滚成功消息必须在本 try 之外 raise——此前成功 raise 被
        # except e2 误捕，重包装成"自动回滚失败+建议 undo --force"，误导用户
        # 误撤上一条好编辑（且无 redo）。成功/失败两分支分开判定：
        rollback_err = None
        try:
            _rollback(proj_dir, entry, done, snap_files)
            _pop_entry(proj_dir, entry)
        except Exception as e2:                  # noqa: BLE001
            rollback_err = e2
        if rollback_err is None:
            raise EditError(f"编辑写入失败（{rel}）：{e}；"
                            f"已从快照 #{entry['seq']} 自动回滚（工程已复原）") from e
        raise EditError(
            f"编辑写入失败：{e}；自动回滚失败（{rollback_err}）——"
            f"快照 #{entry['seq']} 保留，请修复后 bgm edit undo --force"
        ) from rollback_err
    return entry


# ---------------------------------------------------------------- 公共 API

def _default_desc(verb, target, window, meta):
    parts = [verb, target or ""]
    if window:
        parts.append(f"--window {window}")
    for old, new in (meta.get("renames") or {}).items():
        parts.append(f"（改名 {old!r}→{new!r}）")
    return " ".join(p for p in parts if p)


def edit_project(project_path, verb, *, target=None, payload=None, window=None,
                 copy_of=None, transpose=None, quantize=None, scale=None,
                 dest=None, desc=None):
    """编辑动词入口（CLI 与核心双入口同校验路径；BG-D11）。

    管线：load_project → 校验 → 变换 → rename 引用重写 → 再校验（同一校验器）
    → 原子写（快照入栈 + 两段式）→ 结构化 diff。
    返回 {verb, target, changed: [{path, before, after}], undo: {seq, desc, dir}
    | None, notes}。
    """
    if verb not in ("insert", "set", "move", "delete"):
        raise EditError(f"未知编辑动词：{verb!r}")
    if verb != "move" and dest is not None:
        raise EditError("--to 仅用于 move")
    if target is None:
        raise EditError("缺少 --target")
    # 修饰器数值归一（CLI 传字符串、核心 API 传数值；handwritten 报错不用
    # argparse type——stderr 含 flag 名口径，PLAN_v3 §3.4）
    transpose = None if transpose is None else _parse_num(transpose, "transpose")
    quantize = None if quantize is None else _parse_num(quantize, "quantize",
                                                        positive=True)
    scale = None if scale is None else _parse_num(scale, "scale", positive=True)
    proj_path = os.path.abspath(project_path)
    proj = load_project(proj_path)
    before_state = _layout_state(proj, proj_path)
    meta = {}
    addr = _parse_target(target)
    if verb == "insert":
        _insert(proj, addr, payload, copy_of, window, transpose, quantize,
                scale, meta)
    elif verb == "set":
        _set(proj, addr, payload, window, transpose, quantize, scale, meta)
    elif verb == "move":
        _move(proj, addr, window, dest, meta)
    else:
        _delete(proj, addr, window, meta)

    rename_notes = _rewrite_renames(proj, meta)
    _revalidate(proj, proj_path)

    after_state = _layout_state(proj, proj_path)
    changed = _diff_layout(before_state, after_state)
    if not changed:
        return {"verb": verb, "target": target, "changed": [], "undo": None,
                "notes": ["无变更（未写盘、未入栈）"]}
    entry = _commit(proj_path, before_state, after_state,
                    desc or _default_desc(verb, target, window, meta), meta)
    result = {"verb": verb, "target": target, "changed": changed,
              "undo": {"seq": entry["seq"],
                       "desc": desc or _default_desc(verb, target, window, meta),
                       "dir": UNDO_DIR}}
    notes = list(rename_notes)
    orphaned = sorted(set(before_state["ext"]) - set(after_state["ext"])
                      - set(meta.get("ref_renames") or {}))
    for rel in orphaned:
        notes.append(f"外部轨文件 {rel} 已无引用（保留在盘未删除，红线④；"
                     f"undo 恢复引用后仍可用）")
    if meta.get("renames"):
        notes.append("轨改名不重命名已渲染 stem 产物（旧 stem 文件保留在 "
                     "out/<工程>/stems/ 下，属产物不删改）")
    if verb in ("insert", "move", "delete"):
        notes.append("索引已变化——连续编辑必须每次 edit 后重新 show 取索引"
                     "（C4/BG-D11）")
    if notes:
        result["notes"] = notes
    return result


def edit_split(project_path, tracks_dir="tracks"):
    """split 复合命令：薄包装 split_layout + save_project + undo 入栈。"""
    proj_path = os.path.abspath(project_path)
    proj_dir = os.path.dirname(proj_path)
    main_rel = os.path.basename(proj_path)
    before_main = _read_bytes(proj_path)
    if before_main is None:
        raise EditError(f"{project_path}：工程文件不存在")
    try:
        before_slots = {t["$ref"] for t in json.loads(before_main)["tracks"]
                        if isinstance(t, dict) and set(t) == {"$ref"}}
    except (json.JSONDecodeError, KeyError, TypeError):
        before_slots = set()
    proj = load_project(proj_path)
    desc = f"split（内联 → $ref 双布局，tracks_dir={tracks_dir}）"
    try:
        split_layout(proj, proj_path, tracks_dir)
        save_project(proj_path, proj)
    except Exception as e:                       # noqa: BLE001
        _atomic_write_bytes(proj_path, before_main)   # 主文件回滚
        raise EditError(f"split 失败：{e}；主文件已回滚"
                        f"（本次已创建的外部轨文件保留）") from e
    after_main = _read_bytes(proj_path)
    if after_main == before_main:
        return {"verb": "split", "target": project_path, "changed": [],
                "undo": None, "notes": ["无变更（已是 $ref 布局或无可拆分轨）"]}
    refs = [t["$ref"] for t in proj["tracks"]
            if isinstance(t, dict) and set(t) == {"$ref"}]
    created = [r for r in refs if r not in before_slots]
    written = {main_rel: after_main}
    for ref in created:
        blob = _read_bytes(os.path.join(proj_dir, ref))
        if blob is not None:
            written[ref] = blob
    entry = _push_entry(proj_dir, main_rel, {main_rel: before_main}, written,
                        desc)
    changed = _diff_dicts(json.loads(before_main), json.loads(after_main))
    for ref in created:
        changed.append({"path": ref, "before": None,
                        "after": _read_bytes(os.path.join(proj_dir, ref))})
    return {"verb": "split", "target": project_path, "changed": changed,
            "undo": {"seq": entry["seq"], "desc": desc, "dir": UNDO_DIR}}


def edit_merge(project_path):
    """merge 复合命令：薄包装 merge_layout + save_project + undo 入栈
    （外部轨文件不删改——undo 恢复主文件后它们重新被引用）。"""
    proj_path = os.path.abspath(project_path)
    proj_dir = os.path.dirname(proj_path)
    main_rel = os.path.basename(proj_path)
    before_main = _read_bytes(proj_path)
    if before_main is None:
        raise EditError(f"{project_path}：工程文件不存在")
    proj = load_project(proj_path)
    desc = "merge（$ref → 内联展开）"
    merge_layout(proj)
    save_project(proj_path, proj)
    after_main = _read_bytes(proj_path)
    if after_main == before_main:
        return {"verb": "merge", "target": project_path, "changed": [],
                "undo": None, "notes": ["无变更（已是内联布局）"]}
    entry = _push_entry(proj_dir, main_rel, {main_rel: before_main},
                        {main_rel: after_main}, desc)
    changed = _diff_dicts(json.loads(before_main), json.loads(after_main))
    return {"verb": "merge", "target": project_path, "changed": changed,
            "undo": {"seq": entry["seq"], "desc": desc, "dir": UNDO_DIR}}


def _undo_diff(proj_dir, main_rel, ops, snap_files):
    changed = []
    for op, rel, _p in ops:
        ap = os.path.join(proj_dir, rel)
        if op == "write":
            before = _json_or_none(_read_bytes(ap))
            after = _json_or_none(snap_files[rel])
        else:
            before = _json_or_none(_read_bytes(ap))
            after = None
        if before is None or after is None:
            changed.append({"path": "" if rel == main_rel else rel,
                            "before": before, "after": after})
        else:
            prefix = "" if rel == main_rel else f"{rel}:"
            changed += _diff_dicts(before, after, prefix=prefix)
    return changed


def _json_or_none(blob):
    if blob is None:
        return None
    try:
        return json.loads(blob)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def undo_edit(project_path, force=False):
    """撤销栈顶编辑（C6）：漂移检测 → 快照写回 + 编辑期新建文件删除（两段式）
    → 弹出条目。返回 {undone: {seq, desc, ts}, changed, remaining}。"""
    proj_path = os.path.abspath(project_path)
    proj_dir = os.path.dirname(proj_path)
    main_rel = os.path.basename(proj_path)
    entries = _read_manifest(proj_dir)
    if not entries:
        raise EditError(f"undo 栈为空（{os.path.join(proj_dir, UNDO_DIR)}）")
    top = entries[-1]
    snap_dir = os.path.join(proj_dir, UNDO_DIR, f"{top['seq']}.{top['ts']}")
    if not os.path.isdir(snap_dir):
        raise EditError(f"快照目录缺失：{snap_dir}（栈已损坏，请人工处置）")
    drift = []
    for f in top.get("files") or []:
        rel = f.get("path")
        if rel == main_rel or not rel:
            continue
        ap = os.path.join(proj_dir, rel)
        blob = _read_bytes(ap)
        if blob is None:
            drift.append(f"{rel}（文件缺失）")
        elif _sha256(blob) != f.get("sha256"):
            drift.append(f"{rel}（hash 不符）")
    if drift and not force:
        raise EditError(
            "undo 前置漂移检测：外部轨文件与栈顶快照记录不一致：\n  "
            + "\n  ".join(drift)
            + "\n如确认覆盖外部手工修改，请加 --force")
    snap_files = {}
    for fn in sorted(os.listdir(snap_dir)):
        rel = fn.replace("__", "/")
        blob = _read_bytes(os.path.join(snap_dir, fn))
        if blob is not None:
            snap_files[rel] = blob
    written = {f["path"] for f in top.get("files") or [] if f.get("path")}
    ops = [("write", rel, blob) for rel, blob in sorted(snap_files.items())]
    ops += [("unlink", rel, None) for rel in sorted(written - set(snap_files))]
    changed = _undo_diff(proj_dir, main_rel, ops, snap_files)
    crash_after = _read_crash_env()
    done = []
    try:
        for op, rel, payload in ops:
            _apply_op(op, rel, payload, proj_dir)
            done.append((op, rel))
            _crash_hook(crash_after, len(done))
    except OSError as e:
        raise EditError(f"undo 恢复写入失败：{e}"
                        f"（快照 #{top['seq']} 保留，栈未弹出，可重试）") from e
    _pop_entry(proj_dir, top)
    return {"undone": {"seq": top["seq"], "desc": top.get("desc", ""),
                       "ts": top.get("ts")},
            "changed": changed, "remaining": len(_read_manifest(proj_dir))}


def list_undo(project_path):
    """列出快照栈（manifest；R3-P2 栈格式契约，show diff 同源读取）。"""
    proj_dir = os.path.dirname(os.path.abspath(project_path))
    return {"entries": _read_manifest(proj_dir),
            "dir": os.path.join(proj_dir, UNDO_DIR)}


# ---------------------------------------------------------------- 呈现（CLI 共用）

def _clip(v):
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= 120 else s[:117] + "…"


def format_result(res):
    out = []
    if res.get("undone"):
        u = res["undone"]
        out.append(f"已撤销 #{u['seq']}：{u['desc']}（栈剩余 {res['remaining']}）")
    elif res.get("undo"):
        u = res["undo"]
        out.append(f"{res['verb']} {res.get('target') or ''} 完成："
                   f"{len(res['changed'])} 处变更（undo #{u['seq']} 已入栈 "
                   f"{u['dir']}/）".replace("  ", " "))
    else:
        out.append(f"{res['verb']} {res.get('target') or ''} 完成："
                   f"{len(res['changed'])} 处变更".replace("  ", " "))
    for c in res["changed"][:200]:
        b, a = c.get("before"), c.get("after")
        if b is None and a is not None:
            out.append(f"  + {c['path']}：{_clip(a)}")
        elif a is None:
            out.append(f"  - {c['path']}：{_clip(b)}")
        else:
            out.append(f"  ~ {c['path']}：{_clip(b)} → {_clip(a)}")
    if len(res["changed"]) > 200:
        out.append(f"  …（另有 {len(res['changed']) - 200} 处省略，--json 看全量）")
    for n in res.get("notes") or []:
        out.append(f"· {n}")
    return "\n".join(out)


def format_list(res):
    entries = res.get("entries") or []
    if not entries:
        return f"{res['dir']}：栈为空"
    lines = [f"{res['dir']}：{len(entries)} 条快照（栈顶在最后）"]
    for e in entries:
        lines.append(f"  #{e['seq']}\t{e.get('desc', '')}\t（ts={e.get('ts')}）")
    return "\n".join(lines)
