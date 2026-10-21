/* Shared pure drawing and validation helpers; no player clock or scene state. */
(() => {
  "use strict";
  const txt = (c, h, s, x, y, w, size, color, lines = 1) =>
    h.text(c, s, x, y, w, size, {
      minSize: 24,
      maxLines: lines,
      height: size * 1.3 * lines,
      color,
    });
  const line = (c, pts, color, width = 4) => {
    c.strokeStyle = color;
    c.lineWidth = width;
    c.beginPath();
    pts.forEach(([x, y], i) => (i ? c.lineTo(x, y) : c.moveTo(x, y)));
    c.stroke();
  };
  const finite = (v, name, lo, hi, h) => h.num(v, name, lo, hi);
  const noise = (seed, f, i) => {
    let v = (seed ^ Math.imul(f, 374761393) ^ Math.imul(i, 668265263)) >>> 0;
    v = Math.imul(v ^ (v >>> 13), 1274126177) >>> 0;
    return ((v ^ (v >>> 16)) & 65535) / 32767.5 - 1;
  };
  const check = (d, N, h, pn = 4) => {
    h.int(N, "frames", 30, 18000);
    h.label(d.title, "title", 60);
    if (d.palette !== undefined) h.palette(d.palette, pn);
  };
  // The editorial scenes use a 3px default; the shared stroke geometry is unchanged.
  const thinLine = (c, pts, color, width = 3) => line(c, pts, color, width);
  const labelText=(c,h,s,x,y,w,size=36,color='#18243b')=>h.text(c,String(s),x,y,w,size,{
    color,
    minSize:24,
    height:size*1.3
  });
  const validateSeededTitle=(d,N,h)=>{
    h.int(N,'frames',30,18000);
    h.int(d.seed,'seed',-2147483648,2147483647);
    h.label(d.title,'title',40);
  };
  const P=['#faf5e8',
  '#202730',
  '#e66b42',
  '#49a69d'];
  function validateStoryTitle(d,N,h) {
    h.int(N,'frames',30,18000);
    h.palette(d.palette??P);
    h.label(d.title??d.mission,'title',70);
  }
  function storyHeader(c,d,h) {
    const p=d.palette??P;
    c.fillStyle=p[0];
    c.fillRect(0,0,1920,1080);
    h.text(c,d.title??d.mission,100,65,1720,62, {
      color:p[1],minSize:32
    });
    if(d.subtitle)h.text(c,d.subtitle,100,148,1720,30, {
      color:p[1],minSize:24
    });
  }
  window.MGSceneHelpers = {txt, line, finite, noise, check, thinLine, labelText, validateSeededTitle,
    storyPalette: P, validateStoryTitle, storyHeader};
})();
