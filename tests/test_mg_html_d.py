"""Node operation checks; these do not replace browser pixels or visual review."""
import pathlib,shutil,subprocess,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
HTML=ROOT/'templates/mg-styles/html'
@unittest.skipUnless(shutil.which('node'),'node required')
class HTMLDTests(unittest.TestCase):
 def test_scenes_semantics_and_integer_seek(self):
  script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert'),root=process.argv[1];
let sandbox={window:{}};vm.createContext(sandbox);
let runtime=fs.readFileSync(root+'/runtime.js','utf8').split('window.MGReferenceHelpers=h;')[0]+'window.MGReferenceHelpers=h;})();';vm.runInContext(runtime,sandbox);const h=sandbox.window.MGReferenceHelpers;
function draw(scene,d,f,N){const calls=[];const ctx=new Proxy({measureText:s=>({width:Array.from(String(s)).length*20})},{get(t,k){if(k in t)return t[k];return (...args)=>{for(const a of args)if(typeof a==='number')assert(Number.isFinite(a),k+' nonfinite');calls.push([k,...args]);};},set(t,k,v){t[k]=v;calls.push(['set',k,v]);return true;}});scene.render(ctx,d,f,N,h);return JSON.stringify(calls);}
for(const id of ['flat-vector','hud','bauhaus','synthwave']){vm.runInContext(fs.readFileSync(root+'/'+id+'.js','utf8'),sandbox);const s=sandbox.window.MG_REFERENCE;assert.equal(s.id,id);assert.equal(s.examples.length,2);for(const k of ['适用/不适用','视觉隐喻','4–6拍','不可删特征','可替语义','动作/转场/声音点','失败反例'])assert(s.recipe[k]);let outputs=[];for(const e of s.examples){s.validate(e.data,e.frames,h);const key=Math.floor(e.frames*.67),a=draw(s,e.data,key,e.frames);draw(s,e.data,0,e.frames);draw(s,e.data,e.frames-1,e.frames);assert.equal(a,draw(s,e.data,key,e.frames),'seek');outputs.push(a);assert.throws(()=>s.validate(e.data,29,h));const bad=JSON.parse(JSON.stringify(e.data));bad.seed=1.5;assert.throws(()=>s.validate(bad,e.frames,h));for(let f=0;f<e.frames;f++)draw(s,e.data,f,e.frames);if(['flat-vector','synthwave'].includes(id))assert.equal(draw(s,e.data,e.frames-1,e.frames),draw(s,e.data,e.frames-2,e.frames),'final hold');const invalid=JSON.parse(JSON.stringify(e.data));if(id==='hud')invalid.target.points[0].y=.9;else if(id==='flat-vector')invalid.links[0].to='missing';else if(id==='bauhaus')invalid.beats[0].target='missing';else invalid.waypoints[0].side='center';assert.throws(()=>s.validate(invalid,e.frames,h));}assert.notEqual(outputs[0],outputs[1]);if(id==='flat-vector'){const e=s.examples[0],d=JSON.parse(JSON.stringify(e.data)),build=d.stages[1];d.stages[2].out=build.out+14;assert.throws(()=>s.validate(d,build.out+14,h));d.stages[2].out=build.out+31;s.validate(d,build.out+31,h);const N=build.out+31,last=JSON.parse(draw(s,d,N-1,N));const positions=last.filter(x=>x[0]==='translate').map(x=>x.slice(1));assert.deepEqual(positions,[[420,600],[960,600],[1500,600]]);for(let f=N-6;f<N;f++)assert.equal(draw(s,d,f,N),draw(s,d,N-1,N),'six fully settled frames');d.palette=['#000001','#000002','#000003','#000004','#000005','#000006'];s.validate(d,N,h);const styled=draw(s,d,N-1,N);for(const color of d.palette.slice(0,3).concat(d.palette.slice(4)))assert(styled.includes(color),'palette consumed '+color);d.palette=['red'];assert.throws(()=>s.validate(d,N,h));}}
'''
  result=subprocess.run(['node','-e',script,str(HTML)],capture_output=True,text=True)
  self.assertEqual(result.returncode,0,result.stderr)
 def test_shell_wiring_and_no_scene_clock(self):
  for style in ('flat-vector','hud','bauhaus','synthwave'):
   shell=(HTML/f'{style}.html').read_text();js=(HTML/f'{style}.js').read_text()
   self.assertIn(f'src="{style}.js"',shell);self.assertIn('src="runtime.js"',shell)
   for prohibited in ('requestAnimationFrame','Date.now','performance.now','document.','fetch(','Math.random'):
    self.assertNotIn(prohibited,js)
if __name__=='__main__':unittest.main()
