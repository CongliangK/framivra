// run_output_bench.mjs — #113 ELEC-02 双路径输出基准 runner（Electron 主进程）。
//
// 用法（在 experiments/gfx_electron_worker 内、Electron 运行时下）：
//   electron bench/run_output_bench.mjs --path=1 --frames=1000 --out-root=/tmp/e \
//       --canary-js=<vtx.frame_canary.get_canary_js('clean_1px_restore') 落盘文件> \
//       [--mode=b0|b1] [--encoder=spng|fpng|zlib] [--level=1..3]
//       [--filter=none|sub|up|sub_up|all] [--dump-bitmaps=K] [--hold-ms=N]
//
// 流程：offscreen 硬化窗口（#112 main.mjs 同款）→ 每帧 __vtx_render(f) +
// canary 注入桥重放（replaceState ?f= 后整体重执行注入 IIFE——只读复用
// vtx/frame_canary.py 的 JS，未改其模板）→ 双 rAF → invalidate → paint →
// capturePage → 通路 1（OutputBridge）/通路 2（encodeTopngPath2）→ 落盘 +
// 指标。canary 闸门拒绝（陈旧帧）→ 重试（上限 5，计数入报告）。
//
// 产物（--out-root 下）：
//   f<NNNN>.png        每帧输出（通路 1 原子落盘 / 通路 2 worker 落盘）
//   bitmaps/f<NNNN>.bgra  前 K 帧捕获位图快照（供 Python 逐字节验收）
//   bench_report.json  全量指标（eventloop p50/p95/p99/max、external 内存、
//                      双侧分项计时、回执聚合、canary/重试账目、版本/GPU 真值）
//   stdout             VTX_ELEC_BENCH_RESULT <一行 JSON 摘要>
import { app, BrowserWindow } from 'electron';
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';
import {
  OutputBridge, DiskCommitPool, encodeTopngPath2, EventLoopDelayMonitor,
  memorySample, memorySummary, VIEW_W, VIEW_H,
} from '../output_bridge.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PKG = path.resolve(HERE, '..');
const DEFAULT_PAGE = path.join(PKG, 'pages', 'bench_frame.html');

function parseArgs(argv) {
  const opts = { path: '1', frames: 200, mode: 'b0', outRoot: process.cwd(),
                 encoder: 'spng', level: 1, filter: 'sub_up', canaryJs: null,
                 page: DEFAULT_PAGE, dumpBitmaps: 0, holdMs: 0, timeoutMs: 600000,
                 userDataDir: null };
  for (let i = 0; i < argv.length; i++) {
    const m = argv[i].match(/^--([a-z-]+)=(.*)$/);
    if (!m) throw new Error(`无法识别参数：${argv[i]}`);
    const [, k, v] = m;
    switch (k) {
      case 'path': opts.path = v; break;
      case 'frames': opts.frames = parseInt(v, 10); break;
      case 'mode': opts.mode = v; break;
      case 'out-root': opts.outRoot = path.resolve(v); break;
      case 'encoder': opts.encoder = v; break;
      case 'level': opts.level = parseInt(v, 10); break;
      case 'filter': opts.filter = v; break;
      case 'canary-js': opts.canaryJs = path.resolve(v); break;
      case 'page': opts.page = path.resolve(v); break;
      case 'dump-bitmaps': opts.dumpBitmaps = parseInt(v, 10); break;
      case 'hold-ms': opts.holdMs = parseInt(v, 10); break;
      case 'timeout-ms': opts.timeoutMs = parseInt(v, 10); break;
      case 'user-data-dir': opts.userDataDir = path.resolve(v); break;
      default: throw new Error(`未知参数：--${k}`);
    }
  }
  if (opts.path !== '1' && opts.path !== '2') throw new Error('--path 须为 1 或 2');
  if (opts.mode !== 'b0' && opts.mode !== 'b1') throw new Error('--mode 须为 b0/b1');
  if (!opts.canaryJs) throw new Error('--canary-js 必填（vtx.frame_canary 唯一 home）');
  return opts;
}

const OPTS = parseArgs(process.argv.slice(2));
const CANARY_JS = fs.readFileSync(OPTS.canaryJs, 'utf8');

// 就绪前固定开关（#112 main.mjs 同序：disableHardwareAcceleration 必须在
// whenReady 之前）。
app.commandLine.appendSwitch('force-color-profile', 'srgb');
app.commandLine.appendSwitch('hide-scrollbars');
if (OPTS.mode === 'b0') app.disableHardwareAcceleration();

