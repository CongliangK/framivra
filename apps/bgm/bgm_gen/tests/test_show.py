"""show 视图族测试（PLAN_v3 §3.5 / §6 test_show 行；v3 R3-W2）。

[fast]
  ①输出结构断言：structure/notes/cc/events/diff 各视图 --json schema 关键字段
    + --text 非空；
  ②show cc 显示编码后值（构造 63.5 → 显示 64；R1-m2 口径：text 只显编码值、
    JSON 数据面同存 value/encoded 双值）+ 消费 track_cc_curve 一致性；
  ③show events：notes→note_on/off 投影（projected 标记、无 index、显示层剥离
    _on_t/_dur 内部键）+ event_sort_key 规范序 + 窗口过滤；
  ④show diff 空栈/有栈两态（构造假 manifest 目录——§3.4 契约读侧容错）+
    --undo 选择/未命中报错；
  ⑤CLI 接线：各叶 --json 可解析 + text 非空；错误口径 = stderr + return 1 +
    无 Traceback；子进程端到端（helpers.run 口径）；
  ⑥盲测 pytest 化（§3.5 规格）：题库 3 类题型（窗口轨集合/轨音色/窗口音符数）
    × 每类 6 题、seed 固定可复现、ground truth 独立实现（直接 json.load 工程
    原文件计算，不经 load_project/show 模块）、断言全部命中（正确率 100%）。

盲测效力口径（本文件与 core/show.py 双重明示）：锁"show 投影 vs 工程原文件"
一致性——即视图数字与工程文件直算结果逐题相等；**非独立音乐学验证**（不做
听感/声学对拍），音乐正确性由宿主与渲染链 QC 承担。
"""

import json
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers                                              # noqa: E402

helpers.in_proc_env()
from bgm_gen.cli import main as cli_main                    # noqa: E402
from bgm_gen.core.project import load_project, track_cc_curve  # noqa: E402
from bgm_gen.core.show import (ShowError, parse_window,     # noqa: E402
                               show_cc, show_diff, show_events,
                               show_notes, show_structure)
from bgm_gen.engines import midi_util as mu                 # noqa: E402

MINIMAL = os.path.join(helpers.EXAMPLES_DIR, "minimal.json")
BLIND_SEED = 20260823                                       # seed 固定 → 题库可复现


# ---------------------------------------------------------------- fixture 构造
def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return path


def _minimal_copy(d):
    """examples/minimal.json 的 tmp 副本（不动原文件；红线④）。"""
    return _write_json(os.path.join(d, "minimal.json"), helpers.load_json(MINIMAL))


