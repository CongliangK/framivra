'use strict';
// R11 / S4. Pure frame-resolved paper mechanics. No browser, clock, or accumulated state.
// Read-only source authority: R06 ebce8d57…, R03 revision02, R04/R05 faba9ce8….
// The small support/rig corrections are recorded in evidence/S4-REPORT.md.
const sceneId='S4';
const implemented=true;
const OFFSET=Object.freeze([20,16]);
const FOOT_POINTS=Object.freeze([[11,48],[29,48],[-18,48],[-1,48]]);
const FLIGHT=Object.freeze([
  Object.freeze([[400,489],[475,440],[650,344],[756,344]].map(Object.freeze)),
  Object.freeze([[756,344],[831.3157894736842,344],[965,390],[1005,447]].map(Object.freeze))
]);
function at(ctx,f,ks){return ctx.math.keys(f,ks);}
function world(p){return [p[0]+OFFSET[0],p[1]+OFFSET[1]];}
function matrix(ctx,x,y,scale=1,angle=0){return ctx.math.poseMatrix({x,y,scale,angle});}
function birdsFeet(ctx,pose){const mat=ctx.math.poseMatrix(pose);return FOOT_POINTS.map(p=>ctx.math.transform(p,mat));}
function flightPoint(ctx,f){
  if(f<=1434)return ctx.math.cubic(FLIGHT[0],ctx.math.progress(f,1320,1434));
  return ctx.math.cubic(FLIGHT[1],ctx.math.progress(f,1434,1515));
}
function birdPose(ctx,f){
  let x,y,scale,angle,wing,feet;
  if(f<1320){
    x=at(ctx,f,[[1200,400],[1220,407],[1242,400],[1260,400],[1320,400]]);
    angle=at(ctx,f,[[1200,0],[1220,-2],[1242,0],[1260,0],[1300,8],[1312,8],[1320,0]]);
    scale=.9; wing=at(ctx,f,[[1200,0],[1260,0],[1300,12],[1312,12],[1320,12]]); feet=true;
    // The lowest toe is constrained to the existing origin's thin upper contact strip.
    const relative=FOOT_POINTS.map(p=>ctx.math.transform(p,matrix(ctx,0,0,scale,angle)));
    y=angle===0?490:533.2-Math.max(...relative.map(p=>p[1]));
  }else if(f<1515){
    [x,y]=flightPoint(ctx,f);
    scale=at(ctx,f,[[1320,.9],[1434,1],[1515,.82]]);
    angle=at(ctx,f,[[1320,0],[1362,-10],[1434,-5],[1470,1],[1515,0]]);
    // Support is released before the first upstroke. Only the original A hinge moves.
    wing=at(ctx,f,[[1320,12],[1326,12],[1350,-7],[1380,5],[1434,0],[1470,-5],[1515,0]]);
    feet=f>=1503;
  }else{
    x=at(ctx,f,[[1515,1005],[1530,1013],[1545,1005],[1560,1005],[1590,972],[1620,951],[1679,951]]);
    y=at(ctx,f,[[1515,447],[1530,449],[1545,447],[1571,447],[1572,447.15],[1590,450],[1679,450]]);
    scale=.82; angle=at(ctx,f,[[1515,0],[1530,5],[1545,3],[1560,0],[1590,0],[1679,0]]);
    wing=at(ctx,f,[[1515,0],[1530,3],[1545,0],[1679,0]]); feet=true;
  }
  return {pose:{x:x+20,y:y+16,scale,angle},stage:4,space:'world',wing,feet};
}
function gateOccluders(ctx,gateMatrix,bar){
  const S=ctx.svg,c=ctx.geometry.PALETTE;
  return [S.group([
    // Same foreground post for head, body, wings, feet, historical wing, and carried F.
    S.rect(52,-12,28,122,c.mid,{'data-part':'G-front-post'}),
    S.line([bar.pivot,bar.endpoint],c.ink,7,{'data-part':'G-open-bar'})
  ],{transform:S.matrixString(gateMatrix)})];
}
function resolveSceneState(ctx,{globalFrame:f,localSceneFrame}){
  if(!Number.isInteger(f)||f<1200||f>=1680||localSceneFrame!==f-1200)throw new RangeError('S4 requires a matching frame in [1200,1680)');
  const M=ctx.math;
  const cameraMatrix=[1,0,0,1,
    -20+at(ctx,f,[[1200,0],[1320,0],[1434,-12],[1560,-16],[1679,0]]),
    -16+at(ctx,f,[[1200,0],[1320,0],[1434,-8],[1560,-12],[1679,0]])];
  const carrier=ctx.geometry.carrierAssembly(matrix(ctx,750,386,1.8));
  const barAngle=at(ctx,f,[[1200,0],[1260,0],[1314,-98],[1470,-98],[1560,-48],[1679,-48]]);
  const barPivot=[-63,-9],barEndpoint=M.transform([47,-9],M.rotate(barAngle,barPivot));
  const gateOpen=M.clamp01(-barAngle/98);
  const camAngle=at(ctx,f,[[1200,0],[1260,110],[1314,250],[1470,250],[1560,310],[1679,310]]);
  const camPin=M.transform([106,108],M.rotate(camAngle,[101,112]));
  const gate={bar:{pivot:barPivot,endpoint:barEndpoint,length:110,angle:barAngle},cam:{center:[101,112],pin:camPin,angle:camAngle},
    signal:{mount:[0,-58.84],capY:-118-18*gateOpen},hang:M.transform([80/1.8,-15-Math.sqrt(6400-(80/1.8)**2)],carrier.gateMatrix),hangOccupied:f<=1352};
  const bird=birdPose(ctx,f);
  bird.occluders=f>=1380&&f<1515?gateOccluders(ctx,carrier.gateMatrix,gate.bar):[];
  bird.maskBounds=[-256,-256,2048,1536];
  const feet=birdsFeet(ctx,bird.pose);
  // The small spring plate is the actual support. Its top follows the contacting sole
  // after landing; the fixed shelf and legs beneath it never jump or change identity.
  const landed=f>=1515;
  const plateAngle=landed?bird.pose.angle:0;
  const sole=landed?feet[0]:world([1014.02,486.36]);
  const slope=Math.tan(plateAngle*Math.PI/180);
  const plateTop=x=>sole[1]+slope*(x-sole[0]);
  const plate=[world([950,0]),world([1130,0])].map(p=>[p[0],plateTop(p[0])]);
  const plateDown=at(ctx,f,[[1200,0],[1571,0],[1572,.15],[1590,1],[1679,1]]);
  const leverAngle=-18*plateDown;
  const leverPivot=world([1010,490]);
  const leverInput=M.transform(world([994,486.36]),M.rotate(leverAngle,leverPivot));
  const leverOutput=M.transform(world([1077,451]),M.rotate(leverAngle,leverPivot));
  const release=M.progress(f,1589,1619);
  const stopLocked=world([1077,451]);
  const stopEnd=world([1039.32,457.84]);
  const stopOrigin=[M.mix(stopLocked[0],stopEnd[0],release),M.mix(stopLocked[1],stopEnd[1],release)];
  const stop= {points:[stopOrigin,[stopOrigin[0]-14.4,stopOrigin[1]+23.76],[stopOrigin[0]+9.36,stopOrigin[1]+23.76]],
    locked:f<1590,cleared:f>=1590,origin:stopOrigin,lockedOrigin:stopLocked};
  const spin=f<1620?0:(f-1620)*1.5;
  const feedProgress=M.progress(f,1620,1679);
  const reel={center:world([1125,424]),scale:.72,rotation:spin,angularVelocity:f>=1620?1.5:0,stop,
    feedProgress,curveProgress:M.progress(f,1620,1640),feedEdgeX:(f<1640?ctx.math.cubic([[1117,373],[1015,376],[1030,480],[930,498]],M.progress(f,1620,1640))[0]:930-(f-1640)*395/39)+20,
    feedVelocity:f>=1640?-395/39:0};
  // One persistent four-corner reserve. Catch is three actual overlap frames; release
  // of the hanging root occurs only after that contact, never by an owner swap.
  const catchBird=birdPose(ctx,1351),catchPoint=M.transform([-15,-6],M.poseMatrix(catchBird.pose));
  const wingRoot=M.transform([-15,-6],M.poseMatrix(bird.pose));
  let fx,fy,fs,fa;
  if(f<=1352){[fx,fy]=world([810,185]);fs=.65;fa=0;}
  else{
    fx=at(ctx,f,[[1352,830],[1380,691],[1434,670],[1515,1060],[1545,1085],[1605,1066],[1679,1066]]);
    fy=at(ctx,f,[[1352,201],[1380,330],[1434,419],[1515,493],[1545,500],[1605,514],[1679,514]]);
    fs=at(ctx,f,[[1352,.65],[1434,.6],[1545,.55],[1679,.55]]);
    fa=at(ctx,f,[[1352,0],[1380,-35],[1434,-25],[1515,16],[1545,0],[1679,0]]);
  }
  const FMatrix=matrix(ctx,fx,fy,fs,fa),FRoot=[fx,fy],FEnd=M.transform([0,36],FMatrix);
  const carried=f>=1350&&f<1640;
  const gateAnchor=ctx.math.transform([80/1.8,-15-Math.sqrt(6400-(80/1.8)**2)],carrier.gateMatrix);
  const freeTongueEnd=f<1350?[ctx.math.mix(FEnd[0],catchPoint[0],ctx.math.smoothstep(ctx.math.progress(f,1260,1350))),ctx.math.mix(FEnd[1],catchPoint[1],ctx.math.smoothstep(ctx.math.progress(f,1260,1350)))]:(f<1640?wingRoot:[M.mix(wingRoot[0],FRoot[0],M.smoothstep(M.progress(f,1640,1655))),M.mix(wingRoot[1],FRoot[1],M.smoothstep(M.progress(f,1640,1655)))]);
  const frame={matrix:FMatrix,pose:{x:fx,y:fy,scale:fs,angle:fa},root:FRoot,lowerEndpoint:FEnd,
    tongueEnd:freeTongueEnd,gateAnchor,wingRoot,catchPoint,cornerIds:['F-0','F-1','F-2','F-3'],
    attachment:f<=1352?'gate':(f<1640?'wing-root':'feed-paper'),contact:carried,
    feedAnchor:f>=1640?world(M.cubic([[1138,423],[1081,421],[1054,531],[934,548]],.59).map((v,i)=>v-(i===1?1:0))):null};
  const edgeA=[[-110,124],[140,124]],edgeB=[[-102,132],[132,132]];
  const folds={hinge:edgeA,edgeA,edgeB,faces:{P_A:{hinge:edgeA,points:[...edgeA,edgeB[1],edgeB[0]]},P_B:{hinge:edgeB,points:[...edgeB,[124,140],[-94,140]]}}};
  // Nested precreased connector reveals a 130-unit subset of the existing P-C
  // edge. The wide outer reserve remains visible. This is a bounded flat-projection
  // design correction, not a claim of three-dimensional material conservation.
  const connectorProgress=M.smoothstep(M.progress(f,1620,1679));
  const carrierEdge=[[662,609.2],[792,609.2]];
  const paBase=carrierEdge.map(p=>[p[0]-34*connectorProgress,p[1]-97.2*connectorProgress]);
  const paDirection=[200/Math.hypot(200,136),-136/Math.hypot(200,136)];
  const pbDirection=[172/Math.hypot(172,139),139/Math.hypot(172,139)];
  const sharedEdge=paBase.map(p=>p.map((v,i)=>v+paDirection[i]*8*connectorProgress));
  const pbEnd=sharedEdge.map(p=>p.map((v,i)=>v+pbDirection[i]*16*connectorProgress));
  const paper={carrierEdge,connector:[carrierEdge[0],carrierEdge[1],paBase[1],paBase[0]],paBase,sharedEdge,pbEnd,
    pa:[paBase[0],paBase[1],sharedEdge[1],sharedEdge[0]],pb:[sharedEdge[0],sharedEdge[1],pbEnd[1],pbEnd[0]],
    progress:connectorProgress,paDirection,pbDirection};
  return M.deepFreeze({cameraMatrix,bird,mechanics:{carrier,gate,origin:{x:350,y:551,top:549.2,width:180},landing:{shelfY:506,plate,plateAngle,sole,feet,contact:landed},
    lever:{pivot:leverPivot,input:leverInput,output:leverOutput,angle:leverAngle,pressure:plateDown,plateJoint:[leverInput[0],plateTop(leverInput[0])]},
    reel,frame,folds,paper,fixedGuide:{x:120,y:666,width:1080},contactOrder:{catch:f>=1350,unhook:f>=1353,land:landed,plate:f>=1572,stop:f>=1590,turn:f>=1620}}});
}
function far(ctx){const S=ctx.svg,c=ctx.geometry.PALETTE;return S.group([
  S.rect(82,97,1110,8,c.grey),...[140,430,832,1138].map(x=>S.rect(x,105,6,335,c.pale)),
  S.rect(980,200,185,7,c.ink),S.rect(1000,148,30,52,c.red),S.rect(1049,130,30,70,c.ink),S.rect(1098,159,30,41,c.amber),
  S.line([[185,105],[185,168]],c.ink,2),S.polygon([[161,168],[209,168],[198,185],[172,185]],c.mid)
],{transform:'translate(20 16)','data-object':'W-far'});}
function gateShapes(ctx,m){
  const S=ctx.svg,c=ctx.geometry.PALETTE,g=m.gate;
  const rail=[S.line([[-80,0],[80,0]],c.ink,5),S.line([[-80,18],[80,18]],c.ink,5),...[-55,0,55].map(x=>S.line([[x,-3],[x,21]],c.ink,4))];
  return S.group([
    S.group([S.rect(-110,110,250,14,c.stock),...ctx.geometry.G['P-C'].receiver_rectangles.map(r=>S.rect(...r,c.ink)),S.circle(-108,118,4,c.mid)],{'data-object':'P-C-carrier'}),
    S.path(ctx.geometry.G.A.path,c.mid,{'data-object':'A-arch'}),
    S.group(rail,{transform:'translate(0 116) scale(1.35)','data-object':'R-rail'}),
    S.group([S.path('M-22.44 0 A22.44 22.44 0 0 1 22.44 0 Z',c.amber),S.rect(-2.04,0,4.08,-58.16-g.signal.capY,c.ink)],{transform:`translate(0 ${g.signal.capY})`,'data-object':'L-signal'}),
    S.circle(...g.signal.mount,4.08,c.ink),S.line([g.bar.pivot,g.bar.endpoint],c.ink,7,{'data-object':'G-bar'}),S.circle(...g.bar.pivot,7,c.ink),
    S.line([g.bar.endpoint,[72,35],g.cam.center],c.ink,2,{'data-object':'G-linkage'}),
    S.circle(...g.cam.center,15,c.ink),S.circle(...g.cam.pin,4,c.amber),S.circle(...g.hang.map((v,i)=>(v-[750,386][i])/1.8),3,c.ink,{'data-object':'F-hang-pin'})
  ],{transform:S.matrixString(m.carrier.carrierMatrix),'data-object':'G-assembly'});
}
function transport(ctx,m){
  const S=ctx.svg,c=ctx.geometry.PALETTE,r=m.reel,guide=m.fixedGuide;
  const n=[S.group([S.line([[guide.x,guide.y],[guide.x+guide.width,guide.y]],c.mid,3),S.line([[guide.x,guide.y+13],[guide.x+guide.width,guide.y+13]],c.grey,3),...[.15,.4,.65,.9].map(u=>S.line([[guide.x+guide.width*u,guide.y+13],[guide.x+guide.width*u,guide.y+25],[guide.x+guide.width*u+13,guide.y+25]],c.mid,2))],{'data-object':'W-guide'})];
  for(const [x,y] of [[330,533.2],[1040,490]])n.push(S.group([S.rect(x-90,y,180,12,c.stock),S.line([[x-75,y+12],[x-50,y+100]],c.ink,8),S.line([[x+75,y+12],[x+50,y+100]],c.ink,8)],{transform:'translate(20 16)','data-object':x===330?'origin-support':'landing-support'}));
  const plate=m.landing.plate;
  n.push(S.polygon([plate[0],plate[1],[plate[1][0],518],[plate[0][0],518]],c.stock,{'data-object':'landing-pressure-plate'}));
  n.push(S.line(plate,c.mid,1.5),S.line([m.lever.plateJoint,m.lever.input,m.lever.pivot,m.lever.output],c.ink,3,{'data-object':'Q-release-lever'}),S.circle(...m.lever.pivot,4,c.ink));
  n.push(S.line([m.lever.output,r.stop.origin],c.ink,2,{'data-object':'Q-stop-link'}));
  n.push(S.group([S.circle(0,0,77,c.ink),S.circle(0,0,68,c.stock),S.circle(0,0,48,c.paper),S.circle(0,0,36,c.pale),
    S.group([S.path('M-59 -22 A63 63 0 0 1 52 -33 M-47 36 A57 57 0 0 0 46 37','none',{stroke:c.mid,'stroke-width':2})],{transform:`rotate(${r.rotation})`,'data-object':'Q-rotating-stock'}),S.circle(0,0,12,c.ink)
  ],{transform:S.matrixString(matrix(ctx,...r.center,r.scale)),'data-object':'Q-reel'}));
  n.push(S.line([[r.center[0]-66*r.scale,r.center[1]+78*r.scale],[r.center[0]+66*r.scale,r.center[1]+78*r.scale]],c.ink,7,{'data-object':'Q-shelf'}));
  n.push(S.polygon(r.stop.points,c.pale,{'data-object':'Q-stop'}));
  // A continuous physical ribbon emerges from the same reel. No waveform is invented.
  if(r.feedProgress>0){
    const e=r.feedEdgeX-20,u=r.curveProgress;
    function partial(points){
      const mix=(a,b)=>a.map((v,i)=>ctx.math.mix(v,b[i],u));
      const a=mix(points[0],points[1]),b=mix(points[1],points[2]),d=mix(points[2],points[3]),ab=mix(a,b),bd=mix(b,d);
      return [points[0],a,ab,mix(ab,bd)];
    }
    const upper=partial([[1117,373],[1015,376],[1030,480],[930,498]]),lower=partial([[1138,423],[1081,421],[1054,531],[934,548]]);
    const P=p=>p.map(S.number).join(' ');
    const topEnd=u<1?upper[3]:[e,498],bottomEnd=u<1?lower[3]:[e,548];
    const d=`M${P(upper[0])} C${P(upper[1])} ${P(upper[2])} ${P(upper[3])} L${P(topEnd)} L${P(bottomEnd)} L${P(lower[3])} C${P(lower[2])} ${P(lower[1])} ${P(lower[0])} Z`;
    n.push(S.path(d,c.pale,{transform:'translate(20 16)','data-object':'Q-feed-paper'}));
    for(let i=0;i<3;i++){
      const x=e+90+i*130;
      if(x<927)n.push(S.line([[x+20,517],[x+28,522],[x+25,557]],c.mid,1.2,{'data-object':`Q-feed-crease-${i}`}));
    }
  }
  return n;
}
function frameShapes(ctx,m){
  const S=ctx.svg,c=ctx.geometry.PALETTE,F=m.frame;
  const corners=[];
  for(let i=3;i>=0;i--)corners.push(S.group([
    S.polygon(ctx.geometry.G.F.corner_polygon,c.red),S.polygon([[20,16],[30,16],[20,26]],c.paper)
  ],{transform:`translate(${i*13} ${-i*10}) scale(.5)`,'data-object':`F-${i}`}));
  return [...(F.feedAnchor?[S.line([F.root,F.feedAnchor],c.red,3,{'data-object':'F-feed-attachment'})]:[]),...(m.gate.hangOccupied?[S.line([F.lowerEndpoint,F.gateAnchor],c.red,3,{'data-object':'F-gate-attachment'})]:[]),
    S.line([F.tongueEnd,m.gate.hangOccupied?F.lowerEndpoint:F.root],c.red,3,{'data-object':'F-continuous-tongue'}),
    S.group([S.line([[0,36],[9,40],[13,26],[22,30],[26,16],[35,20],[39,6]],c.red,3),...corners],{transform:S.matrixString(F.matrix),'data-object':'F-folded-reserve'})];
}
function renderScene(ctx,{globalFrame,localSceneFrame,layerId}){
  const S=ctx.svg,c=ctx.geometry.PALETTE,m=ctx.state.mechanics;
  const defs=[],nodes=[];
  if(layerId==='T02')nodes.push(far(ctx));
  if(layerId==='T03')nodes.push(gateShapes(ctx,m));
  if(layerId==='T04')nodes.push(...transport(ctx,m));
  if(layerId==='T05'){
    const maskId=ctx.id('F-shared-gate-mask');
    defs.push(ctx.effectHelpers.structuralMask(maskId,ctx.state.bird.maskBounds,ctx.state.bird.occluders.map(ctx.geometry.silhouette)));
    nodes.push(S.group(frameShapes(ctx,m),{mask:`url(#${maskId})`,'data-owner':'T05'}));
  }
  if(layerId==='T06')nodes.push(S.group([
    S.polygon(m.folds.faces.P_A.points,c.pale,{'data-object':'P-A-folded'}),S.polygon(m.folds.faces.P_B.points,c.pale,{'data-object':'P-B-folded'}),
    S.line(m.folds.edgeA,c.mid,1,{'data-object':'P-A-hinge'}),S.line(m.folds.edgeB,c.mid,1,{'data-object':'P-B-hinge'})
  ],{transform:S.matrixString(m.carrier.carrierMatrix),'data-owner':'T06'}));
  if(layerId==='T06'&&m.paper.progress>0){
    const p=m.paper;
    nodes.push(S.polygon(p.connector,c.pale,{'data-object':'P-C-to-P-A-connector'}),
      S.line([p.carrierEdge[0],p.paBase[0]],c.mid,2),S.line([p.carrierEdge[1],p.paBase[1]],c.mid,2),
      S.polygon(p.pa,c.pale,{'data-object':'P-A'}),S.polygon(p.pb,c.stock,{'data-object':'P-B'}),
      S.line([...p.pa,p.pa[0]],c.mid,2),S.line(p.sharedEdge,c.mid,2));
  }
  // T07/T08 are intentionally absent: runtime owns ALL parts of the same original B.
  return ctx.math.deepFreeze({defs,world:nodes,screen:[]});
}
module.exports=Object.freeze({sceneId,implemented,resolveSceneState,renderScene});
