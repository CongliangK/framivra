// Decode only the RGB/RGBA8 PNGs emitted by Electron. Used as an independent
// output semantic guard: bitmap round-trip equality alone misses alpha changes.
import zlib from 'node:zlib';
import { Worker, isMainThread, parentPort, workerData } from 'node:worker_threads';
export function decodeNativePng(png, width, height) {
  if (!png.subarray(0, 8).equals(Buffer.from('89504e470d0a1a0a', 'hex'))) throw new Error('PNG signature');
  let channels = 0, idat = [];
  for (let off = 8; off < png.length;) {
    const n = png.readUInt32BE(off), t = png.toString('ascii', off + 4, off + 8);
    if (off + n + 12 > png.length) throw new Error('truncated PNG');
    const data = png.subarray(off + 8, off + 8 + n);
    if (t === 'IHDR') {
      if (data.readUInt32BE(0) !== width || data.readUInt32BE(4) !== height || data[8] !== 8 || data[10] || data[11] || data[12]) throw new Error('PNG geometry/format');
      channels = data[9] === 6 ? 4 : data[9] === 2 ? 3 : 0;
    }
    if (t === 'IDAT') idat.push(data);
    off += n + 12;
  }
  if (!channels) throw new Error('unsupported native PNG color type');
  const stride = width * channels;
  const raw = zlib.inflateSync(Buffer.concat(idat), { maxOutputLength: (stride + 1) * height });
  if (raw.length !== (stride + 1) * height) throw new Error('PNG payload length');
  const decoded = Buffer.alloc(stride * height);
  const rawWords = new DataView(raw.buffer, raw.byteOffset, raw.length);
  const decodedWords = new DataView(decoded.buffer, decoded.byteOffset, decoded.length);
  for (let y = 0; y < height; y++) {
    const src = y * (stride + 1) + 1, dst = y * stride;
    const f = raw[src - 1];
    // Dispatch once per row. In particular, filter None is a byte copy, not
    // millions of predictor calculations and branches over already-raw bytes.
    switch (f) {
      case 0:
        raw.copy(decoded, dst, src, src + stride);
        break;
      case 1:
        raw.copy(decoded, dst, src, src + channels);
        for (let x = channels; x < stride; x++) decoded[dst+x] = raw[src+x] + decoded[dst+x-channels];
        break;
      case 2: {
        if (!y) { raw.copy(decoded, dst, src, src + stride); break; }
        // Four independent modulo-256 sums. Mask alternating byte lanes before
        // adding so carries cannot contaminate the adjacent byte. DataView is
        // explicitly little-endian and permits PNG's unaligned row starts.
        let x = 0;
        for (; x + 4 <= stride; x += 4) {
          const a = rawWords.getUint32(src+x, true), b = decodedWords.getUint32(dst+x-stride, true);
          const lo = ((a & 0x00ff00ff) + (b & 0x00ff00ff)) & 0x00ff00ff;
          const hi = (((a >>> 8) & 0x00ff00ff) + ((b >>> 8) & 0x00ff00ff)) & 0x00ff00ff;
          decodedWords.setUint32(dst+x, lo | (hi << 8), true);
        }
        for (; x < stride; x++) decoded[dst+x] = raw[src+x] + decoded[dst+x-stride];
        break;
      }
      case 3:
        for (let x = 0; x < stride; x++) {
          const a = x >= channels ? decoded[dst+x-channels] : 0;
          const b = y ? decoded[dst+x-stride] : 0;
          decoded[dst+x] = raw[src+x] + ((a+b) >>> 1);
        }
        break;
      case 4:
        for (let x = 0; x < stride; x++) {
          const a = x >= channels ? decoded[dst+x-channels] : 0;
          const b = y ? decoded[dst+x-stride] : 0;
          const c = y && x >= channels ? decoded[dst+x-stride-channels] : 0;
          const p = a+b-c, pa = Math.abs(p-a), pb = Math.abs(p-b), pc = Math.abs(p-c);
          decoded[dst+x] = raw[src+x] + (pa <= pb && pa <= pc ? a : pb <= pc ? b : c);
        }
        break;
      default: throw new Error('PNG filter');
    }
  }
  if (channels === 4) return decoded;
  const rgba = Buffer.alloc(width * height * 4);
  const rgbaWords = new DataView(rgba.buffer, rgba.byteOffset, rgba.length);
  let i=0, j=0;
  for (; i+3<decoded.length; i+=3, j+=4) {
    rgbaWords.setUint32(j, decodedWords.getUint32(i, true) | 0xff000000, true);
  }
  // The final RGB pixel has no fourth input byte; never overread the payload.
  rgba[j]=decoded[i]; rgba[j+1]=decoded[i+1]; rgba[j+2]=decoded[i+2]; rgba[j+3]=255;
  return rgba;
}

export function markerRanges(width, height, phase) {
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 64 || height < 2 || !['a','b'].includes(phase)) throw new Error('invalid marker geometry/phase');
  const dx = phase === 'a' ? 0 : 16;
  return [[dx*4, (dx+16)*4], [((height-1)*width+width-16-dx)*4, ((height-1)*width+width-dx)*4]];
}

export function markerMatches(bitmap, width, height, phase, token) {
  const ranges = markerRanges(width, height, phase);
  if (bitmap.length !== width*height*4 || token.length !== 48) return false;
  return ranges.every(([start]) => {
    for (let p=0; p<16; p++) {
      const i = start+p*4, t = p*3;
      if (bitmap[i] !== token[t+2] || bitmap[i+1] !== token[t+1] || bitmap[i+2] !== token[t] || bitmap[i+3] !== 255) return false;
    }
    return true;
  });
}