def _v2_events_project(d):
    """含 events 的合成 v2 工程（构造在测试内，PLAN_v3 §3.5 盲测 fixture 口径）：
    pad=sfz 混装轨（notes+cc+pitch_bend，bend_range 显式）、bells=sf2 混装轨
    （notes+cc+params.gain）、flute=sfz events-only 轨（note 载体+keyswitch+PC）。"""
    proj = {
        "name": "blind_v2",
        "meta": {"spec_version": "2", "sr": 44100, "channels": 2, "bpm": 100.0,
                 "duration_hint": 16.0},
        "sections": [{"name": "intro", "start_beat": 0, "length_beat": 8},
                     {"name": "main", "start_beat": 8, "length_beat": 8}],
        "tracks": [
            {"name": "pad", "engine": "sfz",
             "instrument": "VSCO2/CelloEnsSusVib.sfz",
             "notes": [[48, 0, 7.5, 90], [52, 0, 7.5, 80], [55, 4, 8, 70],
                       [48, 8, 7.5, 90], [60, 12, 3.5, 60]],
             "events": [
                 {"type": "cc", "t": 0, "ctrl": 1, "value": 0},
                 {"type": "cc", "t": 4, "ctrl": 1, "value": 63.5},
                 {"type": "cc", "t": 8, "ctrl": 1, "value": 127},
                 {"type": "cc", "t": 0, "ctrl": 11, "value": 100},
                 {"type": "pitch_bend", "t": 12, "semitones": 0.5}],
             "bend_range": 2, "gain_db": -3},
            {"name": "bells", "engine": "sf2", "instrument": "FluidR3_GM:0:9",
             "notes": [[72, 0.5, 1.0, 100], [74, 2.5, 1.0, 90],
                       [76, 4.5, 1.0, 110], [74, 8.5, 1.0, 80]],
             "events": [{"type": "cc", "t": 2, "ctrl": 64, "value": 0.6},
                        {"type": "cc", "t": 6, "ctrl": 64, "value": 127},
                        {"type": "cc", "t": 8, "ctrl": 7, "value": 63.5}],
             "params": {"gain": 0.6}},
            {"name": "flute", "engine": "sfz",
             "instrument": "VPO/Woodwinds/flute-SEC-sustain.sfz",
             "notes": [],
             "events": [
                 {"type": "note_on", "t": 0, "pitch": 74, "vel": 100},
                 {"type": "note_off", "t": 2, "pitch": 74},
                 {"type": "note_on", "t": 1, "pitch": 76, "vel": 90},
                 {"type": "note_off", "t": 2.5, "pitch": 76},
                 {"type": "keyswitch", "t": 0, "pitch": 36},
                 {"type": "program_change", "t": 0, "program": 3}]},
            {"name": "kick", "engine": "synth", "instrument": "kick",
             "notes": [[36, 0, 0.25, 120], [36, 2, 0.25, 110],
                       [36, 4, 0.25, 120], [36, 6, 0.25, 110]]},
        ],
        "master": {"target_lufs": -14, "true_peak_db": -1, "chain": "loudnorm",
                   "qc": {"silent_window_max_s": 2.0}},
    }
    return _write_json(os.path.join(d, "blind_v2.json"), proj)


# ------------------------------------------------ ① 输出结构断言 [fast]
@pytest.mark.fast
def test_show_structure_schema_and_text():
    view = show_structure(load_project(MINIMAL), as_json=True)
    for k in ("project", "meta", "duration_s", "sections", "tracks",
              "automation", "sidechain", "master"):
        assert k in view, f"structure 缺顶层键 {k}"
    assert view["project"] == "minimal"
    assert view["meta"]["spec_version"] == "1"
    assert view["duration_s"] == 4.0
    assert view["sections"][0]["name"] == "A"
    assert [t["name"] for t in view["tracks"]] == ["kick", "strings", "click"]
    tr = view["tracks"][0]
    for k in ("index", "name", "engine", "instrument", "ref_src", "notes",
              "events", "params", "gain_db", "pan", "buses", "clip"):
        assert k in tr, f"structure 轨缺键 {k}"
    for k in ("start_beat", "end_beat", "explicit", "window_s"):
        assert k in tr["clip"], f"structure clip 缺键 {k}"
    assert [t["notes"] for t in view["tracks"]] == [8, 3, 8]
    assert tr["ref_src"] is None and tr["clip"]["explicit"] is True
    assert view["automation"][0]["target_track"] == "strings"
    assert view["automation"][0]["points"] == 2
    assert view["sidechain"]["trigger_track"] == "kick"
    assert view["master"]["chain"] == "loudnorm"
    text = show_structure(load_project(MINIMAL), as_json=False)
    assert text and "kick" in text and "loudnorm" in text


