"""④双布局 + 增量渲染测试（PLAN_v3 §6 test_dual_layout 行 / §5 R1-W2 判据）。

[fast]  $ref 装载正例与拒绝负例（绝对路径/嵌套/兄弟字段/v1 版本门）；
        split↔merge 语义幂等；save 连续两次字节稳定 + 键白名单；
        SameFileError 回归（工程正本在产物目录）；minimal 规模 split 渲染位级
        一致 + $ref 快照内联展开独立重渲；增量专项（子进程 env_extra 覆写
        BGM_NO_INCREMENTAL=0 + --incremental）：命中跳过/改一轨仅该轨重渲/
        仅改 bpm 未命中/缺省渲染与全量逐位一致/边车丢失不假命中/
        BGM_NO_INCREMENTAL=1 硬闸赢过 flag；conftest env 注入与 marker 纪律
        元测试。
[slow]  eden_v2 全量 split 位级一致（out/eden_v2/project.json 缺失时 skip）。

渲染输出一律写 helpers.tmpdir(...)，绝不用仓库 out/ 作输出。
"""

import json
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers                                              # noqa: E402

helpers.in_proc_env()
from bgm_gen.core.project import (ProjectError, load_project, merge_layout,  # noqa: E402
                                  save_project, split_layout)

MINIMAL = os.path.join(helpers.EXAMPLES_DIR, "minimal.json")
ANCHOR_MIX = os.path.join(helpers.OUT_DIR, "minimal", "mix.flac")
EDEN_V2_DIR = os.path.join(helpers.OUT_DIR, "eden_v2")


