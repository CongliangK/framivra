import test from 'node:test'
import assert from 'node:assert/strict'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { VisualTimeline, TimelineHelp } from '../src/client/timeline.mjs'

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

test('ruler scrub ownership: first pointer owns until release; second touch and non-owner capture loss cannot hijack', async t => {
  const { root } = await mount(t)
  const frames = []
  await act(async () => root.render(h(VisualTimeline, { snapshot, result, frame: 0, onFrame: value => frames.push(value) })))
  const { Simulate } = await import('react-dom/test-utils')
  const ruler = document.querySelector('.fv-ruler')
  ruler.getBoundingClientRect = () => ({ left: 0, width: 100 })
  const pd = (id, x) => act(async () => Simulate.pointerDown(ruler, { button: 0, clientX: x, pointerId: id }))
  const pm = (id, x) => act(async () => Simulate.pointerMove(ruler, { clientX: x, pointerId: id }))
  const pu = (id, x) => act(async () => Simulate.pointerUp(ruler, { clientX: x, pointerId: id }))
  await pd(1, 50); assert.deepEqual(frames.slice(-1), [50])
  // Second touch during the scrub is ignored entirely: no pin, no ownership change.
  await pd(2, 20); await pm(2, 30)
  assert.deepEqual(frames.slice(-1), [50])
  // Non-owner's pointerCancel/lostpointercapture (implicit release after its own
  // pointerup) cannot cancel the owner's scrub.
  await act(async () => Simulate.pointerCancel(ruler, { pointerId: 2 }))
  await act(async () => Simulate.lostPointerCapture(ruler, { pointerId: 2 }))
  await pm(1, 80); assert.deepEqual(frames.slice(-1), [80])
  await pu(1, 80); assert.deepEqual(frames.slice(-1), [80])
  // After the owner releases, the next pointer acquires the scrub normally.
  await pd(2, 10); assert.deepEqual(frames.slice(-1), [10])
  await pu(2, 10)
})

test('overview strip: display-only navigation pans/zooms the window; never pins, selects, or gestures', async t => {
  const { root } = await mount(t)
  const frames = [], selections = [], gestures = []
  await act(async () => root.render(h(VisualTimeline, { snapshot, result, frame: 10, onFrame: v => frames.push(v), onSelect: v => selections.push(v), onGesture: ops => gestures.push(ops) })))
  const { Simulate } = await import('react-dom/test-utils')
  const windowText = () => document.querySelector('[aria-label="可见帧范围"]').textContent
  const button = text => [...document.querySelectorAll('button')].find(node => node.textContent === text)
  await act(async () => button('放大').click())
  assert(windowText().includes('帧窗口 [25, 75)'))
  const overview = document.querySelector('.fv-overview')
  overview.getBoundingClientRect = () => ({ left: 0, width: 200 })
  // Stored clip ranges (8 blocks) plus view window and pinned playhead markers.
  assert.equal(document.querySelectorAll('.fv-ov').length, blocks.length)
  assert(document.querySelector('.fv-ov-window'))
  assert(document.querySelector('.fv-ov-pin'))
  assert.equal(overview.getAttribute('role'), 'slider')
  assert.equal(overview.getAttribute('aria-valuenow'), '25')
  // Click at frame 25 centers the 50-frame window at [0, 50).
  await act(async () => Simulate.pointerDown(overview, { button: 0, clientX: 50, pointerId: 3 }))
  assert(windowText().includes('帧窗口 [0, 50)'))
  // A second simultaneous touch cannot hijack the navigation drag.
  await act(async () => Simulate.pointerDown(overview, { button: 0, clientX: 199, pointerId: 4 }))
  await act(async () => Simulate.pointerMove(overview, { clientX: 199, pointerId: 4 }))
  assert(windowText().includes('帧窗口 [0, 50)'))
  // Owner drag continues to steer the window.
  await act(async () => Simulate.pointerMove(overview, { clientX: 100, pointerId: 3 }))
  assert(windowText().includes('帧窗口 [25, 75)'))
  await act(async () => Simulate.lostPointerCapture(overview, { pointerId: 4 }))
  await act(async () => Simulate.pointerUp(overview, { clientX: 100, pointerId: 3 }))
  // Keyboard: quarter-span arrows, Home, End.
  await act(async () => Simulate.keyDown(overview, { key: 'ArrowRight', preventDefault() {} }))
  assert(windowText().includes('帧窗口 [38, 88)'))
  await act(async () => Simulate.keyDown(overview, { key: 'Home', preventDefault() {} }))
  assert(windowText().includes('帧窗口 [0, 50)'))
  await act(async () => Simulate.keyDown(overview, { key: 'End', preventDefault() {} }))
  assert(windowText().includes('帧窗口 [50, 100)'))
  // Pure display: playhead pinned value, selection and gesture callbacks untouched.
  assert.deepEqual(frames, [])
  assert.deepEqual(selections, [])
  assert.deepEqual(gestures, [])
  assert(document.body.textContent.includes('播放头 10'))
})

