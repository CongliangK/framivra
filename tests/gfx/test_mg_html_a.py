"""Node command-stream checks only; not browser pixels or typography acceptance."""
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[2]
HTML=ROOT/'templates/mg-styles/html'

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

if __name__=='__main__':unittest.main()
