/* Original deterministic SVG scene renderer. Shared verbatim by Node and HTML. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.FramivraMotion=api;})(typeof globalThis!=='undefined'?globalThis:this,function(){
'use strict';
const C={paper:'#F6F1E6',ink:'#20282D',coral:'#F26145',blue:'#3C5EDA',mint:'#BEDBCB',muted:'#717975',ghost:'#D7D7CF'};
const clamp=(x,a=0,b=1)=>Math.max(a,Math.min(b,x));
const lerp=(a,b,p)=>a+(b-a)*p;
const smooth=p=>{p=clamp(p);return p*p*(3-2*p);};
const out=p=>1-Math.pow(1-clamp(p),3);
const progress=(f,a,d)=>clamp((f-a)/d);
function settle(f,start,main=7,tail=7,amount=.065){const u=f-start;if(u<=0)return 0;if(u<main)return out(u/main);if(u<main+tail){const q=(u-main)/tail;return 1+amount*Math.sin(q*Math.PI*2)*Math.pow(1-q,2);}return 1;}
const esc=s=>String(s).replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const num=n=>Number(n.toFixed(4));
function rect(x,y,w,h,fill,rx=0,extra=''){return `<rect x="${num(x)}" y="${num(y)}" width="${num(Math.max(w,0))}" height="${num(Math.max(h,0))}" rx="${num(rx)}" fill="${fill}" ${extra}/>`;}
function circle(x,y,r,fill,extra=''){return `<circle cx="${num(x)}" cy="${num(y)}" r="${num(Math.max(0,r))}" fill="${fill}" ${extra}/>`;}
function line(x1,y1,x2,y2,col=C.ink,w=4,extra=''){return `<line x1="${num(x1)}" y1="${num(y1)}" x2="${num(x2)}" y2="${num(y2)}" stroke="${col}" stroke-width="${w}" stroke-linecap="round" ${extra}/>`;}
function path(d,fill,extra=''){return `<path d="${d}" fill="${fill}" ${extra}/>`;}
function text(x,y,s,size=50,col=C.ink,weight=700,anchor='start',latin=false,extra=''){return `<text x="${num(x)}" y="${num(y)}" font-family="${latin?'DejaVu Sans':'Noto Sans CJK SC'}" font-size="${num(size)}" font-weight="${weight}" text-anchor="${anchor}" fill="${col}" ${extra}>${esc(s)}</text>`;}
function group(body,tx=0,ty=0,sx=1,sy=sx,opacity=1,extra=''){return `<g transform="translate(${num(tx)} ${num(ty)}) scale(${num(sx)} ${num(sy)})" opacity="${num(clamp(opacity))}" ${extra}>${body}</g>`;}
function pulse(x,y,scale=1,opacity=1){let b='';for(const [dx,h] of [[-54,76],[0,150],[54,76]])b+=rect(dx-14,-h/2,28,h,C.blue,14);return group(b,x,y,scale,scale,opacity,'data-role="sound-visual"');}
function glyph(kind,x,y,scale=1,dark=false){if(kind==='picture')return circle(x,y,22*scale,C.coral);if(kind==='text')return text(x,y+15*scale,'Aa',40*scale,dark?C.paper:C.ink,700,'middle',true);return pulse(x,y,.34*scale);}
function pointer(x,y=900,opacity=1){return group(path(`M ${num(x-18)} ${y+54} L ${num(x+18)} ${y+54} L ${num(x)} ${y+29} Z`,C.ink),0,0,1,1,opacity);}
function marker(x,y=900,active=false,dark=false,old=false){const bg=dark?C.ink:C.paper,fg=dark?C.paper:C.ink;return active?circle(x,y,20,fg)+circle(x,y,8,C.coral):circle(x,y,old?15:13,bg,`stroke="${old?C.muted:fg}" stroke-width="4"`);}
function baseAxis(y=900,dark=false){return line(280,y,1720,y,C.muted,4)+rect(274,y-6,12,12,dark?C.ghost:C.muted)+path(`M 1720 ${y-10} L 1730 ${y} L 1720 ${y+10} L 1710 ${y} Z`,dark?C.ghost:C.muted);}
function sourceTag(dark=false){return text(1795,78,'timeline.json',30,dark?C.paper:C.muted,400,'end',true);}
function tinyEvents(x,plan){const w=plan.arrangements.initial.event_widths;return rect(x,796,w.picture,12,C.coral,6)+rect(x,820,w.text,12,C.ink,6)+rect(x,844,w.sound,12,C.blue,6)+line(x,785,x,900,C.muted,3,'stroke-dasharray="5 10"');}
function liveAxis(f,plan,revised){const a=plan.anchors,pt=revised?1200:760,hit=revised?a.new_hit:a.first_hit;let cur;
 if(revised){if(f<a.old_point_pass)cur=lerp(280,760,progress(f,379,a.old_point_pass-379));else if(f<hit)cur=lerp(760,1200,progress(f,a.old_point_pass,hit-a.old_point_pass));else cur=lerp(1200,1720,progress(f,hit,48));}
 else cur=f<hit?lerp(280,760,progress(f,195,hit-195)):lerp(760,1720,progress(f,hit,58));
 let b=tinyEvents(pt,plan)+baseAxis()+marker(pt,900,f>=hit)+pointer(cur);
 if(revised)b+=marker(760,900,false,false,true)+text(760,1001,f>=a.old_point_pass?'原落点 · 已过':'原落点',32,C.muted,400,'middle')+text(1200,1001,'新落点',36,C.ink,700,'middle')+text(1800,1001,'编排示意',28,C.muted,400,'end');
 else b+=text(760,1001,'同一落点',36,C.ink,700,'middle');
 return b+text(120,1001,'timeline.json',28,C.muted,400,'start',true);
}
function artwork(f,plan,hit,options={}){
 const a=plan.materials,m=plan.motion;let p=options.after?1:settle(f+1,hit,m.main_motion_frames,m.settle_frames),r=lerp(a.picture.before_radius,a.picture.after_radius,p);if(f<hit&&!options.after)p=0;
 let b=circle(a.picture.center[0],a.picture.center[1],r,C.coral,'data-role="picture"');
 if((f>=hit||options.after)&&!options.before){let tp=options.after?1:settle(f+1,hit,m.main_motion_frames,m.settle_frames,.035),alpha=options.after?1:clamp((f-hit+1)/3);b+=group(text(a.text.position[0],a.text.position[1],a.text.content,a.text.font_size,C.ink,700,'start',false,'data-role="content-title"'),0,150*(1-tp),1,1,alpha);}
 if(!options.noPulse&&!options.after&&f>=hit&&f<hit+m.sound_visual_frames){let u=(f-hit)/m.sound_visual_frames,amp=(.4+.7*Math.sin(Math.PI*clamp(u*1.5)))*(1-.2*u);b+=pulse(a.sound.visual_center[0],a.sound.visual_center[1],amp,clamp((1-u)*1.7));}
 return b;
}
function fixedArtwork(plan,state){if(state==='before')return circle(560,500,92,C.coral);if(state==='tail')return circle(1940,500,320,C.coral);return artwork(900,plan,0,{after:true,noPulse:true});}
function snapshot(x,y,w,h,state,plan,id,opacity=1){const sc=Math.min(w/1920,h/1080),ox=x+(w-1920*sc)/2,oy=y+(h-1080*sc)/2;return `<defs><clipPath id="clip-${id}"><rect x="${num(x)}" y="${num(y)}" width="${num(w)}" height="${num(h)}"/></clipPath></defs><g opacity="${num(clamp(opacity))}" clip-path="url(#clip-${id})">`+rect(x,y,w,h,C.paper)+group(fixedArtwork(plan,state),ox,oy,sc)+`</g>`;}
function intro(f,plan){let b='';const cp=settle(f+1,0,9,7,.025),sp=settle(f,10,8,8,.045),lp=out(progress(f,plan.anchors.json_origin,18)),exit=out(progress(f,108,12));
 b+=group(text(120,215,plan.copy.source[0],110)+text(120,365,plan.copy.source[1],110),0,180*(1-cp)-180*exit,1,1,clamp(cp)*(1-exit));
 let src=text(135,699,'{',190,C.coral,400,'start',true)+text(285,663,'timeline.json',106,C.ink,700,'start',true)+text(1060,699,'}',190,C.coral,400,'start',true);
 b+=group(src,-180*exit,140*(1-sp),1,1,clamp(sp)*(1-exit));
 let rail=lp>0?line(1200,625,1200+580*lp,625,C.ink,9):'';for(const x of [1300,1490,1700])if(x<=1200+580*lp)rail+=line(x,609,x,641,C.ink,4);
 rail+=circle(1490,625,23*clamp(progress(f,31,6)),C.coral);
 for(const [i,kind,yy] of [[0,'picture',470],[1,'text',535],[2,'sound',578]]){let p=settle(f,33+i*3,5,6);rail+=group(glyph(kind,1490,yy,1),0,42*(1-p),1,1,clamp(p));}
 rail+=group(line(1490,655,1490,825,C.muted,2,'stroke-dasharray="4 10"')+text(1490,885,'共同的时间依据',40,C.muted,400,'middle'),0,0,1,1,progress(f,42,8));
 return b+group(rail,0,-80*exit,1,1,1-exit);
}
function arrangement(f,plan){const entry=settle(f,120,8,7,.025),fold=smooth(progress(f,183,12)),fade=1-clamp(progress(f,183,8));let b=group(sourceTag(),0,-50*(1-entry),1,1,clamp(entry)*(1-fold));
 b+=group(text(120,184,plan.copy.arrangement[0],82)+text(120,294,plan.copy.arrangement[1],82),0,110*(1-entry)-100*(1-fade),1,1,clamp(entry)*fade);
 for(const [i,kind,label,col,ww] of [[0,'picture','画面',C.coral,440],[1,'text','文字',C.ink,380],[2,'sound','声音',C.blue,110]]){const yy=430+i*120,yp=lerp(yy,796+i*24,fold),hh=lerp(64,12,fold),p=settle(f,125+i*2,6,6);b+=group(glyph(kind,400,yy+29)+text(480,yy+45,label,48),0,40*(1-p),1,1,clamp(p)*fade);b+=group(rect(760,yp,ww,hh,col,lerp(12,6,fold)),0,35*(1-p),1,1,clamp(p));}
 const ay=lerp(865,900,fold);b+=line(760,lerp(405,785,fold),760,ay,C.muted,3,'stroke-dasharray="5 10"')+baseAxis(ay)+marker(760,ay)+pointer(500,ay,fade)+text(760,lerp(988,1001,fold),'同一落点',lerp(42,36,fold),C.ink,700,'middle');
 b+=group(text(280,988,'前段',30,C.muted,400,'middle')+text(1720,988,'后段',30,C.muted,400,'middle'),0,0,1,1,fade);
 if(fold>0)b+=group(circle(560,500,92,C.coral)+text(120,1001,'timeline.json',28,C.muted,400,'start',true),0,0,1,1,fold);
 return b;
}
function live(f,plan,revised){const hit=revised?plan.anchors.new_hit:plan.anchors.first_hit;let enter=revised?out(progress(f,379,9)):1,exit=revised?0:out(progress(f,286,14));return group(artwork(f,plan,hit),-140*exit,60*(1-enter),1,1,enter*(1-exit))+group(liveAxis(f,plan,revised),0,0,1,1,1);}
function rewrite(f,plan){const entry=settle(f,300,8,7,.025),p=settle(f+1,plan.anchors.rewrite,7,8,.055),xp=lerp(760,1200,p),trail=clamp(progress(f+1,339,3))*(1-clamp(progress(f,360,14)));let b=sourceTag(true)+group(text(120,184,plan.copy.rewrite[0],86,C.paper)+text(120,294,plan.copy.rewrite[1],86,C.paper),0,100*(1-entry),1,1,clamp(entry));
 for(const [i,kind,label,col,ww] of [[0,'picture','画面',C.coral,440],[1,'text','文字',C.paper,380],[2,'sound','声音',C.blue,110]]){const yy=430+i*120;b+=group(glyph(kind,400,yy+29,1,true)+text(480,yy+45,label,48,C.paper),0,0,1,1,clamp(entry));let arrow=line(760,yy+6,760,yy+58,C.muted,4)+line(804,yy+32,1135,yy+32,C.muted,4)+path(`M 1120 ${yy+21} L 1138 ${yy+32} L 1120 ${yy+43}`,'none',`stroke="${C.muted}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"`);b+=group(arrow,0,0,1,1,trail);b+=rect(xp,yy,ww,64,col,12);}
 b+=line(xp,405,xp,865,C.paper,3,'stroke-dasharray="5 10"')+baseAxis(865,true)+marker(760,865,false,true,f>=339)+marker(1200,865,false,true)+text(760,988,'原落点',36,C.ghost,400,'middle')+text(1200,988,'新落点',40,C.paper,700,'middle')+text(280,988,'前段',30,C.ghost,400,'middle')+text(1800,988,'编排示意',28,C.ghost,400,'end');return b;
}
function result(f,plan){const m=plan.motion,carry=settle(f+1,510,m.result_center_arrival_frames,6,.025),lead=settle(f,m.result_lead_in_frame,7,6,.035),tail=settle(f,m.result_tail_in_frame,7,6,.035),join=settle(f,m.result_strip_join_frame,m.result_strip_join_frames,6,.025),head=out(progress(f,521,8));
 const sideExit=out(progress(f,682,9));
 const c0={x:lerp(0,540,carry),y:lerp(0,360,carry),w:lerp(1920,840,carry),h:lerp(1080,472.5,carry)};
 const c={x:lerp(c0.x,600,join),y:lerp(c0.y,410,join),w:lerp(c0.w,780,join),h:lerp(c0.h,370,join)};
 let b=snapshot(c.x,c.y,c.w,c.h,'after',plan,'current');
 if(f>=m.result_lead_in_frame)b+=snapshot(lerp(-300,120,lead)-530*sideExit,lerp(495,410,join),lerp(360,480,join),lerp(202.5,370,join),'before',plan,'lead',clamp(lead)*(1-sideExit));
 if(f>=m.result_tail_in_frame)b+=snapshot(lerp(1900,lerp(1440,1380,join),tail)+520*sideExit,lerp(495,410,join),lerp(360,420,join),lerp(202.5,370,join),'tail',plan,'tail',clamp(tail)*(1-sideExit));
 b+=group(text(120,180,plan.copy.result,86)+text(120,lerp(290,337,join),'成片',42),0,65*(1-head),1,1,head);
 const lineP=out(progress(f+1,510,11)),nx=lerp(1200,lerp(540,600,join),lineP),oldx=lerp(760,lerp(350,360,join),lineP),ay=lerp(900,lerp(908,902,join),lineP);
 let axis=line(lerp(280,120,lineP),ay,lerp(1720,1800,lineP),ay,C.ink,4)+line(nx,lerp(832.5,410,join),nx,ay,C.ink,3)+circle(oldx,ay,12,C.mint,`stroke="${C.muted}" stroke-width="3"`)+circle(nx,ay,17,C.ink);
 axis+=text(oldx,974,'原落点',30,C.muted,400,'middle')+text(nx+(1-join)*40,974,'新落点',36,C.ink,700,'middle')+text(120,1030,'timeline.json',28,C.ink,400,'start',true)+text(1800,1030,'编排示意',28,C.ink,400,'end');
 if(join>0){axis+=group(line(1380,410,1380,902,C.ink,3)+path('M 1380 889 L 1393 902 L 1380 915 L 1367 902 Z',C.ink)+text(1380,974,'后续',32,C.ink,400,'middle'),0,0,1,1,clamp(join)*(1-sideExit));}
 // One semantic settling action, not repeated pulse or a third content hit.
 for(const [i,yy,col] of [[0,829,C.coral],[1,853,C.ink],[2,877,C.blue]]){const p=out(progress(f,594+i*3,5));axis+=circle(nx,lerp(ay-18,yy,clamp(join)),7*p,col);}
 // Near the end, the bridge simplifies once toward the line that will carry the brand.
 // The two context snapshots leave once; the current result remains connected to its new onset. No replay.
 return b+group(axis,0,0,1,1,lineP);
}
function brand(f,plan){const exit=out(progress(f+1,720,9)),rail=settle(f,723,8,7,.025),entry=settle(f+1,741,7,7,.025),sub=out(progress(f,759,9)),stable=out(progress(f,765,9));let b='';
 if(f<729)b+=group(rect(0,0,1920,1080,C.mint)+result(719,plan),0,-200*exit,1,1,1-exit);
 const y=lerp(902,626,rail),ra=clamp(progress(f,723,5));let rb=line(120,y,1800,y,C.paper,10)+line(600,y,1380,y,C.coral,10)+circle(600,y,16,C.blue);b+=group(rb,0,0,1,1,ra);
 b+=group(text(960,505,plan.copy.brand,252,C.paper,700,'middle',true),0,170*(1-entry),1,1,clamp(entry));
 b+=group(text(960,800,plan.copy.tagline,76,C.paper,700,'middle'),0,28*(1-sub),1,1,sub)+group(text(960,918,plan.copy.stability,40,C.mint,400,'middle'),0,28*(1-stable),1,1,stable);return b;
}
function stateAt(frame,plan){const f=Math.max(0,Math.min(plan.project.duration_frames-1,Math.floor(frame))),shot=plan.shots.find(s=>f>=s.in&&f<s.out).id;return {frame:f,seconds:f/plan.project.fps,shot,first_hit:f===plan.anchors.first_hit,new_hit:f===plan.anchors.new_hit,old_point_pass:f===plan.anchors.old_point_pass,audio_included:false};}
function renderFrame(frame,plan){const state=stateAt(frame,plan),f=state.frame,shot=state.shot;let body,bg=C.paper;if(shot==='json_source')body=intro(f,plan);else if(shot==='arrangement')body=arrangement(f,plan);else if(shot==='first_content')body=live(f,plan,false);else if(shot==='rewrite'){body=rewrite(f,plan);bg=C.ink;}else if(shot==='revised_content')body=live(f,plan,true);else if(shot==='result'){body=result(f,plan);bg=C.mint;}else{body=brand(f,plan);bg=C.ink;}
 return `<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080" role="img"><title>Framivra abstract timeline motion preview</title><desc>Frame ${f}; ${state.seconds.toFixed(3)} seconds; shared SVG renderer; no audio; not a browser or product-render validation.</desc>${rect(0,0,1920,1080,bg)}${body}</svg>`;}
return {renderFrame,stateAt,version:'timeline-motion-r1'};
});
