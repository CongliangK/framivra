// Decode only the RGB/RGBA8 PNGs emitted by Electron. Used as an independent
// output semantic guard: bitmap round-trip equality alone misses alpha changes.
import zlib from 'node:zlib';
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