test('缩放到选区 zooms to stored ranges of selected blocks only; clip labels carry safe frame durations', async t => {
  const { root, dom } = await mount(t)
  await act(async () => root.render(h(VisualTimeline, { snapshot, result })))
  const button = text => [...document.querySelectorAll('button')].find(node => node.textContent === text)
  const zoom = button('缩放到选区')
  assert.equal(zoom.disabled, true, 'no selection yet')
  const clips = [...document.querySelectorAll('.fv-clip')]
  assert(clips[0].textContent.includes('20 帧'), 'clip shows stored range duration')
  await act(async () => clips[0].click())
  assert.equal(button('缩放到选区').disabled, false)
  await act(async () => button('缩放到选区').click())
  assert(document.querySelector('[aria-label="可见帧范围"]').textContent.includes('帧窗口 [0, 20)'), 'single block [0,20) zooms window to itself')
  // Two blocks [0,20)+[40,100) ctrl-selected: union window [0, 100). Re-fit first:
  // after the single-block zoom the other clip is outside the window and unmounted.
  await act(async () => button('适配全片').click())
  const fresh = [...document.querySelectorAll('.fv-clip')]
  await act(async () => fresh[1].dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true, ctrlKey: true })))
  await act(async () => button('缩放到选区').click())
  assert(document.querySelector('[aria-label="可见帧范围"]').textContent.includes('帧窗口 [0, 100)'))
  // Disjoint union across tracks zooms to the bounding range of stored ranges.
  await act(async () => button('放大').click())
  await act(async () => button('缩放到选区').click())
  assert(document.querySelector('[aria-label="可见帧范围"]').textContent.includes('帧窗口 [0, 100)'))
})

