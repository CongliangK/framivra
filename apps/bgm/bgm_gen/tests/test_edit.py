"""③ R3-W1 操作层编辑引擎测试（PLAN_v3 §6 test_edit 行 / §5 R3-W1 判据②）。

[fast] 全部：
- CRUD 正负例（exit≠0 + stderr 字段路径 + 无 Traceback；$ref 工程报错带来源标注）
- insert/set 语义细则（索引前插/append/深度合并/未知键拒/无变更不入栈）
- undo 栈 + manifest 格式 + 漂移检测负例（--force 才过）+ 写失败自动恢复
  + 上限 50 FIFO 淘汰
- BGM_CRASH_AFTER=N 原子写窗口注入（子进程精确停在"快照已入栈、第 2 个文件
  已 rename"后 kill；undo 恢复后无半状态）
- 跨形式 copy-of 正/负例（notes↔events 载体转换、vel=0/交叠/控制事件拒绝）
- --transpose/--quantize/--scale 折叠；--window sec 换算正例
- rename 四处引用重写（automation/sidechain/外部轨文件名）
- move 跨轨；split/merge 薄包装 + undo
- eden_v2 十种真实修改（临时副本执行，红线④正本不动；out/eden_v2 缺失时
  pytest.skip，主树合流后全量跑——判据③"show diff 可解析"由主线集成验证）

一律 helpers.tmpdir(...)，绝不写仓库 out/。
"""

import json
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers                                              # noqa: E402

helpers.in_proc_env()
from bgm_gen.core.edit import (EditError, edit_merge, edit_project,  # noqa: E402
                               edit_split, list_undo, undo_edit)
from bgm_gen.core.project import (ProjectError, load_project,  # noqa: E402
                                  save_project, split_layout)

MINIMAL = os.path.join(helpers.EXAMPLES_DIR, "minimal.json")


