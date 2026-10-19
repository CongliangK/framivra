'use strict';
// Standard-library source tests; no browser, audio renderer or production entry changes.
const assert=require('node:assert/strict'),test=require('node:test'),crypto=require('node:crypto'),fs=require('node:fs'),path=require('node:path');
const R=require('./opening.js'),D=R.model,G=D.bird,E=D.events,Q=require('./cues.json');
const hash=s=>crypto.createHash('sha256').update(s).digest('hex');
const close=(a,b,eps=1e-8)=>assert.ok(Math.abs(a-b)<eps,`${a} != ${b}`);
function allNumbersFinite(v){if(typeof v==='number')assert.ok(Number.isFinite(v));else if(v&&typeof v==='object')Object.values(v).forEach(allNumbersFinite);}
function inside(ps,p){let q=false;for(let i=0,j=ps.length-1;i<ps.length;j=i++){const a=ps[i],b=ps[j];if((a[1]>p[1])!==(b[1]>p[1])&&p[0]<(b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0])q=!q;}return q;}
function wingWorld(s){return G.wing.map(p=>{const a=R.rot([p[0]-G.hinge[0],p[1]-G.hinge[1]],s.nearWing);const q=R.point([a[0]+G.hinge[0],a[1]+G.hinge[1]],s.pose);return [q[0]-s.camera.x,q[1]-s.camera.y];});}
test('bounded 300-frame source and explicitly separate 299..301 boundary probe',()=>{
 for(const f of [-1,.2,NaN,Infinity,'18',300])assert.throws(()=>R.renderFrame(f));
 for(const f of [-1,302,.2,NaN,'0'])assert.throws(()=>R.stateAt(f));
 assert.throws(()=>R.renderBoundaryFrame(298));
 assert.equal(R.renderFrame(299).state.boundaryProbe,false);assert.equal(R.renderBoundaryFrame(300).state.boundaryProbe,true);
 assert.match(R.renderBoundaryFrame(300).fragment,/BOUNDARY-OCCLUSION-PROBE-NOT-S2/);
});
test('all 300 absolute states are finite, frozen and deterministic under reverse and shuffled revisits',()=>{
 const hashes=[];for(let f=0;f<300;f++){const r=R.renderFrame(f);allNumbersFinite(r.state);assert.ok(Object.isFrozen(r.state.pose));assert.ok(!r.fragment.includes('NaN'));hashes.push(hash(JSON.stringify(r)));}
 for(let f=299;f>=0;f--)assert.equal(hash(JSON.stringify(R.renderFrame(f))),hashes[f]);
 const order=Array.from({length:300},(_,i)=>(i*137)%300);assert.equal(new Set(order).size,300);
 for(const f of order)assert.equal(hash(JSON.stringify(R.renderFrame(f))),hashes[f]);
});
test('skeleton uses the same complete motion and event graph, only secondary facets differ',()=>{
 for(let f=0;f<300;f++)assert.deepEqual(R.renderFrame(f,{mode:'skeleton'}).state,R.renderFrame(f).state);
 assert.notEqual(R.renderFrame(176,{mode:'skeleton'}).fragment,R.renderFrame(176).fragment);
 assert.throws(()=>R.renderFrame(0,{mode:'unknown'}));
});
test('all seven visual source roles can be selected and removed without modifying shared state',()=>{
 for(const id of D.visual_roles){const r=R.renderFrame(109,{role:id});assert.match(r.fragment,new RegExp(`data-role="${id}"`));for(const other of D.visual_roles)if(id!==other)assert.ok(!r.fragment.includes(`data-role="${other}"`));
 const omit=R.renderFrame(109,{excludeRoles:[id]});assert.ok(!omit.fragment.includes(`data-role="${id}"`));assert.deepEqual(omit.state,r.state);}
 assert.throws(()=>R.renderFrame(0,{role:'T20'}));assert.throws(()=>R.renderFrame(0,{excludeRoles:['no']}));
});
test('recognition has exactly 27 identical fragments and does not drift',()=>{
 const f=R.renderFrame(163).fragment;for(let i=163;i<190;i++){assert.equal(R.renderFrame(i).fragment,f);assert.equal(R.stateAt(i).recognition,true);}
 assert.equal(R.stateAt(162).recognition,false);assert.equal(R.stateAt(190).recognition,false);
});
test('support release precedes landing; both feet and toes stay on their exact fixed support through push',()=>{
 close(R.stateAt(80).pose.y,435);close(R.stateAt(96).pose.y,491.8);
 for(let f=80;f<96;f++){const s=R.stateAt(f);for(const foot of s.feetPaths)for(const p of foot)assert.ok(R.point(p,s.pose)[1]<559+1e-8);}
 const fixed=[[[636.2,559],[658.6,559]],[[598.4,559],[619.4,559]]];
 for(let f=96;f<=206;f++){const s=R.stateAt(f);assert.equal(s.grounded,true);s.feetPaths.forEach((foot,i)=>foot.forEach((p,j)=>{const q=R.point(p,s.pose);close(q[0],fixed[i][j][0]);close(q[1],559);}));}
 assert.equal(R.stateAt(207).grounded,false);
});
test('all four actual tool contacts use the declared shared positions and have a clear prior separation',()=>{
 close(R.stateAt(18).upper.gap,0);assert.ok(R.stateAt(17).upper.gap>0);
 close(R.stateAt(67).lower.gap,0);assert.ok(R.stateAt(66).lower.gap>0);
 const s=R.stateAt(109);for(const b of s.trims.blades){b.offset.forEach(v=>close(v,0));[b.a,b.b].forEach((p,i)=>{const q=R.point(p,s.pose);close(q[0],b.edgeWorld[i][0]);close(q[1],b.edgeWorld[i][1]);});}
 for(const b of R.stateAt(108).trims.blades)assert.ok(Math.hypot(...b.offset)>.5,'gold knife edge must remain outside retained head before contact');
 close(R.stateAt(150).eyeTool.gap,0);assert.deepEqual(R.stateAt(150).eyeTool.tip,R.stateAt(150).anchors.eye);
 assert.ok(R.stateAt(149).eyeTool.gap>0);assert.equal(R.stateAt(149).eye,false);assert.equal(R.stateAt(150).eye,true);
});
test('same head blank and complementary masks precede release, with no pre-contact offcut motion',()=>{
 assert.equal(R.stateAt(108).headCut,false);assert.equal(R.stateAt(109).headCut,true);
 for(let f=0;f<=28;f++)close(R.stateAt(f).upper.wasteY,0);
 for(let f=0;f<=80;f++)close(R.stateAt(f).lower.wasteY,0);
 close(R.stateAt(112).trims.wasteT,0);assert.ok(R.stateAt(113).trims.wasteT>0);
 const r=R.renderFrame(18).fragment;assert.match(r,/id="waste-upper" maskUnits="userSpaceOnUse"/);assert.match(r,/id="waste-lower"/);assert.match(r,/upper-complement/);assert.match(r,/lower-complement/);
 assert.deepEqual(G.scraps[0][0],G.head[0]);assert.deepEqual(G.scraps[0][2],G.head[1]);assert.deepEqual(G.scraps[1][0],G.head[5]);assert.deepEqual(G.scraps[1][2],G.head[6]);
});
test('complete knives include attached tangs and are retained through approach/contact/retraction',()=>{
 for(const f of [100,103,108,109,112,120,135]){const r=R.renderFrame(f);assert.match(r.fragment,/data-part="trim-tool-0"/);assert.match(r.fragment,/data-part="trim-tool-1"/);}
 assert.ok(!R.renderFrame(139).fragment.includes('data-part="trim-tool-0"'));
});
test('single near wing covers all viewport corners by f299 and still covers isolated diagnostic guards',()=>{
 for(const f of [299,300,301]){const ps=wingWorld(R.stateAt(f));for(const p of [[0,0],[1280,0],[1280,720],[0,720]])assert.ok(inside(ps,p),`f${f} uncovered corner ${p}`);}
 assert.ok(!inside(wingWorld(R.stateAt(264)),[0,0]));
 assert.deepEqual(R.stateAt(299).pose,R.stateAt(300).pose);assert.deepEqual(R.stateAt(299).pose,R.stateAt(301).pose);
});
test('stationary screen paper stays outside the common camera and camera does not move before launch',()=>{
 for(let f=0;f<=206;f++)assert.deepEqual(R.stateAt(f).camera,{x:0,y:-0});
 const s=R.renderFrame(239).fragment;assert.ok(s.indexOf('data-role="T01"')<s.indexOf('data-part="common-camera"'));
 assert.ok(R.stateAt(239).camera.x>0);
});
test('events are ordered within 300 frames and exact original score onsets are reported without invented peaks',()=>{
 const frames=Object.values(E);for(let i=1;i<frames.length;i++)assert.ok(frames[i]>frames[i-1]);assert.ok(frames.at(-1)<300);
 const scorePath=path.resolve(__dirname,Q.read_only_existing_score.path),bytes=fs.readFileSync(scorePath),score=JSON.parse(bytes);
 assert.equal(hash(bytes),Q.read_only_existing_score.sha256);
 assert.deepEqual(score.events.filter(e=>e.frame>=0&&e.frame<300),Q.read_only_existing_score.events_0_to_299);
 for(const c of Q.comparison){assert.equal(c.measured_acoustic_peak_frame,null);assert.equal(c.listening,'UNRUN');assert.equal(score.events.find(e=>e.id===c.existing_event).frame,c.audio_onset_frame);}
 assert.equal(Q.comparison.find(c=>c.visual_frame===206).audio_onset_frame,205);
 assert.equal(Q.clock.frames,300);assert.equal(Q.clock.samples_per_video_frame,1600);
});
test('mount exposes only explicit frame hook; fresh and revisited results do not depend on DOM history',()=>{
 const attrs={},el={innerHTML:'',setAttribute(k,v){attrs[k]=v;}},doc={getElementById:()=>el},win={location:{search:'?f=176'}};
 const mount=R.mount(doc,win);assert.equal(attrs['data-frame'],'176');const s=el.innerHTML;
 win.__vtx_render(18);win.__vtx_render(176);assert.equal(el.innerHTML,s);assert.equal(mount.frames,300);
 assert.throws(()=>win.__vtx_render(300));assert.equal(Object.keys(win).sort().join(','),'__opening_design_state,__vtx_render,location');
});
test('browser UMD scripts and inline mount execute only in a source-only Node VM with a fake DOM',()=>{
 const vm=require('node:vm'),a={},root={innerHTML:'',setAttribute(k,v){a[k]=v;}},sandbox={window:{location:{search:'?f=176&mode=skeleton'}},document:{getElementById(){return root;}},URLSearchParams};vm.createContext(sandbox);
 const html=fs.readFileSync(path.join(__dirname,'index.html'),'utf8');
 assert.deepEqual([...html.matchAll(/<script src="([^"]+)"/g)].map(m=>m[1]),['geometry.js','opening.js']);
 for(const f of ['geometry.js','opening.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,f),'utf8'),sandbox);
 const inline=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]);assert.equal(inline.length,1);vm.runInContext(inline[0],sandbox);
 assert.equal(a['data-frame'],'176');assert.equal(root.innerHTML,R.renderFrame(176,{mode:'skeleton'}).fragment);
 assert.equal(sandbox.window.__vtx_render(239),239);
});
test('author runtime has no wall clock, network, timer, autonomous animation or random state',()=>{
 const banned=/\b(?:Date|performance|requestAnimationFrame|setTimeout|setInterval|fetch|XMLHttpRequest|WebSocket)\s*[.(]|Math\.random\s*\(|@keyframes|animation\s*:|transition\s*:/;
 for(const f of ['geometry.js','opening.js','index.html'])assert.ok(!banned.test(fs.readFileSync(path.join(__dirname,f),'utf8')),f);
 assert.throws(()=>{D.bird.head[0][0]=123;});
});
test('head offcut life follows actual world geometry crossing the table, independently of tool life',()=>{
 const firstHidden=[];
 for(let i=0;i<2;i++){
  for(let f=109;f<164;f++){
   const s=R.stateAt(f),p=s.trims.wastePieces[i];
   assert.equal(p.visible,p.minY<559);
   assert.equal(R.renderFrame(f).fragment.includes(`data-part="head-ear-${i}"`),p.visible);
   if(!p.visible&&firstHidden[i]===undefined)firstHidden[i]=f;
   for(let j=0;j<p.polygon.length;j++){
    const v=p.polygon[j],q=R.rot([v[0]-p.pivot[0],v[1]-p.pivot[1]],p.angle);
    const actual=R.point([q[0]+p.pivot[0]+p.translation[0],q[1]+p.pivot[1]+p.translation[1]],s.trims.wastePose);
    actual.forEach((n,k)=>close(n,p.world[j][k]));
   }
  }
  assert.ok(firstHidden[i]<163);
  assert.ok(R.stateAt(firstHidden[i]-1).trims.wastePieces[i].minY<559);
  assert.ok(R.stateAt(firstHidden[i]).trims.wastePieces[i].minY>=559);
 }
 assert.deepEqual(firstHidden,[143,135]);
 assert.equal(R.stateAt(139).trims.visible,false);assert.equal(R.stateAt(139).trims.wasteVisible,true);
 for(let f=112;f<143;f++)assert.deepEqual(R.stateAt(f).trims.wastePose,R.stateAt(112).pose);
});
test('all 128 role activation sets own head-waste knockout only when its visible source participates',()=>{
 for(let f=108;f<=143;f++)for(let bits=0;bits<128;bits++){
  const excluded=D.visual_roles.filter((_,i)=>(bits&(1<<i))!==0),active=id=>!excluded.includes(id);
  const fragment=R.renderFrame(f,{excludeRoles:excluded}).fragment;
  const needed=R.stateAt(f).trims.wasteVisible&&active('T06')&&(active('T07')||active('T08'));
  assert.equal(fragment.includes('id="head-waste-order"'),needed);
  assert.equal(fragment.includes('mask="url(#head-waste-order)"'),needed);
 }
 for(const role of D.visual_roles)for(let f=109;f<143;f++)assert.ok(!R.renderFrame(f,{role}).fragment.includes('head-waste-order'));
});
test('every structural mask uses literal unit-luminance white without changing the art palette',()=>{
 assert.equal(D.palette.white,'#FAF7EE');
 for(let f=0;f<300;f++)for(const mask of R.renderFrame(f).fragment.matchAll(/<mask\b[^>]*>([\s\S]*?)<\/mask>/g)){
  const first=mask[1].match(/<rect\b[^>]*fill="([^"]+)"/);assert.ok(first);assert.equal(first[1],'#FFFFFF');
  for(const fill of mask[1].matchAll(/fill="([^"]+)"/g))assert.match(fill[1],/^(?:black|#000000|#FFFFFF)$/);
 }
});