@pytest.mark.fast
def test_show_structure_ref_src_annotation():
    d = helpers.tmpdir("show_ref")
    os.makedirs(os.path.join(d, "tracks"), exist_ok=True)
    _write_json(os.path.join(d, "tracks", "lead.json"),
                {"name": "lead", "engine": "synth", "instrument": "pluck",
                 "notes": [[60, 0, 1.0, 100]]})
    path = _write_json(os.path.join(d, "refdemo.json"), {
        "name": "refdemo",
        "meta": {"spec_version": "2", "sr": 44100, "channels": 2,
                 "bpm": 120.0, "duration_hint": 4.0},
        "tracks": [{"$ref": "tracks/lead.json"}]})
    view = show_structure(load_project(path), as_json=True)
    assert view["tracks"][0]["ref_src"] == "tracks/lead.json"   # $ref 来源标注
    assert view["tracks"][0]["name"] == "lead"
    text = show_structure(load_project(path), as_json=False)
    assert "tracks/lead.json" in text


@pytest.mark.fast
def test_show_notes_canonical_order_index():
    """notes 规范排序 = (beat, pitch) 序（index=编辑层寻址口径；文件序≠视图序）。"""
    proj = helpers.load_json(MINIMAL)
    proj["tracks"][1]["notes"] = [[57, 0, 8, 84], [50, 2, 8, 88],
                                  [53, 0, 8, 80], [50, 0, 8, 88]]
    path = _write_json(os.path.join(helpers.tmpdir("show_notes"), "unsorted.json"),
                       proj)
    view = show_notes(load_project(path), track="strings", as_json=True)
    t = view["tracks"][0]
    rows = t["notes"]
    assert [r["beat"] for r in rows] == [0, 0, 0, 2]
    assert [r["pitch"] for r in rows] == [50, 53, 57, 50]
    assert [r["index"] for r in rows] == [0, 1, 2, 3]
    assert t["count"] == 4 and t["projected"] == []
    assert view["total"] == 4
    text = show_notes(load_project(path), track="strings", as_json=False)
    assert "#0 pitch 50" in text


@pytest.mark.fast
def test_show_notes_window_beat_sec_index_stability():
    proj = load_project(MINIMAL)
    v1 = show_notes(proj, window="beat:0-1", as_json=True)
    assert [t["count"] for t in v1["tracks"]] == [1, 3, 1]
    assert v1["total"] == 5
    assert v1["window"] == {"unit": "beat", "start": 0.0, "end": 1.0}
    v2 = show_notes(proj, window="sec:0-1", as_json=True)       # bpm120 → 1s=2beat
    assert [t["count"] for t in v2["tracks"]] == [2, 3, 2]
    assert v2["window"] == {"unit": "beat", "start": 0.0, "end": 2.0}
    v3 = show_notes(proj, track="kick", window="beat:2-6", as_json=True)
    rows = v3["tracks"][0]["notes"]
    assert [r["index"] for r in rows] == [2, 3, 4, 5]           # 全量规范序序号
    assert [r["beat"] for r in rows] == [2, 3, 4, 5]            # 窗口不重编号


@pytest.mark.fast
def test_show_notes_projects_note_events():
    path = _v2_events_project(helpers.tmpdir("show_proj_notes"))
    view = show_notes(load_project(path), track="flute", as_json=True)
    t = view["tracks"][0]
    assert t["count"] == 0
    rows = t["projected"]
    assert [r["pitch"] for r in rows] == [74, 76]
    assert [r["beat"] for r in rows] == [0, 1]
    assert all(r["projected"] is True and "index" not in r for r in rows)


@pytest.mark.fast
def test_parse_window_errors_and_library_form():
    proj = load_project(MINIMAL)
    for bad in ("bogus", "beat:5-2", "beat:-1-3", "sec:", "beat:1", "x:1-2"):
        with pytest.raises(ShowError):
            parse_window(proj, bad)
    assert parse_window(proj, None) is None
    assert parse_window(proj, (1.0, 2.5)) == (1.0, 2.5)