test('window blur / hidden tab with NO delivered cancel clears stranded owners: ruler and overview recover, block ghost is canceled', async t => {
  const { root, dom } = await mount(t)
  const frames = [], tokens = []
  // Gestures require managed block ids.
  const editableBlocks = blocks.map((block, index) => ({ ...block, data: { ...block.data, id: `blk-${index}` } }))
  const editable = { ...result, blocks: editableBlocks }
  await act(async () => root.render(h(VisualTimeline, { snapshot, result: editable, frame: 0, onFrame: value => frames.push(value), onGesture: () => {}, onGestureState: (token, active) => tokens.push(active) })))
  const { Simulate } = await import('react-dom/test-utils')
  const blur = () => act(async () => dom.window.dispatchEvent(new dom.window.Event('blur')))
  // --- Ruler: alt-tab mid-scrub can strand the owner pointer.
  const ruler = document.querySelector('.fv-ruler')
  ruler.getBoundingClientRect = () => ({ left: 0, width: 100 })
  await act(async () => Simulate.pointerDown(ruler, { button: 0, clientX: 50, pointerId: 1 }))
  assert.deepEqual(frames.slice(-1), [50])
  await blur()
  // Stale-owner moves after blur no longer pin; a fresh pointer acquires the scrub.
  await act(async () => Simulate.pointerMove(ruler, { clientX: 80, pointerId: 1 }))
  assert.deepEqual(frames.slice(-1), [50], 'stranded owner cannot pin after blur cleanup')
  await act(async () => Simulate.pointerDown(ruler, { button: 0, clientX: 20, pointerId: 2 }))
  assert.deepEqual(frames.slice(-1), [20], 'new pointer acquires scrub after blur cleanup')
  await act(async () => Simulate.pointerUp(ruler, { clientX: 20, pointerId: 2 }))
  // --- Overview strip: same recovery for the navigation drag.
  const overview = document.querySelector('.fv-overview')
  overview.getBoundingClientRect = () => ({ left: 0, width: 200 })
  const windowText = () => document.querySelector('[aria-label="可见帧范围"]').textContent
  const button = text => [...document.querySelectorAll('button')].find(node => node.textContent === text)
  await act(async () => button('放大').click()) // span 50: a seek can actually move the window
  await act(async () => Simulate.pointerDown(overview, { button: 0, clientX: 50, pointerId: 3 }))
  assert(windowText().includes('帧窗口 [0, 50)'))
  await blur()
  await act(async () => Simulate.pointerMove(overview, { clientX: 100, pointerId: 3 }))
  assert(windowText().includes('帧窗口 [0, 50)'), 'stranded overview owner cannot steer after blur')
  await act(async () => Simulate.pointerDown(overview, { button: 0, clientX: 100, pointerId: 5 }))
  assert(windowText().includes('帧窗口 [25, 75)'), 'new pointer acquires overview after blur cleanup')
  await act(async () => Simulate.pointerUp(overview, { clientX: 100, pointerId: 5 }))
  // --- Block gesture: blur mid-drag cancels the ghost and releases the token.
  for (const track of document.querySelectorAll('.fv-track')) track.getBoundingClientRect = () => ({ top: 0, bottom: 100, left: 0, width: 100 })
  const clip = document.querySelector('.fv-clip')
  await act(async () => Simulate.pointerDown(clip, { button: 0, clientX: 10, pointerId: 6 }))
  assert.deepEqual(tokens.slice(-1), [true], 'gesture publishes its token active')
  await act(async () => Simulate.pointerMove(clip, { clientX: 60, pointerId: 6 }))
  assert(document.querySelector('[aria-label="未保存拖动 ghost"]'), 'ghost visible mid-drag')
  await blur()
  assert(!document.querySelector('[aria-label="未保存拖动 ghost"]'), 'blur cancels the ghost')
  assert.deepEqual(tokens.slice(-1), [false], 'blur releases the gesture token')
  // The canceled gesture still owns its pointer's eventual click; a new pointer gesture starts cleanly.
  await act(async () => Simulate.pointerDown(clip, { button: 0, clientX: 10, pointerId: 7 }))
  assert.deepEqual(tokens.slice(-1), [true], 'new gesture acquires after blur cleanup')
  await act(async () => Simulate.pointerCancel(clip, { pointerId: 7 }))
  assert.deepEqual(tokens.slice(-1), [false])
})

