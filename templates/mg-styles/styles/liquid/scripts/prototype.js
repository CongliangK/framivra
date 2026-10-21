/* Original frame-driven Canvas prototype; not the HTML or media renderer. */
(() => {
  'use strict';
  const {W, H, TAU, circle, poly, label, prog, page, data, finish} = window.MG_PROTOTYPE_HELPERS.c;
  window.MG_STYLES = {...(window.MG_STYLES || {}),
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
    }
  };
})();