# ------------------------------------------------ ② show cc 编码值 [fast]
@pytest.mark.fast
def test_show_cc_encoded_value_round_half_up():
    path = _v2_events_project(helpers.tmpdir("show_cc"))
    proj = load_project(path)
    view = show_cc(proj, track="pad", ctrl=1, as_json=True)
    lane = view["tracks"][0]["lanes"][0]
    assert lane["ctrl"] == 1 and lane["count"] == 3
    assert [(p["value"], p["encoded"]) for p in lane["points"]] == \
        [(0.0, 0), (63.5, 64), (127.0, 127)]                    # 63.5 → 64
    text = show_cc(proj, track="pad", ctrl=1, as_json=False)
    assert "63.5" not in text                                   # R1-m2：text 只显编码值
    assert "@4=64" in text
    view2 = show_cc(proj, track="bells", ctrl=64, as_json=True)
    lane2 = view2["tracks"][0]["lanes"][0]
    assert [(p["value"], p["encoded"]) for p in lane2["points"]] == \
        [(0.6, 1), (127.0, 127)]                                # 0.6 → 1
    view3 = show_cc(proj, track="kick", ctrl=11, as_json=True)
    assert view3["tracks"][0]["lanes"] == \
        [{"ctrl": 11, "count": 0, "points": []}]                # 未命中→空车道
    view4 = show_cc(proj, track="pad", as_json=True)
    assert [l["ctrl"] for l in view4["tracks"][0]["lanes"]] == [1, 11]
    with pytest.raises(ShowError):
        show_cc(proj, track="pad", ctrl=200)


@pytest.mark.fast
def test_show_cc_consumes_track_cc_curve():
    """车道投影消费 core.project.track_cc_curve（禁自写排序/过滤）：逐点对拍。"""
    path = _v2_events_project(helpers.tmpdir("show_cc_curve"))
    proj = load_project(path)
    t = next(x for x in proj["tracks"] if x["name"] == "pad")
    view = show_cc(proj, track="pad", as_json=True)
    for lane in view["tracks"][0]["lanes"]:
        pairs = track_cc_curve(t, lane["ctrl"])
        assert [p["t"] for p in lane["points"]] == [tv for tv, _ in pairs]
        assert [p["value"] for p in lane["points"]] == [v for _, v in pairs]
        assert all(p["encoded"] == int(min(127, max(0, mu.round_half_up(p["value"]))))
                   for p in lane["points"])


# ------------------------------------------------ ③ show events [fast]
@pytest.mark.fast
def test_show_events_notes_projection_marks():
    proj = load_project(MINIMAL)
    view = show_events(proj, track="strings", as_json=True)
    t = view["tracks"][0]
    assert t["real_total"] == 0 and t["projected_total"] == 6
    rows = t["events"]
    assert all(r["projected"] is True and "index" not in r for r in rows)
    assert [r["type"] for r in rows] == ["note_on"] * 3 + ["note_off"] * 3
    assert [r["pitch"] for r in rows[:3]] == [50, 53, 57]       # 同 tick pitch 升序


@pytest.mark.fast
def test_show_events_canonical_order_and_index():
    path = _v2_events_project(helpers.tmpdir("show_events"))
    proj = load_project(path)
    view = show_events(proj, track="pad", as_json=True)
    t = view["tracks"][0]
    assert t["real_total"] == 5 and t["projected_total"] == 10
    rows = t["events"]
    real = [r for r in rows if not r.get("projected")]
    assert [r["index"] for r in real] == [0, 1, 2, 3, 4]        # 真实事件规范序序号
    # 视图序 = midi_util.event_sort_key 规范序（编码器与 show 共用单一实现）
    loaded = next(x for x in proj["tracks"] if x["name"] == "pad")
    combined = mu.notes_to_events(loaded["notes"]) + list(loaded["events"])
    expect = [e["type"] for _, e in sorted(
        enumerate(combined), key=lambda p: mu.event_sort_key(p[1], p[0]))]
    assert [r["type"] for r in rows] == expect
    assert rows[0]["type"] == "cc" and rows[0]["ctrl"] == 1     # 类型序 cc 先于 note_on
    assert rows[1]["type"] == "cc" and rows[1]["ctrl"] == 11
    off = next(r for r in rows if r["type"] == "note_off")
    assert "vel" not in off and "_on_t" not in off and "_dur" not in off


