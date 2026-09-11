"""Run: python -m unittest discover -s tests -v

Set VTX_TEST_CHROME and/or VTX_TEST_FFMPEG to enable real integration checks.
All fixtures are synthetic. No private footage or machine paths are required.
"""
import base64
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.environ.get('VTX_TEST_MODULE_DIR', str(Path(__file__).resolve().parents[1] / 'vtx')))
import gfx_render as gfx
import infra
import vtx


class ExportValidation(unittest.TestCase):
    def test_valid_and_invalid_png(self):
        import numpy as np
        png = gfx.png_encode_rgba(np.zeros((2, 3, 4), dtype=np.uint8))
        def url(data):
            return 'data:image/png;base64,' + base64.b64encode(data).decode()
        self.assertEqual(gfx.decode_canvas_export(url(png), 3, 2), png)
        for bad in [None, 'data:,', 'data:image/png;base64,!!!', url(png[:24]),
                    url(png[:-1]), url(png[:40] + bytes([png[40] ^ 1]) + png[41:])]:
            with self.subTest(bad=str(bad)[:40]):
                with self.assertRaises(gfx.GfxRenderError):
                    gfx.decode_canvas_export(bad, 3, 2)
        with self.assertRaises(gfx.GfxRenderError):
            gfx.decode_canvas_export(url(png), 2, 3)


class _FakeTab:
    """按 mode 伪造 CDP tab，驱动 render_page 走不同导出分支（不依赖 Chrome）：
    override  — 截图带真 alpha（flatten 探针判 not flat → 走 override 快路径）
    twoshot   — 截图全不透明但白黑注入有差异（flatten 守卫回落白黑两截）
    canvas    — window.__vtx_export_png() 返回合法画布 dataURL
    canvas_bad— typeof 为 function 但载荷非法 → 回落两截并计 canvas_fallback
    fail      — canvas 页形态 + 截图必抛错（canvas 页 setup 零截图 → 首截即
                worker 帧内失败）→ 全帧重试耗尽计 failed
    """
    def __init__(self, browser, mode):
        self.browser, self.mode, self.bg = browser, mode, 'white'

    def call(self, method, timeout=30, **params):
        return {}

    def navigate(self, url, timeout=5.0):
        pass

    def eval(self, expression):
        if '__vtx_export_png()' in expression:
            if self.mode == 'canvas':
                import numpy as np
                arr = np.zeros((4, 6, 4), dtype=np.uint8)
                arr[:, :, 3] = 128
                return 'data:image/png;base64,' + base64.b64encode(gfx.png_encode_rgba(arr)).decode()
            return 'invalid'  # canvas_bad / fail：载荷非法触发回落
        if '__vtx_export_png' in expression:
            return self.mode in ('canvas', 'canvas_bad', 'fail')  # typeof 探测
        if '#fff' in expression:
            self.bg = 'white'
        elif '#000' in expression:
            self.bg = 'black'
        return 'ok'

    def shot(self):
        import numpy as np
        self.browser.shots += 1
        if self.mode == 'fail':
            raise RuntimeError('fake shot failure')
        v = 200 if self.bg == 'white' else 10
        arr = np.full((4, 6, 4), v, dtype=np.uint8)
        arr[:, :, 3] = 255
        if self.mode == 'override':
            arr[0, 0, 3] = 128  # 有真 alpha → flatten 探针立刻判 not flat → 走 override
        return gfx.png_encode_rgba(arr)


class _FakeBrowser:
    def __init__(self, mode, tabs_n=1):
        self.lock = threading.Lock()
        self.generation = 0
        self.mode, self.tabs_n, self.shots = mode, tabs_n, 0

    def start(self):
        pass

    def stop(self):
        pass

    def open_tabs(self):
        return [_FakeTab(self, self.mode) for _ in range(self.tabs_n)]

    def hard_restart(self):
        self.generation += 1


