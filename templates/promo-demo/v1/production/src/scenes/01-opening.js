'use strict';
// S1: exact v03 opening geometry, partitioned into the approved sparse GFX roles.
// Adapted from Framivra candidate-r3 opening.html (MIT, copyright 2026 ruochongliang).
// Shared runtime alone paints B in T07/T08. This module owns material and workshop.
const sceneId = 'S1';
const implemented = true;
const STOCK_RECT = Object.freeze([[-110,-104],[56,-104],[56,86],[-110,86]].map(Object.freeze));
const EMPTY = Object.freeze({defs:Object.freeze([]),world:Object.freeze([]),screen:Object.freeze([])});

function headOffcuts(s) {
  if (!s.offcuts.visible) return [];
  const x=950-20*s.offcuts.t,y=440+260*s.offcuts.t;
  return [[[x+28,y-66],[x+56,y-66],[x+28,y+22]],[[x+112,y-8],[x+112,y+22],[x+84,y+22]]];
}

function resolveSceneState(ctx, {globalFrame, localSceneFrame}) {
  if (globalFrame !== localSceneFrame) throw new RangeError('S1 requires matching global/local frame');
  const opening = ctx.opening.openingState(globalFrame);
  // One solved immutable state supplies every layer, never query/entry or prior-frame data.
  // T06 owns the original offcuts below B. Their resolved silhouettes cut out
  // both shared B pages, preserving the source's offcuts-over-B paint order.
  const bird=opening.offcuts.visible?{...opening.bird,occluders:headOffcuts(opening).map(ps=>ctx.svg.polygon(ps,'black'))}:opening.bird;
  return ctx.math.deepFreeze({cameraMatrix:opening.cameraMatrix,bird,
    mechanics:{opening,recognition:globalFrame>=163&&globalFrame<190,
      contactFrames:{cut:18,body:67,trim:109,eye:150},
      stockBounds:[240,336,406,526],cutCover:[236,opening.presses.cut,410,opening.presses.cut+198],
      bodyImprintOrigin:[650,opening.presses.body+41],eyeTip:[1343,opening.presses.eye]}});
}

