'use strict';
/** S5 visual author candidate. Native PCM binding and user audio audition are reported separately.
 * World coordinates inherit S4; static R03 background re-layout is NOT a camera.
 * Only this module owns S5 state. T07/T08 remain mandatory shared-runtime bird pages.
 */
const sceneId='S5', implemented=true, PICKUP_FRAME=1920;
const WAVEFORM_DEPENDENCY=Object.freeze({status:'ACTUAL_PCM_NOT_BOUND_AUDITION_PENDING',owner:'T13',window:[1710,2160],analysisWindow:[1680,2160],fps:30,sampleRate:48000,samplesPerFrame:1600,missing:'No actual PCM envelope bound; no invented signal, cue ticks or audio-readiness claim',required:'actual-pcm SHA256 provenance, frame-indexed envelope, user audio audition and real removal comparison'});
const O=[20,16], CAMERA=[1,0,0,1,-20,-16];
const S4_FIXED={"gate":{"bar":{"pivot":[-63,-9],"endpoint":[10.604366699474404,-90.74593080251336],"length":110,"angle":-48},"cam":{"center":[101,112],"pin":[101.1497602759568,105.59862734565897],"angle":310},"signal":{"mount":[0,-58.84],"capY":-126.81632653061224},"hang":[830,201],"hangOccupied":false},"landing":{"shelfY":506,"plate":[[970,505.36],[1150,505.36]],"plateAngle":0,"sole":[980.02,505.36],"feet":[[980.02,505.36],[994.78,505.36],[956.24,505.36],[970.18,505.36]],"contact":true},"lever":{"pivot":[1030,506],"input":[1013.6582738797529,507.4824261906848],"output":[1081.6691238111525,448.20465724136756],"angle":-18,"pressure":1,"plateJoint":[1013.6582738797529,505.36]}};

