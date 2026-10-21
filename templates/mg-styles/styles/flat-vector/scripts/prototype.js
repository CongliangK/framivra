/* Original frame-driven Canvas prototype; not the HTML or media renderer. */
(() => {
  'use strict';
  const {TAU, circle, polygon, p, colors, label, items, number} = window.MG_PROTOTYPE_HELPERS.a;
  window.MG_STYLES = {...(window.MG_STYLES || {}),
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
    }
  };
})();
