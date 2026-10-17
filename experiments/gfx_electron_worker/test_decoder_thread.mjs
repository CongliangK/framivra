import assert from 'node:assert/strict';
import zlib from 'node:zlib';
import { NativePngDecoder, decodeNativePng } from './pixel_codec.mjs';
const width=64, height=2;
function chunk(type,data) { const b=Buffer.alloc(data.length+12); b.writeUInt32BE(data.length); b.write(type,4); data.copy(b,8); return b; }
const header=Buffer.alloc(13);header.writeUInt32BE(width);header.writeUInt32BE(height,4);header[8]=8;header[9]=6;
const pixels=Buffer.alloc((width*4+1)*height);for(let y=0;y<height;y++)for(let x=0;x<width;x++){let i=y*(width*4+1)+1+x*4;pixels[i]=x;pixels[i+1]=y;pixels[i+2]=255-x;pixels[i+3]=[0,128,255][x%3];}
const png=Buffer.concat([Buffer.from('89504e470d0a1a0a','hex'),chunk('IHDR',header),chunk('IDAT',zlib.deflateSync(pixels)),chunk('IEND',Buffer.alloc(0))]);
const expected=decodeNativePng(png,width,height);
const decoder=new NativePngDecoder(width,height);
try {
  for(let generation=1;generation<=2;generation++) {
    decoder.begin(30+generation,generation);
    assert.throws(()=>decoder.assertComplete(),/incomplete/);
    const source=Buffer.from(png); const jobs=['baseline','a','b','aa','bb','final'].map(op=>decoder.decode(source,op));
    source.fill(0); // postMessage must own a copy; never detach or alias caller data.
    for(const actual of await Promise.all(jobs)) assert.deepEqual(actual,expected);
    decoder.assertComplete();
    await assert.rejects(decoder.decode(png,'final'),/operation/);
  }
  assert.throws(()=>decoder.begin(40,4),/generation/);
} finally { await decoder.close(); }
// Host protocol must refuse an unexpected, stale, duplicate, malformed, or
// truncated reply even if another legitimate decode is still in flight.
for(const change of [m=>({...m,generation:2}),m=>({...m,frame:999}),m=>({...m,operation:'a'}),m=>({...m,width:65}),m=>({...m,rgba:new Uint8Array(1)}),m=>({...m,extra:true})]) {
  const d=new NativePngDecoder(width,height);d.begin(31,1);
  try {
    const waiting=d.decode(png,'baseline');
    d.worker.emit('message',change({generation:1,frame:31,operation:'baseline',width,height,rgba:new Uint8Array(width*height*4)}));
    await assert.rejects(waiting,/protocol/);assert.throws(()=>d.assertComplete(),/protocol/);
  } finally {await d.close();}
}
const d=new NativePngDecoder(width,height);d.begin(31,1);
try {const waiting=d.decode(Buffer.from('broken'),'baseline');await assert.rejects(waiting,/decoder_error/);}finally{await d.close();}
const dead=new NativePngDecoder(width,height);dead.begin(31,1);
try {const waiting=dead.decode(png,'baseline');dead.worker.emit('exit',0);await assert.rejects(waiting,/decoder_exit/);}finally{await dead.close();}
console.log('decoder thread exact alpha/copy/lifecycle/protocol negatives PASS');
