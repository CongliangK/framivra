/* Original frame-driven Canvas prototype; not the HTML or media renderer. */
(() => {
  'use strict';
  const {TAU, clamp, ease, cue, circle, label, round, heading, countItems} = window.MG_PROTOTYPE_HELPERS.b;
  window.MG_STYLES = {...(window.MG_STYLES || {}),
    morph(c, m, h) {
      const [bg, ink, accent, second] = m.palette, items = countItems(m);
      c.save(); c.fillStyle = bg; c.fillRect(0, 0, 1920, 1080);
      const travel = cue(m, .12, .82) * Math.max(1, items.length - 1);
      const idx = Math.min(items.length - 1, Math.floor(travel));
      const u = items.length === 1 ? Math.min(1,travel) : travel - Math.floor(travel), bridge = Math.sin(Math.PI * u);
      // Equal angular topology keeps radial shape interpolation untangled.
      const shape = (a, k) => k % 3 === 0 ? 1 : k % 3 === 1 ? .78 + .22 * Math.cos(a * 4) : .8 + .2 * Math.cos(a * 3);
      const next = Math.min(items.length - 1, idx + 1);
      const magnitude = Math.max(1, ...items.map(item => Math.abs(item.value)));
      const currentValue = items[idx].value * (1 - ease(u)) + items[next].value * ease(u);
      const radius = 185 + 55 * Math.abs(currentValue) / magnitude + 12 * Math.sin(m.phase * TAU);
      c.translate(960 + 140 * Math.sin(m.phase * Math.PI), 510);
      c.rotate(.12 * Math.sin(u * TAU)); c.scale(1 + bridge * .12, 1 - bridge * .1);
      c.fillStyle = accent; c.beginPath();
      for (let j = 0; j <= 160; j++) {
        const a = j / 160 * TAU;
        const rA = shape(a, idx), rB = shape(a, idx + 1);
        const r = radius * ((1 - bridge) * ((1 - u) * rA + u * rB) + bridge);
        const x = Math.cos(a) * r, y = Math.sin(a) * r;
        if (!j) c.moveTo(x, y); else c.lineTo(x, y);
      }
      c.closePath(); c.fill();
      c.strokeStyle = ink; c.lineWidth = 5; c.stroke();
      c.textAlign = 'center'; label(c, h, Math.round(currentValue), 0, 0, radius * 1.35, 80, ink);
      c.strokeStyle = second; c.lineWidth = 6; c.beginPath(); c.arc(0, 0, radius * .65, -Math.PI / 2, -Math.PI / 2 + TAU * Math.abs(currentValue) / magnitude); c.stroke();
      c.restore(); c.save();
      c.textAlign = 'center';
      label(c, h, m.title, 960, 140, 1660, 80, ink);
      label(c, h, items[idx].label, 960, 835, 1500, 52, ink);
      label(c, h, m.subtitle, 960, 945, 1500, 28, ink);
      // A continuous travelling dot binds all content beats into one journey.
      c.strokeStyle = ink; c.lineWidth = 3; c.beginPath(); c.moveTo(500, 1015); c.lineTo(1420, 1015); c.stroke();
      c.fillStyle = second; circle(c, 500 + cue(m, .05, .88) * 920, 1015, 12); c.fill(); c.restore();
    }
  };
})();
