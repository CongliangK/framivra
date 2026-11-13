"""③CLI 端到端测试 [fast]（PLAN.md §5 R4-③）。

- 正例：render examples/minimal.json → /tmp，产物齐（mix.flac/qc.json/stems）、
  qc.json ok=true、mix.flac sha256 与 out/minimal/mix.flac 逐位一致
  （确定性回归锚点，R3 已验 e63a34eb…）。
- 负例 4 条：spec_version 错 / notes 3 元数组 / clip 越界 / 未知字段——
  exit≠0、stderr 含字段路径、无 Traceback。
- doctor/engines/render-engine 既有行为不回退（R3 冻结复验）+ doctor.py
  呈现层（默认人读表格 / --json 原样 JSON；bgm doctor 仍输出 JSON，机器可读不变）。
"""

import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers                                              # noqa: E402


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _bgm(*args, timeout=300):
    return helpers.run([helpers.py3(), "-m", "bgm_gen", *args],
                       cwd=helpers.WORKSPACE, timeout=timeout)


# ------------------------------------------------------------------- 正例
@pytest.mark.fast
def test_render_minimal_e2e():
    helpers.require_minimal_assets()
    out_dir = helpers.tmpdir("minimal_cli")
    r = _bgm("render", os.path.join(helpers.EXAMPLES_DIR, "minimal.json"),
             "-o", out_dir, "-q")
    assert r.returncode == 0, f"render 失败：\n{r.stderr[-2000:]}"

    for rel in ("mix.flac", "qc.json", "project.json",
                "stems/kick.synth.wav", "stems/strings.sf2.wav",
                "stems/click.sample.wav"):
        p = os.path.join(out_dir, rel)
        assert os.path.isfile(p) and os.path.getsize(p) > 0, f"产物缺失：{rel}"

    qc = helpers.load_json(os.path.join(out_dir, "qc.json"))
    assert qc["ok"] is True, json.dumps(
        {k: v for k, v in qc["items"].items() if v.get("pass") is False},
        ensure_ascii=False)

    # 位级锚点（out/minimal/mix.flac）由冻结版 ffmpeg/fluidsynth 产出——仅此断言段
    # 版本门控显式 skip（用户裁定 2026-10-11；红队复审 m-1 收窄：产物存在性与
    # QC 判定无条件执行），版本匹配时照常严格执行
    helpers.skip_if_binary_version_drift("fluidsynth", "ffmpeg")
    anchor = os.path.join(helpers.OUT_DIR, "minimal", "mix.flac")
    helpers.require_fixture(anchor)
    assert _sha256(os.path.join(out_dir, "mix.flac")) == _sha256(anchor), (
        "确定性回归锚点失效：mix.flac sha 与 out/minimal/mix.flac 不一致")


