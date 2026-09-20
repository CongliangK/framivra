#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""r07c_regression.py · R-07c 回归用例件（写态四件之②；签发版=v1+修订件 A1–A14+ERRATUM 两件合读）。

编排硬条款①②③④ + 七式指纹断言 + 负例内建（增 14 副本双态）+ 四组哈希落档（J4″）。
零插件依赖、纯标准库 + 本仓 vtx 包（只读复用）；真件全程只读（负例对 tempfile 副本注入）。

子命令（按 §四.2 窗序调用）：
  fingerprints        七式指纹断言（真件绿态；式①按 v1 §三 注锚 L213+L436 两处不动面取
                      `\\[0:v\\]format=yuv420p` 计 2——v1 字面 `\\[base\\]` 后缀与自身注锚矛盾
                      （L213 实为 [cv] 标签），按注锚语义执行并在输出行显形，候 L3 一行 ERRATUM）
  selftest-negative   增 14 负例双态：tempfile 副本逐式注入篡改必红 + 真件绿态对照必绿 + 副本清理
  golden --proj DIR   硬条款①：scripts/gfx_golden.py compare 面全过（免冷建）
  qc --proj DIR       硬条款②：vtx qc exit 0 且 QC：N/N 相等（voice-ime 形态 N=76，--expect 校验）
  capture --side B|A  B′/A′ 成片 + B/A 中间物捕获：温态全命中验证（增 8，禁降格凑数）、
                      stdout 抓名、四组哈希落档（manifest.json）、副本出库至证据目录
  compare --proj DIR  硬条款③④：③切点帧窗 [S−1,S+1] B′/A′ 逐帧全同；④抽 2 段×各 2 非末 pass
                      B(MKV)/A(NUT) framemd5 checksum 列($NF)逐帧全同+行数相等锚（容器异构版，
                      容器字段两面全消）
  all                 以上编排（ fingerprints → selftest-negative → golden → qc → capture → compare）

④ 抽样规则（RT-3 零自由度平移；补注=第二 pass 选择为本书未冻结处的确定性补全，随回执申报）：
  seg00=按帧数降序首段（并列取段序 si 典序最小）；随机段=典序第 ((0x(sha256(HEAD|random)[:8]))%N)+1 个；
  两 pass=非末集上中位 ⌊(P−1)/2⌋ 与其后继（越界回退前驱）。B/A 配对键=段区间+pass 序号 k
  （v1 §三④），文件名由键配方离线重算定位（配方=R4-E1-v2 原文逐字节；键哈希 B/A 必翻为预期）。
  随机段哈希的 HEAD 锚=B 捕获时 HEAD（manifest 固化，A 侧复用），防两代 HEAD 漂移错配。

B′/A′ 捕获语义（增 8+§四.2 窗序）：温态全命中=段文件缺席而 pass 缓存全在时，
composite 逐 pass 打「已缓存（件名）」行且计数==段层数−1；本命令删段文件后重跑 video 阶段
（pass 全命中=仅最终段编码一次 ffmpeg ≈7s 级），stdout 全文留档，任一 pass 未命中=报卡点
sys.exit(1)。成片 out/video_noaudio.mp4 的 sha256+副本出库=B′/A′。

F1 已知差异项（详见 vtx/composite.py::_composite_passes docstring）：④比对若红于「a=0 段
（seg00）层窗口末端边界帧（rel_out≡1,2 (mod 3)）」，属 pts 网格差（MKV 1/1000 vs NUT 1/61440），
非本件实现缺陷；处置=异常扩样序/R4 条款，候 L3 裁定（compare 输出按帧号对 F1 形态显形标注）。