class GfxStatsMock(unittest.TestCase):
    """mock 级（不依赖 Chrome）：path_counts/timings_ms 字段存在且与实际分支一致。"""

    def _run(self, mode, n=3):
        with tempfile.TemporaryDirectory(prefix='vtx_stats_') as tmp:
            root = Path(tmp)
            page = root / 'p.html'
            page.write_text('<html></html>', encoding='utf-8')
            browser = _FakeBrowser(mode, 1)
            with mock.patch.object(gfx, 'Browser', lambda *a, **k: browser):
                st = gfx.render_page(page, root / 'out', n, chrome_path='fake',
                                     tabs=1, width=6, height=4)
            return st

    def test_stats_fields_backward_compatible(self):
        st = self._run('override')
        for key in ('n', 'rendered', 'failed', 'transparent', 'tabs', 'ms_total',
                    'ms_per_frame', 'canvas_export', 'path_counts', 'timings_ms'):
            self.assertIn(key, st)
        self.assertEqual((st['n'], st['rendered'], st['failed']), (3, 3, []))
        self.assertIsInstance(st['canvas_export'], bool)
        self.assertEqual(set(st['timings_ms']), {'nav', 'export', 'decode', 'write'})
        for s in st['timings_ms'].values():
            self.assertEqual(set(s), {'p50', 'p95', 'sum'})

    def test_path_counts_match_actual_branch(self):
        for mode, expect in [('override', 'override'), ('twoshot', 'twoshot'),
                             ('canvas', 'canvas')]:
            with self.subTest(mode=mode):
                st = self._run(mode)
                pc = st['path_counts']
                self.assertEqual(pc[expect], 3)
                self.assertEqual(pc['failed'], 0)
                self.assertEqual(pc['canvas_fallback'], 0)
                self.assertEqual(sum(v for k, v in pc.items() if k != 'canvas_fallback'), 3)
                for s in st['timings_ms'].values():
                    self.assertGreaterEqual(s['sum'], 0.0)
                    self.assertGreaterEqual(s['p95'], s['p50'])

    def test_canvas_fallback_counted_as_twoshot(self):
        st = self._run('canvas_bad', n=2)
        pc = st['path_counts']
        self.assertEqual((pc['canvas_fallback'], pc['twoshot'], pc['canvas']), (2, 2, 0))
        self.assertEqual(st['canvas_export'], True)

    def test_failed_frames_counted_not_timed(self):
        st = self._run('fail', n=2)
        self.assertEqual((st['rendered'], st['path_counts']['failed']), (0, 2))
        # 失败帧不进耗时样本（避免重试噪声）→ 各分项无样本全 0
        self.assertEqual(st['timings_ms']['nav']['sum'], 0.0)
        self.assertEqual(st['timings_ms']['export']['p50'], 0.0)

    def test_all_cached_zero_stats(self):
        with tempfile.TemporaryDirectory(prefix='vtx_stats_') as tmp:
            root = Path(tmp)
            page = root / 'p.html'
            page.write_text('<html></html>', encoding='utf-8')
            out = root / 'out'
            out.mkdir()
            for i in range(2):
                (out / f'f{i:04d}.png').write_bytes(b'x')  # 帧全在 → work 空、早退
            with mock.patch.object(gfx, 'Browser', lambda *a, **k: _FakeBrowser('override', 1)):
                st = gfx.render_page(page, out, 2, chrome_path='fake', tabs=1)
        self.assertEqual(st['path_counts'],
                         dict.fromkeys(('override', 'canvas', 'twoshot', 'opaque',
                                        'canvas_fallback', 'failed'), 0))
        self.assertEqual(st['timings_ms']['nav'], {'p50': 0.0, 'p95': 0.0, 'sum': 0.0})

    def test_timings_summary_math(self):
        s = gfx._timings_summary([0.1, 0.2, 0.3, 0.4])
        self.assertAlmostEqual(s['sum'], 1000.0)
        self.assertAlmostEqual(s['p50'], 250.0)
        self.assertAlmostEqual(s['p95'], 385.0)
        self.assertEqual(gfx._timings_summary([]), {'p50': 0.0, 'p95': 0.0, 'sum': 0.0})


