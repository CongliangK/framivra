'use strict';
const {finite,point,matrix,deepFreeze} = require('./math.js');
// Small immutable SVG tree, with a stable serializer and an optional real DOM adapter.
// No parser, active SVG, inline handlers, stylesheet or external references.
const TAGS = new Set('g defs path polygon polyline rect circle ellipse line clipPath mask linearGradient stop filter feGaussianBlur text textPath use title desc'.split(' '));
const ATTRS = new Set('id d points x y x1 y1 x2 y2 cx cy r rx ry width height fill stroke stroke-width stroke-linecap stroke-linejoin fill-rule clip-rule opacity fill-opacity stroke-opacity transform clip-path mask filter maskUnits maskContentUnits mask-type clipPathUnits filterUnits gradientUnits offset stop-color stop-opacity stdDeviation color-interpolation-filters font-family font-size font-weight text-anchor dominant-baseline href startOffset textLength lengthAdjust'.split(' '));
const NUMERIC = new Set('x y x1 y1 x2 y2 cx cy r rx ry width height opacity fill-opacity stroke-opacity stroke-width stop-opacity stdDeviation font-size textLength'.split(' '));
const SVG_NS = 'http://www.w3.org/2000/svg';
const number = n => { finite(n); return Object.is(n,-0) ? '0' : String(n); };
const escape = s => String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]));
function attrValue(name,value) {
  if (NUMERIC.has(name) && typeof value==='number') return number(value);
  if (typeof value !== 'string' && typeof value !== 'number') throw new TypeError(`Invalid ${name}`);
  if (typeof value==='number') finite(value);
  const str=String(value);
  if (name==='href' && !/^#[A-Za-z][\w.-]*$/.test(str)) throw new TypeError('SVG href must be document-local');
  if (/url\s*\(/i.test(str) && !/^url\(#[A-Za-z][\w.-]*\)$/.test(str)) throw new TypeError('Only local SVG URL references allowed');
  if (/javascript:|data:|https?:|file:/i.test(str)) throw new TypeError('External SVG resources forbidden');
  return str;
}
function node(tag,attrs={},children=[]) {
  if (!TAGS.has(tag)) throw new TypeError(`Unsupported SVG tag ${tag}`);
  const clean={};
  for(const key of Object.keys(attrs).sort()) {
    if (!ATTRS.has(key) && !/^data-[a-z][a-z0-9-]*$/.test(key)) throw new TypeError(`Unsupported SVG attribute ${key}`);
    if(attrs[key]!==undefined && attrs[key]!==null) clean[key]=attrValue(key,attrs[key]);
  }
  const items=(Array.isArray(children)?children:[children]).flat(Infinity).filter(x=>x!==null && x!==undefined);
  for(const item of items) if(typeof item!=='string' && (!item || !TAGS.has(item.tag))) throw new TypeError('Invalid SVG child');
  return deepFreeze({tag,attrs:clean,children:items});
}
function serialize(item) {
  if (Array.isArray(item)) return item.map(serialize).join('');
  if (typeof item === 'string') return escape(item);
  if (!item || !TAGS.has(item.tag)) throw new TypeError('SVG fragment must contain SVG nodes');
  return `<${item.tag}${Object.keys(item.attrs).sort().map(k=>` ${k}="${escape(attrValue(k,item.attrs[k]))}"`).join('')}>${item.children.map(serialize).join('')}</${item.tag}>`;
}
function toDOM(document,item) {
  if(Array.isArray(item)) { const fragment=document.createDocumentFragment(); item.forEach(n=>fragment.appendChild(toDOM(document,n))); return fragment; }
  if(typeof item==='string') return document.createTextNode(item);
  const element=document.createElementNS(SVG_NS,item.tag);
  Object.keys(item.attrs).sort().forEach(k=>element.setAttribute(k,item.attrs[k]));
  item.children.forEach(child=>element.appendChild(toDOM(document,child))); return element;
}
const points = values => values.map(p=>{point(p);return p.map(number).join(',');}).join(' ');
const matrixString = value => { matrix(value); return `matrix(${value.map(number).join(' ')})`; };
const group=(children,attrs={})=>node('g',attrs,children);
const polygon=(p,fill,attrs={})=>node('polygon',{points:points(p),fill,...attrs});
const line=(p,stroke,width=3,attrs={})=>node('polyline',{points:points(p),fill:'none',stroke,'stroke-width':width,'stroke-linejoin':'round',...attrs});
const rect=(x,y,width,height,fill,attrs={})=>node('rect',{x,y,width,height,fill,...attrs});
const circle=(cx,cy,r,fill,attrs={})=>node('circle',{cx,cy,r,fill,...attrs});
const path=(d,fill='none',attrs={})=>node('path',{d,fill,...attrs});
function pathPoints(points,close=false) { return points.map((p,i)=>`${i?'L':'M'}${number(point(p)[0])} ${number(p[1])}`).join(' ')+(close?' Z':''); }
function cubicPath(control) { if(control.length!==4)throw new TypeError('Cubic needs four points'); return `M${points([control[0]])} C${points(control.slice(1))}`; }
function ids(prefix) {
  if(!/^[A-Za-z][\w.-]*$/.test(prefix))throw new TypeError('Unsafe page ID prefix');
  return name=>{if(!/^[A-Za-z][\w.-]*$/.test(name))throw new TypeError('Unsafe SVG ID');return `${prefix}-${name}`;};
}
module.exports={node,serialize,toDOM,group,polygon,line,rect,circle,path,pathPoints,cubicPath,points,matrixString,ids,number};
