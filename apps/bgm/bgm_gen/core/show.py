"""show 视图族（检视层；PLAN_v3 §3.5 机制 E / §6 test_show 行）。

定位：已装载工程（core.project.load_project 规范化 dict）上的**纯投影**——
只读、零写盘、绝不调用 save/edit/render（save 写回由 edit 层唯一负责）。
每个视图的 JSON 结构 = tests/test_show.py 锁定的 schema；--text 为人类速览。

索引口径（=编辑层寻址口径，PLAN_v3 §3.4 target 语法 / C4 / BG-D11 草案；
core/edit.py 的 `tracks/<name>/notes|<events>/<i>` 按本视图序号解析）：
    notes  规范排序 = (beat 升序, pitch 升序, 插入序) 稳定排序；
           index = 视图序号 → 编辑寻址第 i 个**真实** note 元素（含窗口过滤时
           index 仍是全量规范序序号——寻址空间不随窗口重编号）。
    events 规范排序 = midi_util.event_sort_key()（(编码 tick, 类型序, pitch,
           插入序)——单一实现，编码器与 show 共用导入，禁两处实现，C3）；
           index = **真实**事件在规范序中的序号（编辑寻址）；notes→note_on/off
           投影行标记 projected=true、无 index、不可寻址。
    纪律：索引跨编辑会漂移——连续编辑必须每次 edit 后重新 show 取索引（BG-D11）。

窗口（--window beat:a-b | sec:a-b）：半开区间 [a, b)；sec 按 meta.bpm 换算
（beat = sec × bpm / 60）；notes 按 onset beat 过滤、events 按事件 t 过滤。

DoD3 grep 口径注记：本模块不含引擎名分支（视图对引擎零特化）；schema 键
"master" 系工程 section 命名（与 core/project.py `proj["master"]` 同惯例的
数据键），非引擎分支逻辑。
"""

import hashlib
import json
import os
import re

from ..engines.midi_util import event_sort_key, notes_to_events, round_half_up
from .project import build_render_plan, events_to_notes, track_cc_curve

UNDO_DIRNAME = ".bgm_undo"          # 工程旁 undo 栈目录（PLAN_v3 §3.4；W1 写入）
UNDO_MANIFEST = "manifest.json"
_DIFF_BUDGET = 64                    # 单文件结构化 diff 条目上限（防巨工程撑爆输出）
_DIFF_DEPTH = 12
_CLIP_LEN = 120
_WINDOW_RE = re.compile(
    r"^\s*(beat|sec)\s*:\s*(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*$")


class ShowError(Exception):
    """show 视图参数/输入错误（消息可直接面向用户；CLI 收敛为 stderr + return 1）。"""


# ---------------------------------------------------------------- 公共小工具

def parse_window(proj, spec):
    """--window 解析 → (start, end) beat 对（半开 [start, end)）或 None。

    spec：None；或 "beat:a-b" / "sec:a-b" 字符串（CLI 口径）；或 (a, b) beat
    对（库调用口径）。sec 按 proj.meta.bpm 换算。非法格式/越界抛 ShowError。
    """
    if spec is None:
        return None
    if isinstance(spec, (tuple, list)) and len(spec) == 2:
        unit, start, end = "beat", float(spec[0]), float(spec[1])
    elif isinstance(spec, str):
        m = _WINDOW_RE.match(spec)
        if not m:
            raise ShowError(f"window 格式错误：{spec!r}"
                            f"（须为 beat:a-b 或 sec:a-b，且 a<b）")
        unit, start, end = m.group(1), float(m.group(2)), float(m.group(3))
    else:
        raise ShowError(f"window 类型错误：{spec!r}"
                        f"（须为 'beat:a-b'/'sec:a-b' 字符串或 (a,b) beat 对）")
    if not start < end:
        raise ShowError(f"window 非法：{spec!r}（必须 a<b，得到 {start} ≥ {end}）")
    if start < 0:
        raise ShowError(f"window 非法：{spec!r}（beat/sec 不得为负，得到 {start}）")
    if unit == "sec":
        bpm = float((proj.get("meta") or {}).get("bpm", 120.0))
        start, end = start * bpm / 60.0, end * bpm / 60.0
    return (start, end)


