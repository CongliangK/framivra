'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const ROOT=path.resolve(__dirname,'..');
const DATA=require('../src/runtime/data.js'),S=require('../src/runtime/svg.js'),M=require('../src/runtime/math.js'),G=require('../src/runtime/geometry.js'),E=require('../src/runtime/effects.js');
const Entry=require('../src/runtime/entries.js'),R=require('../src/runtime/runtime.js');
const {makeScenes,makeStubs}=require('./fixtures.js');
const runtime=()=>R.createRuntime({scenes:makeScenes()});
const digest=value=>crypto.createHash('sha256').update(value).digest('hex');
test('19 sparse entries and 14 GFX roles exactly project current recipe',()=>{
  assert.equal(Entry.ENTRIES.length,19);assert.equal(new Set(Entry.ENTRIES.map(e=>e.layerId)).size,14);
  for(const e of Entry.ENTRIES){const a=Entry.frameContext(e.entryId,0),b=Entry.frameContext(e.entryId,e.duration-1);assert.equal(a.globalFrame,e.windowStart);assert.equal(b.globalFrame,e.windowStart+e.duration-1);}
  assert.equal(Entry.frameContext('T08.01',282).globalFrame,300);
  assert.equal(Entry.frameContext('T03.01',2910).globalFrame,3599);
  assert.equal(Entry.frameContext('T14.02',0).localSceneFrame,60);
  assert.throws(()=>Entry.getEntry('T99.01'));assert.throws(()=>Entry.getEntry('T07'));
});
test('frame sanitization, no scene-local clamp at 299',()=>{
  for(const v of [undefined,null,NaN,Infinity,-Infinity,-9,'bad',{},[],true])assert.equal(Entry.frameNumber(v,3600),0);
  assert.equal(Entry.frameNumber('3180.9',3600),3180);assert.equal(Entry.frameNumber(4000,3600),3599);
  assert.equal(Entry.frameContext('T07.01',3599).scene.id,'S8');
  assert.throws(()=>Entry.frameNumber(2,0));
});
test('forward, reverse, duplicate and permuted source trees agree over all 19 entries',()=>{
  const r=runtime();let tested=0;
  for(const e of Entry.ENTRIES){
    const locals=[...new Set([0,1,e.duration-2,e.duration-1,...DATA.cues.scenes.flatMap(s=>[s.in-1,s.in,s.in+1]).filter(f=>f>=e.windowStart&&f<e.windowStart+e.duration).map(f=>f-e.windowStart),...Array.from({length:17},(_,i)=>Math.floor(i*(e.duration-1)/16))])].sort((a,b)=>a-b);
    const expected=new Map(locals.map(f=>[f,digest(JSON.stringify(r.renderFrame(e.entryId,f)))]));
    const permuted=locals.filter((_,i)=>i%2).reverse().concat(locals.filter((_,i)=>!(i%2)));
    for(const f of [...locals,...locals.toReversed(),...permuted,...locals]){assert.equal(digest(JSON.stringify(r.renderFrame(e.entryId,f))),expected.get(f));tested++;}
  }
  assert.ok(tested>1500);
});
test('shared body/wing mask and camera are identical, including foreground override',()=>{
  const r=runtime();
  for(const frame of [264,299,300,344,1380,1404,1434,1451,1452,2450,2995]){
    const body=r.renderFrame('T07.01',frame),wings=r.renderFrame('T08.01',frame-18);
    const masks=a=>a.nodes.find(n=>n.tag==='defs').children.map(n=>S.serialize(n).replace(/film-T0[78]-01/g,'film-B')).join('');
    assert.equal(masks(body),masks(wings));
    const b=body.nodes.filter(n=>n.tag==='g'),w=wings.nodes.filter(n=>n.tag==='g');assert.equal(b[0].attrs.transform,w[0].attrs.transform);
    assert.ok(body.fragment.includes('mask="url(#film-T07-01-whole-bird-visible)"'));
  }
});
test('historical wing is bounded and takes current mask, not an accumulated trail',()=>{
  const r=runtime();for(const f of [1403,1404,1451,1452])assert.equal(r.renderFrame('T08.01',f-18).fragment.includes('data-history'),f>=1404&&f<1452);
  const off=R.createRuntime({scenes:makeScenes(),diagnostic:true,effects:{translucent_trails:false}});
  assert.ok(!off.renderFrame('T08.01',1410-18).fragment.includes('data-history'));
});
test('S7 selected owners have exactly zero SVG contribution; nonselected state freezes',()=>{
  const r=runtime();for(let f=2985;f<3015;f++)assert.equal(r.renderFrame('T05.01',f-330).fragment,'');
  for(let f=3045;f<3075;f++)assert.equal(r.renderFrame('T03.01',f-690).fragment,'');
  for(const f of [2940,2970,2995,3030,3060,3089])assert.equal(r.renderFrame('T04.01',f).fragment,r.renderFrame('T04.01',2940).fragment);
  assert.notEqual(r.renderFrame('T04.01',3090).fragment,r.renderFrame('T04.01',2940).fragment);
  assert.ok(r.renderFrame('T05.01',3030-330).fragment.length>0);assert.ok(r.renderFrame('T03.01',3090-690).fragment.length>0);
});
test('mechanical helpers retain W-guide fixed root, mobile P-C mounts, common hinge and force anchor',()=>{
  const a=G.carrierAssembly(M.translate(25,0)),b=G.carrierAssembly(M.translate(40,0));assert.deepEqual(a.fixedGuideMatrix,b.fixedGuideMatrix);
  assert.strictEqual(a.carrierMatrix,a.gateMatrix);assert.strictEqual(a.carrierMatrix,a.thresholdRMatrix);assert.strictEqual(a.carrierMatrix,a.receiverMatrix);
  for(const angle of [0,30,90,150,180]){const panels=G.hingePanels([[0,0],[130,0]],{A:{width:180,angle,side:-1},B:{width:170,angle:180-angle,side:1}});assert.strictEqual(panels.faces.A.hinge,panels.faces.B.hinge);assert.strictEqual(panels.faces.A.points[0],panels.hinge[0]);assert.equal(panels.faces.A.materialWidth,180);}
  const anchor=DATA.geometry.contacts.S5_F_buckle_P_A;
  for(const key of ['f1950','f2159_2160']){const c=G.forceChain({frameEndpoint:anchor[key].F_lower_endpoint,paperAnchor:anchor[key].shared_buckle_P_A_anchor});assert.strictEqual(c.tongueEnd,c.buckleAnchor);assert.strictEqual(c.buckleAnchor,c.paperAnchor);assert.equal(c.tongueWidth,5);}
  assert.deepEqual(M.transform(DATA.geometry.bird.wing_a[0],M.poseMatrix({x:765,y:465,scale:1.5})),[628.5,315]);
});
test('six switches default on; off controls cannot replace film defaults',()=>{
  assert.equal(Object.keys(E.DEFAULTS).length,6);assert.ok(Object.values(runtime().effects).every(v=>v===true));
  assert.throws(()=>R.createRuntime({effects:{local_blur:false}}));assert.throws(()=>E.options({unknown:true},true));assert.throws(()=>E.options({local_blur:0},true));
  const shape=S.rect(1,1,20,4,'black');const soft=E.contactPatch({id:'patch',bounds:[0,0,40,16],shape,sigma:2.6}),hard=E.contactPatch({id:'patch',bounds:[0,0,40,16],shape,enabled:false});
  assert.equal(soft.defs[0].attrs.filterUnits,'userSpaceOnUse');assert.equal(hard.nodes[0].attrs.opacity,'0.09');assert.deepEqual(hard.nodes[0].children,soft.nodes[0].children);
  assert.throws(()=>E.contactPatch({id:'patch',bounds:[0,0,1920,1080],shape}));assert.throws(()=>E.contactPatch({id:'patch',bounds:[0,0,40,16],shape,sigma:3}));
  assert.equal(E.textReveal({id:'r',bounds:[0,0,100,20],progress:.5}).children[0].attrs.width,'50');
});
test('immutable SVG builder escapes text, rejects active/external nodes, and makes local IDs',()=>{
  assert.ok(S.serialize(S.node('text',{},'<&>')).includes('&lt;&amp;&gt;'));
  for(const tag of ['script','foreignObject','image','animate','animateTransform'])assert.throws(()=>S.node(tag));
  assert.throws(()=>S.node('g',{onclick:'bad'}));assert.throws(()=>S.node('use',{href:'https://example.com/x'}));
  assert.throws(()=>S.rect(NaN,0,1,1,'red'));assert.throws(()=>S.node('g',{mask:'url(external.svg#x)'}));
  assert.throws(()=>{DATA.cues.project.fps=1;});assert.throws(()=>{G.G.bird.wing_a[0][0]=1;});
  assert.equal(S.ids('film-T07-01')('mask'),'film-T07-01-mask');
});
test('source bindings are projected, duplicate/missing targets fail; PCM is never fabricated',()=>{
  const fields=Entry.bindSources(DATA.timelineBlueprint,DATA.sourceBindings);
  assert.equal(fields.find(f=>f.id==='S3.gate').fields.dur,2910);
  const copy=JSON.parse(JSON.stringify(DATA.timelineBlueprint));copy.video_tracks.push(copy.video_tracks.find(t=>t.name==='middle-gate-carrier'));
  assert.throws(()=>Entry.bindSources(copy,DATA.sourceBindings));
  assert.throws(()=>G.waveformPoints(null,{startFrame:1710,endFrame:2160,x:0,y:0,width:100,height:10}));
});
test('stub rendering fails closed normally; diagnostic scaffold is transparent and explicit',()=>{
  assert.throws(()=>R.createRuntime({scenes:makeStubs()}).renderFrame('T07.01',176),/UNIMPLEMENTED_SCENE/);
  assert.throws(()=>R.createRuntime({allowScaffold:true}),/diagnostic/);
  const r=R.createRuntime({scenes:makeStubs(),diagnostic:true,allowScaffold:true});assert.equal(r.renderFrame('T07.01',176).fragment,'');assert.equal(r.renderFrame('T07.01',176).implemented,false);
  assert.ok(r.renderFrame('T07.01',176,{paper:true}).fragment.includes('#F1EDE1'));
  assert.equal(Object.values(makeStubs()).filter(s=>s.implemented).length,0); // Dedicated negative fixture, never the real registry.
});
test('mount navigation and synchronous __vtx_render mirror exact local f in a source-only fake DOM',()=>{
  const attrs={},root={innerHTML:'',setAttribute(k,v){attrs[k]=v;}},document={getElementById(id){assert.equal(id,'scene');return root;}},window={location:{search:'?f=1434'}};
  R.mount(document,window,'T07.01',{scenes:makeScenes()});const expected=root.innerHTML;
  assert.equal(attrs['data-global-frame'],'1434');assert.equal(window.__vtx_render(0),0);assert.equal(window.__vtx_render(1434),1434);assert.equal(root.innerHTML,expected);
  assert.equal(attrs['data-frame'],'1434');assert.ok(!root.innerHTML.includes('data-standalone-paper'));
});
test('browser bundle contains one shared runtime and 19 thin entry pages; fake DOM boot only',()=>{
  const output=fs.mkdtempSync(path.join(require('node:os').tmpdir(),'promo-bundle-test-'));
  const record=require('../scripts/build.js').build({draft:true,outDir:output});assert.equal(record.entry_count,19);
  const files=fs.readdirSync(path.join(output,'gfx'));assert.equal(files.length,19);
  const sandbox={window:{location:{search:'?f=0'}},URLSearchParams};vm.createContext(sandbox);vm.runInContext(fs.readFileSync(path.join(output,'runtime/shared.js'),'utf8'),sandbox);
  const attrs={},root={innerHTML:'',setAttribute(k,v){attrs[k]=v;}},doc={getElementById(){return root;}};
  sandbox.window.FilmRuntime.mount(doc,sandbox.window,'T14.02',{diagnostic:true,allowScaffold:true});assert.equal(attrs['data-global-frame'],'2820');assert.equal(attrs['data-status'],'candidate-not-production');
  assert.equal(sandbox.window.__vtx_render(400),329);assert.equal(attrs['data-global-frame'],'3149');
  for(const file of files){const html=fs.readFileSync(path.join(output,'gfx',file),'utf8');assert.ok(html.length<1100);assert.equal((html.match(/src="\.\.\/runtime\/shared.js"/g)||[]).length,1);assert.ok(!html.includes('type="module"'));}
  assert.throws(()=>require('../scripts/build.js').build(),/Production release blocked/);
  fs.rmSync(output,{recursive:true,force:true});
});
test('runtime sources forbid wall clocks, random state, timers, async network and autonomous CSS animation',()=>{
  function scan(root){return fs.readdirSync(root,{withFileTypes:true}).flatMap(e=>e.isDirectory()?scan(path.join(root,e.name)):[path.join(root,e.name)]);}
  const forbidden=/\b(?:Date|performance|requestAnimationFrame|setTimeout|setInterval|fetch|XMLHttpRequest|WebSocket)\s*[.(]|Math\.random\s*\(|@keyframes|animation\s*:|transition\s*:|addEventListener\s*\(/;
  for(const file of scan(path.join(ROOT,'src')).filter(p=>p.endsWith('.js')))assert.ok(!forbidden.test(fs.readFileSync(file,'utf8')),file);
});
test('adapted opening state equals original candidate-r3 helper at every opening frame',()=>{
  const html=fs.readFileSync(path.join(ROOT,'inputs/opening-reference.html'),'utf8');
  const scripts=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
  const cues=scripts[0],root={innerHTML:'',setAttribute(){}};
  const sandbox={document:{getElementById(id){return id==='opening-cues'?{textContent:cues}:root;}},window:{},location:{search:'?f=0'},URLSearchParams};
  vm.createContext(sandbox);vm.runInContext(scripts[1],sandbox);
  const original=sandbox.window.OpeningPreview.frameState,adapted=require('../src/runtime/opening-state.js').openingState;
  for(let f=0;f<300;f++){
    const a=JSON.parse(JSON.stringify(original(f))),b=JSON.parse(JSON.stringify(adapted(f)));
    for(const key of ['f','hx','camera','stage','approach','waste','offcuts','presses','wave'])assert.deepEqual(b[key],a[key],`${f}:${key}`);
    assert.deepEqual(b.bird.pose,{x:a.bird.x,y:a.bird.y,angle:a.bird.angle,scale:a.bird.scale});assert.equal(b.bird.wing,a.bird.wing);
  }
  assert.throws(()=>adapted(300));
});
test('frozen upstream copies preserve hash identity and all approved diagnostic switch IDs',()=>{
  const record=JSON.parse(fs.readFileSync(path.join(ROOT,'evidence/input-snapshot.json'),'utf8'));
  for(const item of record.inputs)assert.equal(digest(fs.readFileSync(path.join(ROOT,item.copy))),item.sha256,item.copy);
  assert.equal(record.r04_r05_manifest_sha256,'faba9ce84481655405869df167a5529b145acff301e3fb60a8f2f99b893e1957');
  assert.deepEqual(DATA.workload.diagnostic_switches,E.DEFAULTS);
  assert.equal(DATA.cues.project.duration_frames,3600);assert.equal(DATA.cues.project.fps,30);
});
