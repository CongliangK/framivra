/* Independent frame-driven composition host. No external assets. */
(() => {
'use strict';
const canvas=document.getElementById('stage'),ctx=canvas.getContext('2d');
const clamp=x=>Math.max(0,Math.min(1,x));
const h={clamp,ease:x=>1-Math.pow(1-clamp(x),3),progress:(m,a,b)=>clamp((m.phase-a)/(b-a)),text(c,s,x,y,w,size,font='sans-serif'){
 const value=String(s);
 if(/[\r\n]/.test(value))throw new Error('Prototype text boxes accept one line; shorten the text.');
 const minimum=Math.min(size,24);
 c.save();
 try {
  c.font=`${size}px ${font}`;
  const measured=c.measureText(value).width;
  const fitted=measured>w?size*w/measured:size;
  if(fitted<minimum)throw new Error('Text does not fit at the 24px minimum; shorten the text or use the HTML reference multiline box.');
  c.font=`${fitted}px ${font}`;c.fillText(value,x,y);
 } finally {c.restore();}
}};
const data=window.MG_CONTENT;
window.__vtx_render=function(frame){
 const f=Math.max(0,Math.min(data.frames-1,Math.trunc(frame)||0));
 const active=Math.max(1,data.frames-1-(data.hold_frames||0));
 const m={...data,f:Math.min(f,active),phase:Math.pow(Math.min(f,active)/active,data.pace)};
 canvas.width=1920;canvas.height=1080;
 ctx.resetTransform();ctx.clearRect(0,0,1920,1080);ctx.save();
 ctx.fillStyle=m.palette[0];ctx.fillRect(0,0,1920,1080);
 try {window.MG_STYLES[data.style](ctx,m,h);} finally {ctx.restore();}
};
window.__vtx_render(Number(new URLSearchParams(location.search).get('f')||0));
window.__vtx_export_png=()=>canvas.toDataURL('image/png');
})();
