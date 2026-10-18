'use strict';
/** S3 / R10: actual frame-pure SVG geometry. R06 core is read-only.
 * Production remains fail-closed: approved source typography and final timeline
 * readback are absent. `implemented` may only change at the integrated readiness gate.
 */
const sceneId='S3';
const implemented=false;
const geometryImplemented=true;
const readiness=Object.freeze({geometry:'AUTHOR_IMPLEMENTED_UNREVIEWED', typography:'CANDIDATE_PATH_PROVIDER_PENDING_INDEPENDENT_REVIEW', sourceBinding:'BLUEPRINT_ONLY', browserPixels:'NOT_RUN', fullFilm:'NOT_RUN'});
const OWNERS=Object.freeze({A:810,R:930,L:1005});
const INITIAL=Object.freeze({A:{x:270,y:438,scale:.4,angle:0},L:{x:355,y:500,scale:.55,angle:0},R:{x:395,y:583,scale:.55,angle:0}});
const STRIPS=Object.freeze({
 A:[[145,460],[310,465],[362,342],[548,370],[734,398],[766,399],[902,280],[913,326],[773,445],[666,448],[538,416],[410,384],[306,509],[150,508]],
 L:[[160,534],[310,529],[490,326],[622,366],[754,406],[801,406],[938,338],[948,383],[807,452],[744,430],[615,411],[486,392],[321,575],[164,580]],
 R:[[188,602],[376,602],[452,509],[591,446],[730,383],[758,322],[935,419],[950,465],[773,368],[715,506],[585,492],[455,478],[371,648],[193,649]]
});
const clamp=(M,f,a,b)=>M.smoothstep(M.progress(f,a,b));
function poseMix(M,a,b,u){return Object.fromEntries(['x','y','scale','angle'].map(k=>[k,M.mix(a[k],b[k],u)]));}
function cubicOutline(S,p){
 return `M${S.points([p[0]])} C${S.points(p.slice(1,4))} C${S.points(p.slice(4,7))} L${S.points([p[7]])} C${S.points(p.slice(8,11))} C${S.points(p.slice(11,14))} Z`;
}
function localPose(M,matrix){return {x:matrix[4],y:matrix[5],scale:Math.hypot(matrix[0],matrix[1]),angle:Math.atan2(matrix[1],matrix[0])*180/Math.PI};}
function resolveSceneState(ctx,{globalFrame:f,localSceneFrame}){
 const M=ctx.math,S=ctx.svg,G=ctx.geometry;
 if(!Number.isInteger(f)||f<720||f>=1200||localSceneFrame!==f-720)throw new RangeError('S3 requires exact global [720,1200) and matching scene frame');
 const side=clamp(M,f,1140,1198), pan=clamp(M,f,1140,1198);
 const cameraMatrix=[1,0,0,1,-20*pan,-16*pan];
 const gatePose={x:M.mix(932,750,side),y:M.mix(261,386,side),scale:M.mix(1.1,1.8,side),angle:0};
 const gateMatrix=M.poseMatrix(gatePose),carrier=G.carrierAssembly(gateMatrix);
 // One rigid A, tilted around its retained left foot. The right foot stays clear
 // until the R locking operation. All three symbols use conformal transforms only.
 const tilt=M.keys(f,[[720,0],[810,-8],[930,-8],[945,0],[1199,0]]);
 const aSeated=M.multiply(gateMatrix,M.rotate(tilt,[-66,110]));
 const aTarget=localPose(M,aSeated);
 const aApproach=clamp(M,f,780,855);
 const aPose=poseMix(M,INITIAL.A,aTarget,aApproach);
 aPose.y-=90*Math.sin(Math.PI*aApproach);
 const rSeat=M.transform([0,116],gateMatrix),rTarget={x:rSeat[0],y:rSeat[1]+(1-clamp(M,f,930,945))*20,scale:gatePose.scale*1.35,angle:0};
 const rPose=poseMix(M,INITIAL.R,rTarget,clamp(M,f,900,945));
 const lPoint=M.transform([0,M.mix(-80,-118,clamp(M,f,1005,1049))],gateMatrix);
 const lPose=poseMix(M,INITIAL.L,{x:lPoint[0],y:lPoint[1],scale:gatePose.scale*.68,angle:0},clamp(M,f,960,1005));
 const glyphs={A:{owner:f<810?'T11':'T03',pose:aPose,matrix:M.poseMatrix(aPose)},R:{owner:f<930?'T11':'T03',pose:rPose,matrix:M.poseMatrix(rPose)},L:{owner:f<1005?'T11':'T03',pose:lPose,matrix:M.poseMatrix(lPose)}};
 const Fstart={x:1008,y:322,scale:.65,angle:0};
 const FhangPoint=M.transform([80/1.8,(201-386)/1.8],gateMatrix);
 const Fhang={x:FhangPoint[0],y:FhangPoint[1],scale:.65,angle:0};
 const Fpose=poseMix(M,Fstart,Fhang,clamp(M,f,1070,1100));
 const Fmatrix=M.poseMatrix(Fpose),Fend=M.transform([0,36],Fmatrix);
 const gateAnchorLocal=[80/1.8,-15-Math.sqrt(80*80-(80/1.8)**2)];
 const FgateAnchor=M.transform(gateAnchorLocal,gateMatrix);
 const baseBird={x:1032,y:306,scale:.8,angle:-2};
 const beakOffset=M.transform([140,-33],M.multiply(M.rotate(-2),M.scale(.8)));
 const pinLocal=[M.mix(100,101,side),M.mix(20/1.1,112,side)],pin=M.transform(pinLocal,gateMatrix);
 let birdPose={...baseBird};
 if(f<=795)birdPose=poseMix(M,baseBird,{...baseBird,x:pin[0]-beakOffset[0],y:pin[1]-beakOffset[1]},clamp(M,f,750,795));
 else if(f<855)birdPose=poseMix(M,{...baseBird,x:pin[0]-beakOffset[0],y:pin[1]-beakOffset[1]},baseBird,clamp(M,f,795,855));
 else if(f>=1050&&f<1070)birdPose=poseMix(M,baseBird,{...baseBird,x:Fstart.x-beakOffset[0],y:Fstart.y-beakOffset[1]},clamp(M,f,1050,1070));
 else if(f>=1070&&f<=1100)birdPose={...baseBird,x:Fpose.x-beakOffset[0],y:Fpose.y-beakOffset[1]};
 else if(f>1100){
  const prior={...baseBird,x:Fhang.x-beakOffset[0],y:Fhang.y-beakOffset[1]};
  const end={x:420,y:506,scale:.9,angle:0};
  birdPose=poseMix(M,prior,end,clamp(M,f,1100,1198));
  birdPose.y-=65*Math.sin(Math.PI*clamp(M,f,1100,1198));
 }
 const birdBeak=M.transform([140,-33],M.poseMatrix(birdPose));
 // A complete shared visibility region, never a wing-only correction.
 const occluders=f>=1140&&f<1185?[S.group([S.rect(52,-12,28,122,G.PALETTE.mid)],{transform:S.matrixString(gateMatrix)})]:[];
 const bird={pose:birdPose,space:'world',stage:4,wing:M.keys(f,[[720,0],[795,0],[825,-6],[855,0],[1049,0],[1070,-4],[1100,0],[1145,-8],[1180,0],[1199,0]]),feet:f>=1188,occluders,maskBounds:[-256,-256,2048,1536]};
 const pull=M.keys(f,[[720,0],[795,1],[879,0],[945,.8],[1049,0],[1100,.6],[1140,.15],[1199,.15]]);
 const wave=M.keys(f,[[720,0],[810,-1],[879,0],[930,1],[1005,-.6],[1049,0],[1120,.8],[1140,.2],[1199,.2]]);
 const strips={};
 for(const [index,name] of ['A','L','R'].entries()){
  const p=STRIPS[name].map(([x,y],i)=>{
   // Paired top/bottom controls share offsets; material width is not stretched.
   const rank=({0:0,1:1,2:2,3:3,4:4,5:5,6:6,7:6,8:5,9:4,10:3,11:2,12:1,13:0})[i];
   const envelope=Math.sin(rank*Math.PI/6);
   return [x+pull*envelope*(20-index*5),y+envelope*(wave*(12+index*4)+pull*(-18+index*10))];
  });
  strips[name]={points:ctx.effectHelpers.deform(ctx.effects.path_deformation,p,STRIPS[name]),rigidPoints:STRIPS[name]};
 }
 const AfootLeft=M.transform([-66,110],glyphs.A.matrix),AfootRight=M.transform([66,110],glyphs.A.matrix);
 const seats=[M.transform([-66,110],gateMatrix),M.transform([66,110],gateMatrix)];
 const barAngle=M.keys(f,[[720,0],[1005,0],[1049,-36.7],[1140,-36.7],[1198,0],[1199,0]]);
 const barStart=[-63,-9],barEnd=M.transform([47,-9],M.rotate(barAngle,barStart));
 const markerProgress=clamp(M,f,810,945),markerX=M.mix(230,862.7,markerProgress);
 const markerFree=[[markerX,447],[markerX,568]], markerFinal=[M.transform(barStart,gateMatrix),M.transform([47,-9],gateMatrix)];
 const marker=markerFree.map((p,i)=>p.map((v,j)=>M.mix(v,markerFinal[i][j],clamp(M,f,915,945))));
 return M.deepFreeze({cameraMatrix,bird,mechanics:{gatePose,gateMatrix,carrier,glyphs,strips,pin,pinLocal,tabEnd:f<=795?birdBeak:pin,birdBeak,F:{pose:Fpose,matrix:Fmatrix,lowerEndpoint:Fend,gateAnchor:FgateAnchor,hung:f>=1100,grasped:f>=1070&&f<=1100},Afeet:[AfootLeft,AfootRight],seats,rightFootGap:seats[1][1]-AfootRight[1],bar:{angle:barAngle,start:barStart,end:barEnd,length:110},marker,stripRetraction:clamp(M,f,1140,1198),side,sourceGuide:{points:[[310,461],[365,447+(ctx.effects.path_deformation?wave*7:0)],[415,430+(ctx.effects.path_deformation?wave*4:0)],[475,411]],flatProgress:clamp(M,f,1020,1050)},sourceReadiness:readiness,guide:{x:120,y:666,width:1080}}});
}
function glyphLocal(ctx,name){
 const {svg:S,geometry:G}=ctx,c=G.PALETTE;let shapes;
 if(name==='A')shapes=[S.path(G.G.A.path,c.mid)];
 if(name==='L')shapes=[S.path('M-33 0A33 33 0 0 1 33 0Z',c.amber),S.rect(-3,0,6,88,c.ink),S.circle(0,87,6,c.ink)];
 if(name==='R')shapes=[...G.G.R.double_rail_y.map(y=>S.line([[-80,y],[80,y]],c.ink,5)),...G.G.R.crossbar_x.map(x=>S.line([[x,-3],[x,21]],c.ink,4))];
 return shapes;
}
function glyph(ctx,name,matrix){
 const S=ctx.svg;return S.group(glyphLocal(ctx,name),{transform:S.matrixString(matrix),'data-object':name,'data-glyph-geometry':'approved-r03','data-owner':ctx.entry.layerId});
}
function foldedFrame(ctx,matrix){
 const {svg:S,math:M,geometry:G}=ctx,c=G.PALETTE;
 const corners=[3,2,1,0].map(i=>S.group([S.polygon(G.G.F.corner_polygon,c.red),S.polygon([[20,16],[30,16],[20,26]],c.paper)],{transform:S.matrixString(M.multiply(M.translate(i*13,-i*10),M.scale(.5))),'data-corner':`F-${i}`}));
 return S.group([S.line([[0,36],[9,40],[13,26],[22,30],[26,16],[35,20],[39,6]],c.red,3),...corners],{transform:S.matrixString(matrix),'data-object':'F','data-owner':'T05'});
}
function platform(ctx,x,y){const S=ctx.svg,c=ctx.geometry.PALETTE;return [S.rect(x-90,y,180,12,c.stock),S.line([[x-75,y+12],[x-50,y+100]],c.ink,8),S.line([[x+75,y+12],[x+50,y+100]],c.ink,8)];}
function reel(ctx,x,y,z){
 const S=ctx.svg,c=ctx.geometry.PALETTE;
 return S.group([S.circle(0,0,77,c.ink),S.circle(0,0,68,c.stock),S.circle(0,0,48,c.paper),S.circle(0,0,36,c.pale),S.path('M-59-22A63 63 0 0 1 52-33 M-47 36A57 57 0 0 0 46 37','none',{stroke:c.mid,'stroke-width':2}),S.circle(0,0,12,c.ink)],{transform:S.matrixString(ctx.math.poseMatrix({x,y,scale:z,angle:0})),'data-object':'Q','data-state':'locked'});
}

