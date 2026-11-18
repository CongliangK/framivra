#!/usr/bin/env node
'use strict';
// Node 18+ standard library only. Generated projects do not execute this builder.
const fs = require('node:fs');
const path = require('node:path');

// Preserve the locked frame and continuous backdrop; scale only the artwork
// into the area above the native subtitle band. Used verbatim by both routes.
function renderPromo(frame, plan, renderFrame) {
  const svg = renderFrame(frame, plan);
  const backdrop = /<\/desc><rect x="0" y="0" width="1920" height="1080"[^>]*\/>/.exec(svg);
  if (!backdrop || !svg.endsWith('</svg>')) throw new Error('Unexpected locked SVG frame structure');
  const art = backdrop.index + backdrop[0].length;
  return svg.slice(0, art) + '<g transform="translate(96 6) scale(0.9)">' + svg.slice(art, -6) + '</g></svg>';
}

// This very function's source is embedded verbatim in HTML; Node calls it for SVG.
// Opaque paper badge with the original coral picture / ink type / blue sound palette.
// Rectangles only, no fonts, filters, scripts, images or external SVG resources.
function renderCue(local) {
  const step = local % 24;
  const width = 44 + 2 * (step <= 12 ? step : 24 - step);
  return '<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">' +
    '<rect x="1576" y="80" width="224" height="112" rx="20" fill="#20282D"/>' +
    '<rect x="1580" y="84" width="216" height="104" rx="17" fill="#F6F1E6"/>' +
    '<rect x="1600" y="106" width="48" height="48" rx="24" fill="#F26145"/>' +
    '<rect x="1664" y="106" width="24" height="48" rx="4" fill="#20282D"/>' +
    '<rect x="1708" y="118" width="12" height="24" rx="6" fill="#3C5EDA"/>' +
    '<rect x="1728" y="102" width="12" height="56" rx="6" fill="#3C5EDA"/>' +
    '<rect x="1748" y="118" width="12" height="24" rx="6" fill="#3C5EDA"/>' +
    '<rect x="1600" y="168" width="' + width + '" height="4" rx="2" fill="#3C5EDA"/></svg>';
}

function main(args) {
  if (args.length !== 2 || !path.isAbsolute(args[0]) || !['html', 'svg'].includes(args[1]))
    throw new Error('Usage: node graphics.js <project absolute path> <html|svg>');
  const [root, mode] = args;
  const read = name => fs.readFileSync(path.join(root, name));
  // The prepare command verifies the shared source lock and copied font bytes.
  const plan = JSON.parse(read('motion-plan.json'));
  const fonts = JSON.parse(read('fonts.lock.json')).fonts;
  const fontPaths = fonts.map(font => font.path);
  const spec = JSON.parse(read('gfx/spec.json'));
  if (!spec || typeof spec !== 'object' || Array.isArray(spec)) throw new Error('spec must be an object');
  for (const [key, clip] of Object.entries(spec)) {
    if (!/^[a-zA-Z0-9][a-zA-Z0-9_-]*$/.test(key) || !clip ||
        !Number.isSafeInteger(clip.start) || clip.start < 0 ||
        !Number.isSafeInteger(clip.frames) || clip.frames < 1 ||
        !['promo', 'cue'].includes(clip.role) ||
        (clip.role === 'promo' && clip.start + clip.frames > plan.project.duration_frames))
      throw new Error(`Invalid clip specification: ${key}`);
  }
  const {renderFrame} = require(path.join(root, 'renderer.js'));
  const write = (name, data) => {
    const dest = path.join(root, name);
    fs.mkdirSync(path.dirname(dest), {recursive: true});
    fs.writeFileSync(dest, data);
  };
  const dependencies = {};
  for (const [key, clip] of Object.entries(spec)) {
    const promo = clip.role === 'promo';
    if (mode === 'svg') {
      const frames = Array.from({length: clip.frames}, (_, local) => {
        const filename = `gfx/${key}/f${String(local).padStart(4, '0')}.svg`;
        const svg = promo ? renderPromo(clip.start + local, plan, renderFrame) : renderCue(local);
        if (/NaN|Infinity|<script\b|<foreignObject\b|<animate\b|\bhref\s*=/i.test(svg))
          throw new Error(`Non-static SVG: ${filename}`);
        write(filename, svg + '\n');
        return filename;
      });
      write(`gfx/${key}.frames.json`, JSON.stringify({format: 'framivra-svg-sequence/v1',
        fps: plan.project.fps, width: 1920, height: 1080, frame_count: clip.frames,
        fonts: promo ? fontPaths : [], frames}, null, 2) + '\n');
      dependencies[key] = []; // SVG manifests enumerate their full input closure.
    } else {
      const faces = promo ? fonts.map(({family, weight, path: fontPath}) =>
        `@font-face{font-family:"${family}";font-style:normal;font-weight:${weight};src:url("../${fontPath}");font-display:block;}`).join('\n') : '';
      // JSON is trusted and identity-checked, but still escape HTML script delimiters.
      const planJSON = JSON.stringify(plan).replace(/</g, '\\u003c');
      const drawing = promo ?
        `${renderPromo.toString()}\nconst plan=${planJSON};\nconst draw=f=>renderPromo(${clip.start}+f,plan,FramivraMotion.renderFrame);` :
        `${renderCue.toString()}\nconst draw=renderCue;`;
      write(`gfx/${key}.html`, '<!doctype html>\n<html><head><meta charset="utf-8">' +
        '<style>html,body{margin:0;width:1920px;height:1080px;overflow:hidden;background:transparent}svg{display:block}\n' + faces +
        '</style></head><body>' + (promo ? '<script src="../renderer.js"></script>' : '') +
        `<script>\n'use strict';\n${drawing}\n` +
        `window.__vtx_render=function(f){if(!Number.isInteger(f)||f<0||f>=${clip.frames})throw new Error('Frame out of range');document.getElementById('stage').innerHTML=draw(f);};\n` +
        '</script><div id="stage"></div><script>\n' +
        "const q=new URLSearchParams(location.search).get('f');window.__vtx_render(q===null?0:Number(q));\n" +
        '</script></body></html>\n');
      dependencies[key] = promo ? ['renderer.js', ...fontPaths] : [];
    }
  }
  console.log(JSON.stringify({mode, cache_dependencies: dependencies}));
}
if (require.main === module) {
  try { main(process.argv.slice(2)); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
module.exports = {main, renderCue};
