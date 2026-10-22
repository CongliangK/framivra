// main.mjs — vtx GFX 路线 B：最小 Electron Offscreen Rendering 宿主（issue #112 ELEC-01）。
//
// 只做一件事：离屏加载一页 gfx 帧（如 gfxA.html?f=0）→ 捕获首帧 PNG →
// 采集渲染环境真值（process.versions 三元组 + GPU 内部接口实测）→ 干净退出。
//
// B0 / B1 双模式（工单口径，独立进程启动）：
//   --mode=b0  Software output device：app.whenReady() 之前 disableHardwareAcceleration()，
//              软件输出设备直接在 CPU 渲染，省去 GPU→CPU 回读拷贝；
//   --mode=b1  GPU Accelerated → CPU Shared Memory Bitmap：保留硬件加速，
//              whenReady 后 app.getGPUFeatureStatus() + await app.getGPUInfo('complete')
//              采集真实 GPU 状态（env_manifest runtime_backend_truth 的 electron 证据源）。
//
// 安全硬化（工单逐项）：
//   - offscreen: { useSharedTexture: false }：显式禁用共享纹理路径（GHSA-8x5q-pvf5-64mp
//     UAF），首帧走 CPU shared memory bitmap（capturePage().toPNG()，不经
//     NativeImage.toBitmap()，规避 Electron ≥43 的 toBitmap sRGB 归一化变更面）；
//   - contextIsolation/sandbox/nodeIntegration/spellcheck/backgroundThrottling 按工单锁定；
//   - deviceScaleFactor = 1（webContents.setDeviceScaleFactor，仅 offscreen 生效）；
//   - force-color-profile=srgb + hide-scrollbars 命令行开关。
//
// 进程组卫生（#103 协作契约，锦上添花层；权威层是宿主侧守卫+账本）：
//   宿主经 vtx.runtime.proc_supervisor.SupervisedProcessGroup 在 argv 尾部追加
//   --parent-alive-fd=<fd>（匿名管道读端）。主进程用 fs.createReadStream(null, {fd})
//   阻塞守听；end/close（宿主暴毙 → 内核关闭写端 → EOF）后立即
//   process.kill(-process.pid, 'SIGKILL') 自毁整棵 Electron（Main+GPU+Zygote+Renderer）。
//
// 产物（--out-dir 下）：
//   frame_f<NNNN>.png  首帧位图；truth.json  渲染环境真值；result.json  运行摘要
//   （stdout 同步打印一行 "VTX_ELECTRON_RESULT <json>" 供宿主解析）。
import { app, BrowserWindow, ipcMain } from 'electron';
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, '..', '..');
const DEFAULT_PAGE = path.join(REPO_ROOT, 'templates', 'skill-demo', 'gfxA.html');

// ---------------------------------------------------------------- 参数解析
function parseArgs(argv) {
  const opts = { mode: null, page: DEFAULT_PAGE, frame: 0, outDir: process.cwd(),
                 userDataDir: path.join(process.cwd(), 'userData'),
                 parentAliveFd: null, timeoutMs: 60000, holdMs: 0 };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const m = a.match(/^--([a-z-]+)=(.*)$/);
    let key, val;
    if (m) { key = m[1]; val = m[2]; }
    else if (a.startsWith('--')) {
      key = a.slice(2);
      val = argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[++i] : true;
    } else { throw new Error(`无法识别的位置参数：${a}`); }
    switch (key) {
      case 'mode': opts.mode = String(val); break;
      case 'page': opts.page = path.resolve(String(val)); break;
      case 'frame': opts.frame = parseInt(val, 10); break;
      case 'out-dir': opts.outDir = path.resolve(String(val)); break;
      case 'user-data-dir': opts.userDataDir = path.resolve(String(val)); break;
      case 'parent-alive-fd': opts.parentAliveFd = parseInt(val, 10); break;
      case 'timeout-ms': opts.timeoutMs = parseInt(val, 10); break;
      case 'hold-ms': opts.holdMs = parseInt(val, 10); break;
      default: throw new Error(`未知参数：--${key}`);
    }
  }
  if (opts.mode !== 'b0' && opts.mode !== 'b1') {
    throw new Error(`--mode 必须是 b0（软件输出）或 b1（GPU 加速），得到：${opts.mode}`);
  }
  return opts;
}

const OPTS = parseArgs(process.argv.slice(2));

function fail(code, msg) {
  process.stderr.write(`✗ gfx_electron_worker：${msg}\n`);
  if (app.isReady()) app.exit(code); else process.exit(code);
}

// ---------------------------------------------------------------- 父存活守听（#103 协作契约）
// 必须在任何 await 之前武装：EOF → 第一件事就是组杀，勿夹杂忙活。
function armParentAliveWatchdog(fd) {
  const selfDestruct = () => {
    try { process.kill(-process.pid, 'SIGKILL'); } catch { /* 组已消亡 */ }
  };
  const stream = fs.createReadStream(null, { fd, autoClose: false });
  stream.on('end', selfDestruct);
  stream.on('close', selfDestruct);
  stream.on('error', (err) => {   // fd 失效=无法证明宿主存活，按宿主死亡处置
    process.stderr.write(`⚠ parent-alive fd=${fd} 异常：${err}\n`);
    selfDestruct();
  });
}

