/* Original frame-driven Canvas prototype; not the HTML or media renderer. */
(() => {
  'use strict';
  const {TAU, circle, polygon, p, colors, label, items, number} = window.MG_PROTOTYPE_HELPERS.a;
  window.MG_STYLES = {...(window.MG_STYLES || {}),
    'soft-3d'(c,m,h) {
      const [bg,ink,accent,second]=colors(m);
      c.save(); c.fillStyle=bg; c.fillRect(0,0,1920,1080);
      const data=items(m), n=data.length, gap=1360/Math.max(1,n);
      const maximum=Math.max(1,...data.map(d=>Math.abs(number(d.value))));
      // Analytic shaded spheres, squash and contact shadows form a 2.5D object stage.
      data.forEach((d,i)=>{
        const q=p(m,h,.03+i*.035,.25+i*.035), x=280+(i+.5)*gap;
        const magnitude=Math.abs(number(d.value))/maximum;
        const radius=Math.min(165,gap*.37)*(.45+.55*Math.sqrt(magnitude));
        const bounce=Math.sin(q*Math.PI)*(140+110*magnitude);
        const y=645-bounce, squash=q>.87?1-.1*Math.sin((q-.87)/.13*Math.PI):1;
        c.save(); c.translate(x,790); c.scale(1,.18);
        const shadow=c.createRadialGradient(0,0,0,0,0,radius*1.2);
        shadow.addColorStop(0,'rgba(0,0,0,.22)'); shadow.addColorStop(1,'rgba(0,0,0,0)');
        circle(c,0,0,radius*1.2,shadow); c.restore();
        c.save(); c.translate(x,y); c.scale(1/squash,squash);
        const gradient=c.createRadialGradient(-radius*.4,-radius*.45,radius*.03,0,0,radius);
        gradient.addColorStop(0,bg); gradient.addColorStop(.3,i%2?second:accent); gradient.addColorStop(1,ink);
        circle(c,0,0,radius,gradient);
        c.globalAlpha=.65; circle(c,-radius*.38,-radius*.43,radius*.1,bg); c.restore();
        label(c,m,h,String(d.value),x-gap*.4,851,gap*.8,34,'monospace');
        label(c,m,h,d.label,x-gap*.44,920,gap*.88,42,'sans-serif');
      });
      label(c,m,h,m.title,130,180,1640,112,'sans-serif');
      label(c,m,h,m.subtitle,135,258,1620,38,'sans-serif');
      c.restore();
    }
  };
})();
