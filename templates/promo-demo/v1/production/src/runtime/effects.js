'use strict';
const S = require('./svg.js');
const {finite,clamp01,deepFreeze} = require('./math.js');
const DEFAULTS=deepFreeze({path_deformation:true,material_masks:true,text_reveal:true,translucent_trails:true,gradient_alpha_reveal:true,local_blur:true});
function options(overrides={},diagnostic=false) {
  for(const [key,value] of Object.entries(overrides)) {
    if(!Object.hasOwn(DEFAULTS,key) || typeof value!=='boolean')throw new TypeError(`Unknown or nonboolean effect ${key}`);
    if(value!==true && !diagnostic)throw new Error('Effect-off controls require an explicit diagnostic profile');
  }
  return deepFreeze({...DEFAULTS,...overrides});
}
function bounds(b) {
  if(!Array.isArray(b)||b.length!==4)throw new TypeError('Effect bounds must be [x,y,width,height]');
  b.forEach(v=>finite(v)); if(!(b[2]>0 && b[3]>0))throw new RangeError('Positive effect bounds required');
  return {x:b[0],y:b[1],width:b[2],height:b[3]};
}
function mask(id,b,occluders,enabled=true) {
  const region=bounds(b);
  return S.node('mask',{id,...region,maskUnits:'userSpaceOnUse',maskContentUnits:'userSpaceOnUse','mask-type':'luminance'},[
    S.rect(...b,'white'),...(enabled ? [S.group(occluders,{fill:'black'})]:[])
  ]);
}
// Structural coverage preserves real material ordering/contact in every diagnostic.
// Only optional workload masks may be disabled with material_masks.
function structuralSilhouette(node) {
  if(typeof node==='string'||!['g','path','polygon','polyline','rect','circle','ellipse','line'].includes(node.tag))throw new TypeError('Structural occluder must be resolved vector geometry');
  const attrs={...node.attrs};
  // Explicit child paint overrides inherited group paint in luminance masks.
  // Preserve transparent holes and shape/transform data, but make every painted
  // silhouette opaque black. Clone immutable input nodes; never alter the film art.
  if(attrs.fill && attrs.fill!=='none')attrs.fill='black';
  if(attrs.stroke && attrs.stroke!=='none')attrs.stroke='black';
  delete attrs.opacity;delete attrs['fill-opacity'];delete attrs['stroke-opacity'];
  if(attrs.mask||attrs.filter||attrs['clip-path'])throw new TypeError('Structural occluders require resolved plain geometry');
  return S.node(node.tag,attrs,node.children.map(structuralSilhouette));
}
function structuralMask(id,b,occluders) { return mask(id,b,occluders.map(structuralSilhouette),true); }
function contactPatch({id,bounds: b,sigma=2.6,shape,enabled=true}) {
  finite(sigma); if(sigma<0||sigma>2.6)throw new RangeError('Contact blur sigma must be in [0,2.6] author units');
  const region=bounds(b);
  if(region.width>160 || region.height>64)throw new RangeError('Contact filter must remain locally bounded (<=160x64 author units)');
  return {defs:enabled?[S.node('filter',{id,...region,filterUnits:'userSpaceOnUse','color-interpolation-filters':'sRGB'},[S.node('feGaussianBlur',{stdDeviation:sigma})])]:[],
    nodes:[S.group(shape,{opacity:.09,...(enabled?{filter:`url(#${id})`}:{})})]};
}
function seamGradient({id,bounds:b,enabled=true}) {
  const [x,y,w,h]=b; bounds(b);
  if(w>300||h>160)throw new RangeError('Seam gradient exceeds bounded local footprint');
  const stops=enabled?[[0,.1],[1,0]]:[[0,.1],[.5,.1],[.5,0],[1,0]];
  return S.node('linearGradient',{id,gradientUnits:'userSpaceOnUse',x1:x,y1:y,x2:x+w,y2:y},stops.map(([offset,opacity])=>S.node('stop',{offset,'stop-color':'#252B29','stop-opacity':opacity})));
}
function textReveal({id,bounds:b,progress,enabled=true}) {
  bounds(b); const width=b[2]*(enabled?clamp01(progress):1);
  return S.node('clipPath',{id,clipPathUnits:'userSpaceOnUse'},[S.rect(b[0],b[1],width,b[3],'white')]);
}
const deform=(enabled,actual,rigid)=>enabled?actual:rigid;
module.exports={DEFAULTS,options,bounds,mask,structuralMask,contactPatch,seamGradient,textReveal,deform};
