// output_bridge.mjs — Electron NativeImage 双路径输出桥（issue #113 ELEC-02）。
//
// 通路 1 bitmap_shared_encoder（核心同口径对照，Electron 无关、纯 Node 可用）：
//   image.toBitmap()（预乘 BGRA 副本）→ /dev/shm 4-Slot POSIX 共享内存环
//   （#110 shm_ring.hpp 布局同构；Node 侧 ftruncate+pwrite，助手侧 mmap，
//   双方 open 后立即 unlink 零残留）→ 原生助手 vtx_elec_output_helper
//   （复用 #111 pixel_ops/png_writer 源码编译）在线程池执行：
//   v2 双像素 canary 验证与恢复 → SIMD 逆预乘 → spng/fpng/zlib 编码 →
//   .tmp.<pid>.<tid> + rename 原子落盘 → 定长二进制回执（计时/体积/crc32/
//   canary 归因）。流控 = socket 信用（credit）：在途未落盘帧 ≤ slots=4
//   （MAX_INFLIGHT_FRAMES），无信用即背压等待（不分配新内存）。
//
// 通路 2 electron_native_topng（原生快路径对照，需 Electron nativeImage）：
//   toBitmap() 副本上 v2 双像素 canary 验证 + 恢复 →
//   nativeImage.createFromBitmap(恢复后像素) **明确重建** NativeImage →
//   .toPNG()（同步编码，计时留在主线程成本）→ worker_threads 池异步
//   .tmp+rename 落盘（只搬写盘，不搬编码）。修改 toBitmap 副本不等于修改
//   原 image——本通路绝不直接编码旧 image（见 encodeTopngPath2）。
//
// 废弃 API 纪律：全程只用 image.toBitmap()（#113 强校验：非空、1920x1080、
// byteLength === width*height*4），严禁 getBitmap()。
//
// 协议权威：experiments/gfx_electron_worker/OUTPUT_BRIDGE.md +
// native/src/elec_output_protocol.hpp（偏移静态断言；本文件的打包器为其
// Node 镜像，tests/test_elec_output_bridge.py 跨语言锁定）。
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { Worker } from 'node:worker_threads';
import { fileURLToPath } from 'node:url';
import { monitorEventLoopDelay } from 'node:perf_hooks';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const DEFAULT_HELPER_PATH = path.join(HERE, 'native', 'build', 'bin',
                                             'vtx_elec_output_helper');

// ---- 工单冻结常量 ----
export const MAX_INFLIGHT_FRAMES = 4;   // 有界在途背压（= 环槽数）
export const RING_SLOTS = 4;
export const VIEW_W = 1920;
export const VIEW_H = 1080;

// ---- #110 环布局常量（shm_ring.hpp 镜像）----
const RING_CTRL_BYTES = 384;
const SLOT_HEADER_BYTES = 128;
const RING_MAGIC = 0x56545852;          // "VTXR"
const SLOT_MAGIC = 0x56545846;          // "VTXF"
const PROTO_VERSION = 1;
const align64 = (v) => (v + 63) & ~63;

export function ringGeometry(width = VIEW_W, height = VIEW_H, slots = RING_SLOTS) {
  const frameBytes = width * height * 4;
  const slotStride = align64(SLOT_HEADER_BYTES + frameBytes);
  return { frameBytes, slotStride, controlBytes: RING_CTRL_BYTES,
           totalBytes: RING_CTRL_BYTES + slotStride * slots,
           slotOffset: (i) => RING_CTRL_BYTES + slotStride * i,
           dataOffset: (i) => RING_CTRL_BYTES + slotStride * i + SLOT_HEADER_BYTES };
}

// ---------------------------------------------------------------- canary v2（JS 端口）
// 公式镜像 vtx/frame_canary.py（唯一 home）；native/src/canary_v2.cpp 为
// C++ 同源端口——三者由 tests 穷举对拍锁定。
export const CANARY2 = {
  xorKey: 0x5A, addKey2: 0x3C, mulR: 16, mulG: 22, alpha: 254, frameMax: 0xFFFF,
};

function premulRound(c) { return Math.floor((508 * c + 255) / 510); }

// 精确反像闭区间 [lo,hi]；null = 空集。
function invExact(v) {
  if (v < 0 || v > 255) return null;
  const lo = Math.ceil((510 * v - 255) / 508);
  const hi = Math.ceil((510 * v + 255) / 508) - 1;
  const clo = Math.max(lo, 0), chi = Math.min(hi, 255);
  return clo > chi ? null : [clo, chi];
}

