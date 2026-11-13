"""描述符系统测试（PLAN_v3 §3.1 / §6 test_descriptors 行；v3 R1-W1）。

[fast]   ①描述符 schema 自洽（7 份可加载、字段齐、参数 id 唯一、events 消费矩阵、
          invariants 注册、coverage 标注）；②validate_payload 纯单元正负例（不渲染）；
          ③行为探针（§3.1 探针语义）：对每个声明参数至少一组赋值渲染，断言
          exit 0 + 出参契约字段齐 + 产物非零字节 + determinism 分级正确，
          取值 {default, 边界近点}，program 相关按 invariants 合法组合——覆盖
          synth/sf2/sfz/sample/master；④doctor 一致性（severity warn/error 划界）；
          ⑤content_hash_key 键组装锁定；⑥`bgm show engines --json` 7 引擎字段齐。
[slow]   demucs 探针（输入 out/smoke/demucs/demucs_in10s.wav 缺失 → pytest.skip，
         不静默当绿）+ master matchering+ref 条件组合。
[service] ai 探针（8188 不可达 → skip；探针=参数注入点被 /object_info 接受）。

纪律：所有渲染输出写 helpers.tmpdir(...)，绝不用 default_out/仓库 out/。
"""

import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers                                              # noqa: E402

helpers.in_proc_env()
from bgm_gen.engines import EngineError, run_engine        # noqa: E402
from bgm_gen.engines.base import (                         # noqa: E402
    DESCRIPTORS, DESCRIPTOR_ERRORS, INVARIANTS,
    content_hash_key, validate_param_value, validate_payload)
from bgm_gen.engines.descriptor_check import _TRACK_ENGINES  # noqa: E402

ALL_ENGINES = {"synth", "sf2", "sfz", "sample", "master", "demucs", "ai"}
CONTRACT_FIELDS = ("path", "sr", "channels", "duration_s", "rms_db",
                   "engine", "determinism")
PARAM_TYPES = {"int", "float", "enum", "bool", "str", "path", "any",
               "object", "array", "tuple"}
EVENT_TYPES = {"cc", "pitch_bend", "keyswitch", "program_change",
               "note_on", "note_off"}


# ---------------------------------------------------------- ① schema 自洽 [fast]
@pytest.mark.fast
def test_descriptor_registry_self_consistent():
    assert not DESCRIPTOR_ERRORS, f"注册表加载失败：{DESCRIPTOR_ERRORS}"
    assert set(DESCRIPTORS) == ALL_ENGINES
    for eng, desc in DESCRIPTORS.items():
        assert desc["engine"] == eng
        assert re.fullmatch(r"\d+\.\d+\.\d+", desc["version"]), f"{eng} 版本非 semver"
        ids = [p["id"] for p in desc["parameters"]]
        assert len(ids) == len(set(ids)), f"{eng} 参数 id 不唯一：{ids}"
        for p in desc["parameters"]:
            assert p.get("type") in PARAM_TYPES, f"{eng}.{p['id']} 类型非法"
            if p.get("type") == "enum":
                assert p.get("enum"), f"{eng}.{p['id']} 枚举为空"
                assert p.get("stepped"), f"{eng}.{p['id']} 枚举缺 stepped（C1）"
        evs = {e["type"] for e in desc["events_supported"]}
        assert evs <= EVENT_TYPES, f"{eng} 事件类型越出闭集：{evs}"
        # §3.2 消费矩阵：synth/sample=空；sf2=cc/pitch_bend+note 载体（拒 KS/PC）；sfz 全集
        if eng in ("synth", "sample"):
            assert evs == set(), f"{eng} 应为 notes-only（事件全拒）"
        if eng == "sf2":
            assert {"cc", "pitch_bend"} <= evs
            assert not evs & {"keyswitch", "program_change"}, f"sf2 应拒 KS/PC"
        if eng == "sfz":
            assert {"cc", "pitch_bend", "keyswitch", "program_change"} <= evs
        for inv in desc["invariants"]:
            assert inv.get("id") and inv.get("rule"), f"{eng} invariant 缺 id/rule"
            if inv.get("check"):
                assert inv["id"] in INVARIANTS, f"{eng} invariant 未注册：{inv['id']}"
        cov = desc["coverage"]
        assert cov["tier"] in ("A", "B", "C")
        assert cov["evidence"] and cov["probes"] and cov["gaps"], \
            f"{eng} coverage.evidence/probes/gaps 必须齐（gaps 如实标注）"
        if eng in _TRACK_ENGINES:
            assert "track_projection" in desc, f"{eng} 工程轨引擎缺 track_projection"
        else:
            assert "track_projection" not in desc
    # sfz 必须 B 档（PLAN_v3 §3.1 档位事实）
    assert DESCRIPTORS["sfz"]["coverage"]["tier"] == "B"


