'use strict';
const M=require('./math.js');
const S=require('./svg.js');
const E=require('./effects.js');
const G=M.deepFreeze(require('../assets/geometry.json'));
const PALETTE=M.deepFreeze({paper:'#F1EDE1',ink:'#252B29',red:'#D9533B',grey:'#C3C6BB',mid:'#90998E',amber:'#D9AE53',pale:'#DCDCCF',stock:'#CBC5B3'});
// Original bird points and hinge are from candidate-r3 / R03, not a replacement logo.
function birdLocal(part,{stage=4,wing=0,feet=false}={}) {
  if(!['body','wings'].includes(part))throw new TypeError('Bird part must be body or wings');
  if(!Number.isInteger(stage)||stage<0||stage>4)throw new RangeError('Bird stage must be integer 0..4');
  M.finite(wing); const b=G.bird, c=PALETTE;
  if(part==='wings') return stage<1?[]:[S.polygon(b.wing_a.map(p=>M.transform(p,M.rotate(wing,b.wing_a_hinge))),c.red),S.polygon(b.wing_b,c.red)];
  return [
    ...(stage>=2?[S.polygon(b.body,c.ink),S.polygon(b.tail,c.ink)]:[]),
    S.polygon(b.beak,c.amber),
    S.polygon(stage<3?[[28,-66],[112,-66],[112,22],[28,22]]:b.head,c.stock),
    ...(stage>=4?[S.circle(...b.eye.center,b.eye.radius,c.ink)]:[]),
    ...(feet?[S.line([[8,33],[11,48],[29,48]],c.ink,3),S.line([[-19,32],[-18,48],[-1,48]],c.ink,3)]:[])
  ];
}
function silhouette(node) {
  if(typeof node==='string')throw new TypeError('Text is not an occluder silhouette');
  if(!['g','path','polygon','polyline','rect','circle','ellipse','line'].includes(node.tag))throw new TypeError('Occluder must be vector geometry');
  const attrs={...node.attrs};
  if(attrs.fill!=='none')attrs.fill='black';
  if(attrs.stroke && attrs.stroke!=='none')attrs.stroke='black';
  delete attrs.opacity; delete attrs['fill-opacity']; delete attrs['stroke-opacity'];
  if(attrs.mask||attrs.filter||attrs['clip-path'])throw new TypeError('Occluder silhouettes must have resolved plain geometry');
  return S.node(node.tag,attrs,node.children.map(silhouette));
}
// Mask is outside the B transform, in the same world/screen space as its occluders.
// The outer world camera is applied once by runtime.renderScene, equally to both pages.
function birdContribution({bird,id,effects,part,history=null}) {
  if(!bird)return {defs:[],world:[],screen:[]};
  if(!['world','screen'].includes(bird.space))throw new TypeError('Bird space must be world or screen');
  const occluders=(bird.occluders||[]).map(silhouette), maskId=id('whole-bird-visible');
  const clip=E.structuralMask(maskId,bird.maskBounds||[-4096,-4096,8192,8192],occluders);
  const shape=S.group(birdLocal(part,bird),{transform:S.matrixString(M.poseMatrix(bird.pose))});
  const shapes=[shape];
  if(part==='wings' && history && effects.translucent_trails) {
    if(history.space!==bird.space)throw new Error('Historical wing must use the current coordinate space');
    shapes.unshift(S.group(birdLocal('wings',history),{transform:S.matrixString(M.poseMatrix(history.pose)),opacity:.09,'data-history':'global-minus-3'}));
  }
  return {defs:[clip],world:bird.space==='world'?[S.group(shapes,{mask:`url(#${maskId})`})]:[],screen:bird.space==='screen'?[S.group(shapes,{mask:`url(#${maskId})`})]:[]};
}
function carrierAssembly(rootMatrix) {
  M.matrix(rootMatrix); const root=[...rootMatrix];
  return M.deepFreeze({carrierMatrix:root,gateMatrix:root,receiverMatrix:root,thresholdRMatrix:root,signalMountMatrix:root,fixedGuideMatrix:[...M.IDENTITY],
    guide:G['W-guide'],carrierRect:G['P-C'].local_carrier_rect,receivers:G['P-C'].receiver_rectangles});
}
// Project a material panel about an immutable common edge. Width is material width;
// its projected depth is cos(angle)*width. This is flat SVG, not a 3-D render API.
function hingePanels(sharedEdge,panels) {
  if(!Array.isArray(sharedEdge)||sharedEdge.length!==2)throw new TypeError('Two hinge endpoints required');
  sharedEdge.forEach(M.point); const edge=sharedEdge.map(p=>[...p]);
  const dx=edge[1][0]-edge[0][0],dy=edge[1][1]-edge[0][1],length=Math.hypot(dx,dy);
  if(!length)throw new RangeError('Hinge edge has zero length');
  const normal=[-dy/length,dx/length],faces={};
  for(const [name,{width,angle,side=1}] of Object.entries(panels)) {
    M.finite(width);M.finite(angle);if(width<=0||![-1,1].includes(side))throw new RangeError('Invalid hinge panel');
    const depth=side*width*Math.cos(angle*Math.PI/180),offset=normal.map(n=>n*depth);
    faces[name]={hinge:edge,materialWidth:width,angle,points:[edge[0],edge[1],edge[1].map((v,i)=>v+offset[i]),edge[0].map((v,i)=>v+offset[i])]};
  }
  return M.deepFreeze({hinge:edge,faces});
}
function forceChain({frameEndpoint,paperAnchor}) {
  M.point(frameEndpoint);M.point(paperAnchor); const anchor=[...paperAnchor];
  return M.deepFreeze({frameEndpoint:[...frameEndpoint],buckleAnchor:anchor,paperAnchor:anchor,tongueEnd:anchor,tongue:[[...frameEndpoint],anchor],tongueWidth:5});
}
function forceChainShapes(chain) {
  return {tongue:S.line(chain.tongue,PALETTE.red,chain.tongueWidth),buckle:S.rect(chain.buckleAnchor[0]-7,chain.buckleAnchor[1]-4,14,8,PALETTE.ink)};
}
function waveformPoints(envelope,{startFrame,endFrame,x,y,width,height}) {
  if(!envelope || envelope.provenance?.kind!=='actual-pcm' || !/^[0-9a-f]{64}$/.test(envelope.provenance.sha256||''))throw new Error('Verified real PCM envelope required; no synthetic fallback');
  if(!Number.isInteger(startFrame)||!Number.isInteger(endFrame)||endFrame<=startFrame)throw new RangeError('Half-open waveform interval required');
  [x,y,width,height].forEach(v=>M.finite(v));
  const samples=envelope.samples.filter(s=>s.frame>=startFrame&&s.frame<endFrame);
  if(!samples.length)throw new Error('PCM envelope has no samples in requested window');
  return samples.map(s=>{if(!Number.isInteger(s.frame)||Math.abs(M.finite(s.value))>1)throw new TypeError('Invalid PCM envelope sample');return [x+width*(s.frame-startFrame)/(endFrame-startFrame),y-height*s.value];});
}
module.exports={G,PALETTE,birdLocal,birdContribution,carrierAssembly,hingePanels,forceChain,forceChainShapes,waveformPoints,silhouette};
