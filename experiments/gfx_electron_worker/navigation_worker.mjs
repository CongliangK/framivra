// Production candidate, Mode N only: real ?f= navigation, no page opt-in APIs.
// Two fresh isolated documents at most; only one capture/decoder generation.
import { app, BrowserWindow, nativeImage, session } from 'electron';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { performance } from 'node:perf_hooks';
import { pathToFileURL } from 'node:url';
import { NativePngDecoder, markerMatches, markerRanges, mergeWitnesses } from './pixel_codec.mjs';
import { installPageErrorGate, PAGE_ERROR_GATE } from './page_error_gate.mjs';
function samePhase(a,b,phase,width,height) {
  let pos=0;
  for (const [start,end] of [...markerRanges(width,height,phase),[a.length,a.length]]) {
    if (!a.subarray(pos,start).equals(b.subarray(pos,start))) return false;
    pos=end;
  }
  return true;
}

const args = Object.fromEntries(process.argv.slice(2).map(a => {
  const m = /^--([a-z-]+)=(.*)$/.exec(a);
  if (!m) throw Error(`invalid argument ${a}`);
  return [m[1], m[2]];
}));
for (const k of Object.keys(args)) if (!['job','profile','parent-alive-fd'].includes(k)) throw Error(`unknown argument ${k}`);
const jobPath = path.resolve(args.job);
if (fs.statSync(jobPath).size > 2_000_000) throw Error('job too large');
const job = JSON.parse(fs.readFileSync(jobPath, 'utf8'));
const { width, height, frames, timeout_ms: timeoutMs, run_id: runId } = job;
const scheduling={max_inflight:2,max_live_documents:2,max_capture_inflight:1,prefetch_limit:1,decoder_threads:1,max_pending_decodes:6};
if (!job.scheduling || Object.keys(job.scheduling).length!==Object.keys(scheduling).length || Object.entries(scheduling).some(([k,v]) => job.scheduling[k]!==v)) throw Error('invalid scheduling');
if (!Number.isInteger(width) || !Number.isInteger(height) || width < 64 || height < 2 || width > 3840 || height > 2160 || width*height > 8294400) throw Error('invalid viewport');
if (!Array.isArray(frames) || frames.length > 20000 || new Set(frames).size !== frames.length || frames.some(f => !Number.isInteger(f) || f < 0 || f > 2147483647)) throw Error('invalid frames');
if (!Number.isInteger(timeoutMs) || timeoutMs < 500 || timeoutMs > 120000) throw Error('invalid timeout');
if (!/^[a-f0-9]{32}$/.test(runId) || !['b0','b1'].includes(job.mode) || job.output !== 'electron_native_topng') throw Error('invalid run/mode/output');
if (!path.isAbsolute(job.page) || !fs.statSync(job.page).isFile() || !path.isAbsolute(job.out_dir) || !path.isAbsolute(job.result)) throw Error('absolute file paths required');
// Destroying a per-frame window must not trigger Electron's default app quit.
app.on('window-all-closed', () => {});
app.setPath('userData', path.resolve(args.profile));
app.commandLine.appendSwitch('force-color-profile', 'srgb');
app.commandLine.appendSwitch('force-device-scale-factor', '1');
app.commandLine.appendSwitch('hide-scrollbars');
if (job.mode === 'b0') app.disableHardwareAcceleration();
// Supervisor owns the group; only a verified session leader may self-destruct.
if (args['parent-alive-fd']) {
  const fd = Number(args['parent-alive-fd']);
  const parent = fs.createReadStream(null, {fd, autoClose:false});
  const die = () => { if (process.platform === 'linux') {
    const stat = fs.readFileSync('/proc/self/stat','utf8').split(') ').at(-1).split(' ');
    if (Number(stat[2]) === process.pid && Number(stat[3]) === process.pid) process.kill(-process.pid, 'SIGKILL');
  } app.exit(71); };
  parent.on('end', die).on('error', die);
}
const delay = ms => new Promise(r => setTimeout(r, ms));
async function deadline(promise, ms, label) {
  let timer;
  try { return await Promise.race([promise, new Promise((_,reject) => {
    timer = setTimeout(() => reject(Error(`timeout:${label}`)), ms);
  })]); } finally { clearTimeout(timer); }
}
function atomicWrite(target, data) {
  const tmp = path.join(path.dirname(target), `.${path.basename(target)}.${runId}.tmp`);
  if (!path.isAbsolute(target) || path.dirname(tmp) !== path.dirname(target)) throw Error('unsafe output');
  let fd;
  try {
    fd = fs.openSync(tmp, 'wx', 0o600);
    fs.writeFileSync(fd, data); fs.fsyncSync(fd); fs.closeSync(fd); fd = undefined;
    fs.renameSync(tmp, target);
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
    if (fs.existsSync(tmp)) fs.unlinkSync(tmp);
  }
}
// No Node, IPC, contextBridge, page originals, or __vtx_render in the page world.
// Isolated-world host code owns the closed shadow overlay. Page CSS interference
// cannot forge nonce pixels; at worst it causes a bounded marker timeout.
const WORLD = 997;
function isolated(wc, code) {
  return wc.executeJavaScriptInIsolatedWorld(WORLD, [{code}], false);
}
function checkDocumentCode(url, f) { return `(() => {
  if (location.href !== ${JSON.stringify(url)} || new URL(location.href).searchParams.get('f') !== ${JSON.stringify(String(f))}) throw Error('wrong_document');
  if (devicePixelRatio !== 1 || innerWidth !== ${width} || innerHeight !== ${height}) throw Error('viewport_mismatch');
})()`; }
function createFrameContext(f, generation, pageErrors, decoder, result) {
  // Start before session/window creation; promotion NEVER resets this budget.
  const start = performance.now();
  const remaining = () => Math.max(1, timeoutMs-(performance.now()-start));
  const ses = createSession(`vtx-${runId}-${generation}`);
  result.sessions_created++;
  let closed = false, capturing = false;
  const watchdog = setTimeout(() => {
    if (!result.failed.includes(f)) result.failed.push(f);
    result.errors[f]='timeout:frame'; result.timed_out=true;
    atomicWrite(job.result,JSON.stringify(result,null,2));
    // Preserve the existing whole-batch timeout exit contract, including a
    // timeout in the ahead document while current capture is still running.
    app.exit(72);
  }, remaining());
  const url = pathToFileURL(job.page); url.searchParams.set('f', String(f));
  const expectedUrl = url.href;
  const win = new BrowserWindow({width, height, show:false, frame:false, transparent:true,
    useContentSize:true, webPreferences:{session:ses, offscreen:{useSharedTexture:false, deviceScaleFactor:1},
      backgroundThrottling:false, contextIsolation:true, nodeIntegration:false, sandbox:true, spellcheck:false}});
  result.live_documents++;
  result.peak_live_documents=Math.max(result.peak_live_documents,result.live_documents);
  if (result.live_documents>2) throw Error('live_document_limit');
  const wc = win.webContents;
  wc.setFrameRate(60);
  wc.setWindowOpenHandler(() => ({action:'deny'}));
  let fatal = null, paints = 0, rejected = 0, active = null, unmarkedPaint = null;
  const fail = reason => { fatal ||= Error(reason); active?.reject(fatal); };
  let errorGate;
  async function checkPageErrors() {
    if (fatal) throw fatal;
    // Yield the renderer task/microtask checkpoint, then drain the gate's CDP
    // notifications (including its trusted HTML rejection observer).
    await isolated(wc, `${checkDocumentCode(expectedUrl,f)};
      new Promise(resolve => setTimeout(resolve, 0))`);
    await errorGate.fence();
    if (fatal) throw fatal;
  }
  wc.on('render-process-gone', (_e,d) => fail(`renderer_gone:${d.reason}`));
  wc.on('will-navigate', e => { e.preventDefault(); fail('unexpected_navigation'); });
  wc.on('will-redirect', e => { e.preventDefault(); fail('unexpected_redirect'); });
  wc.on('paint', (_event, _dirty, image) => {
    paints++;
    unmarkedPaint?.(); unmarkedPaint=null;
    if (!active) return;
    try {
      const size = image.getSize();
      if (size.width !== width || size.height !== height) throw Error('paint_geometry');
      const bitmap = image.toBitmap();
      if (bitmap.length !== width*height*4) throw Error('bitmap_length');
      if (!markerMatches(bitmap, width, height, active.phase, active.token)) { rejected++; return; }
      const png = image.toPNG();
      const ready = active; active = null;
      ready.resolve({bitmap, rgba:decoder.decode(png,ready.operation)});
    } catch (e) { active?.reject(e); active = null; }
  });
  async function witness(phase, operation) {
    if (fatal) throw fatal;
    const token = crypto.randomBytes(48);
    let resolve, reject;
    const captured = new Promise((r,j) => {resolve=r; reject=j;});
    // Attach rejection handler before asynchronous installation to avoid an
    // unhandled rejection if the renderer dies during executeJavaScript.
    captured.catch(() => {});
    active = {phase, token, operation, resolve, reject};
    let pulse;
    try {
      await isolated(wc, `${checkDocumentCode(expectedUrl,f)};
        (() => {
          let state = globalThis.__vtxHostMarker;
          if (!state) {
            const host = document.createElement('div');
            const css = {all:'initial',position:'fixed',left:'0px',top:'0px',width:'${width}px',height:'${height}px',display:'block',visibility:'visible',opacity:'1',transform:'none',filter:'none','mix-blend-mode':'normal','z-index':'2147483647','pointer-events':'none',contain:'strict'};
            for (const [k,v] of Object.entries(css)) host.style.setProperty(k,v,'important');
            const root = host.attachShadow({mode:'closed'});
            const canvas = document.createElement('canvas'); canvas.width=${width}; canvas.height=${height};
            canvas.style.cssText='all:initial;display:block;width:${width}px;height:${height}px';
            root.append(canvas); document.documentElement.append(host);
            state = {host,canvas}; globalThis.__vtxHostMarker=state;
          }
          const c=state.canvas.getContext('2d'); c.clearRect(0,0,${width},${height});
          const t=${JSON.stringify([...token])}, dx=${phase === 'a' ? 0 : 16};
          for(let p=0;p<16;p++) {
            c.fillStyle='rgb('+t.slice(p*3,p*3+3).join(',')+')';
            c.fillRect(dx+p,0,1,1); c.fillRect(${width}-16-dx+p,${height}-1,1,1);
          }
        })()`);
      wc.invalidate(); // Critical for unchanged/static documents (no paint starvation).
      pulse = setInterval(() => { if (!wc.isDestroyed()) wc.invalidate(); }, 30);
      return await deadline(captured, remaining(), 'marker_paint');
    } finally { clearInterval(pulse); active = null; }
  }
  async function prepare() {
    // Materialize a script-free renderer before issuing Runtime commands; a
    // brand-new WebContents has no live CDP Runtime target yet in Electron 44.
    // Explicit transparency avoids about:blank's opaque OSR backdrop surviving
    // the next navigation. HTML bytes and page APIs are never modified; the
    // gate installs only a closure-owned error observer before page scripts.
    await deadline(wc.loadURL('data:text/html,<style>html,body{background:transparent}</style>'), remaining(), 'blank_target');
    errorGate = await deadline(installPageErrorGate(wc, pageErrors, fail), remaining(), 'error_gate');
    if (fatal) throw fatal;
    await deadline(wc.loadURL(expectedUrl), remaining(), 'navigation');
    if (fatal) throw fatal;
    await ready();
  }
  async function ready() {
    await deadline(isolated(wc, `${checkDocumentCode(expectedUrl,f)};
      (async () => {
        await document.fonts.ready;
        await Promise.all([...document.images].map(async im => {
          await im.decode(); if (!im.complete || !im.naturalWidth) throw Error('image_not_ready');
        }));
        if (document.readyState !== 'complete') throw Error('document_not_ready');
        if (document.getAnimations().some(a => a.playState === 'running' || a.pending)) throw Error('unstable_page:active_animation');
        return true;
      })()`), remaining(), 'resources');
    // Track actual browser resource requests including CSS/background/font/fetch;
    // late requests during the witness window invalidate the whole frame.
    await deadline((async () => {
      while (ses._vtxPending.size || Date.now()-ses._vtxChanged < 100) { if (fatal) throw fatal; await delay(20); }
    })(), remaining(), 'resource_quiet');
    if (ses._vtxErrors.length) throw Error(`resource_error:${ses._vtxErrors.join(';').slice(0,1000)}`);
    await deadline(checkPageErrors(), remaining(), 'page_checkpoint');
  }
  async function capture() {
    if (closed) throw fatal || Error('closed_context');
    capturing = true;
    result.capture_inflight++;
    result.peak_capture_inflight=Math.max(result.peak_capture_inflight,result.capture_inflight);
    if (result.capture_inflight!==1) throw Error('capture_limit');
    decoder.begin(f,generation);
    const captureStarted=performance.now();
    // Recheck all readiness and the error checkpoint after waiting in the
    // ahead slot. The epoch is frozen only AFTER this promotion fence.
    await ready();
    const resourceEpoch = ses._vtxEpoch;
    // An untagged snapshot is ONLY a veto against DOM-marker side effects
    // (:last-child/:has/etc.). It is NEVER a source for recovered pixels, nor
    // evidence of target identity. Every output pixel still comes from nonce-
    // attested paint witnesses below; a stale baseline can only reject a frame.
    // A prepared OSR document may have completed resources without producing
    // a new compositor frame. Refresh the unmarked veto snapshot before asking
    // capturePage; this paint is never used as attested output pixel evidence.
    await deadline(new Promise(resolve => { unmarkedPaint=resolve; wc.invalidate(); }), remaining(), 'unmarked_refresh');
    const baselineImage = await wc.capturePage();
    const baselineSize = baselineImage.getSize();
    if (baselineSize.width !== width || baselineSize.height !== height) throw Error('baseline_geometry');
    const baselinePending = decoder.decode(baselineImage.toPNG(),'baseline');
    const a = await witness('a','a'); const b = await witness('b','b');
    // A/B/A/B repeats detect ordinary page timers even in marker-covered pixels.
    await delay(40);
    const aa = await witness('a','aa');
    if (!samePhase(a.bitmap,aa.bitmap,'a',width,height)) throw Error('unstable_page:repeated_a');
    const bb = await witness('b','bb');
    if (!samePhase(b.bitmap,bb.bitmap,'b',width,height)) throw Error('unstable_page:repeated_b');
    const [baseline, ar, br, aar, bbr] = await Promise.all([baselinePending,a.rgba,b.rgba,aa.rgba,bb.rgba]);
    if (!samePhase(ar,aar,'a',width,height)) throw Error('unstable_page:repeated_a');
    if (!samePhase(br,bbr,'b',width,height)) throw Error('unstable_page:repeated_b');
    if (fatal) throw fatal;
    if (ses._vtxEpoch !== resourceEpoch || ses._vtxPending.size || ses._vtxErrors.length) throw Error('resource_changed_during_capture');
    await isolated(wc, checkDocumentCode(expectedUrl,f));
    const clean = mergeWitnesses(a.bitmap,b.bitmap,width,height);
    const expected = mergeWitnesses(ar,br,width,height);
    if (!expected.equals(baseline)) throw Error('marker_side_effect_or_unstable_baseline');
    const rebuilt = nativeImage.createFromBitmap(clean,{width,height,scaleFactor:1});
    const png = rebuilt.toPNG();
    // Bitmap roundtrip alone misses alpha/color normalization regressions.
    // Guard the entire decoded output against *native witness PNGs*, every frame.
    if (!(await decoder.decode(png,'final')).equals(expected)) throw Error('native_png_semantics_mismatch');
    // Keep monitoring through all witnesses and PNG reconstruction. No await
    // between this final fence/veto and the atomic publication/receipt.
    await deadline(checkPageErrors(), remaining(), 'page_checkpoint');
    if (ses._vtxEpoch !== resourceEpoch || ses._vtxPending.size || ses._vtxErrors.length) throw Error('resource_changed_during_capture');
    decoder.assertComplete();
    const out = path.join(job.out_dir, `f${String(f).padStart(4,'0')}.png`);
    atomicWrite(out,png);
    return {frame:f,generation,url:expectedUrl,paints,rejected_paints:rejected,witnesses:4,
      png_bytes:png.length,sha256:crypto.createHash('sha256').update(png).digest('hex'),
      path:'electron_native_topng',baseline_veto_passed:true,png_semantics_passed:true,
      page_error_fence_passed:true,png_decode_count:6,prefetch_age_ms:captureStarted-start,elapsed_ms:performance.now()-start,output:out};
  }
  async function close() {
    if (closed) return;
    closed=true;
    errorGate?.close(); if (!win.isDestroyed()) win.destroy();
    result.live_documents--;
    // Only the exclusive capture owner may touch/drain the shared decoder.
    if (capturing) {
      await decoder.drain();
      result.capture_inflight--;
    }
    clearTimeout(watchdog);
  }
  // Observe prep rejection immediately: ahead errors are private until their
  // ordered turn and must never become host unhandled Promise rejections.
  const prepared=prepare().then(() => null, async e => {
    fatal ||= e; await close(); return fatal;
  });
  return {f, prepared, capture, close,
    skip: async () => { decoder.begin(f,generation); await decoder.drain(); }};
}
function createSession(partition) {
  // Unique session per generation prevents worker/request/late callback state
  // from ever being inherited by another document. Native registry lifetime is
  // batch-scoped; report session count honestly rather than claim two sessions.
  const ses = session.fromPartition(partition, {cache:false});
  ses._vtxPending = new Set(); ses._vtxErrors=[]; ses._vtxChanged=0; ses._vtxEpoch=0; ses._vtxRequests=0;
  const resourceError = message => { if (ses._vtxErrors.length < 32) ses._vtxErrors.push(message.slice(0,1000)); };
  ses.setPermissionRequestHandler((_wc,_p,cb) => cb(false));
  ses.setPermissionCheckHandler(() => false);
  ses.on('will-download', e => e.preventDefault());
  ses.webRequest.onBeforeRequest((d,cb) => {
    ses._vtxChanged=Date.now(); ses._vtxEpoch++; ses._vtxRequests++;
    const cancel=ses._vtxPending.size>=1000 || ses._vtxRequests>10000;
    if (cancel) resourceError('request_limit'); else ses._vtxPending.add(d.id);
    cb({cancel});
  });
  const done = d => { ses._vtxPending.delete(d.id); ses._vtxChanged=Date.now(); ses._vtxEpoch++; };
  ses.webRequest.onCompleted(d => {done(d); if(d.statusCode>=400) resourceError(`http_${d.statusCode}:${d.url}`);});
  ses.webRequest.onErrorOccurred(d => {done(d); resourceError(`${d.error}:${d.url}`);});
  return ses;
}
async function main() {
  fs.mkdirSync(job.out_dir,{recursive:true});
  const result = {schema:'vtx.electron.navigation/2',run_id:runId,requested_backend:'electron',effective_backend:'electron',
    mode:'N',raster_mode:job.mode,output:job.output,versions:process.versions,
    viewport:{width,height,device_scale_factor:1},gpu_feature_status:app.getGPUFeatureStatus(),
    max_inflight:2,max_live_documents:2,max_capture_inflight:1,prefetch_limit:1,
    live_documents:0,peak_live_documents:0,capture_inflight:0,peak_capture_inflight:0,sessions_created:0,
    decoder_threads:1,max_pending_decodes:6,shared_memory:false,frames:[],failed:[],errors:{},
    page_error_gate:PAGE_ERROR_GATE,page_errors:{}};
  const decoder = new NativePngDecoder(width,height);
  let generation=0;
  const create = f => {
    result.page_errors[f]=[];
    return createFrameContext(f,++generation,result.page_errors[f],decoder,result);
  };
  let current=frames.length ? create(frames[0]) : null;
  for (let i=0;i<frames.length;i++) {
    const f=frames[i];
    let next=null;
    try {
      const preparationError=await current.prepared;
      if (result.timed_out) return;
      if (preparationError) { await current.skip(); throw preparationError; }
      // Launch only one ahead; no nonce/baseline/decode until prior close.
      if (i+1<frames.length) next=create(frames[i+1]);
      result.frames.push(await current.capture());
    } catch(e) {
      if (!result.failed.includes(f)) result.failed.push(f);
      result.errors[f] ||= String(e?.stack||e).slice(0,4000);
    } finally { await current.close(); }
    if (result.timed_out) return;
    atomicWrite(job.result,JSON.stringify(result,null,2));
    current=next || (i+1<frames.length ? create(frames[i+1]) : null);
  }
  atomicWrite(job.result,JSON.stringify(result,null,2));
  await decoder.close();
  app.exit(result.failed.length ? 1 : 0);
}
app.whenReady().then(main).catch(e => {console.error(e.stack||e);app.exit(70);});
