"""MG HTML reference contracts: Node operation/state checks, not browser pixels.

Keep group-specific coverage and optional dependency policies distinct; all six
suites consume the same reference runtime, scene helpers, and style assets.
"""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
STYLES = ROOT / 'templates/mg-styles'
HTML = STYLES / 'html'
NODE = shutil.which('node')



class ReferenceGroupA(unittest.TestCase):
    def test_contract_semantic_examples_and_arbitrary_frame_order(self):
        script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const root=process.argv[1],runtime=fs.readFileSync(root+'/runtime.js','utf8');
let helpers=runtime.slice(0,runtime.indexOf('const scene=window.MG_REFERENCE;'))+'})();';
for(const id of ['soft-3d','isometric','pixel','line-art']){
 const sandbox={window:{}};vm.createContext(sandbox);vm.runInContext(helpers,sandbox);vm.runInContext(fs.readFileSync(root+'/scene-helpers.js','utf8'),sandbox);
 vm.runInContext(fs.readFileSync(root+'/../styles/'+id+'/assets/html/'+id+'.scene.js','utf8'),sandbox);
 const scene=sandbox.window.MG_REFERENCE,h=sandbox.window.MGReferenceHelpers;
 assert.equal(scene.id,id);assert.equal(scene.examples.length,2);assert.equal(Object.keys(scene.recipe).length,8);
 function draw(d,f,N){let ops=[];const c=new Proxy({}, {get(t,k){if(k==='measureText')return s=>({width:Array.from(s).length*27});if(k==='createRadialGradient')return (...v)=>({addColorStop(...a){ops.push(['stop',...a]);}});if(k in t)return t[k];return (...args)=>{args.forEach(a=>{if(typeof a==='number')assert(Number.isFinite(a));});ops.push([k,...args]);};},set(t,k,v){t[k]=v;ops.push(['set',k,typeof v==='object'?'gradient':v]);return true;}});scene.render(c,d,f,N,h);return JSON.stringify(ops);}
 let traces=[];
 for(const e of scene.examples){scene.validate(e.data,e.frames,h);const before=JSON.stringify(e.data),frames=[0,Math.floor(e.frames/3),Math.floor(2*e.frames/3),e.frames-1];const baseline=frames.map(f=>draw(e.data,f,e.frames));for(const i of [3,0,2,1,3])assert.equal(draw(e.data,frames[i],e.frames),baseline[i]);assert.equal(JSON.stringify(e.data),before);assert(new Set(baseline).size>1);traces.push(baseline[3]);assert.throws(()=>scene.validate(e.data,1,h));}
 assert.notEqual(traces[0],traces[1]);

 if(id==='pixel'){
  const A=scene.examples[0],B=scene.examples[1];
  assert.notEqual(JSON.stringify(A.data.steps.map(s=>s.obstacle)),JSON.stringify(B.data.steps.map(s=>s.obstacle)));
  assert.notEqual(JSON.stringify(A.data.steps.map(s=>[s.in,s.out])),JSON.stringify(B.data.steps.map(s=>[s.in,s.out])));
  const d=B.data,N=B.frames,s=d.steps.find(s=>s.obstacle==='collect'),at=s.in+Math.floor((s.out-s.in-1)/2),before=scene.state(d,at-1),hit=scene.state(d,at);
  assert(!before.collected[d.steps.indexOf(s)]);assert(hit.collected[d.steps.indexOf(s)]);
  const token=JSON.parse(draw(d,at-1,N)).find(o=>o[0]==='fillRect'&&o[3]===8&&o[4]===8),target=token[1]+before.camera+4;
  assert(hit.world<=target+4&&hit.world+8>=target-4);assert(hit.playerY<=token[2]+8&&hit.playerY+12>=token[2]);
  const collectibleCount=f=>JSON.parse(draw(d,f,N)).filter(o=>o[0]==='fillRect'&&o[3]===8&&o[4]===8).length;
  assert.equal(collectibleCount(at-1),1);assert.equal(collectibleCount(at),0);assert.equal(collectibleCount(at+1),0);
  const short=JSON.parse(JSON.stringify(d));short.reward.in=N-6;scene.validate(short,N,h);for(let f=N-6;f<N;f++)assert(scene.state(short,f).reward);
  short.reward.in=N-5;assert.throws(()=>scene.validate(short,N,h));
 }
 if(id==='isometric'){
  const origin=scene.geometry.project(0,0,0),axes=[[1,0,0],[0,1,0],[0,0,1]].map(v=>scene.geometry.project(...v).map((x,i)=>x-origin[i]));
  axes.forEach(v=>assert(Math.abs(Math.hypot(...v)-150)<1e-9));
  assert(Math.abs(axes[0][1]/axes[0][0]-Math.tan(Math.PI/6))<1e-9);
  assert(Math.abs(axes[1][1]/axes[1][0]+Math.tan(Math.PI/6))<1e-9);
 }
 for(const [key,values] of Object.entries(id==='pixel'||id==='isometric'?{seed:[false,'']}:id==='line-art'?{drawing_speed:[false,0,'']}:{pace:[false,0,''],transition_frames:[false,0,'']})){
  for(const value of values){const data=JSON.parse(JSON.stringify(scene.examples[0].data));data[key]=value;assert.throws(()=>scene.validate(data,scene.examples[0].frames,h));}
 }

 if(id==='soft-3d'){
  const e=scene.examples[0],d=JSON.parse(JSON.stringify(e.data)),N=e.frames;
  d.transition_frames=30;d.scene_story[2].frame=107;assert.throws(()=>scene.validate(d,N,h));
  d.scene_story[2].frame=80;d.scene_story[1].frame=1;assert.throws(()=>scene.validate(d,N,h));
  const radius=62+e.data.objects[0].value*.48;
  const sphere=f=>JSON.parse(draw(e.data,f,N)).find(o=>o[0]==='arc'&&Math.abs(o[3]-radius)<1e-8);
  for(const chapter of e.data.scene_story.slice(1))assert.deepEqual(sphere(chapter.frame-1),sphere(chapter.frame));
  const short=JSON.parse(JSON.stringify(e.data));short.transition_frames=12;short.scene_story=[{frame:0,title:'A',layout:'scatter'},{frame:18,title:'B',layout:'result'}];scene.validate(short,36,h);
  const hold=JSON.parse(draw(short,30,36)).find(o=>o[0]==='arc'&&Math.abs(o[3]-radius)<1e-8);
  for(let f=30;f<36;f++)assert.deepEqual(JSON.parse(draw(short,f,36)).find(o=>o[0]==='arc'&&Math.abs(o[3]-radius)<1e-8),hold);
  assert.throws(()=>scene.validate(short,35,h));

  const six=JSON.parse(JSON.stringify(e.data));six.objects=Array.from({length:6},(_,i)=>({...e.data.objects[0],label:'物体'+i,value:100}));six.links=[];scene.validate(six,N,h);
  for(let f=0;f<N;f++){
   const visible=JSON.parse(draw(six,f,N)).filter(o=>o[0]==='arc'&&Math.abs(o[3]-110)<1e-8);
   assert.equal(visible.length,6);for(const o of visible){assert(o[1]-110>=100&&o[1]+110<=1820);assert(o[2]-110>=300&&o[2]+110<=850);}
  }
  const endpoint=sphere(N-1);assert(Math.abs(endpoint[1]-960)<1e-8);assert(Math.abs(endpoint[2]-480)<1e-8);
 }

 assert.throws(()=>scene.validate({...scene.examples[0].data,title:'X'.repeat(200),mission:'X'.repeat(200)},scene.examples[0].frames,h));
}
'''
        subprocess.run(['node','-e',script,str(HTML)],check=True,cwd=ROOT,capture_output=True,text=True)

    def test_local_shells_and_no_scene_clock_or_dom(self):
        for name in ('soft-3d','isometric','pixel','line-art'):
            source=(HTML.parent/'styles'/name/'assets/html'/f'{name}.scene.js').read_text()
            for banned in ('requestAnimationFrame','performance.now','Date.now','document.','fetch('):
                self.assertNotIn(banned,source)
            shell=(HTML.parent/'styles'/name/'assets/html'/f'{name}.html').read_text()
            self.assertIn(f'src="{name}.scene.js"',shell)
            self.assertLess(shell.index('scene-helpers.js'), shell.index(f'src="{name}.scene.js"'))
            self.assertIn('src="../../../../html/runtime.js"',shell)
            self.assertNotIn('https://',shell)


class AllReferenceExamples(unittest.TestCase):
    def test_every_example_and_balanced_state(self):
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert'),root=process.argv[1];
const helper=fs.readFileSync(root+'/runtime.js','utf8').split('const scene=window.MG_REFERENCE;')[0]+'})();';
const path=require('path');const htmls=fs.readdirSync(root+'/../styles').flatMap(id=>{const dir=path.join(root,'../styles',id,'assets/html');return fs.readdirSync(dir).filter(p=>p.endsWith('.html')).map(p=>path.join(dir,p));});assert.equal(htmls.length,15);
for(const html of htmls){const shell=fs.readFileSync(html,'utf8'),names=[...shell.matchAll(/<script src="([^"]+)"/g)].map(m=>m[1]).filter(n=>!n.endsWith('/runtime.js'));
 let makeContext;const box={window:{},OffscreenCanvas:class {constructor(w,h){this.width=w;this.height=h}getContext(){return makeContext()}}};vm.createContext(box);vm.runInContext(helper,box);for(const name of names)vm.runInContext(fs.readFileSync(path.join(path.dirname(html),name),'utf8'),box);
 const scene=box.window.MG_REFERENCE,h=box.window.MGReferenceHelpers;
 for(const ex of scene.examples){scene.validate(ex.data,ex.frames,h);const snapshot=JSON.stringify(ex.data);
  const frameList=['liquid','aurora-glass'].includes(scene.id)?[...new Set([0,1,Math.floor(ex.frames*.25),Math.floor(ex.frames*.5),Math.floor(ex.frames*.75),ex.frames-2,ex.frames-1])]:Array.from({length:ex.frames},(_,f)=>f);
  for(const f of frameList){let state={font:'24px sans-serif'},stack=[];
   makeContext=()=>new Proxy({canvas:{width:1920,height:1080}}, {get(t,k){if(k in t)return t[k];if(k==='createImageData'||k==='getImageData')return (...a)=>{const w=a.length===4?a[2]:a[0],h=a.length===4?a[3]:a[1];return {width:w,height:h,data:new Uint8ClampedArray(w*h*4)}};if(k==='measureText')return s=>({width:Array.from(String(s)).length*24});return ()=>{};},set(t,k,v){t[k]=v;return true}});
   const c=new Proxy({}, {get(t,k){if(k in state)return state[k];if(k==='save')return ()=>stack.push({...state});if(k==='restore')return ()=>{assert(stack.length,'extra restore');state=stack.pop()};
    if(k==='measureText')return s=>({width:Array.from(String(s)).reduce((n,ch)=>n+(/[\u3000-\uffff]/.test(ch)?1:.6),0)*Number((state.font.match(/([\d.]+)px/)||[])[1]||24)});
    if(k==='canvas')return {width:1920,height:1080};if(k==='createImageData'||k==='getImageData')return (...a)=>{const w=a.length===4?a[2]:a[0],h=a.length===4?a[3]:a[1];return {width:w,height:h,data:new Uint8ClampedArray(w*h*4)}};
    if(k.startsWith('create')&&k.endsWith('Gradient'))return ()=>({addColorStop(){}});
    return (...args)=>{for(const a of args)if(typeof a==='number')assert(Number.isFinite(a),`${html} ${f} ${k}`)};},set(t,k,v){state[k]=v;return true}});
   try{scene.render(c,ex.data,f,ex.frames,h)}catch(e){throw Error(`${html}/${ex.name}/f${f}: ${e.message}`)}
   assert.equal(stack.length,0,`${html} unbalanced frame ${f}`);
  }
  assert.equal(JSON.stringify(ex.data),snapshot,'scene mutated content');
 }
}
'''
        result = subprocess.run(['node', '-e', script, str(HTML)], text=True,
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


HARNESS = r'''
const fs=require('fs'),vm=require('vm'),crypto=require('crypto');
const root=process.argv[1];
const runtime=fs.readFileSync(root+'/runtime.js','utf8').split('window.MGReferenceHelpers=h;')[0]+'window.MGReferenceHelpers=h;})();';
const sandbox={window:{},console};vm.createContext(sandbox);vm.runInContext(fs.readFileSync(process.argv[1]+'/scene-helpers.js','utf8'),sandbox);vm.runInContext(runtime,sandbox);const h=sandbox.window.MGReferenceHelpers;
let calls=[],stack=0;
const ctx=new Proxy({save(){stack++;calls.push(['save']);},restore(){if(--stack<0)throw Error('unbalanced restore');calls.push(['restore']);},measureText(s){return {width:Array.from(s).reduce((a,ch)=>a+(ch.charCodeAt(0)>255?1:.58)*parseFloat(this.font.match(/(\d+)px/)[1]),0)};},createImageData(w,hh){return {data:new Uint8ClampedArray(w*hh*4),width:w,height:hh};},putImageData(im){calls.push(['rgba',crypto.createHash('sha256').update(im.data).digest('hex')]);}}, {get(o,k){if(k in o)return o[k];return (...args)=>{if(args.some(a=>typeof a==='number'&&!Number.isFinite(a)))throw Error('nonfinite '+k);calls.push([k,...args]);};},set(o,k,v){if(typeof v==='number'&&!Number.isFinite(v))throw Error('nonfinite state');o[k]=v;calls.push(['set',k,v]);return true;}});
function draw(scene,d,f,N){calls=[];stack=0;scene.render(ctx,d,f,N,h);if(stack)throw Error('unbalanced canvas stack');return crypto.createHash('sha256').update(JSON.stringify(calls)).digest('hex');}
const result=[];
for(const id of ['cel-boil','collage','morph','liquid']){const source=fs.readFileSync(root+'/../styles/'+id+'/assets/html/'+id+'.js','utf8');if(/requestAnimationFrame|Date\.|performance\.|document\.|Math.random/.test(source))throw Error('scene clock/DOM '+id);vm.runInContext(source,sandbox);const s=sandbox.window.MG_REFERENCE;if(s.id!==id||s.examples.length!==2||s.recipe['4–6拍'].length<4)throw Error('contract');let hashes=[];for(const ex of s.examples){const d=ex.data,N=ex.frames,before=JSON.stringify(d);s.validate(d,N,h);const stages=d.beats||d.shapes||d.stages,frames=new Set([0,N-1,Math.floor(N/2)]);for(const b of stages)for(const f of [b.in,b.in+1,b.out-1])frames.add(f);for(const f of frames){const a=draw(s,d,f,N);draw(s,d,N-1-f,N);if(a!==draw(s,d,f,N))throw Error('seek mismatch '+id);hashes.push(a);}if(JSON.stringify(d)!==before)throw Error('input mutated');const bad=JSON.parse(before);bad.title='超'.repeat(61);let refused=false;try{s.validate(bad,N,h);}catch(e){refused=true;}if(!refused)throw Error('overflow not rejected');}
if(new Set(hashes).size<4)throw Error('inactive animation');const ex=s.examples[0],d=JSON.parse(JSON.stringify(ex.data)),N=ex.frames;
if(id==='cel-boil'){d.characters[0].shape=d.characters[0].shape==='star'?'cat':'star';if(draw(s,d,35,N)===draw(s,ex.data,35,N))throw Error('shape not consumed');const bad=JSON.parse(JSON.stringify(ex.data));bad.exposures={action:3,body:2,hold:3};bad.beats=[{actor:bad.characters[0].id,action:'hop',in:0,out:12,anticipation:2,motion:4,distance:48},{actor:bad.characters[1].id,action:'settle',in:12,out:30,anticipation:2,motion:4}];let refused=false;try{s.validate(bad,30,h);}catch(e){refused=true;}if(!refused)throw Error('exposure budget');
const legal=JSON.parse(JSON.stringify(bad));legal.beats=[{actor:legal.characters[0].id,action:'hop',in:0,out:15,anticipation:3,motion:7,distance:48},{actor:legal.characters[1].id,action:'settle',in:15,out:30,anticipation:3,motion:7}];s.validate(legal,30,h);
function actorBounds(f){let polygons=[];const hh={...h,polygon(c,pts,color){polygons.push(pts);h.polygon(c,pts,color);}};calls=[];stack=0;s.render(ctx,legal,f,30,hh);const pts=polygons[0];return [Math.min(...pts.map(p=>p[0])),Math.min(...pts.map(p=>p[1])),Math.max(...pts.map(p=>p[0])),Math.max(...pts.map(p=>p[1]))];}
const ground=actorBounds(3),air=actorBounds(6),rest=actorBounds(0),prep=actorBounds(2);if(air[1]>=ground[1]-90)throw Error('sampled hop has no visible airborne drawing');if(prep[3]-prep[1]>=rest[3]-rest[1])throw Error('sampled anticipation not squashed');if(draw(s,legal,6,30)!==draw(s,legal,8,30))throw Error('actual action cel not held');}
if(id==='collage'){d.parts[0].shape='leaf';if(draw(s,d,0,N)===draw(s,ex.data,0,N))throw Error('part not consumed');const last=s.examples[1].data.beats.at(-1);if(last.out-last.in<75)throw Error('preferred-readable hold');
// Rigid child's hinge must stay attached under an inherited parent rotation.
const rigid=JSON.parse(JSON.stringify(ex.data));rigid.parts.forEach(p=>{p.motion={hinge:0,drift:[0,0]};});const rootPart=rigid.parts.find(p=>p.id==='body'),child=rigid.parts.find(p=>p.parent==='body');rootPart.angle=30;child.angle=0;child.pivot=[.4,-.6];let polys=[];const hh={...h,polygon(c,pts,color){polys.push({pts,color});h.polygon(c,pts,color);}};calls=[];stack=0;s.render(ctx,rigid,0,N,hh);const index=rigid.parts.indexOf(child),actual=polys[5].pts[0],a=30*Math.PI/180,rx=child.x*rootPart.scale,ry=child.y*rootPart.scale,cx=rootPart.x*1920+rx*Math.cos(a)-ry*Math.sin(a),cy=rootPart.y*1080+rx*Math.sin(a)+ry*Math.cos(a),px=-.18*child.scale,py=-.15*child.scale,expected=[cx+px*Math.cos(a)-py*Math.sin(a),cy+px*Math.sin(a)+py*Math.cos(a)];if(Math.hypot(actual[0]-expected[0],actual[1]-expected[1])>.001)throw Error('child hinge slips under parent rotation');}
if(id==='morph'){d.shapes[0].shape='star';if(draw(s,d,0,N)===draw(s,ex.data,0,N))throw Error('semantic shape inactive');}
if(id==='liquid'){d.stages.forEach(a=>a.amount=.75);s.validate(d,N,h);if(draw(s,d,80,N)===draw(s,ex.data,80,N))throw Error('amount inactive');d.stages[1].amount=.8;let refused=false;try{s.validate(d,N,h);}catch(e){refused=true;}if(!refused)throw Error('discontinuous amount');if(draw(s,ex.data,N-1,N)!==draw(s,ex.data,N-12,N))throw Error('liquid final surface/type not held');}
result.push({id,examples:2,seek_trace_checks:hashes.length,distinct: new Set(hashes).size});}
console.log(JSON.stringify(result));
'''


@unittest.skipUnless(shutil.which('node'), 'Node required for Canvas call trace')
class HTMLGroupB(unittest.TestCase):
    def test_semantic_examples_frame_boundaries_seek_and_mutation(self):
        result = subprocess.run(['node', '-e', HARNESS, str(HTML)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr[-4000:])
        data = json.loads(result.stdout)
        self.assertEqual(len(data), 4)
        self.assertTrue(all(item['seek_trace_checks'] >= 18 for item in data))

    def test_collage_root_sentinel_and_user_identifiers(self):
        # Actual scene drawing operations, not browser/native pixels.
        setup = HARNESS.split('const result=[];')[0]
        probe = r"""
const assert=require('assert');
vm.runInContext(fs.readFileSync(root+'/../styles/collage/assets/html/collage.js','utf8'),sandbox);
const scene=sandbox.window.MG_REFERENCE,N=120;
const part=(id,x,y)=>({id,shape:'planet',parent:null,x,y,scale:30,angle:0,
 pivot:[0,0],motion:{hinge:0,drift:[0,0]}});
const original={title:'Root anchors',seed:0,exposure:3,
 parts:[part('base',.4,.5),part('second',.6,.5),part('third',.7,.6),
 {...part('child',.3,.3),parent:'base',scale:24}],
 beats:['awaken','release','reframe','resolve'].map((mode,i)=>({mode,
  in:30*i,out:30*(i+1),text:mode,active:[]}))};
scene.validate(original,N,h);
const frames=[0,31,91,119],expected=frames.map(f=>draw(scene,original,f,N));
for(const id of ['null','constructor']){
 const renamed=JSON.parse(JSON.stringify(original));
 renamed.parts[0].id=id;renamed.parts[3].parent=id;
 scene.validate(renamed,N,h);const snapshot=JSON.stringify(renamed);
 for(const index of [3,0,2,1,3])assert.equal(draw(scene,renamed,frames[index],N),expected[index],
  'renaming an id must preserve root anchors and actual child links: '+id);
 assert.equal(JSON.stringify(renamed),snapshot,'input must not mutate');
}
for(const id of ['__proto__','hasOwnProperty','toString']){
 const invalid=JSON.parse(JSON.stringify(original));invalid.parts[0].id=id;
 invalid.parts[3].parent=id;assert.throws(()=>scene.validate(invalid,N,h));
}
const missing=JSON.parse(JSON.stringify(original));missing.parts[3].parent='constructor';
assert.throws(()=>scene.validate(missing,N,h),'inherited object keys are not declared parents');
"""
        result = subprocess.run(['node','-e',setup+probe,str(HTML)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr[-4000:])

    def test_liquid_shortest_resolve_has_six_identical_actual_rgba_frames(self):
        # Reuse only the mock Canvas/helper setup, avoiding the unrelated suite.
        setup = HARNESS.split('const result=[];')[0]
        probe = r"""
vm.runInContext(fs.readFileSync(root+'/../styles/liquid/assets/html/liquid.js','utf8'),sandbox);
const scene=sandbox.window.MG_REFERENCE,d=JSON.parse(JSON.stringify(scene.examples[0].data));
d.stages.forEach((s,i)=>{s.in=i*10;s.out=(i+1)*10;});
d.stages[3].out=42;
let refused=false;try{scene.validate(d,42,h);}catch(e){if(!/six complete held frames/.test(e.message))throw e;refused=true;}
if(!refused)throw Error('12-frame resolve leaves only5 complete frames');
d.stages[3].out=43;scene.validate(d,43,h);
const rgba=[],traces=[];
for(let f=37;f<43;f++){traces.push(draw(scene,d,f,43));const pixels=calls.find(c=>c[0]==='rgba');if(!pixels)throw Error('no actual RGBA buffer');rgba.push(pixels[1]);}
if(new Set(rgba).size!==1||new Set(traces).size!==1)throw Error('last6 surfaces/type not held');
draw(scene,d,36,43);if(calls.find(c=>c[0]==='rgba')[1]===rgba[0])throw Error('preceding motion drawing already identical');
scene.examples.forEach(ex=>scene.validate(ex.data,ex.frames,h));
console.log(JSON.stringify({minimum_resolve_frames:13,rejected:12,complete_frames:[37,38,39,40,41,42],rgba_sha256:rgba[0]}));
"""
        result = subprocess.run(['node','-e',setup+probe,str(HTML)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr[-4000:])
        report = json.loads(result.stdout)
        self.assertEqual(report['minimum_resolve_frames'],13)
        self.assertEqual(len(report['complete_frames']),6)

    def test_production_cards_offer_specific_semantic_editing_exercises(self):
        probe = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const sandbox={window:{}};vm.createContext(sandbox);vm.runInContext(fs.readFileSync(process.argv[1]+'/scene-helpers.js','utf8'),sandbox);
const terms={'cel-boil':['characters[0].shape','anticipation','motion','至少7'],collage:['jaw.pivot','head.angle','铰链'],morph:['leaf→star→arrow','transition','10帧'],liquid:['0.65','resolve.label','12帧','13帧','末6帧']};
for(const [id,words] of Object.entries(terms)){
 vm.runInContext(fs.readFileSync(process.argv[1]+'/../styles/'+id+'/assets/html/'+id+'.js','utf8'),sandbox);
 const card=sandbox.window.MG_REFERENCE.recipe;
 assert.equal(Object.keys(card).length,8);
 assert.equal(typeof card['编辑练习'],'string');
 words.forEach(word=>assert(card['编辑练习'].includes(word),id+': '+word));
 assert(card['4–6拍'].length>=4&&card['4–6拍'].length<=6);
}
"""
        result = subprocess.run(['node','-e',probe,str(HTML)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_shells_use_shared_runtime_and_independent_scene(self):
        helpers = (HTML/'scene-helpers.js').read_text()
        for prohibited in ('requestAnimationFrame', 'Date.', 'performance.', 'document.', 'Math.random'):
            self.assertNotIn(prohibited, helpers)
        for name in ['cel-boil','collage','morph','liquid']:
            source = (HTML.parent/'styles'/name/'assets/html'/(name+'.html')).read_text()
            self.assertIn('src="'+name+'.js"', source)
            self.assertLess(source.index('src="../../../../html/scene-helpers.js"'), source.index('src="'+name+'.js"'))
            self.assertIn('src="../../../../html/runtime.js"', source)
            self.assertIn('width="1920" height="1080"', source)
            self.assertNotIn('https://', source)


@unittest.skipUnless(NODE,'Node is required for source-only HTML reference checks')
class HtmlReferenceC(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        program=r'''
const fs=require('fs'),vm=require('vm'),crypto=require('crypto'),path=require('path');
const base=process.argv[1],records=[];
function digest(v){return crypto.createHash('sha256').update(v).digest('hex');}
let calls=[];
function clean(v){if(typeof v==='number'){if(!Number.isFinite(v))throw Error('non-finite Canvas coordinate');return Math.round(v*1e8)/1e8;}if(Array.isArray(v))return v.map(clean);if(v&&v.data)return['pixels',v.width,v.height,digest(v.data)];if(v&&v.width)return['canvas',v.width,v.height];if(typeof v==='function')return'function';if(v&&typeof v==='object')return'gradient';return v;}
function context(canvas){const state={canvas,font:'24px sans-serif',measureText(s){const n=Number((this.font.match(/(\d+(?:\.\d+)?)px/)||[])[1])||24;return{width:Array.from(String(s)).reduce((sum,ch)=>sum+n*(ch.charCodeAt(0)>255?1:.58),0)};},createRadialGradient(){return{addColorStop(){}};},createLinearGradient(){return{addColorStop(){}};},getImageData(x,y,w,h){const data=new Uint8ClampedArray(w*h*4);for(let i=0;i<data.length;i+=4){data[i]=(i/4)%251;data[i+1]=Math.floor(i/4/w)%251;data[i+2]=90;data[i+3]=255;}return{data,width:w,height:h};},createImageData(w,h){return{data:new Uint8ClampedArray(w*h*4),width:w,height:h};}};return new Proxy(state,{get(t,k){if(k in t)return t[k];return(...args)=>{calls.push([String(k),...args.map(clean)]);};},set(t,k,v){t[k]=v;return true;}});}
class Offscreen{constructor(w,h){this.width=w;this.height=h;this.ctx=context(this);}getContext(){return this.ctx;}}
for(const id of ['aurora-glass','sticker','variety-captions']){
 const box={window:{},OffscreenCanvas:Offscreen,Uint8ClampedArray,console,Math,Number,Array,Object,Set,Error};vm.createContext(box);
 const runtime=fs.readFileSync(path.join(base,'runtime.js'),'utf8');vm.runInContext(runtime.slice(0,runtime.indexOf('const scene=window.MG_REFERENCE;'))+'})();',box);
 vm.runInContext(fs.readFileSync(path.join(base,'../styles',id,'assets/html',id+'.js'),'utf8'),box);const s=box.window.MG_REFERENCE,h=box.window.MGReferenceHelpers;
 const results=[];
 function trace(data,f,N){calls=[];const canvas={width:1920,height:1080};s.render(context(canvas),data,f,N,h);return{hash:digest(JSON.stringify(calls)),texts:calls.filter(x=>x[0]==='fillText').map(x=>String(x[1])).join('')};}
 for(const ex of s.examples){const before=JSON.stringify(ex.data);s.validate(ex.data,ex.frames,h);const a=trace(ex.data,Math.floor(ex.frames/2),ex.frames);trace(ex.data,ex.frames-1,ex.frames);const b=trace(ex.data,Math.floor(ex.frames/2),ex.frames);if(a.hash!==b.hash)throw Error('order-dependent drawing trace');if(before!==JSON.stringify(ex.data))throw Error('input mutation');const semantic=[];
  if(id==='sticker'){const end=ex.data.beats[3],first=end.out-end.overview_hold_frames;if(trace(ex.data,first,ex.frames).hash!==trace(ex.data,ex.frames-1,ex.frames).hash)throw Error('overview not actually held');const state=s.inspect(ex.data,ex.frames-1,ex.frames,h);if(!state.overview_complete||Math.abs(state.zoom-1/3)>1e-12)throw Error('overview final not complete');semantic.push(state);}
  if(id==='aurora-glass'){for(const c of ex.data.chapters){const f=c.in+c.reveal_frames-1,st=s.inspect(ex.data,f,ex.frames,h);if(!st.completed||!st.enabled.every(Boolean)||st.displayed!==c.value||st.held_frames<c.hold_frames)throw Error('microUI completion/hold budget failed');semantic.push(st);}}
  if(id==='variety-captions'){for(const c of ex.data.reactions){const st=s.inspect(ex.data,c.out-1,ex.frames,h);if(!st.all_glyphs_visible||!st.fully_stable||st.stable_hold<6)throw Error('caption ends before complete reading state');const drawn=trace(ex.data,c.out-1,ex.frames).texts;if(!drawn.includes(c.text))throw Error('complete caption glyphs not drawn');semantic.push(st);}}
  results.push({name:ex.name,frames:ex.frames,trace_equal:true,semantic});
 }
 const bad=JSON.parse(JSON.stringify(s.examples[0].data));let invalidN=s.examples[0].frames;if(id==='sticker')bad.beats[3].overview_hold_frames=invalidN;if(id==='aurora-glass')bad.chapters[0].hold_frames=invalidN;if(id==='variety-captions')bad.reactions[0].out=bad.reactions[0].in+4;let rejected=false;try{s.validate(bad,invalidN,h);}catch(e){rejected=true;}if(!rejected)throw Error('invalid duration accepted');

 if(id==='variety-captions'){
  const edge=JSON.parse(JSON.stringify(s.examples[0].data));edge.reactions=[{in:0,out:30,text:'好',emotion:'emphasis',stagger:0,punchFrame:29}];let denied=false;try{s.validate(edge,30,h);}catch(e){denied=true;}if(!denied)throw Error('late punch accepted without stable hold');const border=JSON.parse(JSON.stringify(edge));border.subject_rect=[0.2,0.1,0.8,0.27];let protectedEdge=false;try{s.validate(border,30,h);}catch(e){protectedEdge=true;}if(!protectedEdge)throw Error('expanded caption bounding rectangle ignored');edge.reactions[0].text='Run now!';edge.reactions[0].punchFrame=19;s.validate(edge,30,h);edge.reactions[0].text='bad\nline';let multiline=false;try{s.validate(edge,30,h);}catch(e){multiline=true;}if(!multiline)throw Error('literal newline accepted');edge.reactions[0].text='好';edge.reactions[0].punchFrame=19;s.validate(edge,30,h);const st=s.inspect(edge,29,30,h);if(!st.fully_stable||st.stable_hold!==6||!trace(edge,29,30).texts.includes('好'))throw Error('minimal cue final state missing');
 }
 if(id==='sticker'){
  const edge=JSON.parse(JSON.stringify(s.examples[0].data));const b=edge.beats[1];b.out=b.in+10;b.move_frames=1;b.closeup_hold_frames=9;edge.beats[2].in=b.out;let denied=false;try{s.validate(edge,s.examples[0].frames,h);}catch(e){denied=true;}if(!denied)throw Error('incomplete decomposition accepted');
 }
 records.push({id,examples:results,invalid_budget_rejected:rejected,recipe_fields:Object.keys(s.recipe)});
}
console.log(JSON.stringify(records));
'''
        result=subprocess.run([NODE,'-e',program,str(ROOT/'templates/mg-styles/html')],capture_output=True,text=True,cwd=ROOT)
        if result.returncode:
            raise AssertionError(result.stderr)
        cls.records=json.loads(result.stdout)

    def test_two_semantic_examples_and_order_independent_operations(self):
        self.assertEqual({r['id'] for r in self.records},{'aurora-glass','sticker','variety-captions'})
        for record in self.records:
            self.assertEqual(len(record['examples']),2)
            self.assertTrue(all(e['trace_equal'] and e['semantic'] for e in record['examples']))

    def test_completed_readable_states_and_invalid_budgets(self):
        for record in self.records:
            self.assertTrue(record['invalid_budget_rejected'])
            self.assertGreaterEqual(len(record['recipe_fields']),7)
            self.assertIn('编辑练习',record['recipe_fields'])

    def test_local_shell_resources_and_no_scene_clock(self):
        for style in ('aurora-glass','sticker','variety-captions'):
            html=(ROOT/f'templates/mg-styles/styles/{style}/assets/html/{style}.html').read_text()
            self.assertIn(f'src="{style}.js"',html)
            self.assertIn('src="../../../../html/runtime.js"',html)
            code=(ROOT/f'templates/mg-styles/styles/{style}/assets/html/{style}.js').read_text()
            for forbidden in ('requestAnimationFrame(', 'Date.now(', 'performance.now(', 'Math.random(', 'fetch('):
                self.assertNotIn(forbidden,code)


@unittest.skipUnless(shutil.which('node'),'node required')
class HTMLDTests(unittest.TestCase):
 def test_scenes_semantics_and_integer_seek(self):
  script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert'),root=process.argv[1];
let sandbox={window:{}};vm.createContext(sandbox);vm.runInContext(fs.readFileSync(root+'/scene-helpers.js','utf8'),sandbox);
let runtime=fs.readFileSync(root+'/runtime.js','utf8').split('window.MGReferenceHelpers=h;')[0]+'window.MGReferenceHelpers=h;})();';vm.runInContext(runtime,sandbox);const h=sandbox.window.MGReferenceHelpers;
function draw(scene,d,f,N){const calls=[];const ctx=new Proxy({measureText:s=>({width:Array.from(String(s)).length*20})},{get(t,k){if(k in t)return t[k];return (...args)=>{for(const a of args)if(typeof a==='number')assert(Number.isFinite(a),k+' nonfinite');calls.push([k,...args]);};},set(t,k,v){t[k]=v;calls.push(['set',k,v]);return true;}});scene.render(ctx,d,f,N,h);return JSON.stringify(calls);}
for(const id of ['flat-vector','hud','bauhaus','synthwave']){vm.runInContext(fs.readFileSync(root+'/../styles/'+id+'/assets/html/'+id+'.js','utf8'),sandbox);const s=sandbox.window.MG_REFERENCE;assert.equal(s.id,id);assert.equal(s.examples.length,2);for(const k of ['适用/不适用','视觉隐喻','4–6拍','不可删特征','可替语义','动作/转场/声音点','失败反例'])assert(s.recipe[k]);let outputs=[];for(const e of s.examples){s.validate(e.data,e.frames,h);const key=Math.floor(e.frames*.67),a=draw(s,e.data,key,e.frames);draw(s,e.data,0,e.frames);draw(s,e.data,e.frames-1,e.frames);assert.equal(a,draw(s,e.data,key,e.frames),'seek');outputs.push(a);assert.throws(()=>s.validate(e.data,29,h));const bad=JSON.parse(JSON.stringify(e.data));bad.seed=1.5;assert.throws(()=>s.validate(bad,e.frames,h));for(let f=0;f<e.frames;f++)draw(s,e.data,f,e.frames);if(['flat-vector','synthwave'].includes(id))assert.equal(draw(s,e.data,e.frames-1,e.frames),draw(s,e.data,e.frames-2,e.frames),'final hold');const invalid=JSON.parse(JSON.stringify(e.data));if(id==='hud')invalid.target.points[0].y=.9;else if(id==='flat-vector')invalid.links[0].to='missing';else if(id==='bauhaus')invalid.beats[0].target='missing';else invalid.waypoints[0].side='center';assert.throws(()=>s.validate(invalid,e.frames,h));}assert.notEqual(outputs[0],outputs[1]);
if(id==='hud'){
 const e=s.examples[0],d=JSON.parse(JSON.stringify(e.data));d.telemetry[0].value=.125;d.telemetry[0].unit='m';s.validate(d,e.frames,h);
 const ink=JSON.parse(draw(s,d,e.frames-1,e.frames)).filter(c=>c[0]==='fillText').map(c=>c[1]);assert(ink.includes('0.125 m'),'actual final text preserves supplied precision');
}
if(id==='bauhaus'){
 const d={title:'模数',subtitle:'轴心',seed:0,palette:['#eeeeee','#111111','#ff0000','#ffff00','#0000ff'],tick_frames:15,modules:[{id:'a',shape:'square',span:3,color:2}],initial_layout:'a',layouts:{a:[{id:'a',cell:[2,1],turn:0}],b:[{id:'a',cell:[2,1],turn:3}]},beats:[{in:0,out:37,label:'落定',target:'b',axis:'corner',move_frames:31,cascade_frames:0}]};
 assert.throws(()=>s.validate(d,37,h),'unrounded budget rejected');d.beats[0].out=51;s.validate(d,51,h);
 const final=draw(s,d,50,51);for(let f=45;f<51;f++)assert.equal(draw(s,d,f,51),final,'six stable quantized end frames');
 // Reconstruct the actual canvas transform of the filled module square.
 let matrix=[1,0,0,1,0,0],stack=[],rects=[];const mul=(a,b)=>[a[0]*b[0]+a[2]*b[1],a[1]*b[0]+a[3]*b[1],a[0]*b[2]+a[2]*b[3],a[1]*b[2]+a[3]*b[3],a[0]*b[4]+a[2]*b[5]+a[4],a[1]*b[4]+a[3]*b[5]+a[5]];
 for(const call of JSON.parse(final)){const [op,...a]=call;if(op==='save')stack.push(matrix.slice());else if(op==='restore')matrix=stack.pop();else if(op==='translate')matrix=mul(matrix,[1,0,0,1,a[0],a[1]]);else if(op==='rotate')matrix=mul(matrix,[Math.cos(a[0]),Math.sin(a[0]),-Math.sin(a[0]),Math.cos(a[0]),0,0]);else if(op==='fillRect'&&a[2]===300){rects.push([[a[0],a[1]],[a[0]+a[2],a[1]],[a[0]+a[2],a[1]+a[3]],[a[0],a[1]+a[3]]].map(([x,y])=>[matrix[0]*x+matrix[2]*y+matrix[4],matrix[1]*x+matrix[3]*y+matrix[5]]));}}
 assert.equal(rects.length,1);const pts=rects[0];for(const [x,y] of pts){assert(Math.abs((x-300)/100-Math.round((x-300)/100))<1e-8);assert(Math.abs((y-380)/100-Math.round((y-380)/100))<1e-8);assert(x>=180&&x<=1740&&y>=310&&y<=1040);}
 assert(Math.abs(Math.min(...pts.map(p=>p[0]))-500)<1e-8);assert(Math.abs(Math.max(...pts.map(p=>p[1]))-780)<1e-8);
 d.tick_frames=1;d.beats[0].move_frames=30;d.beats[0].out=51;d.layouts.a[0].cell=[0,0];d.layouts.b[0].cell=[0,0];d.layouts.b[0].turn=2;assert.throws(()=>s.validate(d,51,h),'rotated outline safety');
}
if(id==='flat-vector'){
 const e=s.examples[0],b=e.data.stages[1].in;const circle=f=>JSON.parse(draw(s,e.data,f,e.frames)).find(c=>c[0]==='arc');
 assert.deepEqual(circle(b-1).slice(1,4),circle(b).slice(1,4),'question to build origin continuity');const labelState=f=>{const calls=JSON.parse(draw(s,e.data,f,e.frames));let font;for(const call of calls){if(call[0]==='set'&&call[1]==='font')font=call[2];if(call[0]==='fillText'&&call[1]===e.data.origin.label)return {call,font};}throw Error('origin label missing');};assert.deepEqual(labelState(b-1),labelState(b),'origin actual fillText/font continuity');assert.deepEqual(circle(b).slice(1,3),[960,590]);assert.deepEqual(circle(b+12).slice(1,3),[290,600]);
}
if(id==='flat-vector'){const e=s.examples[0],d=JSON.parse(JSON.stringify(e.data)),build=d.stages[1];d.stages[2].out=build.out+14;assert.throws(()=>s.validate(d,build.out+14,h));d.stages[2].out=build.out+31;s.validate(d,build.out+31,h);const N=build.out+31,last=JSON.parse(draw(s,d,N-1,N));const positions=last.filter(x=>x[0]==='translate').map(x=>x.slice(1));assert.deepEqual(positions,[[420,600],[960,600],[1500,600]]);for(let f=N-6;f<N;f++)assert.equal(draw(s,d,f,N),draw(s,d,N-1,N),'six fully settled frames');d.palette=['#000001','#000002','#000003','#000004','#000005','#000006'];s.validate(d,N,h);const styled=draw(s,d,N-1,N);for(const color of d.palette.slice(0,3).concat(d.palette.slice(4)))assert(styled.includes(color),'palette consumed '+color);d.palette=['red'];assert.throws(()=>s.validate(d,N,h));}}
'''
  result=subprocess.run(['node','-e',script,str(HTML)],capture_output=True,text=True)
  self.assertEqual(result.returncode,0,result.stderr)
 def test_all_fifteen_recipe_cards_have_eight_fields(self):
  script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert'),root=process.argv[1];
const path=require('path');const files=fs.readdirSync(root+'/../styles').map(id=>{const dir=path.join(root,'../styles',id,'assets/html');const shell=fs.readFileSync(path.join(dir,id+'.html'),'utf8');return [...shell.matchAll(/<script src="([^"]+)"/g)].map(m=>m[1]).filter(p=>!p.endsWith('/runtime.js')).map(p=>path.join(dir,p));});
assert.equal(files.length,15);
const expected=['适用/不适用','视觉隐喻','4–6拍','不可删特征','可替语义','动作/转场/声音点','失败反例','编辑练习'];
for(const file of files){
 const sandbox={window:{}};vm.createContext(sandbox);
 for(const dependency of file)vm.runInContext(fs.readFileSync(dependency,'utf8'),sandbox);
 const recipe=sandbox.window.MG_REFERENCE.recipe;
 assert.deepEqual(Object.keys(recipe).sort(),expected.slice().sort(),file+' recipe keys');
 assert.equal(typeof recipe['编辑练习'],'string',file+' exercise type');
 assert(recipe['编辑练习'].trim().length>10,file+' concrete editing exercise');
}
'''
  result=subprocess.run(['node','-e',script,str(HTML)],capture_output=True,text=True)
  self.assertEqual(result.returncode,0,result.stderr)
 def test_shell_wiring_and_no_scene_clock(self):
  for style in ('flat-vector','hud','bauhaus','synthwave'):
   shell=(HTML.parent/'styles'/style/'assets/html'/f'{style}.html').read_text();js=(HTML.parent/'styles'/style/'assets/html'/f'{style}.js').read_text()
   self.assertLess(shell.index('scene-helpers.js'), shell.index(f'src="{style}.js"'));self.assertIn(f'src="{style}.js"',shell);self.assertIn('src="../../../../html/runtime.js"',shell)
   for prohibited in ('requestAnimationFrame','Date.now','performance.now','document.','fetch(','Math.random'):
    self.assertNotIn(prohibited,js)


@unittest.skipUnless(shutil.which('node'), 'Node required for control replay')
class ReferenceRuntime(unittest.TestCase):
    def test_capture_queue_failure_recovery_and_bounded_text(self):
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
(async()=>{
let depth=0,resets=0,capture=false,seen=[],callbacks=[],now=0;
const ctx={save(){depth++},restore(){depth--},fillRect(){},measureText(s){return {width:Array.from(s).length*30}},fillText(){}};
const elements={};function el(id){return elements[id]??=(id==='stage'?{getContext:()=>ctx,toDataURL:()=>'',set width(v){depth=0;resets++},set height(v){depth=0;resets++}}:{textContent:'',value:0,append(){},appendChild(){},addEventListener(n,fn){this[n]=fn}})};
const scene={id:'fault-test',title:'Test',examples:[{name:'A',frames:30,data:{}}],recipe:{'分镜':['开始','完成']},validate(){},render(c,d,f){if(f===1)throw Error('intentional');seen.push(f)}};
const sandbox={window:{MG_REFERENCE:scene},document:{getElementById:el,createElement:()=>el(Math.random()),fonts:{ready:Promise.resolve()},documentElement:{classList:{add(c){capture=c==='capture'}}}},location:{search:'?capture=1'},URLSearchParams,performance:{now:()=>now},requestAnimationFrame(fn){callbacks.push(fn)}};
vm.createContext(sandbox);vm.runInContext(fs.readFileSync(process.argv[1]+'/html/runtime.js','utf8'),sandbox);
await sandbox.window.MG_REFERENCE_READY;assert.equal(sandbox.window.__setFrame,undefined);assert(capture);assert.equal(depth,0);
await assert.rejects(sandbox.window.__vtx_render(1),/intentional/);assert.equal(depth,0);
await sandbox.window.__vtx_render(2);assert.equal(depth,0);assert.equal(sandbox.window.__mg_reference_state().frame,2);assert(resets>=6);
assert.throws(()=>sandbox.window.__vtx_render(1.5),/integer/);
const h=sandbox.window.MGReferenceHelpers;
assert.throws(()=>h.text(ctx,'长'.repeat(180),20,20,100,40,{minSize:24,maxLines:2,height:100}),/does not fit/);assert.equal(depth,0);
const fit=h.text(ctx,'四个字',20,20,70,40,{minSize:24,maxLines:2,height:100});assert.equal(fit.lines.length,2);assert(fit.size>=24);assert.equal(depth,0);
// A paused callback must not become a second loop after a new play intent.
async function drain(at){now=at;const pending=callbacks.splice(0);await Promise.all(pending.map(fn=>fn(now)));}
const initialSeen=seen.length;
el('play').click();el('play').click();el('play').click();
assert.equal(callbacks.length,2);await drain(100);assert.deepEqual(seen.slice(initialSeen),[5]);assert.equal(callbacks.length,1);
await drain(200);assert.deepEqual(seen.slice(initialSeen),[5,8]);assert.equal(callbacks.length,1);
// An old draw completing or failing after restart cannot stop the new owner.
const originalRender=scene.render;
for(const rejects of [false,true]){
 let enter,finish;const entered=new Promise(resolve=>enter=resolve);
 scene.render=(...args)=>{if(args[2]===29){enter();return new Promise((resolve,reject)=>{finish=()=>rejects?reject(Error('stale draw')):resolve()})}return originalRender(...args)};
 now+=1000;const old=callbacks.shift()(now);await entered;
 el('play').click();el('play').click();assert(sandbox.window.__mg_reference_state().playing);assert.equal(callbacks.length,1);
 finish();await old;assert(sandbox.window.__mg_reference_state().playing);assert.equal(el('error').textContent,'');assert.equal(callbacks.length,1);
 scene.render=originalRender;await drain(now+100);assert(sandbox.window.__mg_reference_state().playing);assert.equal(callbacks.length,1);
}
// Capture/seek pauses and the final-frame stop still retire the current loop.
await sandbox.window.__vtx_render(10);await drain(now+100);assert.equal(callbacks.length,0);assert.equal(sandbox.window.__mg_reference_state().frame,10);assert(!sandbox.window.__mg_reference_state().playing);
el('seek').value=12;el('seek').input();await sandbox.window.__vtx_render(12);assert.equal(sandbox.window.__mg_reference_state().frame,12);
el('play').click();await drain(now+1000);assert.equal(sandbox.window.__mg_reference_state().frame,29);assert(!sandbox.window.__mg_reference_state().playing);assert.equal(callbacks.length,0);
// A failure belonging to the current playback still surfaces and stops it.
await sandbox.window.__vtx_render(0);el('play').click();await drain(now+34);assert.match(el('error').textContent,/intentional/);assert(!sandbox.window.__mg_reference_state().playing);assert.equal(callbacks.length,0);
console.log('runtime-owner-cases-complete');

})().catch(e=>{console.error(e);process.exit(1)});
'''
        result = subprocess.run(['node', '-e', script, str(STYLES)], text=True,
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('runtime-owner-cases-complete', result.stdout)

if __name__ == '__main__':
    unittest.main()