class FramesParamMock(unittest.TestCase):
    """方案 v2 阶段 1 页内分片：render_page frames 参数只做缺失交集；全缓存早退
    不构造 Browser（零 Chrome）。"""

    def _prep(self, tmp, present=()):
        root = Path(tmp)
        page = root / 'p.html'
        page.write_text('<html></html>', encoding='utf-8')
        out = root / 'out'
        out.mkdir()
        for i in present:
            (out / f'f{i:04d}.png').write_bytes(b'x')
        return page, out

    def test_frames_intersects_missing(self):
        # frames=[1..5]、其中 1/3 已缓存 → 只渲缺失的 2/4/5，frames 外的 0 不碰
        with tempfile.TemporaryDirectory(prefix='vtx_split_') as tmp:
            page, out = self._prep(tmp, present=(1, 3))
            browser = _FakeBrowser('override', 1)
            with mock.patch.object(gfx, 'Browser', lambda *a, **k: browser):
                st = gfx.render_page(page, out, 6, chrome_path='fake', tabs=1,
                                     frames=[1, 2, 3, 4, 5])
            self.assertEqual(st['n'], 6)                       # n 仍=页总帧数
            self.assertEqual(st['rendered'], 3)
            self.assertEqual(st['path_counts']['override'], 3)
            self.assertEqual(st['failed'], [])
            self.assertEqual((out / 'f0001.png').read_bytes(), b'x')  # 已存在未被重写
            for i in (2, 4, 5):
                self.assertTrue((out / f'f{i:04d}.png').exists(), i)
            self.assertFalse((out / 'f0000.png').exists())     # frames 之外不渲

    def test_frames_all_cached_early_return_zero_chrome(self):
        # 块内全部帧已缓存 → work 空 → 不构造/启动 Browser 即早退
        with tempfile.TemporaryDirectory(prefix='vtx_split_') as tmp:
            page, out = self._prep(tmp, present=(0, 1))

            def _no_chrome(*a, **k):
                raise AssertionError('全缓存块不得构造 Browser（白起 Chrome）')

            with mock.patch.object(gfx, 'Browser', _no_chrome):
                st = gfx.render_page(page, out, 2, chrome_path='fake', tabs=1,
                                     frames=[0, 1])
            self.assertEqual((st['rendered'], st['failed']), (0, []))
            self.assertEqual(st['ms_total'], 0.0)
            self.assertEqual(st['path_counts']['override'], 0)

    def test_frames_empty_list_is_noop(self):
        with tempfile.TemporaryDirectory(prefix='vtx_split_') as tmp:
            page, out = self._prep(tmp)
            with mock.patch.object(gfx, 'Browser',
                                   lambda *a, **k: (_ for _ in ()).throw(AssertionError('no chrome'))):
                st = gfx.render_page(page, out, 3, chrome_path='fake', tabs=1,
                                     frames=[])
            self.assertEqual(st['rendered'], 0)
            self.assertFalse(list(out.glob('f*.png')))

    def test_frames_none_keeps_whole_page_semantics(self):
        # frames=None（默认）行为不变：扫全页缺失帧
        with tempfile.TemporaryDirectory(prefix='vtx_split_') as tmp:
            page, out = self._prep(tmp, present=(1,))
            browser = _FakeBrowser('override', 1)
            with mock.patch.object(gfx, 'Browser', lambda *a, **k: browser):
                st = gfx.render_page(page, out, 3, chrome_path='fake', tabs=1)
            self.assertEqual(st['rendered'], 2)
            self.assertTrue((out / 'f0000.png').exists())
            self.assertTrue((out / 'f0002.png').exists())


