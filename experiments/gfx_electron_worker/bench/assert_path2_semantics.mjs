// assert_path2_semantics.mjs — 通路 2 语义自证（issue #113 DoD：
// 「修改 toBitmap 副本不等于修改原 image」的直接证据采集）。
//
// 流程：载入基准页（canary 已注入绘制）→ capturePage 得 image：
//   A = image.toPNG()                      ——原 image 直接编码（含 Canary）；
//   B = 通路 2 全流程（副本恢复→createFromBitmap 重建→toPNG）——应无 Canary。
// 另证：C = 对 image.toBitmap() 副本恢复后再 image.toPNG()（不重建）——
//   仍含 Canary（副本修改不影响本体）。Python 侧（tests）对 A/B/C 三 PNG
//   用 vtx.frame_canary.verify_clean_pixels 裁决：A/C 含签名、B 无签名。
import { app, BrowserWindow, nativeImage } from 'electron';
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PKG = path.resolve(HERE, '..');

function parseArgs(argv) {
  const o = { outDir: null, canaryJs: null, frame: 7, page: path.join(PKG, 'pages', 'bench_frame.html') };
  for (const a of argv) {
    const m = a.match(/^--([a-z-]+)=(.*)$/);
    if (!m) throw new Error(`参数：${a}`);
    if (m[1] === 'out-dir') o.outDir = path.resolve(m[2]);
    else if (m[1] === 'canary-js') o.canaryJs = path.resolve(m[2]);
    else if (m[1] === 'frame') o.frame = parseInt(m[2], 10);
    else if (m[1] === 'page') o.page = path.resolve(m[2]);
    else throw new Error(`未知参数 --${m[1]}`);
  }
  if (!o.outDir || !o.canaryJs) throw new Error('--out-dir 与 --canary-js 必填');
  return o;
}
const OPTS = parseArgs(process.argv.slice(2));
const CANARY_JS = fs.readFileSync(OPTS.canaryJs, 'utf8');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

app.commandLine.appendSwitch('force-color-profile', 'srgb');
app.commandLine.appendSwitch('hide-scrollbars');
app.disableHardwareAcceleration();

app.whenReady().then(async () => {
  fs.mkdirSync(OPTS.outDir, { recursive: true });
  const win = new BrowserWindow({
    width: 1920, height: 1080, show: false, frame: false, transparent: true,
    useContentSize: true,
    webPreferences: {
      offscreen: { useSharedTexture: false, deviceScaleFactor: 1 },
      backgroundThrottling: false, contextIsolation: true, nodeIntegration: false,
      sandbox: true, spellcheck: false,
    },
  });
  win.webContents.setFrameRate(60);
  const u = new URL(`file://${OPTS.page}`);
  u.searchParams.set('f', String(OPTS.frame));
  await win.loadURL(u.href);
  await new Promise((r) => win.webContents.once('paint', r));
  await sleep(120);
  const f = OPTS.frame;
  await win.webContents.executeJavaScript(
    `(function () { history.replaceState(null, '', '?f=${f}'); ${CANARY_JS} return window.__vtxCanary.frame; })()`, true);
  win.webContents.invalidate();
  await win.webContents.executeJavaScript(
    'new Promise((res) => requestAnimationFrame(' +
    '  () => requestAnimationFrame(' +
    '    () => res(window.__vtxCanary ? window.__vtxCanary.drawn : null))))', true);

  const image = await win.webContents.capturePage();
  const size = image.getSize();
  const okContract = !image.isEmpty() && size.width === 1920 && size.height === 1080;

  // A：原 image 直接 toPNG（不经任何副本修改）。
  const pngA = image.toPNG();
  fs.writeFileSync(path.join(OPTS.outDir, 'A_original_topng.png'), pngA);

  // B：通路 2 语义——副本 canary 验证+恢复 → createFromBitmap 重建 → toPNG。
  const bitmap = image.toBitmap();
  const contract2 = bitmap.byteLength === 1920 * 1080 * 4;
  const { verifyCanary2, restoreCanary2Inplace } = await import('../output_bridge.mjs');
  const gate = verifyCanary2(bitmap, f, true);
  const restoreSource = restoreCanary2Inplace(bitmap, 1920);
  const rebuilt = nativeImage.createFromBitmap(bitmap, { width: 1920, height: 1080 });
  const pngB = rebuilt.toPNG();
  fs.writeFileSync(path.join(OPTS.outDir, 'B_rebuilt_topng.png'), pngB);

  // C：只改副本、不重建、原 image 再 toPNG —— 本体应不受影响（仍含 Canary）。
  const pngC = image.toPNG();
  fs.writeFileSync(path.join(OPTS.outDir, 'C_original_after_copy_edit.png'), pngC);

  // D：原 image 的 toBitmap 首 8 字节（Python 侧核验签名仍在本体缓冲）。
  const probe = image.toBitmap().subarray(0, 8).toString('hex');
  const result = {
    schema: 'vtx.elec.path2_semantics/1', frame: f,
    native_image_contract: okContract, bitmap_contract: contract2,
    gate: { accepted: gate.accepted, rendered: gate.renderedFrame, exact: gate.exactMatch },
    restore_source: restoreSource,
    original_bitmap_first8_hex: probe,
    bytes: { A: pngA.byteLength, B: pngB.byteLength, C: pngC.byteLength },
    a_equals_c: Buffer.compare(pngA, pngC) === 0,
    a_equals_b: Buffer.compare(pngA, pngB) === 0,
  };
  fs.writeFileSync(path.join(OPTS.outDir, 'semantics.json'), JSON.stringify(result, null, 2));
  process.stdout.write(`VTX_PATH2_SEMANTICS ${JSON.stringify(result)}\n`);
  win.destroy();
  app.exit(0);
}).catch((e) => {
  process.stderr.write(`✗ ${e && e.stack ? e.stack : e}\n`);
  app.exit(1);
});
