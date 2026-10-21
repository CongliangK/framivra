"""Font-size-aware operation surrogate; raster scenes use seven key frames, others all frames."""
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2] / 'templates/mg-styles/html'


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
        result = subprocess.run(['node', '-e', script, str(ROOT)], text=True,
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
