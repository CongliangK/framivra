"""Real #48 acceptance runner. Writes only an explicitly chosen evidence root.

No unit stubs. Uses complete HTML/JS/text, isolated fontconfig and real CDP,
CLI and CEF production invocations. The CEF negative replaces libcef.so bytes
without changing the worker (an inert ELF trailer, not a version-upgrade claim).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


HTML = '''<!doctype html><meta charset="utf-8"><style>
html,body{margin:0;background:#f8f4e9;color:#243345;font-family:Fixture,serif}
p{font-size:28px;margin:14px}#box{margin:12px;width:80px;height:30px;background:#e27c4f}
</style><p id="text">MW48: Hello, Framivra! 012345</p><div id="box"></div><script>
const f=Number(new URLSearchParams(location.search).get('f'));
document.querySelector('#box').style.transform=`translateX(${f*23}px)`;
document.querySelector('#text').textContent += ` frame ${f}`;
</script>'''


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def command(argv, env=None):
    r = subprocess.run(argv, env=env, cwd=ROOT, capture_output=True, text=True, timeout=180)
    if r.returncode:
        raise RuntimeError(f'{argv}: {r.returncode}\n{r.stdout}\n{r.stderr}')
    return r.stdout


def compare(a, b):
    from PIL import Image, ImageChops
    out = []
    for x, y in zip(sorted(a.glob('f*.png')), sorted(b.glob('f*.png'))):
        with Image.open(x) as left, Image.open(y) as right:
            left, right = left.convert('RGBA'), right.convert('RGBA')
            diff = ImageChops.difference(left, right)
            out.append({'frame': x.name, 'equal_rgba': left.tobytes() == right.tobytes(),
                        'extrema': diff.getextrema(), 'sha256': [sha(x), sha(y)]})
    assert len(out) == 4
    return out


def observe(chrome, html, environment):
    """Actual browser version, active renderer and consumed font, independent of flags."""
    code = '''
import json, urllib.request
from vtx.render.gfx_render import Browser, WSConn
b=Browser(chrome_path=CHROME,tabs=1,width=640,height=180)
try:
 b.start(); t=b.open_tabs()[0]
 try:
  t.navigate(HTML.as_uri()+'?f=0')
  t.call('DOM.enable');t.call('CSS.enable')
  root=t.call('DOM.getDocument')['root']['nodeId']
  node=t.call('DOM.querySelector',nodeId=root,selector='#text')['nodeId']
  fonts=t.call('CSS.getPlatformFontsForNode',nodeId=node)
  info=json.load(urllib.request.urlopen(f'http://127.0.0.1:{b.port}/json/version'))
  ws=WSConn(b.port,'/'+info['webSocketDebuggerUrl'].split('/',3)[3])
  try:
   ws.send_text(json.dumps(dict(id=1,method='SystemInfo.getInfo',params={})))
   while True:
    _,data=ws.recv_msg();msg=json.loads(data)
    if msg.get('id')==1:
     assert 'error' not in msg,msg
     system=msg['result'];break
  finally: ws.close()
  print(json.dumps(dict(version=t.call('Browser.getVersion'),system_info=system,fonts=fonts)))
 finally:t.close()
finally:b.stop()
'''
    prefix = 'from pathlib import Path\nCHROME=' + repr(str(chrome)) + '\nHTML=Path(' + repr(str(html)) + ')\n'
    return json.loads(command([sys.executable, '-c', prefix + code], environment))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--chrome', type=Path, required=True)
    p.add_argument('--alternate-chrome', type=Path, required=True)
    p.add_argument('--cef-worker', type=Path, required=True)
    args = p.parse_args()
    root = args.output.resolve()
    root.mkdir(exist_ok=False, parents=True)
    fonts = root / 'fonts'; fonts.mkdir()
    font_a = Path(command(['fc-match', '-f', '%{file}', 'DejaVu Sans']).strip())
    font_b = Path(command(['fc-match', '-f', '%{file}', 'DejaVu Serif']).strip())
    assert sha(font_a) != sha(font_b)
    active = fonts / 'active.ttf'
    shutil.copyfile(font_a, active)
    config = root / 'fonts.conf'
    config.write_text(f'<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">'
                      f'<fontconfig><dir>{fonts}</dir><cachedir>{root / "font-cache"}</cachedir>'
                      '<alias><family>Fixture</family><prefer><family>DejaVu Sans</family>'
                      '</prefer></alias></fontconfig>')
    project = root / 'project'; project.mkdir()
    html = project / 'page.html'; html.write_text(HTML)
    timeline = project / 'timeline.json'
    timeline.write_text(json.dumps({'version': 2, 'project': {'fps': 30, 'width': 640,
        'height': 180, 'duration_frames': 4}, 'sources': [], 'subtitles': [], 'audio_tracks': [],
        'video_tracks': [{'kind': 'gfx', 'clips': [{'src': 'page.html', 'dur': 4, 'dst_in': 0}]}]}))
    base_env = dict(os.environ, FONTCONFIG_FILE=str(config), VTX_GFX_CACHE_NS='',
                    VTX_GFX_PAGES='1', VTX_GFX_TABS='1', VTX_GFX_SPLIT_FRAMES='0')
    summary = {'schema': 'vtx.gfx-environment-acceptance/1',
               'scope': 'full HTML + JavaScript + consumed text fonts; all 4 frames',
               'git': command(['git', 'rev-parse', 'HEAD']).strip(), 'runs': {}, 'comparisons': {}}

    def run(label, backend='cdp', browser=None, worker=None, expected_warm=False):
        environment = dict(base_env, VTX_GFX=backend, VTX_CHROME=str(browser or args.chrome),
                           VTX_CEF_WORKER=str(worker or args.cef_worker), VTX_CEF_MODE='a0')
        stdout = command([sys.executable, '-m', 'vtx', '-t', str(timeline), 'gfx'], environment)
        (root / (label + '.stdout.txt')).write_text(stdout)
        receipt = json.loads((project / 'out/receipt.json').read_text())
        assert receipt['stages']['gfx']['ok'], receipt
        assert ('帧已缓存' in stdout) == expected_warm, stdout
        ns = 'gfx_exp/cef-alpha' if backend == 'cef' else 'gfx'
        entries = list((project / 'cache' / ns).glob('*/complete.json'))
        expected = receipt['gfx_backend']['environment_identity'].split('|')[-1]
        # Each marker contains a viewport-bound certificate; receipt binds axes
        # before viewport (the timeline already binds viewport in its own bytes).
        from vtx.render.gfx_environment import digest
        entries = [f for f in entries if digest({k: v for k, v in
            json.loads(f.read_text())['environment']['axes'].items() if k not in ('viewport', 'fps')}) == expected]
        assert len(entries) == 1, [str(e) for e in entries]
        marker = entries[0]
        saved = root / label; shutil.copytree(marker.parent, saved)
        row = {'backend': backend, 'key': marker.parent.name,
               'environment_sha256': json.loads(marker.read_text())['environment']['sha256'],
               'frames': {f.name: sha(f) for f in saved.glob('f*.png')},
               'warm': expected_warm, 'receipt': receipt}
        summary['runs'][label] = row
        (root / 'summary.json').write_text(json.dumps(summary, indent=2))
        return saved, marker.parent

    a, cache_a = run('cdp-font-a')
    summary['browser_a_observation'] = observe(args.chrome, html, base_env)
    run('cdp-warm', expected_warm=True)
    # Remove only the identity fields to simulate an old, otherwise valid cache.
    marker = cache_a / 'complete.json'; old = json.loads(marker.read_text())
    old.pop('environment'); marker.write_text(json.dumps(old))
    run('cdp-old-marker-rejected')
    active_stat = active.stat(); shutil.copyfile(font_b, active)
    os.utime(active, ns=(active_stat.st_atime_ns, active_stat.st_mtime_ns))
    command(['fc-cache', '-f', str(fonts)], base_env)
    b, _ = run('cdp-font-b')
    summary['browser_b_font_observation'] = observe(args.chrome, html, base_env)
    summary['comparisons']['font_change'] = compare(a, b)
    assert all(not f['equal_rgba'] for f in summary['comparisons']['font_change'])
    alternate, _ = run('alternate-browser', browser=args.alternate_chrome)
    summary['alternate_browser_observation'] = observe(args.alternate_chrome, html, base_env)
    summary['comparisons']['browser_change'] = compare(b, alternate)
    assert summary['runs']['cdp-font-b']['key'] != summary['runs']['alternate-browser']['key']
    cli, _ = run('cli-font-b', backend='cli')
    summary['comparisons']['cdp_cli'] = compare(b, cli)
    # Same browser path, different backend must not reuse a CDP directory.
    assert summary['runs']['cli-font-b']['key'] != summary['runs']['cdp-font-b']['key']
    runtime = root / 'cef-runtime-copy'
    shutil.copytree(args.cef_worker.resolve().parent, runtime)
    cef, _ = run('cef-runtime-a', backend='cef', worker=runtime / args.cef_worker.name)
    lib = runtime / 'libcef.so'
    before = sha(lib)
    with lib.open('ab') as f:
        f.write(b'\nVTX_48_INERT_ELF_TRAILER\n')
    summary['cef_runtime_negative'] = {'worker_unchanged':
        sha(args.cef_worker) == sha(runtime / args.cef_worker.name),
        'library_before': before, 'library_after': sha(lib),
        'kind': 'real libcef.so file replacement with inert ELF trailer; same version, not upgrade'}
    cef_b, _ = run('cef-runtime-b', backend='cef', worker=runtime / args.cef_worker.name)
    summary['comparisons']['cef_library_replacement'] = compare(cef, cef_b)
    assert summary['runs']['cef-runtime-a']['key'] != summary['runs']['cef-runtime-b']['key']
    # Font fallback under CEF as well, at the same font path.
    shutil.copyfile(font_a, active); command(['fc-cache', '-f', str(fonts)], base_env)
    cef_font, _ = run('cef-font-a', backend='cef', worker=runtime / args.cef_worker.name)
    summary['comparisons']['cef_font_change'] = compare(cef, cef_font)
    assert all(not f['equal_rgba'] for f in summary['comparisons']['cef_font_change'])
    summary['passed'] = True
    (root / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps({'passed': True, 'root': str(root), 'runs': len(summary['runs'])}))


if __name__ == '__main__':
    main()
