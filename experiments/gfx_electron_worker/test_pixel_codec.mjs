import assert from 'node:assert/strict';
import {markerRanges,markerMatches,mergeWitnesses} from './pixel_codec.mjs';
const w=192,h=96;
const clean=Buffer.alloc(w*h*4);for(let i=0;i<clean.length;i++)clean[i]=(i*37)&255;
function marked(phase,token) {
 const b=Buffer.from(clean);
 for(const [start] of markerRanges(w,h,phase))for(let p=0;p<16;p++) {
  b[start+p*4]=token[p*3+2];b[start+p*4+1]=token[p*3+1];b[start+p*4+2]=token[p*3];b[start+p*4+3]=255;
 }
 return b;
}
const ta=Buffer.from(Array.from({length:48},(_,i)=>i*5%256));
const tb=Buffer.from(Array.from({length:48},(_,i)=>(i*7+12)%256));
const a=marked('a',ta),b=marked('b',tb);
assert(markerMatches(a,w,h,'a',ta));
assert(!markerMatches(a,w,h,'a',tb)); // delayed paint / wrong target nonce
assert(!markerMatches(a,w,h,'b',ta));
assert(!markerMatches(clean,w,h,'a',ta));
assert(mergeWitnesses(a,b,w,h).equals(clean)); // all pixels, not adjacent row
const unstable=Buffer.from(b);unstable[w*4+12]^=1;
assert.throws(()=>mergeWitnesses(a,unstable,w,h),/unstable_page/);
assert.throws(()=>markerRanges(32,h,'a'));
console.log('pixel witnesses: nonce/stale/geometry/unstable/full recovery PASS');
