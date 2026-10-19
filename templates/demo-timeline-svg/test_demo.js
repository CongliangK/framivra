#!/usr/bin/env node
'use strict';
// Ordinary, read-only template checks. No browser, product render or security probes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {manifest, verifyVendor} = require('./build_demo.js');
const {renderFrame, stateAt} = require('./renderer.js');
const root = __dirname;
const schemaArg = process.argv.indexOf('--schema');
if (schemaArg !== -1 && (!process.argv[schemaArg + 1] || process.argv[schemaArg + 1].startsWith('--')))
  throw new Error('--schema needs a product timeline.schema.json path');
const schemaPath = schemaArg === -1 ? path.join(root, '../../vtx/timeline.schema.json')
  : path.resolve(process.argv[schemaArg + 1]);
const read = p => JSON.parse(fs.readFileSync(path.join(root, p), 'utf8'));
const plan = read('motion-plan.json'), timeline = read('timeline.json');
const lock = read('fonts.lock.json'), source = read('SOURCES.json');
const hash = p => crypto.createHash('sha256').update(fs.readFileSync(path.join(root, p))).digest('hex');
let checks = 0;
function check(name, fn) {fn(); checks++; console.log(`PASS ${name}`);}

check('frozen motion sources retained byte for byte', () => {
  assert.equal(hash('renderer.js'), source.renderer_sha256);
  assert.equal(hash('motion-plan.json'), source.motion_plan_sha256);
});
check('formal v2 timeline is separate from visual plan', () => {
  assert.equal(timeline.version, 2);
  assert.equal(plan.format, 'framivra-demo-motion-plan/v1');
  assert.deepEqual(timeline.project, plan.project);
});
check('every actual product-schema root field is explicit', () => {
  const schema = JSON.parse(fs.readFileSync(schemaPath, 'utf8'));
  for (const field of schema.required) assert(Object.hasOwn(timeline, field), field);
  assert.deepEqual(timeline.subtitles, []);
});
check('static sequence manifest is project-root-relative and clip-local', () => {
  const m = manifest(), clip = timeline.video_tracks[0].clips[0];
  assert.equal(timeline.video_tracks[0].kind, 'gfx');
  assert.equal(clip.format, 'svg-sequence');
  assert.equal(clip.src, 'gfx/demo.frames.json');
  assert.equal(clip.dst_in, 0);
  assert.equal(clip.dur, m.frame_count);
  assert.equal(m.format, 'framivra-svg-sequence/v1');
  assert.equal(m.generator, 'build_demo.js');
  assert.equal(m.fps, 30); assert.equal(m.width, 1920); assert.equal(m.height, 1080);
  assert.equal(m.frame_count, 900); assert.equal(m.frames.length, 900);
  assert.equal(new Set(m.frames).size, 900);
  assert.equal(m.frames[0], 'gfx/frames-svg/f0000.svg');
  assert.equal(m.frames[899], 'gfx/frames-svg/f0899.svg');
  assert.deepEqual(m.fonts, lock.fonts.map(f => f.path));
});
check('all generator inputs and actual fonts declared as cache dependencies', () => {
  const deps = new Set(timeline.video_tracks[0].clips[0].cache_dependencies);
  for (const file of ['build_demo.js', 'renderer.js', 'motion-plan.json', 'audio/build.py',
    'fonts.lock.json', 'vendor/scoreweft/SOURCES.json', ...manifest().fonts])
    assert(deps.has(file), file);
  for (const file of Object.keys(read('vendor/scoreweft/SOURCES.json').files))
    assert(deps.has(`vendor/scoreweft/${file}`), file);
});
check('two short cues share one source with no 13.7s or 17s trigger', () => {
  const cue = timeline.audio_tracks.find(t => t.name === 'timeline_cue');
  assert.deepEqual(cue.events, [
    {src: 'same_content_cue', src_in: 0, in: 228, out: 262, volume: 1},
    {src: 'same_content_cue', src_in: 0, in: 462, out: 496, volume: 1}]);
  assert.equal(cue.duck, false);
  assert.equal(timeline.sources.find(s => s.id === 'same_content_cue').path, 'assets/audio/content-cue.wav');
  assert.equal(timeline.sources.find(s => s.id === 'timeline_music_bed').path, 'assets/audio/bed-premaster.wav');
  assert.equal(timeline.audio_tracks.find(t => t.name === 'bed').events[0].out, 900);
});
check('fixed semantic anchors unchanged', () => {
  assert.deepEqual(plan.anchors, {json_origin: 18, arrangement_read: 135, first_hit: 228,
    rewrite: 339, old_point_pass: 411, new_hit: 462, result_carry: 510,
    result_settle: 606, brand_transfer: 720, brand_lock: 774, end: 900});
});
const frames = Array.from({length: 900}, (_, f) => renderFrame(f, plan) + '\n');
check('900 deterministic finite static SVG frames', () => {
  frames.forEach((svg, f) => {
    assert.equal(svg, renderFrame(f, plan) + '\n');
    assert(svg.startsWith('<svg xmlns="http://www.w3.org/2000/svg"'));
    assert(svg.includes('viewBox="0 0 1920 1080"'));
    assert(!/NaN|Infinity|<script\b|<foreignObject\b|<animate\b|\bhref\s*=/i.test(svg));
  });
});
check('old point passes empty and result does not retrigger cue', () => {
  for (const f of [227, 410, 411, 412, 419, 432, 461]) {
    assert(!frames[f].includes('data-role="content-title"'));
    assert(!frames[f].includes('data-role="sound-visual"'));
    assert(frames[f].includes('r="92"'));
  }
  for (const f of [228, 462]) {
    assert(frames[f].includes('data-role="content-title"'));
    assert(frames[f].includes('data-role="sound-visual"'));
  }
  for (let f = 510; f < 900; f++) assert(!frames[f].includes('data-role="sound-visual"'));
  assert.equal(stateAt(510, plan).shot, 'result');
});
check('same event artwork at both cue boundaries', () => {
  const artwork = s => s.match(/<circle[^>]*data-role="picture"[^>]*\/>/)[0];
  for (const offset of [0, 3, 7, 13])
    assert.equal(artwork(frames[228 + offset]), artwork(frames[462 + offset]));
});
check('pinned MIT synth subset matches hashes', verifyVendor);
if (process.argv.includes('--prepared')) {
  check('prepared manifest and every SVG match deterministic generator', () => {
    assert.deepEqual(read('gfx/demo.frames.json'), manifest());
    manifest().frames.forEach((p, f) => assert.equal(fs.readFileSync(path.join(root, p), 'utf8'), frames[f]));
  });
  check('prepared fonts match pinned bytes', () => {
    for (const f of lock.fonts) assert.equal(hash(f.path), f.sha256);
  });
  check('prepared PCM24 native inputs have exact size, rate and channels', () => {
    for (const [p, samples] of [['assets/audio/bed-premaster.wav', 1440000],
      ['assets/audio/content-cue.wav', 54400]]) {
      const b = fs.readFileSync(path.join(root, p));
      assert.equal(b.toString('ascii', 0, 4), 'RIFF');
      assert.equal(b.toString('ascii', 8, 12), 'WAVE');
      assert.equal(b.readUInt16LE(20), 1); assert.equal(b.readUInt16LE(22), 2);
      assert.equal(b.readUInt32LE(24), 48000); assert.equal(b.readUInt16LE(34), 24);
      assert.equal(b.toString('ascii', 36, 40), 'data');
      assert.equal(b.readUInt32LE(40), samples * 6); assert.equal(b.length, 44 + samples * 6);
      assert(b.subarray(b.length - 9600).every(x => x === 0));
    }
  });
}
console.log(`${checks} ordinary template checks passed. Native render and human review UNRUN.`);
