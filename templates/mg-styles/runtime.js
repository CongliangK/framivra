/* Independent frame-driven composition host. No external assets. */
(() => {
'use strict';
const canvas=document.getElementById('stage'),ctx=canvas.getContext('2d');
const clamp=x=>Math.max(0,Math.min(1,x));
const h={clamp,ease:x=>1-Math.pow(1-clamp(x),3),progress:(m,a,b)=>clamp((m.phase-a)/(b-a)),text(c,s,x,y,w,size,font='sans-serif'){
 c.save();c.font=`${size}px ${font}`;
 const width=c.measureText(String(s)).width;
 if(width>w)c.font=`${size*w/width}px ${font}`;
 c.fillText(String(s),x,y);c.restore();
}};
const data=window.MG_CONTENT;
window.__vtx_render=function(frame){
 const f=Math.max(0,Math.min(data.frames-1,Math.trunc(frame)||0));
 const active=Math.max(1,data.frames-1-(data.hold_frames||0));
 const m={...data,f:Math.min(f,active),phase:Math.pow(Math.min(f,active)/active,data.pace)};
 ctx.resetTransform();ctx.clearRect(0,0,1920,1080);ctx.save();
 ctx.fillStyle=m.palette[0];ctx.fillRect(0,0,1920,1080);
 window.MG_STYLES[data.style](ctx,m,h);ctx.restore();
};
window.__vtx_render(Number(new URLSearchParams(location.search).get('f')||0));
window.__vtx_export_png=()=>canvas.toDataURL('image/png');
})();