function invTol1(v) {
  let lo = 256, hi = -1;
  for (const w of [v - 1, v, v + 1]) {
    const r = invExact(w);
    if (r) { lo = Math.min(lo, r[0]); hi = Math.max(hi, r[1]); }
  }
  return lo > hi ? null : [lo, hi];
}

function decodeCandidates(px, tol1, isP2) {
  // px = BGRA 内存四元组切片 [B,G,R,A]。
  const rb = (tol1 ? invTol1 : invExact)(px[0]);
  const rg = (tol1 ? invTol1 : invExact)(px[1]);
  const rr = (tol1 ? invTol1 : invExact)(px[2]);
  const out = new Set();
  if (!rb || !rg || !rr) return out;
  for (let b = rb[0]; b <= rb[1]; b++) {
    for (let g = rg[0]; g <= rg[1]; g++) {
      for (let r = rr[0]; r <= rr[1]; r++) {
        if (isP2) {
          if (b === ((CANARY2.mulR * r + CANARY2.mulG * g + CANARY2.addKey2) & 0xFF)) {
            out.add(((g << 8) | r) & 0xFFFF);
          }
        } else {
          if (b === ((r ^ g ^ CANARY2.xorKey) & 0xFF)) out.add(((r << 8) | g) & 0xFFFF);
        }
      }
    }
  }
  return out;
}

export function signatureP1BgraPremul(f) {
  const hi = (f >> 8) & 0xFF, lo = f & 0xFF;
  return [premulRound((hi ^ lo ^ CANARY2.xorKey) & 0xFF), premulRound(lo),
          premulRound(hi), CANARY2.alpha];
}

export function signatureP2BgraPremul(f) {
  const hi = (f >> 8) & 0xFF, lo = f & 0xFF;
  return [premulRound((CANARY2.mulR * lo + CANARY2.mulG * hi + CANARY2.addKey2) & 0xFF),
          premulRound(hi), premulRound(lo), CANARY2.alpha];
}

// 双像素闸门（BGRA 通路；accepted ⟺ S1∩S2 == {expected}）。
export function verifyCanary2(buf, expectedFrame, tol1 = true) {
  const s1 = decodeCandidates(buf.subarray(0, 4), tol1, false);
  const s2 = decodeCandidates(buf.subarray(4, 8), tol1, true);
  if (s1.size === 0 || s2.size === 0) {
    return { accepted: false, renderedFrame: null, reason: 'no_signature' };
  }
  const inter = [...s1].filter((f) => s2.has(f));
  const e1 = signatureP1BgraPremul(expectedFrame);
  const e2 = signatureP2BgraPremul(expectedFrame);
  const exactMatch = buf[0] === e1[0] && buf[1] === e1[1] && buf[2] === e1[2] &&
                     buf[3] === e1[3] && buf[4] === e2[0] && buf[5] === e2[1] &&
                     buf[6] === e2[2] && buf[7] === e2[3];
  if (inter.length === 1) {
    if (inter[0] === expectedFrame) {
      return { accepted: true, renderedFrame: inter[0], reason: 'ok', exactMatch };
    }
    return { accepted: false, renderedFrame: inter[0], reason: `stale_paint:rendered=${inter[0]}` };
  }
  if (inter.length === 0) {
    return { accepted: false, renderedFrame: null, reason: 'attribution_incompatible' };
  }
  return { accepted: false, renderedFrame: null, reason: 'attribution_ambiguous' };
}

// 双像素原地恢复（(0,0)/(0,1) ← 宿主上报原像素；缺省行 1 邻色——仅当
// fixture 证明顶行与行 1 等值时精确）。返回恢复源。
export function restoreCanary2Inplace(buf, width, orig1 = null, orig2 = null) {
  if (orig1 && orig2) {
    buf[0] = orig1[0]; buf[1] = orig1[1]; buf[2] = orig1[2]; buf[3] = orig1[3];
    buf[4] = orig2[0]; buf[5] = orig2[1]; buf[6] = orig2[2]; buf[7] = orig2[3];
    return 'reported_original';
  }
  const r1 = width * 4, r2 = width * 4 + 4;
  for (let k = 0; k < 4; k++) { buf[k] = buf[r1 + k]; buf[4 + k] = buf[r2 + k]; }
  return 'neighbor_row1';
}

