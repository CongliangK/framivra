'use strict';
// S2 / R09 + bounded R20 placement staging revision. All state is solved from the global integer frame; no page owns a clock.
// Geometry follows approved R03, with the final tab attached to the actual rotated beak.
const sceneId='S2';
const implemented=true;
const FINAL_PATHS={
 A:{start:[145,460],curves:[[[310,465],[362,342],[548,370]],[[734,398],[766,399],[902,280]]],edge:[913,326],back:[[[773,445],[666,448],[538,416]],[[410,384],[306,509],[150,508]]]},
 L:{start:[160,534],curves:[[[310,529],[490,326],[622,366]],[[754,406],[801,406],[938,338]]],edge:[948,383],back:[[[807,452],[744,430],[615,411]],[[486,392],[321,575],[164,580]]]},
 R:{start:[188,602],curves:[[[376,602],[452,509],[591,446]],[[730,383],[758,322],[935,419]]],edge:[950,465],back:[[[773,368],[715,506],[585,492]],[[455,478],[371,648],[193,649]]]}
};
const NAMES=['A','L','R'];
const INITIAL={A:{x:215,y:445,glyph:[290,420,.4]},L:{x:325,y:515,glyph:[400,503,.55]},R:{x:365,y:585,glyph:[435,605,.55]}};
const FINAL_GLYPHS={A:[270,438,.4],L:[355,500,.55],R:[395,583,.55]};
const TOP_OFFSETS={A:[-90,35],L:[-55,22],R:[-30,12]};
function offsetPath(p,v){const q=a=>[a[0]+v[0],a[1]+v[1]];return {start:q(p.start),curves:p.curves.map(c=>c.map(q)),edge:q(p.edge),back:p.back.map(c=>c.map(q))};}
function ramp(M,f,a,b){return M.smoothstep(M.progress(f,a,b));}
function lerpPoint(M,a,b,u){return [M.mix(a[0],b[0],u),M.mix(a[1],b[1],u)];}
// Fixed two-link manipulator. The bend branch reproduces the neutral elbow
// exactly; no changing bone length is used to maintain a distant contact.
const ARM_LENGTHS=[Math.hypot(42,16),Math.hypot(42,12)];
function solveArm(shoulder,hand){
 const [l1,l2]=ARM_LENGTHS,dx=hand[0]-shoulder[0],dy=hand[1]-shoulder[1],d=Math.hypot(dx,dy);
 if(d>l1+l2+1e-8||d<Math.abs(l1-l2)-1e-8)throw new RangeError('S2 placement is outside fixed arm reach');
 const a=(l1*l1-l2*l2+d*d)/(2*d),h=Math.sqrt(Math.max(0,l1*l1-a*a));
 return {shoulder,elbow:[shoulder[0]+a*dx/d+h*dy/d,shoulder[1]+a*dy/d-h*dx/d],hand};
}
function pathData(S,p){return `M${S.points([p.start])} C${S.points(p.curves[0])} C${S.points(p.curves[1])} L${S.points([p.edge])} C${S.points(p.back[0])} C${S.points(p.back[1])} Z`;}
function solveStrip(M,name,u){
 const q=FINAL_PATHS[name],a=INITIAL[name];
 const start=[a.x,a.y],end=[a.x+170,a.y-9],bottomEnd=[a.x+174,a.y+37],bottomStart=[a.x+4,a.y+46];
 const short={start,curves:[[[a.x+39,a.y+2],[a.x+63,a.y-4],[a.x+94,a.y-3]],[[a.x+126,a.y-2],[a.x+148,a.y-7],end]],edge:bottomEnd,back:[[[a.x+144,a.y+42],[a.x+121,a.y+45],[a.x+94,a.y+43]],[[a.x+62,a.y+43],[a.x+38,a.y+48],bottomStart]]};
 return {start:lerpPoint(M,short.start,q.start,u),curves:q.curves.map((c,i)=>c.map((p,j)=>lerpPoint(M,short.curves[i][j],p,u))),edge:lerpPoint(M,short.edge,q.edge,u),back:q.back.map((c,i)=>c.map((p,j)=>lerpPoint(M,short.back[i][j],p,u)))};
}
function glyph(ctx,name,pose,attrs={}){
 const {svg:S,geometry:G}=ctx,c=G.PALETTE;
 let nodes;
 if(name==='A')nodes=[S.path(G.G.A.path,c.mid)];
 else if(name==='L')nodes=[S.path('M-33 0A33 33 0 0 1 33 0Z',c.amber),S.rect(-3,0,6,88,c.ink),S.circle(0,87,6,c.ink)];
 else nodes=[S.line([[-80,0],[80,0]],c.ink,5),S.line([[-80,18],[80,18]],c.ink,5),...[-55,0,55].map(x=>S.line([[x,-3],[x,21]],c.ink,4))];
 return S.group(nodes,{transform:S.matrixString(ctx.math.poseMatrix({x:pose[0],y:pose[1],scale:pose[2],angle:pose[3]||0})),'data-material-glyph':name,...attrs});
}
function foldedFrame(ctx,state){
 const {svg:S,math:M,geometry:G}=ctx,c=G.PALETTE;
 const nodes=[S.line([[0,36],[9,40],[13,26],[22,30],[26,16],[35,20],[39,6]],c.red,3,{'data-folded-reserve':'F'})];
 for(let i=3;i>=0;i--)nodes.push(S.group([S.polygon(G.G.F.corner_polygon,c.red),S.polygon([[20,16],[30,16],[20,26]],c.paper)],{transform:S.matrixString(M.poseMatrix({x:i*13,y:-i*10,scale:.5,angle:0})),'data-frame-corner':String(i)}));
 return S.group(nodes,{transform:S.matrixString(M.poseMatrix(state.pose)),'data-object':'F-folded'});
}
function sideCard(ctx,name,state){
 const {svg:S,math:M,geometry:G}=ctx,c=G.PALETTE;
 if(name==='A')return S.group([
  S.polygon([[445,315],[628.5,315],[660,445],[445,475]],c.pale,{'data-card-face':'A'}),
  glyph(ctx,'A',[540,389,.58]),
  S.line([[445,315],[445,475]],c.mid,1.5,{'data-hinge':'A-left'})
 ],{transform:S.matrixString(state.matrix),'data-card':'A'});
 return S.group([S.rect(-85,-95,170,160,c.pale),S.rect(-77,-87,154,144,c.paper),glyph(ctx,name,[0,-9,.8]),S.line([[-65,50],[65,50]],c.mid,1.5)],{transform:S.matrixString(state.matrix),'data-card':name});
}
function resolveSceneState(ctx,{globalFrame:f,localSceneFrame}){
 const {math:M,svg:S,geometry:G}=ctx,c=G.PALETTE;
 if(!Number.isInteger(f)||f<300||f>=720||localSceneFrame!==f-300)throw new RangeError('S2 requires global [300,720) and its exact local frame');
 const opening=ctx.opening.openingState(299),cameraU=ramp(M,f,301,319),coverEnd=345;
 const cameraMatrix=opening.cameraMatrix.map((v,i)=>M.mix(v,M.IDENTITY[i],cameraU));
 let bird;
 if(f<coverEnd){
  const z=M.keys(f,[[300,35],[316,32],[326,27],[345,1.1]]),u=ramp(M,f,326,345);
  const p={x:M.mix(640+41*z,885,u),y:M.mix(360+28*z,260,u),scale:z,angle:0};
  bird={space:'screen',pose:p,wing:0,stage:4,feet:false,occluders:[]};
 }else{
  const pose={x:M.keys(f,[[345,885],[365,765],[390,790],[420,790],[450,748],[480,620],[525,460],[555,580],[600,920],[629,960],[630,960],[719,1032]]),y:M.keys(f,[[345,260],[365,465],[390,454],[420,472.2],[450,472.2],[480,480],[525,570],[555,502],[600,365],[629,395],[630,395],[719,306]]),scale:M.keys(f,[[345,1.1],[365,1.5],[390,1.25],[420,1.1],[480,.92],[525,.82],[600,.8],[719,.8]]),angle:M.keys(f,[[345,0],[365,0],[390,-4],[420,0],[450,0],[480,-7],[525,-12],[555,-10],[600,-2],[719,-2]])};
  bird={space:'world',maskBounds:[-256,-256,2048,1536],pose,stage:4,wing:M.keys(f,[[345,-5],[365,0],[378,-12],[420,4],[450,4],[480,-16],[525,10],[555,-15],[600,0],[719,0]]),feet:f>=408&&f<456,occluders:[]};
 }
 if(f===300)bird=opening.bird;
 // A alone rotates about its LEFT physical edge. The same projection applies to
 // every glyph and face point, avoiding independently animated printed symbols.
 const fold=180*ramp(M,f,365,405),r=fold*Math.PI/180,dx=M.keys(f,[[300,0],[435,0],[450,29.75],[465,79.75],[480,139.75]]);
 const aProjection=[Math.cos(r),-.22*Math.sin(r),0,1,445*(1-Math.cos(r))+dx,445*.22*Math.sin(r)];
 const aLeft=M.transform([660,445],aProjection),aPress=M.transform([445,430],aProjection);
 const lx=M.keys(f,[[300,564],[450,564],[465,614],[480,674]]),rx=M.keys(f,[[300,602],[465,602],[480,662]]);
 const cards={
  A:{matrix:aProjection,hinge:[[445+dx,315],[445+dx,475]],angle:fold},
  L:{matrix:M.poseMatrix({x:lx,y:409,scale:1.05,angle:M.keys(f,[[300,-4],[420,-4],[435,0]])})},
  R:{matrix:M.poseMatrix({x:rx,y:421,scale:1.05,angle:M.keys(f,[[300,-7],[420,-7],[435,0]])})}
 };
 // The same near A card passes the lens; no replacement matte. The projection
 // changes while that real card fills the view, then reveals a top-down plane.
 const foreground=f>=480&&f<525;
 const fullCover=[-13,0,0,13,7660,-4775];
 const approach=ramp(M,f,480,491);
 const coverMatrix=aProjection.map((v,i)=>M.mix(v,fullCover[i],approach));
 const edgePath=points=>{
  const [a,b,c,d]=points,ab=lerpPoint(M,a,b,.5),cd=lerpPoint(M,c,d,.5);
  const seg=(p,q)=>[lerpPoint(M,p,q,1/3),lerpPoint(M,p,q,2/3),q];
  return {start:a,curves:[seg(a,ab),seg(ab,b)],edge:c,back:[seg(c,cd),seg(cd,d)]};
 };
 const coverBase=edgePath([[445,315],[628.5,315],[660,445],[445,475]].map(p=>M.transform(p,coverMatrix)));
 const target=offsetPath(solveStrip(M,'A',0),TOP_OFFSETS.A),uncoverU=M.progress(f,505,525),uncover=uncoverU*uncoverU*uncoverU*(uncoverU*(uncoverU*6-15)+10);
 const nearGlyph=[INITIAL.A.glyph[0]+TOP_OFFSETS.A[0],INITIAL.A.glyph[1]+TOP_OFFSETS.A[1],.4];
 const nearCard={path:{start:lerpPoint(M,coverBase.start,target.start,uncover),curves:coverBase.curves.map((p,i)=>p.map((v,j)=>lerpPoint(M,v,target.curves[i][j],uncover))),edge:lerpPoint(M,coverBase.edge,target.edge,uncover),back:coverBase.back.map((p,i)=>p.map((v,j)=>lerpPoint(M,v,target.back[i][j],uncover)))},glyph:[...lerpPoint(M,M.transform([540,389],coverMatrix),nearGlyph,uncover),M.mix(Math.abs(coverMatrix[0])*.58,.4,uncover)]};
 const top=f>=500;
 const pull=ramp(M,f,630,719),deform=ctx.effects.path_deformation?pull:0;
 const strips=Object.fromEntries(NAMES.map((name,index)=>{
  const travel=ramp(M,f,525+index*10,570+index*15),offset=TOP_OFFSETS[name].map(v=>v*(1-travel));
  return [name,{path:offsetPath(solveStrip(M,name,deform),offset),glyph:INITIAL[name].glyph.map((v,i)=>M.mix(v,FINAL_GLYPHS[name][i],pull)+(i<2?offset[i]:0))}];
 }));
 const beak=M.transform([140,-33],M.poseMatrix(bird.pose));
 // The endpoint uses the true rotated beak. R03's rounded comment [1144,280]
 // was not the geometry result of its own -2 degree pose.
 const tabRoot=lerpPoint(M,[938,398],[1042,281],pull);
 const framePose={x:M.keys(f,[[300,1080],[480,1080],[499,1080],[500,1080],[600,1080],[620,931],[629,931],[630,931],[719,1008]]),y:M.keys(f,[[300,512],[480,512],[499,512],[500,562],[600,562],[610,510],[620,429],[629,429],[630,429],[719,322]]),scale:M.keys(f,[[300,.6],[629,.65],[719,.65]]),angle:0};
 const planU=ramp(M,f,500,525),exit=ramp(M,f,630,690);
 let opx=M.keys(f,[[300,1080],[390,1080],[435,180],[480,300],[499,300],[500,1155],[600,1155],[629,1335]]);
 let opy=top?560:430,arm;
 // The operator follows the carried reserve, places it at620, releases, then
 // withdraws behind the actual tray backstop before the fixed T09 out630.
 if(f>=585&&f<600)opx=M.mix(1155,1140,ramp(M,f,585,600));
 if(f>=600&&f<=620){opx=framePose.x+60;opy=M.mix(560,500,ramp(M,f,600,620));}
 if(f>620&&f<630){opx=M.mix(991,1065,ramp(M,f,620,629));opy=500;}
 let hand=[opx+67,opy-61];
 if(f>=420&&f<480)hand=lerpPoint(M,[247,369],aLeft,ramp(M,f,420,435));
 if(f>=585&&f<=600)hand=lerpPoint(M,[opx+67,opy-61],[framePose.x,framePose.y+20*framePose.scale],ramp(M,f,585,600));
 if(f>600&&f<=620)hand=[framePose.x,framePose.y+20*framePose.scale];
 if(f>=585&&f<=620)arm=solveArm([opx-17,opy-57],hand);
 if(f>620&&f<630){
  const contactScale=M.keys(620,[[300,.6],[629,.65],[719,.65]]);
  hand=[opx-60,429+20*contactScale];
  arm=solveArm([opx-17,opy-57],hand);
 }
 // A hinged backstop is attached to the existing F tray. It rises only once
 // the reserve has cleared its left edge, and exits with that same tray.
 const backstop=f>612&&f<690?{x:1000+360*exit,y:585-210*ramp(M,f,612,620),width:155,height:210*ramp(M,f,612,620)}:null;
 // The low guide is the same workshop rail. Side structures leave by movement,
 // not opacity. The fixed-guide endpoints arrive at the approved f719 target.
 const guide={x:M.mix(100,120,planU),y:M.mix(525,666,planU),width:M.mix(1070,1080,planU),top:planU};
 const supportReveal=ramp(M,f,690,719);
 const carrier={matrix:[1.1,0,0,1.1,932,261+(1-supportReveal)*-520],root:[932,261,1.1],rect:G.G['P-C'].local_carrier_rect,receivers:G.G['P-C'].receiver_rectangles,peg:[1042,281]};
 const foldPlanes={hinge:[[-110,124],[140,124]],a:[[-110,124],[140,124],[132,132],[-102,132]],b:[[-102,132],[132,132],[124,140],[-94,140]],rearHinge:[[-102,132],[132,132]]};
 // Whole-body/whole-wing visibility consumes this one resolved geometry. The
 // foreground card is ABOVE both B pages; use identical occluders in each.
 if(foreground){
  const cardFace=S.path(pathData(S,nearCard.path),'black');
  bird={...bird,occluders:[cardFace]};
 }
 return M.deepFreeze({cameraMatrix,bird,mechanics:{cards,foldAngle:fold,wingCardContact:[628.5,315],actualWingTip:M.transform(M.transform([-91,-100],M.rotate(bird.wing,[-15,-6])),M.poseMatrix(bird.pose)),operator:{x:opx,y:opy,hand,...(arm?{arm}:{})},...(backstop?{backstop}:{}),push:{handContact:aLeft,aToL:aPress,lToR:[600,462],lStarted:f>450,rStarted:f>465},top,foreground,coverMatrix,nearCard,strips,pull,beak,tabRoot,grasp:f>=630,frame:{pose:framePose,cornerIds:[0,1,2,3],inTray:f<600},guide,carrier,foldPlanes,planU,exit,supportReveal},handoff:{cardOwner:f<630?'T10':'T11',ribbonOrder:['A','L','R'],singleGrasp:f>=630}});
}
function farWorkshop(ctx,m){
 const {svg:S,math:M,geometry:G}=ctx,c=G.PALETTE;
 // It physically passes upward during the overhead reveal and leaves its window
 // before T02 ends. No opaque ground and no crossfade to a new title card.
 const y=-850*m.planU;
 return S.group([S.rect(82,132,1110,8,c.grey),...[140,430,832,1138].map(x=>S.rect(x,140,6,335,c.pale)),S.rect(980,235,185,7,c.ink),S.rect(1000,183,30,52,c.red),S.rect(1049,165,30,70,c.ink),S.rect(1098,194,30,41,c.amber),S.line([[185,140],[185,203]],c.ink,2),S.polygon([[161,203],[209,203],[198,220],[172,220]],c.mid)],{transform:S.matrixString(M.translate(0,y)),'data-object':'far-workshop'});
}
function transport(ctx,m){
 const {svg:S,math:M,geometry:G}=ctx,c=G.PALETTE,g=m.guide,u=g.top,parts=[];
 const sideY=(1-u)*0+u*440;
 parts.push(S.group([S.rect(100,478,1070,47,c.pale),S.rect(100,485,1070,34,c.paper),S.rect(175,580,10,90,c.ink),S.rect(1070,580,10,90,c.ink),...Array.from({length:11},(_,i)=>{let x=125+i*100;return S.group([S.circle(x,562,15,c.ink),S.circle(x,562,5,c.mid),S.line([[x-10,559],[x+10,565]],c.paper,2)]);})],{transform:S.matrixString(M.translate(0,sideY)),'data-object':'conveyor-side'}));
 parts.push(S.line([[g.x,g.y],[g.x+g.width,g.y]],u===1?c.mid:c.ink,M.mix(5,3,u)),S.line([[g.x,g.y+M.mix(17,13,u)],[g.x+g.width,g.y+M.mix(17,13,u)]],u===1?c.grey:c.mid,3));
 for(const t of [.15,.4,.65,.9])parts.push(S.line([[g.x+g.width*t,g.y+13],[g.x+g.width*t,g.y+25],[g.x+g.width*t+13,g.y+25]],c.mid,2));
 if(m.top){
  parts.push(S.rect(171,160,9,400,c.pale,{'data-object':'fixed-upright'}));
  const slotShift=-1200*m.exit;
  for(let i=0;i<3;i++){let x=170+i*62+slotShift,y=490+i*66;
   parts.push(S.line([[x-45,y+17],[x+245,y-31]],c.grey,1.5,{'data-paper-slot':NAMES[i]}),S.line([[x-43,y+24],[x+247,y-24]],c.grey,1.5));
  }
 }
 const trayY=M.mix(535,585,m.planU),trayX=1000+360*m.exit;
 if(m.backstop){const b=m.backstop;parts.push(S.rect(b.x,b.y,b.width,b.height,c.pale,{'data-object':'F-tray-backstop'}),S.line([[b.x,b.y],[b.x+b.width,b.y],[b.x+b.width,trayY]],c.grey,1.5),S.line([[b.x,trayY],[b.x+b.width,trayY]],c.mid,2,{'data-hinge':'tray-backstop'}));}
 parts.push(S.rect(trayX,trayY,155,9,c.mid,{'data-object':'F-reserve-tray'}),S.line([[trayX, trayY],[trayX,trayY-15],[trayX+155,trayY-15],[trayX+155,trayY]],c.grey,2));
 return S.group(parts,{'data-object':'W-guide-and-transport'});
}
function operator(ctx,m){
 const {svg:S,math:M,geometry:G}=ctx,c=G.PALETTE,o=m.operator;
 const shoulders=o.arm?o.arm.shoulder:[o.x-17,o.y-57],elbow=o.arm?o.arm.elbow:[M.mix(shoulders[0],o.hand[0],.5),Math.min(shoulders[1],o.hand[1])-12];
 return S.group([S.circle(o.x,o.y-94.5,18.9,c.ink),S.polygon([[o.x-18.9,o.y-69.3],[o.x+18.9,o.y-69.3],[o.x+30.6,o.y-3.6],[o.x-27.9,o.y-3.6]],c.mid),S.line([[o.x-17,o.y-2.7],[o.x-26.1,o.y+54.9],[o.x-42.3,o.y+54.9]],c.ink,8.1),S.line([[o.x+19.8,o.y-2.7],[o.x+27.9,o.y+54.9],[o.x+44.1,o.y+54.9]],c.ink,8.1),S.line([shoulders,elbow,o.hand],c.ink,6.3),S.circle(...o.hand,3.5,c.ink,{'data-contact':'operator-hand'}),S.line([[o.x+19,o.y-57],[o.x+45,o.y-31],[o.x+67,o.y-61]],c.ink,6.3)],{'data-object':'operator'});
}
function ribbonShapes(ctx,m,{omitA=false}={}){
 const {svg:S,math:M,geometry:G,effectHelpers:E,effects}=ctx,c=G.PALETTE,defs=[],nodes=[];
 const paths=Object.fromEntries(NAMES.map(n=>[n,pathData(S,m.strips[n].path)]));
 const u=m.pull;
 // These bounded underpasses become active as the actual strip paths cross.
 const regionA=S.path('M546 365Q590 355 640 371L640 419Q590 401 546 414Z','black');
 const regionR=S.path('M675 392Q710 382 745 385L745 428Q710 432 675 438Z','black');
 const aMask=ctx.id('A-under-L'),rMask=ctx.id('R-under-L'),rClip=ctx.id('R-ink-clip'),rOver=ctx.id('R-over-L');
 const weave=M.smoothstep(M.clamp01((u-.45)/.55)),maskActive=weave>0;
 const maskA=S.group([regionA],{transform:S.matrixString([weave,0,0,1,590*(1-weave),0])}),maskR=S.group([regionR],{transform:S.matrixString([weave,0,0,1,710*(1-weave),0])});
 defs.push(E.mask(aMask,[-20,-20,1320,760],maskActive?[maskA]:[],effects.material_masks),E.mask(rMask,[-20,-20,1320,760],maskActive?[maskR]:[],effects.material_masks),S.node('clipPath',{id:rClip,clipPathUnits:'userSpaceOnUse'},[S.path(paths.R,'white')]),S.node('clipPath',{id:rOver,clipPathUnits:'userSpaceOnUse'},[S.rect(802.5-37.5*weave,340,75*weave,160,'white')]));
 nodes.push(S.path(paths.R,c.stock,{'data-strip':'R'}),S.path(paths.L,c.paper,{stroke:c.mid,'stroke-width':1.5,'data-strip':'L'}));
 if(!omitA)nodes.push(S.path(paths.A,c.pale,{mask:`url(#${aMask})`,'data-strip':'A'}));
 if(maskActive)nodes.push(S.path(paths.R,c.stock,{'clip-path':`url(#${rOver})`,'data-overpass':'R'}));
 // R's two printed rails follow its carrier, but its rigid identity glyph does
 // not stretch. Both curved lines use exactly the carrier's solved projection.
 const r=m.strips.R.path;
 const ink=[13,28].map(offset=>{
  // Apply the same small printed-rail offsets to the current solved carrier.
  // Interpolating toward the final absolute path a second time would pull ink
  // ahead of its paper during the extension and is intentionally avoided.
  const p={start:[r.start[0]+2,r.start[1]+offset],curves:[
   r.curves[0].map((v,i)=>[v[0]+[1,3,0][i],v[1]+offset+[0,1,1][i]]),
   r.curves[1].map((v,i)=>[v[0]+[-3,5,8][i],v[1]+offset+1])
  ]};
  return S.path(`M${S.points([p.start])} C${S.points(p.curves[0])} C${S.points(p.curves[1])}`,'none',{stroke:c.ink,'stroke-width':3});
 });
 nodes.push(S.group(ink,{'clip-path':`url(#${rClip})`,mask:`url(#${rMask})`,'data-object':'R-printed-rails'}));
 for(const name of NAMES){
  if(omitA&&name==='A')continue;
  const p=m.strips[name].path.start,q=lerpPoint(M,[p[0]+46,p[1]+4],{A:[191,464],L:[206,540],R:[239,608]}[name],u);
  nodes.push(S.line([q,[q[0]+21,q[1]+20],[q[0]+42,q[1]],[q[0]+64,q[1]+20]],c.mid,1.5,{'data-fold-reserve':name}));
  nodes.push(glyph(ctx,name,m.strips[name].glyph));
 }
 if(maskActive){
  const id1=ctx.id('seam-A'),id2=ctx.id('seam-R');
  defs.push(E.seamGradient({id:id1,bounds:[515,413,105,55],enabled:effects.gradient_alpha_reveal}),E.seamGradient({id:id2,bounds:[620,462,130,48],enabled:effects.gradient_alpha_reveal}));
  nodes.push(S.path('M515 413C561 415 588 446 617 449L618 468C582 463 556 436 515 433Z',`url(#${id1})`,{opacity:weave}),S.path('M620 487C672 481 711 477 741 462L747 481C709 496 676 503 622 506Z',`url(#${id2})`,{opacity:weave}));
 }
 if(m.top){
  const tabEnd=m.grasp?m.beak:[1071.0104259131926,364.70733853601575];
  for(const name of ['A','R','L']){if(omitA&&name==='A')continue;
   const e=m.strips[name].path.edge;
   nodes.push(S.line([[e[0]-11,e[1]-23],m.tabRoot],c.stock,name==='L'?4:3,{'data-object':`${name}-paper-tongue`}));
  }
  nodes.push(S.line([m.tabRoot,tabEnd],c.stock,4,{'data-contact':'single-beak-grasp','data-grasped':m.grasp?'true':'false'}));
 }
 return {defs,nodes};
}
function foregroundBirdMask(ctx,nodes,defs){
 const {svg:S,math:M,geometry:G,effectHelpers:E,state}=ctx;
 const id=ctx.id('foreground-B-knockout');
 const b=state.bird,shape=S.group([...G.birdLocal('wings',b),...G.birdLocal('body',b)].map(G.silhouette),{transform:S.matrixString(M.poseMatrix(b.pose))});
 defs.push(E.structuralMask(id,b.maskBounds||[-4096,-4096,8192,8192],[shape]));
 return [S.group(nodes,{mask:`url(#${id})`})];
}
function renderScene(ctx,{globalFrame:f,localSceneFrame,layerId}){
 const {svg:S,math:M,geometry:G,state}=ctx,c=G.PALETTE,m=state.mechanics,defs=[],world=[],screen=[];
 if(layerId==='T02'&&f<690)world.push(farWorkshop(ctx,m));
 if(layerId==='T04')world.push(transport(ctx,m));
 if(layerId==='T05'&&f>=330){
  if(f>=620){const p=m.frame.pose;world.push(S.line([m.tabRoot,[p.x,p.y+36*p.scale]],c.stock,3,{'data-object':'F-paper-tongue'}));}
  world.push(foldedFrame(ctx,m.frame));
 }
 if(layerId==='T03'&&f>=690){
  const q=m.carrier;
  world.push(S.group([S.rect(...q.rect,c.stock,{'data-object':'P-C'}),...q.receivers.map((r,i)=>S.rect(...r,c.ink,{'data-receiver':String(i)})),S.circle(-108,118,4,c.mid,{'data-object':'P-C-bearing'})],{transform:S.matrixString(q.matrix),'data-assembly':'P-C-receivers'}));
 }
 if(layerId==='T06'&&f>=690){
  world.push(S.group([S.polygon(m.foldPlanes.a,c.pale,{'data-object':'P-A'}),S.polygon(m.foldPlanes.b,c.pale,{'data-object':'P-B'}),S.line(m.foldPlanes.hinge,c.mid,1,{'data-hinge':'P-C-P-A'}),S.line(m.foldPlanes.rearHinge,c.mid,1,{'data-hinge':'P-A-P-B'})],{transform:S.matrixString(m.carrier.matrix)}));
 }
 if(layerId==='T09'&&f<630){
  let actor=operator(ctx,m);
  if(m.backstop){
   const b=m.backstop,id=ctx.id('operator-behind-tray');
   // Structural mask is the exact filled tray plane, always opaque black.
   // Optional material-mask diagnostics must not reveal a body through a wall.
   defs.push(ctx.effectHelpers.structuralMask(id,[-256,-256,2048,1536],[S.rect(b.x,b.y,b.width,b.height,'black')]));
   actor=S.group([actor],{mask:`url(#${id})`,'data-occlusion':'F-tray-backstop'});
  }
  world.push(...foregroundBirdMask(ctx,[actor],defs));
 }
 if(layerId==='T10'&&f>=330&&f<630){
  if(!m.top){
   let cardNodes=[sideCard(ctx,'R',m.cards.R),sideCard(ctx,'L',m.cards.L)];
   if(!m.foreground)cardNodes.push(sideCard(ctx,'A',m.cards.A));
   world.push(...foregroundBirdMask(ctx,cardNodes,defs));
  }else{const ribbon=ribbonShapes(ctx,m,{omitA:m.foreground});defs.push(...ribbon.defs);world.push(...foregroundBirdMask(ctx,ribbon.nodes,defs));}
  if(m.foreground){
   // A single card owns both faces. No duplicate A symbol in the top view until
   // the near card has left the lens and its same head rejoins the carrier.
   const face=S.group([S.path(pathData(S,m.nearCard.path),c.pale,{'data-card-face':'A'}),glyph(ctx,'A',m.nearCard.glyph)],{'data-card':'A','data-projection':'near-card'});
   world.push(face);
  }
 }
 if(layerId==='T11'&&f>=630){const ribbon=ribbonShapes(ctx,m);defs.push(...ribbon.defs);world.push(...foregroundBirdMask(ctx,ribbon.nodes,defs));}
 return M.deepFreeze({defs,world,screen});
}
module.exports=Object.freeze({sceneId,implemented,resolveSceneState,renderScene});
