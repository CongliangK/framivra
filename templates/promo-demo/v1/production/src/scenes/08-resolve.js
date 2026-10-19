'use strict';
/** S8: one inherited world, one inherited F, and the mandatory shared B rig.
 * Default dependencies are final-bundle relative paths. withSourceScene is a
 * pure dependency factory for isolated candidate tests, never a global override.
 * Typography remains fail-closed until the exact local outline asset is reviewed.
 */
function withSourceScene(SOURCE){
if(!SOURCE||SOURCE.sceneId!=='S7'||typeof SOURCE.resolveSceneState!=='function'||typeof SOURCE.renderScene!=='function')throw new Error('S8 requires the S7 scene contract');
const sceneId='S8',implemented=false,START=3240,END=3600;
const PERSISTENT=Object.freeze(['T02','T03','T04','T05','T06','T09']);
const MINI=Object.freeze(['T02','T03','T04','T06','T09']);
const EMPTY=Object.freeze({defs:Object.freeze([]),world:Object.freeze([]),screen:Object.freeze([])});
const BRAND='Framivra',INK='#252B29';
const add=(a,b)=>[a[0]+b[0],a[1]+b[1]],lerp=(a,b,t)=>a.map((v,i)=>v+(b[i]-v)*t);
function inverse(m){
  const d=m[0]*m[3]-m[1]*m[2];
  if(!Number.isFinite(d)||Math.abs(d)<1e-12)throw new Error('Singular S8 transform');
  return [m[3]/d,-m[1]/d,-m[2]/d,m[0]/d,(m[2]*m[5]-m[3]*m[4])/d,(m[1]*m[4]-m[0]*m[5])/d];
}
function resolveSceneState(ctx,{globalFrame,localSceneFrame}){
  if(!Number.isInteger(globalFrame)||globalFrame<START||globalFrame>=END)throw new RangeError('S8 frame must be in [3240,3600)');
  if(localSceneFrame!==globalFrame-START)throw new Error('S8 local/global frame mismatch');
  const M=ctx.math,S=ctx.svg,f=Math.min(globalFrame,3480),p=(a,b)=>M.smoothstep(M.progress(f,a,b));
  const base=SOURCE.resolveSceneState(ctx,{globalFrame:3239,localSceneFrame:479});
  if(!base.sourceLayers||!base.mechanics?.frame?.corners||!base.bird)throw new Error('S8 requires the actual S7 f3239 world; scaffold substitution forbidden');
  const start=f===3240,C0=base.cameraMatrix,cameraScale=M.mix(C0[0],.70,p(3240,3420));
  const fixedFocus=M.transform([640,360],inverse(C0));
  const cameraMatrix=start?C0:[cameraScale,0,0,cameraScale,640-cameraScale*fixedFocus[0],360-cameraScale*fixedFocus[1]];
  const invCamera=inverse(cameraMatrix),retreat=p(3240,3420);
  // One affine chart for ALL five remembered world owners. The original B/F are
  // excluded by owner before the chart is applied; there is no miniature clone.
  // Canonical S6 has a deeper original buckle/guide than the R03 illustration.
  // A 0.375 final chart (rather than obsolete0.39) keeps every real world owner
  // inside the inherited lower rail while preserving the intended center650.
  const nominalMini=ctx.assets.motion.ending.mini_world_matrix;
  const miniTarget=[.375,0,0,.375,nominalMini[4]+640*(nominalMini[0]-.375),nominalMini[5]];
  const miniScreen=start?base.sourceLayers.miniScreen:lerp(base.sourceLayers.miniScreen,miniTarget,retreat);
  const miniWorld=start?base.sourceLayers.miniWorld:M.multiply(invCamera,M.multiply(miniScreen,C0));
  const matrices=start?base.sourceLayers.matrices:Object.fromEntries(PERSISTENT.map(role=>[role,role==='T05'?M.IDENTITY:miniWorld]));
  const targets=[[140,578],[140,181],[1140,181],[1140,578]],stop=[3310,3290,3330,3330];
  // A single finite pressure pulse, exactly zero at first contact and after3372.
  const press=f>=3350&&f<=3372?3.2*Math.sin(Math.PI*(f-3350)/22)**2:0;
  const corners=start?base.mechanics.frame.corners:base.mechanics.frame.corners.map((corner,i)=>{
    let screen=lerp(M.transform(corner.origin,C0),targets[i],p(3240,stop[i]));
    if(i===2)screen=add(screen,[0,press]);
    const origin=M.transform(screen,invCamera);
    return {...corner,origin,matrix:M.poseMatrix({x:origin[0],y:origin[1],scale:corner.scale,angle:corner.angle})};
  });
  const topRight=M.transform(corners[2].origin,cameraMatrix);
  const support={owner:'T05',object:'F-corner-2',
    x0:topRight[0]-72*corners[2].scale*cameraScale,x1:topRight[0],y:topRight[1],
    thickness:20*corners[2].scale*cameraScale,contactFrame:3350};
  const baseRoot=M.transform([base.bird.pose.x,base.bird.pose.y],C0);
  const screenScale=M.mix(base.bird.pose.scale*C0[0],.78,p(3240,3330));
  // The real S7 checkpoint is below the top rail. A small rise puts B above the
  // same corner, followed by a decreasing-height approach and f3350 foot contact.
  // Final x1105/.78 is an explicit bounded correction to the obsolete R03 target:
  // both entire soles fit the inherited 0.70x corner and the beak stays <=1216.
  const finalX=1105,landingY=support.y-48*screenScale;
  let rootScreen;
  if(f<=3300)rootScreen=M.cubic([baseRoot,add(baseRoot,[25,-30]),[1092,108],[1105,122]],p(3240,3300));
  else rootScreen=[finalX,M.mix(122,landingY,p(3300,3350))];
  const angle=M.keys(f,[[3240,base.bird.pose.angle],[3275,-8],[3300,-4],[3330,0],[3480,0]]);
  const root=M.transform(rootScreen,invCamera);
  const baseMiniInverse=inverse(base.sourceLayers.matrices.T06);
  const occlusionDelta=M.multiply(miniWorld,baseMiniInverse);
  const bird=start?base.bird:{...base.bird,
    pose:{x:root[0],y:root[1],scale:screenScale/cameraScale,angle},
    feet:f>=3330,wing:M.keys(f,[[3240,base.bird.wing],[3260,-12],[3285,11],[3310,-6],[3330,4],[3350,6],[3420,6],[3450,0],[3480,6]]),
    occluders:base.bird.occluders.map(n=>S.group([n],{transform:S.matrixString(occlusionDelta)}))};
  const frameEndpoint=M.transform([0,36],corners[0].matrix);
  const chain=start?base.mechanics.forceChain:ctx.geometry.forceChain({frameEndpoint,paperAnchor:add(frameEndpoint,[10,10])});
  const mechanics=start?base.mechanics:{...base.mechanics,frame:{...base.mechanics.frame,corners},forceChain:chain};
  const footLocal=[[-18,48],[-1,48],[11,48],[29,48]],birdScreen=M.multiply(cameraMatrix,M.poseMatrix(bird.pose));
  const feet=footLocal.map(q=>M.transform(q,birdScreen));
  const sourceLayers=start?base.sourceLayers:{...base.sourceLayers,stateFrame:f,miniScreen,miniWorld,matrices};
  return M.deepFreeze({...base,sceneId,cameraMatrix,bird,mechanics,sourceLayers,
    ending:{effectiveFrame:f,miniatureOwners:MINI,frameOwner:'T05',birdOwners:['T07','T08'],
      inheritedCheckpoint:3239,retreat,press,support,feet,contact:f>=3350,
      wordmark:{text:BRAND,origin:[280,393],size:108,weight:600,reveal:M.progress(f,3360,3420),
        revealWindow:[3360,3420],stableWindow:[3420,3600]},quietHold:f>=3480,
      designCorrections:['CONTINUOUS_RISE_FROM_ACTUAL_S7_THEN_DESCENT_TO_CONTACT','BOTH_SOLES_SUPPORTED_BY_INHERITED_RIGHT_CORNER','FINAL_MINIATURE_SCALE_0_375_FITS_CANONICAL_WORLD_INSIDE_FRAME']},
    productionReadiness:'INCOMPLETE_BRAND_PROVIDER_REVIEW_AND_FINAL_INTEGRATION'});
}
function fontProvider(ctx){
  const font=ctx.assets.fonts?.brand;
  if(!font)return null;
  if(font.format!=='framivra-glyph-paths/1'||font.family!=='Noto Sans SC'||font.weight!==600||!(font.unitsPerEm>0)||
    font.provenance?.license!=='OFL-1.1'||font.provenance.text!==BRAND||
    !/^[0-9a-f]{64}$/.test(font.provenance.fontSha256||'')||!/^[0-9a-f]{64}$/.test(font.provenance.licenseSha256||''))throw new Error('Unproven S8 weight-600 brand outline provider');
  return font;
}
function brandRun(ctx,font){
  const S=ctx.svg,mark=ctx.state.ending.wordmark,scale=mark.size/font.unitsPerEm,nodes=[];
  let cursor=mark.origin[0],minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
  for(const ch of BRAND){
    const glyph=font.glyphs?.[ch];
    if(!glyph||!Number.isFinite(glyph.advance)||glyph.advance<=0||typeof glyph.path!=='string'||!glyph.path||
       !Array.isArray(glyph.bounds)||glyph.bounds.length!==4||!glyph.bounds.every(Number.isFinite))throw new Error(`Missing verified brand glyph: ${ch}`);
    const [a,b,c,d]=glyph.bounds,y=mark.origin[1];
    minX=Math.min(minX,cursor+a*scale);maxX=Math.max(maxX,cursor+c*scale);
    minY=Math.min(minY,y-d*scale);maxY=Math.max(maxY,y-b*scale);
    nodes.push(S.path(glyph.path,INK,{transform:S.matrixString([scale,0,0,-scale,cursor,y])}));
    cursor+=glyph.advance*scale;
  }
  const bounds=[minX,minY,maxX-minX,maxY-minY];
  if(minX<64||minY<36||maxX>1216||maxY>600)throw new Error('Brand outline escapes film safety bounds');
  return {node:S.group(nodes,{'data-brand':BRAND,'data-font-sha256':font.provenance.fontSha256,'data-typography-status':font.reviewStatus||'UNREVIEWED'}),bounds,advance:cursor-mark.origin[0]};
}
function endingReadiness(ctx){
  const font=fontProvider(ctx);
  return {geometryImplemented:true,typographyAvailable:!!font,typographyApproved:font?.reviewStatus==='INDEPENDENTLY_APPROVED',productionImplemented:false,
    blockers:[...(!font?['APPROVED_LOCAL_BRAND_GLYPH_PROVIDER_REQUIRED']:font.reviewStatus!=='INDEPENDENTLY_APPROVED'?['BRAND_GLYPH_PROVIDER_INDEPENDENT_REVIEW_REQUIRED']:[]),
      'SCENE_REVIEW_AND_INTEGRATOR_READINESS_GATE_REQUIRED'],actualFinalAudio:'UNRUN',actualEngine:'UNRUN',performance:'UNRUN'};
}
function renderScene(ctx,args){
  const {layerId,globalFrame}=args;
  if(PERSISTENT.includes(layerId))return SOURCE.renderScene(ctx,args);
  // T07/T08 are rendered exclusively by the unchanged shared runtime.
  if(layerId!=='T15'||globalFrame<3360)return EMPTY;
  const font=fontProvider(ctx);
  if(!font)return EMPTY; // Explicitly no browser font lookup or fallback lettering.
  const S=ctx.svg,run=brandRun(ctx,font),id=ctx.id('S8-wordmark-reveal'),b=run.bounds;
  const clip=ctx.effectHelpers.textReveal({id,bounds:[b[0]-1,b[1]-1,b[2]+2,b[3]+2],progress:ctx.state.ending.wordmark.reveal,enabled:ctx.effects.text_reveal});
  return {defs:[clip],world:[],screen:[S.group([run.node],{'clip-path':`url(#${id})`,'data-owner':'T15'})]};
}
return Object.freeze({sceneId,implemented,resolveSceneState,renderScene,endingReadiness,brandRun,withSourceScene});
}
module.exports=withSourceScene(require('./07-source-layers.js'));