// ---------------------------------------------------------------- 监控面
export class EventLoopDelayMonitor {
  constructor(resolutionMs = 10) {   // 工单口径：resolution 10
    this._h = monitorEventLoopDelay({ resolution: resolutionMs });
    this.enabled = false;
  }
  start() { this._h.reset(); this._h.enable(); this.enabled = true; }
  stop() { this._h.disable(); this.enabled = false; }
  summary() {
    if (!this.enabled && this._h.mean === 0) return null;
    return {
      mean_ms: this._h.mean / 1e6,
      p50_ms: this._h.percentile(50) / 1e6,
      p95_ms: this._h.percentile(95) / 1e6,
      p99_ms: this._h.percentile(99) / 1e6,
      max_ms: this._h.max / 1e6,
      stddev_ms: this._h.stddev / 1e6,
    };
  }
}

export function memorySample() {
  const m = process.memoryUsage();
  return { external: m.external, arrayBuffers: m.arrayBuffers, rss: m.rss,
           heapUsed: m.heapUsed, at: Date.now() };
}

export function memorySummary(samples) {
  if (!samples.length) return null;
  const ext = samples.map((s) => s.external);
  const ab = samples.map((s) => s.arrayBuffers);
  const rss = samples.map((s) => s.rss);
  const minmax = (a) => ({ min: Math.min(...a), max: Math.max(...a),
                           fluctuation: Math.max(...a) - Math.min(...a) });
  // 泄漏斜率：首尾差 / 帧数（B/frame；平稳 ≈ 0）。
  const slope = (a) => (a[a.length - 1] - a[0]) / Math.max(1, a.length);
  return { external: { ...minmax(ext), slope_per_frame: slope(ext), first: ext[0],
                       last: ext[ext.length - 1] },
           arrayBuffers: { ...minmax(ab), slope_per_frame: slope(ab) },
           rss: { ...minmax(rss), slope_per_frame: slope(rss) } };
}

// ---------------------------------------------------------------- 协议打包（Node 镜像）
const encoders = { spng: 0, fpng: 1, zlib: 2 };
const filters = { none: 8, sub: 16, up: 32, sub_up: 48, all: 248 };

function encodeNameId(name) {
  if (!(name in encoders)) throw new TypeError(`未知编码器：${name}（合法：${Object.keys(encoders)}）`);
  return encoders[name];
}

function filterNameId(name) {
  if (!(name in filters)) throw new TypeError(`未知滤波：${name}（合法：${Object.keys(filters)}）`);
  return filters[name];
}

function buildRingControl(geo, runId, slots) {
  const b = Buffer.alloc(RING_CTRL_BYTES);
  b.writeUInt32LE(0, 0);                       // layout_ready：最后单独发布
  b.writeUInt32LE(RING_MAGIC, 4);
  b.writeUInt16LE(PROTO_VERSION, 8);
  b.writeUInt16LE(slots, 10);
  b.writeUInt32LE(geo.width, 12);
  b.writeUInt32LE(geo.height, 16);
  b.writeUInt32LE(geo.frameBytes, 20);
  b.writeUInt32LE(geo.slotStride, 24);
  b.writeBigUInt64LE(runId, 32);
  b.writeInt32LE(process.pid, 40);
  b.writeBigUInt64LE(0n, 48);                  // published_total（诊断面）
  b.writeBigUInt64LE(0n, 56);                  // released_total
  b.writeBigUInt64LE(0n, 64);                  // backpressure_wait_us_total
  return b;
}

// 128B 槽头（FrameSlotHeader 布局镜像；tests 用 --check-header 跨语言锁定）。
export function buildSlotHeader({ runId, requestId, frame, width, height,
                                  tReadyUs = 0, tCaptureUs = 0, gen = 0 }) {
  const b = Buffer.alloc(SLOT_HEADER_BYTES);
  b.writeUInt32LE(SLOT_MAGIC, 0);
  b.writeUInt16LE(PROTO_VERSION, 4);
  b.writeUInt16LE(0, 6);                       // status_code（生产侧 0）
  b.writeBigUInt64LE(runId, 8);
  b.writeUInt32LE(gen, 16);
  b.writeUInt32LE(requestId, 20);
  b.writeInt32LE(frame, 24);
  b.writeInt32LE(-1, 28);                      // canary_frame_index（消费侧填写）
  b.writeUInt32LE(width, 32);
  b.writeUInt32LE(height, 36);
  b.writeUInt32LE(width * 4, 40);              // stride：紧凑
  b.writeUInt8(1, 44);                         // pixel_format = BGRA_PREMUL
  b.writeUInt8(1, 45);                         // color_space = sRGB
  b.writeUInt16LE(0, 46);                      // rejected_paints
  b.writeBigUInt64LE(BigInt(Math.round(tReadyUs)), 48);
  b.writeBigUInt64LE(BigInt(Math.round(tCaptureUs)), 56);
  return b;
}

