/* Original frame-driven Canvas prototype; not the HTML or media renderer. */
(() => {
  'use strict';
  const {TAU, circle, polygon, p, colors, label, items, number} = window.MG_PROTOTYPE_HELPERS.a;
  window.MG_STYLES = {...(window.MG_STYLES || {}),
    'cel-boil'(c,m,h) {
      const [bg,ink,accent,second] = colors(m);
      c.save(); c.fillStyle=bg; c.fillRect(0,0,1920,1080);
      const tick=Math.floor(m.f/3), open=p(m,h,0,.18);
      const wobble = (i,amp=3) => Math.sin((tick*19+i*37+(m.seed||0))*1.7)*amp;
      // Held drawings and uneven contours, rather than smooth vector breathing.
      c.lineJoin='round'; c.lineCap='round'; c.strokeStyle=ink; c.lineWidth=8;
      const nodes=items(m), n=Math.max(1,nodes.length), gap=1300/n;
      c.save(); c.translate(0,70*(1-open));
      label(c,m,h,m.title,130,195,1640,126,'cursive');
      for(let i=0;i<nodes.length;i++) {
        const x=310+(i+.5)*gap, y=590 + Math.sin(i*1.7)*65;
        const appear=p(m,h,.12+i*.035,.3+i*.035), radius=Math.min(125,gap*.37)*appear;
        c.beginPath();
        for(let j=0;j<=40;j++) {
          const a=j/40*TAU, r=radius+wobble(i*43+j,4)*appear;
          const px=x+Math.cos(a)*r, py=y+Math.sin(a)*r;
          j ? c.lineTo(px,py) : c.moveTo(px,py);
        }
        c.fillStyle=i%2 ? second : accent; c.fill(); c.stroke();
        if(appear>.5) {
          circle(c,x-radius*.28,y-10,6,ink); circle(c,x+radius*.28,y-10,6,ink);
          c.beginPath(); c.arc(x,y+8,23,0,Math.PI); c.stroke();
        }
        const small=Math.min(46,310/n+18);
        label(c,m,h,nodes[i].label,x-gap*.42,820,gap*.84,small,'cursive');
        for(let j=0;j<3;j++) {
          const a=-1.1+j*.5, r=radius+20+j*8;
          c.beginPath(); c.moveTo(x+Math.cos(a)*r,y+Math.sin(a)*r);
          c.lineTo(x+Math.cos(a)*(r+24*appear),y+Math.sin(a)*(r+24*appear)); c.stroke();
        }
      }
      c.restore(); label(c,m,h,m.subtitle,134,975,1550,40,'cursive');
      c.restore();
    }
  };
})();