@pytest.mark.fast
def test_descriptor_files_match_harvest_facts():
    """harvest 事实表（唯一事实源）与磁盘描述符深一致（防手工漂移）。

    版本门收窄（红队复审 m-1）：静态深一致与 sfizz sha/size pin 与
    fluidsynth/ffmpeg 版本无关——无条件执行，不再整测 skip；仅"已实测漂移
    （版本≠冻结 pin）"的二进制版本比对行按门控口径豁免（用户裁定 2026-10-11），
    其余任何失败行一律拉红。sfizz sha/size pin 不在豁免面（二进制被替换 =
    真实变化，必须如实失败）。"""
    from bgm_gen.engines.harvest import descriptor_from_facts
    for eng in ALL_ENGINES:
        assert DESCRIPTORS[eng] == descriptor_from_facts(eng)
    helpers.require_engine_assets("sfz")
    r = helpers.run([helpers.py3(), "-m", "bgm_gen.engines.harvest", "verify"],
                    timeout=180)
    if r.returncode == 0:
        return
    drifted = {n for n in ("fluidsynth", "ffmpeg")
               if helpers.probed_binary_version(n) is not None
               and helpers.probed_binary_version(n) != helpers.frozen_binary_pin(n)}
    bad = [ln.strip()[2:] for ln in (r.stdout or "").splitlines()
           if ln.strip().startswith("- ")]

    def _excusable(line):
        m = re.match(r"^[a-z0-9_]+\.([a-z_]+)：实测 ", line)
        return bool(m) and m.group(1) in drifted and "≠ 事实表" in line

    assert bad, f"verify 非零退出但无失败行：\n{r.stdout}\n{r.stderr[-800:]}"
    fatal = [b for b in bad if not _excusable(b)]
    assert not fatal, (
        f"harvest verify 失败（非版本漂移豁免项）：\n{r.stdout}\n{r.stderr[-800:]}")


# ------------------------------------------- ② validate_payload 单元正负 [fast]
POS_CASES = [
    ("synth", {"program": "kick", "notes": [[36, 0, 0.25, 127]]}),
    ("synth", {"program": "hat", "notes": [[36, 0, 0.25, 127]],
               "params": {"decay": 0.05}}),
    ("sf2", {"sf2": "FluidR3_GM", "notes": [[36, 0, 0.25, 127]]}),
    ("sfz", {"sfz": "VSCO2/CelloEnsSusVib.sfz", "notes": [[48, 0, 0.9, 100]]}),
    ("sample", {"hits": [{"file": "x.ogg", "at": 0.0, "gain_db": 0}],
                "length_s": 1.0}),
    ("sample", {"instrument": "x.ogg", "notes": [[70, 0, 0.1, 90]],
                "bpm": 120, "length_s": 1.0}),
    ("master", {"input": "/tmp/x.wav"}),
    ("master", {"input": "/tmp/x.wav", "mode": "matchering", "ref": "/tmp/r.wav"}),
    ("demucs", {"input": "/tmp/x.wav"}),
    ("demucs", {"input": "/tmp/x.wav", "device": "cpu", "vram_need_mb": 0,
                "vram_timeout_s": 0.5, "offline": True, "two_stems": "vocals"}),
    ("ai", {"workflow": "/tmp/x.json"}),
    ("ai", {"workflow": "/tmp/x.json", "params": {"94.inputs.duration": 8},
            "timeout_s": 60, "out": "/tmp/o.wav"}),
]


