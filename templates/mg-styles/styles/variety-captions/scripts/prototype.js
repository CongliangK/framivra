/* Original frame-driven Canvas prototype; not the HTML or media renderer. */
(() => {
  'use strict';
  const {W, H, TAU, circle, poly, label, prog, page, data, finish} = window.MG_PROTOTYPE_HELPERS.c;
  window.MG_STYLES = {...(window.MG_STYLES || {}),
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
})();
