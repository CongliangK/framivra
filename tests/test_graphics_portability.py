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
import unittest

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
