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
