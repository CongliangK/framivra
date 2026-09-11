"""gfx_golden.py 单测：PIL 现场生成小 PNG（64×48）构造假缓存目录 + 假 timeline
（无 chrome 依赖），覆盖 build 抽样/全帧、帧缺失报错、compare 全零/差异/帧缺失、
同页双 dur 并列、名单逐项零匹配报错（B2）、缓存未重渲新鲜度防线（B1 默认警告/
--strict 失败）、页 html 改键后 compare 显式失败（目录缺失，绝不静默错比）。
golden 键与帧目录定位复用 vtx/hygiene.gfx_key（与渲染器同键）。"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