function fail(code, msg) {
  process.stderr.write(`✗ run_output_bench：${msg}\n`);
  if (app.isReady()) app.exit(code); else process.exit(code);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const nowMs = () => Number(process.hrtime.bigint()) / 1e6;

// ---------------------------------------------------------------- GPU 真值（#112 main.mjs 同源）
async function probeRendererGl(win) {
  try {
    return await win.webContents.executeJavaScript(`(function () {
      try {
        const c = document.createElement('canvas');
        const gl = c.getContext('webgl2') || c.getContext('webgl');
        if (!gl) return { error: 'no webgl context' };
        const ext = gl.getExtension('WEBGL_debug_renderer_info');
        const pick = (o, k) => String(ext ? gl.getParameter(ext[k]) : gl.getParameter(o));
        return { renderer: pick(gl.RENDERER, 'UNMASKED_RENDERER_WEBGL'),
                 vendor: pick(gl.VENDOR, 'UNMASKED_VENDOR_WEBGL'),
                 version: String(gl.getParameter(gl.VERSION)) };
      } catch (e) { return { error: String(e) }; }
    })()`, true);
  } catch (e) { return { error: String(e) }; }
}

async function collectGpuTruth(win) {
  let gpuInfo = null, gpuInfoError = null;
  try {
    gpuInfo = await Promise.race([
      app.getGPUInfo('complete'),
      new Promise((_, rej) => setTimeout(() => rej(new Error('getGPUInfo 超时')), 15000)),
    ]);
  } catch (e) { gpuInfoError = String(e); }
  return { gpu_feature_status: app.getGPUFeatureStatus(), gpu_info_error: gpuInfoError,
           renderer_probe: await probeRendererGl(win) };
}

// ---------------------------------------------------------------- 帧驱动（Mode P + canary 重放）
async function driveFrame(wc, f) {
  // ① 页面置帧（__vtx_render，本仓 bench 页非 configurable 定义）；
  // ② canary 注入桥重放：replaceState(?f=N) 后整体重执行注入 IIFE——
  //    IIFE 顶读 location.search 取新 f，double-rAF 后 draw(f)（模板逻辑
  //    未改动；这是 #113 基准的注入重放形态，#116 将以 preload 常驻注入
  //    + __vtx_render 包装接管）。
  const js =
    `(function () {
       try { if (typeof window.__vtx_render === 'function') window.__vtx_render(${f}); } catch (e) {}
       history.replaceState(null, '', '?f=${f}');
       ${CANARY_JS}
       return { canary: window.__vtxCanary ? window.__vtxCanary.frame : null,
                renderOk: typeof window.__vtx_render };
     })()`;
  const r = await wc.executeJavaScript(js, true);
  if (r.canary !== f) throw new Error(`canary 注入重放失败：probe frame=${r.canary} ≠ ${f}`);
  // ③ 双 rAF（clean 模式协议时机；OSR 下由 invalidate/paint 驱动）。
  wc.invalidate();
  await wc.executeJavaScript(
    'new Promise((res) => requestAnimationFrame(' +
    '  () => requestAnimationFrame(' +
    '    () => res(window.__vtxCanary ? window.__vtxCanary.drawn : null))))', true);
  // ④ 推一帧过合成器再捕获。
  wc.invalidate();
  await new Promise((resolve) => {
    const t = setTimeout(resolve, 400);   // paint 超时非致命（计数由闸门兜底）
    wc.once('paint', () => { clearTimeout(t); resolve(); });
  });
}

async function main() {
  const tStart = Date.now();
  fs.mkdirSync(OPTS.outRoot, { recursive: true });
  if (OPTS.userDataDir) { app.setPath('userData', OPTS.userDataDir); }
  if (OPTS.dumpBitmaps > 0) fs.mkdirSync(path.join(OPTS.outRoot, 'bitmaps'), { recursive: true });

  const win = new BrowserWindow({
    width: VIEW_W, height: VIEW_H, show: false, frame: false, transparent: true,
    useContentSize: true,
    webPreferences: {
      offscreen: { useSharedTexture: false, deviceScaleFactor: 1 },
      backgroundThrottling: false, contextIsolation: true, nodeIntegration: false,
      sandbox: true, spellcheck: false,
    },
  });
  win.webContents.setFrameRate(60);

  const pageUrl = new URL(`file://${OPTS.page}`);
  pageUrl.searchParams.set('f', '0');
  const timer = setTimeout(() => fail(2, `整体超时（${OPTS.timeoutMs}ms）`), OPTS.timeoutMs);
  await win.loadURL(pageUrl.href);
  await new Promise((res) => win.webContents.once('paint', res));
  await win.webContents.executeJavaScript(
    'document.fonts ? document.fonts.ready.then(() => true) : true');
  await sleep(120);

  // ---- 输出面 ----
  const monitor = new EventLoopDelayMonitor(10);
  monitor.start();
  let bridge = null, pool = null;
  if (OPTS.path === '1') {
    bridge = new OutputBridge({ encoder: OPTS.encoder, level: OPTS.level,
                                filter: OPTS.filter, threads: 4 });
    await bridge.start();
  } else {
    pool = new DiskCommitPool({ workers: 4 });
  }

  const perFrame = [];
  let canaryRetries = 0, canaryRejects = 0;
  const memSamples = [memorySample()];
  const t0 = nowMs();

  for (let f = 0; f < OPTS.frames; f++) {
    const rec = { f };
    // 捕获 + 闸门（拒绝=陈旧 → 有限重试）。
    let receiptOrPath2 = null;
    for (let attempt = 0; attempt < 5; attempt++) {
      const tDrive0 = nowMs();
      await driveFrame(win.webContents, f);
      rec.tDriveMs = nowMs() - tDrive0;
      const tCap0 = nowMs();
      const image = await win.webContents.capturePage();
      rec.tCaptureMs = nowMs() - tCap0;
      const outPath = path.join(OPTS.outRoot, `f${String(f).padStart(4, '0')}.png`);
      try {
        if (OPTS.path === '1') {
          receiptOrPath2 = await bridge.writeFrameFromImage(image, {
            frame: f, outPath, tCaptureUs: rec.tCaptureMs * 1000 });
        } else {
          receiptOrPath2 = await encodeTopngPath2(image, {
            frame: f, outPath, pool, width: VIEW_W, height: VIEW_H });
        }
        break;
      } catch (e) {
        if (e.code === 'canary_reject' && attempt < 4) {
          canaryRetries++; canaryRejects++;
          continue;
        }
        clearTimeout(timer);
        return fail(3, `帧 ${f} 输出失败：${e.message}`);
      }
    }
    if (!receiptOrPath2) { clearTimeout(timer); return fail(3, `帧 ${f} canary 重试耗尽`); }
    if (OPTS.path === '1') {
      rec.receipt = receiptOrPath2;
      rec.tBridgeMs = receiptOrPath2.tSubmitMs;
      rec.tToBitmapMs = receiptOrPath2.tToBitmapMs;
    } else {
      rec.tPngEncodeMs = receiptOrPath2.tPngEncodeMs;
      rec.tToBitmapMs = receiptOrPath2.tToBitmapMs;
      rec.rebuildEchoOk = receiptOrPath2.rebuildEchoOk;
      if (!receiptOrPath2.rebuildEchoOk) {
        clearTimeout(timer);
        return fail(4, `帧 ${f} 通路 2 重建自证失败（toBitmap 回读 ≠ 恢复像素）`);
      }
    }
    memSamples.push(memorySample());
    perFrame.push(rec);
  }
  const wallMs = nowMs() - t0;
  // 在途高水位：桥/池自记（await 返回后再采样恒为 0，无意义）。
  const maxInflight = bridge ? bridge.metrics.maxInflight : (pool ? pool.maxInflight : 0);

  const gpu = await collectGpuTruth(win);
  monitor.stop();
  const ev = monitor.summary();

  let bridgeMetrics = null, receiptsAgg = null;
  if (bridge) {
    bridgeMetrics = { ...bridge.metrics, memSamples: undefined };
    const rs = perFrame.map((r) => r.receipt).filter(Boolean);
    const pct = (arr, p) => {
      if (!arr.length) return null;
      const s = [...arr].sort((a, b) => a - b);
      return s[Math.min(s.length - 1, Math.floor((p / 100) * s.length))];
    };
    receiptsAgg = {
      n: rs.length,
      tDecodeUs: { p50: pct(rs.map((r) => r.tDecodeUs), 50), p99: pct(rs.map((r) => r.tDecodeUs), 99) },
      tRestoreUs: { p50: pct(rs.map((r) => r.tRestoreUs), 50) },
      tAlphaNormUs: { p50: pct(rs.map((r) => r.tAlphaNormUs), 50), p99: pct(rs.map((r) => r.tAlphaNormUs), 99) },
      tPngEncodeUs: { p50: pct(rs.map((r) => r.tPngEncodeUs), 50), p99: pct(rs.map((r) => r.tPngEncodeUs), 99) },
      tDiskCommitUs: { p50: pct(rs.map((r) => r.tDiskCommitUs), 50), p99: pct(rs.map((r) => r.tDiskCommitUs), 99) },
      tWriteUs: { p50: pct(bridge.metrics.tWriteUs, 50), p99: pct(bridge.metrics.tWriteUs, 99) },
      tSendUs: { p50: pct(bridge.metrics.tSendUs, 50), p99: pct(bridge.metrics.tSendUs, 99) },
      pngBytes: { min: Math.min(...rs.map((r) => r.pngBytes)), max: Math.max(...rs.map((r) => r.pngBytes)),
                  mean: rs.reduce((a, r) => a + r.pngBytes, 0) / rs.length },
      canaryMatch: { exact: rs.filter((r) => r.canaryMatch === 1).length,
                     tol1: rs.filter((r) => r.canaryMatch === 2).length },
      restoreSource: { reported: rs.filter((r) => r.restoreSource === 0).length,
                       neighbor: rs.filter((r) => r.restoreSource === 1).length },
    };
    await bridge.stop();
  }
  if (pool) await pool.stop();

  const capArr = perFrame.map((r) => r.tCaptureMs);
  const driveArr = perFrame.map((r) => r.tDriveMs);
  const pngEncArr = perFrame.map((r) => r.tPngEncodeMs).filter((v) => v !== undefined);
  const pct = (arr, p) => {
    if (!arr.length) return null;
    const s = [...arr].sort((a, b) => a - b);
    return s[Math.min(s.length - 1, Math.floor((p / 100) * s.length))];
  };
  const report = {
    schema: 'vtx.elec.output_bench/1',
    path: `bitmap_shared_encoder(1)|electron_native_topng(2) 选择 ${OPTS.path}`,
    path_id: parseInt(OPTS.path, 10),
    mode: OPTS.mode, frames: OPTS.frames, encoder: OPTS.encoder,
    level: OPTS.level, filter: OPTS.filter,
    started_at_utc: new Date().toISOString().replace(/\.\d+Z$/, 'Z'),
    versions: { electron: process.versions.electron, chrome: process.versions.chrome,
                node: process.versions.node, v8: process.versions.v8 },
    gpu,
    wall_ms: wallMs, fps: OPTS.frames / (wallMs / 1000),
    eventloop: ev,
    memory: memorySummary(memSamples),
    capture_ms: { p50: pct(capArr, 50), p95: pct(capArr, 95), p99: pct(capArr, 99), max: Math.max(...capArr) },
    drive_ms: { p50: pct(driveArr, 50), p99: pct(driveArr, 99) },
    topng_encode_ms: pngEncArr.length ? { p50: pct(pngEncArr, 50), p95: pct(pngEncArr, 95),
                                          p99: pct(pngEncArr, 99), max: Math.max(...pngEncArr),
                                          mean: pngEncArr.reduce((a, b) => a + b, 0) / pngEncArr.length } : null,
    max_inflight: maxInflight,
    canary_retries: canaryRetries, canary_rejects: canaryRejects,
    receipts: receiptsAgg, bridge: bridgeMetrics,
    dod_gates: null,   // 由 Python 验收侧填充判定（本 runner 只交实测数）
  };
  // DoD 门预填（通路 1）：
  if (OPTS.path === '1') {
    report.dod_gates = {
      eventloop_p99_ms: ev ? ev.p99_ms : null, limit_p99_ms: 15,
      external_fluctuation_mb: report.memory ? report.memory.external.fluctuation / 1048576 : null,
      limit_external_mb: 64,
    };
  }
  fs.writeFileSync(path.join(OPTS.outRoot, 'bench_report.json'),
                   JSON.stringify(report, null, 2));
  // per-frame 明细（调试/红队抽查用；大数组单文件）。
  fs.writeFileSync(path.join(OPTS.outRoot, 'per_frame.json'), JSON.stringify(perFrame));
  const summary = {
    ok: true, path: OPTS.path, frames: OPTS.frames, mode: OPTS.mode,
    encoder: OPTS.encoder, filter: OPTS.filter,
    fps: report.fps,
    eventloop_p99_ms: ev ? ev.p99_ms : null, eventloop_max_ms: ev ? ev.max_ms : null,
    external_fluctuation_mb: report.memory ? report.memory.external.fluctuation / 1048576 : null,
    external_slope_b_per_frame: report.memory ? report.memory.external.slope_per_frame : null,
    topng_encode_p99_ms: report.topng_encode_ms ? report.topng_encode_ms.p99 : null,
    canary_retries: canaryRetries, max_inflight: maxInflight,
    wall_ms: wallMs,
  };
  process.stdout.write(`VTX_ELEC_BENCH_RESULT ${JSON.stringify(summary)}\n`);
  clearTimeout(timer);
  if (OPTS.holdMs > 0) await sleep(OPTS.holdMs);
  win.destroy();
  app.exit(0);
}

app.whenReady().then(main).catch((e) => fail(1, `主流程异常：${e && e.stack ? e.stack : e}`));
