/* Offline JavaScript/serialized-SVG harness. It does not implement a browser,
 * layout, compositing, screenshot capture, playback, or pixel validation. */
'use strict';
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const html=fs.readFileSync(process.argv[2],'utf8');
const cueMatch=html.match(/<script id="opening-cues" type="application\/json">([\s\S]*?)<\/script>/);
const code=html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
function load(search) {
  const scene={innerHTML:'', attributes:{}, setAttribute(k,v){this.attributes[k]=v;}};
  const document={getElementById(id){
    if(id==='opening-cues') return {textContent:cueMatch[1]};
    if(id==='scene') return scene;
    throw new Error(`Unexpected DOM access: ${id}`);
  }};
  const context=vm.createContext({document,location:{search},window:{},URLSearchParams});
  vm.runInContext(code,context,{timeout:5000});
  return {scene,api:context.window.OpeningPreview,render:context.window.__vtx_render};
}
const baseline=load('?f=0'), frames=[];
for(let f=0;f<300;f++) {
  const fresh=load('?f='+f);
  assert.equal(baseline.render(f),f);
  assert.equal(baseline.scene.innerHTML,fresh.scene.innerHTML);
  assert.equal(baseline.scene.attributes['data-frame'],String(f));
  frames.push({state:baseline.api.frameState(f),svg:baseline.scene.innerHTML});
}
// Reverse and interleaved repeated calls expose accidental retained animation state.
for(const f of [...Array.from({length:300},(_,i)=>299-i),18,0,299,109,18,150,264,263,0]) {
  baseline.render(f);
  assert.equal(baseline.scene.innerHTML,frames[f].svg);
}
for(const [query,want] of [['',0],['?f=not-a-frame',0],['?f=Infinity',0],['?f=-9',0],['?f=999999',299],['?f=18.9',18]]) {
  const item=load(query);
  assert.equal(item.scene.innerHTML,frames[want].svg);
}
const standalone=load('?f=176&paper=1');
assert.equal(standalone.scene.innerHTML,baseline.api.svgFrame(176,true));
assert.ok(standalone.scene.innerHTML.startsWith('<rect width="1280" height="720" fill="#F1EDE1"/>'));
assert.ok(frames[176].svg.startsWith('<g '));
process.stdout.write(JSON.stringify({frames,embeddedCues:JSON.parse(cueMatch[1])}));
