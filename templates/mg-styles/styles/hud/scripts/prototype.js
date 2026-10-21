/* Original frame-driven Canvas prototype; not the HTML or media renderer. */
(() => {
  'use strict';
  const {TAU, clamp, ease, cue, circle, label, round, heading, countItems} = window.MG_PROTOTYPE_HELPERS.b;
  window.MG_STYLES = {...(window.MG_STYLES || {}),
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
    }
  };
})();