class VtxSplitMath(unittest.TestCase):
    """vtx 页内分片：切块数学（阈值/上限/关闭态）与多块 stats 聚合。"""

    def test_split_page_disabled_and_small(self):
        self.assertIsNone(vtx._split_page(1153, 0, 3))    # SPLIT_FRAMES=0 关闭态
        self.assertIsNone(vtx._split_page(600, 600, 3))   # 不大于阈值不拆
        self.assertIsNone(vtx._split_page(100, 600, 3))
        self.assertIsNone(vtx._split_page(601, 600, 0))   # 阈值≤0 或上限<1 一律关

    def test_split_page_ceil_and_contiguous(self):
        self.assertEqual(vtx._split_page(601, 600, 3), [301, 300])    # ceil=2
        self.assertEqual(vtx._split_page(1153, 600, 3), [577, 576])   # 基准最大页
        self.assertEqual(vtx._split_page(1200, 600, 3), [600, 600])   # 整除

    def test_split_page_max_cap(self):
        self.assertEqual(vtx._split_page(3000, 600, 3), [1000, 1000, 1000])  # ceil=5→截3
        self.assertEqual(vtx._split_page(5000, 600, 4), [1250] * 4)          # ceil=9→截4
        self.assertEqual(vtx._split_page(12000, 600, 2), [6000, 6000])

    def test_split_page_invariants(self):
        for n in (601, 1153, 2000, 3000, 4321, 12024):
            for sf in (600, 1000):
                sizes = vtx._split_page(n, sf, 3)
                if sizes is None:
                    self.assertLessEqual(n, sf)
                    continue
                self.assertEqual(sum(sizes), n)
                self.assertLessEqual(len(sizes), 3)
                self.assertTrue(all(s > 0 for s in sizes))
                self.assertTrue(all(b - a <= 1 for a, b in zip(sizes, sizes[1:])))

    def test_merge_cdp_stats_sums_and_weights(self):
        def block(rendered, ms_total, p50):
            return {"rendered": rendered, "failed": [], "tabs": 4,
                    "ms_total": ms_total,
                    "path_counts": dict.fromkeys(
                        ("override", "canvas", "twoshot", "opaque",
                         "canvas_fallback", "failed"), 0)
                    | {"override": rendered},
                    "timings_ms": {"nav": {"p50": p50, "p95": 0.0, "sum": 0.0},
                                   "export": {"p50": p50, "p95": 0.0, "sum": 0.0},
                                   "decode": {"p50": 0.0, "p95": 0.0, "sum": 0.0},
                                   "write": {"p50": 0.0, "p95": 0.0, "sum": 0.0}}}
        m = vtx._merge_cdp_stats([block(100, 20000.0, 10.0), block(300, 30000.0, 30.0)])
        self.assertAlmostEqual(m['ms_per_frame'], 125.0)      # Σms/Σ帧 = 50000/400
        self.assertEqual(m['path_counts']['override'], 400)   # 路径计数求和
        self.assertAlmostEqual(m['timings_ms']['nav']['p50'], 25.0)  # 帧数加权
        via = vtx._cdp_via(m)                                 # 可直接进 _cdp_via 格式化
        self.assertIn('override×400', via)
        self.assertIn('125ms/帧', via)
        self.assertIsNone(vtx._merge_cdp_stats([]))