// ---------------------------------------------------------------- 就绪前固定开关（whenReady 之前）
app.setPath('userData', OPTS.userDataDir);   // → cache/tmp/<UTC>-gfx_elec*/（宿主指定）
fs.mkdirSync(OPTS.userDataDir, { recursive: true });
app.commandLine.appendSwitch('force-color-profile', 'srgb');
app.commandLine.appendSwitch('hide-scrollbars');
if (OPTS.mode === 'b0') app.disableHardwareAcceleration();   // B0：软件输出设备
if (Number.isInteger(OPTS.parentAliveFd)) armParentAliveWatchdog(OPTS.parentAliveFd);

// ---------------------------------------------------------------- 生效后端归类
// 词表与优先序对齐 vtx/env_manifest.classify_gl_backend（swiftshader > software >
// vulkan > nvidia）；证据只采渲染进程内部接口（GPUInfo/GPUFeatureStatus/WebGL
// 探针），严禁转录命令行启动参数。B0 硬件加速已关，无匹配时归 software_skia（Skia
// 软件光栅）。注：env_manifest 词表无 AMD/Intel 独立桶——非 NVIDIA 硬件 GL 亦按
// 「Linux Chromium GPU 进程原生 EGL/GLX 族」语义并入 nvidia_egl 桶（同源
// classify_gl_backend 的 GLX 并桶注释），归类输入全文记入证据供复核。
function classifyBackend(mode, featureStatus, gpuInfo, rendererProbe) {
  const aux = (gpuInfo && gpuInfo.auxAttributes) || {};
  const devices = (gpuInfo && gpuInfo.gpuDevice) || [];
  const s = [rendererProbe && rendererProbe.renderer, rendererProbe && rendererProbe.vendor,
             aux.glRenderer, aux.glVendor, aux.glVersion,
             ...devices.map((d) => `${d.vendorString || ''} ${d.deviceString || ''}`)]
            .filter(Boolean).join(' ').toLowerCase();
  const inputs = { probe_and_gl_strings: s, auxAttributes: {
    glRenderer: aux.glRenderer ?? null, glVendor: aux.glVendor ?? null,
    glImplementationParts: aux.glImplementationParts ?? null } };
  let backend = '';
  if (s.includes('swiftshader')) backend = 'swiftshader';
  else if (/llvmpipe|softpipe|softfp|software/.test(s)) backend = 'software_skia';
  else if (s.includes('vulkan')) backend = 'vulkan';
  else if (/nvidia|geforce|quadro|tesla/.test(s)) backend = 'nvidia_egl';
  else if (/amd|radeon|intel|iris|xe\s|mesa/.test(s)) backend = 'nvidia_egl';   // 硬件 GL 并桶（见上注）
  if (!backend && mode === 'b0') backend = 'software_skia';
  if (!backend) {
    // 兜底只看能力型字段的软件态（disabled_off 是特性开关常态，不构成软件证据）：
    // webgl/2d_canvas 走 software_only 或被禁 → 软件光栅；仍无法判定则空串
    // （宿主侧 env_manifest 拒收空值，宁可失败不编造）。
    const st = featureStatus || {};
    if (['webgl', '2d_canvas'].some((k) => ['software_only', 'disabled']
         .includes(st[k]))) backend = 'software_skia';
  }
  return { backend, inputs };
}

// 渲染进程内部 WebGL 探针：UNMASKED_RENDERER/VENDOR 是该 Renderer 实际 GL 后端的
// 自报（比 getGPUInfo 在 OSR 场景下的占位快照更可信；两者同记证据链互证）。
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

const withTimeout = (p, ms) => Promise.race([
  p, new Promise((_, rej) => setTimeout(() => rej(new Error(`超时 ${ms}ms`)), ms))]);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------- GPU 真值采集（内部接口实测）
async function collectGpuTruth(win) {
  const gpu_feature_status = app.getGPUFeatureStatus();          // 工单：B1 必采
  let gpu_info_complete = null;
  let gpu_info_error = null;
  try { gpu_info_complete = await withTimeout(app.getGPUInfo('complete'), 15000); }
  catch (e) { gpu_info_error = String(e); }                      // fail-open 显形，不编造
  const renderer_probe = await probeRendererGl(win);             // Renderer 实际 GL 自报
  const { backend, inputs } = classifyBackend(OPTS.mode, gpu_feature_status,
                                              gpu_info_complete, renderer_probe);
  return { gpu_feature_status, gpu_info_complete, gpu_info_error, renderer_probe,
           effective_backend: backend, classify_inputs: inputs };
}

