'use strict';
const DATA=require('./data.js');
const M=require('./math.js');
const S=require('./svg.js');
const E=require('./effects.js');
const G=require('./geometry.js');
const OPENING=require('./opening-state.js');
const ENTRY=require('./entries.js');
const REGISTRY=require('../scenes/index.js');
const EMPTY=M.deepFreeze({defs:[],world:[],screen:[]});
function stateFrame(globalFrame) { return globalFrame>=2940 && globalFrame<3090 ? 2940 : globalFrame; }
function absent(layerId,globalFrame) {
  return DATA.cues.explanatory_withdrawals.some(w=>w.track===layerId&&globalFrame>=w.zero_contribution[0]&&globalFrame<w.zero_contribution[1]);
}
function fragment(value) {
  if(!value||!['defs','world','screen'].every(k=>Array.isArray(value[k])))throw new TypeError('Scene must return {defs:[],world:[],screen:[]} SVG nodes');
  // Serialize now to reject invalid node trees before composing any DOM.
  S.serialize(value.defs);S.serialize(value.world);S.serialize(value.screen);
  return value;
}
/** Shared layer renderer. See contract.d.ts. ctx is frame-local and read-only. */
function renderScene(ctx,{globalFrame,localSceneFrame,layerId}) {
  if(globalFrame!==ctx.globalFrame||localSceneFrame!==ctx.localSceneFrame||layerId!==ctx.entry.layerId)throw new Error('Mismatched scene invocation');
  if(absent(layerId,globalFrame))return EMPTY;
  if(layerId==='T07'||layerId==='T08') {
    let history=null;
    if(layerId==='T08' && globalFrame>=1404 && globalFrame<1452 && ctx.effects.translucent_trails)history=ctx.sharedAt(globalFrame-3).bird;
    return G.birdContribution({bird:ctx.state.bird,part:layerId==='T07'?'body':'wings',id:ctx.id,effects:ctx.effects,history});
  }
  return fragment(ctx.sceneModule.renderScene(ctx,{globalFrame,localSceneFrame,layerId}));
}
function createRuntime({scenes=REGISTRY,diagnostic=false,effects={},allowScaffold=false}={}) {
  const flags=E.options(effects,diagnostic);
  if(allowScaffold && !diagnostic)throw new Error('Incomplete scaffolds require explicit diagnostic mode');
  for(const item of DATA.cues.scenes)if(!scenes[item.id]||scenes[item.id].sceneId!==item.id)throw new Error(`Missing isolated scene module: ${item.id}`);
  const sharedBase=M.deepFreeze({assets:DATA,math:M,svg:S,geometry:G,effectHelpers:E,opening:OPENING,effects:flags});
  function sharedAt(globalFrame) {
    if(!Number.isInteger(globalFrame)||globalFrame<0||globalFrame>=3600)throw new RangeError('Global frame must be integer 0..3599');
    const frame=stateFrame(globalFrame),scene=DATA.cues.scenes.find(s=>frame>=s.in&&frame<s.out),sceneModule=scenes[scene.id];
    if(!sceneModule.implemented && !allowScaffold)throw new Error(`UNIMPLEMENTED_SCENE:${scene.id}`);
    const state=sceneModule.resolveSceneState(sharedBase,{globalFrame:frame,localSceneFrame:frame-scene.in});
    if(!state||!Object.hasOwn(state,'bird'))throw new TypeError(`${scene.id} must explicitly supply bird state (or null)`);
    M.matrix(state.cameraMatrix);
    return M.deepFreeze(state);
  }
  function renderFrame(entryId,input,{paper=false}={}) {
    const f=ENTRY.frameContext(entryId,input),sceneModule=scenes[f.scene.id],state=sharedAt(f.globalFrame);
    const ctx=M.deepFreeze({...sharedBase,...f,state,sceneModule,stateFrame:stateFrame(f.globalFrame),sharedAt,id:S.ids(`film-${entryId.replace('.','-')}`),sourceBindingStatus:'ACTUAL_FINAL_TIMELINE',sourceFields:ENTRY.bindSources(DATA.actualTimeline,DATA.sourceBindings)});
    const result=fragment(renderScene(ctx,{globalFrame:f.globalFrame,localSceneFrame:f.localSceneFrame,layerId:f.entry.layerId}));
    const nodes=[...(paper?[S.rect(0,0,1280,720,G.PALETTE.paper,{'data-standalone-paper':'diagnostic-only'})]:[]),
      ...(result.defs.length?[S.node('defs',{},result.defs)]:[]),
      ...(result.world.length?[S.group(result.world,{transform:S.matrixString(state.cameraMatrix),'data-space':'world'})]:[]),
      ...(result.screen.length?[S.group(result.screen,{'data-space':'screen'})]:[])];
    return M.deepFreeze({entryId,layerId:f.entry.layerId,localFrame:f.localFrame,globalFrame:f.globalFrame,localSceneFrame:f.localSceneFrame,sceneId:f.scene.id,
      stateFrame:ctx.stateFrame,implemented:sceneModule.implemented===true,profile:diagnostic?(allowScaffold?'draft-source':'diagnostic'):'film',effects:flags,fragment:S.serialize(nodes),nodes});
  }
  return Object.freeze({renderFrame,sharedAt,entries:ENTRY.ENTRIES,effects:flags});
}
// Navigation ?f= is the production protocol; this synchronous setter is its exact mirror.
// Merely exposing it never enables navfree, static spans, beginFrame or canvas export.
function mount(document,window,entryId,options={}) {
  ENTRY.getEntry(entryId); const runtime=createRuntime(options),root=document.getElementById('scene');
  if(!root)throw new Error('Missing #scene SVG root');
  const paper=new URLSearchParams(window.location.search).get('paper')==='1';
  function render(localFrame) {
    const result=runtime.renderFrame(entryId,localFrame,{paper});
    root.innerHTML=result.fragment;
    root.setAttribute('data-frame',String(result.localFrame));
    root.setAttribute('data-global-frame',String(result.globalFrame));
    root.setAttribute('data-scene',result.sceneId);
    root.setAttribute('data-profile',result.profile);
    root.setAttribute('data-status',result.implemented?'scene-implemented-unverified':'candidate-not-production');
    return result.localFrame;
  }
  window.__vtx_render=render;
  render(new URLSearchParams(window.location.search).get('f'));
  return runtime;
}
module.exports={createRuntime,renderScene,mount,stateFrame,absent};