@pytest.mark.fast
def test_show_events_window():
    path = _v2_events_project(helpers.tmpdir("show_events_win"))
    proj = load_project(path)
    view = show_events(proj, track="pad", window="beat:0-8", as_json=True)
    assert view["window"] == {"unit": "beat", "start": 0.0, "end": 8.0}
    rows = view["tracks"][0]["events"]
    assert all(0.0 <= r["t"] < 8.0 for r in rows)
    loaded = next(x for x in proj["tracks"] if x["name"] == "pad")
    combined = mu.notes_to_events(loaded["notes"]) + list(loaded["events"])
    expect = [(e["type"], float(e["t"])) for _, e in sorted(
        enumerate(combined), key=lambda p: mu.event_sort_key(p[1], p[0]))
        if 0.0 <= float(e["t"]) < 8.0]
    assert [(r["type"], r["t"]) for r in rows] == expect


# ------------------------------------------------ ④ show diff 两态 [fast]
@pytest.mark.fast
def test_show_diff_empty_stack():
    p = _minimal_copy(helpers.tmpdir("show_diff_empty"))
    view = show_diff(p, as_json=True)
    assert view["stack_size"] == 0 and view["selected"] is None
    assert view["files"] == []
    assert "无 undo 栈" in view["note"]
    text = show_diff(p, as_json=False)
    assert text and "undo" in text


def _fake_undo_stack(d, proj_path):
    """构造假 manifest 目录（模拟 W1 edit.py 写入的 §3.4 契约栈；读侧容错样本）：
    seq1 快照=原文件（当前已改→changed）+ 快照有当前无（removed）+ manifest
    列了但快照没存（snapshot_missing）；seq2 快照=当前内容（unchanged）。"""
    root = os.path.join(d, ".bgm_undo")
    snap1 = os.path.join(root, "1.20260823T100000")
    snap2 = os.path.join(root, "2.20260823T101500")
    os.makedirs(snap1)
    os.makedirs(snap2)
    with open(proj_path, "rb") as f:
        original = f.read()
    with open(os.path.join(snap1, "project.json"), "wb") as f:
        f.write(original)
    with open(os.path.join(snap1, "extra.json"), "w", encoding="utf-8") as f:
        json.dump({"gone": True}, f, ensure_ascii=False)
    cur = helpers.load_json(proj_path)
    cur["tracks"][1]["gain_db"] = -2.0                         # 当前被修改
    _write_json(proj_path, cur)
    with open(proj_path, "rb") as f:
        current = f.read()
    with open(os.path.join(snap2, "project.json"), "wb") as f:
        f.write(current)
    _write_json(os.path.join(d, "drift.json"), {"x": 1})
    manifest = [
        {"seq": 1, "desc": "set tracks/strings/gain_db", "ts": "20260823T100000",
         "files": [
             {"path": "project.json",
              "sha256": helpers.sha256(os.path.join(snap1, "project.json"))},
             {"path": "extra.json",
              "sha256": helpers.sha256(os.path.join(snap1, "extra.json"))},
             {"path": "drift.json", "sha256": "d" * 64}]},
        {"seq": 2, "desc": "基线（当前内容）", "ts": "20260823T101500",
         "files": [{"path": "project.json",
                    "sha256": helpers.sha256(os.path.join(snap2, "project.json"))}]},
    ]
    _write_json(os.path.join(root, "manifest.json"), manifest)
    return root


