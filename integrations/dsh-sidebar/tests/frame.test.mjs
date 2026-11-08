import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash, webcrypto } from 'node:crypto'
import { Readable } from 'node:stream'
import { FrameEpoch, verifyFrame, frameDraftText, decodePNG, frameRequest, fpsMatchesReference, FPS_TOKEN_LIMIT, FPS_DIGIT_LIMIT, FPS_EXPONENT_LIMIT } from '../src/client/frame.mjs'
import { boundedText, FRAME_WIRE_LIMIT } from '../src/bounded.mjs'
import { TimelineReader, referenceArgs } from '../src/protocol.mjs'
import { parseJSON, stringifyJSON } from '../src/json.mjs'
const hash = bytes => createHash('sha256').update(bytes).digest('hex')
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aCioAAAAASUVORK5CYII=', 'base64')
function fixture() {
 const raw = '{"unknown":9007199254740993,"decimal":1.0000000000000001}', version = `sha256:${hash(raw)}`
 return { schema:'framivra.timeline.frame/v1',operation:'frame',ok:true,content_version:version,diagnostics:[],image:{state:'generated',kind:'current-composite',approximate:false,mime_type:'image/png',width:1,height:1,frame:9,fps:{num:30,den:1},content_version:version,input_fingerprint:'a'.repeat(64),identity_semantics:'Fast media stat identity; not live atomic',render_key:'b'.repeat(64),cache_hit:false,bytes:png.length,sha256:hash(png),inline:{encoding:'base64',data:png.toString('base64')}},reference:{schema:'framivra.timeline.reference/v1',operation:'reference',ok:true,content_version:version,diagnostics:[],document:{raw,content_version:version},selection:{global_frame:9,fps:30,tracks:[],blocks:[{address:'/video_tracks/0/clips/0',stable_id:'clip',content_version:version,raw:'{"id":"clip","big":9007199254740993}'}]},coverage:{complete:true}} }
}
const verify = (value, extra={}) => verifyFrame(value,{frame:9,contentVersion:value.content_version,signal:new AbortController().signal,crypto:webcrypto,decode:async()=>{},...extra})
test('exact PNG File and raw reference; image.path is never accessed',async()=>{
 const value=fixture();Object.defineProperty(value.image,'path',{get(){throw new Error('path access forbidden')}})
 const result=await verify(value)
 assert.deepEqual(Buffer.from(await result.file.arrayBuffer()),png)
 assert.equal(result.file.type,'image/png')
 const text=frameDraftText(result,{kind:'block',address:'/video_tracks/0/clips/0'})
 assert(text.includes('9007199254740993'));assert(text.includes('1.0000000000000001'))
 assert(!text.includes('base64'));assert(!text.includes('image.path'))
 assert.throws(()=>frameDraftText(result,{kind:'block',address:'/not-hit'}),/FRAME_NOT_HIT/)
 assert(frameDraftText(result,{kind:'block',address:'/not-hit'},true).includes('position-only'))
})
test('fps identity: kernel non-canonical 30.0 rawJSON token verifies exactly; rounding lookalikes, strings and oversize tokens are rejected',async()=>{
  const raw30 = parseJSON('{"fps":30.0}').fps, raw3e1 = parseJSON('{"fps":3e1}').fps
  assert.equal(JSON.isRawJSON(raw30),true)
  // End-to-end through verifyFrame with the exact kernel wire spelling 30.0.
  const value=fixture();value.reference.selection.fps=raw30
  assert.equal((await verify(value)).certificate.fps.num,30)
  // Other exact decimal spellings of the same rational still verify.
  const trailing=value.reference.selection.fps=parseJSON('{"fps":30.000}').fps
  assert.equal(JSON.isRawJSON(trailing),true)
  await verify(value)
  // Number path: canonical safe integer stays accepted, non-integer rejected.
  assert.equal(fpsMatchesReference(30,1,30),true)
  assert.equal(fpsMatchesReference(60,2,30),true)
  assert.equal(fpsMatchesReference(30,1,30.5),false)
  assert.equal(fpsMatchesReference(30,1,31),false)
  // Exact rational comparison: 30.0000000000000000000001 !== 30 even though
  // Number(rawJSON) would round both to 30.
  assert.equal(fpsMatchesReference(30,1,parseJSON('{"fps":30.0000000000000000000001}').fps),false)
  assert.equal(fpsMatchesReference(30,1,parseJSON('{"fps":29.9999999999999999999999}').fps),false)
  assert.equal(fpsMatchesReference(30,1,raw3e1),true)
  assert.equal(fpsMatchesReference(30,1,parseJSON('{"fps":3.0e1}').fps),true)
  assert.equal(fpsMatchesReference(30,4,parseJSON('{"fps":7.5}').fps),true)
  assert.equal(fpsMatchesReference(30,4,parseJSON('{"fps":7.50}').fps),true)
  assert.equal(fpsMatchesReference(30,4,parseJSON('{"fps":7.5000000000000000000001}').fps),false)
  // Strings and lookalikes never pass as FPS.
  for(const lookalike of ['30','30.0',' 30','30 ','0x1e',{},null,true,[],{rawJSON:'30.0'}])
    assert.equal(fpsMatchesReference(30,1,lookalike),false,JSON.stringify(lookalike))
  // Negative, zero, and inverted fractions never match a positive certificate.
  assert.equal(fpsMatchesReference(30,1,parseJSON('{"fps":-30}').fps),false)
  assert.equal(fpsMatchesReference(30,1,parseJSON('{"fps":0}').fps),false)
  assert.equal(fpsMatchesReference(30,1,parseJSON('{"fps":0.0}').fps),false)
  // Tightly bounded token: oversize digit runs and unsafe exponents are rejected.
  assert.equal(fpsMatchesReference(30,1,JSON.rawJSON('3'.repeat(FPS_DIGIT_LIMIT+1))),false)
  assert.equal(fpsMatchesReference(30,1,JSON.rawJSON(`30${'0'.repeat(FPS_TOKEN_LIMIT)}`)),false)
  assert.equal(fpsMatchesReference(30,1,JSON.rawJSON(`1e${FPS_EXPONENT_LIMIT+1}`)),false)
  assert.equal(fpsMatchesReference(30,1,JSON.rawJSON(`1e-${FPS_EXPONENT_LIMIT+1}`)),false)
  // A genuinely different huge-but-in-bounds rational is rejected by value, not by crash.
  assert.equal(fpsMatchesReference(30,1,JSON.rawJSON(`1e${FPS_EXPONENT_LIMIT}`)),false)
  // Invalid certificate rationals fail closed.
  for(const [num,den] of [[undefined,1],[30,undefined],[0,1],[30,0],[-30,1],[1.5,1],[30,1.5],[30,-1]])
    assert.equal(fpsMatchesReference(num,den,30),false,`${num}/${den}`)
  // Full verifyFrame still rejects a real fps mismatch and malformed fps objects.
  const mismatch=fixture();mismatch.reference.selection.fps=raw30;mismatch.image.fps={num:60000,den:2001}
  await assert.rejects(verify(mismatch),/FRAME_INVALID: FPS does not match core reference/)
  const stringFps=fixture();stringFps.reference.selection.fps='30.0'
  await assert.rejects(verify(stringFps),/FRAME_INVALID: FPS does not match core reference/)
  const missingFps=fixture();delete missingFps.image.fps
  await assert.rejects(verify(missingFps),/FRAME_INVALID: FPS does not match core reference/)
})
test('fps identity reflects the real vtx wire: a kernel-valid timeline with literal fps 30.0 emits selection.fps rawJSON token "30.0" and certificate 30/1',async()=>{
  // Confirmed against the real kernel (vtx.timeline_reference with a timeline
  // whose project.fps is the literal token 30.0, accepted by ALLOWED_FPS):
  // the wire selection carries "fps": 30.0 while timeline_frame emits
  // {num: 30, den: 1}. parseJSON keeps the token as rawJSON (String(30.0)!
  // === "30.0"), which the old Number()-style compare always rejected.
  const envelope=parseJSON(`{"fps": ${'30.0'}}`)
  assert.equal(JSON.isRawJSON(envelope.fps),true)
  assert.equal(fpsMatchesReference(30,1,envelope.fps),true)
})
test('certificates, unsupported inline, stale raw, hash and pixel bounds fail closed',async()=>{
 for(const mutate of [v=>delete v.image.inline,v=>v.image.approximate=true,v=>v.image.frame=8,v=>v.image.bytes++,v=>v.image.sha256='c'.repeat(64),v=>v.image.width=9000,v=>v.image.fps.den=2,v=>v.reference.document.raw+=' ',v=>v.reference.content_version='sha256:'+'e'.repeat(64),v=>v.image.inline.data+='=',v=>v.image.mime_type='image/jpeg']) {
  const v=fixture();mutate(v);await assert.rejects(verify(v),/FRAME_INVALID/)
 }
 await assert.rejects(verify(fixture(),{limits:{mediaTypes:['image/jpeg'],maxImageBytes:10000,maxMessageImageBytes:10000,maxImagesPerMessage:1}}),/DSH image policy/)
 await assert.rejects(verify(fixture(),{decode:async()=>{throw new Error('full decode failed')}}),/full decode failed/)
})
test('temporary decode URLs revoked on success, decode error, dimensions and abort',async()=>{
 for(const mode of ['ok','reject','dimensions','abort']) {
  const ac=new AbortController(), urls=[], revoked=[]
  class Image {naturalWidth=mode==='dimensions'?2:1;naturalHeight=1;decode(){if(mode==='reject')return Promise.reject(Error('decode'));if(mode==='abort'){queueMicrotask(()=>ac.abort());return new Promise(()=>{})}return Promise.resolve()}}
  const browser={Image,URL:{createObjectURL:()=>{urls.push('blob:one');return 'blob:one'},revokeObjectURL:url=>revoked.push(url)}}
  const operation=decodePNG(new Blob([png]),1,1,ac.signal,browser)
  if(mode==='ok')await operation;else await assert.rejects(operation)
  assert.deepEqual(revoked,urls)
 }
})
test('bounded stream counts UTF-8 bytes, exact boundary, overflow, invalid length and abort',async()=>{
 assert.equal(await boundedText(new Response('é'),2,new AbortController().signal),'é')
 await assert.rejects(boundedText(new Response('é'),1,new AbortController().signal),/BODY_LIMIT/)
 for(const length of ['-1','1e3','9999999999999999999'])await assert.rejects(boundedText(new Response('a',{headers:{'content-length':length}}),2,new AbortController().signal),/BODY_LIMIT/)
 let cancelled=false;const ac=new AbortController();const message={headers:new Headers(),body:new ReadableStream({pull(){ac.abort()},cancel(){cancelled=true}})}
 await assert.rejects(boundedText(message,2,ac.signal));assert(cancelled);assert.equal(message.body.locked,false)
 const response=new Response(' '.repeat(FRAME_WIRE_LIMIT+1));await assert.rejects(frameRequest('one',{},new AbortController().signal,async()=>response),/BODY_LIMIT/)
})
test('parent epoch invalidates synchronously and dirty/busy/unknown cannot be bypassed',()=>{
 const owner=new FrameEpoch(),tab=new AbortController(),op=owner.begin(tab.signal)
 owner.generated={file:new File(['x'],'x.png')};owner.invalidate();assert.equal(owner.generated,undefined);assert.throws(()=>owner.check(op))
 for(const key of ['dirty','busy','unknown']){owner.edit={dirty:false,busy:false,unknown:false};owner.editChanged({[key]:true});assert.throws(()=>owner.check(owner.begin(tab.signal)),/FRAME_STALE/)}
 owner.edit={dirty:false,busy:false,unknown:false};const current=owner.begin(tab.signal);tab.abort();assert.throws(()=>owner.check(current))
 assert.deepEqual(referenceArgs({expected_content_version:fixture().content_version,frame:9}),[`--expected-content-version=${fixture().content_version}`,'--frame','9'])
})
test('official argv + true owner pipe; ownership persists until done AND waitForExit',async()=>{
 const value=fixture(),specs=[];let finishDone,finishRange,terminated=false
 const done=new Promise(r=>finishDone=r),range=new Promise(r=>finishRange=r)
 const handle={stdout:Readable.from([Buffer.from(stringifyJSON(value)+'\n')]),done,collected:{stderr:{readFrom:()=>({text:'',lossy:false})}},terminate(){terminated=true},waitForExit:()=>range}
 const reader=new TimelineReader({spawn:spec=>{specs.push(spec);return handle}})
 let settled=false;const pending=reader.frame('/opt/vtx','/own/timeline.json',9,value.content_version,new AbortController().signal).then(v=>{settled=true;return v})
 finishDone({exitCode:0});await new Promise(r=>setImmediate(r));assert(terminated);assert.equal(reader.active.size,1);assert.equal(settled,false)
 assert(specs[0].argv.includes('--include-png'));assert(specs[0].argv.includes('--render'));assert.equal(specs[0].stdio.stdout,'pipe');assert.equal(specs[0].stdio.stderr.maxBytes,16384)
 finishRange(true);assert.deepEqual(await pending,parseJSON(stringifyJSON(value)));assert.equal(reader.active.size,0);await reader.dispose()
})