function packRequest(r) {
  const b = Buffer.alloc(296);
  b.writeUInt32LE(0x56584F52, 0);              // "VXOR"
  b.writeUInt16LE(PROTO_VERSION, 4);
  b.writeUInt32LE(r.id, 8);
  b.writeUInt32LE(r.slot, 12);
  b.writeInt32LE(r.frame, 16);
  b.writeUInt32LE(r.gen >>> 0, 20);
  b.writeUInt32LE(r.width, 24);
  b.writeUInt32LE(r.height, 28);
  b.writeBigUInt64LE(r.runId, 32);
  b.writeUInt8(encodeNameId(r.encoder), 40);
  b.writeUInt8(r.level, 41);
  b.writeUInt8(filterNameId(r.filter), 42);
  b.writeUInt8(r.tol1 ? 1 : 0, 43);
  b.writeUInt8(r.orig1 && r.orig2 ? 1 : 0, 44);
  if (r.orig1 && r.orig2) {
    Buffer.from(r.orig1).copy(b, 48);
    Buffer.from(r.orig2).copy(b, 52);
  }
  b.writeBigUInt64LE(BigInt(Math.round(r.tReadyUs || 0)), 56);
  b.writeBigUInt64LE(BigInt(Math.round(r.tCaptureUs || 0)), 64);
  b.write(r.outPath, 72, 223, 'utf8');
  if (Buffer.byteLength(r.outPath, 'utf8') > 223) {
    throw new RangeError(`outPath 超过 223 字节：${r.outPath}`);
  }
  return b;
}

function unpackDone(b) {
  return {
    magic: b.readUInt32LE(0), protocolVersion: b.readUInt16LE(4),
    statusCode: b.readUInt16LE(6), id: b.readUInt32LE(8), slot: b.readUInt32LE(12),
    frame: b.readInt32LE(16), renderedFrame: b.readInt32LE(20),
    reasonCode: b.readUInt32LE(24), canaryMatch: b.readUInt8(28),
    restoreSource: b.readUInt8(29),
    tReadyWaitUs: Number(b.readBigUInt64LE(40)),
    tCaptureRawUs: Number(b.readBigUInt64LE(48)),
    tDecodeUs: Number(b.readBigUInt64LE(56)),
    tRestoreUs: Number(b.readBigUInt64LE(64)),
    tAlphaNormUs: Number(b.readBigUInt64LE(72)),
    tPngEncodeUs: Number(b.readBigUInt64LE(80)),
    tDiskCommitUs: Number(b.readBigUInt64LE(88)),
    pngBytes: Number(b.readBigUInt64LE(96)), pngCrc32: b.readUInt32LE(104),
  };
}

export const OUT_STATUS = {
  0: 'ok', 1: 'canary_reject', 2: 'header_invalid', 3: 'encode_error',
  4: 'io_error', 5: 'bad_request',
};
export const OUT_REASON = {
  0: 'ok', 1: 'stale_rendered', 2: 'no_signature', 3: 'incompatible',
  4: 'ambiguous', 5: 'twin', 0xFFFFFFFF: 'na',
};

// ---------------------------------------------------------------- OutputBridge（通路 1）
export class OutputBridge {
  // opts: { helperPath, width, height, slots, threads, encoder, level, filter,
  //         tol1, idleTimeoutMs, runDir, logger }
  constructor(opts = {}) {
    this.helperPath = opts.helperPath || DEFAULT_HELPER_PATH;
    this.width = opts.width || VIEW_W;
    this.height = opts.height || VIEW_H;
    this.slots = opts.slots || RING_SLOTS;
    this.threads = opts.threads ?? 4;
    this.encoder = opts.encoder || 'spng';
    this.level = opts.level ?? 1;
    this.filter = opts.filter || 'sub_up';
    this.tol1 = opts.tol1 !== false;
    this.idleTimeoutMs = opts.idleTimeoutMs ?? 120000;
    this.logger = opts.logger || (() => {});
    this.geo = ringGeometry(this.width, this.height, this.slots);
    if (this.slots !== MAX_INFLIGHT_FRAMES) {
      // 工单冻结 MAX_INFLIGHT_FRAMES=4；槽数即界。改槽数=改界，需工单变更。
      throw new RangeError(`slots 必须为 ${MAX_INFLIGHT_FRAMES}（工单冻结 MAX_INFLIGHT_FRAMES）`);
    }
    this.runId = BigInt(Date.now()) * 4096n + BigInt(process.pid & 0xFFF);
    this._freeSlots = [];
    this._creditWaiters = [];
    this._pending = new Map();      // id → {resolve, reject, slot, frame, t0}
    this._nextId = 1;
    this._sock = null;
    this._proc = null;
    this._rxBuf = Buffer.alloc(0);
    this._hello = null;
    this._dead = null;              // Error（助手死亡/协议断裂）
    this.metrics = {
      frames: 0, receiptsOk: 0, receiptsErr: 0, backpressureEvents: 0,
      backpressureWaitMsTotal: 0, tWriteUs: [], tSendUs: [],
      memSamples: [], helperExit: null, canaryRejects: 0, maxInflight: 0,
    };
    this.started = false;
    this.closed = false;
  }