@pytest.mark.fast
def test_show_diff_with_stack_states_and_undo_select():
    d = helpers.tmpdir("show_diff_stack")
    p = _write_json(os.path.join(d, "project.json"), helpers.load_json(MINIMAL))
    _fake_undo_stack(d, p)
    view = show_diff(p, as_json=True)
    assert view["stack_size"] == 2
    assert view["selected"]["seq"] == 2                        # 缺省=栈顶
    files = {f["path"]: f for f in view["files"]}
    assert files["project.json"]["state"] == "unchanged"
    assert files["project.json"]["manifest_sha_ok"] is True
    v1 = show_diff(p, undo=1, as_json=True)                    # 指定 seq
    assert v1["selected"]["seq"] == 1
    f1 = {f["path"]: f for f in v1["files"]}
    assert f1["project.json"]["state"] == "changed"
    diff = f1["project.json"]["diff"]
    paths = {c["path"]: c["kind"] for c in diff["changes"]}
    assert paths["$.tracks[1].gain_db"] == "changed"
    assert f1["extra.json"]["state"] == "removed"
    assert f1["drift.json"]["state"] == "snapshot_missing"
    text = show_diff(p, undo=1, as_json=False)
    assert text and "changed" in text and "tracks[1].gain_db" in text
    with pytest.raises(ShowError) as ei:
        show_diff(p, undo=99)
    assert "seq" in str(ei.value) and "[1, 2]" in str(ei.value)


# ------------------------------------------------ ⑤ CLI 接线 [fast]
@pytest.mark.fast
def test_show_cli_json_leaves_parseable(capsys):
    for leaf, extra in (("structure", []), ("notes", ["--track", "kick"]),
                        ("cc", ["--track", "strings"]),
                        ("events", ["--track", "kick"]), ("diff", [])):
        rc = cli_main(["show", leaf, MINIMAL, "--json", *extra])
        out = capsys.readouterr().out
        assert rc == 0, f"show {leaf} exit {rc}"
        view = json.loads(out)
        assert view["project"] in ("minimal", "minimal.json")


@pytest.mark.fast
def test_show_cli_text_nonempty(capsys):
    for leaf in ("structure", "notes", "cc", "events", "diff"):
        rc = cli_main(["show", leaf, MINIMAL])
        out = capsys.readouterr().out
        assert rc == 0 and out.strip(), f"show {leaf} text 为空"


@pytest.mark.fast
def test_show_cli_error_stderr_no_traceback(capsys):
    bad_json = os.path.join(helpers.tmpdir("show_err"), "bad.json")
    with open(bad_json, "w", encoding="utf-8") as f:
        f.write("not json {")                        # 原始非法 JSON（非 _write_json）
    cases = [
        (["show", "notes", MINIMAL, "--window", "bogus"], "window"),
        (["show", "notes", MINIMAL, "--track", "nope"], "轨"),
        (["show", "cc", MINIMAL, "--ctrl", "200"], "ctrl"),
        (["show", "structure", bad_json], "JSON 解析失败"),
        (["show", "structure", "/nonexistent/x.json"], "No such file"),
    ]
    for argv, expect in cases:
        rc = cli_main(argv)
        err = capsys.readouterr().err
        assert rc == 1, f"{argv} exit {rc}"
        assert expect in err, f"{argv} stderr 缺 {expect!r}：{err}"
        assert "Traceback" not in err


@pytest.mark.fast
def test_show_cli_subprocess_end_to_end():
    """子进程端到端（helpers.run 口径，python3 -m bgm_gen show …）。"""
    r = helpers.run([helpers.py3(), "-m", "bgm_gen", "show", "structure",
                     MINIMAL, "--json"], timeout=120)
    assert r.returncode == 0, r.stderr[-800:]
    out = json.loads(r.stdout)
    assert out["project"] == "minimal"
    assert [t["name"] for t in out["tracks"]] == ["kick", "strings", "click"]
    r2 = helpers.run([helpers.py3(), "-m", "bgm_gen", "show", "notes",
                      MINIMAL, "--window", "beat:0-1", "--json"], timeout=120)
    assert r2.returncode == 0, r2.stderr[-800:]
    assert json.loads(r2.stdout)["total"] == 5
    r3 = helpers.run([helpers.py3(), "-m", "bgm_gen", "show", "diff",
                      MINIMAL, "--json"], timeout=120)
    assert r3.returncode == 0, r3.stderr[-800:]
    assert json.loads(r3.stdout)["stack_size"] == 0