def _write(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _bgm_render(proj_path, out_dir, *flags, env=None, timeout=300):
    return helpers.run([helpers.py3(), "-m", "bgm_gen", "render",
                        proj_path, "-o", out_dir, *flags],
                       cwd=helpers.WORKSPACE, timeout=timeout, env_extra=env)


def _copy_minimal(d, name="proj.json"):
    p = os.path.join(d, name)
    _write(p, helpers.load_json(MINIMAL))
    return p


# 微工程（v3.3 fast 预算找补）：增量用例组专属渲染基座——2 轨纯 synth
# （kick/lead，无 sf2/fluidsynth 子进程）× 8 拍 @120 = 4s，实测单渲 1.7s vs
# minimal 2.8s（增量组 7 渲 × ~1.1s ≈ 回收 7.8s，预算 ≤75s 找补）。位级锚点
# **自锚**：冷跑增量渲染 = 全量渲染事实（与缺省关闭渲染逐位对拍锁定），
# 不再依赖 out/minimal/mix.flac（新机无需先渲 minimal）。R1 增量三断言
# 语义不变（命中/仅改 bpm 未命中/默认关闭位级一致）。
MICRO = {
    "name": "micro",
    "meta": {"spec_version": "1", "sr": 44100, "channels": 2, "bpm": 120,
             "key": "D minor", "timesig": "4/4", "seed": 7,
             "duration_hint": 4.0},
    "sections": [],
    "tracks": [
        {"name": "kick", "engine": "synth", "instrument": "kick",
         "notes": [[36, 0, 0.25, 120], [36, 1, 0.25, 110], [36, 2, 0.25, 120],
                   [36, 3, 0.25, 110], [36, 4, 0.25, 120], [36, 5, 0.25, 110],
                   [36, 6, 0.25, 120], [36, 7, 0.25, 110]],
         "gain_db": -3, "pan": 0, "clip": {"start_beat": 0, "end_beat": 8},
         "bus": []},
        {"name": "lead", "engine": "synth", "instrument": "lead",
         "notes": [[60, 0, 2, 100], [64, 2, 2, 95], [67, 4, 2, 100],
                   [72, 6, 2, 95]],
         "gain_db": -6, "pan": 0.2, "clip": {"start_beat": 0, "end_beat": 8},
         "bus": []},
    ],
    "master": {"target_lufs": -14, "true_peak_db": -1, "chain": "loudnorm",
               "qc": {"silent_window_max_s": 1.0, "bpm_tol_pct": None,
                      "key_top2": None, "hf_ratio_max": None}},
}


def _copy_micro(d, name="proj.json"):
    p = os.path.join(d, name)
    _write(p, MICRO)
    return p


def _make_ref_project(d, ref, mutate_slot=None, external=None, version="2"):
    """构造 v2 工程：tracks[2] 槽位换成 $ref（或 mutate_slot 自定），
    外部文件默认 = minimal 轨 2 的原始 dict（可 external 覆盖）。"""
    proj = helpers.load_json(MINIMAL)
    proj["meta"]["spec_version"] = version
    ext = external if external is not None else proj["tracks"][2]
    os.makedirs(os.path.join(d, "tracks"), exist_ok=True)
    with open(os.path.join(d, "tracks", "vla.json"), "w", encoding="utf-8") as f:
        json.dump(ext, f, ensure_ascii=False)
    if mutate_slot is not None:
        proj["tracks"][2] = mutate_slot
    else:
        proj["tracks"][2] = {"$ref": ref}
    ppath = os.path.join(d, "proj.json")
    _write(ppath, proj)
    return ppath


# ----------------------------------------------------------------- $ref 装载
@pytest.mark.fast
def test_ref_load_positive_and_source_annotation():
    d = helpers.tmpdir("dl_ref_pos")
    ppath = _make_ref_project(d, "tracks/vla.json")
    loaded = load_project(ppath)
    assert loaded["meta"]["spec_version"] == "2"
    assert loaded["_uses_refs"] is True
    t = loaded["tracks"][2]
    assert t["name"] == "click" and t["engine"] == "sample"
    assert t["_ref_src"] == "tracks/vla.json"          # 来源映射留档（show 用）
    # 校验报错路径附加来源标注：破坏外部轨音符 → 路径带 (tracks/vla.json)
    ext = helpers.load_json(MINIMAL)["tracks"][2]
    ext["notes"] = [[36, 0, 0.1]]                       # 3 元数组（非法）
    with open(os.path.join(d, "tracks", "vla.json"), "w", encoding="utf-8") as f:
        json.dump(ext, f, ensure_ascii=False)
    with pytest.raises(ProjectError) as ei:
        load_project(ppath)
    assert "tracks[2].notes[0] (tracks/vla.json)" in str(ei.value)


@pytest.mark.fast
def test_ref_reject_absolute_path():
    d = helpers.tmpdir("dl_ref_abs")
    ppath = _make_ref_project(d, "/etc/passwd")
    with pytest.raises(ProjectError) as ei:
        load_project(ppath)
    assert "tracks[2].$ref" in str(ei.value) and "绝对路径" in str(ei.value)


@pytest.mark.fast
def test_ref_reject_nested_ref():
    d = helpers.tmpdir("dl_ref_nest")
    ppath = _make_ref_project(d, "tracks/vla.json",
                              external={"$ref": "tracks/deeper.json"})
    with pytest.raises(ProjectError) as ei:
        load_project(ppath)
    assert "tracks[2].$ref" in str(ei.value) and "嵌套" in str(ei.value)


@pytest.mark.fast
def test_ref_reject_sibling_fields():
    d = helpers.tmpdir("dl_ref_sib")
    ppath = _make_ref_project(d, "tracks/vla.json",
                              mutate_slot={"$ref": "tracks/vla.json", "name": "x"})
    with pytest.raises(ProjectError) as ei:
        load_project(ppath)
    assert "tracks[2].$ref" in str(ei.value) and "兄弟字段" in str(ei.value)


@pytest.mark.fast
@pytest.mark.parametrize("key,val", [
    ("$ref", {"$ref": "tracks/x.json"}),
    ("events", []),
    ("params", {}),
    ("bend_range", 2),
], ids=["ref", "events", "params", "bend_range"])
def test_v1_version_gate_rejects_v2_track_keys(key, val):
    """版本前置门（PLAN_v3 §3.3/R3-P7）：v1 轨带 v2 专属键一律拒绝，报字段路径。"""
    d = helpers.tmpdir(f"dl_v1gate_{key}")
    proj = helpers.load_json(MINIMAL)                    # spec_version 缺省 = "1"
    proj["tracks"][0][key] = val
    ppath = os.path.join(d, "proj.json")
    _write(ppath, proj)
    with pytest.raises(ProjectError) as ei:
        load_project(ppath)
    assert f"tracks[0].{key}" in str(ei.value)
    assert 'spec_version="1"' in str(ei.value)


@pytest.mark.fast
def test_v2_events_params_accepted_v1_still_rejected():
    """版本门语义（R2 联动改写，原"v2 仍拒"用例作废）：v2+events/params 已被接受
    （load 通过、字段入规范 dict）；v1+events/params 仍拒（版本前置门——逐键负例见
    test_v1_version_gate_rejects_v2_track_keys）。"""
    d = helpers.tmpdir("dl_v2_accept")
    proj = helpers.load_json(MINIMAL)
    proj["meta"]["spec_version"] = "2"
    proj["tracks"][1]["events"] = [{"type": "cc", "t": 0, "ctrl": 7, "value": 100}]
    proj["tracks"][1]["params"] = {"gain": 0.5}
    ppath = os.path.join(d, "proj.json")
    _write(ppath, proj)
    loaded = load_project(ppath)
    assert loaded["tracks"][1]["events"][0]["type"] == "cc"
    assert loaded["tracks"][1]["params"] == {"gain": 0.5}

    v1 = helpers.load_json(MINIMAL)                 # spec_version 缺省 = "1"
    v1["tracks"][1]["events"] = [{"type": "cc", "t": 0, "ctrl": 7, "value": 100}]
    p1 = os.path.join(d, "v1.json")
    _write(p1, v1)
    with pytest.raises(ProjectError) as ei:
        load_project(p1)
    assert "tracks[1].events" in str(ei.value)
    assert 'spec_version="1"' in str(ei.value)


# ------------------------------------------------------ split/merge/save
@pytest.mark.fast
def test_split_merge_idempotent_and_save_byte_stable():
    d = helpers.tmpdir("dl_split_merge")
    p1 = load_project(MINIMAL)
    p1["meta"]["spec_version"] = "2"                     # 纯 split↔merge 幂等测 v2 起手
    orig_path = os.path.join(d, "orig.json")
    save_project(orig_path, p1)
    s1 = open(orig_path, "rb").read()
    save_project(orig_path, p1)
    assert open(orig_path, "rb").read() == s1, "save 连续两次必须字节相等"
    assert b"bank_explicit" not in s1 and b"program_explicit" not in s1 \
        and b"clip_explicit" not in s1 and b"_ref_src" not in s1, \
        "派生键必须被白名单过滤"

    proj_path = os.path.join(d, "proj.json")
    save_project(proj_path, p1)
    split_layout(p1, proj_path, os.path.join(d, "tracks"))
    assert all(isinstance(t, dict) and set(t) == {"$ref"} for t in p1["tracks"])
    assert p1["meta"]["spec_version"] == "2"
    for name in ("kick", "strings", "click"):
        assert os.path.isfile(os.path.join(d, "tracks", f"{name}.json"))
        blob = open(os.path.join(d, "tracks", f"{name}.json"), encoding="utf-8").read()
        assert "_ref_src" not in blob and "bank_explicit" not in blob

    # 磁盘正本 = $ref 布局；装载展开后语义与拆分前逐位同（经 save 字节比对）
    save_project(proj_path, p1)
    on_disk = helpers.load_json(proj_path)
    assert on_disk["meta"]["spec_version"] == "2"
    assert all(set(t) == {"$ref"} for t in on_disk["tracks"])
    p2 = load_project(proj_path)
    assert p2["_uses_refs"] is True
    assert [t.get("_ref_src") for t in p2["tracks"]] == [
        "tracks/kick.json", "tracks/strings.json", "tracks/click.json"]

    # 内存级 split→merge 幂等：merge 反向展开后 save 字节 == 原工程 save 字节
    merge_layout(p1)
    merged_path = os.path.join(d, "merged.json")
    save_project(merged_path, p1)
    assert open(merged_path, "rb").read() == open(orig_path, "rb").read()

    # merge 缺解析基准（_ref_base_dir）→ 清晰报错
    with pytest.raises(ProjectError) as ei:
        merge_layout({"tracks": [{"$ref": "tracks/x.json"}]})
    assert "_ref_base_dir" in str(ei.value)


@pytest.mark.fast
def test_split_render_bitwise_and_snapshot_selfcontained():
    helpers.require_minimal_assets()
    """v1 工程 split（自动升 v2）渲染位级一致 + $ref 快照内联展开独立重渲。

    版本门收窄（红队复审 m-1）：渲染成功性与 $ref 快照自包含结构断言无条件执行；
    仅末段两条位级锚点断言（ANCHOR_MIX 由冻结版 ffmpeg/fluidsynth 产出）受版本
    门控（用户裁定 2026-10-11）——漂移显式 skip，匹配时照常严格执行。"""
    d = helpers.tmpdir("dl_split_render")
    p1 = load_project(MINIMAL)                           # v1
    proj_path = os.path.join(d, "proj.json")
    save_project(proj_path, p1)
    split_layout(p1, proj_path, os.path.join(d, "tracks"))
    save_project(proj_path, p1)                          # v2 $ref 布局落盘

    out1 = os.path.join(d, "out1")
    r = _bgm_render(proj_path, out1, "-q")
    assert r.returncode == 0, r.stderr[-1500:]

    # 快照 = 内联展开单文件（正本不动）；独立重渲位级一致（M6 判据）
    assert "$ref" in open(proj_path, encoding="utf-8").read()   # 正本仍是 $ref 布局
    snap = helpers.load_json(os.path.join(out1, "project.json"))
    assert snap["meta"]["spec_version"] == "2"
    assert len(snap["tracks"]) == 3
    assert all(isinstance(t, dict) and "$ref" not in t for t in snap["tracks"])
    snap_path = os.path.join(d, "snap.json")
    _write(snap_path, snap)
    out2 = os.path.join(d, "out2")
    r2 = _bgm_render(snap_path, out2, "-q")
    assert r2.returncode == 0, r2.stderr[-1500:]

    # 位级锚点段（仅此两断言受版本门）
    helpers.skip_if_binary_version_drift("fluidsynth", "ffmpeg")
    assert helpers.sha256(os.path.join(out1, "mix.flac")) == helpers.sha256(ANCHOR_MIX), \
        "split 布局渲染与内联布局位级漂移"
    assert helpers.sha256(os.path.join(out2, "mix.flac")) == helpers.sha256(ANCHOR_MIX)


@pytest.mark.fast
def test_merge_save_preserves_explicit_bank_program_bitwise(tmp_path):
    helpers.require_minimal_assets()
    """R1 审查官 A·D-1 回归锁：显式 bank/program 经 split→merge→save 不得
    丢失（merge 展开的外部文件 dict 无派生键，曾误判"未显式"而被丢弃，
    导致 merge 布局渲染位级漂移）。"""
    proj = helpers.load_json(MINIMAL)
    proj["tracks"][1]["bank"] = 128
    proj["tracks"][1]["program"] = 0
    src = tmp_path / "src.json"
    _write(str(src), proj)
    out1 = tmp_path / "inline"
    r = _bgm_render(str(src), str(out1), "-q")
    assert r.returncode == 0, r.stderr[-1500:]
    sha1 = helpers.sha256(str(out1 / "mix.flac"))

    loaded = load_project(str(src))
    split_layout(loaded, str(src), str(tmp_path / "tracks"))
    save_project(str(src), loaded)
    reloaded = load_project(str(src))          # $ref 布局再装载
    merge_layout(reloaded)
    merged = tmp_path / "merged.json"
    save_project(str(merged), reloaded)
    blob = merged.read_text(encoding="utf-8")
    assert '"bank": 128' in blob and '"program": 0' in blob, \
        "merge→save 不得丢失显式 bank/program（D-1）"
    out2 = tmp_path / "merged"
    r2 = _bgm_render(str(merged), str(out2), "-q")
    assert r2.returncode == 0, r2.stderr[-1500:]
    assert helpers.sha256(str(out2 / "mix.flac")) == sha1, \
        "merge 布局渲染与内联布局位级漂移（显式 bank/program 丢失）"


# ------------------------------------------------------ SameFileError + 硬闸
@pytest.mark.fast
def test_samefile_regression_and_hard_gate():
    helpers.require_minimal_assets()
    """①工程正本在产物目录：快照复制不炸（SameFileError 修复）、正本不坏、
    产物正确；②BGM_NO_INCREMENTAL=1 硬闸赢过 --incremental（读点 render_project 内）。
    版本门收窄（红队复审 m-1）：SameFileError/硬闸/正本不坏断言无条件执行，仅
    末段 ANCHOR_MIX 位级锚点受版本门控（用户裁定 2026-10-11）。"""
    d = helpers.tmpdir("dl_samefile")
    out_dir = os.path.join(d, "out")
    os.makedirs(out_dir)
    proj_path = os.path.join(out_dir, "project.json")    # 正本在产物目录（eden 场景）
    shutil.copyfile(MINIMAL, proj_path)
    before = open(proj_path, "rb").read()
    r = _bgm_render(proj_path, out_dir, "--incremental")  # env 继承 BGM_NO_INCREMENTAL=1
    assert r.returncode == 0, r.stderr[-1500:]
    assert "BGM_NO_INCREMENTAL=1 硬闸" in r.stdout, "硬闸日志缺失"
    assert "skip re-render" not in r.stdout, "硬闸下不得有跳过重渲"
    assert open(proj_path, "rb").read() == before, "快照不得改写正本"
    # 位级锚点段（仅此断言受版本门）
    helpers.skip_if_binary_version_drift("fluidsynth", "ffmpeg")
    assert helpers.sha256(os.path.join(out_dir, "mix.flac")) == helpers.sha256(ANCHOR_MIX)


# ------------------------------------------------------ 增量渲染专项
@pytest.mark.fast
def test_incremental_hit_miss_default_bitwise_bpm_miss():
    """改一轨仅该轨重渲；缺省渲染与全量逐位一致；增量结果与全量位级一致；
    仅改 meta.bpm → 全部未命中（bpm 在每轨 payload 内）。

    v3.3 预算找补：渲染基座 minimal → 自建 2 轨 synth 微工程（MICRO），
    位级锚点自锚（冷跑增量 == 缺省全量逐位对拍）；R1 增量三断言语义不变。"""
    d = helpers.tmpdir("dl_inc")
    p = _copy_micro(d)
    out_inc = os.path.join(d, "out_inc")
    env0 = {"BGM_NO_INCREMENTAL": "0"}

    # 冷跑（无边车）：全量渲染
    r1 = _bgm_render(p, out_inc, "--incremental", env=env0)
    assert r1.returncode == 0, r1.stderr[-1500:]
    assert "skip re-render" not in r1.stdout
    mix_cold = helpers.sha256(os.path.join(out_inc, "mix.flac"))
    kick1 = helpers.sha256(os.path.join(out_inc, "stems", "kick.synth.wav"))
    lead1 = helpers.sha256(os.path.join(out_inc, "stems", "lead.synth.wav"))

    # 缺省（无 flag，env 硬闸 1）渲染 → 与增量冷跑逐位一致（自锚锚点）
    out_def = os.path.join(d, "out_def")
    r_def = _bgm_render(p, out_def)
    assert r_def.returncode == 0, r_def.stderr[-1500:]
    assert "skip re-render" not in r_def.stdout
    assert helpers.sha256(os.path.join(out_def, "mix.flac")) == mix_cold

    # 改一轨（kick 力度）→ 仅该轨重渲，其余跳过
    proj = helpers.load_json(p)
    proj["tracks"][0]["notes"][0][3] = 127
    _write(p, proj)
    r3 = _bgm_render(p, out_inc, "--incremental", env=env0)
    assert r3.returncode == 0, r3.stderr[-1500:]
    assert "kick（synth）skip" not in r3.stdout
    assert r3.stdout.count("skip re-render") == 1
    assert "lead（synth）skip" in r3.stdout
    assert helpers.sha256(os.path.join(out_inc, "stems", "kick.synth.wav")) != kick1
    assert helpers.sha256(os.path.join(out_inc, "stems", "lead.synth.wav")) == lead1
    sc = helpers.load_json(os.path.join(out_inc, "stems", "kick.synth.render.json"))
    assert sc["content_hash"] and sc["summary"]["sr"] == 44100 \
        and {"channels", "duration_s", "rms_db"} <= set(sc["summary"])

    # 增量结果与全量位级一致（确定性引擎锁）
    out_full2 = os.path.join(d, "out_full2")
    r4 = _bgm_render(p, out_full2, "-q")
    assert r4.returncode == 0, r4.stderr[-1500:]
    assert helpers.sha256(os.path.join(out_full2, "mix.flac")) == \
        helpers.sha256(os.path.join(out_inc, "mix.flac"))

    # 仅改 meta.bpm → 每轨 payload 都变 → 全部未命中（M2/F7 负例）
    proj = helpers.load_json(p)
    proj["meta"]["bpm"] = 150.0
    _write(p, proj)
    r5 = _bgm_render(p, out_inc, "--incremental", env=env0)
    assert r5.returncode == 0, r5.stderr[-1500:]
    assert "skip re-render" not in r5.stdout, "仅改 bpm 不得命中任何边车"


@pytest.mark.fast
def test_incremental_missing_sidecar_rerenders():
    """崩溃窗口（R3-P4）：stem 已写、边车丢失 → 重跑必须重渲不假命中。

    v3.3 预算找补：基座同换 MICRO（见上）。"""
    d = helpers.tmpdir("dl_inc_sidecar")
    p = _copy_micro(d)
    out_dir = os.path.join(d, "out")
    env0 = {"BGM_NO_INCREMENTAL": "0"}
    r1 = _bgm_render(p, out_dir, "--incremental", env=env0)
    assert r1.returncode == 0, r1.stderr[-1500:]
    assert "skip re-render" not in r1.stdout
    kick_path = os.path.join(out_dir, "stems", "kick.synth.wav")
    sidecar_path = os.path.join(out_dir, "stems", "kick.synth.render.json")
    kick_sha = helpers.sha256(kick_path)
    os.remove(sidecar_path)                              # 边车丢失
    with open(kick_path, "wb") as f:                     # 并写坏 stem：假命中则坏数据留存
        f.write(b"garbage")
    r2 = _bgm_render(p, out_dir, "--incremental", env=env0)
    assert r2.returncode == 0, r2.stderr[-1500:]
    assert "kick（synth）skip" not in r2.stdout, "边车丢失必须重渲，不得假命中"
    assert "lead（synth）skip" in r2.stdout              # 其余轨仍正常命中
    assert helpers.sha256(kick_path) == kick_sha, "重渲必须恢复正确 stem 字节"
    assert os.path.isfile(sidecar_path)


# ------------------------------------------------------ conftest 纪律元测试
@pytest.mark.fast
def test_conftest_env_injection_active():
    """conftest 注入 BGM_NO_INCREMENTAL=1（session 级），子进程经 helpers.run 继承。"""
    r = helpers.run([helpers.py3(), "-c",
                     "import os;print(os.environ.get('BGM_NO_INCREMENTAL'))"],
                    cwd=helpers.WORKSPACE)
    assert r.stdout.strip() == "1"


@pytest.mark.fast
def test_marker_discipline_collection_fails_on_unmarked():
    """收集期纪律：无 fast/slow/service 三档 marker 的用例收集期 fail（P-14 元测试）。"""
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    tmpfile = os.path.join(tests_dir, f"_zz_nomark_{os.getpid()}.py")
    try:
        with open(tmpfile, "w", encoding="utf-8") as f:
            f.write("def test_unmarked_should_fail():\n    assert True\n")
        r = helpers.run([helpers.py3(), "-m", "pytest", tmpfile, "-q",
                         "-p", "no:cacheprovider"],
                        cwd=helpers.WORKSPACE, timeout=120)
        assert r.returncode != 0, "无 marker 用例收集期应当 fail"
        assert "fast/slow/service" in r.stdout + r.stderr, \
            f"报错未给三档 marker 提示：\n{(r.stdout + r.stderr)[-1500:]}"
    finally:
        if os.path.isfile(tmpfile):
            os.remove(tmpfile)


# ------------------------------------------------------ [slow] eden_v2 全量
@pytest.mark.slow
def test_eden_v2_split_bitwise():
    src = os.path.join(EDEN_V2_DIR, "project.json")
    if not os.path.isfile(src):
        pytest.skip("out/eden_v2 交付物不在位（主树合并后全量跑）")
    d = helpers.tmpdir("dl_eden_v2")
    p1 = load_project(src)
    proj_path = os.path.join(d, "proj.json")
    save_project(proj_path, p1)
    split_layout(p1, proj_path, os.path.join(d, "tracks"))
    save_project(proj_path, p1)
    out_dir = os.path.join(d, "out")
    r = _bgm_render(proj_path, out_dir, "-q", timeout=600)
    assert r.returncode == 0, r.stderr[-1500:]
    got = helpers.sha256(os.path.join(out_dir, "mix.flac"))
    anchor = helpers.sha256(os.path.join(EDEN_V2_DIR, "mix.flac"))
    assert got == anchor, f"eden_v2 split 复现漂移：{got} ≠ 交付 {anchor}"
