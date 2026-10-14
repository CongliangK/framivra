"""gfx_golden.py 单测：PIL 现场生成小 PNG（64×48）构造假缓存目录 + 假 timeline
（无 chrome 依赖），覆盖 build 抽样/全帧、帧缺失报错、compare 全零/差异/帧缺失、
同页双 dur 并列、名单逐项零匹配报错（B2）、缓存未重渲新鲜度防线（B1 默认警告/
--strict 失败）、页 html 改键后 compare 显式失败（目录缺失，绝不静默错比）。
golden 键与帧目录定位复用 vtx/hygiene.gfx_key（与渲染器同键）。

#104 起扩展：analyze_frame_diff 三级像素裁决（Tier-1 Exact / Tier-2
Premul-Equivalent / Tier-3 Failure+labels）——DoD 四类合成帧分类准确率 100%、
残差 ≤1 容差边界、黑边/α 差异/全透明区差异反例、热力图（Tier-2/3 产 Tier-1
不产、绿/红通道分工、原子写）、analyze CLI（目录/单文件/缺帧配对/退出码）与
1080p 分析段性能门（中位 ≤15ms，解码外）。"""
import contextlib
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import gfx_golden as gg  # noqa: E402
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
            rc = gg.main(["analyze", "--ref", str(rd), "--cand", str(cd)])
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


class AnalyzePerf(unittest.TestCase):
    """#104 性能门：1080p 分析段（解码外、纯裁决数学）中位 ≤15ms。

    两条路径各测：Tier-1 快路径（xor+any 全帧扫描）与稀疏差异全分类路径
    （预乘重投影数学）；heatmap 关闭（预算口径=纯比对，热力图是可选附加产物）。
    """

    def test_1080p_analysis_budget(self):
        w, h = 1920, 1080
        rng = np.random.default_rng(104)
        ref = rng.integers(0, 256, (h, w, 4), dtype=np.uint8)

        def median_ms(a, b, n=7):
            for _ in range(2):                        # 预热（首次调用有惰性 import）
                gg._analyze_arrays(a, b)
            ts = []
            for _ in range(n):
                t0 = time.perf_counter()
                r = gg._analyze_arrays(a, b)
                ts.append((time.perf_counter() - t0) * 1000)
            return sorted(ts)[n // 2], r

        med_exact, r = median_ms(ref, ref.copy())
        self.assertEqual(r["tier"], gg.TIER_EXACT)
        self.assertLessEqual(med_exact, 15.0)

        cand = ref.copy()                             # 稀疏差异：~2000 半透明像素
        pos = rng.choice(h * w, size=2000, replace=False)
        ys, xs = np.divmod(pos, w)
        ref2 = ref.copy()
        ref2[ys, xs, 3] = 128
        cand[ys, xs, 3] = 128
        cand[ys, xs, 0] = np.clip(cand[ys, xs, 0].astype(np.int32) + 1, 0, 255)
        med_diff, r = median_ms(ref2, cand)
        self.assertEqual(r["tier"], gg.TIER_PREMUL)   # A=128 下 +1 直存差 → 残差 ≤1
        self.assertGreater(r["n_diff_pixels"], 0)
        self.assertLessEqual(med_diff, 15.0)

        with tempfile.TemporaryDirectory(prefix="gfx_analyze_perf_") as td:
            # 端到端含 PNG 编解码：只做 sanity 断言（解码耗时另见 decode_ms 字段）
            rp, cp = Path(td) / "ref.png", Path(td) / "cand.png"
            _save_png(ref2, rp)
            _save_png(cand, cp)
            r2 = gg.analyze_frame_diff(rp, cp)
            self.assertEqual(r2["tier"], gg.TIER_PREMUL)
            self.assertLess(r2["decode_ms"], 1000.0)


if __name__ == "__main__":
    unittest.main()