@pytest.mark.fast
@pytest.mark.parametrize("engine,payload",
                         POS_CASES, ids=[f"{e}[{i}]" for i, (e, _) in enumerate(POS_CASES)])
def test_validate_payload_positive(engine, payload):
    validate_payload(engine, payload)          # 不抛即过（不渲染、不触盘——C13 划界）


NEG_CASES = [
    ("synth", {"notes": [[36, 0, 0.25, 127]]}, "program"),
    ("synth", {"program": "xylo", "notes": [[36, 0, 0.25, 127]]}, "枚举"),
    ("synth", {"program": "kick", "notes": [[36, 0, 0.25, 127]],
               "params": {"decay": 0.1}}, "params 键"),
    ("synth", {"program": "kick", "notes": []}, "最小"),
    ("synth", {"program": "kick", "notes": [[36, 0, 0.25]]}, "最小"),
    ("sf2", {"notes": [[36, 0, 0.25, 127]]}, "sf2"),
    ("sf2", {"sf2": "FluidR3_GM", "notes": [[36, 0, 0.25, 127]], "bank": 200}, "越界"),
    ("sf2", {"sf2": "FluidR3_GM", "notes": [[36, 0, 0.25, 127]], "program": -1}, "越界"),
    ("sf2", {"sf2": "FluidR3_GM", "notes": [[36, 0, 0.25, 127]], "gain": -1.0}, "越界"),
    ("sfz", {"sfz": "VSCO2/CelloEnsSusVib.sfz"}, "notes"),
    ("sample", {"length_s": 1.0}, "hits/notes"),
    ("sample", {"notes": [[70, 0, 0.1, 90]], "length_s": 1.0}, "instrument"),
    ("sample", {"hits": [], "length_s": 1.0}, "最小"),
    ("sample", {"hits": [{"file": "x.ogg"}]}, "length_s"),
    ("sample", {"instrument": "x.ogg", "length_s": 1.0}, "hits/notes"),
    ("master", {"input": "/tmp/x.wav", "mode": "bogus"}, "枚举"),
    ("master", {"input": "/tmp/x.wav", "mode": "matchering"}, "ref"),
    ("demucs", {"input": "/tmp/x.wav", "device": "gpu2"}, "枚举"),
    ("ai", {"workflow": "/tmp/x.json", "params": [1, 2]}, "对象"),
    ("sample", {"hits": ["not_a_dict"], "length_s": 1.0}, "对象"),
    ("synth", {"program": "kick", "notes": ["x"]}, "数组"),
    ("master", {}, "input"),
    ("demucs", {}, "input"),
    ("ai", {"timeout_s": 60}, "workflow"),
    ("sf2", {"sf2": "FluidR3_GM", "notes": [[36, 0, 0.25, 127]],
             "gain": float("nan")}, "有限数值"),
    ("sf2", {"sf2": "FluidR3_GM", "notes": [[36, 0, 0.25, 127]],
             "gain": float("inf")}, "有限数值"),
]


@pytest.mark.fast
@pytest.mark.parametrize("engine,payload,expect",
                         NEG_CASES, ids=[f"{e}[{i}]" for i, (e, _, _) in enumerate(NEG_CASES)])
def test_validate_payload_negative(engine, payload, expect):
    with pytest.raises(EngineError) as ei:
        validate_payload(engine, payload)
    assert expect in str(ei.value), f"{engine} 报错缺 {expect!r}：{ei.value}"


@pytest.mark.fast
def test_validate_payload_missing_descriptor_fails_loud():
    with pytest.raises(EngineError) as ei:
        validate_payload("not_an_engine", {})
    assert "描述符" in str(ei.value)


