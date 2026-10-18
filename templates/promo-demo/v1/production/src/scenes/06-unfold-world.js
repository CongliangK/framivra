'use strict';
/** S6 actual flat-vector scene. Canonical S4/S5 world is retained; R03's
 * incompatible independently rescaled workshop targets are deliberately not used.
 * The two paper joints are computed once from an articulated planar chart. This
 * is an author-space fold projection, NOT a claim of a rigid 3-D material solve.
 */
const sceneId='S6';
const implemented=true;
const START=2160, END=2760, SETTLED=2700;
// Frozen canonical S4/S5 mechanical checkpoint, adopted without shared edits.
const S4_FIXED={"gate":{"bar":{"pivot":[-63,-9],"endpoint":[10.604366699474404,-90.74593080251336],"length":110,"angle":-48},"cam":{"center":[101,112],"pin":[101.1497602759568,105.59862734565897],"angle":310},"signal":{"mount":[0,-58.84],"capY":-126.81632653061224},"hang":[830,239.26696362323386],"hangOccupied":false},"landing":{"shelfY":506,"plate":[[970,505.36],[1150,505.36]],"plateAngle":0,"sole":[980.02,505.36],"feet":[[980.02,505.36],[994.78,505.36],[956.24,505.36],[970.18,505.36]],"contact":true},"lever":{"pivot":[1030,506],"input":[1013.6582738797529,507.4824261906848],"output":[1081.6691238111525,448.20465724136756],"angle":-18,"pressure":1,"plateJoint":[1013.6582738797529,505.36]}};
const C={paper:'#F1EDE1',ink:'#252B29',red:'#D9533B',grey:'#C3C6BB',mid:'#90998E',amber:'#D9AE53',pale:'#DCDCCF',stock:'#CBC5B3'};
const add=(a,b)=>[a[0]+b[0],a[1]+b[1]];
const sub=(a,b)=>[a[0]-b[0],a[1]-b[1]];
const mul=(a,t)=>[a[0]*t,a[1]*t];
const lerp=(a,b,t)=>[a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t];
const deg=r=>r*180/Math.PI;
function worldFromScreen(p,camera){return [(p[0]-camera[4])/camera[0],(p[1]-camera[5])/camera[3]];}
function cubicEdge(S,a,b,bend){
  const d=sub(b,a), n=Math.hypot(...d), normal=n?[-d[1]/n,d[0]/n]:[0,0];
  const p=add(lerp(a,b,1/3),mul(normal,bend)),q=add(lerp(a,b,2/3),mul(normal,bend));
  return {control:[a,p,q,b],d:S.cubicPath([a,p,q,b])};
}
function facePath(S,hinge,far,bend=0){
  // A shared hinge remains straight. The non-load-bearing outer paper lip curls.
  const e=cubicEdge(S,far[1],hinge[1],bend);
  return {d:`M${S.points([hinge[0]])} L${S.points([far[0],far[1]])} C${S.points(e.control.slice(1))} Z`,lip:e.control};
}
function planePolygon(M,hinge,far,lip){
  return [hinge[0],far[0],far[1],...Array.from({length:25},(_,i)=>M.cubic(lip,(i+1)/25))];
}
function sampleEdgeRoute(M,hAB,hCA,frame){
  // The physical buckle slides along the existing paper rim, never jumps to a
  // detached screen position. Its three-way endpoint is shared with F's tongue.
  if(frame<=2400)return lerp(hAB[0],hAB[1],59/130);
  const route=[lerp(hAB[0],hAB[1],59/130),hAB[0],hCA[0],hCA[1]];
  const lengths=route.slice(1).map((p,i)=>Math.hypot(...sub(p,route[i]))),total=lengths.reduce((a,b)=>a+b,0);
  let remaining=M.progress(frame,2400,2520)*total;
  for(let i=0;i<lengths.length;i++){
    if(remaining<=lengths[i])return lerp(route[i],route[i+1],M.smoothstep(remaining/lengths[i]));
    remaining-=lengths[i];
  }
  return hCA[1];
}
function resolveSceneState(ctx,{globalFrame,localSceneFrame}){
  const M=ctx.math,S=ctx.svg,G=ctx.geometry;
  if(!Number.isInteger(globalFrame)||globalFrame<START||globalFrame>=END)throw new RangeError('S6 frame must be in [2160,2760)');
  if(localSceneFrame!==globalFrame-START)throw new Error('S6 local/global frame mismatch');
  const f=Math.min(globalFrame,SETTLED), k=(a)=>M.keys(f,a), prog=(a,b)=>M.smoothstep(M.progress(f,a,b));
  // A single camera for every owner page; world-fixed guide and reel never rebase.
  const z=k([[2160,1],[2250,1.015],[2400,.88],[2610,.75],[2700,.72]]);
  const cameraMatrix=[z,0,0,z,k([[2160,-20],[2250,-24],[2400,-1],[2700,2]]),k([[2160,-16],[2250,-24],[2400,44],[2700,101.08]])];
  const endCamera=[.72,0,0,.72,2,101.08];
  const slide=k([[2160,0],[2250,0],[2280,.12],[2400,1],[2700,1]]);
  const gatePose={x:750+150*slide,y:386,scale:1.8,angle:0};
  const carrier=G.carrierAssembly(M.poseMatrix(gatePose));
  // P-C's connector is part of its one flat support, not another independently
  // scaled gate. Its actual hinge shares endpoint objects with P-A.
  const opening=prog(2160,2250);
  const caStartLocal=[(628-750)/1.8,(512-386)/1.8];
  const caEndWorld=worldFromScreen([215,530],endCamera);
  const caEndLocal=[(caEndWorld[0]-900)/1.8,(caEndWorld[1]-386)/1.8];
  const ca0=M.transform(lerp(caStartLocal,caEndLocal,opening),carrier.carrierMatrix);
  const edgeAngle=M.mix(0,90,opening), projectedEdge=M.mix(130,47/.72,opening);
  const edgeVector=[Math.cos(edgeAngle*Math.PI/180)*projectedEdge,Math.sin(edgeAngle*Math.PI/180)*projectedEdge];
  const hCA=[ca0,add(ca0,edgeVector)];
  const aSpan=lerp([200,-136],[413/.72,0],opening);
  const hAB=[add(hCA[0],aSpan),add(hCA[1],aSpan)];
  // P-B stays folded until its declared 2520 release. Its inherited support moves
  // as P-A opens; its own opening parameter remains exactly zero through2520.
  const bOpen=prog(2520,2610), inherited=lerp([172,139],[320,-210],opening);
  const bSpan=lerp(inherited,[387/.72,20/.72],bOpen);
  const bFree=[add(hAB[0],bSpan),add(hAB[1],bSpan)];
  const curlPhase=Math.sin(Math.PI*M.progress(f,2400,2610));
  const settle=Math.sin(Math.PI*M.progress(f,2610,2700))*(1-M.progress(f,2610,2700));
  const ripple=(f>=2280&&f<2700)?Math.sin((f-2280)*Math.PI/80)*(1-M.progress(f,2610,2700)):0;
  const aBend=ctx.effects.path_deformation?(f>=2280?3.2*ripple:0):0;
  const bBend=ctx.effects.path_deformation?(-145*curlPhase-9*settle+4*ripple):0;
  const aPath=facePath(S,hCA,hAB,aBend),bPath=facePath(S,hAB,bFree,bBend);
  const pcInner=[[662+150*slide,609.2],[792+150*slide,609.2]];
  const planes={PC:{hinge:hCA,points:[pcInner[0],pcInner[1],hCA[1],hCA[0]]},
    PA:{hinges:[hCA,hAB],points:[hCA[0],hCA[1],hAB[1],hAB[0]],path:aPath.d,lip:aPath.lip,polygon:planePolygon(M,hCA,hAB,aPath.lip)},
    PB:{hinge:hAB,farEdge:bFree,points:[hAB[0],hAB[1],bFree[1],bFree[0]],path:bPath.d,lip:bPath.lip,polygon:planePolygon(M,hAB,bFree,bPath.lip)},
    joints:{PC_PA:hCA,PA_PB:hAB}, projection:{materialEdge:130,projectedEdge,edgeAngle,aOpening:opening,bOpening:bOpen,aSpan,bSpan}};
  // The same original B; no independent page transforms. Body scale stays in a
  // narrow world-space range, so the common retreat genuinely makes B smaller.
  const birdPose={x:k([[2160,854],[2250,1015],[2350,1120],[2450,1155],[2520,1230],[2610,1260],[2700,(850-2)/.72]]),
    y:k([[2160,362],[2250,320],[2350,355],[2450,235],[2520,188],[2610,185],[2700,(245-101.08)/.72]]),
    scale:k([[2160,.8],[2450,.82],[2700,.86]]),angle:k([[2160,-5],[2250,-12],[2350,-5],[2450,-9],[2610,-4],[2700,-3]])};
  const beak=M.transform([140,-33],M.poseMatrix(birdPose));
  const foldRoot={x:k([[2160,887],[2280,1035],[2400,1135]]),y:k([[2160,368],[2280,350],[2400,280]]),scale:.8,angle:k([[2160,-25],[2280,-18],[2400,-12]])};
  const foldMatrix=M.poseMatrix(foldRoot), deploy=prog(2400,2520);
  const finalCorners=[{p:[170,556],a:-90},{p:[170,104],a:0},{p:[1110,104],a:90},{p:[1110,556],a:180}];
  const releasedCorners=[{p:[184,552],a:-90},{p:[182,112],a:0},{p:[1098,242],a:95},{p:[1080,491],a:160}];
  const corners=finalCorners.map((target,i)=>{
    const folded=M.transform([i*13,-i*10],foldMatrix), mid=worldFromScreen(releasedCorners[i].p,cameraMatrix), end=worldFromScreen(target.p,endCamera);
    let p,a,s;
    if(f<=2400){p=folded;a=foldRoot.angle;s=.4;}
    else if(f<2450){const u=prog(2400,2450);p=lerp(folded,mid,u);a=M.mix(foldRoot.angle,releasedCorners[i].a,u);s=M.mix(.4,i===3?.8:1,u);}
    else {const u=prog(2450,2700);p=lerp(mid,end,u);a=M.mix(releasedCorners[i].a,target.a,u);s=M.mix(i===3?.8:1,1,u);}
    return {id:`F-corner-${i}`,origin:p,angle:a,scale:s,matrix:M.poseMatrix({x:p[0],y:p[1],scale:s,angle:a})};
  });
  const lower=corners[0];
  const chainMatrix=f<=2400?foldMatrix:M.poseMatrix({x:lower.origin[0],y:lower.origin[1],angle:lower.angle,scale:M.mix(.8,lower.scale,prog(2400,2450))});
  const frameEndpoint=M.transform([0,36],chainMatrix);
  const chain=G.forceChain({frameEndpoint,paperAnchor:sampleEdgeRoute(M,hAB,hCA,f)});
  const birdOccluders=[S.polygon(planes.PA.points,C.paper,{'data-occluder':'P-A-paper'})];
  if(f>=2280&&f<2400)birdOccluders.push(S.group([S.rect(52,-12,28,122,C.mid)],{transform:S.matrixString(carrier.gateMatrix),'data-occluder':'G-near-column'}));
  if(f>=2280)birdOccluders.push(S.path(bPath.d,C.paper,{'data-occluder':'P-B-foreground'}));
  const gateOccluders=[S.path(bPath.d,C.paper,{'data-occluder':'P-B-foreground'})];
  const lampLift=prog(2520,2610);
  const lamp={cap:[M.mix(0,155,lampLift),M.mix(S4_FIXED.gate.signal.capY,-68,lampLift)],scale:.68,lift:lampLift,mount:S4_FIXED.gate.signal.mount};
  const feet=[[-66,110],[66,110]].map(p=>M.transform(p,carrier.gateMatrix));
  const receivers=[[-70,110],[70,110]].map(p=>M.transform(p,carrier.receiverMatrix));
  // Q, guide and both original shelves remain in their incoming world coordinates.
  const q={x:1145,y:440,scale:.72,rotation:617.73+.12*(f-2160)-.06*(f-2160)*(f-2160)/540};
  const guide={x:120,y:666,width:1080,secondY:679,bracketY:691,matrix:[1,0,0,1,0,0]};
  const feedEnd=lerp([110,556],hCA[1],prog(2160,2280));
  const feedBend=ctx.effects.path_deformation?8*ripple+8*curlPhase:0,fu=prog(2160,2280);
  const feedPoint=(a,b)=>lerp(a,b,fu);
  const ft=feedPoint([110,463],[feedEnd[0],feedEnd[1]-31]),fb=feedPoint([110,556],feedEnd);
  const feedJoinTop=feedPoint([919,463],[1110,feedEnd[1]-31]);
  const feedJoinBottom=feedPoint([927,556],[1112,feedEnd[1]]);
  const c1=feedPoint([1020,392],[1255,393]),c2=feedPoint([1028,445],[1255,feedEnd[1]-31+feedBend]);
  const c3=feedPoint([1070,556],[1240,feedEnd[1]+feedBend]),c4=feedPoint([1101,437],[1268,439]);
  const feedPath=`M1137 389 C${S.points([c1,c2,feedJoinTop])} L${S.points([ft,fb,feedJoinBottom])} C${S.points([c3,c4,[1158,439]])} Z`;
  const ift=add(ft,[10,10]),ifb=add(fb,[10,-10]),ijt=add(feedJoinTop,[0,10]),ijb=add(feedJoinBottom,[0,-10]);
  const feedInsetPath=`M1137 389 C${S.points([c1,add(c2,[0,10]),ijt])} L${S.points([ift,ifb,ijb])} C${S.points([c3,c4,[1158,439]])} Z`;
  const stationReveal=prog(2280,2520), operatorEnter=prog(2280,2310), handHold=prog(2280,2340)*(1-prog(2400,2490));
  const operator={x:k([[2160,775],[2280,775],[2400,790],[2520,785],[2700,720]]),y:568,scale:.73,enter:operatorEnter,handHold};
  const seamWidth=f<2280?11:7+6*(1-prog(2610,2700))+3*Math.abs(ripple);
  return M.deepFreeze({cameraMatrix,bird:{pose:birdPose,space:'world',stage:4,wing:k([[2160,0],[2280,9],[2400,-7],[2450,0],[2520,10],[2610,-3],[2700,0]]),feet:false,occluders:birdOccluders,maskBounds:[-500,-400,2500,1800]},
    mechanics:{carrier,gatePose,gateOccluders,feet,receivers,planes,forceChain:chain,paperAttachment:chain.paperAnchor,q,guide,feedPath,feedInsetPath,feedEnd,operator,lamp,frame:{corners,foldRoot,foldMatrix,deploy,beak,pullPoint:f<=2400?[foldRoot.x,foldRoot.y]:lerp([foldRoot.x,foldRoot.y],corners[2].origin,prog(2400,2450))},
      stationReveal,seamWidth,ripple,curlPhase,settle,slide,effectiveFrame:f,complete:f===SETTLED,projectionStatus:'PLANAR_SHARED_ANCHOR_GRAPH_NOT_RIGID_MATERIAL_CERTIFICATION'},
    boundaryRevision:'S4_S5_CANONICAL_FIXED_WORLD',sceneId});
}
function line(S,p,c=C.ink,w=2,attrs={}){return S.line(p,c,w,attrs);}
function groupPose(S,M,children,p,attrs={}){return S.group(children,{transform:S.matrixString(M.poseMatrix(p)),...attrs});}
function frameCorner(S,G){return [S.polygon(G.G.F.corner_polygon,C.red),S.polygon([[20,16],[30,16],[20,26]],C.paper)];}
function drawFar(ctx) {const S=ctx.svg,C=ctx.geometry.PALETTE;return S.group([S.rect(82,97,1110,8,C.grey),...[140,430,832,1138].map(x=>S.rect(x,105,6,335,C.pale)),S.rect(980,200,185,7,C.ink),S.rect(1000,148,30,52,C.red),S.rect(1049,130,30,70,C.ink),S.rect(1098,159,30,41,C.amber),S.line([[185,105],[185,168]],C.ink,2),S.polygon([[161,168],[209,168],[198,185],[172,185]],C.mid)],{transform:'translate(20 16)','data-object':'W-far'});}
function operatorShapes(ctx,m){
  const S=ctx.svg,M=ctx.math,o=m.operator,arm=lerp([74,-68],[(m.feet[0][0]-o.x)/o.scale,(m.feet[0][1]-o.y)/o.scale],o.handHold);
  const shapes=[S.circle(0,-105,21,C.ink),S.polygon([[-21,-77],[21,-77],[34,-4],[-31,-4]],C.mid),line(S,[[-19,-3],[-29,61],[-47,61]],C.ink,9),line(S,[[22,-3],[31,61],[49,61]],C.ink,9),line(S,[[-19,-64],[-51,-33],[-71,-49]],C.ink,7),line(S,[[21,-63],[50,-34],arm],C.ink,7)];
  return groupPose(S,M,shapes,{x:o.x,y:o.y,scale:o.scale,angle:0},{'data-object':'W-operator'});
}
function toolStations(ctx,m){
  const S=ctx.svg,M=ctx.math,G=ctx.geometry,stroke={'stroke':C.paper,'stroke-width':3};
  const cut=[S.rect(303,218,6,207,C.grey),S.rect(393,218,6,207,C.grey),S.rect(310,270,76,114,C.ink),S.rect(316,276,64,102,C.mid),S.rect(342,218,10,52,C.mid),groupPose(S,M,[S.polygon(G.G.bird.wing_a,'none',stroke),S.polygon(G.G.bird.wing_b,'none',stroke)],{x:353,y:331,scale:.35,angle:0}),line(S,[[295,426],[406,426]],C.ink,4)];
  const ink=[S.rect(463,246,76,46,C.ink),S.rect(469,253,64,5,C.mid),line(S,[[501,292],[501,335]],C.ink,8),S.rect(456,334,94,9,C.ink),groupPose(S,M,[S.polygon(G.G.bird.body,C.mid),S.polygon(G.G.bird.tail,C.mid)],{x:510,y:345,scale:.35,angle:0}),line(S,[[450,377],[553,377]],C.mid,3)];
  const eye=[S.rect(1203,320,28,40,C.ink),S.rect(1210,360,14,16,C.mid),line(S,[[1217,376],[1217,414]],C.ink,3),S.circle(1217,414,4,C.ink),S.rect(1187,435,64,5,C.stock),line(S,[[1190,441],[1190,477]],C.mid,3),line(S,[[1245,441],[1245,477]],C.mid,3)];
  return [S.group(cut,{'data-object':'W-cut-station','data-function':'guided-die-receiving-bed'}),S.group(ink,{'data-object':'W-ink-station','data-function':'reservoir-shaft-body-stamp-bed'}),S.group(eye,{'data-object':'W-eye-station','data-function':'bottle-shaft-punch-bed'})];
}
function gateShapes(ctx,m){
  const S=ctx.svg,M=ctx.math,g=S4_FIXED.gate,l=m.lamp.cap;
  const rail=[line(S,[[-80,0],[80,0]],C.ink,5),line(S,[[-80,18],[80,18]],C.ink,5),...[-55,0,55].map(x=>line(S,[[x,-3],[x,21]],C.ink,4))];
  const rodEndY=M.mix(-58.16-g.signal.capY,59.84,m.lamp.lift);
  const b=[S.group([S.rect(-110,110,250,14,C.stock),...ctx.geometry.G['P-C'].receiver_rectangles.map(r=>S.rect(...r,C.ink)),S.circle(-108,118,4,C.mid)],{'data-object':'P-C-carrier'}),
    S.path(ctx.geometry.G.A.path,C.mid,{'data-object':'A-arch'}),S.group(rail,{transform:'translate(0 116) scale(1.35)','data-object':'R-rail'}),
    S.group([S.path('M-22.44 0 A22.44 22.44 0 0 1 22.44 0 Z',C.amber),S.rect(-2.04,0,4.08,rodEndY,C.ink)],{transform:`translate(${l[0]} ${l[1]})`,'data-object':'L-signal'}),
    S.circle(...g.signal.mount,4.08,C.ink),line(S,[g.bar.pivot,g.bar.endpoint],C.ink,7,{'data-object':'G-bar'}),S.circle(...g.bar.pivot,7,C.ink),
    line(S,[g.bar.endpoint,[72,35],g.cam.center],C.ink,2,{'data-object':'G-linkage'}),S.circle(...g.cam.center,15,C.ink),S.circle(...g.cam.pin,4,C.amber),
    S.circle(...g.hang.map((v,i)=>(v-[750,386][i])/1.8),3,C.ink,{'data-object':'F-hang-pin'})];
  if(m.lamp.lift>0)b.push(line(S,[[l[0],l[1]+rodEndY],g.signal.mount],C.ink,2,{'data-object':'L-original-mount-link'}));
  return S.group(b,{transform:S.matrixString(m.carrier.gateMatrix),'data-object':'G-assembly'});
}

