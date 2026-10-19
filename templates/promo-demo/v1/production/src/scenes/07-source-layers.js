'use strict';
/** S7 reuses the actual S6 world. Production remains fail-closed until the
 * approved glyph asset, actual final timeline binding and review are integrated.
 */
function withWorld(WORLD){
if(!WORLD||WORLD.sceneId!=='S6'||typeof WORLD.resolveSceneState!=='function'||typeof WORLD.renderScene!=='function')throw new Error('S7 requires the S6 scene contract');
const sceneId='S7',implemented=false,START=2760,END=3240;
const PERSISTENT=['T02','T03','T04','T05','T06','T09'];
const INK='#252B29',PAPER='#F1EDE1',MID='#90998E',RED='#D9533B';
const EMPTY=Object.freeze({defs:Object.freeze([]),world:Object.freeze([]),screen:Object.freeze([])});
const add=(a,b)=>[a[0]+b[0],a[1]+b[1]];
const sub=(a,b)=>[a[0]-b[0],a[1]-b[1]];
const mul=(a,b)=>[a[0]*b,a[1]*b];
const lerp=(a,b,t)=>[a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t];
function inverse(m){
  const det=m[0]*m[3]-m[1]*m[2];
  if(!Number.isFinite(det)||Math.abs(det)<1e-12)throw new Error('Singular S7 transform');
  return [m[3]/det,-m[1]/det,-m[2]/det,m[0]/det,(m[2]*m[5]-m[3]*m[4])/det,(m[1]*m[4]-m[0]*m[5])/det];
}
function stateFrame(f){return f>=2940&&f<3090?2940:f;}
function absent(layer,f){return (layer==='T05'&&f>=2985&&f<3015)||(layer==='T03'&&f>=3045&&f<3075);}
function withdrawal(M,layer,f){
  const q=layer==='T05'?[2970,2985,3015,3030,1]:layer==='T03'?[3030,3045,3075,3090,-1]:null;
  if(!q||f<q[0]||f>=q[3])return [0,0];
  const u=f<q[1]?M.smoothstep(M.progress(f,q[0],q[1])):f<q[2]?1:1-M.smoothstep(M.progress(f,q[2],q[3]));
  return [q[4]*1800*u,0];
}
function resolveSceneState(ctx,{globalFrame,localSceneFrame}){
  if(!Number.isInteger(globalFrame)||globalFrame<START||globalFrame>=END)throw new RangeError('S7 frame must be in [2760,3240)');
  if(localSceneFrame!==globalFrame-START)throw new Error('S7 local/global frame mismatch');
  if(!WORLD.implemented)throw new Error('S7 requires integrated actual S6; scaffold substitution forbidden');
  const M=ctx.math,S=ctx.svg,f=stateFrame(globalFrame);
  const base=WORLD.resolveSceneState(ctx,{globalFrame:2759,localSceneFrame:599});
  const cameraMatrix=base.cameraMatrix,camInv=inverse(cameraMatrix);
  const separation=M.keys(f,ctx.assets.motion.S7_separation.progress_keys);
  const regroup=M.smoothstep(M.progress(f,3090,3180));
  const ending=M.smoothstep(M.progress(f,3180,3239)),miniScale=M.mix(1,.66,ending);
  // One miniature transform for original world only. B and F are excluded.
  const miniScreen=[miniScale,0,0,miniScale,640*(1-miniScale),485*(1-miniScale)];
  const miniWorld=ending===0?M.IDENTITY:M.multiply(camInv,M.multiply(miniScreen,cameraMatrix));
  const recipe=ctx.assets.motion.S7_separation.group_offsets_at_one,matrices={},offsets={};
  for(const layer of PERSISTENT){
    const offset=mul(recipe[layer==='T09'?'T09_operator':layer],separation);
    offsets[layer]=offset;
    const translated=separation===0?M.IDENTITY:M.multiply(camInv,M.multiply(M.translate(...offset),cameraMatrix));
    matrices[layer]=layer==='T05'?translated:M.multiply(miniWorld,translated);
  }
  // Same corner IDs/rotations/scale; inherited S6 draws their real joining paths.
  const cornerTargets=[[150,569],[150,156],[1130,156],[1130,569]];
  const corners=base.mechanics.frame.corners.map((corner,i)=>{
    if(ending===0)return corner;
    const screen=M.transform(corner.origin,cameraMatrix),p=M.transform(lerp(screen,cornerTargets[i],ending),camInv);
    return {...corner,origin:p,matrix:M.poseMatrix({x:p[0],y:p[1],scale:corner.scale,angle:corner.angle})};
  });
  const baseBird=base.bird,baseScreen=M.transform([baseBird.pose.x,baseBird.pose.y],cameraMatrix);
  let birdScreen=add(baseScreen,mul(recipe.B,separation));
  if(f>=3090){
    const beakOffset=M.transform([140,-33],M.poseMatrix({x:0,y:0,scale:baseBird.pose.scale*cameraMatrix[0],angle:-3}));
    const contactRoot=sub([1110,104],beakOffset);
    birdScreen=lerp(add(baseScreen,recipe.B),contactRoot,regroup);
    if(f>=3180)birdScreen=lerp(contactRoot,[1045,248],ending);
  }
  const root=M.transform(birdScreen,camInv);
  const bird=globalFrame===2760?baseBird:{...baseBird,pose:{...baseBird.pose,x:root[0],y:root[1]},wing:M.mix(baseBird.wing,6,ending),
    occluders:baseBird.occluders.map(n=>S.group([n],{transform:S.matrixString(matrices.T06)}))};
  // A narrow changing peel follows the existing P-A/P-B material hinge.
  const peelU=M.progress(f,2820,2910),peelWidth=15*Math.sin(Math.PI*peelU);
  const h=base.mechanics.planes.joints.PA_PB,edge=sub(h[1],h[0]);
  const length=Math.hypot(...edge),normal=[edge[1]/length,-edge[0]/length];
  const center=lerp(h[0],h[1],peelU),reach=length*.32;
  const pa=add(center,mul(edge,-.32)),pb=add(center,mul(edge,.32));
  const peelPath=ctx.effects.path_deformation?`M${S.points([pa])} C${S.points([add(pa,mul(normal,peelWidth)),add(pb,mul(normal,peelWidth)),pb])} L${S.points([add(pb,mul(normal,-1)),add(pa,mul(normal,-1))])} Z`:
    S.pathPoints([pa,add(pa,mul(normal,peelWidth*.5)),add(pb,mul(normal,peelWidth*.5)),pb],true);
  const support=f>3090&&f<3180?Math.sin(Math.PI*M.progress(f,3090,3180)):base.mechanics.operator.handHold;
  const mechanics={...base.mechanics,operator:{...base.mechanics.operator,handHold:support},frame:{...base.mechanics.frame,corners}};
  const originalEndpoint=M.transform([0,36],corners[0].matrix),originalAnchor=base.mechanics.forceChain.paperAnchor;
  const miniAnchor=M.transform(originalAnchor,miniWorld);
  // Existing tongue retracts onto its lower-left reserve during ending retreat.
  // It is never duplicated into the miniature or relabeled as another F.
  const paperAnchor=ending>0?lerp(miniAnchor,add(originalEndpoint,[10,10]),M.smoothstep(M.progress(f,3180,3210))):originalAnchor;
  mechanics.forceChain=ending===0?base.mechanics.forceChain:ctx.geometry.forceChain({frameEndpoint:originalEndpoint,paperAnchor});
  return M.deepFreeze({...base,cameraMatrix,bird,mechanics,sceneId,
    sourceLayers:{stateFrame:f,separation,regroup,ending,offsets,matrices,miniScreen,miniWorld,
      originalScene:'S6',originalFrame:2759,peel:{path:peelPath,width:peelWidth,center,normal,reach},
      labelExit:M.smoothstep(M.progress(f,3090,3150)),leaderExit:M.smoothstep(M.progress(f,3090,3180))},
    productionReadiness:'INCOMPLETE_FONT_REVIEW_AND_ACTUAL_FINAL_SOURCE_BINDING'});
}
function remapMask(ctx,node,layerMatrix){
  const S=ctx.svg,M=ctx.math,front=ctx.state.sourceLayers.matrices.T06,name=node.attrs?.id||'';
  if(node.tag==='mask'&&(name.endsWith('-carrier-foreground-visible')||name.endsWith('-frame-front-paper-mask'))){
    const relative=M.multiply(inverse(layerMatrix),front);
    return S.node(node.tag,node.attrs,[node.children[0],S.group(node.children.slice(1),{transform:S.matrixString(relative)})]);
  }
  return node;
}
function renderInherited(ctx,args){
  const S=ctx.svg,M=ctx.math,E=ctx.effectHelpers,layer=args.layerId,sl=ctx.state.sourceLayers;
  const inherited=WORLD.renderScene(ctx,args),ownMatrix=sl.matrices[layer];
  const extra=withdrawal(M,layer,args.globalFrame),camInv=inverse(ctx.state.cameraMatrix);
  const withdrawMatrix=M.multiply(camInv,M.multiply(M.translate(...extra),ctx.state.cameraMatrix));
  const currentMatrix=M.multiply(withdrawMatrix,ownMatrix);
  const defs=inherited.defs.map(n=>remapMask(ctx,n,currentMatrix));
  let shapes=inherited.world;
  if(layer==='T06'&&sl.peel.width>1e-10){
    const maskId=ctx.id('s7-local-paper-peel'),gradId=ctx.id('s7-peel-alpha');
    defs.push(E.mask(maskId,[-300,-300,2400,1600],[S.path(sl.peel.path,'black')],ctx.effects.material_masks));
    shapes=[S.group(shapes,{mask:`url(#${maskId})`,'data-effect':'bounded-material-peel'})];
    const p=sl.peel.center,w=Math.max(2,sl.peel.width);
    defs.push(E.seamGradient({id:gradId,bounds:[p[0],p[1]-sl.peel.reach,w,2*sl.peel.reach],enabled:ctx.effects.gradient_alpha_reveal}));
    shapes.push(S.path(sl.peel.path,`url(#${gradId})`,{'data-effect':'bounded-peel-alpha'}));
  }
  if(layer==='T05'){
    const f=sl.stateFrame,pull=M.smoothstep(M.progress(f,2760,2790))*(1-M.smoothstep(M.progress(f,2850,2910)));
    if(pull>0){
      const corner=ctx.state.mechanics.frame.corners[2].origin;
      const target=M.transform(M.transform([140,-33],M.poseMatrix(ctx.state.bird.pose)),inverse(ownMatrix));
      const start=add(corner,[-8,15]),end=lerp(start,target,pull);
      const controls=ctx.effects.path_deformation?[start,add(start,[-28,35]),add(end,[20,-6]),end]:[start,lerp(start,end,1/3),lerp(start,end,2/3),end];
      shapes=[...shapes,S.path(S.cubicPath(controls),'none',{stroke:RED,'stroke-width':3,'data-object':'F-source-paper-ear'})];
    }
  }
  return {defs,world:[S.group(shapes,{transform:S.matrixString(currentMatrix),'data-s7-source-owner':layer})],screen:inherited.screen};
}
function sourceRecord(ctx,id){
  const records=ctx.sourceFields?.filter(x=>x.id===id)||[];
  if(records.length!==1)throw new Error(`S7 needs one bound source record: ${id}`);
  const value=records[0],track=id==='S7.middle'?'T03':'T05';
  if(value.track_id!==track||typeof value.fields?.src!=='string'||!value.fields.src||value.fields.src!==value.clip_src)throw new Error(`Invalid source binding: ${id}`);
  if(id==='S7.middle'&&(!Number.isInteger(value.fields.dur)||value.fields.dur<=0))throw new Error('S7 duration requires its bound integer field');
  return value;
}
function sourceLayout(ctx){
  const M=ctx.math,sl=ctx.state.sourceLayers,f=sl.stateFrame;
  const reflow=M.smoothstep(M.progress(f,2820,2910)),exit=sl.labelExit,camera=ctx.state.cameraMatrix;
  const middleAnchor=M.transform(M.transform([-50,-76],ctx.state.mechanics.carrier.gateMatrix),M.multiply(camera,sl.matrices.T03));
  const frameAnchor=M.transform(M.transform(ctx.state.mechanics.frame.corners[2].origin,sl.matrices.T05),camera);
  // Paper slips follow source-owner anchors. They are not fake application UI.
  const middleOrigin=add(middleAnchor,[-205-470*exit,-113-95*exit]);
  // Keep the slip above both red wing tips; it must not become an accidental
  // opaque foreground mask over the required shared B silhouette.
  const frameOrigin=add(frameAnchor,[-270+430*exit,11-70*exit]);
  const middleBox=[...middleOrigin,M.mix(344,226,reflow),M.mix(34,57,reflow)],frameBox=[...frameOrigin,226,34];
  const fields=sourceRecord(ctx,'S7.middle'),frame=sourceRecord(ctx,'S7.frame');
  return M.deepFreeze({reflow,exit,middle:{id:'S7.middle',owner:'T03',anchor:middleAnchor,bounds:middleBox,
      lines:[{text:`src: ${fields.fields.src.split('/').pop()}`,x:middleBox[0]+10,y:middleBox[1]+22,size:12.5},
        {text:`dur: ${fields.fields.dur}`,x:middleBox[0]+M.mix(240,10,reflow),y:middleBox[1]+M.mix(22,44,reflow),size:12.5,
          reveal:M.smoothstep(M.progress(f,2880,2910))}],fields:fields.fields},
    frame:{id:'S7.frame',owner:'T05',anchor:frameAnchor,bounds:frameBox,
      lines:[{text:frame.fields.src.split('/').pop(),x:frameBox[0]+10,y:frameBox[1]+22,size:12.5}],fields:frame.fields}});
}
function leader(ctx,item,exit){
  const S=ctx.svg,b=item.bounds,end=item.owner==='T03'?[b[0]+b[2]-16,b[1]+b[3]]:[b[0]+b[2],b[1]+b[3]/2],anchor=item.anchor;
  const controls=item.owner==='T03'?[anchor,add(anchor,[-18,-20]),add(end,[15,27]),end]:[anchor,add(anchor,[34,10]),add(end,[28,-3]),end];
  return S.group([S.path(S.cubicPath(controls),'none',{stroke:MID,'stroke-width':1.25,'data-source-binding':item.id}),
    S.circle(...anchor,2.4,INK),S.rect(...b,PAPER,{'data-source-slip':item.id}),
    S.line([[b[0],b[1]+b[3]],[b[0]+b[2],b[1]+b[3]]],MID,.8)],{opacity:1-exit,'data-owner':item.owner});
}
function fontProvider(ctx){
  const font=ctx.assets.fonts?.sourceLabels;
  if(!font)return null;
  if(font.format!=='framivra-glyph-paths/1'||!(font.unitsPerEm>0)||!font.glyphs||font.provenance?.license!=='OFL-1.1'||
    !/^[a-f0-9]{64}$/.test(font.provenance.fontSha256||'')||!/^[a-f0-9]{64}$/.test(font.provenance.licenseSha256||''))throw new Error('Unproven S7 glyph-path provider');
  return font;
}
function glyphRun(ctx,font,{text,x,y,size}){
  const S=ctx.svg,nodes=[],scale=size/font.unitsPerEm;
  let cursor=x,minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
  for(const character of text){
    const glyph=font.glyphs[character];
    if(!glyph||!Number.isFinite(glyph.advance)||glyph.advance<=0||typeof glyph.path!=='string')throw new Error(`Missing verified source-label glyph U+${character.codePointAt(0).toString(16)}`);
    if(glyph.path){
      if(!Array.isArray(glyph.bounds)||glyph.bounds.length!==4||!glyph.bounds.every(Number.isFinite))throw new Error('Unbounded glyph outline');
      const [a,b,c,d]=glyph.bounds;
      minX=Math.min(minX,cursor+a*scale);maxX=Math.max(maxX,cursor+c*scale);
      minY=Math.min(minY,y-d*scale);maxY=Math.max(maxY,y-b*scale);
      nodes.push(S.path(glyph.path,INK,{transform:S.matrixString([scale,0,0,-scale,cursor,y])}));
    }
    cursor+=glyph.advance*scale;
  }
  return {node:S.group(nodes,{'data-bound-text':text,'data-font-sha256':font.provenance.fontSha256}),
    bounds:minX===Infinity?[x,y,0,0]:[minX,minY,maxX-minX,maxY-minY],advance:cursor-x};
}
function sourceReadiness(ctx){
  const font=fontProvider(ctx),actual=ctx.sourceBindingStatus==='ACTUAL_FINAL_TIMELINE';
  return {geometryImplemented:true,typographyAvailable:!!font,typographyApproved:font?.reviewStatus==='INDEPENDENTLY_APPROVED',
    actualFinalBinding:actual,productionImplemented:false,
    blockers:[...(!font?['APPROVED_LOCAL_GLYPH_PROVIDER_REQUIRED']:font.reviewStatus!=='INDEPENDENTLY_APPROVED'?['GLYPH_PROVIDER_INDEPENDENT_REVIEW_REQUIRED']:[]),
      ...(!actual?['ACTUAL_FINAL_TIMELINE_SOURCE_BINDING_REQUIRED']:[]),'SCENE_REVIEW_AND_INTEGRATOR_READINESS_GATE_REQUIRED']};
}
function renderScene(ctx,args){
  const {globalFrame,layerId}=args;
  if(absent(layerId,globalFrame))return EMPTY;
  if(PERSISTENT.includes(layerId))return renderInherited(ctx,args);
  // Shared runtime is the ONLY owner of T07/T08. No B glyph appears here.
  if(layerId!=='T12'&&layerId!=='T14')return EMPTY;
  if(globalFrame<2820||globalFrame>=3180||(layerId==='T14'&&globalFrame>=3150))return EMPTY;
  const S=ctx.svg,M=ctx.math,E=ctx.effectHelpers,layout=sourceLayout(ctx),f=ctx.state.sourceLayers.stateFrame;
  const entry=M.smoothstep(M.progress(f,2820,2850));
  if(layerId==='T12')return {defs:[],world:[],screen:[S.group([leader(ctx,layout.middle,ctx.state.sourceLayers.leaderExit),leader(ctx,layout.frame,ctx.state.sourceLayers.leaderExit)],{opacity:entry})]};
  const font=fontProvider(ctx);
  if(!font)return EMPTY; // No system-font fallback or invented outline substitute.
  const defs=[],screen=[];
  for(const item of [layout.middle,layout.frame]){
    const id=ctx.id(`${item.owner}-source-text-reveal`);
    defs.push(E.textReveal({id,bounds:item.bounds,progress:M.progress(f,2820,2880),enabled:ctx.effects.text_reveal}));
    const nodes=item.lines.map(line=>{
      const run=glyphRun(ctx,font,line),[x,y,w,h]=run.bounds,b=item.bounds;
      if(x<b[0]-.001||y<b[1]-.001||x+w>b[0]+b[2]+.001||y+h>b[1]+b[3]+.001)throw new Error(`Source glyph bounds escape ${item.id}`);
      return S.group([run.node],{opacity:line.reveal??1});
    });
    screen.push(S.group(nodes,{'clip-path':`url(#${id})`,opacity:1-layout.exit,
      'data-source-binding':item.id,'data-source-binding-status':ctx.sourceBindingStatus||'UNBOUND',
      'data-typography-status':font.reviewStatus||'UNREVIEWED'}));
  }
  return {defs,world:[],screen};
}
return Object.freeze({sceneId,implemented,resolveSceneState,renderScene,sourceLayout,sourceReadiness,glyphRun,withdrawal,withWorld});
}
// Default final-bundle path. withWorld is a pure dependency factory for isolated
// author tests and downstream scenes; it does not mutate shared module state.
module.exports=withWorld(require('./06-unfold-world.js'));