@pytest.mark.fast
def test_validate_param_value_params_slot_api():
    """§3.6 params 透传槽单键校验 API（validate_param_value——工程层 track.params
    逐键校验复用的同一路径，R4-W2 覆盖审计补直测）：合法值过 / 未知参数拒绝 /
    越界·类型错拒绝；错误口径带引擎名与参数名（与 validate_payload 同源）。"""
    validate_param_value("sf2", "gain", 0.5)               # 合法：范围内浮点
    validate_param_value("synth", "bpm", 200.0)            # 合法
    with pytest.raises(EngineError) as ei:
        validate_param_value("sf2", "nope", 1)
    assert "未知参数" in str(ei.value) and "sf2" in str(ei.value) \
        and "nope" in str(ei.value)
    with pytest.raises(EngineError) as ei:
        validate_param_value("sf2", "gain", -1.0)
    assert "越界" in str(ei.value) and "gain" in str(ei.value)
    with pytest.raises(EngineError) as ei:
        validate_param_value("sf2", "channel", 1.5)
    assert "应为整数" in str(ei.value) and "channel" in str(ei.value)


# -------------------------------------------------- ③ 行为探针 [fast]（§3.1 语义）
_SFZ_CELLO = "VSCO2/CelloEnsSusVib.sfz"
_IMPACT = "_raw/kenney/kenney_impact-sounds/Audio/impactGeneric_light_000.ogg"
_CLICK = "_raw/kenney/kenney_ui-audio/Audio/click1.ogg"

# (id, engine, payload 工厂, 是否 determinism 双渲)——每个声明参数至少一组赋值：
# synth: program/notes/bpm/params；sf2: sf2/bank/program/bpm/gain/channel；
# sfz: sfz/notes/bpm；sample: hits/instrument/notes/bpm/length_s；
# master: input/mode/target_lufs/true_peak/out（ref 走 slow matchering）。
PROBES = [
    ("synth_default", "synth",
     lambda: {"program": "kick", "notes": [[36, 0, 0.25, 127], [36, 1, 0.25, 110],
                                           [36, 2, 0.25, 127], [36, 3, 0.25, 110]]},
     True),
    ("synth_fm_ep_boundary", "synth",
     lambda: {"program": "fm_ep", "bpm": 200, "notes": [[60, 0, 2, 100]],
              "params": {"ratio": 3.0, "idx": 1.0, "bright_decay": 0.05}},
     False),
    ("sf2_full_params", "sf2",
     lambda: {"sf2": "FluidR3_GM", "bank": 0, "program": 48, "channel": 1,
              "gain": 0.6, "bpm": 140,
              "notes": [[48, 0, 0.9, 100], [52, 1.9, 0.9, 100], [55, 3.8, 0.9, 100]]},
     True),
    ("sf2_perc_bank128", "sf2",
     lambda: {"sf2": "GeneralUser_GS", "bank": 128, "program": 0, "bpm": 120,
              "notes": [[36, 0, 0.25, 127]]},
     False),
    ("sfz_default", "sfz",
     lambda: {"sfz": _SFZ_CELLO, "bpm": 120, "notes": [[48, 0, 0.9, 100]]},
     True),
    ("sfz_bpm_boundary", "sfz",
     lambda: {"sfz": _SFZ_CELLO, "bpm": 150, "notes": [[48, 0, 0.9, 100]]},
     False),
    ("sample_hits", "sample",
     lambda: {"hits": [{"file": _IMPACT, "at": 0.0, "gain_db": 0},
                       {"file": _IMPACT, "at": 0.5, "gain_db": -3}],
              "length_s": 2.0},
     True),
    ("sample_notes", "sample",
     lambda: {"instrument": _CLICK, "bpm": 130,
              "notes": [[70, 0, 0.1, 90], [70, 1, 0.1, 90]], "length_s": 2.0},
     False),
    ("master_loudnorm", "master", None, True),
]

DETERMINISM = {"synth": "deterministic", "sf2": "deterministic",
               "sfz": "deterministic", "sample": "deterministic",
               "master": "deterministic"}


def _render_once(engine, payload, tag):
    out = os.path.join(helpers.tmpdir("probes"),
                       f"{engine}_{tag}_{os.getpid()}.wav")
    spec = {"engine": engine, "spec_version": "1", "payload": payload, "out": out}
    return run_engine(spec)


