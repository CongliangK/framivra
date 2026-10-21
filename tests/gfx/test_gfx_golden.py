"""gfx_golden.py 单测：PIL 现场生成小 PNG（64×48）构造假缓存目录 + 假 timeline
（无 chrome 依赖），覆盖 build 抽样/全帧、帧缺失报错、compare 全零/差异/帧缺失、
同页双 dur 并列、名单逐项零匹配报错（B2）、缓存未重渲新鲜度防线（B1 默认警告/
--strict 失败）、页 html 改键后 compare 显式失败（目录缺失，绝不静默错比）。
golden 键与帧目录定位复用 vtx/hygiene.gfx_key（与渲染器同键）。

#104 起扩展：analyze_frame_diff 三级像素裁决（Tier-1 Exact / Tier-2
Premul-Equivalent / Tier-3 Failure+labels）——DoD 四类合成帧分类准确率 100%、
残差 ≤1 容差边界、黑边/α 差异/全透明区差异反例、热力图（Tier-2/3 产 Tier-1
不产、绿/红通道分工、原子写）、analyze CLI（目录/单文件/缺帧配对/退出码）。

#104 返工（红队性能验收修复）追加：全部 Tier-1/Tier-2 通过态路径 1080p 分析段
# 中位 ≤15ms 五路径性能门（全等/稀疏/30% 稠密/100% 稠密/全屏中 α=128·ΔRGB=2）；
# 快速通道界 256 的健全+最紧锁定（走廊数学根基）；halo 签名 ±1 精度锁定
# （M2b 变异 ±3 存活修复）；走廊≡一般路径全字段等价回归。"""
import contextlib
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
from scripts.gfx import gfx_golden as gg  # noqa: E402
from PIL import Image    # noqa: E402

W, H = 64, 48


