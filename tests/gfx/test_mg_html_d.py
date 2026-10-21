"""Node operation checks; these do not replace browser pixels or visual review."""
import pathlib,shutil,subprocess,unittest
ROOT=pathlib.Path(__file__).resolve().parents[2]
HTML=ROOT/'templates/mg-styles/html'
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
if __name__=='__main__':unittest.main()
