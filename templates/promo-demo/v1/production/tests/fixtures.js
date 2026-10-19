'use strict';
// Explicit synthetic SOURCE-TEST scenes; never installed in src/scenes or the bundle.
const DATA=require('../src/runtime/data.js');
function makeScenes() {
  return Object.fromEntries(DATA.cues.scenes.map(scene=>[scene.id,Object.freeze({sceneId:scene.id,implemented:true,
    resolveSceneState(ctx,{globalFrame}) {
      return {cameraMatrix:ctx.math.cameraMatrix({x:640+globalFrame*.01,y:360,z:1}),
        bird:{space:globalFrame>=264&&globalFrame<345?'screen':'world',pose:{x:globalFrame*.1,y:300,angle:globalFrame*.01,scale:1},stage:4,wing:globalFrame%20,feet:true,
          occluders:[ctx.svg.rect(500,200,30,300,ctx.geometry.PALETTE.mid)]},
        mechanics:{carrier:ctx.geometry.carrierAssembly(ctx.math.translate(globalFrame*.01,0)),force:ctx.geometry.forceChain({frameEndpoint:[1,2],paperAnchor:[3,4]})}};
    },
    renderScene(ctx,{layerId}) {
      return {defs:[],world:[ctx.svg.rect(ctx.state.mechanics.carrier.carrierMatrix[4],20,15,25,ctx.geometry.PALETTE.red,{'data-role':layerId})],screen:[]};
    }
  })]));
}
function makeStubs(){return Object.fromEntries(DATA.cues.scenes.map(s=>[s.id,{sceneId:s.id,implemented:false,resolveSceneState(){return {cameraMatrix:[1,0,0,1,0,0],bird:null};},renderScene(){return {defs:[],world:[],screen:[]};}}]));}
module.exports={makeScenes,makeStubs};