# ------------------------------------------------ ⑥ 盲测（§3.5 规格）[fast]
def _blind_raw(path):
    """ground truth 数据面：直接 json.load 工程原文件（不经 load_project/show）。
    $ref 槽位手卷展开（仍是纯文件直读，不走装载层）——R4 审查 B·N5/F-3：
    命运工程为 split 布局，轨槽位 {"$ref": "tracks/<file>.json"}。"""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    base = os.path.dirname(os.path.abspath(path))
    for i, t in enumerate(raw.get("tracks") or []):
        if isinstance(t, dict) and "$ref" in t:
            with open(os.path.join(base, t["$ref"]), encoding="utf-8") as f:
                raw["tracks"][i] = json.load(f)
    return raw


def _blind_tracks_with_notes(raw, a, b):
    """窗口 [a,b) 内能发声的轨名集合（独立直算）：真实 notes onset 落窗，
    或 events-only 轨有 note_on 落窗（show_notes 投影面同口径）。"""
    out = set()
    for t in raw["tracks"]:
        if any(a <= float(n[1]) < b for n in (t.get("notes") or [])):
            out.add(t["name"])
        elif any(e.get("type") == "note_on" and a <= float(e["t"]) < b
                 for e in (t.get("events") or [])):
            out.add(t["name"])
    return out


def _blind_timbre(raw, track_name):
    t = next(x for x in raw["tracks"] if x["name"] == track_name)
    return t["engine"], t["instrument"]


def _blind_note_count(raw, track_name, a, b):
    t = next(x for x in raw["tracks"] if x["name"] == track_name)
    real = sum(1 for n in (t.get("notes") or []) if a <= float(n[1]) < b)
    proj = sum(1 for e in (t.get("events") or [])
               if e.get("type") == "note_on" and a <= float(e["t"]) < b)
    return real + proj


def _make_blind_questions():
    """题库（seed 固定可复现）：3 题型 × 每工程 3 题 × 2 工程 = 每类 6 题（≥5）。
    ground truth 一律 _blind_* 独立直算。fixture = minimal 副本 + 合成 v2 工程。"""
    rng = random.Random(BLIND_SEED)
    paths = [_minimal_copy(helpers.tmpdir("blind_minimal")),
             _v2_events_project(helpers.tmpdir("blind_v2"))]
    # R4 审查 B·M-1（PLAN §6 F-3 落地）：命运工程 fixture 复跑——缺产物时
    # 显式跳过（同 out/minimal 锚点"新机首跑前置"惯例，专用用例给 skip 理由）。
    # fate 排在两 fixture 之后追加：前两工程的 rng 抽题序列不因 fate 存在与否
    # 而变（题库逐环境字节复现）。
    _fate = os.path.join(helpers.OUT_DIR, "fate_mvt1", "project.json")
    if os.path.isfile(_fate):
        paths.append(_fate)
    qs = []
    for path in paths:
        raw = _blind_raw(path)
        tracks = [t["name"] for t in raw["tracks"]]
        max_end = max(n[1] + n[2] for t in raw["tracks"]
                      for n in (t.get("notes") or []))
        for _ in range(3):
            a = rng.uniform(0.0, max_end * 0.6)
            b = a + rng.uniform(0.5, max_end * 0.6)
            qs.append({"type": "window_tracks", "project": path, "a": a, "b": b,
                       "expected": _blind_tracks_with_notes(raw, a, b)})
            tn = rng.choice(tracks)
            qs.append({"type": "note_count", "project": path, "track": tn,
                       "a": a, "b": b,
                       "expected": _blind_note_count(raw, tn, a, b)})
            tn = rng.choice(tracks)
            qs.append({"type": "timbre", "project": path, "track": tn,
                       "expected": _blind_timbre(raw, tn)})
        # R3 审查 B-1 修复：events-only 轨配额——seed=20260823 实抽对 flute
        # （events-only）零覆盖，注入实验证实投影剥离缺陷盲测不可见。确定性补题
        # （不耗 rng，题库仍字节复现）：每 events-only 轨三题型各补 1 题，
        # 窗口取其 note_on 区 [min,max+1)。
        for t in raw["tracks"]:
            if t.get("notes"):
                continue
            ons = [float(e["t"]) for e in (t.get("events") or [])
                   if e.get("type") == "note_on"]
            if not ons:
                continue
            lo, hi = min(ons), max(ons) + 1.0
            qs.append({"type": "window_tracks", "project": path,
                       "a": lo, "b": hi,
                       "expected": _blind_tracks_with_notes(raw, lo, hi)})
            qs.append({"type": "note_count", "project": path, "track": t["name"],
                       "a": lo, "b": hi,
                       "expected": _blind_note_count(raw, t["name"], lo, hi)})
            qs.append({"type": "timbre", "project": path, "track": t["name"],
                       "expected": _blind_timbre(raw, t["name"])})
    return qs


