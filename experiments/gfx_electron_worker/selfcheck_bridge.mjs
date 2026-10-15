// selfcheck_bridge.mjs — 输出桥纯 Node 自检面（issue #113 ELEC-02）。
//
// 消费者：tests/test_elec_output_bridge.py（跨语言锁定：Python 签名/闸门 ↔
// JS 端口 ↔ C++ 助手三面对拍；见 OUTPUT_BRIDGE.md）。无 Electron 依赖——
// Electron 依赖面（通路 2 重建 NativeImage、真实 OSR 捕获）由
// bench_output_bridge.mjs --selfcheck 覆盖。
//
// 子命令（互斥，首个识别者生效）：
//   --sig-dump <dir>            逐帧签名二进制转储：p1sig.bin/p2sig.bin 各
//                                65536×4B（帧号 f → 预乘 BGRA 四元组）——
//                                Python 用 vtx.frame_canary 全域对拍。
//   --gate-file <json> --gate-out <json>
//                                批量闸门：in=[{frame,width,tol1,buf_b64}] →
//                                out=[verdict]（verifyCanary2 镜像裁决）。
//   --header-dump <file>         buildSlotHeader 固定参数二进制 → 助手
//                                --check-header 跨语言偏移锁定。
//   --smoke [--frames=N] [--out-dir=D] [--encoder=E] [--level=L] [--filter=F]
//           [--helper=P] [--threads=T]
//                                JS 合成帧（canary + 确定性内容）走完整桥 →
//                                JSON 摘要（含逐帧回执）。
//   --fixture-smoke <dir>        Python 生成 fixture（frames_bgra.bin +
//                                meta.json，canary 由 vtx/frame_canary.py 签）
//                                走完整桥 → verdicts.json（Python 解 PNG 复核
//                                像素保真 = 跨语言端到端锁）。
import fs from 'node:fs';
import path from 'node:path';
import {
  OutputBridge, verifyCanary2, signatureP1BgraPremul, signatureP2BgraPremul,
  buildSlotHeader, ringGeometry, VIEW_W, VIEW_H,
} from './output_bridge.mjs';

function argValue(name, dflt = null) {
  const i = process.argv.indexOf(name);
  if (i === -1) return dflt;
  const v = process.argv[i + 1];
  return (v === undefined || String(v).startsWith('--')) ? dflt : v;
}

// ---------------------------------------------------------------- 签名转储
function cmdSigDump(dir) {
  fs.mkdirSync(dir, { recursive: true });
  const p1 = Buffer.alloc(0x10000 * 4);
  const p2 = Buffer.alloc(0x10000 * 4);
  for (let f = 0; f <= 0xFFFF; f++) {
    signatureP1BgraPremul(f).forEach((v, k) => { p1[f * 4 + k] = v; });
    signatureP2BgraPremul(f).forEach((v, k) => { p2[f * 4 + k] = v; });
  }
  fs.writeFileSync(path.join(dir, 'p1sig.bin'), p1);
  fs.writeFileSync(path.join(dir, 'p2sig.bin'), p2);
  process.stdout.write(JSON.stringify({ ok: true, frames: 0x10000, bytes: 4 }) + '\n');
}

// ---------------------------------------------------------------- 批量闸门
function cmdGateFile(inPath, outPath) {
  const items = JSON.parse(fs.readFileSync(inPath, 'utf8'));
  const out = items.map((it) => {
    const buf = Buffer.from(it.buf_b64, 'base64');
    const v = verifyCanary2(buf, it.frame, it.tol1 !== false);
    return { accepted: v.accepted, rendered_frame: v.renderedFrame,
             reason: v.reason, exact_match: !!v.exactMatch };
  });
  fs.writeFileSync(outPath, JSON.stringify(out));
  process.stdout.write(JSON.stringify({ ok: true, n: out.length }) + '\n');
}

// ---------------------------------------------------------------- 槽头转储
function cmdHeaderDump(file) {
  const b = buildSlotHeader({
    runId: 0x0102030405060708n, requestId: 0x2468ACE0, frame: 31337,
    width: VIEW_W, height: VIEW_H, tReadyUs: 1234567890, tCaptureUs: 9876543210,
    gen: 7,
  });
  fs.writeFileSync(file, b);
  process.stdout.write(JSON.stringify({ ok: true, bytes: b.length }) + '\n');
}

// ---------------------------------------------------------------- JS 合成帧
// 内容：确定性行模式（alpha=255 不透明 → 逆预乘 = B/R 交换直通，Python 可
// 独立复算抽样像素）。行 0 顶行 = 行 1 邻色（fixture 等值证明）。
function synthFrameInto(buf, width, height, f) {
  const bg = [(f * 3) & 0xFF, (f * 7 + 0x55) & 0xFF, (f * 11 + 0xAA) & 0xFF, 0xFF];
  for (let y = 0; y < height; y++) {
    const ripple = (y ^ (f * 13)) & 0xFF;
    for (let x = 0; x < width; x++) {
      const o = (y * width + x) * 4;
      if (y <= 1) {                       // 顶两行 fixture 等值纯色
        buf[o] = bg[0]; buf[o + 1] = bg[1]; buf[o + 2] = bg[2]; buf[o + 3] = 0xFF;
      } else {
        buf[o] = (x + ripple) & 0xFF;     // B
        buf[o + 1] = (x >> 3) ^ ripple;   // G
        buf[o + 2] = (y + f) & 0xFF;      // R
        buf[o + 3] = 0xFF;                // 不透明：premul == 直通
      }
    }
  }
  const p1 = signatureP1BgraPremul(f);
  const p2 = signatureP2BgraPremul(f);
  for (let k = 0; k < 4; k++) { buf[k] = p1[k]; buf[4 + k] = p2[k]; }
  return { bg };
}