用法（本机纪律 env python3）：
  env python3 scripts/r07c_regression.py fingerprints
  env python3 scripts/r07c_regression.py selftest-negative
  env python3 scripts/r07c_regression.py capture --side B --proj ~/文档/04_bilibili视频/voice-ime --ev /tmp/r07c_ev
  env python3 scripts/r07c_regression.py compare --proj ... --ev /tmp/r07c_ev
  env python3 scripts/r07c_regression.py all --proj ... --ev /tmp/r07c_ev
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# ---- 七式指纹（计数冻结自 v1 §三；模式均为代码行字面量子串，行计数=含该子串的行数）----
FINGERPRINTS = [
    # (锚子串, 冻结计数, 出处注)
    (r"[0:v]format=yuv420p", 2,
     "式①：v1 §三 注锚=L213 单图路径+L436 pass 路径两处不动面（v1 字面含 [base] 后缀系笔误，"
     "L213 实为 [cv] 标签；按注锚语义取公共前缀计 2，候 L3 一行 ERRATUM）"),
    ("format=rgba,setparams=range=unspecified:colorspace=unknown[base]", 1, "式②"),
    ("format=yuv420p[vout]", 1, "式③"),
    ("format=rgba,setparams=range=unspecified:colorspace=unknown[vout]", 1, "式④"),
    ('"ffv1", "-level", "3"', 1, "式⑤"),
    ('"-g", "1",', 1, "式⑥"),
    ('"-filter_complex_threads", "1"', 1, "式⑦"),
]
DEFAULT_COMPOSITE = REPO / "vtx" / "composite.py"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd, **kw):
    return subprocess.run([str(x) for x in cmd], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)


def fail(msg):
    print(f"✗ {msg}")
    sys.exit(1)


# ---------------------------------------------------------------- 指纹断言（增 14.2 参数化）
def assert_fingerprints(path: Path, out=print):
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    results = []
    for anchor, expect, note in FINGERPRINTS:
        n = sum(1 for ln in lines if anchor in ln)
        results.append((anchor, expect, n, note))
        out(f"  {'✓' if n == expect else '✗'} 式锚[{anchor[:52]}] 期望={expect} 实测={n}"
            f"{'（' + note + '）' if expect != n or '式①' in note else ''}")
    bad = [r for r in results if r[1] != r[2]]
    if bad:
        fail(f"指纹断言红：{len(bad)} 式计数偏离（跨文件迁移/误改=全 0 亦红，fail-closed）")
    out(f"✓ 七式指纹断言全绿（{path}）")
    return results


def cmd_fingerprints(args):
    p = Path(args.composite)
    if not p.exists():
        fail(f"指纹对象件不存在：{p}")
    assert_fingerprints(p)


def cmd_selftest_negative(args):
    """增 14：tempfile 副本目录复制真件→逐式注入篡改（锚删）→断言必红→真件绿态对照→清理。"""
    real = Path(args.composite)
    text = real.read_text(encoding="utf-8")
    print(f"  · 真件绿态对照（只读）：")
    assert_fingerprints(real)          # 真件只读断言必绿
    for anchor, expect, note in FINGERPRINTS:
        with tempfile.TemporaryDirectory(prefix="r07c_neg_") as td:
            fake = Path(td) / "composite.py"
            tampered = text.replace(anchor, "#〔负例注入·锚已删〕", 1)
            if tampered == text:
                fail(f"负例注入失败：锚串在真件中未命中（{anchor[:40]}…）")
            fake.write_text(tampered, encoding="utf-8")
            lines = fake.read_text(encoding="utf-8").splitlines()
            n = sum(1 for ln in lines if anchor in ln)
            if n == expect:
                fail(f"负例未红：{anchor[:40]}… 篡改后计数仍={n}（断言器失效）")
            print(f"  ✓ 负例红：式锚[{anchor[:44]}…] 期望={expect} 篡改后={n}（副本 {fake.parent}）")
        # 副本随 TemporaryDirectory 自动清理；真件全程只读
    print(f"✓ 负例双态自证全绿（{len(FINGERPRINTS)} 式注入必红 + 真件必绿；真件 sha256={sha256_file(real)[:12]}…）")


# ---------------------------------------------------------------- 项目几何（切点/段/pass 键离线重算）
def load_project(proj: Path, require_segs=True):
    """只读构建 Ctx + layers（温态前提守卫：seg 缺失=卡点，禁就地补编码）。"""
    from vtx.infra import Ctx
    from vtx.composite import choose_cutpoints, prepare_layers
    tl = (proj / "timeline.json").resolve()
    if not tl.exists():
        fail(f"timeline.json 不存在：{tl}")
    c = Ctx(tl)
    layers = prepare_layers(c)
    missing = [L["seg"].name for L in layers if not L["gfx"] and not L["seg"].exists()]
    if require_segs and missing:
        fail(f"温态前提破坏：seg 缓存缺失 {missing[:4]}…（禁就地补编码；先跑温态合成）")
    bounds = [(L["cl"]["dst_in"], L["cl"]["dst_in"] + L["cl"]["dur"]) for L in layers]
    nseg = int(os.environ.get("VTX_VIDEO_SEGMENTS", "8"))
    cuts = choose_cutpoints(bounds, c.dur, nseg)
    edges = [0] + cuts + [c.dur]
    return c, layers, cuts, edges