def _select_tracks(proj, track):
    """track=None → 全部轨；否则按名选单轨（未命中抛 ShowError 列出合法名）。"""
    tracks = list(proj.get("tracks") or [])
    if track is None:
        return tracks
    for t in tracks:
        if t.get("name") == track:
            return [t]
    names = sorted(t.get("name") for t in tracks)
    raise ShowError(f"轨 {track!r} 不存在（工程轨名：{names}）")


def _window_view(win):
    return None if win is None else {"unit": "beat", "start": win[0], "end": win[1]}


def _fmt(x, nd=4):
    """数值格式化（浮点去尾零；整数/无小数位不动——"100"≠"1"）；None → "—"。"""
    if x is None:
        return "—"
    s = f"{float(x):.{nd}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


# ---------------------------------------------------------------- structure

def show_structure(proj, as_json=False):
    """段落/轨/clip/时长总览（PLAN_v3 §3.5）。

    每轨：引擎、音色（instrument）、notes/events 计数、params 透传槽摘要、
    clip 窗（含显式/推导标记与秒窗）、$ref 来源标注（内联轨为 null）。
    时长 = build_render_plan 同口径（max(duration_hint, 各轨 clip 结束秒)）。
    """
    m = proj["meta"]
    spb = 60.0 / float(m["bpm"])
    plan = build_render_plan(proj)
    tracks = []
    for i, t in enumerate(proj.get("tracks") or []):
        clip = t.get("clip") or {}
        tracks.append({
            "index": i,
            "name": t.get("name"),
            "engine": t.get("engine"),
            "instrument": t.get("instrument"),
            "ref_src": t.get("_ref_src"),                 # $ref 来源标注（内联 None）
            "notes": len(t.get("notes") or []),
            "events": len(t.get("events") or []),
            "params": dict(t.get("params") or {}),        # params 透传槽摘要
            "gain_db": t.get("gain_db", 0.0),
            "pan": t.get("pan", 0.0),
            "buses": len(t.get("bus") or []),
            "clip": {
                "start_beat": clip.get("start_beat"),
                "end_beat": clip.get("end_beat"),
                "explicit": bool(t.get("clip_explicit", False)),
                "window_s": round(
                    (clip.get("end_beat", 0.0) - clip.get("start_beat", 0.0))
                    * spb, 6),
            },
        })
    sc = proj.get("sidechain")
    mst = proj.get("master") or {}
    view = {
        "project": proj.get("name"),
        "meta": dict(m),
        "duration_s": round(float(plan["duration_s"]), 6),
        "sections": [
            {"name": s["name"], "start_beat": s["start_beat"],
             "length_beat": s["length_beat"]}
            for s in (proj.get("sections") or [])],
        "tracks": tracks,
        "automation": [
            {"target_track": a["target_track"], "param": a["param"],
             "curve": a["curve"], "points": len(a["points"])}
            for a in (proj.get("automation") or [])],
        "sidechain": None if sc is None else {
            "trigger_track": sc["trigger_track"],
            "targets": list(sc["targets"]),
            "threshold_db": sc["threshold_db"], "ratio": sc["ratio"],
            "attack_ms": sc["attack_ms"], "release_ms": sc["release_ms"]},
        "master": {"chain": mst.get("chain"),
                   "target_lufs": mst.get("target_lufs"),
                   "true_peak_db": mst.get("true_peak_db")},
    }
    return view if as_json else _render_structure(view)


