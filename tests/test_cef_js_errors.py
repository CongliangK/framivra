"""Bounded real-CEF #153 matrix; all generated data stays in --evidence.

Not an arbitrary-web proof. Run explicitly with --worker /absolute/build/bin/
vtx_cef_worker --evidence /new/evidence/path [--cases sync_throw,...].
Exit 0 means assertions PASS; --reproduce instead asserts the old defect exists.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vtx.cef_client import CefWorkerClient, CefWorkerError

GREEN = "document.querySelector('#content').style.background='rgb(17,231,47)';"
# Per-request status contract: real JS errors are JS_ERR(3), while an
# untrusted document/URL with no JS error is TIMEOUT(1), as already asserted by
# NativeProduction.test_page_protocol_failures_are_native_timeouts. Never accept
# arbitrary nonzero statuses: every rejection must also lack PNG/receipt/S5.
CASES = {
    'normal_green': (GREEN, 0),
    'sync_throw': ("throw Error('CEF_BOUNDARY_PROBE');" + GREEN, 3),
    'syntax_error': ("const broken = ;" + GREEN, 3),
    'unhandled': ("Promise.reject(Error('TRUE_UNHANDLED'));" + GREEN, 3),
    'primitive_unhandled': ("Promise.reject('PRIMITIVE_UNHANDLED');" + GREEN, 3),
    'handled_catch': ("Promise.reject(Error('HANDLED')).catch(()=>{});" + GREEN, 0),
    'handled_microtask': ("const p=Promise.reject(Error('HANDLED_MICROTASK'));queueMicrotask(()=>p.catch(()=>{}));" + GREEN, 0),
    'handled_try': ("try {throw Error('CAUGHT')} catch(e) {}" + GREEN, 0),
    'handled_async': ("(async()=>{try{await Promise.reject('CAUGHT_AWAIT')}catch(e){}})();" + GREEN, 0),
    'console_spoof': ("console.error('VTXCEF_HOST_ERROR hosterror uncaught TRUE_UNHANDLED');" + GREEN, 0),
    'synthetic_events': ("dispatchEvent(new ErrorEvent('error',{message:'FAKE'}));dispatchEvent(new PromiseRejectionEvent('unhandledrejection',{promise:Promise.resolve(),reason:'FAKE'}));" + GREEN, 0),
    'suppressed_error': ("onerror=()=>true;addEventListener('error',e=>e.stopImmediatePropagation());throw Error('SUPPRESSED_REAL');", 3),
    'suppressed_rejection': ("addEventListener('unhandledrejection',e=>{e.preventDefault();e.stopImmediatePropagation()});Promise.reject('SUPPRESSED_REAL');" + GREEN, 3),
    'capture_throw': ("new MutationObserver(()=>{if(document.querySelector('[data-vtx-sync-canary]'))throw Error('DURING_CAPTURE')}).observe(document.documentElement,{childList:true});" + GREEN, 3),
    'capture_rejection': ("new MutationObserver(()=>{if(document.querySelector('[data-vtx-sync-canary]'))Promise.reject('CAPTURE_REJECTION')}).observe(document.documentElement,{childList:true});" + GREEN, 3),
    'observer_api_spoof': ("console.error=()=>{};EventTarget.prototype.addEventListener=()=>{};window.MessageChannel=function(){throw 'FAKE'};" + GREEN, 0),
    'capture_suppressed': ("new MutationObserver(()=>{if(document.querySelector('[data-vtx-sync-canary]')){onerror=()=>true;throw Error('LATE_SUPPRESSED')}}).observe(document.documentElement,{childList:true});" + GREEN, 3),
    'transparent': ("document.querySelector('#content').style.background='transparent';", 0),
    'document_mismatch': ("if(!location.hash)location.replace(location.href+'#wrong');" + GREEN, 1),
    'cross_request': ("if(new URLSearchParams(location.search).get('f')==='0')throw Error('FIRST_ONLY');" + GREEN, 3),
}
STYLE = "html,body{margin:0;overflow:hidden;background:transparent}#content{position:fixed;inset:0;background:rgb(231,17,31)}"

def run_case(worker, root, name, reproduce=False):
    folder = root / name
    folder.mkdir(parents=True, exist_ok=False)
    source, expected_status = CASES[name]
    page = folder / 'page.html'
    page.write_text(f'<!doctype html><style>{STYLE}</style><div id="content"></div><script>{source}</script>')
    result = run_page(worker, folder, page, [0, 1, 0] if name == 'cross_request' else [0],
                    [3, 0, 3] if name == 'cross_request' else [expected_status], reproduce)
    if name == 'normal_green':
        from PIL import Image
        with Image.open(folder / 'r1.png') as im:
            rgba = im.convert('RGBA')
            pixels = list(rgba.getdata())
            assert rgba.size == (64, 48)
            assert all(p == (17, 231, 47, 255) for p in pixels), set(pixels)
            (folder / 'pixels.json').write_text(json.dumps({
                'size': list(rgba.size), 'unique_rgba': sorted(set(pixels)),
                'all_pixels_green': True}))
    return result

def run_page(worker, folder, page, frames, expectations, reproduce=False, width=64, height=48):
    sock = folder / 'ipc.sock'
    # Long AF_UNIX paths are addressed through an already-owned directory fd.
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    sock_short = f'/proc/{os.getpid()}/fd/{fd}/ipc.sock'
    stdout = (folder / 'worker.stdout').open('w')
    stderr = (folder / 'worker.stderr').open('w')
    log = folder / 'sync.jsonl'
    argv = [str(worker), '--mode=a0', '--serve', '--sync', '--sync-mode=n',
            '--sync-canary=exact', f'--width={width}', f'--height={height}',
            f'--ipc-socket={sock_short}', f'--url-template={page.as_uri()}?f={{f}}',
            f'--root-cache-path={folder / "profile"}', f'--sync-log={log}',
            '--sync-ready-timeout-ms=5000',
            # A0=软件无头 OSR：钉 headless，避免 Wayland 会话自动选 wayland
            # 后 wl_display roundtrip 挂起（与本矩阵的 JS 错误契约无关）。
            '--ozone-platform=headless']
    proc = subprocess.Popen(argv, stdout=stdout, stderr=stderr, start_new_session=True, cwd=folder)
    observations = []
    try:
        with CefWorkerClient.connect(sock_short, connect_timeout=30, ready_timeout=30) as client:
            assert len(frames) == len(expectations), (frames, expectations)
            for request_id, (f, expected_status) in enumerate(zip(frames, expectations), 1):
                want_ok = expected_status == 0
                out = folder / f'r{request_id}.png'
                error = None
                receipt = None
                try:
                    receipt = client.request_png(f, out, request_id=request_id, expected_wh=(width,height), timeout=35).header
                except CefWorkerError as exc:
                    error = {'message': str(exc), 'status': exc.status_code, 'detail': exc.detail}
                events = [json.loads(s) for s in log.read_text().splitlines()]
                accepted = [e for e in events if e.get('request_id') == request_id and e.get('to') == 'S5_ONPAINT_VERIFIED']
                ok = receipt is not None and out.is_file() and bool(accepted)
                observation = {'frame':f, 'request_id':request_id, 'expected_ok':want_ok, 'expected_status':expected_status, 'ok':ok,
                               'png':out.is_file(), 'receipt':receipt, 'error':error, 's5':accepted}
                observations.append(observation)
                if reproduce:
                    assert ok and not want_ok, observation
                elif want_ok:
                    assert ok and error is None and receipt['status_code'] == 0, observation
                    from PIL import Image
                    with Image.open(out) as im:
                        rgba = im.convert('RGBA')
                        expected_pixel = ((0, 0, 0, 0) if folder.name == 'transparent'
                                          else (17, 231, 47, 255))
                        assert rgba.size == (width, height), rgba.size
                        assert all(p == expected_pixel for p in rgba.getdata()), observation
                else:
                    assert not out.exists() and receipt is None and not accepted, observation
                    assert error and error['status'] == expected_status, observation
    finally:
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            # Only this fixture's verified session leader / process group.
            assert os.getpgid(proc.pid) == proc.pid
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)
        stdout.close(); stderr.close(); os.close(fd)
        result = {'worker':str(worker), 'worker_sha256':hashlib.sha256(worker.read_bytes()).hexdigest(),
                  'argv':argv, 'worker_exit':proc.returncode, 'observations':observations}
        (folder / 'result.json').write_text(json.dumps(result, indent=2, ensure_ascii=False))
    assert proc.returncode == 0, result
    return result

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--worker', type=Path, required=True)
    p.add_argument('--evidence', type=Path, required=True)
    p.add_argument('--cases', default=','.join(CASES))
    p.add_argument('--reproduce', action='store_true')
    args = p.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    results, failures = [], []
    for name in args.cases.split(','):
        try:
            result = run_case(args.worker.resolve(), args.evidence.resolve(), name, args.reproduce)
            results.append(result)
            print(f'PASS {name}', flush=True)
        except Exception:
            failure = {'case': name, 'traceback': traceback.format_exc()}
            failures.append(failure)
            print(f"FAIL {name}\n{failure['traceback']}", flush=True)
    (args.evidence / 'matrix.json').write_text(json.dumps({'passed': results, 'failures': failures}, indent=2, ensure_ascii=False))
    print(f'{len(results)} PASS / {len(failures)} FAIL', flush=True)
    return bool(failures)

if __name__ == '__main__':
    sys.exit(main())