  get inflight() { return this._pending.size; }

  _fail(err) {
    if (this._dead) return;
    this._dead = err;
    for (const w of this._creditWaiters.splice(0)) w.reject(err);
    for (const [, p] of this._pending) p.reject(err);
    this._pending.clear();
    this._freeSlots.length = 0;
  }

  _feedRx(chunk) {
    this._rxBuf = this._rxBuf.length === 0 ? chunk : Buffer.concat([this._rxBuf, chunk]);
    for (;;) {
      if (this._rxBuf.length < 4) return;
      const magic = this._rxBuf.readUInt32LE(0);
      let size = 0;
      if (magic === 0x56584F48) size = 64;        // Hello
      else if (magic === 0x56584F43) size = 16;   // Credit
      else if (magic === 0x56584F44) size = 144;  // Done
      else { this._fail(new Error(`协议断裂：未知 magic 0x${magic.toString(16)}`)); return; }
      if (this._rxBuf.length < size) return;
      const msg = this._rxBuf.subarray(0, size);
      this._rxBuf = this._rxBuf.subarray(size);
      if (magic === 0x56584F48) {
        this._hello = {
          pid: msg.readInt32LE(8), slots: msg.readUInt32LE(12),
          width: msg.readUInt32LE(16), height: msg.readUInt32LE(20),
          shmBytes: Number(msg.readBigUInt64LE(24)), runId: msg.readBigUInt64LE(32),
          unpremulBackend: msg.readUInt8(40),
        };
        if (this._hello.runId !== this.runId ||
            this._hello.slots !== this.slots ||
            this._hello.width !== this.width || this._hello.height !== this.height ||
            this._hello.shmBytes !== this.geo.totalBytes) {
          this._fail(new Error(`Hello 不符：${JSON.stringify(this._hello)}`));
          return;
        }
      } else if (magic === 0x56584F43) {
        const slot = msg.readUInt32LE(8), seq = msg.readUInt32LE(12);
        this._freeSlots.push({ slot, seq });
        const w = this._creditWaiters.shift();
        if (w) w.resolve();
      } else {
        const done = unpackDone(msg);
        const p = this._pending.get(done.id);
        if (p) {
          this._pending.delete(done.id);
          if (done.statusCode === 0) {            this.metrics.receiptsOk++;
            p.resolve({ ...done, status: 'ok', reason: OUT_REASON[done.reasonCode] || String(done.reasonCode) });
          } else {
            this.metrics.receiptsErr++;
            if (done.statusCode === 1) this.metrics.canaryRejects++;
            const err = new Error(
              `输出助手回执错误 status=${OUT_STATUS[done.statusCode] || done.statusCode}` +
              ` reason=${OUT_REASON[done.reasonCode] || done.reasonCode}` +
              ` rendered=${done.renderedFrame} frame=${done.frame}`);
            err.code = OUT_STATUS[done.statusCode];
            err.receipt = done;
            p.reject(err);
          }
        }
      }
    }
  }