def _render_structure(v):
    lines = [f"project：{v['project']}"]
    m = v["meta"]
    lines.append(f"meta：spec v{m.get('spec_version')} · {_fmt(m.get('bpm'))} BPM · "
                 f"{m.get('sr')} Hz · {m.get('channels')} ch · "
                 f"时长 {_fmt(v['duration_s'], 3)} s")
    if v["sections"]:
        lines.append("sections：")
        for s in v["sections"]:
            lines.append(f"  {s['name']}  [{_fmt(s['start_beat'])}, "
                         f"{_fmt(s['start_beat'] + (s['length_beat'] or 0))})")
    lines.append(f"tracks（{len(v['tracks'])}）：")
    for t in v["tracks"]:
        ref = f"  ← {t['ref_src']}" if t["ref_src"] else ""
        params = ", ".join(f"{k}={_fmt(val)}" for k, val
                           in (t["params"] or {}).items()) or "—"
        src = "显式" if t["clip"]["explicit"] else "推导"
        lines.append(
            f"  [{t['index']}] {t['name']:<10} {t['engine']:<7} "
            f"音色 {t['instrument']} · notes {t['notes']} · events {t['events']} · "
            f"params {{{params}}} · clip [{_fmt(t['clip']['start_beat'])}, "
            f"{_fmt(t['clip']['end_beat'])}) {src}{ref}")
    if v["automation"]:
        lines.append(f"automation（{len(v['automation'])}）：")
        for a in v["automation"]:
            lines.append(f"  {a['target_track']}/{a['param']}  {a['curve']}  "
                         f"{a['points']} 点")
    if v["sidechain"]:
        s = v["sidechain"]
        lines.append(f"sidechain：{s['trigger_track']} → {', '.join(s['targets'])}"
                     f"（thr {_fmt(s['threshold_db'])} dB · {_fmt(s['ratio'])}:1 · "
                     f"{_fmt(s['attack_ms'])}/{_fmt(s['release_ms'])} ms）")
    mst = v["master"] or {}
    lines.append(f"master：{mst.get('chain')} · LUFS {_fmt(mst.get('target_lufs'))}"
                 f" · TP {_fmt(mst.get('true_peak_db'))}")
    return "\n".join(lines)


# ---------------------------------------------------------------- notes

def show_notes(proj, track=None, window=None, as_json=False):
    """notes 规范排序视图（(beat, pitch) 序；index=编辑层寻址口径）。

    每轨两列：
      notes     = 真实 notes 元素按 (beat, pitch, 插入序) 规范排序，每行带
                  index（全量规范序序号——窗口只过滤显示、不重编号）；
      projected = note 事件载体轨（events-only）的 notes 视图投影（events_to_notes
                  合成，标记 projected、无 index、不可寻址——"notes = note_on/off
                  的视图"在 show 层兑现，PLAN_v3 §3.2 投影规范）。
    """
    win = parse_window(proj, window)
    out, total = [], 0
    for t in _select_tracks(proj, track):
        real = sorted(enumerate(t.get("notes") or []),
                      key=lambda p: (p[1][1], p[1][0], p[0]))   # (beat, pitch, 插入序)
        rows = []
        for idx, (_i, n) in enumerate(real):
            beat = float(n[1])
            if win is not None and not win[0] <= beat < win[1]:
                continue
            rows.append({"index": idx, "pitch": float(n[0]), "beat": beat,
                         "dur": float(n[2]), "vel": float(n[3])})
        proj_rows = [
            {"projected": True, "pitch": float(r[0]), "beat": float(r[1]),
             "dur": float(r[2]), "vel": float(r[3])}
            for r in events_to_notes(t.get("events") or [])
            if win is None or win[0] <= float(r[1]) < win[1]]
        out.append({"track": t.get("name"), "engine": t.get("engine"),
                    "count": len(rows), "notes": rows,
                    "projected": proj_rows})
        total += len(rows) + len(proj_rows)
    view = {"project": proj.get("name"), "window": _window_view(win),
            "tracks": out, "total": total}
    return view if as_json else _render_notes(view)


