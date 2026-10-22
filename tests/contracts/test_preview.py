"""Current timeline preview loading and UI routing; real JavaScript, no browser claims."""
import json
import re
import subprocess
import unittest
from pathlib import Path

PREVIEW_HTML = (Path(__file__).resolve().parents[2] / 'vtx/preview.html').read_text()


def _constant(name):
    return json.loads(re.search(r'const ' + name + r' = (.*);', PREVIEW_HTML).group(1))


class PreviewContract(unittest.TestCase):
    def test_preview_loads_only_v2_without_mutating_current_contract(self):
        loader = PREVIEW_HTML[PREVIEW_HTML.index("function load("):
                              PREVIEW_HTML.index("function validate(")]
        validators = PREVIEW_HTML[PREVIEW_HTML.index("function validate("):
                                  PREVIEW_HTML.index("function render(")]
        script = """
const assert = require('node:assert/strict');
const CONSTRAINTS = %s, SAMPLE = %s;
let TL = null, PXF = 6, renders = 0, fits = 0;
const nodes = {}, handlers = {}, HINT = 'details', alerts = [];
const $ = id => nodes[id] ||= {value: '', innerHTML: '', click() {this.clicked = true;},
  showModal() {this.open = true;}, close() {this.open = false;}};
const document = {addEventListener: (event, fn) => handlers[event] = fn};
const alert = message => alerts.push(message);
const render = () => renders++, fitScale = () => {fits++; return 1;};
%s
%s
(async () => {
const current = JSON.parse(JSON.stringify(SAMPLE));
for (const track of current.video_tracks)
  for (const clip of track.clips)
    for (const key of ['scale', 'x', 'y', 'opacity', 'fade_in_frames', 'fade_out_frames'])
      delete clip[key];
const before = JSON.stringify(current);
load(current);
assert.equal(TL, current);
assert.equal(JSON.stringify(current), before);
assert.equal(validate(current).filter(issue => issue.lv === 'err').length, 0);
for(const format of ['html','svg-sequence']) for(const total of [10,11]) {
  const t=JSON.parse(JSON.stringify(current));
  t.video_tracks=[{name:'gfx',kind:'gfx',clips:[{src:'page',format,dst_in:0,dur:10,
    fade_in_frames:4,fade_out_frames:total-4}]}];
  assert.equal(validate(t).filter(issue=>issue.lv==='err' && /fade 总长/.test(issue.m)).length,
               Number(total>10));
}
assert.equal(durF(current), current.project.duration_frames);
for (const old of [null, [], 'text', {}, {version: 1, overlays: [{src: 'old.html', in: 0, out: 30}]},
                   {version: 3}, {version: '2'}]) {
  assert.throws(() => load(old), /version: 2/);
  loadText(JSON.stringify(old));
  assert.match(alerts.at(-1), /version: 2/);
  assert.equal(TL, current);
}
loadText('{bad json');
assert.match(alerts.at(-1), /时间线载入失败/);
assert.equal(TL, current);
assert.equal(renders, 1);
assert.equal(fits, 1);
loadText(JSON.stringify(SAMPLE));
assert.deepEqual(TL, SAMPLE);
assert.equal(renders, 2);
assert.equal(fits, 2);
$("bOpen").onclick(); assert.equal($("file").clicked, true);
$("bPaste").onclick(); assert.equal($("dlg").open, true);
$("paste").value = JSON.stringify({version: 1});
$("bLoad").onclick(); assert.equal($("dlg").open, false);
assert.deepEqual(TL, SAMPLE); assert.match(alerts.at(-1), /version: 2/);
const edited = {...SAMPLE, _meta: {note: 'current input preserved'}};
$("paste").value = JSON.stringify(edited); $("bLoad").onclick();
assert.deepEqual(TL, edited);
$("bSample").onclick(); assert.deepEqual(TL, SAMPLE);
$("file").onchange({target: {files: [{text: async () => JSON.stringify(edited)}]}});
await Promise.resolve(); assert.deepEqual(TL, edited);
let prevented = false;
handlers.drop({preventDefault() {prevented = true;},
  dataTransfer: {files: [{text: async () => JSON.stringify(SAMPLE)}]}});
await Promise.resolve(); assert.equal(prevented, true); assert.deepEqual(TL, SAMPLE);
// File reads may complete out of order; a newer input intent owns the view.
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const startRead=(kind,read)=>kind==='file'
  ? $('file').onchange({target:{files:[{text:read}]}})
  : handlers.drop({preventDefault(){},dataTransfer:{files:[{text:read}]}});
for(const oldKind of ['file','drop']) for(const newKind of ['file','drop']){
  const old=deferred(), latest=deferred();
  const oldTask=startRead(oldKind,()=>old.promise), latestTask=startRead(newKind,()=>latest.promise);
  latest.resolve(JSON.stringify(edited));await latestTask;assert.deepEqual(TL,edited);
  old.resolve(JSON.stringify(SAMPLE));await oldTask;assert.deepEqual(TL,edited);
}
for(const kind of ['file','drop']) for(const action of ['sample','paste','invalid-paste']){
  const old=deferred(), task=startRead(kind,()=>old.promise);
  if(action==='sample') $('bSample').onclick();
  else {$('paste').value=action==='paste'?JSON.stringify(edited):'{bad json';$('bLoad').onclick();}
  const chosen=TL,n=alerts.length;
  old.resolve(JSON.stringify({...SAMPLE,_meta:{note:'stale'}}));await task;
  assert.equal(TL,chosen);assert.equal(alerts.length,n);
}
for(const kind of ['file','drop']){
  const old=deferred(), task=startRead(kind,()=>old.promise);$('bSample').onclick();
  const n=alerts.length;old.reject(new Error('stale read failure'));await task;
  assert.equal(alerts.length,n);assert.deepEqual(TL,SAMPLE);
  const chosen=TL;
  await startRead(kind,()=>Promise.reject(new Error('current read failure')));
  assert.equal(alerts.length,n+1);assert.match(alerts.at(-1),/current read failure/);assert.equal(TL,chosen);
  await startRead(kind,()=>{throw new Error('synchronous read failure');});
  assert.equal(alerts.length,n+2);assert.match(alerts.at(-1),/synchronous read failure/);assert.equal(TL,chosen);
}
// Opening then canceling a paste dialog is not a replacement input intent.
const pending=deferred(), pendingTask=startRead('file',()=>pending.promise);
$('bPaste').onclick();$('bCancel').onclick();pending.resolve(JSON.stringify(edited));
await pendingTask;assert.deepEqual(TL,edited);
for (const button of ["bCancel", "bX"]) {
  $("bPaste").onclick(); $(button).onclick(); assert.equal($("dlg").open, false);
}
})().catch(error => {console.error(error); process.exitCode = 1;});
""" % (json.dumps(_constant("CONSTRAINTS")), json.dumps(_constant("SAMPLE")),
       loader, validators)
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_full_render_is_transactional_and_untrusted_values_are_text(self):
        """Runs actual page JS against inert DOM sinks, not browser pixel proof."""
        script = r"""
const assert = require('node:assert/strict'), vm = require('node:vm');
const created = [], alerts = [], nodes = {}, handlers = {};
class Element {
  constructor(tag='div') {
    this.tag=tag; this.style={}; this.children=[]; this.dataset={};
    this.className=''; this.value=''; this.html=''; this.clientWidth=1000;
    this.offsetWidth=148; this.scrollLeft=0; this.events={}; created.push(this);
    this.classList={add: c => {this.className += ' '+c;},
      remove: c => {this.className=this.className.split(' ').filter(v=>v!==c).join(' ');}};
  }
  // Intentionally inert: records every HTML string; never interprets/runs a tag.
  set innerHTML(value) {
    this.html=String(value); this.children=[];
    if(this.html.includes('id="lane0"')) this.lane0=new Element();
  }
  get innerHTML() {return this.html;}
  get childNodes() {return this.children;}
  appendChild(child) {this.children.push(child); return child;}
  replaceChildren(...children) {this.children=children; this.html='';}
  querySelector(selector) {return selector==='#lane0'?this.lane0:null;}
  insertAdjacentHTML(_, html) {this.html += html;}
  addEventListener(name, fn) {this.events[name]=fn;}
  getBoundingClientRect() {return {left:0};}
  click() {} showModal() {this.open=true;} close() {this.open=false;}
}
const document={getElementById: id => nodes[id] ||= new Element(),
  createElement: tag => new Element(tag), createDocumentFragment: () => new Element('fragment'),
  querySelectorAll: () => [], querySelector: () => null,
  addEventListener: (name, fn) => handlers[name]=fn};
const box={document, window:{addEventListener(){}}, URLSearchParams, assert, created,
  alerts, alert: text => alerts.push(text)};
vm.createContext(box);
vm.runInContext(%s,box,{timeout:2000});
vm.runInContext(`
const clone=()=>JSON.parse(JSON.stringify(SAMPLE));
const current=TL, before=JSON.stringify(TL), priorNodes=[...$('content').childNodes];
const block=created.find(e=>e.className.startsWith('blk ')), oldClick=block.onclick;
block.onclick();
const details=$('detail').innerHTML, meta=$('meta').innerHTML, zoom=$('zoom').value;
const badInputs=[{...clone(),sources:{}},
  {...clone(),video_tracks:[{name:'bad',kind:'broll',clips:null}]},
  {...clone(),audio_tracks:[{name:'bad',events:null}]},
  {...clone(),video_tracks:[{name:'late error',kind:{},clips:[]}]},
  {...clone(),project:{...SAMPLE.project,duration_frames:'not frames'}},
  {...clone(),project:{...SAMPLE.project,duration_frames:1e100}}];
for(const bad of badInputs){
  const n=alerts.length; loadText(JSON.stringify(bad));
  assert.equal(alerts.length,n+1); assert.equal(TL,current);
  assert.equal(JSON.stringify(TL),before); assert.equal($('zoom').value,zoom);
  assert.equal($('detail').innerHTML,details); assert.equal($('meta').innerHTML,meta);
  assert.equal($('content').childNodes.length,priorNodes.length);
  priorNodes.forEach((node,i)=>assert.equal($('content').childNodes[i],node));
  assert.equal(block.onclick,oldClick); block.onclick();
  assert.equal($('detail').innerHTML,details);
}
const marker='<img data-audit-marker="inert">';
const cases=[
  t=>t.video_tracks[0].kind=marker,
  t=>t.project.width=marker, t=>t.project.height=marker, t=>t.project.fps=marker,
  t=>t.audio_tracks[0].events[0].volume=marker,
  t=>t.video_tracks[0].clips[0].fade_in_frames=marker,
  t=>t.video_tracks[0].clips[0].fade_out_frames=marker,
  t=>t.video_tracks[0].clips[0].src_in=marker,
  t=>t.video_tracks[0].clips[0].src_out=marker,
  t=>t.video_tracks[0].clips[0].dur=marker,
  t=>t.video_tracks[0].clips[0].dst_in=marker,
];
for(const mutate of cases){
  const t=clone(); mutate(t); const first=created.length, n=alerts.length;
  loadText(JSON.stringify(t)); assert.equal(alerts.length,n);
  const newBlocks=created.slice(first).filter(e=>e.className.startsWith('blk '));
  for(const e of newBlocks){e.onclick(); assert(!/<img/i.test($('detail').innerHTML));}
  assert(created.slice(first).every(e=>!/<img/i.test(e.innerHTML)));
  assert(!/<img/i.test($('meta').innerHTML));
  assert(JSON.stringify(TL).includes('data-audit-marker'));
}
for(const reserved of ['__proto__','constructor','toString']){
  const t=clone();t.video_tracks[0].kind=reserved;t.audio_tracks[0].name=reserved;
  const n=alerts.length;loadText(JSON.stringify(t));assert.equal(alerts.length,n);
}
for(const fps of [-1,0,1e308]){
  const t=clone();t.project.fps=fps;loadText(JSON.stringify(t));
  assert.equal(TL.project.fps,fps);assert.equal($('vbadge').className,'bad');
}
const long=clone();long.project.duration_frames=1e12;
const count=created.length;loadText(JSON.stringify(long));
assert.equal(TL.project.duration_frames,1e12);assert(created.length-count<6000);
// Valid v2 clips retain all transforms, dependencies, subtitles and detail routes.
const final=clone();final.video_tracks[2].clips[0].cache_dependencies=['gfx/custom.js'];
final._meta={note:'retained extension'};loadText(JSON.stringify(final));
assert.deepEqual(TL,final);assert.equal($('vbadge').className,'');
const clip=created.filter(e=>e.className.startsWith('blk ')).at(-1);clip.onclick();
assert($('detail').innerHTML.includes('DETAILS'));
`,box,{timeout:2000});
""" % json.dumps(re.search(r'<script>([\s\S]*)</script>', PREVIEW_HTML).group(1))
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True,
                                timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