@unittest.skipUnless(os.environ.get('VTX_TEST_CHROME'), 'set VTX_TEST_CHROME')
class BrowserIntegration(unittest.TestCase):
    def test_canvas_html_and_invalid_export(self):
        import numpy as np
        with tempfile.TemporaryDirectory(prefix="vtx 中文 # space '") as tmp:
            root = Path(tmp)
            html = '''<html style="background:transparent"><body style="margin:0;background:transparent">
<canvas width="1920" height="1080" id="c"></canvas><script>
const c=document.getElementById('c'),x=c.getContext('2d');
const f=Number(new URLSearchParams(location.search).get('f'));
x.fillStyle=f?'rgba(0,255,0,0.5)':'rgba(255,0,0,0.5)';x.fillRect(100,100,200,200);
EXPORT
</script></body></html>'''
            frames = {}
            for kind, export in [('canvas', "window.__vtx_export_png=()=>c.toDataURL('image/png');"),
                                 ('html',''), ('invalid', "window.__vtx_export_png=()=> 'invalid';")]:
                page=root/(kind+'.html');page.write_text(html.replace('EXPORT',export),encoding='utf-8')
                out=root/kind
                result=gfx.render_page(page,out,2,chrome_path=os.environ['VTX_TEST_CHROME'],tabs=1)
                self.assertEqual(result['failed'], [])
                arr=[gfx.png_decode_rgba((out/f'f{i:04d}.png').read_bytes()) for i in range(2)]
                self.assertTrue(arr[0][150,150,0]>240)
                self.assertTrue(arr[1][150,150,1]>240)
                self.assertTrue(125<=arr[0][150,150,3]<=130)
                self.assertEqual(int(arr[0][500,500,3]),0)
                frames[kind]=arr
            for kind in ['html','invalid']:
                for i in range(2):
                    for background in [0,255]:
                        def composite(a):
                            a=a.astype(float);alpha=a[:,:,3:4]/255
                            return a[:,:,:3]*alpha+background*(1-alpha)
                        self.assertLess(np.abs(composite(frames[kind][i])-composite(frames['canvas'][i])).mean(),.1)

    def test_path_counts_and_timings(self):
        # 观测字段真实链路：透明页（override 或两截）与 canvas 载荷失效回落两截
        with tempfile.TemporaryDirectory(prefix="vtx_stats_real_") as tmp:
            root = Path(tmp)
            base = ('<html style="background:transparent"><body style="margin:0;'
                    'background:transparent">EXPORT</body></html>')
            pages = {'shot': base.replace('EXPORT', ''),
                     'invalid': base.replace(
                         'EXPORT', "<script>window.__vtx_export_png=()=>'invalid';</script>")}
            for kind, text in pages.items():
                page = root / (kind + '.html')
                page.write_text(text, encoding='utf-8')
                st = gfx.render_page(page, root / kind, 2,
                                     chrome_path=os.environ['VTX_TEST_CHROME'], tabs=1)
                self.assertEqual(st['failed'], [])
                pc = st['path_counts']
                if kind == 'invalid':
                    self.assertEqual((pc['canvas_fallback'], pc['twoshot']), (2, 2))
                else:
                    self.assertEqual(pc['override'] + pc['twoshot'] + pc['opaque'], 2)
                for s in st['timings_ms'].values():
                    self.assertGreaterEqual(s['p95'], s['p50'])
                    self.assertGreaterEqual(s['sum'], 0.0)


@unittest.skipUnless(os.environ.get('VTX_TEST_FFMPEG'), 'set VTX_TEST_FFMPEG')
class SubtitleIntegration(unittest.TestCase):
    def test_special_character_filename(self):
        with tempfile.TemporaryDirectory(prefix="vtx 中文 #,[]; '") as tmp:
            p=Path(tmp)/"sub 'x,[a];.srt"
            p.write_text('1\n00:00:00,000 --> 00:00:01,000\nTEST\n',encoding='utf-8')
            cmd=[os.environ['VTX_TEST_FFMPEG'],'-v','error','-f','lavfi','-i','color=black:s=320x180:d=1',
                 '-vf','subtitles=filename='+infra.filter_filename(p),'-frames:v','1',
                 '-f','rawvideo','-pix_fmt','gray','-']
            result=subprocess.run(cmd,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
            self.assertEqual(len(result.stdout),320*180)
            self.assertGreater(max(result.stdout),150)  # Text actually burned into the frame.

if __name__=='__main__':unittest.main()
