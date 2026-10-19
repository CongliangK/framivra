#!/usr/bin/env node
'use strict';
// Project preparation only. The product consumes static SVG, never this JavaScript.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {spawnSync} = require('node:child_process');
const {renderFrame} = require('./renderer.js');
const ROOT = __dirname;
const readJSON = p => JSON.parse(fs.readFileSync(path.join(ROOT, p), 'utf8'));
const sha256 = b => crypto.createHash('sha256').update(b).digest('hex');
const PLAN = readJSON('motion-plan.json');
const FONT_LOCK = readJSON('fonts.lock.json');

function manifest() {
  const p = PLAN.project;
  return {format: 'framivra-svg-sequence/v1', fps: p.fps, width: p.width,
    height: p.height, frame_count: p.duration_frames, generator: 'build_demo.js',
    fonts: FONT_LOCK.fonts.map(f => f.path),
    frames: Array.from({length: p.duration_frames}, (_, f) =>
      `gfx/frames-svg/f${String(f).padStart(4, '0')}.svg`)};
}

function resolveFonts(fontsDir) {
  return FONT_LOCK.fonts.map(font => {
    const candidates = fontsDir ? [path.join(fontsDir, path.basename(font.path))]
      : [path.join(ROOT, font.path), font.system_path_hint];
    const source = candidates.find(p => fs.existsSync(p) &&
      sha256(fs.readFileSync(p)) === font.sha256);
    if (!source) throw new Error(`Missing pinned font ${path.basename(font.path)} ` +
      `(${font.sha256}). Supply existing font files with --fonts-dir DIR; no download is attempted.`);
    return {...font, source};
  });
}

function verifyVendor() {
  const lock = readJSON('vendor/scoreweft/SOURCES.json');
  for (const [file, hash] of Object.entries(lock.files)) {
    if (sha256(fs.readFileSync(path.join(ROOT, 'vendor/scoreweft', file))) !== hash)
      throw new Error(`Vendored Scoreweft source mismatch: ${file}`);
  }
}

function write(file, contents) {
  const dest = path.join(ROOT, file);
  fs.mkdirSync(path.dirname(dest), {recursive: true});
  fs.writeFileSync(dest, contents);
}

function prepareGraphics(fonts) {
  const m = manifest();
  for (const font of fonts) {
    const dest = path.join(ROOT, font.path);
    if (path.resolve(font.source) !== path.resolve(dest)) write(font.path, fs.readFileSync(font.source));
  }
  const hashes = {};
  for (let f = 0; f < m.frame_count; f++) {
    const svg = renderFrame(f, PLAN) + '\n';
    if (/NaN|Infinity|<script\b|<foreignObject\b|<animate\b|\bhref\s*=/i.test(svg))
      throw new Error(`Unexpected non-static SVG at frame ${f}`);
    write(m.frames[f], svg);
    hashes[m.frames[f]] = sha256(svg);
  }
  write('gfx/demo.frames.json', JSON.stringify(m, null, 2) + '\n');
  return hashes;
}

function prepareAudio(python) {
  const output = path.join(ROOT, '.generated/audio');
  const result = spawnSync(python, [path.join(ROOT, 'audio/build.py'), '--out', output], {
    cwd: ROOT, stdio: 'inherit', timeout: 420000,
    env: {...process.env, PYTHONDONTWRITEBYTECODE: '1', BGM_NO_PARALLEL: '1',
      OMP_NUM_THREADS: '1', OPENBLAS_NUM_THREADS: '1'}});
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`Audio preparation failed (${result.status ?? result.signal})`);
  for (const name of ['bed-premaster.wav', 'content-cue.wav'])
    write(`assets/audio/${name}`, fs.readFileSync(path.join(output, 'native-inputs', name)));
}

function main(args) {
  let mode = 'help', graphicsOnly = false, fontsDir, python = 'python3';
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (['--prepare', '--check', '--help'].includes(a)) {
      if (mode !== 'help') throw new Error('Choose one of --prepare or --check');
      mode = a.slice(2);
    } else if (a === '--graphics-only') graphicsOnly = true;
    else if (a === '--fonts-dir' || a === '--python') {
      if (!args[i + 1] || args[i + 1].startsWith('--')) throw new Error(`${a} needs a value`);
      if (a === '--fonts-dir') fontsDir = path.resolve(args[++i]);
      else python = args[++i];
    } else throw new Error(`Unknown option: ${a}`);
  }
  if (mode === 'help') {
    console.log('Read-only preflight: node build_demo.js --check [--fonts-dir DIR]\n' +
      'Write generated inputs: node build_demo.js --prepare [--fonts-dir DIR] [--python python3]\n' +
      'Graphics-only preparation: add --graphics-only (audio remains unprepared)');
    return;
  }
  const fonts = resolveFonts(fontsDir);
  verifyVendor();
  if (mode === 'check') {
    console.log(JSON.stringify({status: 'INPUTS_AVAILABLE', node: process.version,
      fonts: fonts.map(f => ({path: f.path, source: f.source, sha256: f.sha256})),
      audio_dependencies: 'Python 3 + NumPy + FFmpeg required; not probed by this read-only check',
      native_render: 'UNRUN', human_review: 'UNRUN'}, null, 2));
    return;
  }
  const frames = prepareGraphics(fonts);
  if (!graphicsOnly) prepareAudio(python);
  const sources = ['build_demo.js', 'renderer.js', 'motion-plan.json', 'fonts.lock.json',
    'audio/build.py', 'vendor/scoreweft/SOURCES.json', 'timeline.json'];
  write('.generated/preparation.json', JSON.stringify({
    status: graphicsOnly ? 'GRAPHICS_PREPARED_AUDIO_NOT_BUILT' : 'INPUTS_PREPARED',
    node: process.version, frames, fonts: fonts.map(f => ({path: f.path, sha256: f.sha256})),
    sources: Object.fromEntries(sources.map(p => [p, sha256(fs.readFileSync(path.join(ROOT, p)))])),
    audio: graphicsOnly ? 'UNRUN' : 'PREMASTER_INPUTS_ONLY',
    native_render: 'UNRUN', human_review: 'UNRUN'}, null, 2) + '\n');
  console.log(`${manifest().frame_count} static SVG frames prepared; ` +
    (graphicsOnly ? 'audio not prepared.' : 'premaster bed and reusable cue prepared.') +
    ' Native render and human review remain UNRUN.');
}

module.exports = {manifest, prepareGraphics, resolveFonts, verifyVendor};
if (require.main === module) {
  try {main(process.argv.slice(2));}
  catch (error) {console.error(error.message); process.exitCode = 1;}
}