// Integrator-owned dependency; this author module neither fetches fonts nor uses
// host text metrics. Glyph outlines stay rigid while their baselines follow the
// cubic's measured arc. Candidate bytes do not imply font/source approval.
const sourceGlyphDependency=Object.freeze({api:'ctx.assets.fonts.sourceLabels',format:'framivra-glyph-paths/1',assetSha256:'2acef4fe4abb53af646c4bfb02a9bd35855cbec460fc537d4c6e218dd18ab7a0',fontSha256:'6b692c4b6d15ccf59f1c1fe8d11cb8a92f51960f3e9f1f523781755a3af7e29f',licenseSha256:'075f91382875da1c4b491f1facf2586f55448705133264d98ce15cab6211b125'});
function sourceGlyphs(ctx,provider,text,curve,size,offsetY){
 const {svg:S,math:M,geometry:G}=ctx;
 if(provider.format!==sourceGlyphDependency.format||!Number.isFinite(provider.unitsPerEm)||provider.unitsPerEm<=0||provider.provenance?.fontSha256!==sourceGlyphDependency.fontSha256||provider.provenance?.licenseSha256!==sourceGlyphDependency.licenseSha256)throw new Error('S3_SOURCE_GLYPH_PROVIDER_IDENTITY_MISMATCH');
 const scale=size/provider.unitsPerEm,table=[{t:0,p:M.cubic(curve,0),s:0}];
 for(let i=1;i<=128;i++){const p=M.cubic(curve,i/128),prev=table[i-1];table.push({t:i/128,p,s:prev.s+Math.hypot(p[0]-prev.p[0],p[1]-prev.p[1])});}
 let distance=0;const nodes=[];
 for(const char of text){
  const glyph=provider.glyphs?.[char];
  if(!glyph||!Number.isFinite(glyph.advance)||glyph.advance<=0||typeof glyph.path!=='string'||(char!==' '&&(!glyph.path||!Array.isArray(glyph.bounds)||glyph.bounds.length!==4||!glyph.bounds.every(Number.isFinite))))throw new Error('S3_SOURCE_GLYPH_MISSING:'+char);
  const advance=glyph.advance*scale,center=distance+advance/2;
  if(distance+advance>table[128].s)throw new Error('S3_SOURCE_LABEL_EXCEEDS_CURVE');
  let i=1;while(table[i].s<center)i++;
  const a=table[i-1],b=table[i],t=M.mix(a.t,b.t,(center-a.s)/(b.s-a.s)),p=M.cubic(curve,t),v=1-t;
  const tangent=[0,1].map(j=>3*v*v*(curve[1][j]-curve[0][j])+6*v*t*(curve[2][j]-curve[1][j])+3*t*t*(curve[3][j]-curve[2][j]));
  const angle=Math.atan2(tangent[1],tangent[0])*180/Math.PI;
  const matrix=M.multiply(M.translate(p[0],p[1]+offsetY),M.multiply(M.rotate(angle),M.multiply(M.translate(-advance/2,0),[scale,0,0,-scale,0,0])));
  if(glyph.path)nodes.push(S.path(glyph.path,G.PALETTE.ink,{transform:S.matrixString(matrix),'data-source-glyph':char,'data-glyph-bounds':glyph.bounds.join(',')}));
  distance+=advance;
 }
 return S.group(nodes,{'data-source-label':text,'data-glyph-provider':provider.family,'data-glyph-review':provider.reviewStatus||'UNKNOWN'});
}

