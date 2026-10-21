/* Shared Canvas prototype helpers. The three scopes intentionally preserve
 * their different drawing/text semantics; they are not interchangeable. */
window.MG_PROTOTYPE_HELPERS = {};

/* Original procedural treatments. No upstream code, fonts, meshes or imagery.
 * Every renderer owns only its canvas state; the caller clears each frame.
 * soft-3d is a shaded 2.5D illustration, not a physically rendered 3D scene. */
(() => {
  'use strict';
  const TAU = Math.PI * 2;
  const circle = (c, x, y, r, fill) => {
    c.beginPath(); c.arc(x, y, Math.max(0.01, r), 0, TAU);
    if (fill) { c.fillStyle = fill; c.fill(); } else c.stroke();
  };
  const polygon = (c, points, fill) => {
    c.beginPath(); points.forEach(([x,y], i) => i ? c.lineTo(x,y) : c.moveTo(x,y));
    c.closePath(); c.fillStyle = fill; c.fill();
  };
  const p = (m, h, a, b) => h.ease(h.clamp((m.phase - a) / (b - a)));
  const colors = m => m.palette;
  const label = (c,m,h,str,x,y,width,size,font) => {
    c.fillStyle = colors(m)[1]; h.text(c, String(str), x,y,width,size,font);
  };
  const items = m => m.items || [];
  const number = v => Number.isFinite(Number(v)) ? Number(v) : 0;
  window.MG_PROTOTYPE_HELPERS.a = {TAU, circle, polygon, p, colors, label, items, number};
})();

/* Original frame-pure interpretations of general motion-design techniques.
 * No upstream code, fonts, photographs or branded compositions are included. */
(() => {
  'use strict';
  const TAU = Math.PI * 2;
  const clamp = x => Math.max(0, Math.min(1, x));
  const ease = x => { x = clamp(x); return x * x * (3 - 2 * x); };
  const cue = (m, a, b) => ease((m.phase - a) / (b - a));
  const circle = (c, x, y, r) => { c.beginPath(); c.arc(x, y, Math.max(0, r), 0, TAU); };
  function label(c, h, text, x, y, width, size, color, font = 'sans-serif') {
    c.fillStyle = color; c.textBaseline = 'middle';
    h.text(c, String(text), x, y, width, size, font);
  }
  function round(c, x, y, w, ht, r) { c.beginPath(); c.roundRect(x, y, w, ht, r); }
  function heading(c, m, h, color, x = 100, y = 130, width = 1700) {
    c.textAlign = 'left'; label(c, h, m.title, x, y, width, 76, color);
    label(c, h, m.subtitle, x, y + 90, width, 29, color);
  }
  function countItems(m) { return m.items.length ? m.items : [{label: m.subtitle || m.title, value: 0}]; }
  window.MG_PROTOTYPE_HELPERS.b = {TAU, clamp, ease, cue, circle, label, round, heading, countItems};
})();

/* Original frame-addressable motion grammars. No upstream artwork or assets. */
(() => {
  'use strict';
  const W = 1920, H = 1080, TAU = Math.PI * 2;
  const circle = (c, x, y, r, color) => { c.fillStyle = color; c.beginPath(); c.arc(x, y, Math.max(0, r), 0, TAU); c.fill(); };
  const poly = (c, points, color) => { c.fillStyle = color; c.beginPath(); points.forEach(([x,y], i) => i ? c.lineTo(x,y) : c.moveTo(x,y)); c.closePath(); c.fill(); };
  const label = (c,h,t,x,y,w,s,font='sans-serif') => h.text(c,String(t),x,y,w,s,font);
  const prog = (m,h,a,b) => h.ease(h.progress(m,a,b));
  const page = (c,color) => { c.fillStyle=color; c.fillRect(0,0,W,H); };
  const data = m => Array.isArray(m.items) ? m.items : [];
  const finish = (c,m,h,color) => { const p=prog(m,h,.89,1); if(p>0) { c.fillStyle=color; c.fillRect(W*(1-p),0,W*p,14); } };
  window.MG_PROTOTYPE_HELPERS.c = {W, H, TAU, circle, poly, label, prog, page, data, finish};
})();