// ---------------------------------------------------------------- 主流程
async function main() {
  const startedMs = Date.now();
  fs.mkdirSync(OPTS.outDir, { recursive: true });

  // 安全硬化 Offscreen BrowserWindow（工单逐项；useSharedTexture:false = GHSA 处方）
  const win = new BrowserWindow({
    width: 1920, height: 1080, show: false, frame: false, transparent: true,
    useContentSize: true,
    webPreferences: {
      // deviceScaleFactor 锁 1（Electron 44 形态：offscreen.deviceScaleFactor；
      // 旧的 webContents.setDeviceScaleFactor 已移除）。缺省亦为 1，显式写出
      // 是工单「锁定 deviceScaleFactor = 1」的自证。
      offscreen: { useSharedTexture: false, deviceScaleFactor: 1 },
      backgroundThrottling: false,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
      preload: path.join(HERE, 'preload.cjs'),
    },
  });
  // deviceScaleFactor=1 已在 offscreen 选项显式锁定（Electron 44 API 形态）

  let pageReport = null;                          // preload.cjs 隔离世界回传的页面自证
  ipcMain.on('vtx:page-loaded', (_e, report) => { pageReport = report; });

  let paintCount = 0;
  const firstPaint = new Promise((res) => win.webContents.once('paint', () => res()));
  win.webContents.on('paint', () => { paintCount++; });

  // 全局熔断：任何一步卡死都在 timeout-ms 内以非零码退出
  const timer = setTimeout(() => fail(2, `首帧捕获超时（${OPTS.timeoutMs}ms）`),
                           OPTS.timeoutMs);

  const pageUrl = new URL(`file://${OPTS.page}`);
  pageUrl.searchParams.set('f', String(OPTS.frame));
  try { await win.loadURL(pageUrl.href); }
  catch (e) { clearTimeout(timer); return fail(3, `页面加载失败 ${pageUrl.href}：${e}`); }

  await firstPaint;                               // OSR 合成器产出首帧
  await win.webContents.executeJavaScript(
    'document.fonts ? document.fonts.ready.then(() => true) : true');  // 字体就绪再定格
  await sleep(120);                               // 布局/字体安定窗口

  const image = await win.webContents.capturePage();   // CPU shared memory bitmap 路径
  const png = image.toPNG();
  const size = image.getSize();
  if (!(size.width === 1920 && size.height === 1080)) {
    clearTimeout(timer);
    return fail(4, `首帧尺寸 ${size.width}x${size.height} ≠ 1920x1080（deviceScaleFactor 未锁 1？）`);
  }

  const gpuTruth = await collectGpuTruth(win);
  const versions = {
    electron: process.versions.electron,
    chrome: process.versions.chrome,
    node: process.versions.node,
    v8: process.versions.v8,
  };

  const pngName = `frame_f${String(OPTS.frame).padStart(4, '0')}.png`;
  fs.writeFileSync(path.join(OPTS.outDir, pngName), png);
  const truth = {
    schema: 'vtx.gfx_electron_worker.truth/1',
    mode: OPTS.mode,
    collected_at_utc: new Date().toISOString().replace(/\.\d+Z$/, 'Z'),
    versions,                                   // 工单：三元组 electron/chrome/v8（+node）
    viewport: { width: size.width, height: size.height, device_scale_factor: 1 },
    color_space: 'srgb',                        // force-color-profile=srgb 开关锁定
    page_report: pageReport,                    // 隔离世界回传：f 参数 + devicePixelRatio 自证
    paint_events_until_capture: paintCount,
    gpu: { ...gpuTruth, source: 'electron' },   // getGPUFeatureStatus + getGPUInfo('complete')
    switches: ['force-color-profile=srgb', 'hide-scrollbars',
               ...(OPTS.mode === 'b0' ? ['disable-hardware-acceleration'] : [])],
  };
  fs.writeFileSync(path.join(OPTS.outDir, 'truth.json'),
                   JSON.stringify(truth, null, 2));
  const result = {
    ok: true, mode: OPTS.mode, png: pngName,
    png_bytes: png.length, png_sha256: crypto.createHash('sha256').update(png).digest('hex'),
    width: size.width, height: size.height,
    effective_backend: gpuTruth.effective_backend,
    versions, page_device_pixel_ratio: pageReport ? pageReport.devicePixelRatio : null,
    elapsed_ms: Date.now() - startedMs,
  };
  fs.writeFileSync(path.join(OPTS.outDir, 'result.json'), JSON.stringify(result, null, 2));
  process.stdout.write(`VTX_ELECTRON_RESULT ${JSON.stringify(result)}\n`);
  clearTimeout(timer);

  if (OPTS.holdMs > 0) await sleep(OPTS.holdMs);  // 卫生演练：保持全树存活等宿主强杀
  win.destroy();
  app.exit(0);
}

app.whenReady().then(main).catch((e) => fail(1, `主流程异常：${e && e.stack ? e.stack : e}`));