  async start() {
    if (this.started) throw new Error('bridge 已启动');
    this.runDir = fs.mkdtempSync(path.join(os.tmpdir(), 'vtx-elec-out-'));
    this.shmPath = `/dev/shm/vtx_elec_out_${process.pid}_${Date.now().toString(36)}`;
    this.sockPath = path.join(this.runDir, 'bridge.sock');

    // SHM 环创建（tmpfs 文件；助手 open 后立即 unlink 零残留）。
    this.shmFd = fs.openSync(this.shmPath, 'w+');
    fs.ftruncateSync(this.shmFd, this.geo.totalBytes);
    fs.writeSync(this.shmFd, buildRingControl({ ...this.geo, width: this.width, height: this.height },
                                              this.runId, this.slots), 0,
                 RING_CTRL_BYTES, 0);
    const ready = Buffer.alloc(4);
    ready.writeUInt32LE(RING_MAGIC, 0);
    fs.writeSync(this.shmFd, ready, 0, 4, 0);    // layout_ready 最后发布

    // Unix socket server（助手 connect）。
    await new Promise((res, rej) => {
      this._server = net.createServer((sock) => {
        if (this._sock) { sock.destroy(); return; }  // 单宿主契约
        this._sock = sock;
        sock.on('data', (c) => this._feedRx(c));
        sock.on('error', () => this._fail(new Error('bridge socket 错误')));
        sock.on('close', () => this._fail(new Error('输出助手断连（socket close）')));
      });
      this._server.on('error', rej);
      this._server.listen(this.sockPath, () => res());
    });

    // 拉起助手。
    this._proc = spawn(this.helperPath, [
      `--shm=${this.shmPath}`, `--sock=${this.sockPath}`,
      `--slots=${this.slots}`, `--width=${this.width}`, `--height=${this.height}`,
      `--threads=${this.threads}`, `--idle-timeout-ms=${this.idleTimeoutMs}`,
    ], { stdio: ['ignore', 'pipe', 'pipe'] });
    let stderrTail = '';
    this._proc.stderr.on('data', (c) => { stderrTail = (stderrTail + c).slice(-2000); });
    this._proc.on('exit', (code, signal) => {
      this.metrics.helperExit = { code, signal, stderrTail };
      this._fail(new Error(`输出助手退出 code=${code} signal=${signal} stderr=${stderrTail.slice(-400)}`));
    });

    // 等 Hello + 全部初始 credit（或死亡）。
    const t0 = Date.now();
    while (!(this._hello && this._freeSlots.length >= this.slots)) {
      if (this._dead) throw this._dead;
      if (Date.now() - t0 > 15000) throw new Error('等待助手 Hello/credit 超时');
      await new Promise((r) => setTimeout(r, 5));
    }
    this.started = true;
    return this;
  }

  async _acquireCredit() {
    if (this._dead) throw this._dead;
    if (this._freeSlots.length > 0) return;
    const t0 = process.hrtime.bigint();
    this.metrics.backpressureEvents++;
    await new Promise((resolve, reject) => {
      this._creditWaiters.push({ resolve, reject });
    });
    this.metrics.backpressureWaitMsTotal +=
      Number(process.hrtime.bigint() - t0) / 1e6;
  }

  // NativeImage 契约强校验 + toBitmap（工单口径；严禁 getBitmap）。
  static assertNativeImage(image, width, height) {
    if (typeof image.isEmpty !== 'function' ||
        typeof image.getSize !== 'function' ||
        typeof image.toBitmap !== 'function') {
      throw new TypeError('image 不是 Electron NativeImage（缺 isEmpty/getSize/toBitmap）');
    }
    if (image.isEmpty()) throw new Error('NativeImage 契约失败：isEmpty() === true');
    const size = image.getSize();
    if (size.width !== width || size.height !== height) {
      throw new Error(`NativeImage 契约失败：getSize()=${size.width}x${size.height}` +
                      ` ≠ ${width}x${height}`);
    }
  }

  // 通路 1 写帧（NativeImage 入口）。
  async writeFrameFromImage(image, meta) {
    OutputBridge.assertNativeImage(image, this.width, this.height);
    const t0 = process.hrtime.bigint();
    const bitmap = image.toBitmap();            // 预乘 BGRA 副本（唯一取像素面）
    const tToBitmapMs = Number(process.hrtime.bigint() - t0) / 1e6;
    if (bitmap.byteLength !== this.geo.frameBytes) {
      throw new Error(`NativeImage 契约失败：toBitmap().byteLength=${bitmap.byteLength}` +
                      ` ≠ width*height*4=${this.geo.frameBytes}`);
    }
    const r = await this.writeFrameBitmap(bitmap, { ...meta, tToBitmapMs });
    return { ...r, tToBitmapMs };
  }

