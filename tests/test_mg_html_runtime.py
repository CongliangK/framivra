"""Shared study-player control and failure recovery; no browser-pixel claim."""
import pathlib
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1] / 'templates/mg-styles'


@unittest.skipUnless(shutil.which('node'), 'Node required for control replay')
class ReferenceRuntime(unittest.TestCase):
    def test_capture_queue_failure_recovery_and_bounded_text(self):
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
(async()=>{
let depth=0,resets=0,capture=false,seen=[];
const ctx={save(){depth++},restore(){depth--},fillRect(){},measureText(s){return {width:Array.from(s).length*30}},fillText(){}};
const elements={};function el(id){return elements[id]??=(id==='stage'?{getContext:()=>ctx,toDataURL:()=>'',set width(v){depth=0;resets++},set height(v){depth=0;resets++}}:{textContent:'',value:0,append(){},appendChild(){},addEventListener(n,fn){this[n]=fn}})};
const scene={id:'fault-test',title:'Test',examples:[{name:'A',frames:30,data:{}}],recipe:{'分镜':['开始','完成']},validate(){},render(c,d,f){if(f===1)throw Error('intentional');seen.push(f)}};
const sandbox={window:{MG_REFERENCE:scene},document:{getElementById:el,createElement:()=>el(Math.random()),fonts:{ready:Promise.resolve()},documentElement:{classList:{add(c){capture=c==='capture'}}}},location:{search:'?capture=1'},URLSearchParams,performance:{now:()=>0},requestAnimationFrame(){}};
vm.createContext(sandbox);vm.runInContext(fs.readFileSync(process.argv[1]+'/html/runtime.js','utf8'),sandbox);
await sandbox.window.MG_REFERENCE_READY;assert(capture);assert.equal(depth,0);
await assert.rejects(sandbox.window.__vtx_render(1),/intentional/);assert.equal(depth,0);
await sandbox.window.__vtx_render(2);assert.equal(depth,0);assert.equal(sandbox.window.__mg_reference_state().frame,2);assert(resets>=6);
assert.throws(()=>sandbox.window.__vtx_render(1.5),/integer/);
const h=sandbox.window.MGReferenceHelpers;
assert.throws(()=>h.text(ctx,'长'.repeat(180),20,20,100,40,{minSize:24,maxLines:2,height:100}),/does not fit/);assert.equal(depth,0);
const fit=h.text(ctx,'四个字',20,20,70,40,{minSize:24,maxLines:2,height:100});assert.equal(fit.lines.length,2);assert(fit.size>=24);assert.equal(depth,0);
})().catch(e=>{console.error(e);process.exit(1)});
'''
        result = subprocess.run(['node', '-e', script, str(ROOT)], text=True,
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