const plus=(a,b)=>[a[0]+b[0],a[1]+b[1]], sub=(a,b)=>[a[0]-b[0],a[1]-b[1]], mul=(p,s)=>p.map(x=>x*s), wp=p=>plus(p,O);
function resolveSceneState(ctx,{globalFrame:f,localSceneFrame}) {
 if(!Number.isInteger(f)||f<1680||f>=2160||localSceneFrame!==f-1680)throw new RangeError('S5 requires matching frame in [1680,2160)');
 const M=ctx.math, smooth=(a,b)=>M.smoothstep(M.progress(f,a,b)), key=k=>M.keys(f,k);
 // Feed deformation is material payout from a fixed axle, never a replacement reel.
 const payout=smooth(1680,1768), lift=smooth(2130,2159), pull=smooth(2115,2159);
 const gate={pose:{x:750,y:386,scale:1.8,angle:0},barAngle:-48};
 const assembly=ctx.geometry.carrierAssembly(M.poseMatrix(gate.pose));
 const carrierEdge=[[642,593.2],[772,593.2]].map(wp);
 const paBase=[[608,496],[738,496]].map(wp);
 // Every panel edge has one pair of endpoint objects, consumed by both faces.
 // Oblique author-space projection: material length stays fixed while fold depth changes.
 const paWidth=300, paFinal=[200,-136], paLen=Math.hypot(...paFinal), paDirection=mul(paFinal,1/paLen);
 const paDepth=M.mix(8,paLen,lift), paAngle=Math.acos(paDepth/paWidth)*180/Math.PI;
 const sharedEdge=paBase.map(p=>plus(p,mul(paDirection,paDepth)));
 const pbWidth=260,pbFinal=[172,139],pbLen=Math.hypot(...pbFinal),pbDirection=mul(pbFinal,1/pbLen);
 const pbDepth=M.mix(16,pbLen,lift),pbAngle=Math.acos(pbDepth/pbWidth)*180/Math.PI;
 const pbEnd=sharedEdge.map(p=>plus(p,mul(pbDirection,pbDepth)));
 const pa=[paBase[0],paBase[1],sharedEdge[1],sharedEdge[0]],pb=[sharedEdge[0],sharedEdge[1],pbEnd[1],pbEnd[0]];
 // F and B use exact approved contact targets. F rests on the feed until pickup.
 // B approaches the stationary reserve, then carries it into the f1950 buckle contact.
 const birdPose={x:key([[1680,971],[1770,640],[1800,330.8],[1830,530],[1860,735],[1890,610],[1920,748],[1950,690],[2010,690],[2100,702],[2159,854]]),
 y:key([[1680,466],[1770,438],[1800,447.6],[1830,425],[1860,477.4],[1890,458],[1920,501.4],[1950,465],[2010,465],[2100,465],[2159,362]]),
 scale:key([[1680,.82],[1770,.8],[2159,.8]]),angle:key([[1680,0],[1770,-3],[1800,0],[1830,-4],[1860,0],[1890,0],[2100,0],[2159,-5]])};
 const beak=M.transform([140,-33],M.poseMatrix(birdPose));
 const framePose={x:key([[1680,1066],[1770,925],[1890,860],[1920,860],[1950,802],[2010,802],[2100,814],[2159,887]]),
 y:key([[1680,514],[1770,488],[1890,475],[1920,475],[1950,439],[2010,439],[2100,439],[2159,368]]),
 scale:key([[1680,.55],[1890,.65],[1920,.65],[1950,.7],[2100,.7],[2159,.8]]),angle:key([[1680,0],[1890,-5],[1920,-5],[1950,-15],[2100,-15],[2159,-25]])};
 const frameMatrix=M.poseMatrix(framePose),frameEndpoint=M.transform([0,36],frameMatrix);
 const anchor=wp([key([[1680,792],[1950,792],[2010,792],[2100,804],[2159,867]]),key([[1680,463],[2100,463],[2159,360]])]);
 // Connected material tab: starts folded, then seats onto the free P-A edge.
 const tabRoot=plus(sharedEdge[0],mul(sub(sharedEdge[1],sharedEdge[0]),59/130));
 const tabReserve=plus(mul(plus(tabRoot,anchor),.5),[0,14*(1-lift)]);
 const chain=ctx.geometry.forceChain({frameEndpoint,paperAnchor:anchor});
 const linked=f>=1950,seat=smooth(1950,1965);
 const tap=key([[1680,0],[1799,0],[1800,0],[1806,1],[1818,.15],[1830,0],[2159,0]]);
 const cover=key([[1680,0],[1860,0],[1875,1],[2159,1]]);
 // The contact points are derived from B, not independent decorative marks.
 const footTip=M.transform([29,48],M.poseMatrix(birdPose));
 const firstHinge=[wp([280,490]),wp([334,490])];
 const firstTip=wp([334,470+18*tap]);
 // In the touching frame the right foot is physically constrained to the tongue tip.
 if(f>=1794&&f<=1806) {
   const u=M.smoothstep(M.progress(f,1794,1800))*(1-M.smoothstep(M.progress(f,1800,1806)));
   const desired=wp([334,470+18*tap]); birdPose.x+=u*(desired[0]-footTip[0]);birdPose.y+=u*(desired[1]-footTip[1]);
 }
 const resolvedBeak=M.transform([140,-33],M.poseMatrix(birdPose));
 const secondHinge=[wp([757,490]),wp([827,490])];
 const coverContact=wp([827,435]);
 if(f>=1854&&f<=1866) {
   const u=M.smoothstep(M.progress(f,1854,1860))*(1-M.smoothstep(M.progress(f,1860,1866)));
   birdPose.x+=u*(coverContact[0]-resolvedBeak[0]);birdPose.y+=u*(coverContact[1]-resolvedBeak[1]);
 }
 const feedU=M.progress(f,1679,1768),feedT=89,feedV=-395/39;
 const feedLeft=(2*feedU**3-3*feedU**2+1)*535+(feedU**3-2*feedU**2+feedU)*feedT*feedV+(-2*feedU**3+3*feedU**2)*90;
 const feed={payout,left:feedLeft,velocity:((6*feedU**2-6*feedU)*535+(3*feedU**2-4*feedU+1)*feedT*feedV+(-6*feedU**2+6*feedU)*90)/feedT,top:M.mix(498,447,payout),bottom:M.mix(548,540,payout),joinX:M.mix(930,899,payout),sourceTop:wp([1117,373]),sourceBottom:wp([1138,423])};
 // Integral of piecewise linear angular velocity. Keeps a nonzero S4 boundary tangent.
 function integral(t,a,b,v0,v1){const d=Math.max(0,Math.min(t,b)-a);return d<=0?0:v0*d+(v1-v0)*d*d/(2*(b-a));}
 const reelAngle=88.5+1.5*Math.min(Math.max(f-1679,0),286)+integral(f,1965,2010,1.5,.8)+integral(f,2010,2100,.8,.12)+Math.max(f-2100,0)*.12;
 const reelVelocity=f<1965?1.5:f<2010?M.mix(1.5,.8,M.progress(f,1965,2010)):f<2100?M.mix(.8,.12,M.progress(f,2010,2100)):.12;
 const coverPoints=[secondHinge[0],secondHinge[1],wp([827+cover*55,435+cover*55]),wp([757+cover*55,435+cover*55])];
 // Incoming F reserve really touches the released ribbon through its short tongue.
 // Project its moving root to the actual lower feed cubic, then collapse the tongue
 // when the root is inside the ribbon. The first endpoint is exactly S4's f1679 anchor.
 const feedLower=[[1138,423],[1081,421],[M.mix(1054,1050,payout),M.mix(531,540,payout)],[M.mix(934,907,payout),feed.bottom]];
 const screenF=[framePose.x-20,framePose.y-16];
 let feedAnchor=null;
 if(f<PICKUP_FRAME){let lowerY=feed.bottom;if(screenF[0]>feedLower[3][0]){let a=0,b=1;for(let n=0;n<32;n++){const t=(a+b)/2;if(M.cubic(feedLower,t)[0]>screenF[0])a=t;else b=t;}lowerY=M.cubic(feedLower,(a+b)/2)[1];}
 const projected=wp([screenF[0],Math.min(screenF[1],lowerY-1)]),incoming=wp(M.cubic(feedLower,.59).map((v,i)=>v-(i===1?1:0))),u=smooth(1680,1700);feedAnchor=incoming.map((v,i)=>M.mix(v,projected[i],u));}
 const occluders=[];
 // The paper peel may cover only its actual material silhouette; whole B shares it.
 if(f>=2130)occluders.push(ctx.svg.polygon(pa,'black'));
 return M.deepFreeze({cameraMatrix:CAMERA.slice(),bird:{pose:birdPose,space:'world',stage:4,wing:key([[1680,0],[1740,-9],[1770,2],[1800,7],[1830,-5],[1860,4],[1890,0],[2159,0]]),feet:f<1830,occluders,maskBounds:[0,0,1600,900]},
 mechanics:{gate,assembly,landing:S4_FIXED.landing,lever:S4_FIXED.lever,carrierEdge,connector:[carrierEdge[0],carrierEdge[1],paBase[1],paBase[0]],paper:{paBase,sharedEdge,pbEnd,pa,pb,paWidth,pbWidth,paAngle,pbAngle,paDirection,pbDirection,tabRoot,tabReserve,anchor},
 frame:{pose:framePose,matrix:frameMatrix,feedAnchor,beak:M.transform([140,-33],M.poseMatrix(birdPose)),grasp:M.transform([0,0],frameMatrix),chain,linked,seat},
 reel:{pose:{x:1145,y:440,scale:.72,angle:0},angle:reelAngle,velocity:reelVelocity,unlocked:true},guide:{x:120,y:666,width:1080,fixedMatrix:M.IDENTITY},feed,
 tongues:{firstHinge,firstTip,tap,secondHinge,coverPoints,cover,coverContact},quiet:f>=2010&&f<2100,lift,pull},
 dependencies:{waveform:ctx.assets.waveform?{...WAVEFORM_DEPENDENCY,status:'ACTUAL_NATIVE_PCM_BOUND_AUDITION_PENDING',missing:null,provenance:ctx.assets.waveform.provenance,required:'User audio audition and final engine waveform/removal verification remain pending'}:WAVEFORM_DEPENDENCY,audio:ctx.assets.waveform?'ACTUAL_NATIVE_AUDIO_BOUND_USER_AUDITION_PENDING':'ACTUAL_NATIVE_AUDIO_NOT_BOUND_USER_AUDITION_PENDING'},revision:'S5-preserve-S4-world-v1'});
}
function foldedFrame(ctx,frame) {
 const S=ctx.svg,M=ctx.math,C=ctx.geometry.PALETTE,corner=ctx.geometry.G.F.corner_polygon;
 return S.group([S.line([[0,36],[9,40],[13,26],[22,30],[26,16],[35,20],[39,6]],C.red,3),...[3,2,1,0].map(i=>S.group([S.polygon(corner,C.red),S.polygon([[20,16],[30,16],[20,26]],C.paper)],{transform:S.matrixString(M.multiply(M.translate(i*13,-i*10),M.scale(.5))),'data-object':`F-corner-${i}`}))],{transform:S.matrixString(frame.matrix),'data-object':'F-folded'});
}
function drawGate(ctx,m){
  const S=ctx.svg,c=ctx.geometry.PALETTE,g=S4_FIXED.gate;
  const rail=[S.line([[-80,0],[80,0]],c.ink,5),S.line([[-80,18],[80,18]],c.ink,5),...[-55,0,55].map(x=>S.line([[x,-3],[x,21]],c.ink,4))];
  return S.group([
    S.group([S.rect(-110,110,250,14,c.stock),...ctx.geometry.G['P-C'].receiver_rectangles.map(r=>S.rect(...r,c.ink)),S.circle(-108,118,4,c.mid)],{'data-object':'P-C-carrier'}),
    S.path(ctx.geometry.G.A.path,c.mid,{'data-object':'A-arch'}),
    S.group(rail,{transform:'translate(0 116) scale(1.35)','data-object':'R-rail'}),
    S.group([S.path('M-22.44 0 A22.44 22.44 0 0 1 22.44 0 Z',c.amber),S.rect(-2.04,0,4.08,-58.16-g.signal.capY,c.ink)],{transform:`translate(0 ${g.signal.capY})`,'data-object':'L-signal'}),
    S.circle(...g.signal.mount,4.08,c.ink),S.line([g.bar.pivot,g.bar.endpoint],c.ink,7,{'data-object':'G-bar'}),S.circle(...g.bar.pivot,7,c.ink),
    S.line([g.bar.endpoint,[72,35],g.cam.center],c.ink,2,{'data-object':'G-linkage'}),
    S.circle(...g.cam.center,15,c.ink),S.circle(...g.cam.pin,4,c.amber),S.circle(...[830,239.26696362323386].map((v,i)=>(v-[750,386][i])/1.8),3,c.ink,{'data-object':'F-hang-pin'})
  ],{transform:S.matrixString(m.assembly.carrierMatrix),'data-object':'G-assembly'});
}
function drawFar(ctx) {const S=ctx.svg,C=ctx.geometry.PALETTE;return S.group([S.rect(82,97,1110,8,C.grey),...[140,430,832,1138].map(x=>S.rect(x,105,6,335,C.pale)),S.rect(980,200,185,7,C.ink),S.rect(1000,148,30,52,C.red),S.rect(1049,130,30,70,C.ink),S.rect(1098,159,30,41,C.amber),S.line([[185,105],[185,168]],C.ink,2),S.polygon([[161,168],[209,168],[198,185],[172,185]],C.mid)],{transform:'translate(20 16)','data-object':'W-far'});}
function drawReel(ctx,r) {const S=ctx.svg,C=ctx.geometry.PALETTE,M=ctx.math;return S.group([S.group([S.circle(0,0,77,C.ink),S.circle(0,0,68,C.stock),S.circle(0,0,48,C.paper),S.circle(0,0,36,C.pale),S.path('M-59-22A63 63 0 0 1 52-33 M-47 36A57 57 0 0 0 46 37','none',{stroke:C.mid,'stroke-width':2})],{transform:S.matrixString(M.rotate(r.angle)),'data-object':'Q-rotor'}),S.circle(0,0,12,C.ink),S.line([[-66,78],[66,78]],C.ink,7),S.polygon([[-119,47],[-139,80],[-106,80]],C.pale)],{transform:S.matrixString(M.poseMatrix(r.pose)),'data-object':'Q-reel-assembly'});}
function drawTransport(ctx,m){
  const S=ctx.svg,c=ctx.geometry.PALETTE,r={center:[1145,440],scale:.72,rotation:m.reel.angle,stop:{origin:[1059.32,473.84],points:[[1059.32,473.84],[1044.9199999999998,497.59999999999997],[1068.6799999999998,497.59999999999997]]}},guide=m.guide;
  const n=[S.group([S.line([[guide.x,guide.y],[guide.x+guide.width,guide.y]],c.mid,3),S.line([[guide.x,guide.y+13],[guide.x+guide.width,guide.y+13]],c.grey,3),...[.15,.4,.65,.9].map(u=>S.line([[guide.x+guide.width*u,guide.y+13],[guide.x+guide.width*u,guide.y+25],[guide.x+guide.width*u+13,guide.y+25]],c.mid,2))],{'data-object':'W-guide'})];
  for(const [x,y] of [[330,533.2],[1040,490]])n.push(S.group([S.rect(x-90,y,180,12,c.stock),S.line([[x-75,y+12],[x-50,y+100]],c.ink,8),S.line([[x+75,y+12],[x+50,y+100]],c.ink,8)],{transform:'translate(20 16)','data-object':x===330?'origin-support':'landing-support'}));
  const plate=m.landing.plate;
  n.push(S.polygon([plate[0],plate[1],[plate[1][0],518],[plate[0][0],518]],c.stock,{'data-object':'landing-pressure-plate'}));
  n.push(S.line(plate,c.mid,1.5),S.line([m.lever.plateJoint,m.lever.input,m.lever.pivot,m.lever.output],c.ink,3,{'data-object':'Q-release-lever'}),S.circle(...m.lever.pivot,4,c.ink));
  n.push(S.line([m.lever.output,r.stop.origin],c.ink,2,{'data-object':'Q-stop-link'}));
  n.push(S.group([S.circle(0,0,77,c.ink),S.circle(0,0,68,c.stock),S.circle(0,0,48,c.paper),S.circle(0,0,36,c.pale),
    S.group([S.path('M-59 -22 A63 63 0 0 1 52 -33 M-47 36 A57 57 0 0 0 46 37','none',{stroke:c.mid,'stroke-width':2})],{transform:`rotate(${r.rotation})`,'data-object':'Q-rotating-stock'}),S.circle(0,0,12,c.ink)
  ],{transform:S.matrixString(ctx.math.poseMatrix({x:r.center[0],y:r.center[1],scale:r.scale,angle:0})),'data-object':'Q-reel'}));
  n.push(S.line([[r.center[0]-66*r.scale,r.center[1]+78*r.scale],[r.center[0]+66*r.scale,r.center[1]+78*r.scale]],c.ink,7,{'data-object':'Q-shelf'}));
  n.push(S.polygon(r.stop.points,c.pale,{'data-object':'Q-stop'}));
  return n;
}
function feedPath(ctx,feed,inset=0) {const M=ctx.math,n=ctx.svg.number;const p=feed.payout,left=feed.left+inset,top=feed.top+inset,bottom=feed.bottom-inset,j=feed.joinX;
 // Source mouth and terminal tangent stay attached to the same fixed Q assembly.
 const a=[1117,373],b=[1138,423]; return `M${a[0]+20} ${a[1]+16}C${M.mix(1015,1000,p)+20} ${M.mix(376,376,p)+16} ${M.mix(1030,1008,p)+20} ${top-18+16} ${j+20} ${top+16}L${left+20} ${top+16}V${bottom+16}L${M.mix(934,907,p)+20} ${bottom+16}C${M.mix(1054,1050,p)+20} ${M.mix(531,540,p)+16} ${1081+20} ${421+16} ${b[0]+20} ${b[1]+16}Z`;}
