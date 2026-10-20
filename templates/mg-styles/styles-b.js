/* Original frame-pure interpretations of general motion-design techniques.
 * No upstream code, fonts, photographs or branded compositions are included. */
(() => {
  'use strict';
  const TAU = Math.PI * 2;
  const clamp = x => Math.max(0, Math.min(1, x));
  const ease = x => { x = clamp(x); return x * x * (3 - 2 * x); };
  const cue = (m, a, b) => ease((m.phase - a) / (b - a));
  const circle = (c, x, y, r) => { c.beginPath(); c.arc(x, y, Math.max(0, r), 0, TAU); };
  function label(c, h, text, x, y, width, size, color, font = 'sans-serif') {
    c.fillStyle = color; c.textBaseline = 'middle';
    h.text(c, String(text), x, y, width, size, font);
  }
  function round(c, x, y, w, ht, r) { c.beginPath(); c.roundRect(x, y, w, ht, r); }
  function heading(c, m, h, color, x = 100, y = 130, width = 1700) {
    c.textAlign = 'left'; label(c, h, m.title, x, y, width, 76, color);
    label(c, h, m.subtitle, x, y + 90, width, 29, color);
  }
  function countItems(m) { return m.items.length ? m.items : [{label: m.subtitle || m.title, value: 0}]; }
  const styles = {
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
    },
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
    },
    hud(c, m, h) {
      const [, , accent, second] = m.palette, bg = '#08171c', ink = '#d7ffff';
      const items = countItems(m), lock = cue(m, .58, .73), color = lock > .5 ? second : accent;
      const detected = m.phase >= .44, confirmed = m.phase >= .73;
      c.save(); c.fillStyle = bg; c.fillRect(0, 0, 1920, 1080);
      c.strokeStyle = color; c.lineWidth = 1; c.globalAlpha = .12;
      for (let row = 0; row < 15; row++) for (let col = 0; col < 23; col++) {
        const x = col * 90 + row % 2 * 45, y = row * 78; c.beginPath();
        for (let k = 0; k <= 6; k++) { const a = k / 6 * TAU + Math.PI / 6, px = x + Math.cos(a) * 52, py = y + Math.sin(a) * 52; if (!k) c.moveTo(px, py); else c.lineTo(px, py); } c.stroke();
      }
      c.globalAlpha = 1; c.translate(960, 560);
      c.shadowColor = color; c.shadowBlur = 9;
      for (let r = 0; r < 4; r++) {
        c.save(); c.rotate((r % 2 ? -1 : 1) * m.phase * .7 + r);
        c.lineWidth = r ? 2 : 5; c.strokeStyle = color;
        c.beginPath(); c.arc(0, 0, 170 + r * 49, -.2, -.2 + TAU * cue(m, r * .04, .23 + r * .04)); c.stroke();
        for (let j = 0; j < 36; j++) { const a = j / 36 * TAU; c.beginPath(); c.moveTo(Math.cos(a) * (170 + r * 49), Math.sin(a) * (170 + r * 49)); c.lineTo(Math.cos(a) * (178 + r * 49), Math.sin(a) * (178 + r * 49)); c.stroke(); }
        c.restore();
      }
      const sweep = m.phase * TAU * 2;
      c.save(); c.rotate(sweep); const radar = c.createLinearGradient(0, 0, 310, 0); radar.addColorStop(0, '#00ffff00'); radar.addColorStop(1, '#00ffff55');
      c.fillStyle = radar; c.beginPath(); c.moveTo(0, 0); c.arc(0, 0, 310, -.35, 0); c.closePath(); c.fill(); c.restore();
      // Wireframe latitude/longitude hologram, then four lock brackets converge.
      c.strokeStyle = color; c.lineWidth = 2;
      for (let j = -2; j <= 2; j++) { c.beginPath(); c.ellipse(0, j * 46, Math.sqrt(1 - (j / 3) ** 2) * 137, 18, 0, 0, TAU); c.stroke(); c.beginPath(); c.ellipse(0, 0, 24 + (j + 2) * 25, 137, m.phase * .5, 0, TAU); c.stroke(); }
      c.shadowBlur = 0;
      // Freeze the actual detected position; no teleport at the state boundary.
      const targetPhase = Math.min(m.phase,.44);
      const targetX = 180 * Math.sin(targetPhase * 9), targetY = 130 * Math.cos(targetPhase * 7);
      c.fillStyle = color; circle(c, targetX, targetY, detected ? 9 : 5); c.fill();
      if (detected) { c.beginPath(); c.moveTo(targetX - 25, targetY); c.lineTo(targetX + 25, targetY); c.moveTo(targetX, targetY - 25); c.lineTo(targetX, targetY + 25); c.stroke(); }
      const size = 225 + 100 * (1 - lock);
      c.lineWidth = 7;
      for (const sx of [-1, 1]) for (const sy of [-1, 1]) { c.beginPath(); c.moveTo(targetX + sx * (size - 50), targetY + sy * size); c.lineTo(targetX + sx * size, targetY + sy * size); c.lineTo(targetX + sx * size, targetY + sy * (size - 50)); c.stroke(); }
      c.restore(); c.save(); heading(c, m, h, ink, 100, 110, 1700);
      items.forEach((item, i) => {
        const y = 350 + i * Math.min(95, 550 / items.length), p = cue(m, .16 + i * .025, .4 + i * .025);
        c.textAlign = 'left'; label(c, h, item.label, 100, y, 420, 27, color, 'monospace');
        const activeFrames = Math.max(1, m.frames - 1 - (m.hold_frames || 0));
        const settleFrame = Math.round((.4 + i * .025) * activeFrames);
        const phaseFrame = Math.round(m.phase * activeFrames);
        const rolling = p < 1 && phaseFrame < settleFrame && phaseFrame >= settleFrame - 5;
        const settled = rolling ? ((m.seed + i * 313 + m.f * 71) % 997) : Math.round(item.value * p);
        c.textAlign = 'right'; label(c, h, settled, 1780, y, 370, 38, ink, 'monospace');
        c.fillStyle = color; c.fillRect(1450, y + 28, p * Math.min(330, 35 + Math.abs(item.value) * 2), 4);
      });
      const status = confirmed ? 'SIGNAL CONFIRMED' : detected ? 'SIGNAL DETECTED / LOCKING' : 'SEARCHING / ANALYSING SIGNAL';
      const stateStart = confirmed ? .73 : detected ? .44 : 0;
      const typed = status.slice(0, Math.floor(cue(m, stateStart, Math.min(1, stateStart + .12)) * status.length));
      c.textAlign = 'center'; label(c, h, typed + (Math.floor(m.f / 12) % 2 ? ' _' : ''), 960, 1010, 1400, 30, color, 'monospace');
      c.globalAlpha = .035; c.fillStyle = '#ffffff'; for (let y = 0; y < 1080; y += 5) c.fillRect(0, y, 1920, 1); c.restore();
    },
    collage(c, m, h) {
      const [bg, ink, accent, second] = m.palette, items = countItems(m);
      const active = Math.max(1,m.frames-1-(m.hold_frames||0));
      // Stop-motion holds quantize time, while the last active frame still
      // reaches the ending. Pacing is applied after this time sampling.
      const stepFrame = m.f>=active ? active : Math.floor(m.f / 5)*5;
      const stepPhase = Math.pow(stepFrame/active,m.pace);
      const motion = {...m, f: stepFrame, phase: stepPhase};
      c.save(); c.fillStyle = bg; c.fillRect(0, 0, 1920, 1080);
      // Deterministic paper fibres stay fixed in space rather than flickering.
      c.fillStyle = '#00000009'; for (let i = 0; i < 480; i++) c.fillRect((i * 491 + m.seed * 13) % 1920, (i * 137) % 1080, 1 + i % 5, 1);
      c.save(); c.translate(100, 110); c.rotate(-.025);
      c.fillStyle = ink; c.fillRect(-15, -20, 1730 * cue(m, 0, .13), 150);
      c.textAlign = 'left'; label(c, h, m.title, 25, 55, 1630, 94, bg, 'serif'); c.restore();
      const cols = Math.min(4, items.length), rows = Math.ceil(items.length / cols);
      const w = 1620 / cols, ht = 580 / rows;
      items.forEach((item, i) => {
        const p = cue(motion, .12 + i / items.length * .42, .27 + i / items.length * .42);
        c.save(); c.translate(175 + (i % cols) * w + w / 2, 370 + Math.floor(i / cols) * ht + ht / 2 + (1 - p) * 90);
        c.rotate((i % 2 ? 1 : -1) * (.055 + (1 - p) * .2)); c.scale(.9 + .1 * p, .9 + .1 * p);
        c.shadowColor = '#00000030'; c.shadowBlur = 12; c.shadowOffsetY = 8;
        c.fillStyle = i % 2 ? '#fffdf1' : '#e7e4d5';
        c.beginPath(); c.moveTo(-w * .45, -ht * .4);
        for (let j = 0; j <= 15; j++) c.lineTo(-w * .45 + j / 15 * w * .9, -ht * .4 + (j % 2) * 7);
        c.lineTo(w * .45, ht * .4); c.lineTo(-w * .45, ht * .4); c.closePath(); c.fill(); c.shadowColor = 'transparent';
        // Oversized head and independently hinged jaw/arms form an original cut-paper puppet.
        const unit = Math.min(w * .23, ht * .24), nod = Math.sin(stepPhase * 18 + i) * .12;
        c.save(); c.translate(-w * .2, -ht * .02); c.scale(unit / 90, unit / 90);
        c.strokeStyle = '#ffffff'; c.lineWidth = 7; c.fillStyle = i % 2 ? accent : second;
        c.save(); c.rotate(nod); round(c, -37, -10, 74, 110, 8); c.fill(); c.stroke();
        for (const side of [-1, 1]) { c.save(); c.translate(side * 35, 12); c.rotate(side * (.5 + nod)); round(c, -12, 0, 24, 83, 7); c.fill(); c.stroke(); c.restore(); } c.restore();
        c.fillStyle = ink; c.beginPath(); c.ellipse(0, -66, 66, 54, -.08, 0, TAU); c.fill(); c.stroke();
        c.fillStyle = '#fffdf1'; circle(c, -20, -74, 8); c.fill(); circle(c, 20, -74, 8); c.fill();
        c.save(); c.translate(-30, -42); c.rotate((Math.floor(stepFrame / 5) + i) % 3 * .12); c.fillStyle = second; c.fillRect(0, 0, 60, 16); c.restore();
        c.restore();
        // Printed halftone shadows remain on the paper, separate from puppet joints.
        c.fillStyle = '#00000025'; for (let hy = 0; hy < 6; hy++) for (let hx = 0; hx < 10; hx++) { circle(c, w * .08 + hx * w * .025, -ht * .26 + hy * ht * .035, 1.4); c.fill(); }
        c.strokeStyle = ink; c.lineWidth = 5; c.beginPath(); c.moveTo(-w * .28, ht * .05); c.lineTo(w * .28, -ht * .12); c.stroke();
        c.textAlign = 'center'; label(c, h, item.value, w * .16, -10, w * .42, Math.min(80, ht * .23), ink, 'serif');
        label(c, h, item.label, 0, ht * .26, w * .74, Math.min(38, ht * .15), ink, 'serif');
        // A tape strip holds each cut-out in a continuous editorial spread.
        c.fillStyle = '#e3c566aa'; c.fillRect(-w * .16, -ht * .44, w * .32, 27); c.restore();
      });
      c.textAlign = 'left'; label(c, h, m.subtitle, 110, 1000, 1640, 36, ink, 'serif');
      c.fillStyle = accent; c.fillRect(110, 930, 180 + cue(m, .7, .9) * 1460, 8);
      // A moving torn paper edge reveals the final editorial summary, without a crossfade.
      const turn = cue(motion, .70, .84);
      if (turn > 0) {
        const edge = 1920 - turn * 1920; c.fillStyle = '#fffdf1'; c.beginPath(); c.moveTo(1920, 285);
        for (let j = 0; j <= 20; j++) c.lineTo(edge + (j % 2 ? 12 : 0), 285 + j / 20 * 610);
        c.lineTo(1920, 895); c.closePath(); c.fill();
        c.strokeStyle = '#00000035'; c.lineWidth = 4; c.beginPath(); c.moveTo(edge, 285); c.lineTo(edge, 895); c.stroke();
        c.save(); c.beginPath(); c.rect(edge + 24, 295, 1920 - edge - 24, 590); c.clip(); c.textAlign = 'left';
        label(c, h, m.title, 160, 430, 1550, 80, ink, 'serif');
        items.forEach((item, i) => { const y = 555 + i * Math.min(80, 270 / items.length); label(c, h, item.label, 170, y, 1100, Math.min(38, 240 / items.length), ink, 'serif'); c.textAlign = 'right'; label(c, h, item.value, 1730, y, 320, Math.min(48, 270 / items.length), accent, 'serif'); c.textAlign = 'left'; }); c.restore();
      } c.restore();
    },
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
  window.MG_STYLES = { ...(window.MG_STYLES || {}), ...styles };
})();