BLIND_QUESTIONS = _make_blind_questions()


def _eval_blind_question(q):
    """被测侧 = load_project + show 视图；命中 = show 投影与工程原文件直算相等。"""
    proj = load_project(q["project"])
    if q["type"] == "window_tracks":
        view = show_notes(proj, window=(q["a"], q["b"]), as_json=True)
        got = {t["track"] for t in view["tracks"]
               if t["count"] or t["projected"]}
    elif q["type"] == "note_count":
        view = show_notes(proj, track=q["track"], window=(q["a"], q["b"]),
                          as_json=True)
        t = view["tracks"][0]
        got = t["count"] + len(t["projected"])
    else:
        view = show_structure(proj, as_json=True)
        t = next(x for x in view["tracks"] if x["name"] == q["track"])
        got = (t["engine"], t["instrument"])
    return got == q["expected"]


@pytest.mark.fast
@pytest.mark.parametrize(
    "q", BLIND_QUESTIONS,
    ids=[f"{q['type']}[{i}]" for i, q in enumerate(BLIND_QUESTIONS)])
def test_show_blind_question(q):
    assert _eval_blind_question(q), \
        f"盲测未命中（show 投影 ≠ 工程原文件直算）：{q}"


@pytest.mark.fast
def test_show_blind_bank_shape_and_reproducible():
    kinds = {}
    for q in BLIND_QUESTIONS:
        kinds[q["type"]] = kinds.get(q["type"], 0) + 1
    assert set(kinds) == {"window_tracks", "note_count", "timbre"}
    assert all(n >= 5 for n in kinds.values()), kinds      # ≥3 题型 × 每类 ≥5 题
    assert BLIND_QUESTIONS == _make_blind_questions()      # seed 固定 → 题库可复现


@pytest.mark.fast
def test_blind_fate_fixture_included():
    """PLAN_v3 §6 F-3（R4 审查 B·M-1 落地）：盲测以命运工程 fixture 复跑——
    $ref split 布局 × 23 轨 × 8295 音符下的 show 投影一致性。缺产物显式 skip。"""
    _fate = os.path.join(helpers.OUT_DIR, "fate_mvt1", "project.json")
    if not os.path.isfile(_fate):
        pytest.skip("out/fate_mvt1/project.json 缺失（定稿渲染前置，F-3 复跑）")
    fate_qs = [q for q in BLIND_QUESTIONS if q["project"] == _fate]
    assert len(fate_qs) >= 9, f"fate 题量 {len(fate_qs)} < 9（3 题型×3 题）"
    for q in fate_qs:
        assert _eval_blind_question(q), f"命运盲测未命中：{q}"
