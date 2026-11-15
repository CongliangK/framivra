import test from 'node:test'
import assert from 'node:assert/strict'
import React, { useState } from 'react'
import { JSDOM } from 'jsdom'
import { FrameEpoch, FrameTools } from '../src/client/frame.mjs'

// Presentation-only regression for the FrameTools UI branch: preview stage,
// objectURL lifecycle, folded prose/certificate, preserved test-facing labels.
// All async generate/stage/copy semantics stay covered by frame-ghost.test.mjs.
const snapshot = { content_version: 'sha256:' + 'a'.repeat(64), document: { project: { duration_frames: 100 } } }
const cleanResult = () => ({
  file: new File(['png-bytes'], 'framivra-frame-10-0000.png', { type: 'image/png' }),
  certificate: { frame: 10, content_version: snapshot.content_version, width: 1920, height: 1080 },
  reference: { selection: { tracks: [], blocks: [] } },
})

async function fixture(t, { withObjectURL = false } = {}) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://frame.invalid/' })
  const saved = Object.fromEntries(['window', 'document', 'navigator', 'IS_REACT_ACT_ENVIRONMENT'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]))
  globalThis.window = dom.window; globalThis.document = dom.window.document; globalThis.IS_REACT_ACT_ENVIRONMENT = true
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator })
  const { createRoot } = await import('react-dom/client'), { act } = await import('react' )
  const root = createRoot(document.getElementById('root'))
  const owner = new FrameEpoch(), tab = new AbortController()
  const calls = []
  const urlBase = globalThis.URL // frame.mjs resolves bare `URL`, i.e. Node's global in tests
  const savedCreate = urlBase.createObjectURL, savedRevoke = urlBase.revokeObjectURL
  if (withObjectURL) {
    let seq = 0
    urlBase.createObjectURL = file => { calls.push({ op: 'create', file }); return `blob:frame-${++seq}` }
    urlBase.revokeObjectURL = url => { calls.push({ op: 'revoke', url }) }
    t.after(() => { if (savedCreate) urlBase.createObjectURL = savedCreate; else delete urlBase.createObjectURL; if (savedRevoke) urlBase.revokeObjectURL = savedRevoke; else delete urlBase.revokeObjectURL })
  }
  let frame = 10, refresh
  function Harness() {
    const [, update] = useState(0); refresh = () => update(n => n + 1)
    return React.createElement(FrameTools, { owner, opened: { snapshot, grant: 'grant' }, sessionId: 'one', frame, tabSignal: tab.signal, getDraftServices: () => ({}), call: async () => ({}) })
  }
  await act(async () => root.render(React.createElement(Harness)))
  const section = () => document.querySelector('[aria-label="当前合成帧与草稿"]')
  const rerender = async next => { if (next !== undefined) frame = next; await act(async () => refresh()) }
  t.after(async () => { await act(async () => { tab.abort(); root.unmount(); await Promise.resolve() }); dom.window.close(); for (const [key, descriptor] of Object.entries(saved)) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key] } })
  return { owner, root, section, rerender, calls, act }
}

test('frame visual: stage placeholders, folded prose/certificate, labels and consent order preserved', async t => {
  const f = await fixture(t)
  const section = f.section()
  // Consent stays the first input; all four action labels keep their prefixes.
  assert.equal(section.querySelector('input').type, 'checkbox')
  const labels = [...section.querySelectorAll('button')].map(node => node.textContent)
  for (const prefix of ['显式生成并验证固定帧 PNG', '复制已生成 PNG', '将该帧 + 精确引用加入当前草稿', '显式取消帧并等待回收']) assert(labels.some(text => text.startsWith(prefix)), prefix)
  assert.equal(section.getAttribute('aria-busy'), 'false')
  // No frame pinned -> concise placeholder, no img.
  await f.rerender(null)
  assert(section.textContent.includes('未固定帧'))
  assert.equal(section.querySelector('img'), null)
  // Frame pinned, nothing generated -> no-render placeholder; prose folded into a closed details.
  await f.rerender(10)
  assert(section.textContent.includes('未生成画面（需显式生成）'))
  const details = section.querySelector('details')
  assert(details && !details.hasAttribute('open'), 'certificate details exists and starts closed')
  assert(details.textContent.includes('不是成片 QC'), 'boundary prose moved into details')
  assert.equal(section.querySelector('details pre'), null, 'certificate pre only appears after generation')
  // Black 16:9 stage CSS contract, fully scoped to .fv-frame-tools.
  assert.equal(section.className, 'fv-frame-tools')
  const css = section.querySelector('style').textContent
  assert(css.includes('.fv-frame-tools .fv-frame-tools-stage'))
  assert(!/\.fv-frame[^-]/.test(css), 'no unscoped .fv-frame selectors')
  assert(css.includes('aspect-ratio:16/9') && css.includes('background:#000'))
  assert(css.includes('object-fit:contain'))
})

test('frame visual: verified objectURL only from owner.generated.file, revoked on replace/invalidation/unmount', async t => {
  const f = await fixture(t, { withObjectURL: true })
  const first = cleanResult(), second = cleanResult()
  // jsdom guard: URL.createObjectURL now exists; generate -> create from the exact file.
  f.owner.generated = first
  await f.rerender(10)
  assert.deepEqual(f.calls, [{ op: 'create', file: first.file }])
  const img = f.section().querySelector('img')
  assert(img && img.src === 'blob:frame-1')
  assert(img.alt.includes('已验证合成帧 10'))
  // Certificate now visible inside the closed details.
  assert(f.section().querySelector('details pre'), 'certificate pre present after generation')
  // Replacement revokes the old URL before creating the new one.
  f.owner.generated = second
  await f.rerender(10)
  assert.deepEqual(f.calls, [{ op: 'create', file: first.file }, { op: 'revoke', url: 'blob:frame-1' }, { op: 'create', file: second.file }])
  assert.equal(f.section().querySelector('img').src, 'blob:frame-2')
  assert(!f.section().innerHTML.includes('blob:frame-1'), 'revoked URL never stays in the DOM')
  // Invalidation (owner.generated cleared, rerender) revokes; no img remains.
  f.owner.invalidate(); f.owner.generated = undefined
  await f.rerender(10)
  assert.deepEqual(f.calls.at(-1), { op: 'revoke', url: 'blob:frame-2' })
  assert.equal(f.section().querySelector('img'), null)
  // Unmount of a live preview revokes its URL too.
  f.owner.generated = first
  await f.rerender(10)
  await f.act(async () => { f.root.unmount(); await Promise.resolve() })
  assert.deepEqual(f.calls.at(-1), { op: 'revoke', url: 'blob:frame-3' })
})
