"""开源路径适配回归；不依赖个人磁盘布局或外部渲染。"""

import copy
import os

import pytest

from bgm_gen import paths
from bgm_gen.catalog import portable_catalog
from bgm_gen.engines.sf2 import resolve_sf2
from tools import make_timbre_previews as previews

pytestmark = pytest.mark.fast


def test_asset_roots_env_then_runtime_then_repo(monkeypatch, tmp_path):
    monkeypatch.delenv("MUSIC_ASSETS_ROOT", raising=False)
    monkeypatch.delenv("MUSIC_SAMPLES_ROOT", raising=False)
    rt = {"assets_root": str(tmp_path / "configured"),
          "samples_root": str(tmp_path / "oneshots")}
    assert paths.assets_root(rt) == rt["assets_root"]
    assert paths.samples_root(rt) == rt["samples_root"]
    assert paths.assets_root({}) == os.path.join(paths.data_root(), "assets")
    assert paths.samples_root({}) == os.path.join(paths.data_root(), "assets", "samples")
    monkeypatch.setenv("MUSIC_ASSETS_ROOT", str(tmp_path / "env-assets"))
    monkeypatch.setenv("MUSIC_SAMPLES_ROOT", str(tmp_path / "env-samples"))
    assert paths.assets_root(rt) == str(tmp_path / "env-assets")
    assert paths.samples_root(rt) == str(tmp_path / "env-samples")


def test_asset_root_expanduser_and_missing_local_config(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MUSIC_ASSETS_ROOT", "~/assets")
    assert paths.assets_root({}) == str(tmp_path / "assets")
    monkeypatch.setattr(paths, "data_root", lambda **kwargs: str(tmp_path))
    assert paths.local_runtime() == {}


def test_catalog_publication_prefix_boundary_and_no_mutation(tmp_path):
    root = str(tmp_path / "assets")
    sample = root + "/oneshots"
    rt = {"assets_root": root, "samples_root": sample}
    cat = {"root": root, "path": root + "/sf2/font.sf2",
           "samples": [sample + "/hit.wav"], "count": 19,
           "untouched": root + "-other/font.sf2"}
    before = copy.deepcopy(cat)
    got = portable_catalog(cat, rt)
    assert got == {"root": "$MUSIC_ASSETS_ROOT",
                   "path": "$MUSIC_ASSETS_ROOT/sf2/font.sf2",
                   "samples": ["$MUSIC_SAMPLES_ROOT/hit.wav"], "count": 19,
                   "untouched": root + "-other/font.sf2"}
    assert cat == before


def test_preview_release_set_and_credits():
    slugs = {slug for slug, _title, _entries in previews.build_previews()}
    assert not ({"sample_mixkit", "sample_gdc_drums"} & slugs)
    assert slugs <= set(previews.ATTRIBUTIONS)
    assert "Alexander Holm" in previews.ATTRIBUTIONS["sfz_salamander"]
    assert "CC-BY 3.0" in previews.ATTRIBUTIONS["sfz_salamander"]
    assert "Paul Battersby" in previews.ATTRIBUTIONS["sfz_vpo"]


def test_preview_render_uses_env_roots_without_mutating_runtime(monkeypatch, tmp_path):
    rt = {"assets_root": str(tmp_path / "old"),
          "samples_root": str(tmp_path / "old-samples"),
          "sf2": {"FluidR3_GM": "system-font"}}
    before = copy.deepcopy(rt)
    # Real local_runtime() returns a newly loaded dict; preserve that contract.
    monkeypatch.setattr(previews, "local_runtime", lambda: copy.deepcopy(rt))
    monkeypatch.setenv("MUSIC_ASSETS_ROOT", str(tmp_path / "assets"))
    monkeypatch.setenv("MUSIC_SAMPLES_ROOT", str(tmp_path / "samples"))
    gu_dir = tmp_path / "assets" / "sf2"
    gu_dir.mkdir(parents=True)
    (gu_dir / "GeneralUser_GS.sf2").write_bytes(b"")
    seen = {}
    monkeypatch.setattr(previews, "run_engine", lambda spec, runtime: seen.update(runtime))
    monkeypatch.setattr(previews, "_ffmpeg", lambda *a, **kw: bytes(8))
    assert previews.render_snippet("probe", "sf2", {}).shape == (1, 2)
    assert seen["assets_root"] == str(tmp_path / "assets")
    assert seen["samples_root"] == str(tmp_path / "samples")
    assert seen["sf2"]["GeneralUser_GS"] == str(tmp_path / "assets/sf2/GeneralUser_GS.sf2")
    assert seen["sf2"]["FluidR3_GM"] == "system-font"
    assert rt == before


def test_preview_fallback_repo_root_still_maps_generaluser(monkeypatch, tmp_path):
    """无 env 无 runtime 的纯 fallback：解析出的仓库 assets/ 根同样注入 GeneralUser
    映射，resolve_sf2 不报"未知 sf2 名字"（README fallback 承诺，纯逻辑层验证）。"""
    monkeypatch.delenv("MUSIC_ASSETS_ROOT", raising=False)
    monkeypatch.delenv("MUSIC_SAMPLES_ROOT", raising=False)
    monkeypatch.setattr(previews, "local_runtime", lambda: {})
    monkeypatch.setattr(paths, "data_root", lambda **kwargs: str(tmp_path))
    gu_dir = tmp_path / "assets" / "sf2"
    gu_dir.mkdir(parents=True)
    (gu_dir / "GeneralUser_GS.sf2").write_bytes(b"")
    seen = {}
    monkeypatch.setattr(previews, "run_engine", lambda spec, runtime: seen.update(runtime))
    monkeypatch.setattr(previews, "_ffmpeg", lambda *a, **kw: bytes(8))
    previews.render_snippet("probe", "sf2", {})
    assert seen["assets_root"] == str(tmp_path / "assets")
    assert seen["sf2"]["GeneralUser_GS"] == str(gu_dir / "GeneralUser_GS.sf2")
    assert resolve_sf2("GeneralUser_GS", seen) == str(gu_dir / "GeneralUser_GS.sf2")