# ------------------------------------------------------------------- 负例
def _neg_case(name, mutate):
    proj = helpers.load_json(os.path.join(helpers.EXAMPLES_DIR, "minimal.json"))
    mutate(proj)
    path = os.path.join(helpers.tmpdir("neg"), f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(proj, f, ensure_ascii=False)
    return _bgm("render", path, "-o", helpers.tmpdir(f"neg_out_{name}"), "-q")


@pytest.mark.fast
@pytest.mark.parametrize("name,mutate,expect", [
    ("bad_spec_version",
     lambda p: p["meta"].__setitem__("spec_version", "99"),
     "meta.spec_version"),
    ("notes_3elem",
     lambda p: p["tracks"][0].__setitem__("notes", [[36, 0, 0.25]]),
     "tracks[0].notes[0]"),
    ("clip_out_of_range",
     lambda p: p["tracks"][0]["clip"].__setitem__("start_beat", -1),
     "tracks[0].clip.start_beat"),
    ("unknown_field",
     lambda p: p["tracks"][0].__setitem__("frobnicate", 1),
     "tracks[0]"),
], ids=["spec_version", "notes-3elem", "clip-range", "unknown-field"])
def test_render_negative(name, mutate, expect):
    r = _neg_case(name, mutate)
    assert r.returncode != 0, f"负例 {name} 应当失败却 exit 0"
    assert "Traceback" not in (r.stderr or ""), \
        f"负例 {name} 抛了裸 Traceback（应走可定位报错）：\n{r.stderr[-1500:]}"
    if name == "unknown_field":
        assert "未知字段" in r.stderr and "frobnicate" in r.stderr
    assert expect in r.stderr, (
        f"负例 {name} 报错缺字段路径 {expect!r}：\n{r.stderr[-1500:]}")


# --------------------------------------------- 既有子命令不回退 + doctor 呈现
@pytest.mark.fast
def test_subcommand_engines_no_regression():
    r = _bgm("engines")
    assert r.returncode == 0
    for eng in ("synth", "sf2", "sfz", "sample", "master", "demucs", "ai"):
        assert eng in r.stdout, f"engines 列表缺 {eng}"
    assert "deterministic" in r.stdout          # determinism 列仍在


@pytest.mark.fast
def test_subcommand_render_engine_no_regression():
    spec = {"engine": "synth", "spec_version": "1", "sr": 44100,
            "out": os.path.join(helpers.tmpdir("cli_re"), "kick.wav"),
            "payload": {"program": "kick", "bpm": 120,
                        "notes": [[36, 0, 0.25, 127]]}}
    path = os.path.join(helpers.tmpdir("cli_re"), "spec.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(spec, f)
    r = _bgm("render-engine", "--engine", "synth", "--spec", path)
    assert r.returncode == 0, r.stderr[-800:]
    result = json.loads(r.stdout)
    assert result["sr"] == 44100 and result["rms_db"] > -40


@pytest.mark.fast
def test_subcommand_doctor_json_unchanged():
    """`bgm doctor` 输出保持 JSON + exit 语义（R3 冻结口径，机器可读不回退）。"""
    r = _bgm("doctor", timeout=180)
    report = json.loads(r.stdout)
    assert "ok" in report and isinstance(report["items"], list) and report["items"]
    assert r.returncode == (0 if report["ok"] else 1)


@pytest.mark.fast
def test_doctor_pretty_default_and_json_flag():
    """doctor.py 呈现层：默认人读绿红表，--json 输出与 doctor_report() 一致。"""
    helpers.in_proc_env()
    ref = helpers.doctor_report_once()

    # 默认 = 人读表格：逐项 ✓/✗、可选档标注、非 JSON
    r_pretty = helpers.run([helpers.py3(), os.path.join(helpers.PKG_DIR, "doctor.py")],
                           timeout=180)
    assert "Traceback" not in r_pretty.stderr, r_pretty.stderr[-800:]
    assert not r_pretty.stdout.lstrip().startswith("{")
    # 只数体检项行——" 总判定：…（N ✗ / M 项）"行带前导空格且含 ✗，不能计入
    #（R3 审查 B-2：任一必检项失败时该行走失败分支，此前口径必误判并掩盖真实失败原因）
    marks = [ln for ln in r_pretty.stdout.splitlines()
             if ("✓" in ln or "✗" in ln) and not ln.lstrip().startswith("总判定")]
    assert len(marks) == len(ref["items"]), \
        f"绿红行数 {len(marks)} != 体检项数 {len(ref['items'])}"
    assert "可选档" in r_pretty.stdout              # optional 项有"可选档"标注
    assert r_pretty.returncode == (0 if ref["ok"] else 1)

    # --json = 原 JSON（与 in-process doctor_report 逐字段一致）
    r_json = helpers.run([helpers.py3(), os.path.join(helpers.PKG_DIR, "doctor.py"),
                          "--json"], timeout=180)
    assert json.loads(r_json.stdout) == ref
    assert r_json.returncode == (0 if ref["ok"] else 1)