def _assert_contract(result, engine):
    for k in CONTRACT_FIELDS:
        assert k in result, f"{engine} 出参缺 {k}：{sorted(result)}"
    assert result["engine"] == engine
    assert os.path.getsize(result["path"]) > 0, f"{engine} 产物零字节"
    assert result["duration_s"] > 0


@pytest.mark.fast
@pytest.mark.parametrize("pid,engine,mk_payload,double_render",
                         PROBES, ids=[p[0] for p in PROBES])
def test_behavior_probe(pid, engine, mk_payload, double_render):
    if engine == "master":
        src = os.path.join(helpers.tmpdir("probes"), f"probe_in_{pid}.wav")
        import numpy as np
        helpers.write_wav(src, np.sin(2 * np.pi * 440.0 *
                                      np.arange(44100) / 44100) * 0.3,
                          sr=44100, channels=1)
        payload = {"input": src, "mode": "loudnorm",
                   "target_lufs": -16, "true_peak": -1.5}
    else:
        payload = mk_payload()
    helpers.require_engine_assets(engine)
    result = _render_once(engine, payload, pid)
    _assert_contract(result, engine)
    assert result["determinism"] == DETERMINISM[engine], \
        f"{engine} determinism 分级错误：{result['determinism']}"
    if double_render:
        again = _render_once(engine, payload, pid + "_det")
        _assert_contract(again, engine)
        assert helpers.sha256(again["path"]) == helpers.sha256(result["path"]), \
            f"{engine} 双渲 sha 不一致（deterministic 分级被打破）"


# --------------------------------------------- ④ doctor 一致性 severity [fast]
def _mk_desc(eng, ver="9.9.9", extra_pins=()):
    return {"engine": eng, "version": ver,
            "binary_pin": {"version": ver, "sha256": None, "size": None,
                           "additional_pins": [{"name": n, "version": ver,
                                                "sha256": None}
                                               for n in extra_pins]},
            "parameters": [], "input_ports": [], "events_supported": [],
            "formats": [], "invariants": [],
            "coverage": {"tier": "A", "evidence": ["x"], "probes": ["x"],
                         "gaps": ["x"]}}


def _mk_report_item():
    report = {"ok": True, "items": []}

    def item(name, ok, detail="", optional=False, severity="error"):
        report["items"].append({"name": name, "ok": bool(ok), "detail": detail,
                                "optional": optional, "severity": severity})
        if not ok and not optional and severity == "error":
            report["ok"] = False
    return report, item


@pytest.mark.fast
def test_doctor_check_descriptors_error_flips_ok():
    from bgm_gen.doctor import check_descriptors
    report, item = _mk_report_item()
    errs = {e: "解析失败：测试注入" for e in ALL_ENGINES}
    check_descriptors(item, {}, errs, {})
    assert report["ok"] is False                      # error 拉红
    assert len(report["items"]) == 14                 # 每引擎 2 项
    for it in report["items"]:
        assert it["severity"] in ("error", "warn")
        if not it["ok"]:
            assert it["severity"] == "error", f"失败项必须是 error：{it['name']}"


@pytest.mark.fast
def test_doctor_check_descriptors_warn_does_not_flip_ok():
    from bgm_gen.doctor import check_descriptors
    report, item = _mk_report_item()
    descs = {e: _mk_desc(e, ver="9.9.9",
                         extra_pins=("ffmpeg",) if e in ("synth", "sample", "sf2")
                         else (("matchering",) if e == "master" else ()))
             for e in ALL_ENGINES}
    descs["sfz"]["binary_pin"] = {"version": "9.9.9", "sha256": "f" * 64,
                                  "size": 12345, "additional_pins": []}
    facts = {"fluidsynth": "0.0", "ffmpeg": "0.0", "sfizz_render": ("0" * 64, 0),
             "comfyui_version": "0.0", "demucs": "0.0", "matchering": "0.0"}
    check_descriptors(item, descs, {}, facts)
    assert report["ok"] is True                       # warn 黄灯不拉红（C2）
    bad = [it for it in report["items"] if not it["ok"]]
    assert bad, "版本漂移必须产生 warn 项"
    for it in bad:
        assert it["severity"] == "warn", f"漂移项必须是 warn：{it['name']}"
    assert all(it["severity"] in ("error", "warn") for it in report["items"])


