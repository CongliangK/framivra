"""Real native macOS CDP cache acceptance; writes only --output (new directory).

Requires the normal renderer dependencies and an installed Google Chrome app.
This is an explicit integration runner, not a mocked unit test or pixel parity claim.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from vtx.render import gfx_environment
from tests.runners.run_gfx_environment_acceptance import observe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--chrome', type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        parser.error('requires real native arm64 macOS')
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    project = root / 'project'; project.mkdir()
    html = project / 'page.html'
    html.write_text('''<!doctype html><meta charset="utf-8"><style>
body{margin:0;background:#faf2dd;color:#204050;font:28px sans-serif}
#text{padding:24px}#box{width:70px;height:30px;background:#e47b38}
</style><p id="text">中文 Framivra CDP 0123</p><div id="box"></div><script>
const f=Number(new URLSearchParams(location.search).get('f'));
box.style.transform=`translateX(${f*35}px)`;text.textContent+=` frame ${f}`;
</script>''')
    timeline = project / 'timeline.json'
    timeline.write_text(json.dumps({'version':2,'project':{'fps':30,'width':640,
        'height':180,'duration_frames':4},'sources':[],'subtitles':[],'audio_tracks':[],
        'video_tracks':[{'kind':'gfx','clips':[{'src':'page.html','dst_in':0,'dur':4}]}]}))
    environment = dict(os.environ, VTX_GFX='cdp', VTX_CHROME=str(args.chrome),
                       VTX_GFX_TABS='1', VTX_GFX_PAGES='1', VTX_GFX_SPLIT_FRAMES='0')
    summary = {'git':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
               'runs':{},'scope':'real macOS CDP; HTML mutation, corrupt PNG, missing environment, loader override'}
    def save():
        (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    def run(label, warm=False, extra=None, failure=False):
        env = dict(environment, **(extra or {})); start=time.monotonic()
        command=[sys.executable,'-m','vtx','-t',str(timeline),'gfx']
        result=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True,timeout=240)
        (root/(label+'.log')).write_text(result.stdout+result.stderr)
        row={'command':command,'seconds':time.monotonic()-start,'exit':result.returncode}
        summary['runs'][label]=row; save()
        if failure:
            assert result.returncode == 1, result.stdout + result.stderr
            assert 'gfx environment unknown' in result.stdout and 'refused' in result.stdout, result.stdout
            return
        assert result.returncode == 0, result.stdout+result.stderr
        assert ('帧已缓存' in result.stdout) == warm, result.stdout
        receipt=json.loads((project/'out/receipt.json').read_text())
        expected=receipt['gfx_backend']['environment_identity'].split('|')[-1]
        markers=[]
        for marker in (project/'cache/gfx').glob('*/complete.json'):
            record=json.loads(marker.read_text()); axes=record.get('environment',{}).get('axes',{})
            if gfx_environment.digest({k:v for k,v in axes.items() if k not in ('viewport','fps')}) == expected:
                markers.append(marker)
        marker=max(markers,key=lambda p:p.stat().st_mtime_ns)
        record=json.loads(marker.read_text()); gfx_environment.verify_certificate(record['environment'])
        frames={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in marker.parent.glob('f*.png')}
        assert len(frames)==4
        row.update(key=marker.parent.name,frames=frames,environment=record['environment'],warm=warm)
        save();return marker
    marker=run('cold')
    summary['observation']=observe(args.chrome.resolve(),html,environment);save()
    assert summary['observation']['fonts']['fonts']
    run('warm',warm=True)
    frame=next(marker.parent.glob('f*.png'));frame.write_bytes(b'broken PNG')
    run('corrupt-frame-repaired')
    assert summary['runs']['cold']['frames']==summary['runs']['corrupt-frame-repaired']['frames']
    old=json.loads(marker.read_text());old.pop('environment');marker.write_text(json.dumps(old))
    run('legacy-marker-rejected')
    html.write_text(html.read_text().replace('#e47b38','#38b7e4'))
    run('html-change')
    assert summary['runs']['cold']['key']!=summary['runs']['html-change']['key']
    assert summary['runs']['cold']['frames']!=summary['runs']['html-change']['frames']
    run('loader-override-rejected',extra={'DYLD_LIBRARY_PATH':'/does/not/exist'},failure=True)
    run('final-warm',warm=True)
    summary['pass']=True;save()
    print(json.dumps({'pass':True,'output':str(root)},indent=2))


if __name__ == '__main__':
    main()
