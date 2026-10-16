// Small real Electron 44 pixel-normalization probe; no Chromium fallback.
import { app, nativeImage } from 'electron';
import fs from 'node:fs';
import zlib from 'node:zlib';
function crc32(b) { let c=0xffffffff; for (const v of b) { c^=v; for(let i=0;i<8;i++) c=(c>>>1)^((c&1)?0xedb88320:0); } return (c^0xffffffff)>>>0; }
function chunk(t,d) { const b=Buffer.alloc(12+d.length); b.writeUInt32BE(d.length); b.write(t,4); d.copy(b,8); b.writeUInt32BE(crc32(b.subarray(4,-4)),b.length-4); return b; }
function png(rgba) { const h=Buffer.alloc(13); h.writeUInt32BE(rgba.length/4,0); h.writeUInt32BE(1,4); h[8]=8; h[9]=6; return Buffer.concat([Buffer.from('89504e470d0a1a0a','hex'),chunk('IHDR',h),chunk('IDAT',zlib.deflateSync(Buffer.concat([Buffer.from([0]),rgba]))),chunk('IEND',Buffer.alloc(0))]); }
app.whenReady().then(() => {
const rgba=Buffer.from([201,99,37,128,17,133,251,64,255,127,1,254,0,0,0,0,12,35,78,255]);
const im=nativeImage.createFromBuffer(png(rgba));
const bitmap=im.toBitmap();
const rebuilt=nativeImage.createFromBitmap(bitmap,{width:5,height:1});
const out=process.argv.find(a=>a.startsWith('--out='))?.slice(6);
if(out) {fs.mkdirSync(out,{recursive:true});fs.writeFileSync(out+'/original.png',im.toPNG());fs.writeFileSync(out+'/rebuilt.png',rebuilt.toPNG());}
console.log(JSON.stringify({versions:process.versions,rgba:[...rgba],bitmap:[...bitmap],echo:[...rebuilt.toBitmap()]}));
app.exit(0);
});