def pass_key_recipe(pk, idx, layer):
    """pass 键配方（与 vtx/composite.py::_composite_passes.pass_key 构成逐字节对齐；
    定位正确性由 capture 的存在性断言背书：算错=文件找不到=红）。"""
    ident = layer["pngdir"].name if layer["gfx"] else layer["seg"].name
    h = hashlib.sha1()
    h.update(f"{pk}|pass{idx}\n".encode())
    from vtx.composite import _graph_code_digest
    h.update(f"passgraph={_graph_code_digest()}\n".encode())
    h.update((ident + "|" + json.dumps(layer["cl"], sort_keys=True, ensure_ascii=False)
              + f"|{layer['x']}|{layer['y']}|{layer['w2']}|{layer['h2']}\n").encode())
    return h.hexdigest()[:12]


def segment_pass_files(c, layers, edges):
    """每段 (a, b, [层], [该段全部非末 pass 目标名])；键链离线重算。"""
    segs = []
    for si in range(len(edges) - 1):
        a, b = edges[si], edges[si + 1]
        sl = [L for L in layers if L["cl"]["dst_in"] < b and L["cl"]["dst_in"] + L["cl"]["dur"] > a]
        prev_key = f"{a}|{b}|{c.w}x{c.h}|0|1"
        names = []
        for k, L in enumerate(sl[:-1]):          # 非末 pass
            pk = pass_key_recipe(prev_key, k, L)
            names.append(f"p{k}_{pk}")
            prev_key = pk
        segs.append({"si": si, "a": a, "b": b, "n_layers": len(sl), "passes": names})
    return segs


def pick_segments(segs, head):
    """RT-3 零自由度抽样：seg00=帧数降序首段（并列 si 典序最小）；随机段=典序第 hash%N+1 个。"""
    order = sorted(segs, key=lambda s: (-(s["b"] - s["a"]), s["si"]))
    seg00 = order[0]
    by_typ = sorted(segs, key=lambda s: f"seg{s['si']:02d}_{s['a']:06d}")
    digest = hashlib.sha256(f"{head}|random".encode()).hexdigest()
    idx = int(digest[:8], 16) % len(by_typ)
    rnd = by_typ[idx]
    picks = [seg00] if rnd["si"] == seg00["si"] else [seg00, rnd]
    return picks, idx


def pick_passes(seg):
    """中位 ⌊(P−1)/2⌋ 与后继（越界回退前驱）——第二 pass 选择为确定性补全，随回执申报。"""
    P = len(seg["passes"])
    med = (P - 1) // 2
    nxt = med + 1 if med + 1 <= P - 1 else med - 1
    ks = sorted({med, nxt})
    return [(k, seg["passes"][k]) for k in ks]


def framemd5(path: Path):
    r = run(["ffmpeg", "-v", "error", "-i", path, "-map", "0:v:0", "-f", "framemd5", "-"])
    if r.returncode != 0:
        fail(f"framemd5 失败：{path}（{r.stderr[-200:]}）")
    rows = [ln for ln in r.stdout.splitlines() if re.match(r"^[0-9]+,", ln)]
    return [ln.rsplit(",", 1)[1].strip() for ln in rows]