test('coarse-pointer contract: 44px minimums hit only compact controls; trim handles get a class, hide under coarse primary pointer, and refuse touch pointers with the form alternative', async t => {
  const { root } = await mount(t)
  // Gestures (and therefore trim handles) require managed block ids.
  const editableBlocks = blocks.map((block, index) => ({ ...block, data: { ...block.data, id: `blk-${index}` } }))
  const editable = { ...result, blocks: editableBlocks }
  await act(async () => root.render(h(VisualTimeline, { snapshot, result: editable, frame: 10, onGesture: () => {} })))
  const css = document.querySelector('.fv-timeline style').textContent
  // Scoped 44px rules — never the broad selector that inflated clips/handles.
  assert(css.includes('.fv-timeline .fv-tools button{min-height:44px;min-width:44px}'), 'tools buttons get coarse 44px minimums')
  assert(css.includes('.fv-timeline .fv-track-title{min-height:44px}'), 'track titles get coarse minimum height')
  assert(!css.includes('.fv-timeline button{min-height:44px'), 'no broad coarse rule over every timeline button')
  assert(!/\.fv-clip\{[^}]*44/.test(css), 'clips keep their compact lane size')
  assert(css.includes('.fv-timeline .fv-trim{display:none}'), 'trim handles hidden under a coarse primary pointer')
  // Handles carry the explicit class (visible under a fine primary pointer on hybrid devices).
  const handles = [...document.querySelectorAll('button.fv-trim')]
  assert(handles.length > 0, 'trim handles present with fv-trim class')
  const { Simulate } = await import('react-dom/test-utils')
  document.querySelector('.fv-ruler').getBoundingClientRect = () => ({ left: 0, width: 100 })
  const tokens = []
  // Touch pointer on a trim handle is refused in JS with visible guidance — no gesture starts.
  await act(async () => Simulate.pointerDown(handles[0], { button: 0, clientX: 5, pointerId: 9, pointerType: 'touch' }))
  assert(document.body.textContent.includes('触控指针不支持 trim 手柄'), 'touch trim refusal message is visible')
  await act(async () => Simulate.pointerMove(handles[0], { clientX: 30, pointerId: 9 }))
  assert(!document.querySelector('[aria-label="未保存拖动 ghost"]'), 'no ghost from refused touch trim')
  // Touch pointers still own selection/move gestures on clip bodies.
  const clip = document.querySelector('.fv-clip')
  await act(async () => root.render(h(VisualTimeline, { snapshot, result: editable, frame: 10, onGesture: () => {}, onGestureState: (token, active) => tokens.push(active) })))
  const freshClip = document.querySelector('.fv-clip')
  await act(async () => Simulate.pointerDown(freshClip, { button: 0, clientX: 10, pointerId: 10, pointerType: 'touch' }))
  assert.deepEqual(tokens.slice(-1), [true], 'touch move gesture is allowed on the clip body')
  await act(async () => Simulate.pointerCancel(freshClip, { pointerId: 10 }))
  assert.deepEqual(tokens.slice(-1), [false])
})

test('compact mode: inline help suppressed and class applied; exported TimelineHelp preserves the same text', async t => {
  const { root } = await mount(t)
  await act(async () => root.render(h(VisualTimeline, { snapshot, result, frame: 10, onFrame: () => {} })))
  const section = document.querySelector('.fv-timeline')
  assert(!section.classList.contains('fv-compact'), 'default is the full standalone timeline')
  assert(document.querySelector('details.fv-help'), 'default keeps the inline help details')
  const defaultText = document.querySelector('details.fv-help').textContent
  // Compact (monitor-pane embedding): same tree minus the inline help.
  await act(async () => root.render(h(VisualTimeline, { snapshot, result, frame: 10, onFrame: () => {}, compact: true })))
  const compactSection = document.querySelector('.fv-timeline')
  assert(compactSection.classList.contains('fv-compact'), 'compact adds the fv-compact class')
  assert(!document.querySelector('details.fv-help'), 'compact suppresses the inline help only')
  assert(compactSection.querySelector('.fv-ruler') && compactSection.querySelector('.fv-overview'), 'grid/overview/ruler unchanged in compact')
  const css = compactSection.querySelector('style').textContent
  assert(css.includes('.fv-timeline.fv-compact .fv-track{padding:0 6px}'), 'compact tightens track padding only')
  assert(css.includes('.fv-timeline.fv-compact .fv-row{margin:0}'), 'compact zeroes row margins only')
  assert(css.includes('height:46px'), 'lane height 46 preserved in compact')
  assert(css.includes('height:40px'), 'clip height 40 preserved in compact')
  // The exported TimelineHelp mounts the identical content for the inspector.
  await act(async () => root.render(h('div', null, h(TimelineHelp, { result }), h(TimelineHelp, { result, onGesture: () => {} }))))
  const [readOnlyHelp, editableHelp] = [...document.querySelectorAll('details.fv-help')]
  assert(readOnlyHelp && editableHelp, 'exported TimelineHelp renders details.fv-help')
  assert.equal(readOnlyHelp.querySelector('summary').textContent, '操作说明（选择 / 播放头 / 拖拽 / 精度）')
  assert(readOnlyHelp.textContent.includes('kernel range'))
  assert(readOnlyHelp.textContent.includes('全部 4 轨 / 8 块'), 'uses live result data')
  assert(readOnlyHelp.textContent.includes('未授写许可'), 'read-only variant copy preserved')
  assert.equal(readOnlyHelp.textContent, defaultText, 'exported help text matches the former inline default verbatim')
  assert(editableHelp.textContent.includes('未保存 batch'), 'editable variant copy preserved')
})