@pytest.mark.fast
def test_doctor_report_contains_descriptor_items():
    report = helpers.doctor_report_once()
    names = {it["name"] for it in report["items"]}
    for eng in ALL_ENGINES:
        assert f"descriptor.{eng}" in names, f"doctor 缺 descriptor.{eng} 项"
        assert f"descriptor.{eng}.binary_pin" in names
    for it in report["items"]:
        assert it["severity"] in ("error", "warn"), f"severity 非法：{it}"
    pins = [it for it in report["items"] if it["name"].endswith(".binary_pin")]
    assert all(it["severity"] == "warn" for it in pins)
    # ok 口径：error 级非 optional 失败才拉红（与 item 收口语义一致）
    expect_ok = all(it["ok"] for it in report["items"]
                    if not it.get("optional") and it["severity"] == "error")
    assert report["ok"] is expect_ok


@pytest.mark.fast
def test_doctor_load_descriptors_full_parity(tmp_path):
    """R1 审查官 B·D1：doctor 结构检查与 base 注册表同源（唯一共享实现），
    损坏描述符（如 parameters 清空）必须在 doctor 侧报错而非假绿。"""
    import shutil
    from bgm_gen.doctor import load_descriptors
    desc_dir = os.path.join(helpers.PKG_DIR, "engines", "descriptors")
    d = tmp_path / "descs"
    d.mkdir()
    for eng in ALL_ENGINES:
        shutil.copy(os.path.join(desc_dir, f"{eng}.json"), d)
    bad = d / "synth.json"
    doc = json.loads(bad.read_text(encoding="utf-8"))
    doc["parameters"] = []
    bad.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    _, errs = load_descriptors(str(d))
    assert "synth" in errs and "parameters" in errs["synth"]


# ------------------------------------------- ⑤ content_hash_key 键组装 [fast]
@pytest.mark.fast
def test_content_hash_key_semantics():
    payload = {"program": "kick", "notes": [[36, 0, 0.25, 127]]}
    h1 = content_hash_key("synth", payload, 44100, 2)
    h2 = content_hash_key("synth", dict(payload), 44100, 2)
    assert h1 == h2 and len(h1) == 64                      # 规范化稳定
    assert h1 != content_hash_key("synth", payload, 48000, 2)      # sr 参与
    assert h1 != content_hash_key("synth", payload, 44100, 1)      # channels 参与
    payload2 = dict(payload, notes=[[36, 0, 0.25, 126]])
    assert h1 != content_hash_key("synth", payload2, 44100, 2)     # payload 参与
    # 描述符/binary_pin 版本参与（BG-D12：三版本键）
    desc = DESCRIPTORS["synth"]
    assert desc["version"] in ("1.0.0", "1.1.0")          # 锁当前版本口径
    # （1.1.0 = nylon 拨弦程序新增轮，2026-08-25 guitar_test30）
    assert content_hash_key("sf2", {"sf2": "FluidR3_GM",
                                    "notes": [[36, 0, 0.25, 127]]}, 44100, 2) \
        != content_hash_key("synth", payload, 44100, 2)   # engine 名参与


# -------------------------------------- ⑥ show engines CLI 呈现 [fast]
@pytest.mark.fast
def test_show_engines_json_and_text():
    r = helpers.run([helpers.py3(), "-m", "bgm_gen", "show", "engines", "--json"],
                    timeout=120)
    assert r.returncode == 0, r.stderr[-800:]
    out = json.loads(r.stdout)
    assert out["count"] == 7
    engines = {e["engine"]: e for e in out["engines"]}
    assert set(engines) == ALL_ENGINES
    for eng, entry in engines.items():
        for k in ("engine", "version", "binary_pin", "parameters",
                  "input_ports", "events_supported", "formats",
                  "invariants", "coverage", "determinism", "summary"):
            assert k in entry, f"{eng} 缺字段 {k}"
        assert entry["coverage"]["tier"] in ("A", "B", "C")
        assert entry["determinism"] in ("deterministic", "tolerant",
                                        "nondeterministic")
    assert engines["sfz"]["coverage"]["tier"] == "B"
    # text 表格：7 行引擎 + 表头；既有 `bgm engines` 输出不受影响
    rt = helpers.run([helpers.py3(), "-m", "bgm_gen", "show", "engines"],
                     timeout=120)
    assert rt.returncode == 0, rt.stderr[-800:]
    for eng in ALL_ENGINES:
        assert eng in rt.stdout, f"text 表格缺 {eng}"