# ---------------------------------------------------------------- 捕获（B′/A′ + B/A；四组哈希落档）
def cmd_capture(args):
    proj, ev = Path(args.proj).resolve(), Path(args.ev).resolve()
    ev.mkdir(parents=True, exist_ok=True)
    side = args.side.upper()
    ext = "mkv" if side == "B" else "nut"
    c, layers, cuts, edges = load_project(proj)
    segs = segment_pass_files(c, layers, edges)

    head = args.head or run(["git", "rev-parse", "HEAD"], cwd=REPO).stdout.strip()
    # ---- 温态全命中验证 + stdout 抓名（增 8；删段文件留 pass 缓存=仅最终段编码 ≈7s）----
    vdir = c.cache / "vseg"
    for f in vdir.glob("seg*.mp4"):
        f.unlink()
    env = dict(os.environ)
    env.setdefault("VTX_NO_NVENC", "1") if args.no_nvenc else None
    r = run(["env", "python3", "-X", "utf8", str(REPO / "vtx" / "vtx.py"),
             "-t", str(proj / "timeline.json"), "video"], cwd=REPO, env=env)
    (ev / f"stdout_{side}.log").write_text(r.stdout + "\n--stderr--\n" + r.stderr, encoding="utf-8")
    if r.returncode != 0:
        fail(f"video 阶段 rc={r.returncode}（详见 {ev}/stdout_{side}.log）")
    cached = re.findall(r"pass (\d+)/(\d+) 已缓存（([^）]+)）", r.stdout)
    if not cached:
        fail("温态全命中验证失败：stdout 零「已缓存」行（段缓存未删/键漂移），禁降格凑数（增 8）")
    timing = [ln for ln in r.stdout.splitlines() if ln.startswith("[timing] pass")]
    print(f"  · 温态命中 pass 行={len(cached)}，[timing] 行={len(timing)}（打点行在档 ✓）")

    # ---- 成片 B′/A′：sha256 + 副本出库 ----
    final = proj / "out" / "video_noaudio.mp4"
    if not final.exists():
        fail(f"成片缺失：{final}")
    copy_name = f"{'BPRIME' if side == 'B' else 'APRIME'}_video_noaudio.mp4"
    shutil.copy2(final, ev / copy_name)
    manifest_p = ev / "manifest.json"
    man = json.loads(manifest_p.read_text(encoding="utf-8")) if manifest_p.exists() else {}

    picks = None
    if side == "B":
        picks, rnd_idx = pick_segments(segs, head)
    else:
        b = man.get("B", {})
        picks = b.get("picks") or fail("A 捕获前缺 B 侧 manifest picks（先 capture --side B）")
        picks = [next(s for s in segs if s["si"] == p["si"]) for p in picks]

    entries = []
    pdir = c.cache / "vseg_pass"
    for seg in picks:
        for k, name in pick_passes(seg):
            want = pdir / f"{name}.{ext}"
            if not want.exists():
                fail(f"{'B' if side=='B' else 'A'} 中间物缺席：{want.name}"
                     f"（段 {seg['si']} [{seg['a']},{seg['b']}) pass {k}；键配方或代际不符）")
            fr = framemd5(want)
            if len(fr) != seg["b"] - seg["a"]:
                fail(f"{want.name} 帧数 {len(fr)} ≠ 段长 {seg['b']-seg['a']}")
            copy = ev / f"{side}_seg{seg['si']:02d}_p{k}_{name}.{ext}"
            shutil.copy2(want, copy)
            entries.append({"si": seg["si"], "a": seg["a"], "b": seg["b"], "k": k,
                            "name": f"{name}.{ext}", "sha256": sha256_file(want),
                            "copy": copy.name, "frames": len(fr)})
            print(f"  ✓ {side} 件 seg{seg['si']:02d} p{k} {name}.{ext} "
                  f"帧数={len(fr)} sha256={sha256_file(want)[:12]}… 副本出库")
    man[side] = {
        "head": head, "cuts": cuts, "edges": edges,
        "picks": [{"si": s["si"], "a": s["a"], "b": s["b"], "passes": s["passes"]} for s in picks],
        "final_sha256": sha256_file(final), "final_copy": copy_name,
        "final_frames": len(framemd5(final)),
        "pass_files": entries,
        "composite_sha256": sha256_file(DEFAULT_COMPOSITE),
        "ffmpeg_version": run(["ffmpeg", "-version"]).stdout.splitlines()[0],
        "timing_lines": len(timing),
    }
    manifest_p.write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ {side} 侧捕获落档：{manifest_p}（成片 sha256={man[side]['final_sha256'][:12]}…）")


