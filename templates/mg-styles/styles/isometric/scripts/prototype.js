/* Original frame-driven Canvas prototype; not the HTML or media renderer. */
(() => {
  'use strict';
  const {TAU, circle, polygon, p, colors, label, items, number} = window.MG_PROTOTYPE_HELPERS.a;
  window.MG_STYLES = {...(window.MG_STYLES || {}),
    'isometric'(c,m,h) {
      const [bg,ink,accent,second]=colors(m);
      c.save(); c.fillStyle=bg; c.fillRect(0,0,1920,1080);
      label(c,m,h,m.title,110,155,1620,92,'sans-serif');
      label(c,m,h,m.subtitle,115,225,1600,34,'sans-serif');
      const data=items(m), n=data.length, maximum=Math.max(1,...data.map(x=>Math.abs(number(x.value))));
      const unit=Math.min(115,640/Math.max(1,n));
      const project=(x,y,z)=>[960+(x-y)*unit*Math.sqrt(3)/2,680+(x+y)*unit*.5-z];
      // A common ground plane establishes a spatial system before anything grows.
      for(let i=-3;i<=3;i++) {
        c.strokeStyle=ink; c.globalAlpha=.1; c.lineWidth=2;
        c.beginPath(); c.moveTo(...project(i,-3,0)); c.lineTo(...project(i,3,0)); c.stroke();
        c.beginPath(); c.moveTo(...project(-3,i,0)); c.lineTo(...project(3,i,0)); c.stroke();
      }
      c.globalAlpha=1;
      data.forEach((d,i)=>{
        const x=i-(n-1)/2,y=-x+(i%2)*.45;
        const rise=p(m,h,.1+i*.025,.4+i*.025);
        const height=(80+Math.abs(number(d.value))/maximum*270)*rise;
        const a=project(x-.5,y-.5,0),b=project(x+.5,y-.5,0),d0=project(x-.5,y+.5,0),e=project(x+.5,y+.5,0);
        const top=q=>[q[0],q[1]-height];
        polygon(c,[d0,e,top(e),top(d0)],accent);
        polygon(c,[b,e,top(e),top(b)],second);
        polygon(c,[top(a),top(b),top(e),top(d0)],ink);
        c.save(); c.globalAlpha=.35; c.fillStyle=bg;
        for(let row=1;row<4;row++) { const point=project(x+.51,y, height*row/4); c.fillRect(point[0]-9,point[1]-6,12,10); }
        c.restore();
        const anchor=project(x,y,height+34);
        label(c,m,h,String(d.value),anchor[0]-80,anchor[1],160,38,'monospace');
        const foot=project(x,y,0);
        label(c,m,h,d.label,foot[0]-unit*.8,foot[1]+84,unit*1.6,30,'sans-serif');
      });
      c.restore();
    }
  };
})();
