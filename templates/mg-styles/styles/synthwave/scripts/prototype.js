/* Original frame-driven Canvas prototype; not the HTML or media renderer. */
(() => {
  'use strict';
  const {W, H, TAU, circle, poly, label, prog, page, data, finish} = window.MG_PROTOTYPE_HELPERS.c;
  window.MG_STYLES = {...(window.MG_STYLES || {}),
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
    }
  };
})();
