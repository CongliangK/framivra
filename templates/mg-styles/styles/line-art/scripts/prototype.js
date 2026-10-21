/* Original frame-driven Canvas prototype; not the HTML or media renderer. */
(() => {
  'use strict';
  const {TAU, circle, polygon, p, colors, label, items, number} = window.MG_PROTOTYPE_HELPERS.a;
  window.MG_STYLES = {...(window.MG_STYLES || {}),
    'line-art'(c,m,h) {
      const [bg,ink,accent]=colors(m);
      c.save(); c.fillStyle=bg; c.fillRect(0,0,1920,1080);
      c.strokeStyle=ink; c.lineWidth=5; c.lineCap='round';
      const data=items(m), n=data.length;
      const points=[];
      for(let i=0;i<=500;i++) {
        const u=i/500, x=130+1660*u;
        const y=560+170*Math.sin(u*TAU*2.0)*Math.sin(u*Math.PI);
        points.push([x,y]);
      }
      const q=p(m,h,.03,.62), count=Math.max(1,Math.floor(q*500));
      c.beginPath(); points.slice(0,count+1).forEach(([x,y],i)=>i?c.lineTo(x,y):c.moveTo(x,y)); c.stroke();
      const tip=points[count]; circle(c,...tip,11,accent);
      data.forEach((d,i)=>{
        const u=(i+.5)/Math.max(1,n), point=points[Math.round(u*500)], show=p(m,h,.12+u*.45,.22+u*.45);
        c.save(); c.globalAlpha=show;
        c.beginPath(); c.moveTo(...point); c.lineTo(point[0],point[1]+(i%2?95:-95)*show); c.stroke();
        circle(c,...point,8,bg); circle(c,...point,8);
        label(c,m,h,d.label,point[0]-680/Math.max(1,n),point[1]+(i%2?150:-125),1360/Math.max(1,n),40,'serif');
        c.restore();
      });
      label(c,m,h,m.title,130,185,1650,112,'serif');
      label(c,m,h,m.subtitle,135,961,1620,40,'serif');
      c.strokeStyle=accent; c.lineWidth=2; c.beginPath(); c.moveTo(135,230); c.lineTo(135+1650*p(m,h,.65,.85),230); c.stroke();
      c.restore();
    }
  };
})();