async function runSmoke(frames, outDir, encoder, level, filter, helperPath, threads) {
  fs.mkdirSync(outDir, { recursive: true });
  const geo = ringGeometry();
  const bridge = new OutputBridge({ encoder, level: Number(level), filter,
                                    helperPath, threads: Number(threads) });
  await bridge.start();
  const receipts = [];
  for (let f = 0; f < frames; f++) {
    const buf = Buffer.alloc(geo.frameBytes);
    synthFrameInto(buf, VIEW_W, VIEW_H, f);
    const out = path.join(outDir, `f${String(f).padStart(5, '0')}.png`);
    const r = await bridge.writeFrameBitmap(buf, { frame: f, outPath: out,
      tReadyUs: f * 100, tCaptureUs: f * 200 });
    receipts.push({ frame: f, status: r.status, rendered: r.renderedFrame,
                    png_bytes: r.pngBytes, crc32: r.pngCrc32,
                    t_png_encode_us: r.tPngEncodeUs,
                    t_alpha_norm_us: r.tAlphaNormUs, t_disk_commit_us: r.tDiskCommitUs,
                    canary_match: r.canaryMatch, restore_source: r.restoreSource });
  }
  const m = bridge.metrics;
  await bridge.stop();
  process.stdout.write(JSON.stringify({
    ok: receipts.every((r) => r.status === 'ok'), frames, out_dir: outDir,
    hello_slots: 4, receipts,
    metrics: { frames: m.frames, receipts_ok: m.receiptsOk, receipts_err: m.receiptsErr,
               backpressure_events: m.backpressureEvents,
               backpressure_wait_ms_total: Math.round(m.backpressureWaitMsTotal * 1000) / 1000,
               helper_exit: m.helperExit },
  }) + '\n');
}

// ---------------------------------------------------------------- fixture 冒烟
async function runFixtureSmoke(dir, encoder, level, filter, helperPath, threads) {
  const meta = JSON.parse(fs.readFileSync(path.join(dir, 'meta.json'), 'utf8'));
  const bins = fs.readFileSync(path.join(dir, 'frames_bgra.bin'));
  const geo = ringGeometry(meta.width, meta.height);
  const bridge = new OutputBridge({ width: meta.width, height: meta.height,
                                    encoder, level: Number(level), filter,
                                    tol1: meta.tol1 !== false,
                                    helperPath, threads: Number(threads) });
  await bridge.start();
  const out = [];
  for (const it of meta.frames) {
    const buf = bins.subarray(it.offset, it.offset + geo.frameBytes);
    let rec = null, errCode = null, errMsg = null;
    try {
      rec = await bridge.writeFrameBitmap(buf, {
        frame: it.frame, outPath: path.join(dir, it.out),
        orig1: it.orig1 || null, orig2: it.orig2 || null,
      });
    } catch (e) {                    // canary 拒绝等按帧显形（不中断批次）
      errCode = e.code || 'error'; errMsg = String(e.message || e);
      rec = e.receipt || {};
    }
    out.push({ frame: it.frame, out: it.out, status: rec.status || errCode,
               rendered: rec.renderedFrame, reason: rec.reason || errMsg,
               png_bytes: rec.pngBytes, crc32: rec.pngCrc32,
               canary_match: rec.canaryMatch, restore_source: rec.restoreSource,
               t_png_encode_us: rec.tPngEncodeUs });
  }
  const m = bridge.metrics;
  await bridge.stop();
  fs.writeFileSync(path.join(dir, 'verdicts.json'), JSON.stringify({
    ok: out.every((r) => r.status === 'ok'), frames: out.length, verdicts: out,
    metrics: { backpressure_events: m.backpressureEvents,
               backpressure_wait_ms_total: Math.round(m.backpressureWaitMsTotal * 1000) / 1000,
               helper_exit: m.helperExit },
  }, null, 1));
  process.stdout.write(JSON.stringify({ ok: true, n: out.length }) + '\n');
}

// ---------------------------------------------------------------- main
const argv = process.argv.slice(2);
try {
  if (argv.includes('--sig-dump')) {
    cmdSigDump(argValue('--sig-dump'));
  } else if (argv.includes('--gate-file')) {
    cmdGateFile(argValue('--gate-file'), argValue('--gate-out'));
  } else if (argv.includes('--header-dump')) {
    cmdHeaderDump(argValue('--header-dump'));
  } else if (argv.includes('--fixture-smoke')) {
    await runFixtureSmoke(argValue('--fixture-smoke'),
      argValue('--encoder', 'spng'), argValue('--level', '1'),
      argValue('--filter', 'sub_up'), argValue('--helper'),
      argValue('--threads', '4'));
  } else {
    await runSmoke(Number(argValue('--frames', '6')), argValue('--out-dir', '/tmp/vtx-elec-selfcheck'),
      argValue('--encoder', 'spng'), argValue('--level', '1'),
      argValue('--filter', 'sub_up'), argValue('--helper'),
      argValue('--threads', '4'));
  }
} catch (e) {
  process.stderr.write(`✗ selfcheck_bridge：${e && e.stack ? e.stack : e}\n`);
  process.exit(1);
}