  // 通路 1 写帧（合成位图入口；单测/非 Electron 环境）。
  async writeFrameBitmap(bitmap, meta) {
    if (this.closed) throw new Error('bridge 已关闭');
    if (this._dead) throw this._dead;
    if (!Number.isInteger(meta.frame) || meta.frame < 0 || meta.frame > 0xFFFF) {
      throw new RangeError(`frame 超出 16-bit 域：${meta.frame}`);
    }
    if (bitmap.byteLength !== this.geo.frameBytes) {
      throw new Error(`bitmap 字节数 ${bitmap.byteLength} ≠ ${this.geo.frameBytes}`);
    }
    await this._acquireCredit();
    const { slot } = this._freeSlots.pop();
    const id = this._nextId++;
    const outPath = path.resolve(meta.outPath);
    const header = buildSlotHeader({
      runId: this.runId, requestId: id, frame: meta.frame,
      width: this.width, height: this.height,
      tReadyUs: meta.tReadyUs || 0, tCaptureUs: meta.tCaptureUs || 0, gen: meta.gen || 0,
    });
    // pwrite 槽头 + 像素（tmpfs → 助手 mmap 同页一致；REQ 后发保证次序）。
    const t0 = process.hrtime.bigint();
    fs.writeSync(this.shmFd, header, 0, SLOT_HEADER_BYTES, this.geo.slotOffset(slot));
    fs.writeSync(this.shmFd, bitmap, 0, this.geo.frameBytes, this.geo.dataOffset(slot));
    const tWriteUs = Number(process.hrtime.bigint() - t0) / 1e3;
    // 门 READY（诊断面；权威流控=credit）。
    const gate = Buffer.alloc(4);
    gate.writeUInt32LE(2, 0);
    fs.writeSync(this.shmFd, gate, 0, 4, 128 + slot * 64);

    const req = {
      id, slot, frame: meta.frame, gen: meta.gen || 0, width: this.width,
      height: this.height, runId: this.runId, encoder: meta.encoder || this.encoder,
      level: meta.level ?? this.level, filter: meta.filter || this.filter,
      tol1: this.tol1, orig1: meta.orig1 || null, orig2: meta.orig2 || null,
      tReadyUs: meta.tReadyUs || 0, tCaptureUs: meta.tCaptureUs || 0, outPath,
    };
    const t1 = process.hrtime.bigint();
    await new Promise((resolve, reject) => {
      this._sock.write(packRequest(req), (err) => (err ? reject(err) : resolve()));
    });
    const tSendUs = Number(process.hrtime.bigint() - t1) / 1e3;
    this.metrics.tWriteUs.push(tWriteUs);
    this.metrics.tSendUs.push(tSendUs);
    this.metrics.frames++;
    this.metrics.memSamples.push(memorySample());

    const tSubmit = process.hrtime.bigint();
    try {
      const receipt = await new Promise((resolve, reject) => {
        this._pending.set(id, { resolve, reject, slot, frame: meta.frame });
        this.metrics.maxInflight = Math.max(this.metrics.maxInflight, this._pending.size);
      });
      receipt.tSubmitMs = Number(process.hrtime.bigint() - tSubmit) / 1e6;
      return receipt;
    } catch (e) {
      throw e;
    }
  }

  async stop({ timeoutMs = 10000 } = {}) {
    if (this.closed) return;
    this.closed = true;
    // EOF = 优雅关闭：助手排空在途后退出 0。
    if (this._sock) this._sock.destroy();
    if (this._server) {
      await new Promise((r) => this._server.close(r));
      try { fs.unlinkSync(this.sockPath); } catch { /* 已随 close 消失 */ }
    }
    if (this._proc) {
      const t0 = Date.now();
      while (this._proc.exitCode === null && this._proc.signalCode === null) {
        if (Date.now() - t0 > timeoutMs) {
          this._proc.kill('SIGKILL');
          break;
        }
        await new Promise((r) => setTimeout(r, 20));
      }
    }
    if (this.shmFd !== undefined) { try { fs.closeSync(this.shmFd); } catch { /* - */ } }
    try { fs.rmSync(this.runDir, { recursive: true, force: true }); } catch { /* - */ }
  }
}

// ---------------------------------------------------------------- 通路 2
// worker_threads 落盘池（只搬写盘：.tmp.<pid>.<tid> + fs.promises.rename；
// PNG 编码在主线程同步完成，绝不进 worker）。
const WORKER_SRC = `
const { parentPort } = require('node:worker_threads');
const fs = require('node:fs');
parentPort.on('message', (m) => {
  Promise.resolve().then(async () => {
    const tmp = m.finalPath + '.tmp.' + process.pid + '.' + process.threadId;
    await fs.promises.writeFile(tmp, Buffer.from(m.png));
    await fs.promises.rename(tmp, m.finalPath);
    parentPort.postMessage({ id: m.id, ok: true });
  }).catch((e) => parentPort.postMessage({ id: m.id, ok: false, error: String(e) }));
});
`;

