/* Original frame-driven Canvas prototype; not the HTML or media renderer. */
(() => {
  'use strict';
  const {TAU, clamp, ease, cue, circle, label, round, heading, countItems} = window.MG_PROTOTYPE_HELPERS.b;
  window.MG_STYLES = {...(window.MG_STYLES || {}),
    'aurora-glass'(c, m, h) {
      const [bg, ink, accent, second] = m.palette;
      c.save(); c.fillStyle = bg; c.fillRect(0, 0, 1920, 1080);
      for (let i = 0; i < 5; i++) {
        const x = 300 + i * 340 + Math.sin(m.phase * 1.8 + i) * 140, y = 510 + Math.cos(m.phase * 2 + i * 1.8) * 300;
        const glow = c.createRadialGradient(x, y, 0, x, y, 620);
        glow.addColorStop(0, i % 2 ? accent : second); glow.addColorStop(1, bg);
        c.globalAlpha = .42; c.fillStyle = glow; c.fillRect(0, 0, 1920, 1080);
      }
      c.globalAlpha = 1;
      const items = countItems(m), cols = Math.min(3, items.length), rows = Math.ceil(items.length / cols), w = 1560 / cols, ht = 500 / rows;
      items.forEach((item, i) => {
        const p = cue(m, .08 + i / items.length * .24, .38 + i / items.length * .24);
        const x = 180 + i % cols * w, y = 360 + Math.floor(i / cols) * ht + (1 - p) * 22 + Math.sin(m.phase * 3 + i) * 5;
        c.save(); c.globalAlpha = .4 + .6 * p;
        const frost = document.createElement('canvas'); frost.width = Math.ceil(w - 28); frost.height = Math.ceil(ht - 28);
        const fc = frost.getContext('2d'); fc.filter = 'blur(22px)';
        fc.drawImage(c.canvas, x - 35, y - 35, w + 42, ht + 42, -35, -35, w + 42, ht + 42);
        c.save(); round(c, x, y, w - 28, ht - 28, 30); c.clip(); c.drawImage(frost, x, y); c.restore();
        const glass = c.createLinearGradient(x, y, x + w, y + ht); glass.addColorStop(0, '#ffffff30'); glass.addColorStop(1, '#ffffff08');
        c.fillStyle = glass; c.strokeStyle = '#ffffff65'; c.lineWidth = 2; round(c, x, y, w - 28, ht - 28, 30); c.fill(); c.stroke();
        c.textAlign = 'left'; label(c, h, item.label, x + 32, y + 52, w - 90, Math.min(32, ht * .2), ink);
        label(c, h, item.value, x + 32, y + ht * .6, w - 90, Math.min(75, ht * .3), ink);
        c.strokeStyle = '#ffffff70'; c.lineWidth = 3; c.beginPath();
        for (let j = 0; j < 25; j++) { const px = x + 32 + j / 24 * (w - 95), py = y + ht - 55 + Math.sin(j * .5 + m.phase * 3 + i) * 8; if (!j) c.moveTo(px, py); else c.lineTo(px, py); } c.stroke(); c.restore();
      });
      // Real magnification of the already painted scene, bounded by a glass rim.
      const lx = 340 + cue(m, .35, .83) * 1230, ly = 610 + Math.sin(m.phase * Math.PI) * 110, r = 130;
      const sample = document.createElement('canvas'); sample.width = sample.height = 260;
      sample.getContext('2d').drawImage(c.canvas, lx - r / 1.2, ly - r / 1.2, 2 * r / 1.2, 2 * r / 1.2, 0, 0, 260, 260);
      c.save(); circle(c, lx, ly, r); c.clip(); c.drawImage(sample, lx - r, ly - r); c.restore();
      c.strokeStyle = '#ffffffaa'; c.lineWidth = 3; circle(c, lx, ly, r); c.stroke();
      c.strokeStyle = '#ffffff55'; c.lineWidth = 7; c.beginPath(); c.arc(lx - 2, ly - 2, r - 7, Math.PI, Math.PI * 1.65); c.stroke();
      // Fixed, seed-addressed dither prevents temporal noise from becoming shimmer.
      c.fillStyle = '#ffffff'; c.globalAlpha = .035;
      for (let i = 0; i < 2600; i++) c.fillRect((i * 733 + m.seed * 17) % 1920, (i * 389 + m.seed * 7) % 1080, 1, 1);
      c.globalAlpha = 1;
      c.textAlign = 'left'; label(c, h, m.title, 180, 140, 1560, 84, ink); label(c, h, m.subtitle, 184, 242, 1550, 31, ink);
      c.restore();
    }
  };
})();
