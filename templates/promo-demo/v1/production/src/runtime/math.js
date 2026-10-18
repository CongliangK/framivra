'use strict';
// Key interpolation follows the original MIT-licensed opening preview.
function finite(value, name = 'number') {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new TypeError(`${name} must be finite`);
  return value;
}
const clamp01 = value => Math.min(1, Math.max(0, finite(value)));
const mix = (a, b, u) => finite(a) + (finite(b) - a) * finite(u);
const smoothstep = value => { const u = clamp01(value); return u*u*(3-2*u); };
const progress = (frame, start, end) => {
  if (!(end > start)) throw new RangeError('Progress interval must have positive length');
  return clamp01((finite(frame) - finite(start)) / (finite(end) - start));
};
function keys(frame, values) {
  if (!Array.isArray(values) || !values.length) throw new TypeError('Key list required');
  values.forEach(([f,v],i) => { finite(f); finite(v); if(i && f <= values[i-1][0]) throw new RangeError('Keys must strictly increase'); });
  finite(frame);
  if (frame <= values[0][0]) return values[0][1];
  for (let i=1;i<values.length;i++) {
    const [fa,a] = values[i-1], [fb,b] = values[i];
    if (frame <= fb) return mix(a,b,smoothstep(progress(frame,fa,fb)));
  }
  return values[values.length-1][1];
}
function point(p) {
  if (!Array.isArray(p) || p.length !== 2) throw new TypeError('Expected [x,y]');
  p.forEach(v => finite(v)); return p;
}
function matrix(m) {
  if (!Array.isArray(m) || m.length !== 6) throw new TypeError('Expected affine matrix of six numbers');
  m.forEach(v => finite(v)); return m;
}
const IDENTITY = Object.freeze([1,0,0,1,0,0]);
function multiply(a,b) {
  matrix(a); matrix(b);
  return [a[0]*b[0]+a[2]*b[1],a[1]*b[0]+a[3]*b[1],a[0]*b[2]+a[2]*b[3],a[1]*b[2]+a[3]*b[3],a[0]*b[4]+a[2]*b[5]+a[4],a[1]*b[4]+a[3]*b[5]+a[5]];
}
const translate = (x,y) => [1,0,0,1,finite(x),finite(y)];
const scale = value => [finite(value),0,0,value,0,0];
function rotate(degrees, pivot=[0,0]) {
  point(pivot); const a = finite(degrees)*Math.PI/180, c=Math.cos(a), s=Math.sin(a);
  return multiply(translate(...pivot),multiply([c,s,-s,c,0,0],translate(-pivot[0],-pivot[1])));
}
function transform(p,m) { point(p); matrix(m); return [m[0]*p[0]+m[2]*p[1]+m[4],m[1]*p[0]+m[3]*p[1]+m[5]]; }
function poseMatrix({x,y,scale: z=1,angle=0}) {
  if (!(z>0)) throw new RangeError('Bird scale must be positive');
  return multiply(translate(x,y),multiply(rotate(angle),scale(z)));
}
function cameraMatrix({x,y,z}) {
  if (!(z>0)) throw new RangeError('Camera zoom must be positive');
  return [finite(z),0,0,z,640-finite(x)*z,360-finite(y)*z];
}
function cubic(points,u) {
  if (points.length!==4) throw new TypeError('Cubic needs four control points');
  points.forEach(point); u=clamp01(u); const v=1-u;
  return [0,1].map(i=>v*v*v*points[0][i]+3*v*v*u*points[1][i]+3*v*u*u*points[2][i]+u*u*u*points[3][i]);
}
function deepFreeze(value) {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) { Object.values(value).forEach(deepFreeze); Object.freeze(value); }
  return value;
}
module.exports = {finite,clamp01,mix,smoothstep,progress,keys,point,matrix,IDENTITY,multiply,translate,scale,rotate,transform,poseMatrix,cameraMatrix,cubic,deepFreeze};
