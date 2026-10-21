/* Original frame-driven Canvas prototype; not the HTML or media renderer. */
(() => {
  'use strict';
  const {TAU, clamp, ease, cue, circle, label, round, heading, countItems} = window.MG_PROTOTYPE_HELPERS.b;
  window.MG_STYLES = {...(window.MG_STYLES || {}),
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
    }
  };
})();
