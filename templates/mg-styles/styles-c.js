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
  const styles = {
    bauhaus(c,m,h) {
      const [bg,ink,accent,secondary]=m.palette;
      const p=prog(m,h,0,.19), q=prog(m,h,.2,.49), end=prog(m,h,.82,1);
      page(c,bg);
      // A circle, a rigid axis, then the content: geometry establishes hierarchy.
      c.fillStyle=ink; c.fillRect(112,90,14,900*p);
      c.save(); c.translate(1420,320); c.rotate((1-p)*Math.PI*.5);
      c.fillStyle=accent; c.fillRect(-280,-240,480*p,480);
      c.beginPath(); c.arc(0,0,270,0,Math.PI); c.fillStyle=secondary; c.fill();
      c.restore();
      circle(c,1420-120*end,320+280*end,145*p,ink);
      c.fillStyle=ink;
      label(c,h,m.title,180,270+50*(1-p),990,112,'sans-serif');
      label(c,h,m.subtitle,185,370,940,34,'sans-serif');
      const items=data(m), n=Math.max(1,items.length), gap=1150/n;
      items.forEach((item,i)=>{
        const a=prog(m,h,.22+i*.035,.46+i*.035), x=185+i*gap;
        c.fillStyle=i%2?secondary:accent;
        c.fillRect(x,785-260*a,Math.max(16,gap-34),260*a);
        c.fillStyle=ink;
        label(c,h,item.label,x,850,gap-26,Math.min(34,gap*.22));
        label(c,h,item.value,x,940,gap-26,64,'sans-serif');
      });
      c.fillStyle=ink; c.fillRect(178,425,950*q,5);
      // Closing geometry repeats the opening axis without fading the full page.
      c.fillStyle=secondary; c.fillRect(1740,620,90,320*end);
      finish(c,m,h,ink);
    },
    synthwave(c,m,h) {
      const [bg,ink,accent,secondary]=m.palette, p=prog(m,h,0,.2);
      page(c,'#100c28');
      const sky=c.createLinearGradient(0,0,0,670); sky.addColorStop(0,bg); sky.addColorStop(1,'#39204a'); c.fillStyle=sky;c.fillRect(0,0,W,670);
      // A striped setting sun is the destination; perspective flow supplies momentum.
      c.save(); c.beginPath(); c.arc(1350,420,225*p,0,TAU); c.clip();
      const sun=c.createLinearGradient(0,200,0,650);sun.addColorStop(0,secondary);sun.addColorStop(1,accent);c.fillStyle=sun;c.fillRect(1050,150,600,600);
      c.fillStyle='#39204a';for(let y=430;y<670;y+=30)c.fillRect(1050,y,600,9+(y-430)*.025);c.restore();
      poly(c,[[0,675],[300,480],[510,660],[850,510],[1050,675]],'#20192e');
      poly(c,[[1050,675],[1510,500],[1690,620],[W,450],[W,675]],'#251632');
      c.strokeStyle=accent;c.lineWidth=2;c.globalAlpha=.65;
      for(let i=-12;i<=12;i++){c.beginPath();c.moveTo(960+i*44,675);c.lineTo(960+i*380,1080);c.stroke();}
      const shift=(m.phase*5)%1;
      for(let i=0;i<15;i++){const t=(i+shift)/15,y=675+405*t*t;c.beginPath();c.moveTo(0,y);c.lineTo(W,y);c.stroke();}c.globalAlpha=1;
      c.save();c.translate(150,285+50*(1-p));c.transform(1,0,-.14,1,0,0);
      c.shadowColor=accent;c.shadowBlur=28;c.fillStyle=ink;
      label(c,h,m.title,0,0,1060,110,'sans-serif');c.shadowBlur=0;c.fillStyle=secondary;label(c,h,m.subtitle,0,85,1020,36);c.restore();
      const items=data(m);items.forEach((item,i)=>{const x=160+i*1560/Math.max(1,items.length),a=prog(m,h,.25+i*.025,.46+i*.025);c.globalAlpha=a;c.fillStyle='#fff4fb';label(c,h,item.label,x,840,1450/Math.max(1,items.length),30,'monospace');c.fillStyle=secondary;label(c,h,item.value,x,910,1450/Math.max(1,items.length),52,'monospace');});c.globalAlpha=1;
      // Restrained, deterministic scan lines; no nondeterministic VHS noise.
      c.fillStyle='rgba(8,5,20,.12)';for(let y=0;y<H;y+=6)c.fillRect(0,y,W,2);
      finish(c,m,h,accent);
    },
    pixel(c,m,h) {
      const [bg,ink,accent,secondary]=m.palette, items=data(m), count=Math.max(1,items.length);
      page(c,bg); const unit=20;
      c.fillStyle=ink;c.fillRect(80,70,1760,14);c.fillRect(80,985,1760,14);
      label(c,h,m.title,110,190,1660,78,'monospace');c.fillStyle=secondary;label(c,h,m.subtitle,110,250,1660,30,'monospace');
      // Explicit tiled steps, not smooth movement, carry the progression metaphor.
      const beat=Math.floor(m.phase*32), travel=Math.floor(prog(m,h,.06,.8)*76)*unit;
      const avatarX=160+travel, ground=805;
      c.fillStyle=ink;for(let x=80;x<1840;x+=unit*2)c.fillRect(x,ground,unit*2,unit);
      const sprite=['00111100','01111110','11011011','11111111','00111100','01111110','11000011'];
      const jump=beat%4<2?0:-20;
      sprite.forEach((row,y)=>[...row].forEach((v,x)=>{if(v==='1'){c.fillStyle=y<4?accent:ink;c.fillRect(avatarX+x*unit,ground-180+y*unit+jump,unit,unit);}}));
      items.forEach((item,i)=>{
        const cell=1520/count,x=200+i*cell,a=prog(m,h,.18+i*.035,.38+i*.035);
        const block=Math.min(130,cell*.6), y=535;
        c.fillStyle=ink;c.fillRect(x+8,y+8,block,block);c.fillStyle=i%2?secondary:accent;c.fillRect(x,y,block,block*a);
        c.fillStyle=ink;label(c,h,item.label,x,465,cell-20,Math.min(32,cell*.18),'monospace');label(c,h,item.value,x,y+block/2+16,block-8,40,'monospace');
        if(a===1){c.fillStyle=secondary;c.fillRect(x+block*.4,y-45,unit,unit);}
      });
      c.fillStyle=ink;label(c,h,String(Math.round(prog(m,h,0,.9)*100)).padStart(3,'0')+'%',1580,945,220,38,'monospace');
      finish(c,m,h,accent);
    },
    liquid(c,m,h) {
      const [bg,ink,accent,secondary]=m.palette,p=prog(m,h,0,.22),items=data(m);
      page(c,bg);
      // One falling drop expands into a pool; its surface becomes the content carrier.
      const drop=prog(m,h,0,.22), spread=prog(m,h,.18,.5);
      if(drop<1)circle(c,960,100+420*drop,35+30*drop,accent);
      c.fillStyle=secondary;c.beginPath();c.moveTo(0,H);
      for(let x=0;x<=W;x+=12){const wave=Math.sin(x/200+m.phase*TAU*1.3)*30+Math.sin(x/90-m.phase*5)*9;c.lineTo(x,790-160*spread+wave*spread);}c.lineTo(W,H);c.closePath();c.fill();
      c.fillStyle=accent;c.beginPath();c.moveTo(0,H);
      for(let x=0;x<=W;x+=12)c.lineTo(x,870-130*spread+Math.sin(x/250-m.phase*TAU)*34*spread);
      c.lineTo(W,H);c.closePath();c.fill();
      c.save();c.translate(960,470);c.scale(1+.18*Math.sin(m.phase*TAU)*p,1-.08*Math.sin(m.phase*TAU)*p);
      const merge=prog(m,h,.22,.42), split=prog(m,h,.70,.9);
      // Two satellite drops meet the body, then pinch away into independent droplets.
      const distance=250*(1-merge)+210*split;
      const lobe=(48*(1-merge)+42*split)*p;
      circle(c,-distance,20+30*Math.sin(m.phase*6),lobe,accent);
      circle(c,distance,-20-50*split,lobe,accent);
      const r=165*p;c.beginPath();for(let i=0;i<=100;i++){const a=i/100*TAU,rr=r*(1+.09*Math.sin(a*3+m.phase*8));const x=Math.cos(a)*rr,y=Math.sin(a)*rr;i?c.lineTo(x,y):c.moveTo(x,y);}c.closePath();c.fillStyle=accent;c.fill();c.restore();
      c.fillStyle=ink;label(c,h,m.title,140,185,1640,92);label(c,h,m.subtitle,145,255,1620,34);
      items.forEach((item,i)=>{const width=1600/Math.max(1,items.length), x=160+i*width,a=prog(m,h,.35+i*.035,.59+i*.035);c.save();c.translate(x,840+80*(1-a));c.globalAlpha=a;c.fillStyle=ink;label(c,h,item.value,0,0,width-30,64);label(c,h,item.label,0,65,width-30,30);c.restore();});
      finish(c,m,h,ink);
    },
    'variety-captions'(c,m,h) {
      const [bg,ink,accent,secondary]=m.palette,items=data(m),p=prog(m,h,0,.17);
      page(c,bg);
      // Portrait stage is intentional inside the landscape canvas, not cropped text.
      const sx=660,sw=600;c.fillStyle=ink;c.fillRect(sx-12,35,sw+24,1010);
      const stage=c.createLinearGradient(sx,40,sx+sw,1030);stage.addColorStop(0,secondary);stage.addColorStop(1,accent);c.fillStyle=stage;c.fillRect(sx,47,sw,986);
      c.save();c.beginPath();c.rect(sx,47,sw,986);c.clip();
      c.fillStyle='rgba(255,255,255,.22)';for(let i=0;i<9;i++){c.save();c.translate(960,560);c.rotate(i*TAU/9+m.phase*.12);c.fillRect(0,-25,700,50);c.restore();}
      // Original geometric host keeps the template free of external likeness/assets.
      c.fillStyle=bg;c.fillRect(805,550,310,360);circle(c,960,425,125*p,bg);circle(c,922,415,10,ink);circle(c,999,415,10,ink);
      c.strokeStyle=ink;c.lineWidth=9;c.beginPath();c.arc(960,441,45,.1,Math.PI-.1);c.stroke();
      c.fillStyle=ink;label(c,h,m.title,695,155,530,55);label(c,h,m.subtitle,700,225,520,26);
      const active=items.length?Math.min(items.length-1,Math.floor(h.clamp((m.phase-.2)/.7)*items.length)):-1;
      if(active>=0){const item=items[active],local=h.clamp(((m.phase-.2)/.7*items.length)-active),pop=1+.12*Math.sin(Math.min(1,local*5)*Math.PI);c.save();c.translate(960,795);c.scale(pop,pop);c.fillStyle=bg;c.fillRect(-255,-66,510,140);c.lineWidth=8;c.strokeStyle=ink;c.strokeRect(-255,-66,510,140);c.fillStyle=ink;label(c,h,item.label,-235,-2,470,42);c.fillStyle=accent;label(c,h,item.value,-235,50,470,35);c.restore();}
      c.restore();
      c.fillStyle=ink;label(c,h,'●',150,210,180,110);label(c,h,m.title,120,340,470,54);c.fillStyle=accent;c.fillRect(120,380,250*p,12);
      c.fillStyle=ink;label(c,h,m.subtitle,1330,710,450,35);c.strokeStyle=ink;c.lineWidth=8;c.beginPath();c.moveTo(1390,500);c.lineTo(1610,500);c.lineTo(1550,440);c.moveTo(1610,500);c.lineTo(1550,560);c.stroke();
      finish(c,m,h,ink);
    }
  };
  window.MG_STYLES={...(window.MG_STYLES||{}),...styles};
})();