function renderScene(ctx,{globalFrame:f,layerId}) {
 const S=ctx.svg,C=ctx.geometry.PALETTE,E=ctx.effectHelpers,M=ctx.math,m=ctx.state.mechanics,id=ctx.id,defs=[],world=[];
 if(layerId==='T02')world.push(drawFar(ctx));
 if(layerId==='T03')world.push(drawGate(ctx,m));
 if(layerId==='T04') {
  world.push(...drawTransport(ctx,m));
  const feed=E.deform(ctx.effects.path_deformation,m.feed,{...m.feed,payout:0,left:535,top:498,bottom:548,joinX:930});
  world.push(S.path(feedPath(ctx,feed),C.pale,{'data-object':'Q-feed'}));
  for(let i=0;i<3;i++){const x=feed.left+90+i*130;if(x<927)world.push(S.line([[x+20,517],[x+28,522],[x+25,557]],C.mid,1.2,{'data-object':`Q-feed-crease-${i}`}));}
  if(m.feed.payout>0)world.push(S.path(feedPath(ctx,feed,10),C.paper,{opacity:m.feed.payout,'data-object':'Q-feed-inset'}));
  const t=m.tongues,maskId=id('buckle-cover');
  defs.push(E.structuralMask(maskId,[700,400,240,160],[S.polygon(t.coverPoints,'black')]));
  // New details are present as fold reserves, uncovered by physical payout, not fading props.
  const clip=id('feed-detail-clip');defs.push(S.node('clipPath',{id:clip,clipPathUnits:'userSpaceOnUse'},[S.rect(M.mix(1138,90,M.smoothstep(M.progress(f,1680,1768)))+20,350,1200,240,'white')]));
  const buckle= S.group([S.line([plus(m.paper.anchor,[-101,33]),plus(m.paper.anchor,[-54,33]),plus(m.paper.anchor,[-35,0]),m.paper.anchor],C.ink,5),S.circle(...plus(m.paper.anchor,[-54,33]),6,C.ink),S.circle(...m.paper.anchor,5,C.ink),S.circle(...m.paper.anchor,2,C.red)],{mask:`url(#${maskId})`,'data-object':'Q-P-buckle'});
  world.push(S.group([S.polygon([wp([280,490]),wp([280,470+18*t.tap]),wp([312,451+32*t.tap]),t.firstTip,wp([334,490])],C.stock,{'data-object':'first-tongue'}),S.line(t.firstHinge,C.mid,2),buckle,S.polygon(t.coverPoints,C.pale,{'data-object':'second-cover',stroke:C.mid,'stroke-width':1.5,'stroke-opacity':1-t.cover}),S.line(t.secondHinge,C.mid,2)],{'clip-path':`url(#${clip})`}));
  if(f>=1950)world.push(S.circle(...m.paper.anchor,5+2*(1-m.frame.seat),C.ink,{'data-object':'seating-response'}),S.circle(...m.paper.anchor,2,C.red));
 }
 if(layerId==='T06') {
  world.push(S.group([S.polygon([[-110,124],[140,124],[132,132],[-102,132]],C.pale,{'data-object':'P-A-folded'}),S.polygon([[-102,132],[132,132],[124,140],[-94,140]],C.pale,{'data-object':'P-B-folded'}),S.line([[-110,124],[140,124]],C.mid,1,{'data-object':'P-A-hinge'}),S.line([[-102,132],[132,132]],C.mid,1,{'data-object':'P-B-hinge'})],{transform:S.matrixString(m.assembly.carrierMatrix),'data-owner':'T06'}));
  const p=m.paper,pa=E.deform(ctx.effects.path_deformation,p.pa,[p.paBase[0],p.paBase[1],plus(p.paBase[1],[8,0]),plus(p.paBase[0],[8,0])]);
  world.push(S.polygon(m.connector,C.pale,{'data-object':'P-C-to-P-A-connector'}),S.line([m.carrierEdge[0],p.paBase[0]],C.mid,2),S.line([m.carrierEdge[1],p.paBase[1]],C.mid,2),S.polygon(pa,C.pale,{'data-object':'P-A'}),S.polygon(p.pb,C.stock,{'data-object':'P-B'}),S.line([...pa,pa[0]],C.mid,2),S.line(p.sharedEdge,C.mid,2));
  const seam=id('paper-seam'),edge=p.sharedEdge;defs.push(E.seamGradient({id:seam,bounds:[edge[0][0],edge[0][1],130,13],enabled:ctx.effects.gradient_alpha_reveal}));
  world.push(S.polygon([edge[0],edge[1],plus(edge[1],[9,11]),plus(edge[0],[9,11])],`url(#${seam})`,{'data-object':'P-A-P-B-seam',opacity:M.smoothstep(M.progress(f,1680,1710))}));
  world.push(S.group([S.polygon([p.tabRoot,p.tabReserve,p.anchor,plus(p.anchor,[5,3]),plus(p.tabReserve,[5,3]),plus(p.tabRoot,[5,3])],C.pale,{'data-object':'P-A-buckle-tab'}),S.line([p.tabRoot,p.tabReserve,p.anchor],C.mid,1.5)],{opacity:M.smoothstep(M.progress(f,1680,1710))}));
 }
 if(layerId==='T05') {
  world.push(foldedFrame(ctx,m.frame));
  if(m.frame.feedAnchor)world.push(S.line([[m.frame.pose.x,m.frame.pose.y],m.frame.feedAnchor],C.red,3,{'data-object':'F-feed-attachment'}));
  // The grasp tongue cannot precede physical pickup; no long line appears at approach.
  if(f>=PICKUP_FRAME)world.push(S.line([m.frame.grasp,m.frame.beak],C.red,5,{'data-object':'beak-F-grasp'}));
  if(m.frame.linked) {const q=ctx.geometry.forceChainShapes(m.frame.chain);world.push(S.group([q.tongue],{'data-object':'F-to-buckle-tongue'}));}
 }
 // T13 uses only the bound native PCM; the user's audio audition remains separate.
 if(layerId==='T13' && ctx.assets.waveform) {
  const points=ctx.geometry.waveformPoints(ctx.assets.waveform,{startFrame:1680,endFrame:2160,x:110,y:500,width:807,height:28});
  const feed=E.deform(ctx.effects.path_deformation,m.feed,{...m.feed,payout:0,left:535,top:498,bottom:548,joinX:930});
  const reveal=id('real-pcm-reveal'),paperClip=id('real-pcm-feed'),covered=id('real-pcm-covered');
  const entering=M.smoothstep(M.progress(f,1710,1770)),leaving=M.smoothstep(M.progress(f,2100,2159));
  // The trailing edge withdraws the unchanged signal before the T13 clip ends.
  // Both edges live in the same paper-local coordinates as the original PCM points.
  const start=110+807*leaving,end=110+807*entering;
  defs.push(S.node('clipPath',{id:reveal,clipPathUnits:'userSpaceOnUse'},[S.rect(start,460,Math.max(0,end-start),80,'white')]),
   S.node('clipPath',{id:paperClip,clipPathUnits:'userSpaceOnUse'},[S.path(feedPath(ctx,feed),'white')]));
  const p=m.paper,pa=E.deform(ctx.effects.path_deformation,p.pa,[p.paBase[0],p.paBase[1],plus(p.paBase[1],[8,0]),plus(p.paBase[0],[8,0])]);
  const bird=ctx.state.bird,t=m.tongues;
  // T13 paints above these pages, so restore their actual material silhouettes here.
  defs.push(E.structuralMask(covered,[0,350,1280,260],[S.polygon(pa,'black'),S.polygon(p.pb,'black'),S.polygon(m.connector,'black'),
   S.line([...pa,pa[0]],'black',2),S.line(p.sharedEdge,'black',2),S.line([m.carrierEdge[0],p.paBase[0]],'black',2),S.line([m.carrierEdge[1],p.paBase[1]],'black',2),
   S.polygon([p.tabRoot,p.tabReserve,p.anchor,plus(p.anchor,[5,3]),plus(p.tabReserve,[5,3]),plus(p.tabRoot,[5,3])],'black'),S.line([p.tabRoot,p.tabReserve,p.anchor],'black',1.5),
   S.polygon([wp([280,490]),wp([280,470+18*t.tap]),wp([312,451+32*t.tap]),t.firstTip,wp([334,490])],'black'),S.polygon(t.coverPoints,'black'),
   S.group([...ctx.geometry.birdLocal('body',bird),...ctx.geometry.birdLocal('wings',bird)],{transform:S.matrixString(M.poseMatrix(bird.pose))}),foldedFrame(ctx,m.frame)]));
  // Follow the feed's leading edge/top during payout, with no amplitude remapping.
  const onPaper=S.group([S.line(points,C.ink,1.4,{'data-object':'actual-pcm-envelope'})],{transform:S.matrixString(M.translate(feed.left-90,feed.top-447)),'clip-path':`url(#${reveal})`,'data-object':'paper-local-pcm'});
  world.push(S.group([onPaper],{'clip-path':`url(#${paperClip})`,mask:`url(#${covered})`,'data-object':'Q-feed-pcm'}));
 }
 return M.deepFreeze({defs,world,screen:[]});
}
module.exports=Object.freeze({sceneId,implemented,resolveSceneState,renderScene,WAVEFORM_DEPENDENCY,drawGate,drawFar,drawReel,foldedFrame,feedPath,drawTransport});