def _render_notes(v):
    win = v["window"]
    lines = [f"project {v['project']}"
             + (f" · 窗口 [{_fmt(win['start'])}, {_fmt(win['end'])})（beat）"
                if win else "")]
    for t in v["tracks"]:
        lines.append(f"track {t['track']}（{t['engine']}）notes {t['count']}"
                     f"（投影 {len(t['projected'])}）")
        for r in t["notes"]:
            lines.append(f"  #{r['index']} pitch {_fmt(r['pitch'], 0)} "
                         f"beat {_fmt(r['beat'])} dur {_fmt(r['dur'])} "
                         f"vel {_fmt(r['vel'], 0)}")
        for r in t["projected"]:
            lines.append(f"  (投影) pitch {_fmt(r['pitch'], 0)} "
                         f"beat {_fmt(r['beat'])} dur {_fmt(r['dur'])} "
                         f"vel {_fmt(r['vel'], 0)}")
    return "\n".join(lines)


# ---------------------------------------------------------------- cc 车道

def _encoded_cc(value):
    """cc 编码值 = clamp(round_half_up(value), 0, 127)——与 midi_util 编码器
    同口径（C3b；值域已由校验层保证 0..127，clamp 为防御）。"""
    return int(min(127, max(0, round_half_up(float(value)))))


def show_cc(proj, track=None, ctrl=None, as_json=False):
    """cc 车道投影视图（消费 core.project.track_cc_curve——禁自写排序/过滤；
    PLAN_v3 §3.2"车道=投影"）。

    显示**编码后值**（R1-m2 口径）：text 只显 round_half_up 编码值（即进 SMF 的
    值）；JSON 数据面同存原始 value 与 encoded 双值。ctrl=None → 该轨全部
    存在的车道（升序）；ctrl 给定时未命中车道为空（count 0）。
    """
    if ctrl is not None:
        try:
            ctrl = int(ctrl)
        except (TypeError, ValueError):
            raise ShowError(f"--ctrl 必须是整数 0..127，得到 {ctrl!r}") from None
        if not 0 <= ctrl <= 127:
            raise ShowError(f"--ctrl {ctrl} 越界（合法 0..127）")
    out = []
    for t in _select_tracks(proj, track):
        if ctrl is not None:
            ctrls = [ctrl]
        else:
            ctrls = sorted({int(e["ctrl"]) for e in (t.get("events") or [])
                            if e.get("type") == "cc"})
        lanes = []
        for c in ctrls:
            pts = [{"t": float(tv), "value": float(v), "encoded": _encoded_cc(v)}
                   for tv, v in track_cc_curve(t, c)]
            lanes.append({"ctrl": c, "count": len(pts), "points": pts})
        out.append({"track": t.get("name"), "engine": t.get("engine"),
                    "lanes": lanes})
    view = {"project": proj.get("name"), "ctrl": ctrl, "tracks": out}
    return view if as_json else _render_cc(view)


def _render_cc(v):
    lines = [f"project {v['project']}"
             + (f" · ctrl {v['ctrl']}" if v["ctrl"] is not None else "")]
    for t in v["tracks"]:
        lines.append(f"track {t['track']}（{t['engine']}）")
        for lane in t["lanes"]:
            if not lane["count"]:
                lines.append(f"  cc{lane['ctrl']}：（无事件）")
                continue
            pts = "  ".join(f"@{_fmt(p['t'])}={p['encoded']}"
                            for p in lane["points"])
            lines.append(f"  cc{lane['ctrl']} {lane['count']} 点：{pts}")
    return "\n".join(lines)


# ---------------------------------------------------------------- events

