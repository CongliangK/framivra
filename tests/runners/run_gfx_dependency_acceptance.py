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
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

REPO = Path(__file__).resolve().parents[2]
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
        '#left{left:0}#right{left:32px}#shared{left:64px;background:#224466}'
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


def evidence_url(value):
    """Keep fixture routes/frame query only; other URL values are digest-only."""
    if value is None:
        return None
    def digest(text):
        return 'sha256:' + hashlib.sha256(text.encode()).hexdigest()
    try:
        parsed = urlsplit(value)
        authority = parsed.netloc.rsplit('@', 1)[-1]
        query = [(key, val if key == 'f' and val.isascii() and val.isdecimal()
                  else digest(val)) for key, val in parse_qsl(parsed.query, keep_blank_values=True)]
        return urlunsplit((parsed.scheme, authority, parsed.path, urlencode(query),
                           digest(parsed.fragment) if parsed.fragment else ''))
    except ValueError:
        return digest(value)


class ProtocolObserver:
    """Passive, allowlisted transport metadata; no CDP calls or request mutation."""
    def __init__(self, append):
        self.append = append
        self.connections = {}
        self.pending = {}
        self.sequence = 0

    def observe(self, ws, direction, msg):
        # Retain the object for this bounded worker; id cannot be reused while
        # registered. This does not open/close or otherwise operate the socket.
        if id(ws) not in self.connections:
            self.connections[id(ws)] = (ws, len(self.connections) + 1)
        connection = self.connections[id(ws)][1]
        key = (connection, msg.get('id'))
        method = msg.get('method')
        row = None
        if direction == 'send' and method and not method.startswith('Fetch.'):
            self.pending[key] = method
            row = dict(method=method, command_id=msg.get('id'))
            if method == 'Page.navigate':
                row['url'] = evidence_url(msg.get('params', {}).get('url'))
        elif direction == 'recv' and method in ('Fetch.requestPaused', 'Network.requestWillBeSent'):
            params = msg.get('params', {})
            request = params.get('request', {})
            row = dict(event=method, request_id=params.get('requestId'),
                frame_id=params.get('frameId'), network_id=params.get('networkId'),
                resource_type=params.get('resourceType', params.get('type')),
                request_method=request.get('method'), url=evidence_url(request.get('url')),
                pending_commands=[dict(command_id=k[1], method=v)
                    for k, v in self.pending.items() if k[0] == connection])
        elif direction == 'recv' and key in self.pending:
            command = self.pending.pop(key)
            row = dict(method=command, command_id=msg.get('id'), completed=True,
                       error_present='error' in msg)
            if command == 'Page.navigate':
                row['frame_id'] = msg.get('result', {}).get('frameId')
        if row is not None:
            self.sequence += 1
            row.update(sequence=self.sequence, connection=connection, direction=direction,
                       monotonic_ns=time.monotonic_ns())
            self.append('protocol', row)


def check_gc_contract(before, after, planned, current_keys):
    """#47 protects current keyed gfx/seg/audio; native GC reclaims vseg intermediates."""
    def under(name, roots):
        return any(name == root or name.startswith(root + '/') for root in roots)
    protected_outputs = {'out/video_noaudio.mp4', 'out/receipt.json'}
    assert protected_outputs <= {name for group in before.values() for name in group}
    assert current_keys and all(root.startswith(('cache/gfx/', 'cache/seg/', 'cache/audio/'))
                                for root in current_keys)
    protected = set()
    for group in before.values():
        for name, identity in group.items():
            if name in protected_outputs or under(name, current_keys):
                protected.add(name)
                assert not under(name, planned), ('GC plans protected artifact', name)
                found = [g[name] for g in after.values() if name in g]
                assert found == [identity], ('GC changed protected artifact', name)
    for root in current_keys:
        assert under(root, protected) or any(name.startswith(root + '/') for name in protected), root
    old_products, new_products = before['products'], after['products']
    assert not set(new_products) - set(old_products), 'GC created unexpected products'
    removed = []
    for name, identity in old_products.items():
        if under(name, planned):
            assert name.startswith(('cache/gfx/', 'cache/seg/', 'cache/audio/',
                                    'cache/vseg/', 'cache/vseg_qc/', 'cache/vseg_draft/')), name
            assert name not in new_products, ('GC retained planned product', name)
            removed.append(name)
        else:
            assert new_products.get(name) == identity, ('GC changed unplanned product', name)
    assert any(name.startswith('cache/gfx/') for name in removed), 'no stale gfx actually reclaimed'
    return dict(protected=sorted(protected), removed_products=sorted(removed),
                disposable_namespaces=['cache/vseg', 'cache/vseg_qc', 'cache/vseg_draft'])