# ---------------------------------------------------------------- 比对（硬条款③④）
def cmd_compare(args):
    proj, ev = Path(args.proj).resolve(), Path(args.ev).resolve()
    man = json.loads((ev / "manifest.json").read_text(encoding="utf-8"))
    B, A = man.get("B"), man.get("A") or fail("manifest 缺 A 侧（先 capture --side A）")
    B = B or fail("manifest 缺 B 侧（先 capture --side B）")
    report = {"③_cut_windows": [], "④_pass_pairs": []}
    ok = True

    # ---- ③ 切点帧窗 ----
    fb, fa = framemd5(ev / B["final_copy"]), framemd5(ev / A["final_copy"])
    if len(fb) != len(fa):
        ok = False
        print(f"✗ ③ 成片总帧数不等：B′={len(fb)} A′={len(fa)}")
    for s in B["cuts"]:
        win = [i for i in (s - 1, s, s + 1) if 0 <= i < len(fb) and i < len(fa)]
        bad = [i for i in win if fb[i] != fa[i]]
        entry = {"cut": s, "window": win, "diff_frames": bad}
        report["③_cut_windows"].append(entry)
        if bad:
            ok = False
            print(f"✗ ③ 切点 S={s} 帧窗差：{bad}")
        else:
            print(f"  ✓ ③ 切点 S={s} 帧[{s-1},{s+1}] 逐帧全同")
    # F1 形态显形：全片帧差定位（非门，供归因档）
    diffs = [i for i, (x, y) in enumerate(zip(fb, fa)) if x != y]
    report["③_full_diff_frames"] = diffs
    if diffs:
        print(f"  ⚠ ③ 成片全片帧差（非门，归因档）：{len(diffs)} 帧 @ {diffs[:12]}…——若集中于 a=0 段层窗口"
              f"末端边界帧（rel_out≡1,2 (mod 3)）=F1 pts 网格差形态（见 composite.py docstring）")

    # ---- ④ 中间物 B/A 容器异构比对 ----
    for bfe, afe in zip(B["pass_files"], A["pass_files"]):
        if (bfe["si"], bfe["k"], bfe["a"], bfe["b"]) != (afe["si"], afe["k"], afe["a"], bfe["b"]):
            fail(f"④ 配对错位：B={bfe} A={afe}")
        pb, pa = ev / bfe["copy"], ev / afe["copy"]
        mb, ma = framemd5(pb), framemd5(pa)
        pair_ok = len(mb) == len(ma) == bfe["frames"] and mb == ma
        diffs = [i for i, (x, y) in enumerate(zip(mb, ma)) if x != y]
        report["④_pass_pairs"].append({"si": bfe["si"], "k": bfe["k"], "frames": len(mb),
                                       "diff_frames": diffs, "ok": pair_ok})
        if pair_ok:
            print(f"  ✓ ④ seg{bfe['si']:02d} pass {bfe['k']}：{len(mb)}=={len(ma)} 行，$NF 逐帧全同"
                  f"（B={bfe['name']} vs A={afe['name']}，容器异构）")
        else:
            ok = False
            f1 = (bfe["si"] == 0 and len(diffs) <= 2 * (bfe["b"] - bfe["a"]) // 30 + 2)
            print(f"✗ ④ seg{bfe['si']:02d} pass {bfe['k']}：差异帧 {diffs[:12]}"
                  f"{'（seg00 边界帧形态=F1 pts 网格差候选，走异常扩样序/R4，候 L3 裁定）' if f1 else ''}")
    (ev / "compare_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    if not ok:
        fail(f"硬条款③④比对红（报告 {ev}/compare_report.json）")
    print(f"✓ 硬条款③④全绿（四组哈希在档：B′={B['final_sha256'][:12]}… A′={A['final_sha256'][:12]}…"
          f" + {len(B['pass_files'])}×2 中间物）")


# ---------------------------------------------------------------- ① golden / ② qc
def cmd_golden(args):
    proj = Path(args.proj).resolve()
    golden = Path(args.golden).resolve() if args.golden else proj / "gfx_golden.json"
    r = run(["env", "python3", str(REPO / "scripts" / "gfx_golden.py"), "compare",
             "-t", str(proj / "timeline.json"), "--golden", str(golden)], cwd=REPO)
    (Path(args.ev).resolve() / "golden.log").write_text(r.stdout + "\n--stderr--\n" + r.stderr,
                                                        encoding="utf-8") if args.ev else None
    if r.returncode != 0:
        fail(f"① golden compare rc={r.returncode}（{r.stdout[-400:]}{r.stderr[-200:]}）")
    print(f"✓ ① golden compare 全过（{golden}）")


def cmd_qc(args):
    proj = Path(args.proj).resolve()
    r = run(["env", "python3", "-X", "utf8", str(REPO / "vtx" / "vtx.py"),
             "-t", str(proj / "timeline.json"), "qc"], cwd=REPO)
    if args.ev:
        (Path(args.ev).resolve() / "qc.log").write_text(r.stdout + "\n--stderr--\n" + r.stderr,
                                                        encoding="utf-8")
    m = re.search(r"QC：(\d+)/(\d+)", r.stdout)
    if r.returncode != 0:
        fail(f"② qc rc={r.returncode}（{r.stderr[-300:]}）")
    if not m:
        fail("② qc stdout 未检出「QC：N/N」行")
    eq, tot = int(m.group(1)), int(m.group(2))
    if eq != tot or (args.expect and eq != args.expect):
        fail(f"② qc 通过数 {eq}/{tot} 不符（expect={args.expect or 'N==N'}）")
    print(f"✓ ② qc {eq}/{tot} exit 0")


# ---------------------------------------------------------------- 编排
def main():
    ap = argparse.ArgumentParser(description="R-07c 回归用例件（①②③④+指纹+负例+四组哈希落档）")
    ap.add_argument("--composite", default=str(DEFAULT_COMPOSITE), help="指纹对象件（增 14.2 参数化）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fingerprints")
    sub.add_parser("selftest-negative")
    for name in ("golden", "qc", "capture", "compare"):
        s = sub.add_parser(name)
        s.add_argument("--proj", required=name != "compare")
        if name == "golden":
            s.add_argument("--golden", default=None)
        if name == "qc":
            s.add_argument("--expect", type=int, default=76)
        if name in ("capture", "compare"):
            s.add_argument("--ev", required=True)
        if name == "capture":
            s.add_argument("--side", required=True, choices=["B", "A"])
            s.add_argument("--head", default=None, help="随机段哈希 HEAD 锚（默认 git HEAD；B 侧固化后 A 复用）")
            s.add_argument("--no-nvenc", action="store_true")
    p = sub.add_parser("all")
    p.add_argument("--proj", required=True)
    p.add_argument("--ev", required=True)
    p.add_argument("--expect-qc", type=int, default=76)
    p.add_argument("--skip-golden", action="store_true", help="golden 件缺席时显式跳过（须在回执申报）")
    p.add_argument("--no-nvenc", action="store_true")
    args = ap.parse_args()

    if args.cmd == "fingerprints":
        cmd_fingerprints(args)
    elif args.cmd == "selftest-negative":
        cmd_selftest_negative(args)
    elif args.cmd == "golden":
        cmd_golden(args)
    elif args.cmd == "qc":
        cmd_qc(args)
    elif args.cmd == "capture":
        cmd_capture(args)
    elif args.cmd == "compare":
        cmd_compare(args)
    elif args.cmd == "all":
        proj, ev = Path(args.proj).resolve(), Path(args.ev).resolve()
        ev.mkdir(parents=True, exist_ok=True)
        print("==〔1/6〕七式指纹断言 ==")
        cmd_fingerprints(args)
        print("==〔2/6〕负例双态自证 ==")
        cmd_selftest_negative(args)
        if not args.skip_golden:
            print("==〔3/6〕硬条款① golden ==")
            args.ev = str(ev)
            cmd_golden(args)
        else:
            print("==〔3/6〕硬条款① golden：SKIP（--skip-golden，须随回执申报）==")
        print("==〔4/6〕硬条款② qc ==")
        cmd_qc(args)
        print("==〔5/6〕B′/B 捕获（写态前旧码温态；本件须先于件①落树调用）==")
        args.side = "B"
        cmd_capture(args)
        print("==〔6/6〕A′/A 捕获 + ③④ 比对（写态落树+热身轮后调用）==")
        args.side = "A"
        cmd_capture(args)
        cmd_compare(args)
        print("✓ R-07c 回归件编排全绿")


if __name__ == "__main__":
    main()