test('editChanged is semantically idempotent: unchanged edit reports never abort the epoch or drop the verified PNG',()=>{
 const owner=new FrameEpoch(),tab=new AbortController(),first=owner.begin(tab.signal)
 owner.generated={certificate:{frame:9}}
 // Same flags as a repeat read-only proposal poll: no real transition.
 assert.equal(owner.editChanged({dirty:false,busy:false,unknown:false}),false)
 assert.equal(owner.epoch,0,'unchanged report must not invalidate')
 assert.equal(first.signal.aborted,false,'in-flight frame render must survive a read-only poll')
 assert.deepEqual(owner.generated,{certificate:{frame:9}},'verified PNG must be kept across a no-op report')
 // A real transition still invalidates synchronously, aborts, and clears.
 assert.equal(owner.editChanged({dirty:true}),true)
 assert.equal(owner.epoch,1);assert.equal(first.signal.aborted,true);assert.equal(owner.generated,undefined)
 assert.throws(()=>owner.assertReady(),/FRAME_STALE/)
 // Re-reporting the same dirty state must not invalidate a second time.
 assert.equal(owner.editChanged({dirty:true}),false)
 assert.equal(owner.epoch,1)
 // Clearing the flag is a real transition and invalidates again.
 assert.equal(owner.editChanged({dirty:false}),true)
 assert.equal(owner.epoch,2)
})