export class DiskCommitPool {
  constructor({ workers = 4, maxInflight = MAX_INFLIGHT_FRAMES } = {}) {
    this.maxInflight = maxInflight;
    this._workers = [];
    this._next = 0;
    this._nextId = 1;
    this._pending = new Map();
    this._drainWaiters = [];
    this.closed = false;
    for (let i = 0; i < workers; i++) {
      const w = new Worker(WORKER_SRC, { eval: true });
      w.on('message', (m) => {
        const p = this._pending.get(m.id);
        if (p) {
          this._pending.delete(m.id);
          m.ok ? p.resolve() : p.reject(new Error(`worker 落盘失败：${m.error}`));
        }
        if (this._pending.size < this.maxInflight) {
          const d = this._drainWaiters.shift();
          if (d) d();
        }
      });
      w.unref();
      this._workers.push(w);
    }
  }

  get inflight() { return this._pending.size; }

  async submit(pngBuffer, finalPath) {
    if (this.closed) throw new Error('pool 已关闭');
    if (this._pending.size >= this.maxInflight) {
      await new Promise((r) => this._drainWaiters.push(r));   // 有界背压
    }
    const id = this._nextId++;
    const w = this._workers[this._next++ % this._workers.length];
    const msg = { id, finalPath: path.resolve(finalPath),
                  png: pngBuffer.buffer.slice(pngBuffer.byteOffset,
                                              pngBuffer.byteOffset + pngBuffer.byteLength) };
    const transfer = [msg.png];
    await new Promise((resolve, reject) => {
      this._pending.set(id, { resolve, reject });
      this.maxInflight = Math.max(this.maxInflight || 0, this._pending.size);
      w.postMessage(msg, transfer);
    });
  }

  async stop() {
    this.closed = true;
    while (this._pending.size > 0) await new Promise((r) => setTimeout(r, 10));
    await Promise.all(this._workers.map((w) => w.terminate()));
  }
}

// 通路 2：canary 验证+恢复 → 重建 NativeImage → toPNG（主线程同步）→
// worker 异步落盘。「修改 toBitmap 副本 ≠ 修改原 image」：本函数从不编码
// 传入的原 image；toPNG 只作用于 createFromBitmap(恢复后像素) 的新实例。
export async function encodeTopngPath2(image, {
  frame, outPath, pool, width = VIEW_W, height = VIEW_H,
  orig1 = null, orig2 = null, tol1 = true, gen = 0,
}) {
  OutputBridge.assertNativeImage(image, width, height);
  const tB0 = process.hrtime.bigint();
  const bitmap = image.toBitmap();
  const tToBitmapMs = Number(process.hrtime.bigint() - tB0) / 1e6;
  if (bitmap.byteLength !== width * height * 4) {
    throw new Error(`NativeImage 契约失败：toBitmap().byteLength=${bitmap.byteLength}` +
                    ` ≠ ${width * height * 4}`);
  }
  const gate = verifyCanary2(bitmap, frame, tol1);
  if (!gate.accepted) {
    const err = new Error(`通路 2 canary 闸门拒绝：${gate.reason}` +
                          `（rendered=${gate.renderedFrame}，期望 ${frame}）`);
    err.code = 'canary_reject';
    err.gate = gate;
    throw err;
  }
  const restoreSource = restoreCanary2Inplace(bitmap, width, orig1, orig2);
  const { nativeImage } = await import('electron');
  const tR0 = process.hrtime.bigint();
  const rebuilt = nativeImage.createFromBitmap(bitmap, { width, height });
  const tRebuildMs = Number(process.hrtime.bigint() - tR0) / 1e6;
  // 重建自证：新 image 的 toBitmap 前 8 字节 == 恢复后像素（证明重建生效）。
  const rb = rebuilt.toBitmap();
  let rebuildEchoOk = true;
  for (let k = 0; k < 8; k++) if (rb[k] !== bitmap[k]) { rebuildEchoOk = false; break; }
  const t0 = process.hrtime.bigint();
  const png = rebuilt.toPNG();                 // 同步编码：主线程成本（工单口径）
  const tPngEncodeMs = Number(process.hrtime.bigint() - t0) / 1e6;
  const pngBytes = png.byteLength;
  await pool.submit(png, outPath);
  return { frame, gen, tToBitmapMs, tPngEncodeMs, tRebuildMs, pngBytes,
           restoreSource, canaryExact: gate.exactMatch, rebuildEchoOk,
           outPath: path.resolve(outPath) };
}