def prepare_font_observation(tab, call, owned_target):
    """Enable inspector sources only on this newly owned, proven-empty target."""
    assert owned_target, 'unowned target cannot prepare font observation'
    info = call(tab, 'Target.getTargetInfo')['targetInfo']
    tree = call(tab, 'Page.getFrameTree')['frameTree']
    frame = tree['frame']
    assert info['targetId'] == owned_target and info['type'] == 'page', info
    assert info['url'] == frame['url'] == 'about:blank', 'target already has a document'
    assert frame['id'] == owned_target and not frame.get('parentId') and not tree.get('childFrames'), tree
    blank = call(tab, 'Runtime.evaluate', expression=(
        '({url:location.href,top:window===window.top,empty:document.body!==null&&'
        'document.body.childNodes.length===0})'), returnByValue=True)
    assert not blank.get('exceptionDetails') and blank.get('result', {}).get('value') == {
        'url': 'about:blank', 'top': True, 'empty': True}, blank
    call(tab, 'DOM.enable')
    call(tab, 'CSS.enable')
    return dict(target_id=owned_target, frame_id=frame['id'], url='about:blank')


def worker(timeline, trace_path, injection=None, stage="gfx"):
    """Observe actual production calls; never fabricate a Fetch result or pixels."""
    from vtx import cli, gfx_render, gfx_cli, events, gfx_dependencies
    from vtx.gfx_dependencies import RequestPolicy
    trace = dict(font_observation_targets=[], protocol=[], requests=[], acknowledgements=[], metrics=[], fallback_calls=0, injected=False,
        screenshot_attempts=0, production_failures=[], propagated_production_failure=False,
        invocation=dict(stage=stage, timeline=str(timeline),
            timeline_sha256=hashlib.sha256(timeline.read_bytes()).hexdigest(),
            head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True,timeout=10).strip(),
            tree=subprocess.check_output(['git','rev-parse','HEAD^{tree}'],cwd=REPO,text=True,timeout=10).strip()))
    failure_objects = []
    original_check = RequestPolicy.check
    original_resolve = gfx_dependencies.resolve
    original_init = events.ExecutionContext.__init__
    def observe_init(context, *args, **kwargs):
        original_init(context, *args, **kwargs)
        trace['execution_run_id'] = context.run_id
    def observe_failure(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except gfx_dependencies.DependencyError as error:
            failure_objects.append(error)
            trace['production_failures'].append(dict(reason=error.reason, resource=error.resource,
                origin=error.origin, type=type(error).__name__, native_error=events.safe_error(error)))
            raise
    def check(policy, *args, **kwargs):
        return observe_failure(original_check, policy, *args, **kwargs)
    def resolve(*args, **kwargs):
        return observe_failure(original_resolve, *args, **kwargs)
    pending = {}
    original_response = RequestPolicy.response
    original_send = gfx_render.WSConn.send_text
    original_recv = gfx_render.WSConn.recv_msg
    original_call = gfx_render.Tab.call
    original_tab_init = gfx_render.Tab.__init__
    owned_targets = {}
    prepared_targets = set()
    def tab_init(tab, port, ws_path, *args, **kwargs):
        original_tab_init(tab, port, ws_path, *args, **kwargs)
        prefix = "/devtools/page/"
        assert ws_path.startswith(prefix) and "/" not in ws_path[len(prefix):], ws_path
        owned_targets[tab] = ws_path[len(prefix):]
    def save():
        trace_path.write_text(json.dumps(trace, indent=2))
    def append(key, row):
        if len(trace[key]) >= 2048:
            raise RuntimeError('bounded trace capacity exceeded')
        trace[key].append(row)
    protocol = ProtocolObserver(append)
    def response(policy, params):
        method, args = original_response(policy, params)
        append('requests', dict(url=evidence_url(params['request']['url']), kind=params.get('resourceType'),
                               decision=method, request_id=params['requestId']))
        return method, args
    def send(ws, data):
        msg = json.loads(data)
        protocol.observe(ws, 'send', msg)
        if msg.get('method') in ('Fetch.fulfillRequest', 'Fetch.failRequest'):
            pending[(id(ws), msg['id'])] = dict(id=msg['id'], method=msg['method'],
                                               request_id=msg['params']['requestId'])
        return original_send(ws, data)
    def recv(ws):
        op, data = original_recv(ws)
        if op != 0x8:
            msg = json.loads(data)
            protocol.observe(ws, 'recv', msg)
            row = pending.pop((id(ws), msg.get('id')), None)
            if row is not None:
                append('acknowledgements', dict(row, error=msg.get('error')))
        return op, data
    def call(tab, method, **kwargs):
        if method == 'Fetch.enable':
            assert tab not in prepared_targets, 'font observation prepared twice'
            prepared = prepare_font_observation(tab, original_call, owned_targets.get(tab))
            append('font_observation_targets', prepared)
            prepared_targets.add(tab)
        if method == 'Page.captureScreenshot':
            trace['screenshot_attempts'] += 1
            result = original_call(tab, 'Runtime.evaluate', expression=METRICS, returnByValue=True)
            if result.get('exceptionDetails'):
                raise AssertionError(result)
            measurements = result.get('result', {}).get('value')
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
            trace['injected_target'] = target
            trace['injected_kind'] = 'Document'
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
        with patch.object(RequestPolicy, 'check', check), \
             patch.object(gfx_dependencies, 'resolve', resolve), \
             patch.object(events.ExecutionContext, '__init__', observe_init), \
             patch.object(RequestPolicy, 'response', response), \
             patch.object(gfx_render.WSConn, 'send_text', send), \
             patch.object(gfx_render.WSConn, 'recv_msg', recv), \
             patch.object(gfx_render.Tab, '__init__', tab_init), \
             patch.object(gfx_render.Tab, 'call', call), \
             patch.object(gfx_cli, 'render_cli', fallback), \
             patch.object(sys, 'argv', ['vtx', '-t', str(timeline), '--events-jsonl', str(trace_path.with_suffix('.events.jsonl')), stage]):
            code = cli.main()
            if code:
                raise SystemExit(code)
    except BaseException as error:
        trace['native_error'] = events.safe_error(error)
        trace['terminal_exception_type'] = type(error).__name__
        current = error
        seen = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if any(current is original for original in failure_objects):
                trace['propagated_production_failure'] = True
            current = current.__cause__ if current.__cause__ is not None else (
                None if current.__suppress_context__ else current.__context__)
        raise
    finally:
        trace['pending_acknowledgements'] = list(pending.values())
        save()


def check_trace(trace, expected_urls, *, rejected=False, target=None):
    assert trace['fallback_calls'] == 0, trace
    if rejected:
        assert target is not None and trace.get('injected') is True, trace
        assert trace.get('injected_target') == target and trace.get('injected_kind') == 'Document', trace
        assert trace.get('propagated_production_failure') is True, trace
        assert trace.get('terminal_exception_type') in ('DependencyError', 'SystemExit'), trace
        assert any(f['reason'] == 'strict runtime request rejected' for f in trace['production_failures']), trace
        assert trace['screenshot_attempts'] == 0 and trace['metrics'] == [], trace
        denied = [r for r in trace['requests'] if r['decision'] == 'Fetch.failRequest']
        assert denied and all(r['url'] == target and r['kind'] == 'Document' for r in denied), trace
        assert all(any(a['request_id'] == r['request_id'] and a.get('method') == 'Fetch.failRequest'
                       and a['error'] is None for a in trace['acknowledgements']) for r in denied), trace
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


def check_round_counts(trace, changed):
    assert trace['screenshot_attempts'] == len(trace['metrics']) == 2*len(changed), 'wrong per-round screenshot count'


def check_no_products(project, trace):
    assert trace['screenshot_attempts'] == 0 and trace['metrics'] == []
    assert trace['fallback_calls'] == 0
    assert not list(project.rglob('f[0-9]*.png')), 'forbidden partial screenshot exists'
    assert not list(project.rglob('complete.json')), 'forbidden completion marker exists'


def check_static_failure(trace, expected_reason):
    assert trace.get('propagated_production_failure') is True, trace
    assert trace['requests'] == [] and trace['fallback_calls'] == 0
    assert any(f['reason'] == expected_reason for f in trace['production_failures']), trace
    assert trace.get('terminal_exception_type') in ('DependencyError', 'SystemExit'), trace


def check_native_events(path, trace, *, timeline, stage, identity, failed=False,
                        receipt=None, runtime=False, gfx_attempts=None):
    """Required original event validator plus invocation/receipt/failure binding."""
    from vtx.events import read_events
    invocation = trace['invocation']
    assert invocation == dict(stage=stage, timeline=str(timeline),
        timeline_sha256=hashlib.sha256(timeline.read_bytes()).hexdigest(),
        head=identity['head'], tree=identity['tree']), invocation
    if receipt is not None:
        assert receipt['timeline'] == invocation['timeline_sha256'], 'receipt timeline mismatch'
    parsed = read_events(path.read_text())
    assert parsed['complete'], 'native events missing/truncated/incomplete'
    rows = parsed['events']
    assert rows and all(r['run_id'] == trace['execution_run_id'] for r in rows)
    terminal = rows[-1]
    assert terminal['event'] == 'run_terminal'
    assert terminal['outcome'] == ('failed' if failed else 'succeeded')
    tasks = [r for r in rows if r['event'] == 'task_terminal']
    attempts = [r for r in rows if r['event'] == 'attempt_terminal']
    if failed:
        assert trace.get('propagated_production_failure') is True
        assert terminal['error'] == trace['native_error'], 'failure/event cause mismatch'
        if runtime:
            causes = [f['native_error'] for f in trace['production_failures']]
            assert any(r['outcome'] == 'failed' and r['error'] in causes for r in attempts)
            assert any(r['outcome'] == 'failed' and r['error'] == trace['native_error'] for r in tasks)
        assert not any(r['event'] == 'receipt_commit' and r['outcome'] == 'succeeded' for r in rows)
        if receipt is not None:
            entry = receipt.get('stages', {}).get(stage, {})
            assert entry.get('ok') is not True, 'failed operation retains successful receipt'
            if entry.get('observed_by'):
                ref = entry['observed_by']
                assert any(r['event'] == 'receipt_commit' and r['outcome'] == 'failed'
                    and r['event_id'] == ref['event_id'] and r['task_id'] == ref['task_id']
                    and r['run_id'] == ref['run_id'] for r in rows)
    else:
        assert terminal['error'] is None
        assert tasks and all(r['outcome'] == 'succeeded' for r in tasks)
        assert all(r['outcome'] == 'succeeded' for r in attempts)
        if gfx_attempts is not None:
            assert len(attempts) == gfx_attempts, 'unexpected native gfx attempts'
        assert receipt is not None
        entry = receipt['stages'][stage]
        assert entry['ok'] is True
        ref = entry['observed_by']
        assert ref['run_id'] == trace['execution_run_id']
        commits = [r for r in rows if r['event'] == 'receipt_commit' and
                   r['stage'] == stage and r['outcome'] == 'succeeded' and
                   r['task_id'] == ref['task_id'] and r['event_id'] == ref['event_id']]
        assert len(commits) == 1, 'receipt not committed by this exact native invocation'
    return dict(run_id=trace['execution_run_id'], event_count=len(rows), stage=stage,
                outcome=terminal['outcome'], attempts=len(attempts))


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
    parser.add_argument('--stage', choices=('gfx','validate','video'), default='gfx')
    parser.add_argument('--inject', choices=('local', 'external'))
    args = parser.parse_args()
    if args.worker:
        return worker(args.worker.resolve(), args.trace, args.inject, args.stage)
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
        from tests.runners.impact_inventory import observe, delta, validate_sidecars, is_pass_sidecar
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
        for tool in ('ffmpeg', 'ffprobe'):
            run([tool, '-version'], tool + '-version', limit=10)
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
        required_native = []
        def native(stage, label, *, project_timeline=timeline, failed=False, injection=None,
                   gfx_attempts=None):
            trace_path = evidence/(label+'-trace.json')
            argv = [sys.executable,__file__,'--worker',project_timeline,'--stage',stage,'--trace',trace_path]
            if injection: argv += ['--inject',injection]
            run(argv,label,expect_failure=failed)
            trace = json.loads(trace_path.read_text())
            receipt = None
            source = project_timeline.parent/'out/receipt.json'
            if not failed or source.exists():
                receipt = json.loads(source.read_text())
                shutil.copyfile(source,evidence/(label+'-receipt.json'))
            event_path = trace_path.with_suffix('.events.jsonl')
            binding = check_native_events(event_path,trace,timeline=project_timeline,stage=stage,
                identity=report['identity'],failed=failed,receipt=receipt,runtime=bool(injection),
                gfx_attempts=gfx_attempts)
            names = dict(trace=trace_path.name,events=event_path.name,
                         receipt=None if receipt is None else label+'-receipt.json')
            required_native.append(dict(names,binding=binding,sha256={name:
                hashlib.sha256((evidence/name).read_bytes()).hexdigest()
                for name in names.values() if name is not None}))
            return trace
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
            native('validate',label+'-validate')
            trace = native('gfx',label+'-gfx',gfx_attempts=len(changed))
            check_round_counts(trace,changed)
            report['rendered_frames'] = report.get('rendered_frames', 0) + len(trace['metrics'])
            assert report['rendered_frames'] <= 24
            if changed:
                urls = {r['url'] for i in changed for r in records[i].get('resource_requests', [])}
                check_trace(trace, urls)
            else:
                assert not trace['requests'] and not trace['metrics'] and trace['fallback_calls'] == 0
            native('video',label+'-video')
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
        before_gc = observe(project)
        (evidence/'gc-before.json').write_text(json.dumps(before_gc,indent=2))
        (evidence/'gc-plan.json').write_text(json.dumps(planned,indent=2))
        run([sys.executable,'-m','vtx','-t',timeline,'gc'],'gc')
        after_gc = observe(project)
        (evidence/'gc-after.json').write_text(json.dumps(after_gc,indent=2))
        assert all(not (project/p).exists() for p in planned)
        c=Ctx(timeline)
        assert all((gfx_dir(c,cl)/'f0000.png').is_file() for cl in tl['video_tracks'][0]['clips'])
        current_keys = [node['artifact'] for node in before['nodes'].values()
                        if node['artifact'] and node['artifact'].startswith(
                            ('cache/gfx/', 'cache/seg/', 'cache/audio/'))]
        gc_checks = check_gc_contract(before_gc, after_gc, planned, current_keys)
        report['gc'] = dict(planned=planned, remaining=after_gc['products'], **gc_checks)
        save()
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
            tr=native('gfx',case,project_timeline=nt,failed=True)
            reason = ('self-closing non-void HTML unsupported' if case.startswith('unknown') else
                      'undeclared dependency' if case == 'undeclared' else 'nonlocal or ambiguous URL')
            check_static_failure(tr,reason)
            check_no_products(neg,tr)
            assert original==(neg/'a.html').read_bytes()
        for case in ('local','external'):
            neg=root/('runtime-'+case);negative=fixture(neg,font,provenance)
            negative['video_tracks'][0]['clips'] = negative['video_tracks'][0]['clips'][:1]
            negative['project']['duration_frames'] = 2
            (neg/'undeclared.html').write_text('<p>Forbidden</p>')
            nt=neg/'timeline.json';nt.write_text(json.dumps(negative))
            trace=evidence/('runtime-'+case+'-trace.json')
            tr=native('gfx','runtime-'+case,project_timeline=nt,failed=True,injection=case)
            target=(neg/'undeclared.html').as_uri() if case == 'local' else 'https://dependency-control.invalid/forbidden'
            check_trace(tr,set(),rejected=True,target=target)
            check_no_products(neg,tr)
        assert report['rendered_frames'] == 10
        assert len(required_native) == 21  # 4 rounds × 3 stages + 7 static + 2 runtime
        for item in required_native:
            for key in ('trace','events','receipt'):
                if item[key] is not None:
                    path = evidence/item[key]
                    assert path.is_file() and path.stat().st_size > 0
                    assert hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256'][item[key]], 'validated evidence changed'
        report['required_native_evidence'] = required_native
        report['passed']=True
    except Exception as error:
        report['error']=f'{type(error).__name__}: {error}'
        raise
    finally:
        save()


if __name__ == '__main__':
    main()