# --------------------------------------------- [slow] demucs / matchering 探针
@pytest.mark.slow
def test_demucs_probe():
    src = os.path.join(helpers.OUT_DIR, "smoke", "demucs", "demucs_in10s.wav")
    if not os.path.isfile(src):
        pytest.skip("worktree 无 demucs 探针输入（out/smoke/demucs/demucs_in10s.wav；"
                    "主树合流后会有）")
    out_dir = helpers.tmpdir("probe_demucs")
    result = run_engine({"engine": "demucs", "spec_version": "1", "sr": 44100,
                         "payload": {"input": src, "out_dir": out_dir,
                                     "device": "auto", "model": "htdemucs",
                                     "two_stems": "vocals",
                                     "vram_need_mb": 4096,
                                     "vram_timeout_s": 120, "offline": True}})
    _assert_contract(result, "demucs")
    assert result["determinism"] == "tolerant"
    assert set(result["stems"]) >= {"vocals", "no_vocals"}, result["stems"].keys()
    for m in result["stems"].values():
        assert os.path.getsize(m["path"]) > 0, f"stem 零字节：{m['path']}"


@pytest.mark.slow
def test_master_matchering_probe():
    src = os.path.join(helpers.OUT_DIR, "smoke", "master", "master_in10s.wav")
    ref = os.path.join(helpers.OUT_DIR, "smoke", "master", "ref10s.wav")
    if not (os.path.isfile(src) and os.path.isfile(ref)):
        pytest.skip("worktree 无 matchering 探针输入（out/smoke/master/"
                    "master_in10s.wav + ref10s.wav；主树合流后会有）")
    out = os.path.join(helpers.tmpdir("probe_master"),
                       f"matched_probe_{os.getpid()}.wav")
    result = run_engine({"engine": "master", "spec_version": "1",
                         "payload": {"input": src, "mode": "matchering",
                                     "ref": ref, "out": out}})
    _assert_contract(result, "master")
    assert result["determinism"] == "deterministic"


# ------------------------------------------- [service] ai 探针（/object_info）
@pytest.mark.service
def test_ai_probe_params_accepted_by_object_info():
    base = helpers.runtime().get("comfyui", {}).get("base_url",
                                                    "http://127.0.0.1:8188")
    import urllib.request
    try:
        with urllib.request.urlopen(f"{base}/object_info", timeout=5) as r:
            info = json.loads(r.read().decode())
    except Exception:                                        # noqa: BLE001
        pytest.skip(f"ComfyUI 不可达：{base}（service 档前置服务未启动，显式 skip）")
    wf = helpers.load_json(os.path.join(helpers.WORKFLOWS_DIR, "wf_ace15.json"))
    # 探针 = 参数注入点（点路径）被 /object_info 接受：节点 id → class_type 存在、
    # 且字段在其输入键内（/object_info 键=类名，非节点 id）
    probe_paths = [("94", "duration"), ("98", "seconds"), ("3", "seed"),
                   ("104s", "filename_prefix"), ("94", "bpm")]
    for node, field in probe_paths:
        assert node in wf, f"workflow 缺节点 {node}"
        class_type = wf[node].get("class_type")
        assert class_type in info, f"/object_info 缺类 {class_type}（节点 {node}）"
        node_inputs = info[class_type].get("input", {})
        keys = list(node_inputs.get("required", {})) + \
            list(node_inputs.get("optional", {}))
        assert field in keys, \
            f"节点 {node}({class_type}) 输入无字段 {field}（{sorted(keys)}）"
        assert field in wf[node].get("inputs", {}), \
            f"workflow 注入点 {node}.inputs.{field} 不存在（params 覆盖会报错）"
