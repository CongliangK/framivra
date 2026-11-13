"""①资产层测试 [fast]（PLAN.md §5 R4-①）。

assets.json 15 条逐项：
- relpath 非 null：解析 assets_root 后存在；size 与磁盘偏差 ≤1%
  （目录条目按"普通文件求和"口径——os.walk followlinks=False 且排除符号链接，
  防 rpm .build-id 符号链接假象，R1 审查实测教训）；
- license 非空且白名单命中（CC0/MIT/Apache/BSD/GPL/LGPL/ISC/GeneralUser 字样）；
- driver 非空且与 runtime.json 无矛盾（条目提到的工具/解释器在 runtime 中登记
  且实际存在）。

relpath=null 条目（conda site-packages / 系统 dnf 包）无根相对路径可校，
只做 license/driver 两项；其存在性由 doctor 的 python_torch/tools 探测覆盖。
"""

import ctypes.util
import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers                                              # noqa: E402

LICENSE_WHITELIST = ("CC0", "MIT", "Apache", "BSD", "GPL", "LGPL", "ISC",
                     "GeneralUser",
                     # v2 弹药（2026-08-22 用户批准全面提质）：CC-BY 音色库非商业音乐，
                     # 无指纹执法风险（D17 立法针对对象），署名义务在 manifest 与 REPORT 落实
                     "CC-BY", "Sonniss")


def _manifest():
    m = helpers.load_json(helpers.ASSETS_JSON)
    from bgm_gen.paths import assets_root
    # 发布 manifest 只存可移植根标记；实际校验仍使用本地配置，绝不跳过资产断言。
    root = m["_meta"]["root"]
    if root == "$MUSIC_ASSETS_ROOT":
        root = assets_root(helpers.runtime())
    return root, m["items"]


def _path_ok(p):
    if not p:
        return False
    p = os.path.expanduser(p)
    return os.path.exists(p) if os.path.isabs(p) else bool(_which(p))


def _which(name):
    from shutil import which
    return which(name)


def _dir_size(path):
    """目录普通文件字节求和（不跟随、不计数符号链接）。"""
    total = 0
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        dirnames[:] = [d for d in dirnames
                       if not os.path.islink(os.path.join(dirpath, d))]
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            st = os.lstat(fp)
            if stat.S_ISREG(st.st_mode):
                total += st.st_size
    return total


def _runtime_consistency(item, rt, root):
    """driver 与 runtime.json 无矛盾：条目涉及的登记项必须存在。"""
    blob = f"{item.get('name', '')} {item.get('driver', '')}"
    checks = []
    if "sfizz" in blob:
        checks.append(("tools.sfizz_render", _path_ok(
            (rt.get("tools") or {}).get("sfizz_render"))))
    if "fluidsynth" in blob:
        checks.append(("tools.fluidsynth_cli", _path_ok(
            (rt.get("tools") or {}).get("fluidsynth_cli"))))
        checks.append(("libfluidsynth", bool(ctypes.util.find_library("fluidsynth"))))
    if "demucs" in blob or "htdemucs" in blob:
        checks.append(("demucs.python", os.path.isfile(
            (rt.get("demucs") or {}).get("python") or "")))
        hf = ((rt.get("demucs") or {}).get("env") or {}).get("HF_HOME")
        checks.append(("demucs.env.HF_HOME", bool(hf) and os.path.isdir(hf)))
    if "matchering" in blob:
        checks.append(("matchering.python", os.path.isfile(
            (rt.get("matchering") or {}).get("python") or "")))
    if "pretty_midi" in blob or "midiutil" in blob or "site-packages" in blob:
        checks.append(("python_torch", os.path.isfile(rt.get("python_torch") or "")))
    if "GeneralUser" in blob:
        got = (rt.get("sf2") or {}).get("GeneralUser_GS")
        want = os.path.join(root, "sf2", "GeneralUser_GS.sf2")
        checks.append(("sf2.GeneralUser_GS", got == want and os.path.isfile(want)))
    for key, ok in checks:
        assert ok, f"driver 与 runtime.json 矛盾：{item.get('name')} → {key}"


@pytest.mark.fast
def test_manifest_count_matches_items():
    root, items = _manifest()
    meta = helpers.load_json(helpers.ASSETS_JSON)["_meta"]
    assert meta.get("count") == len(items), (
        f"_meta.count={meta.get('count')} != 实际条目数 {len(items)}")
    helpers.require_external_runtime()
    assert root and os.path.isdir(root), f"assets_root 不存在：{root}"


@pytest.mark.fast
@pytest.mark.parametrize("item", [i for i in _manifest()[1]],
                         ids=lambda it: it.get("name", "?")[:40])
def test_item_license_and_driver(item):
    rt = helpers.runtime()
    root, _ = _manifest()
    lic = item.get("license")
    assert isinstance(lic, str) and lic.strip(), \
        f"license 缺失或为空：{item.get('name')}"
    assert any(k in lic for k in LICENSE_WHITELIST), (
        f"license 不在白名单（{LICENSE_WHITELIST}）：{item.get('name')} → {lic}")
    drv = item.get("driver")
    assert isinstance(drv, str) and drv.strip(), \
        f"driver 缺失或为空：{item.get('name')}"
    helpers.require_external_runtime()
    _runtime_consistency(item, rt, root)


@pytest.mark.fast
@pytest.mark.parametrize("item", [i for i in _manifest()[1] if i.get("relpath")],
                         ids=lambda it: it.get("name", "?")[:40])
def test_item_exists_and_size(item):
    helpers.require_external_runtime()
    root, _ = _manifest()
    p = os.path.join(root, *item["relpath"].strip("/").split("/"))
    assert os.path.exists(p), f"relpath 解析后不存在：{item['relpath']} → {p}"
    declared = item.get("size_bytes")
    if not isinstance(declared, (int, float)) or declared <= 0:
        pytest.skip(f"size_bytes 缺失/非正数（{item.get('name')}），只验存在性")
    actual = _dir_size(p) if os.path.isdir(p) else os.path.getsize(p)
    assert abs(actual - declared) <= declared * 0.01, (
        f"size 偏差 >1%：{item['name']} 声明 {declared} vs 磁盘 {actual}"
        f"（偏差 {100.0 * abs(actual - declared) / declared:.2f}%）")
