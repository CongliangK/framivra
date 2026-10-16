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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vtx.cef_client import CefWorkerClient, CefWorkerError

GREEN = "document.querySelector('#content').style.background='rgb(17,231,47)';"
CASES = {
    'sync_throw': ("throw Error('CEF_BOUNDARY_PROBE');" + GREEN, False),
    'syntax_error': ("const broken = ;" + GREEN, False),
    'unhandled': ("Promise.reject(Error('TRUE_UNHANDLED'));" + GREEN, False),
    'primitive_unhandled': ("Promise.reject('PRIMITIVE_UNHANDLED');" + GREEN, False),
    'handled_catch': ("Promise.reject(Error('HANDLED')).catch(()=>{});" + GREEN, True),
    'handled_microtask': ("const p=Promise.reject(Error('HANDLED_MICROTASK'));queueMicrotask(()=>p.catch(()=>{}));" + GREEN, True),
    'handled_try': ("try {throw Error('CAUGHT')} catch(e) {}" + GREEN, True),
    'handled_async': ("(async()=>{try{await Promise.reject('CAUGHT_AWAIT')}catch(e){}})();" + GREEN, True),
    'console_spoof': ("console.error('VTXCEF_HOST_ERROR hosterror uncaught TRUE_UNHANDLED');" + GREEN, True),
    'synthetic_events': ("dispatchEvent(new ErrorEvent('error',{message:'FAKE'}));dispatchEvent(new PromiseRejectionEvent('unhandledrejection',{promise:Promise.resolve(),reason:'FAKE'}));" + GREEN, True),
    'suppressed_error': ("onerror=()=>true;addEventListener('error',e=>e.stopImmediatePropagation());throw Error('SUPPRESSED_REAL');", False),
    'suppressed_rejection': ("addEventListener('unhandledrejection',e=>{e.preventDefault();e.stopImmediatePropagation()});Promise.reject('SUPPRESSED_REAL');" + GREEN, False),
    'capture_throw': ("new MutationObserver(()=>{if(document.querySelector('[data-vtx-sync-canary]'))throw Error('DURING_CAPTURE')}).observe(document.documentElement,{childList:true});" + GREEN, False),
    'capture_rejection': ("new MutationObserver(()=>{if(document.querySelector('[data-vtx-sync-canary]'))Promise.reject('CAPTURE_REJECTION')}).observe(document.documentElement,{childList:true});" + GREEN, False),
    'transparent': ("document.querySelector('#content').style.background='transparent';", True),
    'document_mismatch': ("if(!location.hash)location.replace(location.href+'#wrong');" + GREEN, False),
    'cross_request': ("if(new URLSearchParams(location.search).get('f')==='0')throw Error('FIRST_ONLY');" + GREEN, False),
}
STYLE = "html,body{margin:0;overflow:hidden;background:transparent}#content{position:fixed;inset:0;background:rgb(231,17,31)}"

def run_case(worker, root, name, reproduce=False):
    folder = root / name
    folder.mkdir(parents=True, exist_ok=False)
    source, want_ok = CASES[name]
    page = folder / 'page.html'
    page.write_text(f'<!doctype html><style>{STYLE}</style><div id="content"></div><script>{source}</script>')
    return run_page(worker, folder, page, [0, 1, 0] if name == 'cross_request' else [0],
                    [False, True, False] if name == 'cross_request' else [want_ok], reproduce)

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
            '--sync-ready-timeout-ms=5000']
    proc = subprocess.Popen(argv, stdout=stdout, stderr=stderr, start_new_session=True, cwd=folder)
    observations = []
    try:
        with CefWorkerClient.connect(sock_short, connect_timeout=30, ready_timeout=30) as client:
            for request_id, (f, want_ok) in enumerate(zip(frames, expectations), 1):
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
                observation = {'frame':f, 'request_id':request_id, 'expected_ok':want_ok, 'ok':ok,
                               'png':out.is_file(), 'receipt':receipt, 'error':error, 's5':accepted}
                observations.append(observation)
                if reproduce:
                    assert ok and not want_ok, observation
                elif want_ok:
                    assert ok, observation
                else:
                    assert not out.exists() and receipt is None and not accepted, observation
                    assert error and error['status'] == 3, observation
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
    results = []
    for name in args.cases.split(','):
        result = run_case(args.worker.resolve(), args.evidence.resolve(), name, args.reproduce)
        results.append(result)
        print(f'PASS {name}', flush=True)
    (args.evidence / 'matrix.json').write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f'ALL PASS ({len(results)} cases)', flush=True)

if __name__ == '__main__':
    main()
