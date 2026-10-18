#!/usr/bin/env python3
"""Low-load functional matrix. These diagnostics are not performance evidence."""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import random
import sys

from PIL import Image
from run import Shell, render, atomic_json, png_decode_rgba

HTML = '''<!doctype html><meta charset="utf-8"><style>
@font-face {font-family:probe;src:url("FONT")}
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:transparent}
#gradient{position:absolute;inset:30px;background:radial-gradient(circle,rgba(20,70,240,.6),transparent);filter:blur(14px)}
#text{position:absolute;top:50px;left:30px;font:48px probe;color:rgba(20,30,80,.7)}
img{position:absolute;top:140px;left:30px;width:96px;height:96px}
svg{position:absolute;right:40px;bottom:40px;width:150px;height:150px}
</style><div id="gradient"></div><div id="text">Font / 渐变 / alpha</div>
<img src="data:image/png;base64,IMAGE"><svg viewBox="0 0 100 100"><defs><filter id="blur"><feGaussianBlur stdDeviation="3"/></filter></defs><circle cx="50" cy="50" r="35" fill="#de5711" opacity=".6" filter="url(#blur)"/></svg>
<script>
window.__vtx_render=f=>{if(f===999)throw Error('intentional frame error');
 document.getElementById('text').style.transform='translateX('+(f%90)+'px)';
 document.getElementById('gradient').style.opacity=(.2+(f%20)/25);
 document.querySelector('svg').style.transform='rotate('+(f%360)+'deg)';};
window.__vtx_render(+(new URLSearchParams(location.search).get('f')||0));
</script>'''


def fixture(path):
    image = Image.new('RGBA', (48,48),(20,80,200,117))
    buf = io.BytesIO();image.save(buf,format='PNG')
    font=Path('/usr/share/fonts/gnu-free/FreeSans.ttf')
    path.write_text(HTML.replace('FONT','data:font/ttf;base64,'+base64.b64encode(font.read_bytes()).decode()).replace('IMAGE',
        base64.b64encode(buf.getvalue()).decode()))
    return {'page_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'font_sha256':hashlib.sha256(font.read_bytes()).hexdigest()}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary',required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--count',type=int,default=16)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    page=a.output/'heavy.html';inputs=fixture(page)
    rng=random.Random(67063);frames=[0,0,22,1,22]+[rng.randrange(120) for _ in range(a.count)]
    report={'seed':67063,'frames':frames,'inputs':inputs,'performance_status':None}
    clean={}
    for nav in (True,False):
        arm='navigation' if nav else 'beginframe'
        shell=Shell(a.binary,a.output/arm,640,360,navigation=nav)
        try:
            clean[arm]=render(shell,page,frames,a.output/arm/'frames',navigation=nav)
            if not nav:
                diag=render(shell,page,frames,a.output/arm/'diagnostic',diagnostic=True)
                report['canary_statuses']=[x['canary']['status'] for x in diag]
                tab=shell.tabs[-1]
                idle=[tab.call('HeadlessExperimental.beginFrame',frameTimeTicks=200000+i*34,interval=1000/30) for i in range(3)]
                report['idle_no_screenshot']=idle
                capture=tab.call('HeadlessExperimental.beginFrame',frameTimeTicks=201000,interval=1000/30,screenshot={'format':'png'})
                if not capture.get('screenshotData'):raise AssertionError('no output after idle frame')
                rgba=png_decode_rgba(base64.b64decode(capture['screenshotData']))
                report['capture_after_idle_same_pixels']=hashlib.sha256(rgba.tobytes()).hexdigest()==diag[-1]['rgba_sha256']
                try:
                    render(shell,page,[999],a.output/arm/'error')
                except RuntimeError as error:
                    report['intentional_error']=str(error)
                else:raise AssertionError('throwing render accepted')
                recovery=render(shell,page,[22],a.output/arm/'recovery')
                report['recovery_same_pixels']=recovery[0]['rgba_sha256']==clean[arm][2]['rgba_sha256']
            report[arm]=clean[arm]
            report[arm+'_environment']=shell.metadata
        finally:shell.close()
    report['full_frame_exact']=[x['rgba_sha256']==y['rgba_sha256'] for x,y in
                              zip(clean['navigation'],clean['beginframe'])]
    byframe={};report['history_independent']=True
    for x in clean['beginframe']:
        f=x['token']['frame'];h=x['rgba_sha256']
        if f in byframe and byframe[f]!=h:report['history_independent']=False
        byframe[f]=h
    report['no_damage_results']=[x['hasDamage'] for x in clean['beginframe']]
    report['ok']=all(report['full_frame_exact']) and report['history_independent'] and report['recovery_same_pixels']
    atomic_json(a.output/'functional.json',report)
    print(json.dumps({k:report[k] for k in ('ok','full_frame_exact','history_independent','recovery_same_pixels','no_damage_results')}))
    return 0 if report['ok'] else 1

if __name__=='__main__':sys.exit(main())