function renderScene(ctx,{globalFrame:f,localSceneFrame,layerId}){
 if(f<720||f>=1200||localSceneFrame!==f-720)throw new RangeError('Invalid S3 render frame');
 const {svg:S,math:M,geometry:G,effectHelpers:E}=ctx,c=G.PALETTE,k=ctx.state.mechanics;
 const out={defs:[],world:[],screen:[]},id=ctx.id;
 if(layerId==='T07'||layerId==='T08')return out; // mandatory shared bird dispatcher
 if(layerId==='T03'){
  const nodes=[S.rect(...G.G['P-C'].local_carrier_rect,c.stock,{'data-object':'P-C'}),...G.G['P-C'].receiver_rectangles.map((r,i)=>S.rect(...r,c.ink,{'data-object':`receiver-${i}`})),S.circle(-108,118,4,c.mid)];
  out.world.push(S.group(nodes,{transform:S.matrixString(k.gateMatrix),'data-owner':'T03','data-assembly':'G-P-C'}));
  for(const name of ['A','R','L'])if(k.glyphs[name].owner==='T03')out.world.push(glyph(ctx,name,k.glyphs[name].matrix));
  if(f>=795)out.world.push(S.group([S.line([[100,110],k.pinLocal],c.ink,2),S.circle(...k.pinLocal,8/1.1,c.ink)],{transform:S.matrixString(k.gateMatrix),'data-object':'receiver-pin-mount'}));
  if(f>=1100)out.world.push(S.circle(...k.F.gateAnchor,3*k.gatePose.scale,c.ink,{'data-object':'F-hang-pin'}));
  if(f>=945){
   const endpoint=k.bar.end;
   const bar=[S.line([k.bar.start,endpoint],c.ink,7),S.circle(-63,-9,7,c.ink),S.line([endpoint,[72,35],[101,112]],c.ink,2),S.circle(101,112,15,c.ink),S.circle(106,108,4,c.amber)];
   out.world.push(S.group(bar,{transform:S.matrixString(k.gateMatrix),'data-object':'gate-bar-linkage','data-rigid-bar-length':110}));
  }

 }
 if(layerId==='T04'){
  if(f<1198)out.world.push(S.rect(171,160+720*k.side,9,400,c.pale,{'data-object':'W-ribbon-upright'}));
  const guide=k.guide;
  out.world.push(S.group([S.line([[guide.x,guide.y],[guide.x+guide.width,guide.y]],c.mid,3),S.line([[guide.x,guide.y+13],[guide.x+guide.width,guide.y+13]],c.grey,3),...[.15,.4,.65,.9].map(u=>S.line([[guide.x+guide.width*u,guide.y+13],[guide.x+guide.width*u,guide.y+25],[guide.x+guide.width*u+13,guide.y+25]],c.mid,2))],{'data-object':'W-guide','data-fixed-world':'true'}));
  if(f>=1140){
   const enter=clamp(M,f,1140,1198),left=M.mix(-220,350,enter),right=M.mix(1490,1060,enter),qx=M.mix(1490,1145,enter);
   out.world.push(S.group(platform(ctx,left,549.2),{'data-object':'origin-support'}),S.group(platform(ctx,right,506),{'data-object':'landing-support'}));
   const dx=right-1060;
   out.world.push(S.polygon([[970+dx,502.36],[1150+dx,502.36],[1150+dx,518],[970+dx,518]],c.stock,{'data-object':'landing-pressure-plate'}),S.line([[970+dx,502.36],[1150+dx,502.36]],c.mid,1.5),S.line([[1014+dx,502.36],[1014+dx,502.36],[1030+dx,506],[1097+dx,467]],c.ink,3,{'data-object':'Q-release-lever'}),S.circle(1030+dx,506,4,c.ink),S.line([[1097+dx,467],[1097+dx,467]],c.ink,2,{'data-object':'Q-stop-link'}),reel(ctx,qx,440,.72),S.line([[qx-47.52,496.16],[qx+47.52,496.16]],c.ink,7,{'data-object':'Q-shelf'}),S.polygon([[1097+dx,467],[1082.6+dx,490.76],[1106.36+dx,490.76]],c.pale,{'data-object':'Q-stop'}));
  }
 }
 if(layerId==='T06'){
  // P-C owns the common edge. These narrow folded reserves remain present before
  // the later S5/S6 hinge release; they are never substituted for the carrier.
  out.world.push(S.group([S.polygon([[-110,124],[140,124],[132,132],[-102,132]],c.pale,{'data-object':'P-A-folded'}),S.polygon([[-102,132],[132,132],[124,140],[-94,140]],c.pale,{'data-object':'P-B-folded'}),S.line([[-110,124],[140,124]],c.mid,1),S.line([[-102,132],[132,132]],c.mid,1)],{transform:S.matrixString(k.gateMatrix),'data-owner':'T06'}));
 }
 if(layerId==='T05'){
  const shapes=[];
  if(f>=1100)shapes.push(S.line([k.F.lowerEndpoint,k.F.gateAnchor],c.red,3,{'data-object':'F-gate-attachment'}));
  else shapes.push(S.line([k.pin,k.F.lowerEndpoint],c.stock,3,{'data-object':'F-carry-attachment'}));
  shapes.push(foldedFrame(ctx,k.F.matrix));
  if(f>=1100){const mask=id('F-shared-gate-mask');out.defs.push(E.structuralMask(mask,[-256,-256,2048,1536],[]));out.world.push(S.group(shapes,{mask:`url(#${mask})`,'data-owner':'T05'}));}
  else out.world.push(...shapes);
 }
 if(layerId==='T11'){
  if(f>=1198)return out;
  const A=cubicOutline(S,k.strips.A.points),L=cubicOutline(S,k.strips.L.points),R=cubicOutline(S,k.strips.R.points);
  const dA=id('A-under-L'),dR=id('strip-underpass'),over=id('R-over-L'),lineClip=id('ribbon-line-clip'),exit=id('strips-retract'),birdMask=id('ribbon-behind-bird');
  const birdShape=S.group([...G.birdLocal('wings',ctx.state.bird),...G.birdLocal('body',ctx.state.bird)],{transform:S.matrixString(M.poseMatrix(ctx.state.bird.pose))});
  const frontMaterials=[G.silhouette(birdShape)];
  if(f>=810){
   frontMaterials.push(G.silhouette(S.group([S.rect(...G.G['P-C'].local_carrier_rect,c.stock),...G.G['P-C'].receiver_rectangles.map(r=>S.rect(...r,c.ink))],{transform:S.matrixString(k.gateMatrix)})));
   for(const name of ['A','R','L'])if(k.glyphs[name].owner==='T03')frontMaterials.push(G.silhouette(S.group(glyphLocal(ctx,name),{transform:S.matrixString(k.glyphs[name].matrix)})));
  }
  out.defs.push(E.structuralMask(birdMask,[-256,-256,2048,1536],frontMaterials));
  const weaveA=k.strips.A.points[3][1]-STRIPS.A[3][1],weaveR=k.strips.R.points[3][1]-STRIPS.R[3][1];
  const Aunder=S.path('M546 365Q590 355 640 371L640 419Q590 401 546 414Z','black');
  const Runder=S.path('M675 392Q710 382 745 385L745 428Q710 432 675 438Z','black');
  out.defs.push(E.mask(dA,[100,250,1020,420],[S.group([Aunder],{transform:S.matrixString(M.translate(0,weaveA))})],ctx.effects.material_masks),E.mask(dR,[100,250,1020,420],[S.group([Runder],{transform:S.matrixString(M.translate(0,weaveR))})],ctx.effects.material_masks),S.node('clipPath',{id:over,clipPathUnits:'userSpaceOnUse'},[S.rect(765,340+weaveR,75,160,'white')]),S.node('clipPath',{id:lineClip,clipPathUnits:'userSpaceOnUse'},[S.path(R,'white')]),S.node('clipPath',{id:exit,clipPathUnits:'userSpaceOnUse'},[S.rect(1280*k.stripRetraction,0,1280*(1-k.stripRetraction),720,'white')]));
  const shapes=[S.path(R,c.stock,{'data-strip':'R'}),S.path(L,c.paper,{stroke:c.mid,'stroke-width':1.5,'data-strip':'L'}),S.path(A,c.pale,{mask:`url(#${dA})`,'data-strip':'A'}),...(ctx.effects.material_masks?[S.path(R,c.stock,{'clip-path':`url(#${over})`,'data-weave':'R-over-L'})]:[])];
  const rp=k.strips.R.points;
  const rail=(dy)=>`M${rp[0][0]+2},${rp[0][1]+dy} C${rp[1][0]+1},${rp[1][1]+dy} ${rp[2][0]+3},${rp[2][1]+dy+1} ${rp[3][0]},${rp[3][1]+dy+1} C${rp[4][0]-3},${rp[4][1]+dy+1} ${rp[5][0]+5},${rp[5][1]+dy+1} ${rp[6][0]+8},${rp[6][1]+dy+1}`;
  shapes.push(S.group([S.path(rail(13),'none',{stroke:c.ink,'stroke-width':3}),S.path(rail(28),'none',{stroke:c.ink,'stroke-width':3})],{'clip-path':`url(#${lineClip})`,mask:`url(#${dR})`,'data-object':'R-carrier-lines'}));
  const seams=[{name:'weave-seam-A',bounds:[515,413,105,55],d:'M515 413C561 415 588 446 617 449L618 468C582 463 556 436 515 433Z'},{name:'weave-seam-R',bounds:[620,462,130,48],d:'M620 487C672 481 711 477 741 462L747 481C709 496 676 503 622 506Z'}];
  for(const seam of seams){out.defs.push(E.seamGradient({id:id(seam.name),bounds:[seam.bounds[0],seam.bounds[1],seam.bounds[2]+weaveA*.5,seam.bounds[3]],enabled:ctx.effects.gradient_alpha_reveal}));shapes.push(S.path(seam.d,`url(#${id(seam.name)})`));}
  for(const [x,y] of [[161,472],[176,548],[209,616]])shapes.push(S.line([[x+30,y-8],[x+51,y+12],[x+72,y-8],[x+94,y+12]],c.mid,1.5,{'data-object':'ribbon-fold-reserve'}));
  for(const name of ['A','L','R'])if(k.glyphs[name].owner==='T11')shapes.push(glyph(ctx,name,k.glyphs[name].matrix));
  if(f>=810&&f<945)shapes.push(S.line(k.marker,c.ink,7,{'data-object':'time-marker-becomes-bar'}));
  shapes.push(S.line([[902,303],k.pin],c.stock,3),S.line([[939,442],k.pin],c.stock,3),S.line([[937,360],k.pin],c.stock,4),S.line([k.pin,k.tabEnd],c.stock,4,{'data-object':'single-grasp-tab'}));
  out.world.push(S.group([S.group(shapes,{mask:`url(#${birdMask})`})],{'clip-path':`url(#${exit})`,'data-owner':'T11','data-rich-window':f>=810&&f<1140?'E01':'outside'}));
  if(f>1140){
   const u=clamp(M,f,1140,1198),length=u*110,dy=12*(1-u),foldMask=id('residual-under-folds');
   const foldedOccluder=S.group([S.polygon([[-110,124],[140,124],[124,140],[-94,140]],'black')],{transform:S.matrixString(k.gateMatrix)});
   out.defs.push(E.structuralMask(foldMask,[-256,-256,2048,1536],[foldedOccluder]));
   out.world.push(S.group([S.group([S.line([[-95,127+dy],[-95+length*.2,135+dy],[-95+length*.4,127+dy],[-95+length*.6,135+dy],[-95+length*.8,127+dy],[-95+length,135+dy]],c.stock,5)],{transform:S.matrixString(k.gateMatrix),'data-object':'residual-strips-folding-under-PC'})],{mask:`url(#${foldMask})`}));
  }
 }
 if(layerId==='T12'&&f>=960&&f<1140){
  const anchor=M.transform([-66,40],k.glyphs.A.matrix),start=[475,411-125*k.sourceGuide.flatProgress],u=clamp(M,f,960,985),tip=start.map((v,i)=>M.mix(v,anchor[i],u));
  out.world.push(S.line([start,[(start[0]+tip[0])*.5,tip[1]],tip],c.mid,1.5,{'data-object':'source-to-A-leader'}),S.circle(...tip,3,c.ink));
 }
 if(layerId==='T14'&&f>=810&&f<1140){
  const source=ctx.sourceFields.find(x=>x.id===(f<1050?'S3.strip':'S3.gate'));
  if(!source)throw new Error('S3 approved source binding recipe missing');
  const p=k.sourceGuide.points.map((v,i)=>[v[0],M.mix(v[1],435,k.sourceGuide.flatProgress)]);
  out.defs.push(S.path(S.cubicPath(p),'none',{id:id('source-text-path'),'data-source-binding':source.id,'data-source-binding-status':ctx.sourceBindingStatus,'data-source-src':source.fields.src}),E.textReveal({id:id('source-text-reveal'),bounds:[300,386,520,90],progress:clamp(M,f,810,840),enabled:ctx.effects.text_reveal}));
  const provider=ctx.assets?.fonts?.sourceLabels;
  if(provider){
   const fields=source.displayFields;
   if(!fields||typeof fields.src!=='string'||fields.src!==source.fields.src.split('/').pop())throw new Error('S3_SOURCE_BINDING_DISPLAY_MISMATCH');
   const labels=f<1050?[fields.src]:['src: '+fields.src,'dst_in: '+fields.dst_in,'dur: '+fields.dur];
   if(f>=1050&&['dst_in','dur'].some(key=>!Number.isInteger(source.fields[key])||fields[key]!==String(source.fields[key])))throw new Error('S3_SOURCE_BINDING_NUMBER_MISMATCH');
   const text=labels.map((label,i)=>sourceGlyphs(ctx,provider,label,p,M.mix(12,11.5,k.sourceGuide.flatProgress),i*18));
   out.world.push(S.group(text,{transform:S.matrixString(M.translate(0,-125*k.sourceGuide.flatProgress)),'clip-path':`url(#${id('source-text-reveal')})`,'data-owner':'T14','data-source-binding':source.id,'data-source-binding-status':ctx.sourceBindingStatus,'data-source-text-status':'CANDIDATE_GLYPHS_UNREVIEWED'}));
  }
  // Without the explicit provider only diagnostic defs remain. No machine-font
  // fallback is possible, and implemented=false keeps normal film mode closed.
 }
 if(layerId==='T02'&&f>=1140){
  const y=M.mix(-430,0,clamp(M,f,1140,1198));
  const shapes=[S.rect(102,113,1110,8,c.grey),...[160,450,852,1158].map(x=>S.rect(x,121,6,335,c.pale)),S.rect(1000,216,185,7,c.ink),S.rect(1020,164,30,52,c.red),S.rect(1069,146,30,70,c.ink),S.rect(1118,175,30,41,c.amber),S.line([[205,121],[205,184]],c.ink,2),S.polygon([[181,184],[229,184],[218,201],[192,201]],c.mid)];
  out.world.push(S.group(shapes,{transform:S.matrixString(M.translate(0,y)),'data-owner':'T02','data-object':'W-far'}));
 }
 return out;
}
module.exports=Object.freeze({sceneId,implemented,geometryImplemented,readiness,sourceGlyphDependency,resolveSceneState,renderScene});
