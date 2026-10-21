/* Original frame-driven Canvas prototype; not the HTML or media renderer. */
(() => {
  'use strict';
  const {W, H, TAU, circle, poly, label, prog, page, data, finish} = window.MG_PROTOTYPE_HELPERS.c;
  window.MG_STYLES = {...(window.MG_STYLES || {}),
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
    }
  };
})();