# ---------------------------------------------------------------- 小工具
def _write(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _copy_project(d, name="proj.json", version=None):
    proj = helpers.load_json(MINIMAL)
    if version:
        proj["meta"]["spec_version"] = version
    path = os.path.join(d, name)
    _write(path, proj)
    return path


def _bgm_edit(*args, env=None):
    return helpers.run([helpers.py3(), "-m", "bgm_gen", "edit", *args],
                       cwd=helpers.WORKSPACE, env_extra=env)


def _make_ref_project(d, name="proj.json"):
    """minimal v2 → split → $ref 双布局工程（in-process 构造，省子进程）。"""
    proj = load_project(MINIMAL)
    proj["meta"]["spec_version"] = "2"
    path = os.path.join(d, name)
    split_layout(proj, path, os.path.join(d, "tracks"))
    save_project(path, proj)
    return path


def _disk(path):
    with open(path, "rb") as f:
        return f.read()


def _manifest(d):
    p = os.path.join(d, ".bgm_undo", "manifest.json")
    return json.load(open(p, encoding="utf-8")) if os.path.isfile(p) else []


# ---------------------------------------------------------------- insert
@pytest.mark.fast
def test_insert_note_splice_before_view_index_and_append():
    d = helpers.tmpdir("edit_ins")
    path = _copy_project(d)
    r = edit_project(path, "insert", target="tracks/kick/notes/2",
                     payload=[36, 0.5, 0.25, 42])
    assert r["undo"]["seq"] == 1 and r["changed"]
    p = load_project(path)
    assert p["tracks"][0]["notes"][2] == [36.0, 0.5, 0.25, 42.0], \
        "带索引 insert 必须在规范视图该索引**之前**插入"
    # 无索引 = append
    edit_project(path, "insert", target="tracks/kick/notes",
                 payload=[60, 9.0, 0.5, 80])
    p = load_project(path)
    assert p["tracks"][0]["notes"][-1] == [60.0, 9.0, 0.5, 80.0]
    # 索引漂移纪律提示（C4/BG-D11）
    assert any("重新 show" in n for n in r["notes"])


@pytest.mark.fast
def test_insert_negative():
    d = helpers.tmpdir("edit_insneg")
    path = _copy_project(d)
    cases = [
        (dict(target="tracks/kick/notes/x", payload=[36, 0, 0.1, 100]), "非负整数"),
        (dict(target="tracks/kick/notes/99", payload=[36, 0, 0.1, 100]), "越界"),
        (dict(target="tracks/kick/notes", payload=[36, 0, 0.1, 100],
              copy_of="tracks/kick/notes/0"), "互斥"),
        (dict(target="tracks/kick/notes"), "需要 --payload 或 --copy-of"),
        (dict(target="frobnicate/1", payload=[36, 0, 0.1, 100]), "语法错误"),
        (dict(target="tracks/kick/clip", payload={"start_beat": 1}), "不支持"),
        (dict(target="tracks/kick/notes", payload=[36, 0, 0.1, 100],
              window="beat:0-1"), "仅与 --copy-of"),
    ]
    for kw, expect in cases:
        with pytest.raises(EditError) as ei:
            edit_project(path, "insert", **kw)
        assert expect in str(ei.value), f"{kw} → {ei.value}"
    assert _manifest(d) == [], "负例不得入 undo 栈"


@pytest.mark.fast
def test_insert_events_v1_gate_and_v2_ok():
    d = helpers.tmpdir("edit_v1gate")
    v1 = _copy_project(d, "v1.json")
    with pytest.raises(EditError) as ei:
        edit_project(v1, "insert", target="tracks/kick/events",
                     payload={"type": "cc", "t": 0, "ctrl": 1, "value": 50})
    assert "spec_version" in str(ei.value) and "tracks[0].events" in str(ei.value)
    # v2 正例：sf2 轨（strings）接受 cc；synth/sample 轨全拒事件（消费矩阵）
    v2 = _copy_project(d, "v2.json", version="2")
    r = edit_project(v2, "insert", target="tracks/strings/events",
                     payload={"type": "cc", "t": 0, "ctrl": 1, "value": 50})
    assert r["undo"]["seq"] == 1
    assert load_project(v2)["tracks"][1]["events"][0]["ctrl"] == 1
    with pytest.raises(EditError) as ei:
        edit_project(v2, "insert", target="tracks/kick/events",
                     payload={"type": "cc", "t": 0, "ctrl": 1, "value": 50})
    assert "不支持 cc 事件" in str(ei.value), "synth 轨事件能力门（再校验口径）"


# ---------------------------------------------------------------- set
@pytest.mark.fast
def test_set_deep_merge_and_unknown_key():
    d = helpers.tmpdir("edit_set")
    path = _copy_project(d, version="2")
    r = edit_project(path, "set", target="tracks/kick",
                     payload={"gain_db": -5, "clip": {"end_beat": 10}})
    p = load_project(path)
    t = p["tracks"][0]
    assert t["gain_db"] == -5.0
    assert t["clip"] == {"start_beat": 0.0, "end_beat": 10.0}, \
        "对象深度合并：clip 嵌套对象须合并而非整体替换"
    assert any(c["path"] == "tracks[0].clip.end_beat" for c in r["changed"])
    for target, payload, expect in [
            ("tracks/kick", {"frobnicate": 1}, "tracks/kick.frobnicate"),
            ("tracks/kick/notes/0", {"frobnicate": 1}, ".frobnicate"),
            ("tracks/kick/clip", {"nope": 1}, "tracks/kick/clip.nope"),
            ("sections/0", {"nope": 1}, "sections[0].nope"),
            ("meta/nope", 1, "meta/nope"),
            ("master/nope", 1, "master/nope"),
            ("master/qc/nope", 1, "master/qc/nope"),
    ]:
        with pytest.raises(EditError) as ei:
            edit_project(path, "set", target=target, payload=payload)
        assert expect in str(ei.value), f"{target} → {ei.value}"
    # params 未知键（描述符驱动，与校验层同口径）
    with pytest.raises(EditError) as ei:
        edit_project(path, "set", target="tracks/strings/params",
                     payload={"nope_param": 1})
    assert "未知参数" in str(ei.value)


@pytest.mark.fast
def test_set_scalar_targets_and_master_qc():
    d = helpers.tmpdir("edit_scalar")
    path = _copy_project(d)
    edit_project(path, "set", target="meta/bpm", payload=150)
    edit_project(path, "set", target="meta/key", payload="E minor")
    edit_project(path, "set", target="master/target_lufs", payload=-16)
    edit_project(path, "set", target="master/qc/duration_min_s", payload=30)
    p = load_project(path)
    assert p["meta"]["bpm"] == 150.0 and p["meta"]["key"] == "E minor"
    assert p["master"]["target_lufs"] == -16.0
    assert p["master"]["qc"]["duration_min_s"] == 30
    # master 深度合并（qc 子对象合并）
    edit_project(path, "set", target="master",
                 payload={"qc": {"silent_window_max_s": 2.0}})
    p = load_project(path)
    assert p["master"]["qc"]["silent_window_max_s"] == 2.0
    assert p["master"]["qc"]["duration_min_s"] == 30, "qc 子对象须合并而非替换"
    # 再校验兜底：matchering 无 ref → 字段路径拒绝
    with pytest.raises(EditError) as ei:
        edit_project(path, "set", target="master", payload={"chain": "matchering"})
    assert "master.ref" in str(ei.value)


@pytest.mark.fast
def test_set_window_notes_events_and_sec_equivalence():
    d = helpers.tmpdir("edit_win")
    pa = _copy_project(d, "a.json", version="2")
    edit_project(pa, "set", target="tracks/kick/notes", window="beat:2-4",
                 payload={"vel": 9})
    p = load_project(pa)
    assert [int(n[3]) for n in p["tracks"][0]["notes"][:4]] == [120, 110, 9, 9], \
        "窗口 [2,4) 只应命中 beat 2、3"
    # sec 换算（bpm 120 → sec:1-2 == beat:2-4）等价
    pb = _copy_project(d, "b.json", version="2")
    edit_project(pb, "set", target="tracks/kick/notes", window="sec:1-2",
                 payload={"vel": 9})
    q = load_project(pb)
    assert [int(n[3]) for n in q["tracks"][0]["notes"][:4]] == [120, 110, 9, 9]
    # events 容器窗口
    edit_project(pa, "insert", target="tracks/strings/events",
                 payload=[{"type": "cc", "t": 0, "ctrl": 1, "value": 10},
                          {"type": "cc", "t": 3, "ctrl": 1, "value": 20}])
    edit_project(pa, "set", target="tracks/strings/events", window="beat:0-1",
                 payload={"value": 99})
    p = load_project(pa)
    vals = [int(e["value"]) for e in p["tracks"][1]["events"]]
    assert vals == [99, 20]
    # 容器无窗口 + payload → 拒绝（整替换须 delete+insert）
    with pytest.raises(EditError) as ei:
        edit_project(pa, "set", target="tracks/kick/notes", payload=[[1, 2, 3, 4]])
    assert "--window" in str(ei.value)


@pytest.mark.fast
def test_set_window_modifiers_transpose_quantize_scale():
    d = helpers.tmpdir("edit_mod")
    path = _copy_project(d)
    edit_project(path, "set", target="tracks/kick/notes", window="beat:0-4",
                 transpose=2)
    p = load_project(path)
    assert [n[0] for n in p["tracks"][0]["notes"][:4]] == [38.0] * 4
    edit_project(path, "set", target="tracks/kick/notes", window="beat:0-4",
                 quantize=0.25)
    p = load_project(path)
    assert [n[1] for n in p["tracks"][0]["notes"][:4]] == [0.0, 1.0, 2.0, 3.0]
    edit_project(path, "set", target="tracks/kick/notes", window="beat:0-4",
                 scale=2)
    p = load_project(path)
    assert [n[1] for n in p["tracks"][0]["notes"][:4]] == [0.0, 2.0, 4.0, 6.0]
    assert [n[2] for n in p["tracks"][0]["notes"][:4]] == [0.5] * 4, \
        "scale 缩放时间轴：dur 同步缩放"


@pytest.mark.fast
def test_set_note_and_event_element_merge():
    d = helpers.tmpdir("edit_el")
    path = _copy_project(d, version="2")
    edit_project(path, "set", target="tracks/kick/notes/0",
                 payload={"vel": 127})
    p = load_project(path)
    assert p["tracks"][0]["notes"][0][3] == 127.0
    edit_project(path, "insert", target="tracks/strings/events",
                 payload={"type": "cc", "t": 0, "ctrl": 11, "value": 60})
    edit_project(path, "set", target="tracks/strings/events/0",
                 payload={"value": 90})
    p = load_project(path)
    assert p["tracks"][1]["events"][0]["value"] == 90.0
    # 事件未知字段按最终 type 闭集拒绝
    with pytest.raises(EditError) as ei:
        edit_project(path, "set", target="tracks/strings/events/0",
                     payload={"semitones": 1})
    assert "未知字段" in str(ei.value)


# ---------------------------------------------------------------- move
@pytest.mark.fast
def test_move_cross_track_and_window():
    d = helpers.tmpdir("edit_mv")
    path = _copy_project(d)
    before = load_project(path)
    n_kick, n_str = (len(before["tracks"][0]["notes"]),
                     len(before["tracks"][1]["notes"]))
    edit_project(path, "move", target="tracks/kick/notes/0",
                 dest="tracks/strings/notes/0")
    p = load_project(path)
    assert len(p["tracks"][0]["notes"]) == n_kick - 1
    assert len(p["tracks"][1]["notes"]) == n_str + 1
    assert p["tracks"][1]["notes"][0] == [36.0, 0.0, 0.25, 120.0], "move 跨轨"
    # 窗口 move（容器 → 容器 append）
    edit_project(path, "move", target="tracks/kick/notes", window="beat:4-6",
                 dest="tracks/strings/notes")
    p = load_project(path)
    assert len(p["tracks"][0]["notes"]) == n_kick - 3, "窗口 move 移除源段"
    # 载体不一致拒绝
    with pytest.raises(EditError) as ei:
        edit_project(path, "move", target="tracks/kick/notes/0",
                     dest="tracks/strings/events/0")
    assert "载体必须一致" in str(ei.value)


@pytest.mark.fast
def test_move_track_reorder():
    d = helpers.tmpdir("edit_mvt")
    path = _copy_project(d)
    edit_project(path, "move", target="tracks/kick", dest="tracks/click")
    p = load_project(path)
    assert [t["name"] for t in p["tracks"]] == ["strings", "kick", "click"]
    edit_project(path, "move", target="tracks/kick", dest="tracks")
    p = load_project(path)
    assert p["tracks"][-1]["name"] == "kick"


# ---------------------------------------------------------------- delete
@pytest.mark.fast
def test_delete_variants():
    d = helpers.tmpdir("edit_del")
    path = _copy_project(d, version="2")
    # params 键删（先建后删）
    edit_project(path, "set", target="tracks/strings/params",
                 payload={"gain": 0.5})
    edit_project(path, "delete", target="tracks/strings/params/gain")
    assert "gain" not in load_project(path)["tracks"][1].get("params", {})
    # clip 删 → 装载层按 notes 推导（kick 末音 7+0.25 + 4 拍尾巴）
    edit_project(path, "delete", target="tracks/kick/clip")
    t = load_project(path)["tracks"][0]
    assert t["clip_explicit"] is False and t["clip"]["end_beat"] == 11.25
    # automation 全删（三条）→ 删轨（click；strings 在 sidechain.targets 中，
    # 删 strings 会断 sidechain 引用——C9 注记：sidechain 不在 target 语法内）
    for _ in range(3):
        edit_project(path, "delete", target="automation/0")
    edit_project(path, "delete", target="tracks/click")
    assert [t["name"] for t in load_project(path)["tracks"]] == ["kick", "strings"]
    # sections 删
    edit_project(path, "delete", target="sections/0")
    assert load_project(path)["sections"] == []
    # 窗口删
    n = len(load_project(path)["tracks"][0]["notes"])
    edit_project(path, "delete", target="tracks/kick/notes", window="beat:4-6")
    assert len(load_project(path)["tracks"][0]["notes"]) == n - 2
    # master.qc 键删（load 物化全键缺省 None——以落盘原文为准）
    edit_project(path, "delete", target="master/qc/duration_min_s")
    assert "duration_min_s" not in helpers.load_json(path)["master"]["qc"]
    assert load_project(path)["master"]["qc"]["duration_min_s"] is None
    # 窗口无匹配 → 报错（typo 保护）
    with pytest.raises(EditError) as ei:
        edit_project(path, "delete", target="tracks/kick/notes",
                     window="beat:999-1000")
    assert "无匹配" in str(ei.value)


# ---------------------------------------------------------------- copy-of 折叠
@pytest.mark.fast
def test_copy_of_same_carrier_and_cross_form():
    d = helpers.tmpdir("edit_copy")
    path = _copy_project(d, version="2")
    # 目标轨：events-only sf2 轨（XOR 规则——notes 轨不得混 note 事件）
    edit_project(path, "insert", target="tracks", payload={
        "name": "evdst", "engine": "sf2", "instrument": "FluidR3_GM:0:48",
        "events": [{"type": "note_on", "t": 4, "pitch": 60, "vel": 90},
                   {"type": "note_off", "t": 5, "pitch": 60}]})
    # 跨形式：notes → events（四元组 → note_on/off 对；--window 源选择）
    edit_project(path, "insert", target="tracks/evdst/events",
                 copy_of="tracks/kick/notes", window="beat:0-1")
    evs = load_project(path)["tracks"][3]["events"]
    assert [e["type"] for e in evs[2:]] == ["note_on", "note_off"]
    assert evs[2]["pitch"] == 36.0 and evs[2]["vel"] == 120.0
    # 跨形式：events → notes（对 → 四元组，复用 events_to_notes 投影）
    edit_project(path, "insert", target="tracks/click/notes",
                 copy_of="tracks/evdst/events")
    assert [36.0, 0.0, 0.25, 120.0] in load_project(path)["tracks"][2]["notes"]
    # 同载体：段落复制（--window 源选择）
    edit_project(path, "insert", target="tracks/kick/notes",
                 copy_of="tracks/kick/notes", window="beat:0-2")
    p = load_project(path)
    assert len(p["tracks"][0]["notes"]) == 10, "段落复制（源窗口 beat:0-2 = 2 音符）"
    # 轨副本（C7 duplicate 折叠）：名字自动唯一化
    edit_project(path, "insert", target="tracks", copy_of="tracks/kick")
    names = [t["name"] for t in load_project(path)["tracks"]]
    assert "kick copy" in names


@pytest.mark.fast
def test_copy_of_negative_lossy():
    d = helpers.tmpdir("edit_copyneg")
    path = _copy_project(d, version="2")
    # 目标 events-only 轨
    edit_project(path, "insert", target="tracks", payload={
        "name": "evtrack", "engine": "sf2", "instrument": "FluidR3_GM:0:48",
        "events": [{"type": "note_on", "t": 0, "pitch": 60, "vel": 90},
                   {"type": "note_off", "t": 1, "pitch": 60}]})
    # vel=0 源音符 → 拒绝（报源字段路径）
    p = load_project(path)
    p["tracks"][0]["notes"][0][3] = 0
    save_project(path, p)
    with pytest.raises(EditError) as ei:
        edit_project(path, "insert", target="tracks/evtrack/events",
                     copy_of="tracks/kick/notes")
    assert "vel=0" in str(ei.value)
    # 同 pitch 交叠复音 → 拒绝
    p = load_project(path)
    p["tracks"][0]["notes"] = [[60, 0, 1, 100], [60, 0.5, 1, 100]]
    save_project(path, p)
    with pytest.raises(EditError) as ei:
        edit_project(path, "insert", target="tracks/evtrack/events",
                     copy_of="tracks/kick/notes")
    assert "交叠" in str(ei.value)
    # 控制事件 → notes 拒绝
    p = load_project(path)
    p["tracks"][3]["events"].insert(0, {"type": "cc", "t": 0, "ctrl": 1,
                                        "value": 50})
    save_project(path, p)
    with pytest.raises(EditError) as ei:
        edit_project(path, "insert", target="tracks/click/notes",
                     copy_of="tracks/evtrack/events")
    assert "控制事件" in str(ei.value)
    # 单个事件 → notes 拒绝（需 on/off 对）
    with pytest.raises(EditError) as ei:
        edit_project(path, "insert", target="tracks/click/notes",
                     copy_of="tracks/evtrack/events/0")
    assert "单个事件" in str(ei.value)


@pytest.mark.fast
def test_insert_fold_transpose_quantize_scale():
    d = helpers.tmpdir("edit_fold")
    path = _copy_project(d)
    edit_project(path, "insert", target="tracks/kick/notes",
                 payload=[[48, 0.5, 0.5, 100]], transpose=2, scale=2,
                 quantize=0.5)
    p = load_project(path)
    assert p["tracks"][0]["notes"][-1] == [50.0, 1.0, 1.0, 100.0], \
        "固定顺序 transpose→scale→quantize"


# ---------------------------------------------------------------- rename（C9）
@pytest.mark.fast
def test_rename_inline_references():
    d = helpers.tmpdir("edit_ren")
    path = _copy_project(d)
    r = edit_project(path, "set", target="tracks/strings", payload={"name": "pads"})
    p = load_project(path)
    assert [a["target_track"] for a in p["automation"][:2]] == ["pads", "pads"]
    assert p["sidechain"]["targets"] == ["pads"]
    assert p["sidechain"]["trigger_track"] == "kick"
    edit_project(path, "set", target="tracks/kick", payload={"name": "boom"})
    p = load_project(path)
    assert p["sidechain"]["trigger_track"] == "boom"
    assert any("改名" in n for n in r["notes"]) \
        and any("stem" in n for n in r["notes"]), \
        "rename 注记：stem 产物不重命名"
    assert any("重新 show" not in n for n in r["notes"])


@pytest.mark.fast
def test_rename_ref_layout_file_and_undo():
    d = helpers.tmpdir("edit_renref")
    path = _make_ref_project(d)
    before = {f: _disk(os.path.join(d, f))
              for f in ("proj.json", "tracks/kick.json", "tracks/strings.json",
                        "tracks/click.json")}
    r = edit_project(path, "set", target="tracks/kick", payload={"name": "boom"})
    paths = {c["path"] for c in r["changed"]}
    assert "tracks[0].$ref" in paths and "sidechain.trigger_track" in paths
    assert os.path.isfile(os.path.join(d, "tracks", "boom.json")) \
        and not os.path.isfile(os.path.join(d, "tracks", "kick.json")), \
        "$ref 布局下 rename 必须重命名外部轨文件"
    p = load_project(path)
    assert p["tracks"][0]["_ref_src"] == "tracks/boom.json"
    assert p["sidechain"]["trigger_track"] == "boom"
    # undo 恢复（字节级 + 无半状态）
    undo_edit(path)
    assert {f: _disk(os.path.join(d, f)) for f in before} == before
    assert sorted(os.listdir(os.path.join(d, "tracks"))) == \
        ["click.json", "kick.json", "strings.json"]


@pytest.mark.fast
def test_delete_ref_track_leaves_orphan_noted():
    """$ref 工程删轨：外部文件保留在盘（红线④）+ 结果注记；undo 恢复引用。"""
    d = helpers.tmpdir("edit_delref")
    path = _make_ref_project(d)
    edit_project(path, "delete", target="automation/2")   # click 的 pan 引用
    r = edit_project(path, "delete", target="tracks/click")
    assert any("已无引用" in n and "tracks/click.json" in n for n in r["notes"])
    assert os.path.isfile(os.path.join(d, "tracks", "click.json"))
    assert [t["name"] for t in load_project(path)["tracks"]] == ["kick", "strings"]
    undo_edit(path)
    assert load_project(path)["tracks"][2]["name"] == "click", "undo 恢复引用"


# ---------------------------------------------------------------- undo 契约
@pytest.mark.fast
def test_undo_stack_manifest_format_and_restore():
    d = helpers.tmpdir("edit_undo1")
    path = _copy_project(d)
    edit_project(path, "insert", target="tracks/kick/notes",
                 payload=[36, 8, 0.25, 120])
    edit_project(path, "set", target="tracks/kick/notes/0",
                 payload={"vel": 5})
    entries = _manifest(d)
    assert [e["seq"] for e in entries] == [1, 2]
    e = entries[-1]
    assert set(e) == {"seq", "desc", "files", "ts"}
    assert e["desc"].startswith("set") and isinstance(e["ts"], int)
    assert e["files"] and e["files"][0]["path"] == "proj.json"
    assert len(e["files"][0]["sha256"]) == 64
    # 快照目录契约：<seq>.<ts>/ 内含主文件副本
    snap = os.path.join(d, ".bgm_undo", f"{e['seq']}.{e['ts']}")
    assert os.path.isfile(os.path.join(snap, "proj.json"))
    # undo 逐条回退
    undo_edit(path)
    p = load_project(path)
    assert p["tracks"][0]["notes"][0][3] == 120.0, "撤销 set vel"
    undo_edit(path)
    assert len(load_project(path)["tracks"][0]["notes"]) == 8, "撤销 insert"
    with pytest.raises(EditError) as ei:
        undo_edit(path)
    assert "栈为空" in str(ei.value)


@pytest.mark.fast
def test_undo_drift_detection_negative_and_force():
    d = helpers.tmpdir("edit_drift")
    path = _make_ref_project(d)
    edit_project(path, "set", target="tracks/kick/notes/0",
                 payload={"vel": 100})
    # 手改外部轨文件（模拟编辑后漂移）
    ext = os.path.join(d, "tracks", "kick.json")
    obj = helpers.load_json(ext)
    obj["notes"][1][3] = 1
    _write(ext, obj)
    with pytest.raises(EditError) as ei:
        undo_edit(path)
    assert "漂移" in str(ei.value) and "tracks/kick.json" in str(ei.value)
    # --force 覆盖
    r = undo_edit(path, force=True)
    assert r["undone"]["seq"] == 1
    p = load_project(path)
    assert p["tracks"][0]["notes"][0][3] == 120.0, "force 恢复快照内容"


@pytest.mark.fast
def test_undo_write_failure_autorecover():
    d = helpers.tmpdir("edit_ro")
    path = _make_ref_project(d)
    before = {f: _disk(os.path.join(d, f))
              for f in ("proj.json", "tracks/kick.json")}
    os.chmod(os.path.join(d, "tracks"), 0o555)          # 只读目录模拟
    try:
        with pytest.raises(EditError) as ei:
            edit_project(path, "set", target="tracks/kick/notes/0",
                         payload={"vel": 100})
        assert "已从快照" in str(ei.value) and "tracks/kick.json" in str(ei.value), \
            str(ei.value)
        # R3 审查 M-1：回滚成功不得被误报为"自动回滚失败"（误导 undo --force 误撤）
        assert "自动回滚失败" not in str(ei.value), str(ei.value)
    finally:
        os.chmod(os.path.join(d, "tracks"), 0o755)
    assert _manifest(d) == [], "写失败条目必须回滚出栈"
    assert {f: _disk(os.path.join(d, f)) for f in before} == before, \
        "写失败自动恢复：工程文件字节不变"


@pytest.mark.fast
def test_undo_limit_50_fifo():
    d = helpers.tmpdir("edit_lim")
    path = _copy_project(d)
    for i in range(51):
        edit_project(path, "set", target="tracks/kick/notes/0",
                     payload={"vel": 90 + (i % 5)})
    entries = _manifest(d)
    assert len(entries) == 50, "上限 50 FIFO 淘汰"
    assert entries[0]["seq"] == 2 and entries[-1]["seq"] == 51
    snap_dirs = [f for f in sorted(os.listdir(os.path.join(d, ".bgm_undo")))
                 if os.path.isdir(os.path.join(d, ".bgm_undo", f))]
    assert len(snap_dirs) == 50


@pytest.mark.fast
def test_revalidate_no_half_write_and_noop():
    d = helpers.tmpdir("edit_reval")
    path = _copy_project(d)
    blob = _disk(path)
    with pytest.raises(EditError) as ei:
        edit_project(path, "set", target="tracks/kick/notes/0",
                     payload={"vel": 300})
    assert "tracks[0].notes[0].vel" in str(ei.value)
    assert _disk(path) == blob and _manifest(d) == [], "再校验失败不得写盘/入栈"
    # 无变更 → 不入栈
    r = edit_project(path, "set", target="tracks/kick", payload={"gain_db": -3})
    assert r["undo"] is None and r["changed"] == []
    assert _manifest(d) == []


# ---------------------------------------------------------------- split/merge
@pytest.mark.fast
def test_split_merge_wrappers_and_undo():
    d = helpers.tmpdir("edit_sm")
    path = _copy_project(d, version="2")
    orig = _disk(path)
    r = edit_split(path)
    assert r["undo"]["seq"] == 1
    on_disk = helpers.load_json(path)
    assert all(set(t) == {"$ref"} for t in on_disk["tracks"])
    assert sorted(os.listdir(os.path.join(d, "tracks"))) == \
        ["click.json", "kick.json", "strings.json"]
    r = edit_merge(path)
    on_disk = helpers.load_json(path)
    assert all("$ref" not in t for t in on_disk["tracks"]), "merge 内联展开"
    # undo merge → slots 恢复（外部文件仍在）；undo split → 全量回退
    undo_edit(path)
    assert all(set(t) == {"$ref"} for t in helpers.load_json(path)["tracks"])
    undo_edit(path)
    assert _disk(path) == orig, "undo split 后主文件字节复原"
    assert os.listdir(os.path.join(d, "tracks")) == [], \
        "undo split 移除本次创建的外部轨文件（无半状态）"
    # split 失败回滚：tracks-dir 指向文件 → 失败 + 主文件不变
    blocker = os.path.join(d, "blocker")
    open(blocker, "w").close()
    with pytest.raises(EditError) as ei:
        edit_split(path, tracks_dir="blocker")
    assert "split 失败" in str(ei.value)
    assert _disk(path) == orig


# ---------------------------------------------------------------- CLI 端到端
@pytest.mark.fast
def test_cli_modifier_string_parsing_and_half_up():
    """CLI 修饰器=字符串（argparse 不用 type，handler 手写报错口径，PLAN_v3
    §3.4）；quantize 舍入=round-half-up（0.125 格 0.25 → 0.25，banker's 会得 0）。"""
    d = helpers.tmpdir("edit_modcli")
    path = _copy_project(d)
    r = _bgm_edit("insert", path, "--target", "tracks/kick/notes",
                  "--payload", "[60, 0.125, 0.5, 100]", "--transpose", "2",
                  "--scale", "2", "--quantize", "0.25", "--json")
    assert r.returncode == 0, r.stderr[-800:]
    assert json.loads(r.stdout)["changed"]
    note = load_project(path)["tracks"][0]["notes"][-1]
    assert note[0] == 62.0 and note[1] == 0.25 and note[2] == 1.0, \
        "transpose→scale→quantize 固定顺序"
    r = _bgm_edit("insert", path, "--target", "tracks/kick/notes",
                  "--payload", "[60, 0.125, 0.1, 100]", "--quantize", "0.25")
    assert r.returncode == 0, r.stderr[-500:]
    assert load_project(path)["tracks"][0]["notes"][-1][1] == 0.25, \
        "half-up：0.125/0.25=0.5 → 1（banker's round 会得 0.0）"
    r = _bgm_edit("insert", path, "--target", "tracks/kick/notes",
                  "--payload", "[60,0,0.1,100]", "--transpose", "abc")
    assert r.returncode == 1 and "--transpose 必须是数值" in r.stderr
    assert "Traceback" not in r.stderr
    r = _bgm_edit("insert", path, "--target", "tracks/kick/notes",
                  "--payload", "[60,0,0.1,100]", "--scale", "0")
    assert r.returncode == 1 and "必须为正数" in r.stderr


@pytest.mark.fast
def test_cli_negative_parametrized():
    d = helpers.tmpdir("edit_clineg")
    path = _copy_project(d)
    cases = [
        (["insert", path, "--target", "frob/1", "--payload", '{"x":1}'],
         "--target 语法错误"),
        (["set", path, "--target", "tracks/kick/notes", "--window", "beat:x",
          "--payload", '{"vel":1}'], "--window 语法错误"),
        (["set", path, "--target", "tracks/kick", "--payload", "{oops"],
         "--payload JSON 解析失败"),
        (["set", path, "--target", "tracks/kick", "--payload",
          '{"frobnicate":1}'], "tracks/kick.frobnicate"),
        (["insert", path, "--target", "tracks/kick/notes", "--window",
          "beat:0-1", "--payload", "[36,0,0.1,100]"], "仅与 --copy-of"),
        (["move", path, "--target", "tracks/kick/notes/0"], "需要 --to"),
    ]
    for args, expect in cases:
        r = _bgm_edit(*args)
        assert r.returncode == 1, f"{args} 应 exit 1：\n{r.stderr[-800:]}"
        assert "Traceback" not in r.stderr, r.stderr[-800:]
        assert expect in r.stderr, f"{args} 报错缺 {expect!r}：\n{r.stderr[-800:]}"


@pytest.mark.fast
def test_cli_ref_source_annotation_negative():
    d = helpers.tmpdir("edit_cliref")
    path = _make_ref_project(d)
    r = _bgm_edit("set", path, "--target", "tracks/kick/notes/0",
                  "--payload", '{"vel":300}')
    assert r.returncode == 1
    assert "Traceback" not in r.stderr
    assert "tracks[0].notes[0].vel" in r.stderr
    assert "(tracks/kick.json)" in r.stderr, "$ref 工程报错必须带来源标注"


@pytest.mark.fast
def test_cli_positive_json_and_undo_list():
    d = helpers.tmpdir("edit_clipos")
    path = _copy_project(d)
    r = _bgm_edit("insert", path, "--target", "tracks/kick/notes",
                  "--payload", "[36,8,0.25,120]", "--json")
    assert r.returncode == 0, r.stderr[-800:]
    out = json.loads(r.stdout)
    assert out["undo"]["seq"] == 1
    assert out["changed"] and \
        all(set(c) == {"path", "before", "after"} for c in out["changed"])
    assert len(load_project(path)["tracks"][0]["notes"]) == 9
    # undo --list（text + json）
    r = _bgm_edit("undo", path, "--list")
    assert r.returncode == 0 and "#1" in r.stdout and "insert" in r.stdout
    r = _bgm_edit("undo", path, "--list", "--json")
    entries = json.loads(r.stdout)["entries"]
    assert len(entries) == 1 and entries[0]["seq"] == 1
    # undo 正例（CLI 口径）
    r = _bgm_edit("undo", path, "--json")
    assert r.returncode == 0, r.stderr[-800:]
    assert json.loads(r.stdout)["undone"]["seq"] == 1
    assert len(load_project(path)["tracks"][0]["notes"]) == 8
    # 空栈 undo → exit 1 + stderr
    r = _bgm_edit("undo", path)
    assert r.returncode == 1 and "栈为空" in r.stderr


@pytest.mark.fast
def test_cli_crash_injection_atomic_window():
    """BGM_CRASH_AFTER=2：rename 写序 = [新外部轨文件, 主文件, 旧文件删除]——
    第 2 次文件写（主文件 rename）后 os._exit(137)：快照已入栈、旧文件残留
    半状态；undo 恢复后无半状态（字节级复原）。"""
    d = helpers.tmpdir("edit_crash")
    path = _make_ref_project(d)
    before = {f: _disk(os.path.join(d, f))
              for f in ("proj.json", "tracks/kick.json", "tracks/strings.json",
                        "tracks/click.json")}
    r = _bgm_edit("set", path, "--target", "tracks/kick",
                  "--payload", '{"name":"boom"}',
                  env={"BGM_CRASH_AFTER": "2"})
    assert r.returncode == 137, f"crash 钩子应 os._exit(137)：{r.returncode}"
    # 快照已入栈（manifest 先于 apply 提交）
    entries = _manifest(d)
    assert len(entries) == 1 and entries[0]["files"][0]["path"] == "proj.json"
    # 半状态：主文件与新外部文件已 rename，旧文件未删
    assert os.path.isfile(os.path.join(d, "tracks", "boom.json"))
    assert os.path.isfile(os.path.join(d, "tracks", "kick.json"))
    assert helpers.load_json(path)["tracks"][0] == {"$ref": "tracks/boom.json"}
    # undo（漂移检测应通过：boom.json 已达成编辑后哈希）恢复无半状态
    r = _bgm_edit("undo", path)
    assert r.returncode == 0, r.stderr[-800:]
    assert {f: _disk(os.path.join(d, f)) for f in before} == before, \
        "恢复后必须字节级复原（无半状态）"
    assert sorted(os.listdir(os.path.join(d, "tracks"))) == \
        ["click.json", "kick.json", "strings.json"]
    load_project(path)


# ---------------------------------------------------------------- eden_v2 十种真实修改
@pytest.mark.fast
def test_eden_v2_ten_real_edits():
    src = os.path.join(helpers.OUT_DIR, "eden_v2", "project.json")
    if not os.path.isfile(src):
        pytest.skip("out/eden_v2 交付物不在位（主树合流后全量跑，判据③）")
    d = helpers.tmpdir("edit_eden")
    path = os.path.join(d, "project.json")
    shutil.copyfile(src, path)                           # 临时副本（红线④）
    src_sha = helpers.sha256(src)
    proj = helpers.load_json(path)
    t0 = proj["tracks"][0]["name"]
    t1 = proj["tracks"][1]["name"]
    sf2_track = next((t["name"] for t in proj["tracks"]
                      if t["engine"] in ("sf2", "sfz")), None)

    def run_ok(*args):
        r = _bgm_edit(*args, "--json")
        assert r.returncode == 0, f"{args}\n{r.stderr[-1200:]}"
        out = json.loads(r.stdout)
        assert out["changed"], f"{args} 必须产生结构化 diff"
        return out

    run_ok("set", path, "--target", "meta/spec_version", "--payload", '"2"')
    run_ok("insert", path, "--target", f"tracks/{t0}/notes",
           "--copy-of", f"tracks/{t0}/notes", "--window", "beat:0-4")
    run_ok("set", path, "--target", f"tracks/{t0}/notes",
           "--window", "beat:0-100000", "--transpose", "1")
    run_ok("set", path, "--target", f"tracks/{t0}/notes",
           "--window", "beat:0-100000", "--quantize", "0.25")
    run_ok("set", path, "--target", f"tracks/{t0}/notes",
           "--window", "beat:0-100000", "--scale", "2")
    # 窗删（选一个真实存在的拍点窗口）
    beats = sorted(n[1] for n in load_project(path)["tracks"][0]["notes"])
    run_ok("delete", path, "--target", f"tracks/{t0}/notes",
           "--window", f"beat:{beats[0]}-{beats[0] + 0.01}")
    # clip 改
    end = load_project(path)["tracks"][0]["clip"]["end_beat"]
    run_ok("set", path, "--target", f"tracks/{t0}/clip",
           "--payload", json.dumps({"end_beat": end + 1}))
    # automation 增（选未占用的 (轨, 参数) 组合）+ 删
    used = {(a["target_track"], a["param"])
            for a in load_project(path)["automation"]}
    param = next(p for p in ("gain_db", "pan", "lowpass_hz")
                 if (t0, p) not in used)
    run_ok("insert", path, "--target", "automation",
           "--payload", json.dumps({"target_track": t0, "param": param,
                                    "points": [[0, 0], [1, 1]],
                                    "curve": "linear"}))
    idx = next(i for i, a in enumerate(load_project(path)["automation"])
               if a["target_track"] == t0 and a["param"] == param)
    run_ok("delete", path, "--target", f"automation/{idx}")
    # 事件插入（sf2/sfz 轨；v2 已就位）
    run_ok("insert", path, "--target", f"tracks/{sf2_track}/events",
           "--payload", '{"type":"cc","t":0,"ctrl":1,"value":50}')
    # 轨增删（copy-of duplicate + 删除）
    run_ok("insert", path, "--target", "tracks", "--copy-of", f"tracks/{t1}")
    run_ok("delete", path, "--target", f"tracks/{t1} copy")
    # meta 改
    run_ok("set", path, "--target", "meta/bpm",
           "--payload", str(float(proj["meta"]["bpm"]) + 5))
    assert helpers.sha256(src) == src_sha, "红线④：正本必须逐字节不动"


# ---------------------------------------------- R3 审查修复回归锁（主线补）
@pytest.mark.fast
def test_nan_infinity_rejected_everywhere():
    """R3 审查 M-2：裸 NaN/Infinity 不得经 edit/payload/修饰器写入工程。"""
    d = helpers.tmpdir("edit_nan")
    path = _copy_project(d)
    # CLI --payload 的 NaN / Infinity 字面量（parse_constant 拒）
    for bad in ('{"vel": NaN}', '{"vel": Infinity}', '{"bpm": -Infinity}'):
        r = _bgm_edit("set", path, "--target", "meta", "--payload", bad)
        assert r.returncode != 0 and "非法常量" in r.stderr, r.stderr[-400:]
    # --transpose nan（_parse_num isfinite 拒）
    r = _bgm_edit("set", path, "--target", "tracks/kick/notes",
                  "--transpose", "nan")
    assert r.returncode != 0 and "有限数值" in r.stderr, r.stderr[-400:]
    # 工程层再校验兜底：直接构造 NaN 字段文件 → load_project 拒（json 字面量
    # 解析侧由 edit 层拒绝；此处验证 _num/_event_num 的 isfinite 面）
    proj = helpers.load_json(MINIMAL)
    proj["meta"]["spec_version"] = "2"
    proj["tracks"][0]["notes"] = [[36, float("nan"), 0.25, 100]]
    _write(path, proj)
    with pytest.raises(ProjectError):
        load_project(path)


@pytest.mark.fast
def test_xor_gate_key_absent_dumb_track_rejected():
    """R3 审查 M-3：events 键缺席也必须过 XOR 门——notes 删空/插空轨拒绝。"""
    d = helpers.tmpdir("edit_xor")
    path = _copy_project(d, version="2")
    # ① edit 删空整轨 notes（窗口全覆盖）→ 再校验必须拒绝且不落盘
    r = _bgm_edit("delete", path, "--target", "tracks/kick/notes",
                  "--window", "beat:0-100000")
    assert r.returncode != 0 and "恰有其一" in r.stderr, r.stderr[-400:]
    assert "notes" in helpers.load_json(path)["tracks"][0], "拒绝不得落盘"
    # ② insert 整轨 notes=[]（无 events）→ 拒绝
    r = _bgm_edit("insert", path, "--target", "tracks",
                  "--payload", '{"name":"muted","engine":"synth",'
                               '"instrument":"kick","notes":[]}')
    assert r.returncode != 0 and "恰有其一" in r.stderr, r.stderr[-400:]
    # ③ 装载层直接拒：v2 文件 notes=[] 且无 events
    proj = helpers.load_json(MINIMAL)
    proj["meta"]["spec_version"] = "2"
    proj["tracks"][0]["notes"] = []
    p2 = os.path.join(d, "dumb.json")
    _write(p2, proj)
    with pytest.raises(ProjectError) as ei:
        load_project(p2)
    assert "恰有其一" in str(ei.value)


@pytest.mark.fast
def test_ref_project_show_diff_finds_flat_snapshot():
    """R3 审查 M-4：$ref 工程编辑后 show diff 必须命中外部轨快照（写侧扁平名
    tracks__x.json，读侧回退查找）——此前恒 snapshot_missing。"""
    from bgm_gen.core.show import show_diff
    d = helpers.tmpdir("edit_snap")
    path = _make_ref_project(d)
    edit_project(path, "set", target="tracks/kick/notes/0",
                 payload={"vel": 111})
    out = show_diff(path, as_json=True)
    states = {f["path"]: f["state"] for f in out["files"]}
    assert "tracks/kick.json" in states, states
    assert states["tracks/kick.json"] == "changed", states
    assert "snapshot_missing" not in states.values(), states
    undo_edit(path)
    out2 = show_diff(path, as_json=True)
    assert out2["stack_size"] == 0