// Source Python/HTML used ties-to-even stroke quantization at 1.5 delivery scale.
function roundEven(n) { const a=Math.floor(n),frac=n-a;return frac===.5?a+(a%2):Math.round(n); }
function renderScene(ctx, {globalFrame, localSceneFrame, layerId}) {
  if (globalFrame!==localSceneFrame || globalFrame<0 || globalFrame>=300) throw new RangeError('S1 frame outside [0,300)');
  if (!['T02','T04','T06','T09'].includes(layerId)) return EMPTY;
  const s=ctx.state.mechanics.opening,f=s.f,S=ctx.svg,M=ctx.math,G=ctx.geometry.G.bird,C=ctx.geometry.PALETTE;
  const {paper:PAPER,ink:INK,red:RED,grey:GREY,mid:MID,amber:AMBER,pale:PALE,stock:STOCK}=C;
  const world=[],defs=[];
  const poly=(ps,color,attrs={})=>world.push(S.polygon(ps,color,attrs));
  // Keep rectangle vertices identical to the source oracle, including winding.
  const rect=(x,y,w,h,color)=>poly([[x,y],[x+w,y],[x+w,y+h],[x,y+h]],color);
  const circle=(x,y,r,color)=>world.push(S.circle(x,y,r,color));
  const line=(ps,color=INK,width=3)=>world.push(S.line(ps,color,Math.max(1,roundEven(width*s.camera.z*1.5))/(s.camera.z*1.5)));
  const transformPoints=(points,matrix)=>points.map(p=>M.transform(p,matrix));

  if(layerId==='T02') {
    rect(-450,70,2400,13,GREY);
    for(const x of [120,625,1180,1780]) rect(x,84,9,478,PALE);
    rect(1550,118,270,12,INK);
    [74,105,91].forEach((h,i)=>rect(1570+i*65,118-h,46,h,[RED,INK,AMBER][i]));
    line([[1450,83],[1450,162]],INK,3);
    poly([[1408,162],[1492,162],[1470,189],[1430,189]],MID);
  }

  if(layerId==='T04') {
    // Original red supply reel is not the later Q sound-paper reel.
    circle(-90,432,158,INK);circle(-90,432,124,RED);circle(-90,432,28,PAPER);
    rect(-90,371,460,150,RED);
    rect(-220,340,2160,200,PALE);rect(-220,356,2160,169,PAPER);
    for(let x=-400;x<2000;x+=95){const xx=x+(s.hx-280)%95;line([[xx,531],[xx+38,531]],MID,2);}
    line([[-220,543],[1940,543]],INK,8);line([[-220,570],[1940,570]],MID,4);
    for(let x=-150;x<1950;x+=180){
      circle(x,587,27,INK);circle(x,587,9,MID);
      const a=((f*5+x)%360)*Math.PI/180,xx=Math.cos(a)*20,yy=Math.sin(a)*20;
      line([[x-xx,587-yy],[x+xx,587+yy]],PAPER,3);
    }
    rect(115,615,18,180,INK);rect(1640,615,18,180,INK);
    if(f>=190){
      // The original pale vacated-stock impression remains on the conveyor.
      // This is fixed support material, never a second animated B or red wing.
      const footprint=[G.wing_a,G.wing_b,G.body,G.tail,[[28,22],[56,-66],[112,-66],[112,-47],[140,-33],[112,-25],[112,-8],[84,22]]];
      for(const ps of footprint)poly(ps.map(([x,y])=>[1250+x,440+y]),PALE,{'data-part':'vacated-stock-impression'});
    }
  }

  if(layerId==='T06') {
    if(s.stage===0)poly(transformPoints(STOCK_RECT,M.poseMatrix(s.bird.pose)),RED,{'data-part':'uncut-stock'});
    if(s.waste.visible){
      const w=s.waste,wm=M.poseMatrix({x:w.x,y:w.y,angle:w.angle,scale:1});
      const contours=[STOCK_RECT,G.wing_a,G.wing_b].map(ps=>S.pathPoints(transformPoints(ps,wm),true));
      world.push(S.path(contours.join(' '),RED,{'data-part':'complementary-waste','fill-rule':'evenodd'}));
    }
    if(s.offcuts.visible){
      headOffcuts(s).forEach((ps,i)=>poly(ps,STOCK,{'data-part':i?'head-offcut-b':'head-offcut-a'}));
    }
  }

  if(layerId==='T09') {
    const y=s.presses.cut;
    rect(266,84,20,240,GREY);rect(420,84,20,240,GREY);
    rect(236,y,174,198,INK);rect(240,y+4,166,190,MID);
    for(const tri of [G.wing_a,G.wing_b])line([...tri,tri[0]].map(([x,yy])=>[350+x,y+108+yy]),PAPER,2);
    rect(332,72,20,Math.max(20,y-72),MID);
    rect(566,105,155,76,INK);rect(581,119,125,9,MID);line([[644,181],[644,253]],INK,14);
    const y2=s.presses.body;
    rect(495,y2-15,251,31,INK);rect(632,203,22,Math.max(38,y2-203),MID);
    poly(G.body.map(([x,y])=>[650+x,y2+41+y]),MID);poly(G.tail.map(([x,y])=>[650+x,y2+41+y]),MID);
    const k=s.presses.trim;
    rect(1025,170,18,137,GREY);
    poly([[978,374],[1006,374],[978,462]].map(([x,y])=>[x-30*(1-k),y-100*(1-k)]),MID);
    poly([[1062,432],[1062,462],[1034,462]].map(([x,y])=>[x+30*(1-k),y-100*(1-k)]),MID);
    rect(1312,147,64,74,INK);rect(1328,221,31,38,MID);
    const y4=s.presses.eye;
    rect(1338,251,10,Math.max(4,y4-251),INK);circle(1343,y4,6,INK);
    circle(1740,311,30,INK);poly([[1711,350],[1764,350],[1784,452],[1696,452]],MID);
    line([[1712,450],[1699,539]],INK,13);line([[1766,450],[1783,539]],INK,13);
    line([[1680,543],[1706,543]],INK,10);line([[1774,543],[1800,543]],INK,10);
    line([[1710,366],[1679,394],[1651,369-s.wave]],INK,10);line([[1763,367],[1804,389],[1820,355]],INK,10);
    circle(1820,346,9,RED);line([[1820,355],[1843,473]],INK,4);
    if(s.approach){
      // T09 is above the two B tracks. A nonpainting knockout restores the source
      // foreground draw order. It is semantic ordering, not an E01/E02 mask effect.
      // Apply the inverse common camera only to the screen-space B occluder;
      // runtime still projects all workshop geometry exactly once.
      const c=ctx.state.cameraMatrix,inv=[1/c[0],0,0,1/c[3],-c[4]/c[0],-c[5]/c[3]];
      const cutout=ctx.geometry.silhouette(S.group([
        ...ctx.geometry.birdLocal('wings',s.bird),...ctx.geometry.birdLocal('body',s.bird)
      ],{transform:S.matrixString(M.multiply(inv,M.poseMatrix(s.bird.pose)))}));
      const id=ctx.id('foreground-b-order');
      defs.push(ctx.effectHelpers.mask(id,[-4096,-4096,8192,8192],[cutout],true));
      return M.deepFreeze({defs,world:[S.group(world,{mask:`url(#${id})`,'data-part':'tools-behind-foreground-b'})],screen:[]});
    }
  }
  return M.deepFreeze({defs,world,screen:[]});
}
module.exports=Object.freeze({sceneId,implemented,resolveSceneState,renderScene});
