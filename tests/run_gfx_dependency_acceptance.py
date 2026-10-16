"""Opt-in bounded #47 real Chrome acceptance; no local/browser execution on import."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
MAX_EVIDENCE = 20 * 1024 * 1024
MAX_LOG = 128 * 1024


def font_provenance(font, *, query=None, notice=None):
    """Bind this exact font and notice to the same installed Debian package.

    This is installed-package provenance, not a claim of registry signature
    verification. Missing ownership/manifests/notice or modified bytes fail.
    """
    font = Path(font).resolve(strict=True)
    notice = Path(notice or '/usr/share/doc/fonts-dejavu-core/copyright').resolve(strict=True)
    if query is None:
        def query(*args):
            return subprocess.check_output(['dpkg-query', *args], text=True, timeout=10)
    package = 'fonts-dejavu-core'
    for path in (font, notice):
        expected = package + ': ' + str(path)
        if query('-S', str(path)).strip().splitlines() != [expected]:
            raise ValueError('font/notice package ownership mismatch: ' + str(path))
    fields = query('-W', '-f=${Package}\t${Version}\t${Status}\n', package).strip().split('\t')
    if len(fields) != 3 or fields[0] != package or not fields[1] or fields[2] != 'install ok installed':
        raise ValueError('font package installation/version unproven')
    files = set(query('-L', package).splitlines())
    if str(font) not in files or str(notice) not in files:
        raise ValueError('font/notice absent from matching package file list')
    manifest = {}
    for line in query('--control-show', package, 'md5sums').splitlines():
        digest, relative = line.split(None, 1)
        manifest['/' + relative.lstrip('/')] = digest
    for path in (font, notice):
        if manifest.get(str(path)) != hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest():
            raise ValueError('font/notice bytes do not match installed package manifest: ' + str(path))
    text = notice.read_text()
    stanzas = text.split('\n\n')
    if not any(stanza.startswith('Files: *\n') and '\nLicense: bitstream-vera\n' in stanza
               and 'Permission is hereby granted' in stanza for stanza in stanzas):
        raise ValueError('applicable font license stanza unproven')
    if font.name != 'DejaVuSans.ttf':
        raise ValueError('this bounded case only supports the selected DejaVuSans.ttf')
    return dict(source_path=str(font), package=package, version=fields[1],
        font_sha256=hashlib.sha256(font.read_bytes()).hexdigest(),
        notice_path=str(notice), notice_sha256=hashlib.sha256(notice.read_bytes()).hexdigest(),
        notice_text=text, applicable_license='bitstream-vera (font Files: * stanza)',
        verification='dpkg ownership + installed version + file list + package md5 manifest',
        artifact_font_bytes=False)


def copy_font_with_notice(root, font, provenance):
    """No temporary font copy without the verified corresponding notice."""
    data = Path(font).read_bytes()
    notice = provenance['notice_text'].encode()
    if (hashlib.sha256(data).hexdigest() != provenance['font_sha256'] or
            hashlib.sha256(notice).hexdigest() != provenance['notice_sha256']):
        raise ValueError('font or corresponding notice changed after provenance check')
    # Notice precedes font creation; never leave a new font without its notice.
    (root / 'font-LICENSE.txt').write_bytes(notice)
    (root / 'font.ttf').write_bytes(data)


def fixture(root, font, provenance):
    from PIL import Image
    root.mkdir(parents=True, exist_ok=True)
    for name in ('left', 'right', 'shared'):
        (root / name).mkdir()
    copy_font_with_notice(root, font, provenance)
    for name, color in [('left/image.png', '#ee2211'), ('right/image.png', '#1144ee'),
                        ('direct.png', '#22cc44')]:
        Image.new('RGB', (8, 8), color).save(root / name)
    (root / 'shared/router.css').write_text('@import "theme.css";')
    for name in ('left', 'right'):
        (root / name / 'alias.css').symlink_to('../shared/router.css')
        (root / name / 'theme.css').write_text(
            '#' + name + '{background-image:url("image.png");background-repeat:repeat}')
    (root / 'global.css').write_text(
        '@font-face{font-family:Fixture;src:url("font.ttf")}'
        'html,body{margin:0;width:1920px;height:1080px;background:#ffffff}'
        'div{position:absolute;width:24px;height:24px;top:0}'
        '#left{left:0}#right{left:32}#shared{left:64;background:#224466}'
        '#direct{position:absolute;left:96px;top:0;width:24px;height:24px}'
        'p{position:absolute;left:0;top:24px;margin:0;font:20px Fixture;color:#000000}')
    common = '<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="global.css">'
    (root / 'a.html').write_text(common +
        '<link rel="stylesheet" href="left/alias.css"><link rel="stylesheet" href="right/alias.css">'
        '<body><div id="left"></div><div id="right"></div><div id="shared"></div><p>MW47</p></body>')
    (root / 'b.html').write_text(common +
        '<body><div id="shared"></div><img id="direct" src="direct.png"><p>MW47</p></body>')
    return dict(version=2, project=dict(fps=30, width=1920, height=1080, duration_frames=4,
        gfx_dependency_policy='strict', cache_dependencies=['global.css', 'font.ttf']),
        sources=[], audio_tracks=[], subtitles=[], video_tracks=[dict(kind='gfx', name='pages', clips=[
            dict(src='a.html', dst_in=0, dur=2, cache_dependencies=[
                'shared/router.css','left/theme.css','right/theme.css','left/image.png','right/image.png']),
            dict(src='b.html', dst_in=2, dur=2, cache_dependencies=['direct.png'])])])


METRICS = '''(()=>({images:Array.from(document.images).map(i=>({src:i.currentSrc,
complete:i.complete,width:i.naturalWidth,height:i.naturalHeight})),
font:document.fonts.check('20px Fixture','MW47'),
faces:Array.from(document.fonts).map(f=>({family:f.family,status:f.status})),
family:getComputedStyle(document.querySelector('p')).fontFamily}))()'''


def worker(timeline, trace_path, injection=None):
    """Observe actual production calls; never fabricate a Fetch result or pixels."""
    from vtx import cli, gfx_render, gfx_cli
    from vtx.gfx_dependencies import RequestPolicy
    trace = dict(requests=[], acknowledgements=[], metrics=[], fallback_calls=0, injected=False)
    pending = {}
    original_response = RequestPolicy.response
    original_send = gfx_render.WSConn.send_text
    original_recv = gfx_render.WSConn.recv_msg
    original_call = gfx_render.Tab.call
    def save():
        trace_path.write_text(json.dumps(trace, indent=2))
    def append(key, row):
        if len(trace[key]) >= 2048:
            raise RuntimeError('bounded trace capacity exceeded')
        trace[key].append(row)
    def response(policy, params):
        method, args = original_response(policy, params)
        append('requests', dict(url=params['request']['url'], kind=params.get('resourceType'),
                               decision=method, request_id=params['requestId']))
        return method, args
    def send(ws, data):
        msg = json.loads(data)
        if msg.get('method') in ('Fetch.fulfillRequest', 'Fetch.failRequest'):
            pending[(id(ws), msg['id'])] = dict(id=msg['id'], method=msg['method'],
                                               request_id=msg['params']['requestId'])
        return original_send(ws, data)
    def recv(ws):
        op, data = original_recv(ws)
        if op != 0x8:
            msg = json.loads(data)
            row = pending.pop((id(ws), msg.get('id')), None)
            if row is not None:
                append('acknowledgements', dict(row, error=msg.get('error')))
        return op, data
    def call(tab, method, **kwargs):
        if method == 'Page.captureScreenshot':
            result = original_call(tab, 'Runtime.evaluate', expression=METRICS, returnByValue=True)
            if result.get('exceptionDetails'):
                raise AssertionError(result)
            measurements = result.get('result', {}).get('value')
            original_call(tab, 'DOM.enable')
            original_call(tab, 'CSS.enable')
            document = original_call(tab, 'DOM.getDocument')
            node = original_call(tab, 'DOM.querySelector', nodeId=document['root']['nodeId'], selector='p')
            fonts = original_call(tab, 'CSS.getPlatformFontsForNode', nodeId=node['nodeId'])
            measurements['platform_fonts'] = fonts.get('fonts', [])
            append('metrics', measurements)
        result = original_call(tab, method, **kwargs)
        if method == 'Fetch.enable' and injection and not trace['injected']:
            trace['injected'] = True
            target = ((timeline.parent / 'undeclared.html').as_uri() if injection == 'local'
                      else 'https://dependency-control.invalid/forbidden')
            # Controlled test-only navigation after real Fetch installation. The
            # production static source/record stays unchanged and script-disabled.
            original_call(tab, 'Page.navigate', url=target)
            for _ in range(4):
                original_call(tab, 'Runtime.evaluate', expression='true', returnByValue=True)
            raise AssertionError('runtime forbidden navigation was not rejected')
        return result
    def fallback(*args, **kwargs):
        trace['fallback_calls'] += 1
        raise AssertionError('strict mode attempted CLI fallback')
    try:
        with patch.object(RequestPolicy, 'response', response), \
             patch.object(gfx_render.WSConn, 'send_text', send), \
             patch.object(gfx_render.WSConn, 'recv_msg', recv), \
             patch.object(gfx_render.Tab, 'call', call), \
             patch.object(gfx_cli, 'render_cli', fallback), \
             patch.object(sys, 'argv', ['vtx', '-t', str(timeline), '--events-jsonl', str(trace_path.with_suffix('.events.jsonl')), 'gfx']):
            code = cli.main()
            if code:
                raise SystemExit(code)
    finally:
        trace['pending_acknowledgements'] = list(pending.values())
        save()


def check_trace(trace, expected_urls, *, rejected=False):
    assert trace['fallback_calls'] == 0, trace
    if rejected:
        assert any(r['decision'] == 'Fetch.failRequest' for r in trace['requests']), trace
    else:
        assert trace['metrics'], 'no actual pre-screenshot DOM measurements'
        for m in trace['metrics']:
            assert m and m['font'] is True and 'Fixture' in m['family'], m
            assert any(f['family'] == 'Fixture' and f['status'] == 'loaded' for f in m['faces']), m
            assert any(f.get('isCustomFont') is True and f.get('glyphCount', 0) >= 4
                       and 'DejaVu' in f.get('familyName', '') for f in m['platform_fonts']), m
            assert all(i['complete'] and i['width'] == 8 and i['height'] == 8 for i in m['images']), m
        assert expected_urls <= {r['url'].split('?')[0] for r in trace['requests']}, trace
        assert all(r['decision'] == 'Fetch.fulfillRequest' for r in trace['requests']), trace
    assert not trace['pending_acknowledgements'], trace
    assert len(trace['acknowledgements']) == len(trace['requests']), trace
    assert all(a['error'] is None for a in trace['acknowledgements']), trace
    assert {a['request_id'] for a in trace['acknowledgements']} == {r['request_id'] for r in trace['requests']}


def restore_edit(path, old, new):
    before = path.stat()
    data = path.read_bytes()
    assert len(old) == len(new) and data.count(old) == 1
    path.write_bytes(data.replace(old, new))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_mtime_ns == before.st_mtime_ns and path.stat().st_size == before.st_size


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--worker', type=Path)
    parser.add_argument('--trace', type=Path)
    parser.add_argument('--inject', choices=('local', 'external'))
    args = parser.parse_args()
    if args.worker:
        return worker(args.worker.resolve(), args.trace, args.inject)
    if not args.output:
        parser.error('--output is required')
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    evidence = root / 'evidence'; evidence.mkdir()
    report = dict(passed=False, frames_per_page=2, max_rendered_frames=24, commands=[], cases=[])
    deadline = time.monotonic() + 480
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', VTX_GFX='cdp', VTX_GFX_PAGES='1',
        VTX_GFX_TABS='1', VTX_GFX_SPLIT_FRAMES='0', VTX_GFX_NAVFREE='0', VTX_GFX_STATIC='0',
        VTX_NO_NVENC='1', VTX_SEG_NVENC='0', VTX_VIDEO_SEGMENTS='2', VTX_DOCTOR_OFFLINE='1')
    env.pop('VTX_GFX_CACHE_NS', None)
    def save():
        (evidence / 'report.json').write_text(json.dumps(report, indent=2))
        assert not any(p.suffix.lower() in ('.ttf','.otf','.woff','.woff2') for p in evidence.rglob('*')), 'font bytes are not artifact evidence'
        assert sum(p.stat().st_size for p in evidence.rglob('*') if p.is_file()) <= MAX_EVIDENCE
    def run(argv, label, expect_failure=False, limit=90):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('eight minute subprocess budget exhausted')
        # Logs go to ephemeral root, only capped copies enter uploaded evidence.
        log = root / (label + '.raw-log')
        with log.open('wb') as stream:
            proc = subprocess.Popen(list(map(str, argv)), cwd=REPO, env=env,
                                    stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                proc.wait(timeout=min(limit, remaining))
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL); proc.wait()
                raise
            finally:
                with log.open('rb') as source:
                    content = source.read(MAX_LOG)
                (evidence / (label + '.log')).write_bytes(content)
                report['commands'].append(dict(label=label, argv=list(map(str, argv)),
                    returncode=proc.returncode, log_bytes=log.stat().st_size,
                    truncated=log.stat().st_size > MAX_LOG))
                save()
        assert log.stat().st_size <= MAX_LOG, ('required log exceeded evidence cap', label)
        assert (proc.returncode != 0) == expect_failure, (label, proc.returncode)
        return content.decode(errors='replace')
    try:
        from PIL import Image
        from vtx.infra import Ctx
        from vtx.gfx_dependencies import resolve
        from vtx.hygiene import gfx_dir, gc_plan
        from vtx.impact import capture, compare
        from tests.impact_inventory import observe, delta, validate_sidecars, is_pass_sidecar
        chrome = shutil.which('google-chrome') or shutil.which('google-chrome-stable')
        font = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
        assert chrome and font.is_file(), 'preinstalled official Chrome and DejaVu font required'
        provenance = font_provenance(font)
        (evidence/'font-provenance.json').write_text(json.dumps(provenance,indent=2))
        (evidence/'font-LICENSE.txt').write_text(provenance['notice_text'])
        env['VTX_CHROME'] = chrome
        report['identity'] = {k: run(['git', *cmd], k, limit=10).strip() for k, cmd in [
            ('head', ['rev-parse', 'HEAD']), ('tree', ['rev-parse', 'HEAD^{tree}']),
            ('status', ['status', '--porcelain'])]}
        assert not report['identity']['status'], 'requires clean frozen checkout'
        run([chrome, '--version'], 'chrome-version', limit=10)
        run([chrome, '--headless', '--disable-gpu', '--no-first-run',
             '--user-data-dir=' + str(root/'preflight-profile'), '--dump-dom', 'about:blank'],
            'sandbox-preflight', limit=30)
        project = root / 'project'
        tl = fixture(project, font, provenance)
        timeline = project / 'timeline.json'; timeline.write_text(json.dumps(tl))
        (project / 'undeclared.html').write_text('<p>Forbidden</p>')
        report['font'] = {k:v for k,v in provenance.items() if k != 'notice_text'}
        expected_shared = (34, 68, 102); expected_left = (238, 34, 17)
        previous = {}; before = None
        def render(label, changed):
            nonlocal before, previous
            c = Ctx(timeline)
            records = [resolve(c, cl) for cl in tl['video_tracks'][0]['clips']]
            current_snapshot = capture(tl, project, nseg=2)
            (evidence / (label+'-records.json')).write_text(json.dumps(records, indent=2))
            inputs = {}
            for path in sorted(project.rglob('*')):
                if not path.is_file() or path.parts[len(project.parts)] in ('cache', 'out'):
                    continue
                data = path.read_bytes()
                row = dict(sha256=hashlib.sha256(data).hexdigest(), size=len(data))
                if path.suffix in ('.html', '.css', '.json'):
                    row['text'] = data.decode()
                elif path.suffix == '.png':
                    row['base64'] = base64.b64encode(data).decode()
                if path.is_symlink(): row['symlink'] = os.readlink(path)
                inputs[str(path.relative_to(project))] = row
            (evidence/(label+'-inputs.json')).write_text(json.dumps(inputs,indent=2))
            run([sys.executable, '-m', 'vtx', '-t', timeline, 'validate'], label+'-validate')
            run([sys.executable, __file__, '--worker', timeline, '--trace', evidence/(label+'-trace.json')], label+'-gfx')
            trace = json.loads((evidence/(label+'-trace.json')).read_text())
            report['rendered_frames'] = report.get('rendered_frames', 0) + len(trace['metrics'])
            assert report['rendered_frames'] <= 24
            if changed:
                urls = {r['url'] for i in changed for r in records[i].get('resource_requests', [])}
                check_trace(trace, urls)
            else:
                assert not trace['requests'] and not trace['metrics'] and trace['fallback_calls'] == 0
            shutil.copyfile(project/'out/receipt.json', evidence/(label+'-gfx-receipt.json'))
            run([sys.executable, '-m', 'vtx', '-t', timeline, 'video'], label+'-video', limit=90)
            shutil.copyfile(project/'out/receipt.json', evidence/(label+'-video-receipt.json'))
            for i, output in enumerate((project/'out/video_noaudio.mp4', project/'cache/qc_clean.mp4')):
                run(['ffmpeg','-v','error','-xerror','-threads','1','-i',output,
                     '-map','0:v:0','-f','null','-'],label+'-output-decode-'+str(i))
                value = run(['ffprobe','-v','error','-count_frames','-select_streams','v:0',
                    '-show_entries','stream=width,height,nb_read_frames,r_frame_rate','-of','json',output],
                    label+'-output-probe-'+str(i))
                stream = json.loads(value)['streams'][0]
                assert [stream['width'],stream['height'],int(stream['nb_read_frames']),stream['r_frame_rate']] == [1920,1080,4,'30/1']
            observation = observe(project); current = observation['products']
            def count_frames(path):
                value = run(['ffprobe','-v','error','-count_frames','-select_streams','v:0',
                    '-show_entries','stream=nb_read_frames','-of','json',path], label+'-probe-'+path.stem)
                return json.loads(value)['streams'][0]['nb_read_frames']
            sidecars = validate_sidecars(project, current, count_frames, {})
            assert sidecars, 'no actual vseg_pass sidecar evidence'
            observation['sidecar_checks'] = sidecars
            for namespace in ('vseg', 'vseg_qc', 'vseg_pass'):
                assert any(p.startswith('cache/'+namespace+'/') for p in current), namespace
            for i, path in enumerate(sorted(current.keys() - previous.keys())):
                p = project/path
                if p.suffix != '.png' and not is_pass_sidecar(path):
                    run(['ffmpeg','-v','error','-xerror','-threads','1','-i',p,'-map','0:v:0','-f','null','-'],label+'-decode-'+str(i))
            for i, clip in enumerate(tl['video_tracks'][0]['clips']):
                frames = sorted(gfx_dir(c, clip).glob('f*.png'))
                assert len(frames) == 2
                for frame in frames:
                    with Image.open(frame) as im:
                        im.load(); rgb = im.convert('RGB')
                        assert rgb.size == (1920,1080)
                        assert rgb.getpixel((72,8)) == expected_shared
                        if i == 0:
                            assert rgb.getpixel((8,8)) == expected_left
                            assert rgb.getpixel((40,8)) == (17,68,238)
                        else:
                            assert rgb.getpixel((104,8)) == (34,204,68)
                        glyph = rgb.crop((0,26,75,50))
                        assert sum(max(pixel)<100 for pixel in glyph.getdata()) > 30, 'no visible glyph ROI'
                shutil.copyfile(frames[0], evidence/(label+'-page-'+str(i)+'.png'))
            paths = sorted({n['artifact'] for n in current_snapshot['nodes'].values() if n['artifact']})
            prediction = compare(before, current_snapshot) if before else None
            result = delta(previous, current, prediction['new_artifacts'] if prediction else paths,
                {gfx_dir(c,cl).relative_to(project).as_posix():2 for cl in tl['video_tracks'][0]['clips']})
            assert result['passed'], result
            if not changed: assert previous == current, 'warm rewrote cached bytes/mtime'
            added_gfx = [p for p in result['actual_new'] if p.startswith('cache/gfx/')]
            assert len(added_gfx) == len(changed), added_gfx
            report['cases'].append(dict(label=label, changed_pages=changed, reconciliation=result))
            (evidence/(label+'-inventory.json')).write_text(json.dumps(observation,indent=2))
            before, previous = current_snapshot, current
            save()
        render('cold', [0,1]); render('warm', [])
        # Require visibly changed bytes with both size and mtime preserved; a
        # different encoder that cannot preserve size must fail this acceptance.
        image = project/'left/image.png'; stat = image.stat()
        old_hash = hashlib.sha256(image.read_bytes()).hexdigest()
        Image.new('RGB',(8,8),'#cc8811').save(image); os.utime(image,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        assert image.stat().st_size == stat.st_size and image.stat().st_mtime_ns == stat.st_mtime_ns
        assert hashlib.sha256(image.read_bytes()).hexdigest() != old_hash
        report['local_mutation'] = dict(size=stat.st_size,mtime_ns=stat.st_mtime_ns,old_sha256=old_hash,
            new_sha256=hashlib.sha256(image.read_bytes()).hexdigest())
        expected_left=(204,136,17)
        render('local-restored-mtime',[0])
        restore_edit(project/'global.css',b'#224466',b'#664422'); expected_shared=(102,68,34)
        render('shared-restored-mtime',[0,1])
        plan = gc_plan(Ctx(timeline))
        planned = [str(p.relative_to(project)) for entries in plan.values() for p,_,_ in entries]
        assert any(p.startswith('cache/gfx/') for p in planned), planned
        run([sys.executable,'-m','vtx','-t',timeline,'gc'],'gc')
        after_gc = observe(project)['products']
        assert all(not (project/p).exists() for p in planned)
        c=Ctx(timeline)
        assert all((gfx_dir(c,cl)/'f0000.png').is_file() for cl in tl['video_tracks'][0]['clips'])
        for node in before['nodes'].values():
            artifact = node['artifact']
            if artifact:
                assert (project/artifact).exists(), ('GC removed live artifact', artifact)
        report['gc'] = dict(planned=planned, remaining=after_gc)
        # Failures use separate project directories, never warm cache bypass.
        for case, html in [('unknown','<style/>body{color:red}'),
                           ('unknown-space','<style />body{color:red}'),
                           ('unknown-case','<STYLE/>body{color:red}'),
                           ('unknown-attr','<style id="s"/>body{color:red}'),
                           ('unknown-nested','<table><style/></table>'),
                           ('undeclared','<img src="missing.png">'),
                           ('external','<img src="https://dependency-control.invalid/x.png">')]:
            neg=root/case; negative=fixture(neg,font,provenance); (neg/'a.html').write_text(html)
            negative['video_tracks'][0]['clips'] = negative['video_tracks'][0]['clips'][:1]
            negative['project']['duration_frames'] = 2
            nt=neg/'timeline.json';nt.write_text(json.dumps(negative))
            original=(neg/'a.html').read_bytes()
            run([sys.executable,__file__,'--worker',nt,'--trace',evidence/(case+'-trace.json')],case,True)
            tr=json.loads((evidence/(case+'-trace.json')).read_text())
            assert tr['requests']==[] and tr['fallback_calls']==0 and original==(neg/'a.html').read_bytes()
            assert not list(neg.rglob('complete.json'))
        for case in ('local','external'):
            neg=root/('runtime-'+case);negative=fixture(neg,font,provenance)
            negative['video_tracks'][0]['clips'] = negative['video_tracks'][0]['clips'][:1]
            negative['project']['duration_frames'] = 2
            (neg/'undeclared.html').write_text('<p>Forbidden</p>')
            nt=neg/'timeline.json';nt.write_text(json.dumps(negative))
            trace=evidence/('runtime-'+case+'-trace.json')
            run([sys.executable,__file__,'--worker',nt,'--trace',trace,'--inject',case], 'runtime-'+case,True)
            check_trace(json.loads(trace.read_text()),set(),rejected=True)
            assert not list(neg.rglob('complete.json'))
        report['passed']=True
    except Exception as error:
        report['error']=f'{type(error).__name__}: {error}'
        raise
    finally:
        save()


if __name__ == '__main__':
    main()
