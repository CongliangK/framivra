"""Node command-stream checks only; not browser pixels or typography acceptance."""
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
HTML=ROOT/'templates/mg-styles/html'

class ReferenceGroupA(unittest.TestCase):
    def test_contract_semantic_examples_and_arbitrary_frame_order(self):
        script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const root=process.argv[1],runtime=fs.readFileSync(root+'/runtime.js','utf8');
let helpers=runtime.slice(0,runtime.indexOf('const scene=window.MG_REFERENCE;'))+'})();';
for(const id of ['soft-3d','isometric','pixel','line-art']){
 const sandbox={window:{}};vm.createContext(sandbox);vm.runInContext(helpers,sandbox);
 vm.runInContext(fs.readFileSync(root+'/'+id+'.scene.js','utf8'),sandbox);
 const scene=sandbox.window.MG_REFERENCE,h=sandbox.window.MGReferenceHelpers;
 assert.equal(scene.id,id);assert.equal(scene.examples.length,2);assert.equal(Object.keys(scene.recipe).length,7);
 function draw(d,f,N){let ops=[];const c=new Proxy({}, {get(t,k){if(k==='measureText')return s=>({width:Array.from(s).length*27});if(k==='createRadialGradient')return (...v)=>({addColorStop(...a){ops.push(['stop',...a]);}});if(k in t)return t[k];return (...args)=>{args.forEach(a=>{if(typeof a==='number')assert(Number.isFinite(a));});ops.push([k,...args]);};},set(t,k,v){t[k]=v;ops.push(['set',k,typeof v==='object'?'gradient':v]);return true;}});scene.render(c,d,f,N,h);return JSON.stringify(ops);}
 let traces=[];
 for(const e of scene.examples){scene.validate(e.data,e.frames,h);const before=JSON.stringify(e.data),frames=[0,Math.floor(e.frames/3),Math.floor(2*e.frames/3),e.frames-1];const baseline=frames.map(f=>draw(e.data,f,e.frames));for(const i of [3,0,2,1,3])assert.equal(draw(e.data,frames[i],e.frames),baseline[i]);assert.equal(JSON.stringify(e.data),before);assert(new Set(baseline).size>1);traces.push(baseline[3]);assert.throws(()=>scene.validate(e.data,1,h));}
 assert.notEqual(traces[0],traces[1]);
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
  const endpoint=sphere(N-1);assert(Math.abs(endpoint[1]-960)<1e-8);assert(Math.abs(endpoint[2]-480)<1e-8);
 }

 assert.throws(()=>scene.validate({...scene.examples[0].data,title:'X'.repeat(200),mission:'X'.repeat(200)},scene.examples[0].frames,h));
}
'''
        subprocess.run(['node','-e',script,str(HTML)],check=True,cwd=ROOT,capture_output=True,text=True)

    def test_local_shells_and_no_scene_clock_or_dom(self):
        for name in ('soft-3d','isometric','pixel','line-art'):
            source=(HTML/f'{name}.scene.js').read_text()
            for banned in ('requestAnimationFrame','performance.now','Date.now','document.','fetch('):
                self.assertNotIn(banned,source)
            shell=(HTML/f'{name}.html').read_text()
            self.assertIn(f'src="{name}.scene.js"',shell)
            self.assertIn('src="runtime.js"',shell)
            self.assertNotIn('https://',shell)

if __name__=='__main__':unittest.main()