def show_events(proj, track=None, window=None, as_json=False):
    """事件流规范排序（midi_util.event_sort_key 单一实现，与编码器同序——C3）+
    notes→note_on/off 投影显示（PLAN_v3 §3.2"notes=note 事件的视图"）。

    混装轨合并顺序与引擎编码完全一致：combined = notes_to_events(notes) + events
    （插入序=投影先行；见 engines/sf2.py / sfz.py 同式）——show 与编码器对
    同一工程给出同一规范序。index = **真实**事件在规范序中的序号（编辑寻址
    口径：`tracks/<name>/events/<i>`）；投影行 projected=true、无 index。
    real_total = 真实事件总数（寻址空间大小；窗口只过滤显示不重编号）。
    """
    win = parse_window(proj, window)
    out, total = [], 0
    for t in _select_tracks(proj, track):
        real = list(t.get("events") or [])
        proj_events = notes_to_events(t.get("notes") or [])
        combined = proj_events + real                # 与引擎编码插入序一致
        order = sorted(enumerate(combined),
                       key=lambda p: event_sort_key(p[1], p[0]))
        rows, real_pos = [], 0
        for i, e in order:
            is_proj = i < len(proj_events)
            if not is_proj:
                index = real_pos                     # 全量规范序序号（不随窗口重编号）
                real_pos += 1
            else:
                index = None
            et = float(e.get("t", 0.0))
            if win is not None and not win[0] <= et < win[1]:
                continue
            row = {"type": e["type"], "t": et}
            for k in ("pitch", "vel", "value", "ctrl", "semitones",
                      "program", "bank"):
                if k in e:
                    row[k] = e[k]
            if is_proj:
                row["projected"] = True
            else:
                row["index"] = index
            rows.append(row)
        out.append({"track": t.get("name"), "engine": t.get("engine"),
                    "real_total": len(real),
                    "projected_total": len(proj_events),
                    "events": rows})
        total += len(rows)
    view = {"project": proj.get("name"), "window": _window_view(win),
            "tracks": out, "total": total}
    return view if as_json else _render_events(view)


def _event_desc(e):
    typ = e["type"]
    if typ == "cc":
        return f"ctrl {_fmt(e.get('ctrl'), 0)} = {_fmt(e.get('value'))}"
    if typ == "pitch_bend":
        return f"semitones {_fmt(e.get('semitones'))}"
    if typ == "keyswitch":
        return f"pitch {_fmt(e.get('pitch'), 0)}"
    if typ == "program_change":
        base = f"program {_fmt(e.get('program'), 0)}"
        return base + (f" bank {_fmt(e.get('bank'), 0)}"
                       if e.get("bank") is not None else "")
    out = f"pitch {_fmt(e.get('pitch'), 0)}"
    if e.get("vel") is not None:          # note_off 投影无 vel 键（编码恒 0）
        out += f" vel {_fmt(e.get('vel'), 0)}"
    return out


def _render_events(v):
    win = v["window"]
    lines = [f"project {v['project']}"
             + (f" · 窗口 [{_fmt(win['start'])}, {_fmt(win['end'])})（beat）"
                if win else "")]
    for t in v["tracks"]:
        lines.append(f"track {t['track']}（{t['engine']}）真实事件 "
                     f"{t['real_total']}（notes 投影 {t['projected_total']}）")
        for r in t["events"]:
            tag = " (投影)" if r.get("projected") else f"#{r['index']}"
            lines.append(f"  {tag} {r['type']:<15} @{_fmt(r['t'])}  "
                         f"{_event_desc(r)}")
    return "\n".join(lines)


# ---------------------------------------------------------------- diff

