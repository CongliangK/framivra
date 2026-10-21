"""Node Canvas call-trace checks; explicitly not browser/pixel/movie validation."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
HTML = ROOT / 'templates/mg-styles/html'

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


if __name__ == '__main__':
    unittest.main()
