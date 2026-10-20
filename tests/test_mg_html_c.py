"""Node operation/semantic checks only; these do not claim browser pixels or playback."""
from pathlib import Path
import json
import shutil
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
NODE=shutil.which('node')


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
 vm.runInContext(fs.readFileSync(path.join(base,id+'.js'),'utf8'),box);const s=box.window.MG_REFERENCE,h=box.window.MGReferenceHelpers;
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
            html=(ROOT/f'templates/mg-styles/html/{style}.html').read_text()
            self.assertIn(f'src="{style}.js"',html)
            self.assertIn('src="runtime.js"',html)
            code=(ROOT/f'templates/mg-styles/html/{style}.js').read_text()
            for forbidden in ('requestAnimationFrame(', 'Date.now(', 'performance.now(', 'Math.random(', 'fetch('):
                self.assertNotIn(forbidden,code)


if __name__=='__main__':unittest.main()