// The original pixels come from a second *verified compositor bitmap*, never
// page-provided originals, a neighbor row, or an untagged capturePage snapshot.
export function mergeWitnesses(a, b, width, height) {
  if (a.length !== width*height*4 || b.length !== a.length) throw new Error('witness buffer geometry');
  const ar = markerRanges(width, height, 'a'), br = markerRanges(width, height, 'b');
  const excluded = [...ar, ...br].sort((x,y)=>x[0]-y[0]);
  let pos = 0;
  for (const [start,end] of [...excluded, [a.length,a.length]]) {
    if (!a.subarray(pos,start).equals(b.subarray(pos,start))) throw new Error('unstable_page: compositor witnesses differ outside markers');
    pos = end;
  }
  const clean = Buffer.from(a);
  for (const [start,end] of ar) b.copy(clean,start,start,end);
  return clean;
}


// One host decoder thread, never a page/render worker. Six independently encoded
// PNGs still undergo the same full RGBA decoder. Only immutable PNG copies cross
// the boundary; compositor bitmaps remain owned by the main thread.
const DECODE_OPERATIONS = ['baseline', 'a', 'b', 'aa', 'bb', 'final'];
export class NativePngDecoder {
  constructor(width, height) {
    this.width = width; this.height = height; this.pending = new Map();
    this.submitted = []; this.completed = 0; this.generation = 0; this.used = new Set(); this.failure = null; this.closed = false;
    this.worker = new Worker(new URL(import.meta.url), {workerData:{vtxNativePngDecoder:1}});
    this.worker.on('message', message => {
      if (this.closed) return;
      const entry = this.pending.get(message?.operation);
      if (!entry || message.generation !== this.generation || message.frame !== this.frame ||
          message.width !== this.width || message.height !== this.height ||
          !(message.rgba instanceof Uint8Array) || message.rgba.byteLength !== this.width*this.height*4 ||
          Object.keys(message).sort().join(',') !== 'frame,generation,height,operation,rgba,width') {
        this.fail(Error('decoder_protocol')); return;
      }
      this.pending.delete(message.operation); this.completed++;
      entry.resolve(Buffer.from(message.rgba.buffer, message.rgba.byteOffset, message.rgba.byteLength));
    });
    this.worker.on('error', e => this.fail(Error(`decoder_error:${e.message}`)));
    this.worker.on('exit', code => { if (!this.closed) this.fail(Error(`decoder_exit:${code}`)); });
  }
  fail(error) {
    this.failure ||= error;
    for (const entry of this.pending.values()) entry.reject(this.failure);
    this.pending.clear();
  }
  begin(frame, generation) {
    if (this.closed || this.failure) throw this.failure || Error('decoder_closed');
    if (this.pending.size || !Number.isInteger(frame) || !Number.isInteger(generation) || generation !== this.generation+1) throw Error('decoder_generation');
    this.frame = frame; this.generation = generation; this.used.clear(); this.completed = 0; this.submitted = [];
  }
  decode(png, operation) {
    if (this.closed || this.failure) return Promise.reject(this.failure || Error('decoder_closed'));
    if (!this.generation || !Buffer.isBuffer(png) || operation !== DECODE_OPERATIONS[this.used.size] || this.used.has(operation) || this.pending.size >= 6) return Promise.reject(Error('decoder_operation'));
    this.used.add(operation);
    const promise = new Promise((resolve,reject) => {
      this.pending.set(operation, {resolve,reject});
      try { this.worker.postMessage({generation:this.generation, frame:this.frame, operation,
        width:this.width, height:this.height, png}); }
      catch (e) { this.fail(e); }
    });
    // Errors may arrive while Chromium is painting; fence them when collected.
    promise.catch(() => {}); this.submitted.push(promise);
    return promise;
  }
  async drain() { await Promise.allSettled(this.submitted); }
  assertComplete() {
    if (this.failure) throw this.failure;
    if (this.closed || this.pending.size || this.used.size !== 6 || this.completed !== 6) throw Error('decoder_incomplete');
  }
  close() {
    if (this.closed) return;
    this.closed = true; this.fail(Error('decoder_closed'));
    return this.worker.terminate();
  }
}
if (!isMainThread && workerData?.vtxNativePngDecoder === 1) {
  let generation = 0, frame = -1, used = new Set();
  parentPort.on('message', message => {
    const {generation:g, frame:f, operation, width, height, png} = message;
    if (!Number.isInteger(g) || !Number.isInteger(f) || f < 0 ||
        !Number.isInteger(width) || !Number.isInteger(height) || width < 64 || height < 2 ||
        width > 3840 || height > 2160 || width*height > 8294400 ||
        !(png instanceof Uint8Array) || !DECODE_OPERATIONS.includes(operation) ||
        Object.keys(message).sort().join(',') !== 'frame,generation,height,operation,png,width') throw Error('decoder_request');
    if (g > generation) { generation = g; frame = f; used = new Set(); }
    if (g !== generation || f !== frame || operation !== DECODE_OPERATIONS[used.size] || used.has(operation)) throw Error('decoder_request_generation');
    used.add(operation);
    const rgba = decodeNativePng(Buffer.from(png.buffer,png.byteOffset,png.byteLength),width,height);
    parentPort.postMessage({generation:g,frame:f,operation,width,height,rgba},[rgba.buffer]);
  });
}
