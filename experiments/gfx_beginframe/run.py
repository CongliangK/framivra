#!/usr/bin/env python3
"""Isolated BeginFrame research runner; never selected by production routing.

No completed #62 receipt is emitted: this low-load probe is not an acceptance run.
Each request has a run/generation/request/frame tuple. CDP call IDs bind responses;
page acknowledgement binds JS state; BeginFrame response supplies the screenshot.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from vtx.gfx_render import Tab, WSConn, png_decode_rgba
from vtx.gfx_cli import png_invalid_reason
from vtx.frame_canary import signature_rgb, signature2_rgb, verify_diagnostic_corners

VERSION = '149.0.7827.55'
BINARY_SHA256 = '670ba079b75107746ba41abad131180a31a7c7219aa1bd4061fb471f4535d541'
FLAGS = ['--no-sandbox', '--enable-begin-frame-control',
         '--run-all-compositor-stages-before-draw', '--force-color-profile=srgb',
         '--remote-debugging-port=0']


def atomic_json(path, data):
    atomic_bytes(path, (json.dumps(data, indent=2, allow_nan=False)+'\n').encode())


def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+'.tmp')
    temp.write_bytes(data)
    os.replace(temp, path)


def browser_session(port, path):
    obj = Tab.__new__(Tab)
    obj.ws = WSConn(port, path)
    obj._mid = 0
    obj.request_policy = None
    obj._policy_commands = set()
    return obj


class Shell:
    def __init__(self, binary, output, width, height, navigation=False):
        self.output, self.width, self.height = Path(output), width, height
        self.navigation = navigation
        flags = [f for f in FLAGS if not (navigation and f in ('--enable-begin-frame-control', '--run-all-compositor-stages-before-draw'))]
        self.output.mkdir(parents=True, exist_ok=True)
        binary = Path(binary).resolve(strict=True)
        sha = hashlib.sha256(binary.read_bytes()).hexdigest()
        version = subprocess.check_output([str(binary), '--version'], text=True).strip()
        if sha != BINARY_SHA256 or VERSION not in version:
            raise ValueError('binary differs from fixed lock; review a new lock before running')
        self.temp = tempfile.TemporaryDirectory(prefix='vtx-beginframe-')
        self.log = open(self.output/'chrome.stderr', 'wb')
        self.process = subprocess.Popen([str(binary), *flags,
            '--user-data-dir='+self.temp.name, 'about:blank'], stdout=subprocess.DEVNULL,
            stderr=self.log)
        self.tabs = []
        try:
            active = Path(self.temp.name)/'DevToolsActivePort'
            deadline = time.monotonic()+15
            while not active.exists():
                if self.process.poll() is not None:
                    raise RuntimeError('shell exited; inspect chrome.stderr')
                if time.monotonic() > deadline:
                    raise TimeoutError('DevToolsActivePort unavailable')
                time.sleep(.02)  # startup polling only, never a render/commit barrier
            port, path = active.read_text().splitlines()
            self.port = int(port)
            self.browser = browser_session(self.port, path)
            protocol = self.get('/json/protocol', raw=True)
            atomic_bytes(self.output/'protocol.json', protocol)
            self.metadata = {'binary':str(binary), 'binary_sha256':sha, 'version':version,
                'flags':flags, 'protocol_sha256':hashlib.sha256(protocol).hexdigest(),
                'browser_version':self.browser.call('Browser.getVersion'),
                'system_info':self.browser.call('SystemInfo.getInfo'),
                'acceptance_status':None, 'performance_status':None}
        except BaseException:
            self.close()
            raise

    def get(self, route, raw=False):
        with urllib.request.urlopen(f'http://127.0.0.1:{self.port}'+route, timeout=10) as r:
            data = r.read()
        return data if raw else json.loads(data)

    def target(self):
        result = self.browser.call('Target.createTarget', url='about:blank',
                                   enableBeginFrameControl=not self.navigation)
        target_id = result['targetId']
        ws = next(x['webSocketDebuggerUrl'] for x in self.get('/json/list')
                  if x['id'] == target_id)
        tab = Tab(self.port, urllib.parse.urlsplit(ws).path, self.width, self.height)
        tab.call('Emulation.setDefaultBackgroundColorOverride',
                 color={'r':0,'g':0,'b':0,'a':0})
        self.tabs.append(tab)
        return tab, target_id

    def close(self):
        for tab in self.tabs:
            tab.close()
        if hasattr(self, 'browser'):
            self.browser.close()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.log.close()
        self.temp.cleanup()


def evaluate(tab, expression):
    result = tab.call('Runtime.evaluate', expression=expression, awaitPromise=True,
                      returnByValue=True)
    if result.get('exceptionDetails'):
        raise RuntimeError('page evaluation failed: '+json.dumps(result['exceptionDetails']))
    value = result.get('result', {})
    if value.get('subtype') == 'error':
        raise RuntimeError('page returned error: '+str(value))
    return value.get('value')


def checkpoint(tab, token, *, invoke):
    # Microtask checkpoint + font promises + image decode are distinct from submit.
    # Layout read forces style/layout, not compositor completion. Only BeginFrame
    # response is used as submit/capture evidence. No rAF/sleep claim is made.
    text = json.dumps(token)
    return evaluate(tab, '''(async()=>{
      const token=TOKEN;
      REQUIRE_RENDER
      INVOKE
      await Promise.resolve();
      await document.fonts.ready;
      await Promise.all(Array.from(document.images).map(async i=>{
        if(!i.complete) await new Promise((r,j)=>{i.addEventListener('load',r,{once:true});i.addEventListener('error',()=>j(Error('image load failed')),{once:true})});
        if(!i.naturalWidth) throw Error('image decode failed');
        await i.decode();
      }));
      const rect=document.documentElement.getBoundingClientRect();
      window.__beginframe_ack=token;
      return {token,fonts:document.fonts.status,images:document.images.length,
              layout:[rect.width,rect.height]};
    })()'''.replace('TOKEN', text).replace('REQUIRE_RENDER',
      "if(typeof window.__vtx_render !== 'function') throw Error('pure-function __vtx_render required');" if invoke else '').replace('INVOKE',
      'await window.__vtx_render(token.frame);' if invoke else ''))


def render(shell, page, frames, output, *, navigation=False, diagnostic=False, run_id=None):
    tab, target = shell.target()
    run = run_id or uuid.uuid4().hex
    generation = 0
    records = []
    for index, frame in enumerate(frames):
        started = time.monotonic()
        if navigation or index == 0:
            generation += 1
            url = Path(page).resolve().as_uri()+f'?f={frame}'
            nav = tab.call('Page.navigate', url=url)
            if nav.get('errorText'):
                raise RuntimeError(nav['errorText'])
            # CDP context readiness predicate; no wall-clock settling assumption.
            deadline = time.monotonic()+15
            while not evaluate(tab, 'location.href === '+json.dumps(url)+' && document.readyState === "complete"'):
                if time.monotonic() > deadline:
                    raise TimeoutError('document readiness')
        token = {'run':run,'generation':generation,'request':index,'frame':frame,'target':target}
        ready = checkpoint(tab, token, invoke=not navigation)
        if diagnostic:
            colors = [signature_rgb('diagnostic_8x8', frame),
                      signature2_rgb('diagnostic_8x8', frame)]
            evaluate(tab, '''(()=>{const colors=COLORS;
              ['tl','br'].forEach((id,i)=>{let e=document.getElementById('__bf_'+id);
                if(!e){e=document.createElement('div');e.id='__bf_'+id;document.body.appendChild(e)}
                e.style.cssText='position:fixed;width:8px;height:8px;z-index:2147483647;'+
                  (i?'bottom:0;right:0;':'top:0;left:0;')+'background:rgb('+colors[i].join(',')+');';
              });return true;})()'''.replace('COLORS', json.dumps(colors)))
        if ready['token'] != token:
            raise RuntimeError('wrong page acknowledgement')
        if navigation:
            response = tab.call('Page.captureScreenshot', format='png',
                                captureBeyondViewport=False)
            encoded = response.get('data')
            damage = None
        else:
            response = tab.call('HeadlessExperimental.beginFrame',
                frameTimeTicks=100000+index*(1000/30), interval=1000/30,
                screenshot={'format':'png'})
            encoded = response.get('screenshotData')
            damage = response.get('hasDamage')
        if not encoded:
            raise RuntimeError('capture missing; never reuse preceding screenshot')
        if evaluate(tab, 'window.__beginframe_ack') != token:
            raise RuntimeError('stale generation/request acknowledgement')
        png = base64.b64decode(encoded, validate=True)
        path = Path(output)/f'r{index:05d}_f{frame:04d}.png'
        atomic_bytes(path, png)
        reason = png_invalid_reason(path, width=shell.width, height=shell.height)
        if reason:
            path.unlink()
            raise RuntimeError(reason)
        rgba = png_decode_rgba(png)
        canary = verify_diagnostic_corners(rgba, frame) if diagnostic else None
        if canary is not None and canary['status'] != 'ok':
            path.unlink()
            raise RuntimeError('wrong captured frame: '+str(canary))
        records.append({'canary':canary,'token':token,'target':target,'path':str(path),
            'rgba_sha256':hashlib.sha256(rgba.tobytes()).hexdigest(),
            'hasDamage':damage,'readiness':ready,
            'diagnostic_wall_seconds':time.monotonic()-started})
    return records


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary', default=os.environ.get('VTX_TEST_HEADLESS_SHELL'))
    p.add_argument('--page', type=Path, required=True)
    p.add_argument('--frames', default='0,1,22,1,0,22')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--width', type=int, default=1920)
    p.add_argument('--height', type=int, default=1080)
    p.add_argument('--navigation', action='store_true')
    p.add_argument('--diagnostic', action='store_true')
    p.add_argument('--pure-function-declared', action='store_true',
                   help='caller declares page render(f) pure and resource-complete')
    args = p.parse_args()
    if not args.binary:
        p.error('explicit --binary or VTX_TEST_HEADLESS_SHELL required; no skip/pass')
    if not args.navigation and not args.pure_function_declared:
        p.error('--pure-function-declared is required for persistent rendering')
    frames = [int(x) for x in args.frames.split(',')]
    if not frames or min(frames) < 0:
        p.error('frames must be nonnegative integers')
    shell = None
    report = {'status':'failed','completed_receipt':None}
    try:
        shell = Shell(args.binary, args.output, args.width, args.height, args.navigation)
        report['environment'] = shell.metadata
        report['frames'] = render(shell,args.page,frames,args.output/'frames',
                                  navigation=args.navigation, diagnostic=args.diagnostic)
        report['status'] = 'probe_complete'
    except Exception as error:
        report['error'] = repr(error)
        raise
    finally:
        if shell:
            shell.close()
        atomic_json(args.output/'probe.json',report)

if __name__ == '__main__':
    main()
