/* Original frame-driven Canvas prototype; not the HTML or media renderer. */
(() => {
  'use strict';
  const {TAU, clamp, ease, cue, circle, label, round, heading, countItems} = window.MG_PROTOTYPE_HELPERS.b;
  window.MG_STYLES = {...(window.MG_STYLES || {}),
    sticker(c, m, h) {
      const [bg, ink, accent, second] = m.palette, items = countItems(m), n = items.length;
      c.save(); c.fillStyle = bg; c.fillRect(0, 0, 1920, 1080);
      const step = 700, world = Math.max(1600, n * step), pull = cue(m, .72, .9);
      const zoom = 1 - pull * (1 - Math.min(1, 1620 / world));
      const target = 350 + Math.max(0, n - 1) * step * cue(m, .1, .68);
      c.translate(960, 560); c.scale(zoom, zoom); c.translate(-target * (1 - pull) - world / 2 * pull, 0);
      c.lineWidth = 8; c.strokeStyle = ink;
      c.beginPath(); c.moveTo(350, 0); c.lineTo(350 + (n - 1) * step, 0); c.stroke();
      const maximum = Math.max(1, ...items.map(x => Math.abs(x.value)));
      items.forEach((item, i) => {
        const x = 350 + i * step, p = cue(m, .04 + i / Math.max(1, n) * .5, .16 + i / Math.max(1, n) * .5);
        c.save(); c.translate(x, 0); c.translate(0, (1 - p) * 180); c.scale(.65 + .35 * p, .65 + .35 * p);
        c.shadowColor = '#00000025'; c.shadowBlur = 22; c.shadowOffsetY = 15;
        c.fillStyle = '#ffffff'; round(c, -225, -195, 450, 390, 45); c.fill(); c.shadowColor = 'transparent';
        c.fillStyle = i % 2 ? second : accent; round(c, -207, -177, 414, 354, 34); c.fill();
        const barHeight = Math.abs(item.value) / maximum * 140 * p;
        c.fillStyle = ink; c.fillRect(-135, item.value < 0 ? 0 : -barHeight, 70, barHeight);
        c.strokeStyle = ink; c.lineWidth = 2; c.beginPath(); c.moveTo(-155, 0); c.lineTo(-45, 0); c.stroke();
        c.textAlign = 'left'; label(c, h, Math.round(item.value * p), -30, -30, 200, 74, ink);
        label(c, h, item.label, -170, 260, 420, 37, ink);
        c.strokeStyle = ink; c.lineWidth = 4; c.beginPath(); c.moveTo(-125, 195); c.lineTo(-125, 225); c.lineTo(-170, 225); c.stroke();
        c.restore();
      }); c.restore();
      c.save(); heading(c, m, h, ink); c.textAlign = 'right';
      label(c, h, String(n).padStart(2, '0'), 1800, 915, 300, 100, accent);
      label(c, h, 'CONNECTED IDEAS', 1800, 1000, 600, 24, ink, 'monospace'); c.restore();
    }
  };
})();