def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json_or_none(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _clip_value(v):
    s = repr(v)
    return s if len(s) <= _CLIP_LEN else s[: _CLIP_LEN - 3] + "…"


def _json_diff(before, after):
    """结构化 JSON diff（有界递归）：返回 {"count", "truncated", "changes"}。
    changes = [{path, kind: added|removed|changed, before?, after?}]。
    """
    out, hit = [], [False]

    def walk(b, a, p, d):
        if len(out) >= _DIFF_BUDGET:
            hit[0] = True
            return
        if d > _DIFF_DEPTH:
            if b != a:
                out.append({"path": p, "kind": "changed",
                            "before": _clip_value(b), "after": _clip_value(a)})
            return
        if isinstance(b, dict) and isinstance(a, dict):
            for k in sorted(set(b) | set(a)):
                if k not in b:
                    out.append({"path": f"{p}.{k}", "kind": "added",
                                "after": _clip_value(a[k])})
                elif k not in a:
                    out.append({"path": f"{p}.{k}", "kind": "removed",
                                "before": _clip_value(b[k])})
                else:
                    walk(b[k], a[k], f"{p}.{k}", d + 1)
        elif isinstance(b, list) and isinstance(a, list):
            for i in range(max(len(b), len(a))):
                if i >= len(b):
                    out.append({"path": f"{p}[{i}]", "kind": "added",
                                "after": _clip_value(a[i])})
                elif i >= len(a):
                    out.append({"path": f"{p}[{i}]", "kind": "removed",
                                "before": _clip_value(b[i])})
                else:
                    walk(b[i], a[i], f"{p}[{i}]", d + 1)
        else:
            # R3 审查 m-6：数值等值（int↔float，如 0 vs 0.0 的序列化规范化噪音）
            # 不计为 changed——首编辑物化默认值不再淹没真实 diff。
            nb = isinstance(b, (int, float)) and not isinstance(b, bool)
            na = isinstance(a, (int, float)) and not isinstance(a, bool)
            if nb and na:
                changed = float(b) != float(a)
            else:
                changed = type(b) is not type(a) or b != a
            if changed:
                out.append({"path": p, "kind": "changed",
                            "before": _clip_value(b), "after": _clip_value(a)})

    walk(before, after, "$", 0)
    return {"count": len(out), "truncated": hit[0], "changes": out}


def _snapshot_dir(root, seq):
    """定位快照目录：<seq>.<ts>/（ts 格式由写入方定，按 f"{seq}." 前缀匹配）。"""
    try:
        names = os.listdir(root)
    except OSError:
        return None
    cands = sorted(n for n in names
                   if n.startswith(f"{seq}.")
                   and os.path.isdir(os.path.join(root, n)))
    return os.path.join(root, cands[0]) if cands else None


def _inside(dir_path, path):
    rp = os.path.realpath(path)
    return rp.startswith(os.path.realpath(dir_path) + os.sep)


def _diff_file(root, entry, f):
    """manifest 单文件条目 vs 当前工程文件。状态：
    unchanged / changed / removed（快照有、当前无）/ snapshot_missing（容错）
    / missing（两边皆无）/ invalid（条目非对象无 path）。"""
    path = f.get("path") if isinstance(f, dict) else None
    rec = {"path": path, "sha256_manifest": f.get("sha256")
           if isinstance(f, dict) else None}
    if not isinstance(path, str) or not path:
        rec["state"] = "invalid"
        return rec
    snap_dir = _snapshot_dir(root, entry["seq"])
    proj_dir = os.path.dirname(root)
    cur = os.path.normpath(os.path.join(proj_dir, path))
    if not _inside(proj_dir, cur):
        cur = None
    snap = None
    if snap_dir is not None:
        cand = os.path.normpath(os.path.join(snap_dir, path))
        if not (_inside(snap_dir, cand) and os.path.isfile(cand)):
            # R3 审查 M-4：W1 写侧快照文件名为扁平形（"/"→"__"，edit._snap_name），
            # 嵌套路径查找失败时回退扁平名——否则 $ref 工程 show diff 恒 snapshot_missing。
            cand = os.path.normpath(os.path.join(snap_dir, path.replace("/", "__")))
        if _inside(snap_dir, cand) and os.path.isfile(cand):
            snap = cand
    if cur is None or not os.path.isfile(cur):
        rec["state"] = "removed" if snap else "missing"
        return rec
    rec["sha256_current"] = _sha256(cur)
    if snap is None:
        rec["state"] = "snapshot_missing"          # 栈残损：容错不炸
        return rec
    rec["sha256_snapshot"] = _sha256(snap)
    rec["manifest_sha_ok"] = (rec["sha256_manifest"]
                              == rec["sha256_snapshot"])
    if rec["sha256_current"] == rec["sha256_snapshot"]:
        rec["state"] = "unchanged"
        return rec
    rec["state"] = "changed"
    before, after = _read_json_or_none(snap), _read_json_or_none(cur)
    rec["diff"] = _json_diff(before, after) \
        if before is not None and after is not None else None
    return rec


def show_diff(proj_path, undo=None, as_json=False):
    """对比 .bgm_undo 快照栈/当前工程文件（PLAN_v3 §3.4 manifest 契约，只读）。

    manifest.json = [{seq, desc, files:[{path, sha256}], ts}]（跨 worker 接口：
    W1 edit.py 写入、本视图读取——R3-P2）。容错：栈目录缺失 → 空结果不炸；
    快照文件缺失 → snapshot_missing；manifest 非数组/条目缺 seq → 忽略条目。
    --undo N 按 seq 选择（缺省=栈顶最大 seq）；未命中抛 ShowError 列出可用 seq。
    """
    proj_path = os.path.abspath(proj_path)
    root = os.path.join(os.path.dirname(proj_path), UNDO_DIRNAME)
    mpath = os.path.join(root, UNDO_MANIFEST)
    base = {"project": os.path.basename(proj_path)}
    if not os.path.isfile(mpath):
        view = dict(base, stack_size=0, selected=None,
                    note=f"无 undo 栈：{root} 不存在（尚未执行过编辑动词）",
                    files=[])
        return view if as_json else _render_diff(view)
    try:
        with open(mpath, encoding="utf-8") as fp:
            entries = json.load(fp)
    except (OSError, json.JSONDecodeError) as e:
        raise ShowError(f".bgm_undo/manifest.json 读取/解析失败：{e}") from e
    if not isinstance(entries, list):
        raise ShowError("manifest.json 顶层必须是数组（[{seq, desc, files, ts}]）")
    entries = sorted((e for e in entries
                      if isinstance(e, dict) and isinstance(e.get("seq"), int)),
                     key=lambda e: e["seq"])
    if not entries:
        view = dict(base, stack_size=0, selected=None,
                    note=f"undo 栈为空（{mpath} 无有效条目）", files=[])
        return view if as_json else _render_diff(view)
    if undo is not None:
        sel = next((e for e in entries if e.get("seq") == undo), None)
        if sel is None:
            raise ShowError(f"undo 快照 seq={undo} 不存在"
                            f"（栈内 seq：{[e['seq'] for e in entries]}）")
    else:
        sel = entries[-1]
    files = [_diff_file(root, sel, f) for f in (sel.get("files") or [])]
    view = dict(base, stack_size=len(entries),
                selected={"seq": sel["seq"], "ts": sel.get("ts"),
                          "desc": sel.get("desc")},
                files=files)
    return view if as_json else _render_diff(view)


def _render_diff(v):
    if v["selected"] is None:
        return f"project {v['project']} · {v.get('note') or '（无 undo 栈）'}"
    sel = v["selected"]
    lines = [f"project {v['project']} · undo 栈 {v['stack_size']} 条",
             f"对比 seq={sel['seq']}（{sel.get('ts')}）「{sel.get('desc')}」"]
    for f in v["files"]:
        state = f["state"]
        if state == "unchanged":
            lines.append(f"  {f['path']}  unchanged")
        elif state == "changed":
            lines.append(f"  {f['path']}  changed"
                         f"（{f['sha256_snapshot'][:8]}… → "
                         f"{f['sha256_current'][:8]}…）")
            diff = f.get("diff")
            if diff:
                for c in diff["changes"]:
                    if c["kind"] == "changed":
                        lines.append(f"    {c['path']}：{c.get('before')} → "
                                     f"{c.get('after')}")
                    else:
                        lines.append(f"    {c['path']}：{c['kind']}"
                                     f" {c.get('before') or c.get('after')}")
                if diff["truncated"]:
                    lines.append(f"    （diff 条目截断于 {diff['count']}，"
                                 f"上限 {_DIFF_BUDGET}）")
        else:
            lines.append(f"  {f['path']}  {state}")
    return "\n".join(lines)
