/* Original frame-driven Canvas prototype; not the HTML or media renderer. */
(() => {
  'use strict';
  const {W, H, TAU, circle, poly, label, prog, page, data, finish} = window.MG_PROTOTYPE_HELPERS.c;
  window.MG_STYLES = {...(window.MG_STYLES || {}),
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
    }
  };
})();
