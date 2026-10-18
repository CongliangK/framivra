'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const ROOT=path.resolve(__dirname,'..'),R=require('../src/runtime/runtime.js'),S=require('../src/runtime/svg.js'),DATA=require('../src/runtime/data.js');
const runtime=options=>R.createRuntime({diagnostic:true,allowScaffold:true,...options});
const digest=b=>crypto.createHash('sha256').update(b).digest('hex');
function masks(result){return Object.fromEntries(result.nodes.filter(n=>n.tag==='defs').flatMap(n=>n.children).filter(n=>n.tag==='mask').map(n=>[n.attrs.id,S.serialize(n)]));}
test('actual emitted timeline is the binding source and immutable runtime input',()=>{
 const actual=JSON.parse(fs.readFileSync(path.join(ROOT,'timeline.json'),'utf8'));
 assert.deepEqual(DATA.actualTimeline,actual);
 const Entry=require('../src/runtime/entries.js'),fields=Entry.bindSources(actual,DATA.sourceBindings);
 assert.equal(fields.find(x=>x.id==='S3.gate').fields.dur,2910);
 assert.equal(fields.find(x=>x.id==='S3.strip').fields.src,'gfx/T11_0630_1200.html');
 assert.throws(()=>{DATA.actualTimeline.video_tracks[0].name='changed';});
 const broken=structuredClone(actual);broken.video_tracks.find(t=>t.name==='middle-gate-carrier').clips[0].dst_in++;
 assert.throws(()=>Entry.createEntries(DATA.trackMap,broken),/drift/);
});
test('material-mask diagnostics retain mandatory bird and contact coverage',()=>{
 const on=runtime(),off=runtime({effects:{material_masks:false}});
 // Entire opening is unchanged: offcuts are original structural paint order.
 for(let f=0;f<300;f++)for(const e of on.entries.filter(e=>f>=e.windowStart&&f<e.windowStart+e.duration))assert.equal(on.renderFrame(e.entryId,f-e.windowStart).fragment,off.renderFrame(e.entryId,f-e.windowStart).fragment,`${e.entryId}@${f}`);
 for(const f of [365,613,620,628,629,650,719,818,1050,1198,1380,1434,1450,1830,1920,1950,2158,2159,2280,2450,2590,2759,2940,3239,3490]){
  for(const e of on.entries.filter(e=>f>=e.windowStart&&f<e.windowStart+e.duration)){
   const a=masks(on.renderFrame(e.entryId,f-e.windowStart)),b=masks(off.renderFrame(e.entryId,f-e.windowStart));
   for(const id of Object.keys(a).filter(id=>/whole-bird-visible|foreground-B-knockout|F-shared-gate-mask|buckle-cover|carrier-foreground-visible|A-under-B|frame-front-paper-mask|ribbon-behind-bird|residual-under-folds|operator-behind-tray|real-pcm-covered/.test(id)))assert.equal(a[id],b[id],`${id}@${f}`);
  }
 }
 assert.notEqual(on.renderFrame('T05.01',2480-330).fragment,off.renderFrame('T05.01',2480-330).fragment,'optional edge reveal really changes');
});
test('native captions are one real stream with nonempty exact half-open windows',()=>{
 const tl=DATA.actualTimeline;assert.equal(tl.subtitles.length,8);let previous=0;
 for(const s of tl.subtitles){assert.ok(s.text.trim().length);assert.ok(Number.isInteger(s.in)&&Number.isInteger(s.out));assert.ok(s.in>=previous&&s.out>s.in&&s.out<=3600);previous=s.out;}
 const font=JSON.parse(fs.readFileSync(path.join(ROOT,'production/caption-font.json'),'utf8'));
 assert.equal(font.bundled,false);assert.equal(font.font_path,undefined);assert.match(font.sha256,/^[0-9a-f]{64}$/);
 assert.equal(digest(fs.readFileSync(path.join(ROOT,font.license_path))),font.license_sha256);
 assert.equal(font.native_ass_family,'Noto Sans CJK SC');assert.deepEqual(font.faces[0].glyphs_missing,[]);
 assert.equal(font.user_environment_consumption_verified,false);
});
test('source release cannot be asserted by merely implementing a scene or enabling effects',()=>{
 assert.throws(()=>require('../scripts/build.js').build(),/Production release blocked/);
 const manifest=JSON.parse(fs.readFileSync(path.join(ROOT,'entry-manifest.json'),'utf8'));
 assert.equal(manifest.production_ready,false);assert.equal(manifest.role_accounting.planned,20);assert.equal(manifest.role_accounting.verified_complete_contributions,null);
 assert.ok(manifest.blockers.includes('USER_AUDIO_AUDITION_APPROVAL_REQUIRED'));
});

