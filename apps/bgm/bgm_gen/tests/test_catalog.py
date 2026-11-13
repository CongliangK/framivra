"""catalog 音色目录测试 [fast]（P1 交付：扫描 → catalog dict → Markdown）。

断言口径（2026-08-22 勘察事实）：
    synth 6 音色齐全（kick/snare/hat + fm_ep/bass/lead，引擎白名单）；
    sf2 表含 FluidR3_GM / GeneralUser_GS（runtime.json）；
    sfz 四库计数与磁盘一致（VSCO2_CE 75 / VPO 454 / SalamanderGrand 1 / karoryfer 155，
    测试内独立 os.walk 复数，不信扫描逻辑自证）；
    sample 段非空（Kenney 10 包 / drums_gdc 12 / mixkit 类目）；
    catalog dict 可 json.dumps；md 含各段标题。
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers                                              # noqa: E402

helpers.in_proc_env()
from bgm_gen.catalog import (build_catalog, portable_catalog,  # noqa: E402
                             render_markdown)


@pytest.fixture(scope="module")
def cat():
    return build_catalog()


# ------------------------------------------------------------------ synth
@pytest.mark.fast
def test_synth_timbres(cat):
    sy = cat["sources"]["synth"]
    assert sy["count"] == 7
    assert set(sy["percussive"]) == {"kick", "snare", "hat"}
    assert set(sy["tonal"]) == {"fm_ep", "bass", "lead", "nylon"}
    assert set(sy["timbres"]) == set(sy["percussive"]) | set(sy["tonal"])
    assert sy["timbres"]["fm_ep"]["params"] == ["ratio", "idx", "bright_decay"]


# -------------------------------------------------------------------- sf2
@pytest.mark.fast
def test_sf2_fonts(cat):
    helpers.require_external_runtime()
    s2 = cat["sources"]["sf2"]
    assert {"FluidR3_GM", "GeneralUser_GS"} <= set(s2["fonts"])
    for name in ("FluidR3_GM", "GeneralUser_GS"):
        assert s2["fonts"][name]["exists"], f"sf2 文件缺失：{name}"
    assert len(s2["gm_programs"]) == 128
    assert s2["gm_programs"][0] == "Acoustic Grand Piano"
    assert s2["gm_programs"][-1] == "Gunshot"
    assert "FluidR3_GM:0:48" in s2["shorthand"]


# -------------------------------------------------------------------- sfz
@pytest.mark.fast
def test_sfz_counts_match_disk(cat):
    helpers.require_external_runtime()
    sz = cat["sources"]["sfz"]
    libs = sz["libraries"]
    assert set(libs) == {"VSCO2_CE", "VPO", "SalamanderGrand", "karoryfer"}
    expect = {"VSCO2_CE": 75, "VPO": 454, "SalamanderGrand": 1, "karoryfer": 155}
    for name, n in expect.items():
        # 独立于 catalog 扫描逻辑的磁盘复数（walk 全树数 .sfz）
        disk = sum(len([f for f in files if f.lower().endswith(".sfz")])
                   for _dp, _dn, files
                   in os.walk(os.path.join(sz["root"], name)))
        assert libs[name]["count"] == disk == n, (
            f"{name}：catalog {libs[name]['count']} / 磁盘 {disk} / 预期 {n} 三者不一致")
        assert sum(len(f) for f in libs[name]["groups"].values()) == n
    assert sz["total"] == sum(expect.values()) == 685
    # Salamander 入口文件名含空格，路径原样保留
    assert libs["SalamanderGrand"]["groups"]["."] == ["Salamander Grand Piano V3.sfz"]


# ----------------------------------------------------------------- sample
@pytest.mark.fast
def test_sample_section_nonempty(cat):
    helpers.require_external_runtime()
    sm = cat["sources"]["sample"]
    assert sm["exists"]
    kn = sm["kenney"]
    assert len(kn["packages"]) == 10
    assert kn["total_ogg"] == sum(p["ogg"] for p in kn["packages"].values()) > 700
    for pkg, p in kn["packages"].items():                  # 每包有类目与示例
        assert p["ogg"] > 0 and p["categories"], pkg
    assert sm["drums_gdc"]["total_wav"] == 12
    assert sm["drums_gdc"]["manifest_entries"] == 12
    assert len(sm["mixkit"]["categories"]) >= 30
    assert sm["mixkit"]["total_mp3"] > 1000


# ------------------------------------------------- JSON 契约 + md 渲染
@pytest.mark.fast
def test_json_serializable_and_md_sections(cat):
    text = json.dumps(cat, ensure_ascii=False)             # 不抛 = 全 Plain 类型
    back = json.loads(text)
    assert back["generated"]
    assert set(back["sources"]) == {"synth", "sf2", "sfz", "sample"}
    assert "ai" in back["engines_note"]                    # 非静态源说明段带引擎名

    md = render_markdown(cat)
    for title in ("# bgm 音色目录", "## synth", "## sf2", "## sfz", "## sample",
                  "非静态音色源"):
        assert title in md, f"md 缺段标题：{title}"
    # v3 R1-W1：payload 段由描述符驱动——每个描述符参数名都必须出现在 md 中
    for engine in ("synth", "sf2", "sfz", "sample"):
        desc_path = os.path.join(helpers.PKG_DIR, "engines", "descriptors",
                                 f"{engine}.json")
        with open(desc_path, encoding="utf-8") as f:
            desc = json.load(f)
        assert desc["engine"] == engine
        assert cat["sources"][engine]["payload"], f"{engine} payload 文档为空"
        for p in desc["parameters"]:
            assert p["id"] in md, f"md 缺描述符参数名 {engine}.{p['id']}"


# --------------------------------------------- 盘内活数据同步锁（R4-W2 F-1）
@pytest.mark.fast
def test_disk_catalog_fresh_render_sync(cat):
    helpers.require_external_runtime()
    """盘内 catalog/timbres.{md,json} == fresh 渲染（`generated` 日期行归一除外）。

    R4-W2 覆盖审计 F-1 修复锁：R2 bump sf2/sfz 描述符 v1.1.0 后未重落盘，
    盘内 payload 段曾落后漂移（v1.0.0）。本例防再漂移——描述符 parameters
    改动必须伴随 `python3 -m bgm_gen catalog --write` 重落盘（timbres.md
    为 git 内活数据，PLAN_v3 §6 test_catalog 行同步要求）。"""
    # 发布编目只脱敏路径；计数、描述符、采样事实仍全量精确对拍。
    cat = portable_catalog(cat)
    cat_dir = os.path.join(helpers.WORKSPACE, "catalog")

    def _norm_md(text):
        # 生成日期随落盘日变化（date.today()，日期粒度）——归一后须逐行一致
        return [l for l in text.splitlines() if not l.startswith("> 生成：")]

    with open(os.path.join(cat_dir, "timbres.md"), encoding="utf-8") as f:
        md_disk = _norm_md(f.read())
    assert md_disk == _norm_md(render_markdown(cat)), (
        "盘内 catalog/timbres.md 与 fresh 渲染漂移（日期行除外）——"
        "描述符改动后须 `python3 -m bgm_gen catalog --write` 重落盘")

    with open(os.path.join(cat_dir, "timbres.json"), encoding="utf-8") as f:
        j_disk = json.load(f)
    j_fresh = json.loads(json.dumps(cat, ensure_ascii=False))
    j_disk.pop("generated", None)
    j_fresh.pop("generated", None)
    assert j_disk == j_fresh, "盘内 catalog/timbres.json 与 fresh build_catalog 漂移"
