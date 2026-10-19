/* Original isolated 300-frame paper-swift study. MIT, 2026 Framivra contributors.
 * Pure author-space SVG/state functions. This is design code, not a new engine,
 * production replacement, renderer result or verification of the original film.
 */
(function(root,factory){
  if(typeof module==='object'&&module.exports)module.exports=factory(require('./geometry.js'));
  else root.OpeningDesign=factory(root.OpeningDesignModel);
})(typeof globalThis!=='undefined'?globalThis:this,function(D){
'use strict';
const G=D.bird,C=D.palette,E=D.events,A=D.anchors;
const clamp=(x,a=0,b=1)=>Math.max(a,Math.min(b,x));
const phase=(f,a,b)=>clamp((f-a)/(b-a));
const smooth=t=>t*t*(3-2*t);
const lerp=(a,b,t)=>a+(b-a)*t;
const num=x=>Number(x.toFixed(5));
function freeze(o){if(o&&typeof o==='object'&&!Object.isFrozen(o)){Object.values(o).forEach(freeze);Object.freeze(o);}return o;}
function frame(f,max=299){if(!Number.isInteger(f)||f<0||f>max)throw new RangeError(`frame must be integer 0..${max}`);return f;}
function interpolate(f,keys){
 if(f<=keys[0][0])return keys[0][1];
 for(let i=1;i<keys.length;i++)if(f<=keys[i][0])return lerp(keys[i-1][1],keys[i][1],smooth(phase(f,keys[i-1][0],keys[i][0])));
 return keys[keys.length-1][1];
}
function rot(p,a){const q=a*Math.PI/180,c=Math.cos(q),s=Math.sin(q);return [p[0]*c-p[1]*s,p[0]*s+p[1]*c];}
function point(p,pose){const q=rot([p[0]*pose.scale,p[1]*pose.scale],pose.angle);return [q[0]+pose.x,q[1]+pose.y];}
function inversePoint(p,pose){const q=rot([p[0]-pose.x,p[1]-pose.y],-pose.angle);return q.map(x=>x/pose.scale);}
function bezier(t,p0,p1,p2,p3){const u=1-t;return [0,1].map(i=>u*u*u*p0[i]+3*u*u*t*p1[i]+3*u*t*t*p2[i]+t*t*t*p3[i]);}
function nearWing(f){return interpolate(f,[[0,0],[190,0],[202,17],[214,-42],[228,12],[242,-20],[254,6],[264,0],[299,0]]);}
function cameraAt(f){const t=smooth(phase(f,206,264));return {x:40*t,y:-22*t};}
function birdPose(f){
 let p={x:625,y:435,scale:1.4,angle:0};
 if(f>=80&&f<96)p.y=435+56.8*Math.pow(phase(f,80,96),2);
 else if(f>=96&&f<190)p.y=491.8;
 else if(f>=190&&f<202)p.y=491.8+10*smooth(phase(f,190,202));
 else if(f>=202&&f<=206){const t=phase(f,202,206);p.x=625+4*t*t;p.y=501.8-9*t*t;p.angle=-5*t*t;}
 else if(f>206&&f<=264){
  const t=phase(f,206,264),q=bezier(t,[629,492.8],[665,405],[850,220],[986,245]);
  p={x:q[0],y:q[1],scale:lerp(1.4,1.65,t),angle:interpolate(f,[[206,-5],[239,-9],[264,4]])};
 }else if(f>264){
  const t=smooth(phase(f,264,299)),cam=cameraAt(264),start=point([-45,-50],{x:986-cam.x,y:245-cam.y,scale:1.65,angle:4});
  const focus=[lerp(start[0],640,t),lerp(start[1],360,t)];
  const scale=1.65*Math.pow(32/1.65,t),angle=lerp(4,0,t),offset=rot([-45*scale,-50*scale],angle);
  p={x:focus[0]-offset[0]+cam.x,y:focus[1]-offset[1]+cam.y,scale,angle};
 }
 return p;
}
function gap(f,start,contact,holdEnd,end,max){
 if(f<start||f>=end)return max;
 if(f<=contact){const t=phase(f,start,contact);return max*(1-t*t);}
 if(f<=holdEnd)return 0;
 return max*smooth(phase(f,holdEnd,end));
}
function outside(a,b){const dx=b[0]-a[0],dy=b[1]-a[1],len=Math.hypot(dx,dy);return [dy/len,-dx/len];}
function headWasteAt(f){
 // Once released, offcuts inherit the cut pose rather than following later bird flight.
 // The existing fall acceleration continues after tool retraction; only table occlusion
 // ends their visible life. Art timing must never delete still-visible material.
 const pose=birdPose(112),move=smooth(phase(f,112,120)),fall=Math.max(0,(f-120)/19);
 const pieces=G.scraps.map((polygon,index)=>{
  const translation=[(index?38:-35)*move+(index?16:-15)*fall,(index?20:-14)*move+80*fall*fall];
  const angle=(index?14:-15)*move,pivot=polygon[0];
  const world=polygon.map(p=>{const q=rot([p[0]-pivot[0],p[1]-pivot[1]],angle);return point([q[0]+pivot[0]+translation[0],q[1]+pivot[1]+translation[1]],pose);});
  const minY=Math.min(...world.map(p=>p[1])),maxY=Math.max(...world.map(p=>p[1]));
  return {index,polygon,translation,angle,pivot,world,minY,maxY,visible:f>=109&&minY<559};
 });
 return {pose,pieces,visible:pieces.some(p=>p.visible)};
}
function stateAt(f){
 frame(f,301);const ff=Math.min(f,299),pose=birdPose(ff),camera=cameraAt(ff),waste=headWasteAt(ff);
 const trimGap=ff<=109?(598*Math.pow(1-phase(ff,97,109),3)+2*(1-phase(ff,97,109))):(ff<=112?0:600*smooth(phase(ff,112,139))),trimEdges=[[G.head[0],G.head[1]],[G.head[5],G.head[6]]];
 const trims=trimEdges.map(([a,b],i)=>{const n=outside(a,b),offset=n.map(v=>v*trimGap);return {a,b,n,offset,edgeWorld:[point([a[0]+offset[0],a[1]+offset[1]],pose),point([b[0]+offset[0],b[1]+offset[1]],pose)],index:i};});
 const eye=point(G.eye,pose),eyeGap=ff<=150?600*Math.pow(1-phase(ff,141,150),3):(ff<=152?0:600*smooth(phase(ff,152,163)));
 const feet=phase(ff,80,92),grounded=ff>=96&&ff<=206;
 const feetY=grounded?(559-pose.y)/pose.scale:lerp(29,48,feet)*(1-smooth(phase(ff,207,219)))+29*smooth(phase(ff,207,219));
 const footWorld=[[[636.2,559],[658.6,559]],[[598.4,559],[619.4,559]]];
 const feetPaths=footWorld.map((ends,i)=>{
  if(grounded)return ends.map(p=>inversePoint(p,pose));
  const heels=[8,-19],toes=[24,-4];
  if(ff<207)return [[heels[i],feetY],[toes[i],feetY]];
  const launch=birdPose(206),t=smooth(phase(ff,206,219));
  return ends.map((p,j)=>{const a=inversePoint(p,launch),b=[j?toes[i]:heels[i],29];return [lerp(a[0],b[0],t),lerp(a[1],b[1],t)];});
 });
 return freeze({frame:f,sourceFrame:ff,boundaryProbe:f>=300,pose,camera,nearWing:nearWing(ff),farWing:nearWing(Math.max(0,ff-3))*.24,
  headCut:ff>=109,eye:ff>=150,feet,feetY,feetPaths,grounded,
  upper:{visible:ff<55,gap:ff<=22?gap(ff,0,18,22,55,132):520*smooth(phase(ff,22,55)),wasteY:550*Math.pow(phase(ff,28,55),1.5),wasteVisible:ff<55},
  lower:{visible:ff>=55&&ff<80,gap:ff<=70?gap(ff,55,67,70,80,230):950*smooth(phase(ff,70,80)),wasteY:240*Math.pow(phase(ff,80,100),2),wasteVisible:ff<100},
  trims:{visible:ff>=97&&ff<139,blades:trims,wasteT:phase(ff,112,139),wasteVisible:waste.visible,wastePose:waste.pose,wastePieces:waste.pieces},
  eyeTool:{visible:ff>=141&&ff<163,gap:eyeGap,tip:[eye[0],eye[1]-eyeGap],target:eye},
  recognition:ff>=163&&ff<190,launch:ff>=190&&ff<219,
  anchors:{footPlaneY:559,eye,wing:point(G.hinge,pose)},
  status:'DESIGN_SOURCE_UNRENDERED_UNAUDITIONED'});
}
function attrs(a={}){return Object.entries(a).filter(([,v])=>v!==null&&v!==undefined).map(([k,v])=>` ${k}="${String(v).replaceAll('&','&amp;').replaceAll('"','&quot;')}"`).join('');}
const fill=c=>C[c]||c;
function rect(x,y,w,h,c,a={}){return `<rect x="${num(x)}" y="${num(y)}" width="${num(w)}" height="${num(h)}" fill="${fill(c)}"${attrs(a)}/>`;}
function poly(ps,c,a={}){return `<polygon points="${ps.map(p=>p.map(num).join(',')).join(' ')}" fill="${fill(c)}"${attrs(a)}/>`;}
function path(d,c='none',a={}){return `<path d="${d}" fill="${fill(c)}"${attrs(a)}/>`;}
function circle(x,y,r,c,a={}){return `<circle cx="${num(x)}" cy="${num(y)}" r="${r}" fill="${fill(c)}"${attrs(a)}/>`;}
function group(s,a={}){return `<g${attrs(a)}>${s}</g>`;}
const poseAttr=p=>`translate(${num(p.x)} ${num(p.y)}) rotate(${num(p.angle)}) scale(${num(p.scale)})`;
const stroke=(c,w,extra={})=>({stroke:fill(c),'stroke-width':w,'stroke-linejoin':'miter',...extra});
function silhouette(head='blank'){
 return [G.wingback,G.wing,G.body,G.beak,G[head==='blank'?'blank':'head']].map(p=>poly(p,'#000000')).join('');
}
function wingShapes(s,skeleton){
 const far=poly(G.wingback,'red')+(skeleton?'':poly([[-48,-151],[46,-44],[10,-31]],'salmon'));
 const near=poly(G.wing,'red')+(skeleton?'':poly(G.wingfold,'rust')+poly([[-120,-137],[-6,-56],[-17,-5]],'salmon'));
 return group(far,{transform:`rotate(${num(s.farWing)} 10 -31)`})+group(near,{transform:`rotate(${num(s.nearWing)} -17 -5)`,'data-part':'same-near-wing'});
}
function bodyShapes(s,skeleton){
 let z=poly(G.body,'ink')+(skeleton?'':poly(G.tailface,'dark')+poly([[-65,-6],[-15,-25],[42,-18],[77,-8],[15,10]],'dark'));
 z+=poly(G.beak,'amber')+poly(s.headCut?G.head:G.blank,'stock');
 z+=poly([[65,-48],[91,-64],[111,-55],[78,10]],'white');
 if(s.eye)z+=circle(...G.eye,3.25,'ink',{'data-part':'new-eye'});
 if(s.feet>0){
  // The feet start folded against the printed body, then deploy as support is freed.
  const lines=s.feetPaths.map((p,i)=>`M${i?-21:4} 29 L${num(p[0][0])} ${num(p[0][1])} L${num(p[1][0])} ${num(p[1][1])}`).join(' ');
  z+=path(lines,'none',stroke('ink',2.5,{'data-part':'feet','stroke-linecap':'square'}));
 }
 return z;
}
function materialDefs(s,headOrdering){
 const masks=['upper','lower'].map(name=>{
  const [y,h]=name==='upper'?[-166,175]:[9,75];
  return `<mask id="waste-${name}" maskUnits="userSpaceOnUse" x="-184" y="-176" width="346" height="270">${rect(-174,y,326,h,'#FFFFFF')}${silhouette('blank')}</mask>`;
 }).join('');
 const ordering=headOrdering?`<mask id="head-waste-order" maskUnits="userSpaceOnUse" x="-8192" y="-8192" width="16384" height="16384">${rect(-8192,-8192,16384,16384,'#FFFFFF')}${headWaste(s,true)}</mask>`:'';
 return `<defs>${masks}<clipPath id="above-table"><rect x="0" y="0" width="1280" height="559"/></clipPath>${ordering}</defs>`;
}
function material(s){
 // Printed colours are present from frame zero. The retained bird is painted above
 // both complementary waste pieces, so the pale head never changes pigment.
 let z='';
 if(s.upper.wasteVisible){
  const t=`translate(625 ${num(435+s.upper.wasteY)}) scale(1.4)`;
  z+=group(rect(-174,-166,326,175,'stock')+rect(-174,-166,209,175,'red'),{transform:t,mask:'url(#waste-upper)','data-part':'upper-complement'});
 }
 if(s.lower.wasteVisible){
  const t=`translate(625 ${num(435+s.lower.wasteY)}) scale(1.4)`;
  z+=group(rect(-174,9,326,75,'ink'),{transform:t,mask:'url(#waste-lower)','data-part':'lower-complement'});
 }
 return group(z,{'clip-path':'url(#above-table)'});
}
function headWaste(s,knockout=false){
 if(!s.trims.wasteVisible)return '';
 return group(group(s.trims.wastePieces.filter(p=>p.visible).map(p=>
  group(poly(p.polygon,knockout?'#000000':'stock'),{transform:`translate(${num(p.translation[0])} ${num(p.translation[1])}) rotate(${num(p.angle)} ${p.pivot[0]} ${p.pivot[1]})`,'data-part':`head-ear-${p.index}`})
 ).join(''),{transform:poseAttr(s.trims.wastePose)}),{'clip-path':'url(#above-table)'});
}
function table(skeleton){
 let z=rect(155,559,970,7,'ink')+rect(155,566,970,28,'stock')+rect(155,594,970,4,'white')+rect(186,598,12,122,'pale')+rect(1082,598,12,122,'pale');
 if(!skeleton){z+=rect(465,571,320,3,'ink')+rect(465,579,320,2,'mid');for(const x of [205,1071])z+=circle(x,578,3,'mid');}
 return z;
}
function trimBlade(b){
 const a=b.a,c=b.b,n=b.n,back=n.map(v=>v*7),middle=[(a[0]+c[0])/2,(a[1]+c[1])/2];
 const heel=[middle[0]+n[0]*24,middle[1]+n[1]*24];
 // Knife and its short tang form one visible rigid unit. There are no detached poles.
 return path(`M${num(middle[0]+back[0])} ${num(middle[1]+back[1])} L${num(heel[0])} ${num(heel[1])}`,'none',stroke('mid',6))+
  poly([a,c,[c[0]+back[0],c[1]+back[1]],[a[0]+back[0],a[1]+back[1]]],'ink')+
  path(`M${a[0]} ${a[1]} L${c[0]} ${c[1]}`,'none',stroke('amber',1));
}
function tools(s,skeleton){
 let z='';
 if(s.upper.visible){
  const dy=-s.upper.gap;
  const contour=[G.wingback,G.wing,G.body,G.beak,G.blank].map(p=>poly(p,'none',stroke('ink',5.5))+poly(p,'none',stroke('amber',1))).join('');
  const arms=[[-120,-137],[-48,-151],[91,-64]].map(([x,y])=>path(`M${x} -177 V${y}`,'none',stroke('mid',3))).join('');
  z+=group(rect(-138,-185,255,8,'ink')+rect(-113,-320,5,135,'mid')+rect(111,-320,5,135,'mid')+arms+
   group(contour,{mask:'url(#waste-upper)'}),{transform:`translate(625 ${num(435+dy)}) scale(1.4)`,'data-part':'upper-profile-die'});
 }
 if(s.lower.visible){
  const edge=[[-160,38],[-43,29],[36,31],[89,12]],reverse=edge.slice().reverse().map(([x,y])=>[x,y+8]);
  const dx=s.lower.gap,dy=s.lower.gap*.24;
  z+=group(poly([...edge,...reverse],'ink')+path(`M${edge.map(p=>p.join(' ')).join(' L')}`,'none',stroke('amber',1.2))+
   path('M89 16 H183 V-15','none',stroke('mid',5)),{transform:`translate(${num(625+dx)} ${num(435+dy)}) scale(1.4)`,'data-part':'lower-profile-knife'});
 }
 if(s.trims.visible){
  z+=group(s.trims.blades.map(b=>group(trimBlade(b),{transform:`translate(${num(b.offset[0])} ${num(b.offset[1])})`,'data-part':`trim-tool-${b.index}`})).join(''),{transform:poseAttr(s.pose)});
 }
 if(s.eyeTool.visible){
  const [x,y]=s.eyeTool.tip;
  z+=path(`M${num(x)} ${num(y-280)} V${num(y-6)}`,'none',stroke('mid',4))+circle(x,y-3,3,'ink',{'data-part':'eye-tool-tip'});
 }
 return z;
}
function render(f,options={},boundary=false){
 frame(f,boundary?301:299);
 const mode=options.mode||'finished';if(!['finished','skeleton'].includes(mode))throw new TypeError('mode must be finished or skeleton');
 const role=options.role||null;if(role&&!D.visual_roles.includes(role))throw new TypeError('unknown opening visual role');
 const excluded=new Set(options.excludeRoles||[]);for(const id of excluded)if(!D.visual_roles.includes(id))throw new TypeError('unknown excluded opening role');
 const s=stateAt(f),skeleton=mode==='skeleton',pieces={};
 const active=id=>(!role||id===role)&&!excluded.has(id);
 const headOrdering=s.trims.wasteVisible&&active('T06')&&(active('T07')||active('T08'));
 pieces.T01=rect(0,0,1280,720,'paper');
 if(s.boundaryProbe)pieces.T01+=rect(0,0,1280,720,'#00FF00',{'data-part':'BOUNDARY-OCCLUSION-PROBE-NOT-S2'});
 pieces.T02=rect(470,-20,10,130,'pale')+rect(784,-20,10,130,'pale');
 pieces.T04=table(skeleton);
 pieces.T06=material(s)+headWaste(s);
 const birdOrder=headOrdering?{mask:'url(#head-waste-order)'}:{};
 pieces.T08=group(group(wingShapes(s,skeleton),{transform:poseAttr(s.pose)}),birdOrder);
 pieces.T07=group(group(bodyShapes(s,skeleton),{transform:poseAttr(s.pose)}),birdOrder);
 pieces.T09=tools(s,skeleton);
 // Stock waste stays behind the bird; active head offcuts own the foreground knockout.
 // Shared table-plane clipping prevents below-support ghosts.
 const order=['T01','T02','T06','T04','T08','T07','T09'];
 const background=active('T01')?group(pieces.T01,{'data-role':'T01'}):'';
 const world=order.filter(id=>id!=='T01'&&active(id)).map(id=>group(pieces[id],{'data-role':id})).join('');
 const fragment=materialDefs(s,headOrdering)+background+group(world,{transform:`translate(${num(-s.camera.x)} ${num(-s.camera.y)})`,'data-part':'common-camera'});
 return freeze({frame:f,state:s,fragment,mode,role,status:s.status});
}
function renderFrame(f,options={}){return render(f,options,false);}
function renderBoundaryFrame(f,options={}){if(f<299)throw new RangeError('boundary probe is only 299..301');return render(f,options,true);}
function svgFrame(f,options={}){const r=options.boundary?renderBoundaryFrame(f,options):renderFrame(f,options);return `<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1280 720"><title>Original opening source SVG, not HTML render</title>${r.fragment}</svg>`;}
function mount(document,window){
 const root=document.getElementById('scene');if(!root)throw new Error('Missing scene SVG');
 const q=new URLSearchParams(window.location.search),mode=q.get('mode')||'finished',role=q.get('role')||null;
 function renderAt(n){const f=Number(n);const result=renderFrame(frame(f),{mode,role});root.innerHTML=result.fragment;root.setAttribute('data-frame',String(f));root.setAttribute('data-status',result.status);return f;}
 window.__vtx_render=renderAt;window.__opening_design_state=stateAt;
 renderAt(q.has('f')?Number(q.get('f')):0);
 // No autoplay, clock, network, audio or random state is part of deterministic rendering.
 return freeze({renderAt,frames:300,fps:30});
}
return freeze({model:D,stateAt,renderFrame,renderBoundaryFrame,svgFrame,mount,point,rot});
});