def _make_frame(path: Path, i: int, tamper: bool = False):
    """第 i 帧内容随帧号确定变化；tamper=True 改一个像素（模拟渲染差异）。"""
    im = Image.new("RGBA", (W, H), (i % 256, (i * 7) % 256, (i * 13) % 256, 255))
    if tamper:
        im.putpixel((0, 0), (255, 255, 255, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)


def _project(root: Path, clips):
    """clips = [(src, dur)]：写假 html + timeline.json，返回 timeline 路径。"""
    for src, _ in clips:
        p = root / src
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("<html><body>fake gfx page</body></html>", encoding="utf-8")
    tl = {"project": {"fps": 30, "duration_frames": sum(d for _, d in clips),
                      "width": 1920, "height": 1080},
          "sources": [], "video_tracks": [{"name": "gfx", "kind": "gfx",
                                           "clips": [{"src": s, "dst_in": 0, "dur": d}
                                                     for s, d in clips]}],
          "subtitles": [], "audio_tracks": []}
    tp = root / "timeline.json"
    tp.write_text(json.dumps(tl), encoding="utf-8")
    return tp


def _fill_cache(tp: Path, clips, skip=(), tamper=()):
    """按 hygiene.gfx_key 现场生成帧产物（与被测工具同一套键）。"""
    c = gg.make_ctx(tp)
    for src, dur in clips:
        d = gg.hygiene.gfx_dir(c, {"src": src, "dur": dur})
        for i in range(dur):
            if i in skip:
                continue
            _make_frame(d / f"f{i:04d}.png", i, tamper=(src, i) in tamper)


class GfxGolden(unittest.TestCase):
    def setUp(self):
        from tests.gfx._environment import bind_environment
        bind_environment(self)
        browser = mock.patch.object(gg, "chrome_version", return_value="unit fixture; not real browser")
        browser.start()
        self.addCleanup(browser.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_build_sample_and_full(self):
        """抽样页取 ≥sample 帧且含首中尾；--full-pages 页全帧；mode 标记正确。"""
        tp = _project(self.root, [("pages/a.html", 10), ("pages/b.html", 7)])
        _fill_cache(tp, [("pages/a.html", 10), ("pages/b.html", 7)])
        g = self.root / "golden.json"
        rc = gg.main(["build", "-t", str(tp), "--golden", str(g),
                      "--full-pages", "b", "--sample", "5"])
        self.assertEqual(rc, 0)
        golden = json.loads(g.read_text(encoding="utf-8"))
        self.assertEqual(golden["meta"]["viewport"], "1920x1080")
        self.assertEqual(golden["meta"]["fps"], 30)
        a = golden["pages"]["pages/a.html"]["durs"]["10"]
        b = golden["pages"]["pages/b.html"]["durs"]["7"]
        self.assertEqual(a["mode"], "sample")
        self.assertEqual(b["mode"], "full")
        self.assertEqual(sorted(map(int, a["frames"])), gg.sample_frames(10, 5))
        self.assertGreaterEqual(len(a["frames"]), 5)
        for must in (0, 5, 9):   # 首/中/尾必含
            self.assertIn(f"{must:04d}", a["frames"])
        self.assertEqual(sorted(map(int, b["frames"])), list(range(7)))
        for inst in (a, b):
            for h in inst["frames"].values():   # 64 hex sha256
                self.assertEqual(len(h), 64)
                int(h, 16)
        self.assertNotEqual(a["key"], b["key"])  # dur 入键 → 两页不同目录

    def test_build_missing_frame_errors(self):
        """帧缺失 → build 报错退出非零，golden 不落盘。"""
        tp = _project(self.root, [("pages/a.html", 10)])
        _fill_cache(tp, [("pages/a.html", 10)], skip=(2,))   # 抽样必含帧 2
        g = self.root / "golden.json"
        rc = gg.main(["build", "-t", str(tp), "--golden", str(g)])
        self.assertEqual(rc, 1)
        self.assertFalse(g.exists())
        # 目录整个缺失同理
        tp2 = _project(self.root, [("pages/none.html", 4)])
        rc = gg.main(["build", "-t", str(tp2), "--golden", str(self.root / "g2.json")])
        self.assertEqual(rc, 1)

    def test_compare_identical_diff_missing(self):
        """全零差异 exit 0；篡改一帧像素 → exit 1 报首个差异帧与双 hash；
        还原 → exit 0；删帧 → exit 1 报帧缺失。"""
        tp = _project(self.root, [("pages/a.html", 10)])
        _fill_cache(tp, [("pages/a.html", 10)])
        g = self.root / "golden.json"
        self.assertEqual(gg.main(["build", "-t", str(tp), "--golden", str(g)]), 0)
        report = self.root / "report.json"
        self.assertEqual(gg.main(["compare", "-t", str(tp), "--golden", str(g),
                                  "--report", str(report)]), 0)
        rep = json.loads(report.read_text(encoding="utf-8"))
        self.assertTrue(rep["ok"])

        # 篡改中帧（dur//2=5，抽样契约必含）一个像素 → 像素 hash 必变（本工具立身之本）
        c = gg.make_ctx(tp)
        f5 = gg.hygiene.gfx_dir(c, {"src": "pages/a.html", "dur": 10}) / "f0005.png"
        _make_frame(f5, 5, tamper=True)
        report2 = self.root / "report2.json"
        self.assertEqual(gg.main(["compare", "-t", str(tp), "--golden", str(g),
                                  "--report", str(report2)]), 1)
        rep2 = json.loads(report2.read_text(encoding="utf-8"))
        self.assertFalse(rep2["ok"])
        self.assertEqual(len(rep2["differ"]), 1)
        d = rep2["differ"][0]
        self.assertEqual(d["frame"], "0005")
        self.assertNotEqual(d["golden"], d["current"])
        golden = json.loads(g.read_text(encoding="utf-8"))
        self.assertEqual(d["golden"],
                         golden["pages"]["pages/a.html"]["durs"]["10"]["frames"]["0005"])

        # 还原 → 全零
        _make_frame(f5, 5)
        self.assertEqual(gg.main(["compare", "-t", str(tp), "--golden", str(g)]), 0)

        # 删中帧 → 帧缺失，exit 1
        f5.unlink()
        report3 = self.root / "report3.json"
        self.assertEqual(gg.main(["compare", "-t", str(tp), "--golden", str(g),
                                  "--report", str(report3)]), 1)
        rep3 = json.loads(report3.read_text(encoding="utf-8"))
        self.assertEqual(rep3["missing_or_unreadable"][0]["frame"], "0005")

    def test_same_page_two_durs(self):
        """同页双 dur → 同一 src 键下 durs 并列、缓存键不同；compare 双实例全零。"""
        tp = _project(self.root, [("pages/a.html", 6), ("pages/a.html", 9)])
        _fill_cache(tp, [("pages/a.html", 6), ("pages/a.html", 9)])
        g = self.root / "golden.json"
        self.assertEqual(gg.main(["build", "-t", str(tp), "--golden", str(g),
                                  "--sample", "3"]), 0)
        golden = json.loads(g.read_text(encoding="utf-8"))
        durs = golden["pages"]["pages/a.html"]["durs"]
        self.assertEqual(sorted(durs), ["6", "9"])
        self.assertNotEqual(durs["6"]["key"], durs["9"]["key"])
        report = self.root / "report.json"
        self.assertEqual(gg.main(["compare", "-t", str(tp), "--golden", str(g),
                                  "--report", str(report)]), 0)
        rep = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(len(rep["identical"]), 2)
        # 只篡改 dur=6 实例的一帧 → 精确定位到该实例
        c = gg.make_ctx(tp)
        f0 = gg.hygiene.gfx_dir(c, {"src": "pages/a.html", "dur": 6}) / "f0000.png"
        _make_frame(f0, 0, tamper=True)
        self.assertEqual(gg.main(["compare", "-t", str(tp), "--golden", str(g)]), 1)


    def test_build_rejects_unmatched_full_pages(self):
        """B2：build --full-pages 名单逐项零匹配 → 报错退出非零，golden 不落盘。"""
        tp = _project(self.root, [("pages/a.html", 6)])
        _fill_cache(tp, [("pages/a.html", 6)])
        g = self.root / "golden.json"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = gg.main(["build", "-t", str(tp), "--golden", str(g),
                          "--full-pages", "no_such_page,c5a_main"])
        self.assertEqual(rc, 1)
        self.assertFalse(g.exists())
        self.assertIn("逐项零匹配", buf.getvalue())

    def test_compare_rejects_unmatched_pages(self):
        """B2：compare --pages 名单中有项在 golden 零匹配 → 报错退出非零
        （防拼错时其余名单静默通过被误当全量比对）。"""
        tp = _project(self.root, [("pages/a.html", 6)])
        _fill_cache(tp, [("pages/a.html", 6)])
        g = self.root / "golden.json"
        self.assertEqual(gg.main(["build", "-t", str(tp), "--golden", str(g)]), 0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = gg.main(["compare", "-t", str(tp), "--golden", str(g),
                          "--pages", "a,no_such_page"])
        self.assertEqual(rc, 1)
        self.assertIn("逐项零匹配", buf.getvalue())
        # 名单全部可匹配 → 正常
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gg.main(["compare", "-t", str(tp), "--golden", str(g),
                                      "--pages", "a"]), 0)

    def test_compare_stale_cache_warn_and_strict(self):
        """B1：golden 建后缓存未重渲（帧 mtime ≤ built）→ 默认显式警告但 exit 0，
        --strict 判失败 exit 1；重渲一帧后（mtime 变新）恢复正常。"""
        tp = _project(self.root, [("pages/a.html", 6)])
        _fill_cache(tp, [("pages/a.html", 6)])
        g = self.root / "golden.json"
        self.assertEqual(gg.main(["build", "-t", str(tp), "--golden", str(g)]), 0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(gg.main(["compare", "-t", str(tp), "--golden", str(g)]), 0)
        self.assertIn("缓存未重渲", buf.getvalue())
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            self.assertEqual(gg.main(["compare", "-t", str(tp), "--golden", str(g),
                                      "--strict"]), 1)
        # 重渲一帧（内容不变，mtime 显式推到 built+120s 越过 2s 容差）→ 防线解除
        import time as _time
        golden = json.loads(g.read_text(encoding="utf-8"))
        built = _time.mktime(_time.strptime(golden["meta"]["built"],
                                            "%Y-%m-%d %H:%M:%S"))
        c = gg.make_ctx(tp)
        f0 = gg.hygiene.gfx_dir(c, {"src": "pages/a.html", "dur": 6}) / "f0000.png"
        _make_frame(f0, 0)
        import os as _os
        _os.utime(f0, (built + 120, built + 120))
        buf3 = io.StringIO()
        with contextlib.redirect_stdout(buf3):
            self.assertEqual(gg.main(["compare", "-t", str(tp), "--golden", str(g),
                                      "--strict"]), 0)
        self.assertNotIn("缓存未重渲", buf3.getvalue())

    def test_compare_fails_explicitly_after_html_key_change(self):
        """页 html 改键（审查指出的缺失用例）：html 变 → gfx_key 变 → 新键目录
        不存在 → compare 显式失败（目录缺失），绝不静默错比旧目录。"""
        tp = _project(self.root, [("pages/a.html", 6)])
        _fill_cache(tp, [("pages/a.html", 6)])
        g = self.root / "golden.json"
        self.assertEqual(gg.main(["build", "-t", str(tp), "--golden", str(g)]), 0)
        golden = json.loads(g.read_text(encoding="utf-8"))
        old_key = golden["pages"]["pages/a.html"]["durs"]["6"]["key"]
        (self.root / "pages/a.html").write_text("<html>changed</html>", encoding="utf-8")
        report = self.root / "report.json"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(gg.main(["compare", "-t", str(tp), "--golden", str(g),
                                      "--report", str(report)]), 1)
        rep = json.loads(report.read_text(encoding="utf-8"))
        self.assertFalse(rep["ok"])
        self.assertEqual(len(rep["dir_missing"]), 1)
        self.assertNotEqual(rep["dir_missing"][0]["key"], old_key)  # 键确实随内容变了
        self.assertIn("目录缺失", buf.getvalue())

    def test_golden_write_is_atomic_no_tmp_left(self):
        """B4：golden 落盘走 tmp+os.replace，成功后不留 .tmp 残件。"""
        tp = _project(self.root, [("pages/a.html", 4)])
        _fill_cache(tp, [("pages/a.html", 4)])
        g = self.root / "golden.json"
        self.assertEqual(gg.main(["build", "-t", str(tp), "--golden", str(g)]), 0)
        self.assertTrue(g.exists())
        self.assertFalse((self.root / "golden.json.tmp").exists())


    def test_build_zero_duration_has_no_frame_proof(self):
        tp = _project(self.root, [("a.html", 0)])
        context = gg.make_ctx(tp)
        gg.hygiene.gfx_dir(context, {"src": "a.html", "dur": 0}).mkdir(parents=True)
        golden = self.root / "golden.json"
        self.assertEqual(gg.main(["build", "-t", str(tp), "--golden", str(golden)]), 1)
        self.assertFalse(golden.exists())

    def test_missing_golden_proof_cannot_pass(self):
        clips = [("a.html", 4)]
        tp = _project(self.root, clips)
        _fill_cache(tp, clips)
        golden, report = self.root / "golden.json", self.root / "report.json"
        self.assertEqual(gg.main(["build", "-t", str(tp), "--golden", str(golden),
                                  "--full-pages", "a.html"]), 0)
        original = golden.read_text()
        for fault in ("empty_durs", "empty_frames", "missing_full_frame", "missing_sample_frame", "bad_hash", "zero_duration"):
            with self.subTest(fault=fault):
                doc = json.loads(original)
                inst = doc["pages"]["a.html"]["durs"]["4"]
                if fault == "empty_durs":
                    doc["pages"]["a.html"]["durs"] = {}
                elif fault == "empty_frames":
                    inst["frames"] = {}
                elif fault == "missing_full_frame":
                    del inst["frames"]["0001"]
                elif fault == "missing_sample_frame":
                    inst["mode"] = "sample"
                    del inst["frames"]["0001"]
                elif fault == "bad_hash":
                    inst["frames"]["0001"] = ""
                else:
                    inst["dur"] = 0
                golden.write_text(json.dumps(doc))
                self.assertEqual(gg.main(["compare", "-t", str(tp), "--golden", str(golden),
                                          "--report", str(report)]), 1)
                self.assertFalse(json.loads(report.read_text())["ok"])

    def test_declared_duration_and_sample_do_not_expand_unproved_work(self):
        clips = [("a.html", 4)]
        tp = _project(self.root, clips)
        _fill_cache(tp, clips)
        golden, report = self.root / "golden.json", self.root / "report.json"
        self.assertEqual(gg.main(["build", "-t", str(tp), "--golden", str(golden),
                                  "--full-pages", "a.html"]), 0)
        original = golden.read_text()
        huge = 10 ** 100
        for mode in ("full", "sample"):
            with self.subTest(mode=mode):
                doc = json.loads(original)
                inst = doc["pages"]["a.html"]["durs"].pop("4")
                inst.update(dur=huge, mode=mode)
                doc["pages"]["a.html"]["durs"][str(huge)] = inst
                doc["meta"]["sample"] = huge - 1
                golden.write_text(json.dumps(doc))
                with mock.patch.object(gg, "sample_frames", side_effect=AssertionError("unbounded sample")):
                    self.assertEqual(gg.main(["compare", "-t", str(tp), "--golden", str(golden),
                                              "--report", str(report)]), 1)
                self.assertFalse(json.loads(report.read_text())["ok"])
        # Oversampling a tiny duration means all frames, not huge sampling work.
        self.assertEqual(gg.sample_frames(4, huge), [0, 1, 2, 3])
        doc = json.loads(original)
        doc["pages"]["a.html"]["durs"]["4"]["mode"] = "sample"
        doc["meta"]["sample"] = huge
        golden.write_text(json.dumps(doc))
        with mock.patch.object(gg, "sample_frames", side_effect=AssertionError("unbounded sample")):
            self.assertEqual(gg.main(["compare", "-t", str(tp), "--golden", str(golden)]), 0)

    def test_strict_requires_parseable_freshness_evidence(self):
        clips = [("a.html", 4)]
        tp = _project(self.root, clips)
        _fill_cache(tp, clips)
        golden, report = self.root / "golden.json", self.root / "report.json"
        self.assertEqual(gg.main(["build", "-t", str(tp), "--golden", str(golden)]), 0)
        doc = json.loads(golden.read_text())
        doc["meta"]["built"] = "unknown"
        golden.write_text(json.dumps(doc))
        argv = ["compare", "-t", str(tp), "--golden", str(golden), "--report", str(report)]
        self.assertEqual(gg.main(argv), 0)  # legacy comparison is explicitly not strict
        self.assertIsNotNone(json.loads(report.read_text())["freshness_error"])
        self.assertEqual(gg.main(argv + ["--strict"]), 1)
        doc["meta"]["built"] = "2000-01-01 00:00:00"
        golden.write_text(json.dumps(doc))
        self.assertEqual(gg.main(argv + ["--strict"]), 0)

    def test_unreadable_instance_not_reported_as_identical(self):
        clips = [("a.html", 4)]
        tp = _project(self.root, clips)
        _fill_cache(tp, clips)
        golden, report = self.root / "golden.json", self.root / "report.json"
        self.assertEqual(gg.main(["build", "-t", str(tp), "--golden", str(golden)]), 0)
        directory = gg.hygiene.gfx_dir(gg.make_ctx(tp), {"src": "a.html", "dur": 4})
        for path in directory.glob("*.png"):
            path.unlink()
        self.assertEqual(gg.main(["compare", "-t", str(tp), "--golden", str(golden),
                                  "--report", str(report)]), 1)
        rep = json.loads(report.read_text())
        self.assertEqual(rep["identical"], [])
        self.assertEqual(rep["compared_frames"], 0)


# --------------------------------- #104 三级像素裁决：合成帧构造器（DoD 四类）

W2, H2 = 64, 48


def _rhu(num, den):
    """round-half-up(num/den)：与被测工具 _rdiv255 同口径的整数四舍五入。"""
    return (2 * num + den) // (2 * den)


def _save_png(arr, path: Path):
    Image.fromarray(arr.astype(np.uint8), "RGBA").save(path, "PNG")


def _pair_tier2(w=W2, h=H2):
    """DoD-1：A∈[1,15] 半透明渐变；两后端 Premul→Straight 整数舍入不同
    （甲=四舍五入 round(P·255/A)，乙=截断 floor）→ 同一预乘真值的两种直存。"""
    A = np.broadcast_to(1 + (np.arange(w) % 15), (h, w))
    P = np.arange(h)[:, None] % (A + 1)            # 每像素预乘真值 0..A
    s_up = np.minimum(255, _rhu(P * 255, A))
    s_dn = np.minimum(255, (P * 255) // A)
    ref = np.dstack([s_up, s_up, s_up, A]).astype(np.uint8)
    cand = np.dstack([s_dn, s_dn, s_dn, A]).astype(np.uint8)
    assert (s_up != s_dn).any()                    # 保证确有差异像素（非 Tier-1）
    return ref, cand


def _pair_halo(w=W2, h=H2):
    """DoD-2：预乘 RGB 未做除法直接写入 PNG（黑边/发黑签名）。"""
    A = np.broadcast_to(30 + (np.arange(w) % 171), (h, w))    # α ∈ [30, 200]
    C = np.broadcast_to(np.arange(h)[:, None] % 76 + 180, (h, w))  # 亮色 180..255
    prem = _rhu(C * A, 255)                        # round(C·A/255)：被误当 Straight 直存
    ref = np.dstack([C, C, C, A]).astype(np.uint8)
    cand = np.dstack([prem, prem, prem, A]).astype(np.uint8)
    return ref, cand


def _pair_opaque_shift(w=W2, h=H2):
    """DoD-3：A=255 纯色块 #1f6feb → #1f6fec（蓝通道 +1 色阶）。"""
    ref = np.zeros((h, w, 4), np.uint8)
    ref[..., 0], ref[..., 1], ref[..., 2], ref[..., 3] = 31, 111, 235, 255
    cand = ref.copy()
    cand[:, w // 2:, 2] = 236
    return ref, cand


class AnalyzeFrameDiffTiers(unittest.TestCase):
    """#104 DoD：四类人工合成帧，分类准确率必须 100%。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _analyze(self, ref, cand, heatmap_out=None):
        rp, cp = self.root / "ref.png", self.root / "cand.png"
        _save_png(ref, rp)
        _save_png(cand, cp)
        return gg.analyze_frame_diff(rp, cp, heatmap_out=heatmap_out)

    def test_dod1_premul_roundtrip_is_tier2(self):
        """A∈[1,15] 渐变的两种 Premul→Straight 舍入 → premul_equivalent，
        差异像素全落半透明区且全部计为逆预乘舍入点。"""
        r = self._analyze(*_pair_tier2())
        self.assertEqual(r["tier"], gg.TIER_PREMUL)
        self.assertEqual(r["labels"], [])
        self.assertEqual(r["max_abs_diff_alpha"], 0)
        self.assertGreater(r["n_diff_pixels"], 0)
        self.assertEqual(r["semi_diff_pixels"], r["n_diff_pixels"])
        self.assertEqual(r["opaque_diff_pixels"], 0)
        self.assertEqual(r["transparent_diff_pixels"], 0)
        self.assertEqual(r["premul_rounding_pixel_count"], r["n_diff_pixels"])
        self.assertLessEqual(r["max_premul_residual"], 1)
        self.assertIsNone(r["heatmap"])

    def test_dod2_double_blend_halo_is_tier3(self):
        """预乘值未除 α 直存 → visual_failure 且标签含 premul_double_blend_halo
        （半透明差异像素残差 >1 且全部命中 C_cand≈round(C_ref·A/255) 签名）。"""
        r = self._analyze(*_pair_halo())
        self.assertEqual(r["tier"], gg.TIER_FAIL)
        self.assertIn(gg.L_HALO, r["labels"])
        self.assertGreater(r["max_premul_residual"], 1)
        self.assertEqual(r["halo_signature_pixels"], r["semi_diff_pixels"])
        self.assertEqual(r["max_abs_diff_alpha"], 0)

    def test_dod3_opaque_shift_is_tier3(self):
        """A=255 区 1 色阶偏移 → visual_failure，标签恰为 opaque_color_shift。"""
        r = self._analyze(*_pair_opaque_shift())
        self.assertEqual(r["tier"], gg.TIER_FAIL)
        self.assertEqual(r["labels"], [gg.L_OPAQUE])
        self.assertEqual(r["max_abs_diff"], 1)
        self.assertEqual(r["n_diff_pixels"], H2 * (W2 - W2 // 2))
        self.assertEqual(r["opaque_diff_pixels"], r["n_diff_pixels"])

    def test_dod4_identical_is_tier1(self):
        """完全一致图像 → verified_exact（且即便给了 heatmap_out 也不产热力图）。"""
        ref, _ = _pair_opaque_shift()
        hm = self.root / "diff_f0000.png"
        r = self._analyze(ref, ref.copy(), heatmap_out=hm)
        self.assertEqual(r["tier"], gg.TIER_EXACT)
        self.assertEqual(r["labels"], [])
        self.assertEqual(r["max_abs_diff"], 0)
        self.assertEqual(r["n_diff_pixels"], 0)
        self.assertEqual(r["premul_rounding_pixel_count"], 0)
        self.assertIsNone(r["heatmap"])
        self.assertFalse(hm.exists())              # Tier-1 恒不产热力图


class AnalyzeFrameDiffEdges(unittest.TestCase):
    """#104 裁决边界：容差分界 / 反例 / 输入口径 / 异常。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _analyze(self, ref, cand):
        rp, cp = self.root / "ref.png", self.root / "cand.png"
        _save_png(ref, rp)
        _save_png(cand, cp)
        return gg.analyze_frame_diff(rp, cp)

    @staticmethod
    def _base(pair_pixel=(100, 100, 100, 128)):
        b = np.zeros((8, 8, 4), np.uint8)
        b[..., :3], b[..., 3] = (10, 20, 30), 255
        b[4, 4] = pair_pixel
        return b

    def test_premul_residual_exactly_1_is_tier2(self):
        """A=128、Straight 100 vs 101：重投影残差恰 1（50→51）→ Tier-2（容差上边界）。"""
        cand = self._base()
        cand[4, 4, :3] = 101
        r = self._analyze(self._base(), cand)
        self.assertEqual(r["tier"], gg.TIER_PREMUL)
        self.assertEqual(r["max_premul_residual"], 1)
        self.assertEqual(r["premul_rounding_pixel_count"], 1)

    def test_white_bg_residual_computed_when_alpha_differs(self):
        """α 不同的半透明像素：白底残差(44) > 黑底残差(28)，max_premul_residual
        取双底色 max —— 锁定白底链语义（α 相等时白底≡黑底是数学恒等，不触发）。"""
        cand = self._base()
        cand[4, 4] = (100, 100, 100, 200)   # 黑底 50 vs 78=28；白底 177 vs 133=44
        r = self._analyze(self._base(), cand)
        self.assertEqual(r["tier"], gg.TIER_FAIL)
        self.assertIn(gg.L_ALPHA, r["labels"])
        self.assertEqual(r["max_premul_residual"], 44)

    def test_premul_residual_2_is_tier3_unclassified(self):
        """A=128、Straight 100 vs 103：残差 2 超容差 → Tier-3；无预乘签名、
        max|Δ|=3 ≤5 → 四官方标签全不命中，兜底 unclassified_diff 且 labels 非空。"""
        cand = self._base()
        cand[4, 4, :3] = 103
        r = self._analyze(self._base(), cand)
        self.assertEqual(r["tier"], gg.TIER_FAIL)
        self.assertEqual(r["max_premul_residual"], 2)
        self.assertEqual(r["labels"], [gg.L_UNCLASS])

    def test_alpha_only_diff_is_tier3_alpha_label(self):
        """仅 α 通道差（255→200）→ alpha_channel_diff；|ΔA|=55>5 同时命中 structural。"""
        cand = self._base(pair_pixel=(100, 100, 100, 255))
        cand[2, 2, 3] = 200
        r = self._analyze(self._base(pair_pixel=(100, 100, 100, 255)), cand)
        self.assertEqual(r["tier"], gg.TIER_FAIL)
        self.assertEqual(r["max_abs_diff_alpha"], 55)
        self.assertEqual(r["labels"], [gg.L_ALPHA, gg.L_STRUCT])

    def test_transparent_region_rgb_diff_is_failure(self):
        """全透明区（A=0）RGB 差异：合成上不可见但违反「差异像素须在半透明区」
        → Tier-3（tier2 判据不含此区），标签走兜底 unclassified_diff。"""
        ref = np.zeros((8, 8, 4), np.uint8)
        ref[..., :3] = 5
        cand = ref.copy()
        cand[1, 1, 0] = 8
        r = self._analyze(ref, cand)
        self.assertEqual(r["tier"], gg.TIER_FAIL)
        self.assertEqual(r["transparent_diff_pixels"], 1)
        self.assertEqual(r["labels"], [gg.L_UNCLASS])

    def test_structural_big_shift(self):
        """不透明区大幅色差（255→0，撕裂/丢层形态）→ structural_or_missing_layer。"""
        ref, _ = _pair_opaque_shift(32, 24)
        cand = ref.copy()
        cand[:, 8:, :3] = 0
        r = self._analyze(ref, cand)
        self.assertEqual(r["tier"], gg.TIER_FAIL)
        self.assertIn(gg.L_STRUCT, r["labels"])
        self.assertGreater(r["max_abs_diff"], 5)

    def test_quick_bound_256_sound_and_tight(self):
        """快速通道界 α·ΔC ≤ 256 的健全性与最紧性（走廊/quick 通道的数学根基）。

        健全：∀A∈[1,254]，取 ΔC=min(256//A,255)，∀C_r∈[0,255−ΔC] 残差 ≤1
        （A∈[1,254]×C_r×C_c 全域穷举的边界行锁定）；最紧：积 258 即有反例
        （A=2, C 63→192；A=129, C 84→86，残差恰 2）→ 界不可放宽；端到端反例
        帧必须 Tier-3（防界放宽与 |Δ|>128 回绕陷阱双双回归）。"""
        C = np.arange(256)
        for a in range(1, 255):
            dc = min(256 // a, 255)
            f = (2 * C * a + 255) // 510           # round_hu(C·A/255) 契约公式
            res = np.abs(f[dc:] - f[:256 - dc])     # 全部 C_r 的残差
            self.assertLessEqual(int(res.max()), 1, f"A={a} ΔC={dc}")
        for a, c0, c1 in ((2, 63, 192), (129, 84, 86)):
            f = lambda c: (2 * c * a + 255) // 510  # noqa: E731
            self.assertEqual(abs(f(c1) - f(c0)), 2)  # 积 258 反例：界 256 最紧
        ref = np.zeros((4, 4, 4), np.uint8)         # 端到端：反例帧必须失败
        ref[..., 3] = 2
        ref[..., 0] = 63
        cand = ref.copy()
        cand[..., 0] = 192                          # |Δ|=129 → 积 258 > 256
        r = self._analyze(ref, cand)
        self.assertEqual(r["tier"], gg.TIER_FAIL)
        self.assertEqual(r["max_premul_residual"], 2)
        self.assertEqual(r["labels"], [gg.L_STRUCT])

    def test_halo_signature_threshold_precision(self):
        """halo 签名精度锁定 ±1（M2b 变异「阈值放宽 ±3 无测试击杀」修复）。

        签名距离恰 1 → 命中（收紧到 0 即被本例杀死）；距离恰 2 → 不命中
        （放宽到 ±2/±3 即被本例杀死）。A=128 时 round(100·128/255)=50：
        cand C=51 距离 1、C=52 距离 2，两者残差均 24>1（同入签名判定集）。"""
        def pair(cc):
            ref = self._base(pair_pixel=(100, 100, 100, 128))
            cand = self._base(pair_pixel=(100, 100, 100, 128))
            cand[4, 4, :3] = cc
            return ref, cand

        r1 = self._analyze(*pair(51))
        self.assertEqual(r1["tier"], gg.TIER_FAIL)
        self.assertEqual(r1["halo_signature_pixels"], 1)
        self.assertIn(gg.L_HALO, r1["labels"])      # 距离 1：±1 命中
        r2 = self._analyze(*pair(52))
        self.assertEqual(r2["tier"], gg.TIER_FAIL)
        self.assertEqual(r2["halo_signature_pixels"], 0)   # 距离 2：±1 不命中
        self.assertNotIn(gg.L_HALO, r2["labels"])
        self.assertEqual(r2["labels"], [gg.L_STRUCT])      # mad=48>5，无 halo

    def test_rgb_png_input_normalized_to_rgba(self):
        """无 α 的 RGB PNG 双方一致 → 统一 convert RGBA 口径后 Tier-1。"""
        arr = np.zeros((16, 16, 3), np.uint8)
        arr[..., 0] = np.arange(16 * 16).reshape(16, 16) % 256
        rp, cp = self.root / "r.png", self.root / "c.png"
        Image.fromarray(arr, "RGB").save(rp)
        Image.fromarray(arr.copy(), "RGB").save(cp)
        r = gg.analyze_frame_diff(rp, cp)
        self.assertEqual(r["tier"], gg.TIER_EXACT)

    def test_size_mismatch_raises_value_error(self):
        """尺寸不一致 → ValueError（CLI 归入 errors，不静默错比）。"""
        rp, cp = self.root / "r.png", self.root / "c.png"
        _save_png(np.zeros((8, 8, 4), np.uint8), rp)
        _save_png(np.zeros((9, 8, 4), np.uint8), cp)
        with self.assertRaises(ValueError):
            gg.analyze_frame_diff(rp, cp)

    def test_corrupt_png_raises_value_error(self):
        (self.root / "bad.png").write_bytes(b"not a png")
        _save_png(np.zeros((8, 8, 4), np.uint8), self.root / "ok.png")
        with self.assertRaises(ValueError):
            gg.analyze_frame_diff(self.root / "ok.png", self.root / "bad.png")


class AnalyzeHeatmap(unittest.TestCase):
    """#104 热力图：Tier-2/3 产出、Tier-1 不产；绿/红分工；原子写。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.hm = self.root / "heat"
        self.addCleanup(self._tmp.cleanup)

    def _analyze(self, ref, cand, name="f0000"):
        rp, cp = self.root / "ref.png", self.root / "cand.png"
        _save_png(ref, rp)
        _save_png(cand, cp)
        return gg.analyze_frame_diff(rp, cp, heatmap_out=self.hm / f"diff_{name}.png")

    def test_tier2_heatmap_green_only(self):
        r = self._analyze(*_pair_tier2(), name="f0001")
        self.assertEqual(r["heatmap"], str(self.hm / "diff_f0001.png"))
        with Image.open(r["heatmap"]) as im:
            self.assertEqual(im.mode, "RGB")
            self.assertEqual(im.size, (2 * W2, H2))       # 双半幅：宽 2W
            arr = np.asarray(im)
        self.assertGreater(arr[:, :W2].max(), 0)          # 左半幅有原图内容
        self.assertGreater(arr[:, W2:, 1].sum(), 0)       # 右半幅绿=Tier-2 舍入点
        self.assertEqual(arr[:, W2:, 0].sum(), 0)         # 无红（无实质错误像素）

    def test_tier3_heatmap_red_only(self):
        r = self._analyze(*_pair_opaque_shift(), name="f0002")
        with Image.open(r["heatmap"]) as im:
            arr = np.asarray(im)
        self.assertGreater(arr[:, W2:, 0].sum(), 0)       # 红=实质错误像素
        self.assertEqual(arr[:, W2:, 1].sum(), 0)         # 无绿（无半透明舍入点）

    def test_atomic_write_no_tmp_left(self):
        self._analyze(*_pair_tier2(), name="f0003")
        self._analyze(*_pair_opaque_shift(), name="f0004")
        self.assertEqual(list(self.hm.glob("*.tmp")), [])  # tmp+replace，无残件
        self.assertEqual(sorted(p.name for p in self.hm.glob("*.png")),
                         ["diff_f0003.png", "diff_f0004.png"])


class AnalyzeCli(unittest.TestCase):
    """#104 analyze CLI：目录配对/单文件/缺帧/类型混用/退出码/报告与热力图。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _dirs(self, pairs):
        """pairs = {帧号: (ref_arr, cand_arr)} → 写出 ref/ cand/ 两帧目录。"""
        rd, cd = self.root / "ref", self.root / "cand"
        rd.mkdir(parents=True, exist_ok=True)
        cd.mkdir(parents=True, exist_ok=True)
        for num, (ref, cand) in pairs.items():
            _save_png(ref, rd / f"f{num:04d}.png")
            _save_png(cand, cd / f"f{num:04d}.png")
        return rd, cd

    def test_dir_mode_report_heatmaps_and_rc(self):
        """Tier-1/2/3 各一帧 + 一帧 ref 独有：rc 1；报告分档计数 + 缺帧错误；
        热力图只产 Tier-2/3 两帧。"""
        t2_ref, t2_cand = _pair_tier2()
        t3_ref, t3_cand = _pair_opaque_shift()
        rd, cd = self._dirs({0: (t3_ref, t3_ref.copy()),      # Tier-1
                             1: (t2_ref, t2_cand),             # Tier-2
                             2: (t3_ref, t3_cand)})            # Tier-3
        _save_png(t3_ref, rd / "f0003.png")                    # ref 独有 → 配对错误
        hm, rj = self.root / "heat", self.root / "report.json"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = gg.main(["analyze", "--ref", str(rd), "--cand", str(cd),
                          "--heatmap-dir", str(hm), "--report", str(rj)])
        self.assertEqual(rc, 1)
        rep = json.loads(rj.read_text(encoding="utf-8"))
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["compared"], 3)
        self.assertEqual(rep["tiers"], {"verified_exact": 1,
                                        "premul_equivalent": 1,
                                        "visual_failure": 1})
        self.assertEqual(rep["frames"][0]["tier"], gg.TIER_EXACT)
        self.assertEqual(rep["frames"][2]["labels"], [gg.L_OPAQUE])
        self.assertEqual(rep["errors"][0]["frame"], "0003")
        self.assertEqual(sorted(p.name for p in hm.glob("*.png")),
                         ["diff_f0001.png", "diff_f0002.png"])  # Tier-1 不产
        self.assertIn("帧错误", buf.getvalue())

    def test_dir_mode_all_pass_rc0(self):
        t2_ref, t2_cand = _pair_tier2()
        rd, cd = self._dirs({0: (t2_ref, t2_ref.copy()),
                             1: (t2_ref, t2_cand)})
        with contextlib.redirect_stdout(io.StringIO()):
            report = self.root / "quality-report.json"
            rc = gg.main(["analyze", "--ref", str(rd), "--cand", str(cd), "--report", str(report)])
        self.assertEqual(json.loads(report.read_text())["quality"]["display"], "unknown")
        self.assertEqual(rc, 0)                       # Tier-1/Tier-2 均为通过

    def test_file_mode_tier2_pass_with_heatmap(self):
        t2_ref, t2_cand = _pair_tier2()
        rp, cp = self.root / "ref.png", self.root / "cand.png"
        _save_png(t2_ref, rp)
        _save_png(t2_cand, cp)
        hm = self.root / "heat"
        with contextlib.redirect_stdout(io.StringIO()):
            rc = gg.main(["analyze", "--ref", str(rp), "--cand", str(cp),
                          "--heatmap-dir", str(hm)])
        self.assertEqual(rc, 0)
        self.assertTrue((hm / "diff_f0000.png").is_file())   # 非 f 帧名 → 记 f0000

    def test_file_mode_size_mismatch_rc1(self):
        rp, cp = self.root / "r.png", self.root / "c.png"
        _save_png(np.zeros((8, 8, 4), np.uint8), rp)
        _save_png(np.zeros((9, 8, 4), np.uint8), cp)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = gg.main(["analyze", "--ref", str(rp), "--cand", str(cp)])
        self.assertEqual(rc, 1)
        self.assertIn("尺寸不一致", buf.getvalue())

    def test_mixed_file_and_dir_rejected(self):
        rd, _ = self._dirs({0: (np.zeros((8, 8, 4), np.uint8),) * 2})
        f = self.root / "single.png"
        _save_png(np.zeros((8, 8, 4), np.uint8), f)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gg.main(["analyze", "--ref", str(rd), "--cand", str(f)]), 1)

    def test_missing_path_rejected(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                gg.main(["analyze", "--ref", str(self.root / "none"),
                         "--cand", str(self.root / "none2")]), 1)

    def test_empty_dirs_rejected(self):
        rd, cd = self._dirs({})
        with contextlib.redirect_stdout(io.StringIO()):
            rc = gg.main(["analyze", "--ref", str(rd), "--cand", str(cd)])
        self.assertEqual(rc, 1)                       # 无帧对≠通过（宁缺毋滥）


class AnalyzeCorridorEquivalence(unittest.TestCase):
    """#104 返工：全画幅快速走廊 ≡ 一般路径（同帧对强制两路，全字段一致）。

    heatmap_out 非 None 时走廊旁路、必走一般路径（xor+take 收集 + 精确重投影），
    借此对同帧对分别取两路结果，断言除 heatmap/elapsed 外逐字段相等——锁死
    走廊三条件化简（n_semi==n_diff / max_premul_residual 保守上界 1 / 计数
    口径）不改变裁决语义。确定性形态 + 种子随机形态双覆盖。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    _EQ_FIELDS = ("tier", "labels", "w", "h", "max_abs_diff", "max_abs_diff_rgb",
                  "max_abs_diff_alpha", "n_diff_pixels", "semi_diff_pixels",
                  "opaque_diff_pixels", "transparent_diff_pixels",
                  "premul_rounding_pixel_count", "max_premul_residual",
                  "halo_signature_pixels")

    def _assert_eq(self, ref, cand):
        fast = gg._analyze_arrays(ref, cand)        # 走廊可命中
        slow = gg._analyze_arrays(ref, cand, heatmap_out=self.root / "hm.png")
        for f in self._EQ_FIELDS:
            self.assertEqual(fast[f], slow[f], f"{f}: 走廊≠一般路径")

    def test_deterministic_morphologies(self):
        w, h = 24, 16
        rng = np.random.default_rng(7)
        base = rng.integers(0, 256, (h, w, 4), dtype=np.uint8)
        cases = [_pair_tier2(w, h), _pair_halo(w, h), _pair_opaque_shift(w, h)]
        # 全屏中 α=128·ΔRGB=2（走廊命中的红队核心场景）
        r5 = rng.integers(0, 254, (h, w, 4), dtype=np.uint8)
        r5[..., 3] = 128
        c5 = r5.copy()
        c5[..., 0] = r5[..., 0] + 2
        cases.append((r5, c5))
        # α·ΔC=258 反例（走廊必须拒绝 → 两路同 Tier-3）
        r6 = np.zeros((h, w, 4), np.uint8)
        r6[..., 3] = 2
        r6[..., 1] = 63
        c6 = r6.copy()
        c6[..., 1] = 192
        cases.append((r6, c6))
        cases.append((base, base.copy()))           # Tier-1（两路同早退）
        for ref, cand in cases:
            self._assert_eq(ref, cand)

    def test_seeded_random_pairs(self):
        rng = np.random.default_rng(104)
        for _ in range(30):
            h, w = int(rng.integers(8, 40)), int(rng.integers(8, 48))
            ref = rng.integers(0, 256, (h, w, 4), dtype=np.uint8)
            cand = ref.copy()
            if rng.random() < 0.7:                  # 大概率构造半透明差异
                k = max(1, h * w // 8)
                ys = rng.integers(0, h, k)
                xs = rng.integers(0, w, k)
                a = int(rng.integers(1, 255))
                ref[ys, xs, 3] = a
                cand[ys, xs, 3] = a
                cand[ys, xs, :3] = np.clip(ref[ys, xs, :3].astype(int)
                                           + int(rng.integers(-3, 4)), 0, 255)
            if rng.random() < 0.3:                  # 混 α 差异 / 不透明差异
                cand[..., 3] = np.clip(cand[..., 3].astype(int)
                                       + int(rng.integers(-2, 3)), 0, 255)
            self._assert_eq(ref, cand)


class AnalyzePerf(unittest.TestCase):
    """#104 返工性能门：全部 Tier-1/Tier-2 通过态路径 1080p 分析段中位 ≤15ms。

    五路径：全等 / 稀疏 2000 半透明 / 稠密 Tier-2 30% / 稠密 Tier-2 100% /
    全屏中 α=128·ΔRGB=2（工单核心场景的中 α 推广——红队复现的 167ms 无护栏
    慢路径）。阈值按 15ms 预算断言，机器抖动由 2 次预热 + 7 轮取中位消化；
    PNG 解码 1080p 实测 ~35-55ms 属 I/O，不在本门内（decode_ms 字段另行披露）。

    环境校准（慢 runner 修复）：15ms 是本机绝对值口径，GitHub 共享 runner 慢
    数倍曾以 15.28ms 误红。故测试开头跑一段固定纯 numpy 参考工作量（走廊同款
    原语混合，但与 gfx_golden 零代码共用），预算按 max(1.0, ref/_LOCAL_REF_MS)
    等比缩放：本机 ref≈_LOCAL_REF_MS ⟹ 系数 1.0、语义不变；慢机按实测机器
    速度等比放宽。护栏硬约束（变异验证锁定）：参考工作量必须独立于被测代码——
    若用 _analyze_arrays 自校准，走廊被移除（返工前 ~150-167ms 慢路径）会同步
    抬高预算、回归自掩蔽；纯 numpy 工作量不受实现回归影响，走廊禁用时本机
    系数仍 ≈1.0、预算仍 15ms，慢路径两次必超 → 本测试必须 FAIL（已在
    /tmp 副本把 _dense_pass_corridor 改为永不触发验证击杀）。
    """

    # 本机校准常数：AMD Ryzen 9 7950X（32 线程、空载），Python 3.13.11 +
    # numpy 2.5.1，9 组「2 预热+5 轮取中位」实测 4.93-5.19ms，取 5.1。
    _LOCAL_REF_MS = 5.1
    _BASE_BUDGET_MS = 15.0

    @classmethod
    def _ref_workload_ms(cls):
        """固定参考工作量中位耗时（ms）：纯 numpy，禁 import/调用 gfx_golden。

        与走廊同量级同原语混合（(n,4) uint8 列 strided subtract+abs+maximum、
        uint8 回绕差、bool 翻转、int16/uint16 乘、max/count_nonzero 归约，
        n=1080p 像素数），保证参考/被测的机器速度比跨机可迁移；数据 arange
        确定性构造（无 RNG），输出缓冲预分配（稳态零分配）。
        """
        n = 1920 * 1080
        pix = np.arange(n, dtype=np.int64) % 251
        f8 = np.empty((n, 4), np.uint8)
        f8[:, 0] = pix
        f8[:, 1] = pix + 250
        f8[:, 2] = (pix * 3) % 256
        f8[:, 3] = (pix % 254) + 1          # α ∈ [1,254]
        g8 = np.empty((n, 4), np.uint8)
        g8[:, 0] = pix + 1
        g8[:, 1] = pix
        g8[:, 2] = (pix * 3 + 2) % 256
        g8[:, 3] = f8[:, 3]                 # α 全等
        t16 = np.empty(n, np.int16)
        acc = np.empty(n, np.int16)
        prod = np.empty(n, np.uint16)
        u8 = np.empty(n, np.uint8)

        def one_round():
            t0 = time.perf_counter()
            np.subtract(f8[:, 3], g8[:, 3], out=u8)
            u8.any()
            for k in (0, 1, 2):
                np.subtract(f8[:, k], g8[:, k], dtype=np.int16, out=t16)
                np.abs(t16, out=t16)
                if k:
                    np.maximum(acc, t16, out=acc)
                else:
                    acc[...] = t16
            semi = (u8 - np.uint8(1)) < np.uint8(254)
            su8 = semi.view(np.uint8)
            np.bitwise_xor(su8, np.uint8(1), out=su8)
            np.multiply(acc, su8, dtype=np.int16, out=t16)
            int(t16.max())
            np.multiply(acc.view(np.uint16), f8[:, 3], dtype=np.uint16, out=prod)
            int(prod.max())
            int(np.count_nonzero(acc))
            return (time.perf_counter() - t0) * 1000

        one_round(); one_round()            # 预热
        ts = [one_round() for _ in range(5)]
        return sorted(ts)[2]

    @classmethod
    def _calibrated_budget_ms(cls):
        """→ (预算 ms, 参考 ms, 缩放系数)：快机系数钉 1.0（不收紧），慢机等比放宽。"""
        ref = cls._ref_workload_ms()
        scale = max(1.0, ref / cls._LOCAL_REF_MS)
        return cls._BASE_BUDGET_MS * scale, ref, scale

    def test_1080p_all_pass_state_paths_within_budget(self):
        budget, ref_ms, scale = self._calibrated_budget_ms()
        w, h = 1920, 1080
        rng = np.random.default_rng(104)
        base = rng.integers(0, 256, (h, w, 4), dtype=np.uint8)

        def median_ms(a, b, n=7):
            for _ in range(2):                        # 预热（惰性 import+scratch 首配）
                gg._analyze_arrays(a, b)
            ts, r = [], None
            for _ in range(n):
                t0 = time.perf_counter()
                r = gg._analyze_arrays(a, b)
                ts.append((time.perf_counter() - t0) * 1000)
            return sorted(ts)[n // 2], r

        cases = []                                    # (名, a, b, 中位 ms)

        m, r = median_ms(base, base.copy())           # 1) 全等 Tier-1
        self.assertEqual(r["tier"], gg.TIER_EXACT)
        cases.append(("identical", base, base.copy(), m))

        cand = base.copy()                            # 2) 稀疏 2000 半透明
        ref2 = base.copy()
        pos = rng.choice(h * w, size=2000, replace=False)
        ys, xs = np.divmod(pos, w)
        ref2[ys, xs, 3] = 128
        cand[ys, xs, 3] = 128
        cand[ys, xs, 0] = np.clip(cand[ys, xs, 0].astype(np.int32) + 1, 0, 255)
        m, r = median_ms(ref2, cand)
        self.assertEqual(r["tier"], gg.TIER_PREMUL)
        self.assertGreater(r["n_diff_pixels"], 0)
        self.assertEqual(r["premul_rounding_pixel_count"], r["n_diff_pixels"])
        cases.append(("sparse 2000 semi", ref2, cand, m))

        A = np.broadcast_to(1 + (np.arange(w) % 15), (h, w))   # 3) 稠密 Tier-2 ~30%
        P = np.arange(h)[:, None] % (A + 1)
        s_up = np.minimum(255, _rhu(P * 255, A))
        s_dn = np.minimum(255, (P * 255) // A)
        ref3 = np.dstack([s_up, s_up, s_up, A]).astype(np.uint8)
        cand3 = np.dstack([s_dn, s_dn, s_dn, A]).astype(np.uint8)
        self.assertGreater((ref3 != cand3).any(axis=2).mean(), 0.25)
        m, r = median_ms(ref3, cand3)
        self.assertEqual(r["tier"], gg.TIER_PREMUL)
        cases.append(("dense T2 30%", ref3, cand3, m))

        A4 = 2 * (1 + np.arange(h * w).reshape(h, w) % 3)      # 4) 稠密 Tier-2 100%
        P4 = A4 // 2                    # P=A/2 → P·255/A=127.5 恰在舍入半界
        su4 = np.minimum(255, _rhu(P4 * 255, A4))
        sd4 = np.minimum(255, (P4 * 255) // A4)
        ref4 = np.dstack([su4, su4, su4, A4]).astype(np.uint8)
        cand4 = np.dstack([sd4, sd4, sd4, A4]).astype(np.uint8)
        self.assertGreater((ref4 != cand4).any(axis=2).mean(), 0.99)  # 真 ~100%
        m, r = median_ms(ref4, cand4)
        self.assertEqual(r["tier"], gg.TIER_PREMUL)
        cases.append(("dense T2 100%", ref4, cand4, m))

        ref5 = rng.integers(0, 254, (h, w, 4), dtype=np.uint8)  # 5) 全屏中 α
        ref5[..., 3] = 128                            # α=128 全屏
        cand5 = ref5.copy()
        cand5[..., 0] = ref5[..., 0].astype(np.int32) + 2   # 直存 ΔRGB=2（积 256）
        m, r = median_ms(ref5, cand5)
        self.assertEqual(r["tier"], gg.TIER_PREMUL)
        self.assertEqual(r["max_abs_diff"], 2)
        self.assertEqual(r["max_premul_residual"], 1)
        self.assertEqual(r["premul_rounding_pixel_count"], r["n_diff_pixels"])
        self.assertEqual(r["n_diff_pixels"], h * w)
        cases.append(("full mid-alpha A=128 dRGB=2", ref5, cand5, m))

        for name, a, b, ms in cases:
            if ms > budget:             # 机器级瞬态重测一次（两次均超才判失败；
                ms, _ = median_ms(a, b)  # 真回归（如走廊禁用 ~150ms）远超任何
            self.assertLessEqual(        # 校准预算，两次必超，牙齿不变）
                ms, budget,
                f"{name} 分析段中位 {ms:.2f}ms > 预算 {budget:.1f}ms"
                f"（校准: ref={ref_ms:.2f}ms, _LOCAL_REF_MS="
                f"{self._LOCAL_REF_MS}, scale={scale:.2f}）")

        with tempfile.TemporaryDirectory(prefix="gfx_analyze_perf_") as td:
            # 端到端含 PNG 编解码：只做 sanity 断言（解码耗时另见 decode_ms 字段）
            rp, cp = Path(td) / "ref.png", Path(td) / "cand.png"
            _save_png(ref5, rp)
            _save_png(cand5, cp)
            r2 = gg.analyze_frame_diff(rp, cp)
            self.assertEqual(r2["tier"], gg.TIER_PREMUL)
            self.assertLess(r2["decode_ms"], 1000.0)


class CompareCountRegression(unittest.TestCase):
    def setUp(self):
        from tests.gfx._environment import bind_environment
        bind_environment(self)
        browser = mock.patch.object(gg, "chrome_version", return_value="unit fixture; not real browser")
        browser.start()
        self.addCleanup(browser.stop)

    def test_difference_and_missing_count_only_successful_comparisons(self):
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            root = Path(td)
            clips = [("a.html", 4)]
            tp = _project(root, clips)
            _fill_cache(tp, clips)
            golden, report = root / "golden.json", root / "report.json"
            self.assertEqual(gg.main(["build", "-t", str(tp), "--golden", str(golden),
                                      "--full-pages", "a.html"]), 0)
            d = gg.hygiene.gfx_dir(gg.make_ctx(tp), {"src": "a.html", "dur": 4})
            _make_frame(d / "f0001.png", 1, tamper=True)
            argv = ["compare", "-t", str(tp), "--golden", str(golden), "--report", str(report)]
            self.assertEqual(gg.main(argv), 1)
            self.assertEqual(json.loads(report.read_text())["compared_frames"], 4)
            (d / "f0002.png").unlink()
            self.assertEqual(gg.main(argv), 1)
            self.assertEqual(json.loads(report.read_text())["compared_frames"], 3)
            self.assertEqual(list(root.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
