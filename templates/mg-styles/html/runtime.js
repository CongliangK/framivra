/* Shared study controls only. A scene never reads this player's clock. */
(() => {
'use strict';
const W=1920,H=1080,FPS=30;
const clamp=(v,a=0,b=1)=>Math.max(a,Math.min(b,v));
const h={W,H,FPS,clamp,lerp:(a,b,t)=>a+(b-a)*t,ease:t=>1-(1-clamp(t))**3,smooth:t=>{t=clamp(t);return t*t*(3-2*t);},
 int(v,name,lo,hi){if(!Number.isInteger(v)||v<lo||v>hi)throw new Error(`${name}: expected integer ${lo}..${hi}`);return v;},
 num(v,name,lo,hi){if(typeof v!=='number'||!Number.isFinite(v)||v<lo||v>hi)throw new Error(`${name}: invalid finite number`);return v;},
 palette(v,n=4){if(!Array.isArray(v)||v.length!==n||v.some(c=>typeof c!=='string'||!/^#[a-f0-9]{6}$/i.test(c)))throw new Error(`palette needs ${n} RGB colors`);return v;},
 label(v,name='text',limit=180){if(typeof v!=='string'||!v.trim()||Array.from(v).length>limit)throw new Error(`${name}: invalid text`);return v;},
 stage(stages,f){const s=stages.find(s=>s.in<=f&&f<s.out);if(!s)throw new Error('frame outside story windows');return s;},
 windows(stages,N,min=1){let end=0;if(!Array.isArray(stages)||!stages.length)throw new Error('story windows required');for(const s of stages){h.int(s.in,'in',end,end);h.int(s.out,'out',end+min,N);end=s.out;}if(end!==N)throw new Error('windows must cover every frame');},
 circle(c,x,y,r,color){c.beginPath();c.arc(x,y,Math.max(.001,r),0,Math.PI*2);if(color){c.fillStyle=color;c.fill();}else c.stroke();},
 polygon(c,pts,color){c.beginPath();pts.forEach(([x,y],i)=>i?c.lineTo(x,y):c.moveTo(x,y));c.closePath();if(color){c.fillStyle=color;c.fill();}else c.stroke();},
 text(c,value,x,y,width,size,options={}){
  if(typeof value!=='string'&&typeof value!=='number')throw new Error('text must be string or number');
  const text=String(value),min=options.minSize??24,maxLines=options.maxLines??1,height=options.height??size*1.3*maxLines;
  const font=options.font??'"Noto Sans CJK SC", "Microsoft YaHei", sans-serif',weight=options.weight??'600',align=options.align??'left';
  if(![x,y,width,size,min,height].every(Number.isFinite)||width<=0||height<=0||min<12||size<min)throw new Error('invalid text box');
  c.save();let chosen;
  for(let px=Math.floor(size);px>=min;px--){
   c.font=`${weight} ${px}px ${font}`;const lines=[''];
   for(const ch of Array.from(text)){if(ch==='\r')continue;if(ch==='\n'){lines.push('');continue;}let line=lines[lines.length-1];if(line&&c.measureText(line+ch).width>width){lines.push(ch);}else lines[lines.length-1]+=ch;}
   if(lines.length<=maxLines&&lines.every(line=>c.measureText(line).width<=width)&&lines.length*px*1.25<=height){chosen={lines,size:px};break;}
  }
  if(!chosen){c.restore();throw new Error('Text does not fit its box at the minimum font size; shorten the content.');}
  c.textAlign=align;c.textBaseline='top';if(options.color)c.fillStyle=options.color;
  chosen.lines.forEach((line,i)=>c.fillText(line,x,y+i*chosen.size*1.25));c.restore();return chosen;
 }
};
window.MGReferenceHelpers=h;
const scene=window.MG_REFERENCE;
if(!scene||typeof scene.render!=='function'||typeof scene.validate!=='function')throw new Error('reference scene must declare validate and render');
const params=new URLSearchParams(location.search);
if(params.get('capture')==='1'||params.has('f'))document.documentElement.classList.add('capture');
const by=id=>document.getElementById(id),canvas=by('stage'),ctx=canvas.getContext('2d');
let index=0,data,frames,frame=0,playing=false,startTime=0,startFrame=0,queue=Promise.resolve(),generation=0;
const copy=v=>JSON.parse(JSON.stringify(v));
function error(e){playing=false;by('play').textContent='播放';by('error').textContent=String(e.message||e);}
function loadExample(i){const ex=scene.examples[i];scene.validate(ex.data,ex.frames,h);index=i;data=copy(ex.data);frames=ex.frames;frame=0;playing=false;generation++;by('play').textContent='播放';by('seek').max=frames-1;by('frames').value=frames;by('content').value=JSON.stringify(data,null,2);by('error').textContent='';return render(0);}
async function draw(f,d,N){
 /* Reset also clears a clip left by a failed third-party scene. */
 canvas.width=W;canvas.height=H;ctx.save();
 try{ctx.fillStyle=scene.background||'#f8f5ed';ctx.fillRect(0,0,W,H);await scene.render(ctx,d,f,N,h);}
 finally{ctx.restore();}
}
function render(f){h.int(f,'frame',0,frames-1);const g=generation,d=data,N=frames;queue=queue.catch(()=>{}).then(async()=>{if(g!==generation)return;await(document.fonts?.ready||Promise.resolve());if(g!==generation)return;await draw(f,d,N);if(g!==generation)return;frame=f;by('seek').value=f;by('clock').textContent=`${f} / ${N-1} · ${(f/FPS).toFixed(2)}s`;});return queue;}
/* Capture callers await this Promise before reading pixels. No clock here. */
window.__vtx_render=f=>{playing=false;by('play').textContent='播放';return render(f);};
window.__vtx_export_png=()=>canvas.toDataURL('image/png');
window.__mg_reference_state=()=>({id:scene.id,frame,frames,example:index,playing});
by('ref-title').textContent=scene.title;by('ref-limits').textContent=scene.limits||'程序图形参考；系统字体与目标浏览器需实际核验。';
scene.examples.forEach((ex,i)=>{scene.validate(ex.data,ex.frames,h);const o=document.createElement('option');o.value=i;o.textContent=ex.name;by('example').appendChild(o);});
for(const [key,value] of Object.entries(scene.recipe||{})){const box=document.createElement('section'),title=document.createElement('h3'),body=document.createElement('p');title.textContent=key;body.textContent=Array.isArray(value)?value.join(' → '):value;box.append(title,body);by('recipe').appendChild(box);}
by('example').addEventListener('change',()=>loadExample(Number(by('example').value)).catch(error));
by('seek').addEventListener('input',()=>{playing=false;by('play').textContent='播放';render(Number(by('seek').value)).catch(error);});
by('previous').addEventListener('click',()=>window.__vtx_render(Math.max(0,frame-1)).catch(error));
by('next').addEventListener('click',()=>window.__vtx_render(Math.min(frames-1,frame+1)).catch(error));
by('apply').addEventListener('click',()=>{try{const next=JSON.parse(by('content').value),N=Number(by('frames').value);h.int(N,'frames',30,18000);scene.validate(next,N,h);data=next;frames=N;frame=0;playing=false;generation++;by('seek').max=N-1;by('play').textContent='播放';by('error').textContent='';render(0).catch(error);}catch(e){error(e);}});
async function tick(now){if(!playing)return;const wanted=Math.min(frames-1,startFrame+Math.floor((now-startTime)*FPS/1000));try{await render(wanted);}catch(e){error(e);return;}if(wanted===frames-1){playing=false;by('play').textContent='播放';return;}if(playing)requestAnimationFrame(tick);}
by('play').addEventListener('click',()=>{playing=!playing;by('play').textContent=playing?'暂停':'播放';if(playing){startFrame=frame===frames-1?0:frame;startTime=performance.now();requestAnimationFrame(tick);}});
const requested=Number(new URLSearchParams(location.search).get('f')||0);
window.MG_REFERENCE_READY=loadExample(0).then(()=>render(clamp(Math.trunc(requested),0,frames-1))).catch(e=>{error(e);throw e;});
})();