function drawCanonicalTransport(ctx,m){
  const S=ctx.svg,c=ctx.geometry.PALETTE,r={center:[1145,440],scale:.72,rotation:m.q.rotation,stop:{origin:[1059.32,473.84],points:[[1059.32,473.84],[1044.9199999999998,497.59999999999997],[1068.6799999999998,497.59999999999997]]}},guide=m.guide;
  const n=[S.group([S.line([[guide.x,guide.y],[guide.x+guide.width,guide.y]],c.mid,3),S.line([[guide.x,guide.y+13],[guide.x+guide.width,guide.y+13]],c.grey,3),...[.15,.4,.65,.9].map(u=>S.line([[guide.x+guide.width*u,guide.y+13],[guide.x+guide.width*u,guide.y+25],[guide.x+guide.width*u+13,guide.y+25]],c.mid,2))],{'data-object':'W-guide'})];
  for(const [x,y] of [[330,533.2],[1040,490]])n.push(S.group([S.rect(x-90,y,180,12,c.stock),S.line([[x-75,y+12],[x-50,y+100]],c.ink,8),S.line([[x+75,y+12],[x+50,y+100]],c.ink,8)],{transform:'translate(20 16)','data-object':x===330?'origin-support':'landing-support'}));
  const plate=S4_FIXED.landing.plate;
  n.push(S.polygon([plate[0],plate[1],[plate[1][0],518],[plate[0][0],518]],c.stock,{'data-object':'landing-pressure-plate'}));
  n.push(S.line(plate,c.mid,1.5),S.line([S4_FIXED.lever.plateJoint,S4_FIXED.lever.input,S4_FIXED.lever.pivot,S4_FIXED.lever.output],c.ink,3,{'data-object':'Q-release-lever'}),S.circle(...S4_FIXED.lever.pivot,4,c.ink));
  n.push(S.line([S4_FIXED.lever.output,r.stop.origin],c.ink,2,{'data-object':'Q-stop-link'}));
  n.push(S.group([S.circle(0,0,77,c.ink),S.circle(0,0,68,c.stock),S.circle(0,0,48,c.paper),S.circle(0,0,36,c.pale),
    S.group([S.path('M-59 -22 A63 63 0 0 1 52 -33 M-47 36 A57 57 0 0 0 46 37','none',{stroke:c.mid,'stroke-width':2})],{transform:`rotate(${r.rotation})`,'data-object':'Q-rotating-stock'}),S.circle(0,0,12,c.ink)
  ],{transform:S.matrixString(ctx.math.poseMatrix({x:r.center[0],y:r.center[1],scale:r.scale,angle:0})),'data-object':'Q-reel'}));
  n.push(S.line([[r.center[0]-66*r.scale,r.center[1]+78*r.scale],[r.center[0]+66*r.scale,r.center[1]+78*r.scale]],c.ink,7,{'data-object':'Q-shelf'}));
  n.push(S.polygon(r.stop.points,c.pale,{'data-object':'Q-stop'}));
  return n;
}
function renderScene(ctx,{globalFrame,localSceneFrame,layerId}){
  const S=ctx.svg,M=ctx.math,E=ctx.effectHelpers,G=ctx.geometry,m=ctx.state.mechanics,defs=[],world=[],screen=[];
  const f=m.effectiveFrame;
  if(layerId==='T02'){
    world.push(drawFar(ctx));
    const rid=ctx.id('station-reveal');
    defs.push(S.node('clipPath',{id:rid,clipPathUnits:'userSpaceOnUse'},[S.rect(270,205,1000,Math.max(.001,280*m.stationReveal),C.paper)]));
    if(m.stationReveal>0)world.push(S.group(toolStations(ctx,m),{'clip-path':`url(#${rid})`,'data-object':'W-remembered-stations'}));
  } else if(layerId==='T03'){
    const maskId=ctx.id('carrier-foreground-visible');
    defs.push(E.structuralMask(maskId,[-300,-300,2400,1600],f>=2280?m.gateOccluders:[]));
    const parts=[gateShapes(ctx,m)];
    if(f>=2280&&f<2700){
      m.feet.forEach((p,i)=>{
        const w=92+18*(1-M.progress(f,2280,2700))+3*Math.abs(m.ripple),h=5+1.5*m.curlPhase;
        const patch=E.contactPatch({id:ctx.id(`foot-contact-${i}`),bounds:[p[0]-w/2-9,p[1]-6,w+18,h+19],sigma:2.6-1.3*M.smoothstep(M.progress(f,2610,2700)),shape:S.rect(p[0]-w/2,p[1]-2,w,h,C.ink),enabled:ctx.effects.local_blur});
        defs.push(...patch.defs);parts.push(...patch.nodes);
      });
    }
    world.push(f>=2280?S.group(parts,{mask:`url(#${maskId})`,'data-layer-role':'middle-gate-carrier'}):parts);
  } else if(layerId==='T04'){
    world.push(...drawCanonicalTransport(ctx,m));
    world.push(S.path(m.feedPath,C.pale,{'data-object':'Q-continuous-feed'}));
    const shift=mul(sub(m.feedEnd,[110,556]),M.smoothstep(M.progress(f,2160,2280)));
    for(let i=0;i<3;i++)world.push(line(S,[[200+130*i,517],[208+130*i,522],[205+130*i,557]].map(p=>add(p,shift)),C.mid,1.2,{'data-object':`Q-feed-crease-${i}`}));
    world.push(S.path(m.feedInsetPath,C.paper,{'data-object':'Q-feed-inset'}));
    world.push(S.group([S.polygon([[300,506],[300,486],[332,467],[354,486],[354,506]],C.stock,{'data-object':'first-tongue'}),line(S,[[300,506],[354,506]],C.mid,2),line(S,[[777,506],[847,506]],C.mid,2)],{transform:S.matrixString(M.translate(...shift))}));
    const j=m.forceChain.paperAnchor;
    world.push(S.group([line(S,[add(j,[-101,33]),add(j,[-54,33]),add(j,[-35,0]),j],C.ink,5),S.circle(...add(j,[-54,33]),6,C.ink),S.circle(...j,5,C.ink),S.circle(...j,2,C.red)],{'data-object':'Q-P-buckle'}));
  } else if(layerId==='T06'){
    world.push(S.group([S.polygon([[-110,124],[140,124],[132,132],[-102,132]],C.pale,{'data-object':'P-A-folded-reserve'}),S.polygon([[-102,132],[132,132],[124,140],[-94,140]],C.pale,{'data-object':'P-B-folded-reserve'}),line(S,[[-110,124],[140,124]],C.mid,1),line(S,[[-102,132],[132,132]],C.mid,1)],{transform:S.matrixString(m.carrier.carrierMatrix),'data-owner':'T06'}));
    const paperMask=ctx.id('A-under-B'),seamId=ctx.id('shared-fold-seam');
    defs.push(E.structuralMask(paperMask,[-300,-300,2400,1600],f>=2280?[S.path(m.planes.PB.path,C.paper)]:[]));
    world.push(S.polygon(m.planes.PC.points,C.pale,{'data-object':'P-C-to-P-A-connector'}),line(S,[m.planes.PC.points[0],m.planes.joints.PC_PA[0]],C.mid,2),line(S,[m.planes.PC.points[1],m.planes.joints.PC_PA[1]],C.mid,2));
    world.push(S.path(m.planes.PA.path,C.pale,{'data-object':'P-A'}),S.path(m.planes.PB.path,C.stock,{'data-object':'P-B'}));
    const aOutline=line(S,[...m.planes.joints.PC_PA,...m.planes.joints.PA_PB.toReversed(),m.planes.joints.PC_PA[0]],C.mid,2);
    world.push(f>=2280?S.group([aOutline],{mask:`url(#${paperMask})`}):aOutline,line(S,m.planes.joints.PA_PB,C.mid,2));
    const h=m.planes.joints.PA_PB,v=sub(h[1],h[0]),len=Math.hypot(...v);
    defs.push(E.seamGradient({id:seamId,bounds:[0,0,Math.min(260,len),m.seamWidth],enabled:ctx.effects.gradient_alpha_reveal}));
    world.push(S.group([S.polygon([[0,0],[len,0],[len+9*(1-m.planes.projection.aOpening),m.seamWidth],[9*(1-m.planes.projection.aOpening),m.seamWidth]],`url(#${seamId})`)],{transform:S.matrixString(M.multiply(M.translate(...h[0]),M.rotate(deg(Math.atan2(v[1],v[0]))))),'data-effect':'local-paper-seam'}));
  } else if(layerId==='T05'){
    const maskId=ctx.id('frame-front-paper-mask');
    defs.push(E.structuralMask(maskId,[-300,-300,2400,1600],f>=2280?[S.path(m.planes.PB.path,C.paper)]:[]));
    const pieces=[];
    // Every corner has the same persistent ID, including the four folded reserves.
    if(f<=2400){
      pieces.push(S.group([line(S,[[0,36],[9,40],[13,26],[22,30],[26,16],[35,20],[39,6]],C.red,3),...[3,2,1,0].map(i=>S.group(frameCorner(S,G),{transform:S.matrixString(M.multiply(M.translate(i*13,-i*10),M.scale(.5))),'data-object':`F-corner-${i}`}))],{transform:S.matrixString(m.frame.foldMatrix),'data-object':'F-folded'}));
    } else {
      if(f<2520){
        const reserve=[[0,36],[9,40],[13,26],[22,30],[26,16],[35,20],[39,6]].map(p=>M.transform(p,m.frame.corners[2].matrix));
        pieces.push(line(S,reserve,C.red,3,{opacity:1-M.smoothstep(M.progress(f,2400,2520)),'data-object':'F-folded-reserve'}));
      }
      for(const corner of m.frame.corners.toReversed())pieces.push(S.group(frameCorner(S,G),{transform:S.matrixString(corner.matrix),'data-object':corner.id}));
    }
    if(f>2400){
      const cs=m.frame.corners;
      // Clockwise edges use transformed endpoints of the original four corner
      // arms. Control points change, not merely the group's CSS transform.
      const order=[1,2,3,0];
      for(let j=0;j<4;j++){
        const a=cs[order[j]],b=cs[order[(j+1)%4]],start=M.transform([72,7],a.matrix),end=M.transform([7,72],b.matrix);
        const bend=(1-M.smoothstep(M.progress(f,2450,2700)))*(j%2?32:-32);
        const actual=cubicEdge(S,start,end,bend),rigid=cubicEdge(S,start,end,0),edge=ctx.effects.path_deformation?actual:rigid;
        const revealId=ctx.id(`F-edge-${j}-reveal`),reveal=M.smoothstep(M.progress(f,2400+j*15,2505+j*15));
        const box=[Math.min(start[0],end[0])-80,Math.min(start[1],end[1])-80,Math.abs(end[0]-start[0])+160,Math.abs(end[1]-start[1])+160];
        // Actual local reveal mask follows the edge, with one dark folded portion.
        const cut=lerp(start,end,reveal),normal=sub(end,start),n=Math.hypot(...normal)||1,axis=[normal[0]/n,normal[1]/n],perp=[-axis[1],axis[0]];
        const revealPoly=[add(add(start,mul(axis,-100)),mul(perp,-100)),add(cut,mul(perp,-100)),add(cut,mul(perp,100)),add(add(start,mul(axis,-100)),mul(perp,100))];
        defs.push(S.node('mask',{id:revealId,x:box[0],y:box[1],width:box[2],height:box[3],maskUnits:'userSpaceOnUse','mask-type':'luminance'},[S.rect(...box,'black'),S.polygon(revealPoly,'white')]));
        pieces.push(S.path(edge.d,'none',{stroke:C.red,'stroke-width':5,'stroke-linecap':'round',...(ctx.effects.material_masks?{mask:`url(#${revealId})`}:{}),'data-object':`F-edge-${j}`}));
      }
    }
    if(f<2520){const release=M.smoothstep(M.progress(f,2490,2520));pieces.push(line(S,[m.frame.pullPoint,lerp(m.frame.beak,m.frame.pullPoint,release)],C.red,5,{'data-object':'B-F-pull-tab'}));}
    pieces.push(G.forceChainShapes(m.forceChain).tongue);
    world.push(f>=2280?S.group(pieces,{mask:`url(#${maskId})`,'data-layer-role':'continuous-red-frame'}):pieces);
  } else if(layerId==='T09'&&f>=2280){
    // This sparse owner enters as the same operator comes from behind the support.
    const rid=ctx.id('operator-support-reveal');
    defs.push(S.node('clipPath',{id:rid,clipPathUnits:'userSpaceOnUse'},[S.rect(m.operator.x-110,m.operator.y-110,220,Math.max(.001,180*m.operator.enter),C.paper)]));
    world.push(S.group([operatorShapes(ctx,m)],{'clip-path':`url(#${rid})`}));
  }
  // T07/T08 are exclusively emitted by the mandatory shared runtime bird path.
  return {defs,world:world.flat(),screen};
}
module.exports=Object.freeze({sceneId,implemented,resolveSceneState,renderScene});