test('structural masks blacken explicit child paint without mutating geometry or holes',()=>{
 const E=require('../src/runtime/effects.js');
 const input=S.group([S.path('M0 0H20V20H0Z M5 5H15V15H5Z','#F1EDE1',{'fill-rule':'evenodd','fill-opacity':.3}),S.line([[1,1],[10,1]],'#AABBCC',2)],{opacity:.4,transform:'translate(5 6)'});
 const before=S.serialize(input),mask=E.structuralMask('opaque-test',[0,0,100,100],[input]),copy=mask.children[1].children[0];
 assert.equal(S.serialize(input),before);assert.equal(copy.attrs.transform,input.attrs.transform);assert.equal(copy.attrs.opacity,undefined);
 assert.equal(copy.children[0].attrs.fill,'black');assert.equal(copy.children[0].attrs['fill-rule'],'evenodd');assert.equal(copy.children[0].attrs['fill-opacity'],undefined);
 assert.equal(copy.children[1].attrs.fill,'none');assert.equal(copy.children[1].attrs.stroke,'black');
 const on=runtime(),off=runtime({effects:{material_masks:false}});
 for(const f of [2280,2450,2759,2940,3239,3599])for(const r of [on,off])for(const e of r.entries.filter(e=>f>=e.windowStart&&f<e.windowStart+e.duration)){
  const result=r.renderFrame(e.entryId,f-e.windowStart);
  const walk=n=>{if(typeof n==='string')return;if(n.attrs.fill&&n.attrs.fill!=='none')assert.equal(n.attrs.fill,'black');if(n.attrs.stroke&&n.attrs.stroke!=='none')assert.equal(n.attrs.stroke,'black');assert.equal(n.attrs.opacity,undefined);n.children.forEach(walk);};
  for(const m of result.nodes.filter(n=>n.tag==='defs').flatMap(n=>n.children).filter(n=>n.tag==='mask'&&/carrier-foreground-visible|A-under-B|frame-front-paper-mask/.test(n.attrs.id)))m.children.slice(1).forEach(walk);
 }
});
test('S5 keeps F supported until genuine pickup, then preserves the buckle contact',()=>{
 const r=runtime(),base=r.sharedAt(1890).mechanics.frame.pose;
 for(let f=1890;f<1920;f++){
  const m=r.sharedAt(f).mechanics;assert.deepEqual(m.frame.pose,base);assert.ok(m.frame.feedAnchor);
  assert.ok(!r.renderFrame('T05.01',f-330).fragment.includes('beak-F-grasp'));
 }
 const pick=r.sharedAt(1920).mechanics.frame;assert.ok(Math.hypot(pick.beak[0]-pick.grasp[0],pick.beak[1]-pick.grasp[1])<1e-9);
 for(let f=1920;f<=1950;f++){
  const q=r.sharedAt(f).mechanics.frame;assert.ok(Math.hypot(q.beak[0]-q.grasp[0],q.beak[1]-q.grasp[1])<=.40000001);assert.equal(q.feedAnchor,null);
 }
 const q=r.sharedAt(1950).mechanics.frame;assert.equal(q.linked,true);assert.ok(r.renderFrame('T05.01',1950-330).fragment.includes('beak-F-grasp'));
});
test('real native PCM drives the scheduled T13 entry with a bounded visible window',()=>{
 assert.equal(DATA.waveform.provenance.stage,'framivra-audio-stage');assert.equal(DATA.waveform.samples.length,480);
 const r=runtime(),e=r.entries.find(e=>e.layerId==='T13');assert.equal(e.windowStart,1710);assert.equal(e.duration,450);
 for(let f=1710;f<2160;f++){
  const result=r.renderFrame(e.entryId,f-e.windowStart);
  const defs=result.nodes.filter(n=>n.tag==='defs').flatMap(n=>n.children),reveal=defs.find(n=>n.tag==='clipPath'&&n.attrs.id.includes('real-pcm-reveal'));
  assert.ok(reveal);const width=Number(reveal.children[0].attrs.width);
  if(f===1710||f===2159)assert.equal(width,0);else assert.ok(width>0);
  assert.ok(defs.some(n=>n.tag==='clipPath'&&n.attrs.id.includes('real-pcm-feed')));
  assert.ok(defs.some(n=>n.tag==='mask'&&n.attrs.id.includes('real-pcm-covered')));
  assert.ok(result.fragment.includes('data-object="actual-pcm-envelope"'));
 }
 assert.equal(r.sharedAt(1920).dependencies.waveform.status,'ACTUAL_NATIVE_PCM_BOUND_AUDITION_PENDING');
});
test('S3 lifts its stable source readout and leader into the clear paper reading area',()=>{
 const r=runtime(),textEntry=r.entries.find(e=>e.layerId==='T14'&&e.windowStart<=1050&&e.windowStart+e.duration>1050);
 for(let f=1050;f<1140;f++){
  const html=r.renderFrame(textEntry.entryId,f-textEntry.windowStart).fragment;
  assert.match(html,/transform="matrix\(1 0 0 1 0 -125\)"/);assert.ok(html.includes('data-source-binding="S3.gate"'));
 }
});

test('S2 uses a fixed-length arm, genuine hand contact and the actual tray occluder',()=>{
 const r=runtime(),l1=Math.hypot(42,16),l2=Math.hypot(42,12);
 for(let f=585;f<630;f++){
  const m=r.sharedAt(f).mechanics,a=m.operator.arm;
  assert.ok(Math.abs(Math.hypot(a.shoulder[0]-a.elbow[0],a.shoulder[1]-a.elbow[1])-l1)<1e-8);
  assert.ok(Math.abs(Math.hypot(a.hand[0]-a.elbow[0],a.hand[1]-a.elbow[1])-l2)<1e-8);
  if(f>=600&&f<=620)assert.deepEqual(a.hand,[m.frame.pose.x,m.frame.pose.y+20*m.frame.pose.scale]);
  if(f>=613){assert.ok(m.backstop);const e=r.entries.find(e=>e.layerId==='T09'&&f>=e.windowStart&&f<e.windowStart+e.duration);assert.match(r.renderFrame(e.entryId,f-e.windowStart).fragment,/data-occlusion="F-tray-backstop"/);}
 }
});
