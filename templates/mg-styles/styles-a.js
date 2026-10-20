/* Original procedural treatments. No upstream code, fonts, meshes or imagery.
 * Every renderer owns only its canvas state; the caller clears each frame.
 * soft-3d is a shaded 2.5D illustration, not a physically rendered 3D scene. */
(() => {
  'use strict';
  const TAU = Math.PI * 2;
  const circle = (c, x, y, r, fill) => {
    c.beginPath(); c.arc(x, y, Math.max(0.01, r), 0, TAU);
    if (fill) { c.fillStyle = fill; c.fill(); } else c.stroke();
  };
  const polygon = (c, points, fill) => {
    c.beginPath(); points.forEach(([x,y], i) => i ? c.lineTo(x,y) : c.moveTo(x,y));
    c.closePath(); c.fillStyle = fill; c.fill();
  };
  const p = (m, h, a, b) => h.ease(h.clamp((m.phase - a) / (b - a)));
  const colors = m => m.palette;
  const label = (c,m,h,str,x,y,width,size,font) => {
    c.fillStyle = colors(m)[1]; h.text(c, String(str), x,y,width,size,font);
  };
  const items = m => m.items || [];
  const number = v => Number.isFinite(Number(v)) ? Number(v) : 0;
  const styles = {
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
    },
    'isometric'(c,m,h) {
      const [bg,ink,accent,second]=colors(m);
      c.save(); c.fillStyle=bg; c.fillRect(0,0,1920,1080);
      label(c,m,h,m.title,110,155,1620,92,'sans-serif');
      label(c,m,h,m.subtitle,115,225,1600,34,'sans-serif');
      const data=items(m), n=data.length, maximum=Math.max(1,...data.map(x=>Math.abs(number(x.value))));
      const unit=Math.min(115,640/Math.max(1,n));
      const project=(x,y,z)=>[960+(x-y)*unit,680+(x+y)*unit*.5-z];
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
    },
    'flat-vector'(c,m,h) {
      const [bg,ink,accent,second]=colors(m);
      c.save(); c.fillStyle=bg; c.fillRect(0,0,1920,1080);
      const data=items(m), n=data.length;
      const back=u=>{const x=h.clamp(u)-1;return 1+2.70158*x*x*x+1.70158*x*x;};
      // A visible seed compresses before release; all parts share its local rig.
      const anticipate=Math.sin(Math.PI*h.clamp(m.phase/.055));
      const release=h.clamp((m.phase-.055)/.19);
      const expand=.55+.45*back(release);
      const flight=Math.sin(Math.PI*release);
      const sx=expand*(1+.14*anticipate-.10*flight);
      const sy=expand*(1-.16*anticipate+.12*flight);
      const center=[580,560];
      c.save(); c.translate(center[0],center[1]+24*anticipate-68*flight); c.scale(sx,sy);
      circle(c,0,0,175,accent);
      c.fillStyle=ink; c.beginPath(); c.arc(0,0,91,0,Math.PI); c.fill();
      circle(c,-45,-40,10,ink); circle(c,45,-40,10,ink); c.restore();
      data.forEach((d,i)=>{
        // One argument leads at a time. Its connector arrives before its badge.
        const start=.29+i*.075;
        const draw=p(m,h,start,start+.055);
        const local=h.clamp((m.phase-start-.045)/.075);
        const pop=Math.max(0,back(local));
        const a=-1.0 + i/Math.max(1,n-1)*2.0;
        const end=[1190+Math.cos(a)*190,550+Math.sin(a)*290];
        c.strokeStyle=ink; c.lineWidth=8; c.beginPath(); c.moveTo(...center);
        c.lineTo(center[0]+(end[0]-center[0])*draw,center[1]+(end[1]-center[1])*draw); c.stroke();
        if(local>0){
          c.save(); c.translate(...end); c.scale(1+.10*Math.sin(local*Math.PI),1-.09*Math.sin(local*Math.PI));
          circle(c,0,0,66*pop,i%2?second:accent); c.restore();
        }
        c.save(); c.globalAlpha=p(m,h,start+.09,start+.15);
        label(c,m,h,d.label,end[0]+95,end[1]+14,425,45,'sans-serif'); c.restore();
      });
      label(c,m,h,m.title,115,180,1670,105,'sans-serif');
      label(c,m,h,m.subtitle,115,967,1640,41,'sans-serif');
      // The seed's colour becomes a small travelling handoff, preserving the end card.
      const q=p(m,h,.84,.94); circle(c,130+1630*q,280,14,second);
      c.restore();
    },
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
    },
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
  window.MG_STYLES = {...(window.MG_STYLES || {}), ...styles};
})();
