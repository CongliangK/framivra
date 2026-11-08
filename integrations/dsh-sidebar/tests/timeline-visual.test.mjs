import test from 'node:test'
import assert from 'node:assert/strict'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { VisualTimeline } from '../src/client/timeline.mjs'

const h = React.createElement
const version = `sha256:${'a'.repeat(64)}`
const snapshot = { content_version: version, coverage: { complete: true }, diagnostics: [], document: { project: { name: '可视化回归', duration_frames: 100, fps: 30 } } }
const tracks = [
  { family: 'video', track_index: 0, address: '/t/0', data: { name: '主视频' } },
  { family: 'video', track_index: 1, address: '/t/1', data: { kind: 'gfx', name: '动效' } },
  { family: 'audio', track_index: 2, address: '/t/2', data: { name: '旁白' } },
  { family: 'subtitles', track_index: 3, address: '/t/3', data: { name: '字幕' } },
]
const blocks = tracks.flatMap((track, i) => [[0, 20], [40, 100]].map((range, block_index) => ({ family: track.family, track_index: track.track_index, block_index, address: `${track.address}/b/${block_index}`, range, data: { name: `${track.family}-${block_index}` } })))
const result = { content_version: version, coverage: snapshot.coverage, diagnostics: [], tracks, blocks }

async function mount(t) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://owner/' })
  globalThis.window = dom.window
  globalThis.document = dom.window.document
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const { createRoot } = await import('react-dom/client')
  const root = createRoot(document.getElementById('root'))
  t.after(async () => { await act(async () => root.unmount()); dom.window.close(); delete globalThis.window; delete globalThis.document })
  return { root, dom }
}

test('visual timeline: fixed header column grid aligns ruler with clip rows; family classes and CSS contract present', async t => {
  const { root } = await mount(t)
  await act(async () => root.render(h(VisualTimeline, { snapshot, result, frame: 10, onFrame: () => {} })))
  // Grid contract: ruler row and every track share the same two-column template.
  const cssNode = document.querySelector('.fv-timeline style')
  const css = cssNode.textContent
  assert(css.includes('.fv-timeline'))
  assert(css.includes('grid-template-columns:var(--fv-tl-head-w,112px) minmax(0,1fr)'))
  // Container query: the timeline is its own inline-size container; narrow layout
  // retargets the grid columns directly (a container cannot size its own custom var).
  assert(css.includes('container:timeline/inline-size'))
  assert(css.includes('@container timeline (max-width:520px)'))
  assert(css.includes('var(--fv-tl-head-w-narrow,84px)'))
  assert(!css.includes('@media (max-width:520px)'))
  // Sticky ruler keeps an opaque surface so scrolling clips never show through.
  assert(css.includes('.fv-ruler-row{') || /fv-ruler-row\{[^}]*position:sticky/.test(css))
  assert(/fv-ruler-row\{[^}]*position:sticky/.test(css))
  assert(css.includes('var(--fv-tl-surface,var(--fv-surface,#202125))'))
  assert.equal(document.querySelector('link[rel="stylesheet"]'), null)
  const rulerRow = document.querySelector('.fv-ruler-row')
  const ruler = document.querySelector('.fv-ruler')
  assert(rulerRow, 'ruler row exists')
  assert(rulerRow.contains(ruler))
  const trackGrids = [...document.querySelectorAll('.fv-track')]
  assert.equal(trackGrids.length, tracks.length)
  for (const track of trackGrids) assert(track.contains(track.querySelector('.fv-track-head')))
  // Ruler and lane rows are second-column siblings: same parent chain, no horizontal margins class.
  const lanes = document.querySelectorAll('.fv-lanes .fv-row')
  assert.equal(lanes.length, 4) // displayLanes is greedy: [0,20) and [40,100) never overlap -> one lane per track.
  // Family color classes cover every family.
  for (const family of ['fv-video', 'fv-gfx', 'fv-audio', 'fv-subtitles']) {
    assert(document.querySelector(`.fv-clip.${family}`), `clip class ${family} present`)
  }
  for (const token of ['--fv-tl-accent', '--fv-tl-video', '--fv-tl-gfx', '--fv-tl-audio', '--fv-tl-subs', '--fv-tl-playhead', '--fv-tl-hit']) assert(css.includes(token), `token ${token} in css`)
})

test('visual timeline: keyboard/button accessible names, compact status bar, help details, selected count', async t => {
  const { root } = await mount(t)
  const frames = []
  await act(async () => root.render(h(VisualTimeline, { snapshot, result, frame: 10, onFrame: value => frames.push(value) })))
  const button = text => [...document.querySelectorAll('button')].find(node => node.textContent === text)
  for (const name of ['适配全片', '放大', '缩小', '向前平移', '向后平移', '前一帧', '后一帧']) assert(button(name), `button ${name} present`)
  assert(button('后一帧').disabled === false)
  button('后一帧').click()
  await act(async () => {})
  assert.deepEqual(frames.slice(-1), [11])
  assert.equal(document.querySelector('.fv-ruler').getAttribute('aria-label'), '固定播放头帧标尺')
  assert(document.querySelector('[aria-label="显示范围"]'))
  assert(document.querySelector('[aria-label="活动引用"]').textContent.includes('活动引用：无'))
  assert(document.body.textContent.includes('悬停帧：—'))
  assert(document.body.textContent.includes('已选 0'))
  assert(document.body.textContent.includes('播放头 10'))
  const help = document.querySelector('details.fv-help')
  assert(help && help.textContent.includes('kernel range'))
  // Heading stays a single compact line; explanatory paragraphs live only inside the details.
  const h3 = document.querySelector('.fv-timeline h3')
  assert.equal(h3.textContent, '可视化回归')
  await act(async () => button('放大').click())
  assert(document.querySelector('[aria-label="可见帧范围"]').textContent.includes('帧窗口 [25, 75)'))
})

test('visual timeline: multi-selection count reflects ctrl-clicked clips via uncontrolled intent', async t => {
  const { root, dom } = await mount(t)
  await act(async () => root.render(h(VisualTimeline, { snapshot, result })))
  const clips = [...document.querySelectorAll('.fv-clip')]
  await act(async () => clips[0].click())
  await act(async () => clips[2].dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true, ctrlKey: true })))
  assert.equal(document.querySelectorAll('.fv-clip[aria-pressed="true"]').length, 2)
  assert(document.body.textContent.includes('已选 2'))
})
