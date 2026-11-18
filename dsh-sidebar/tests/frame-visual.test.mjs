import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import React, { useState } from 'react'
import { JSDOM } from 'jsdom'
import { FrameEpoch, FrameTools } from '../src/client/frame.mjs'

// Presentation-only regression for the FrameTools UI branch: preview stage,
// objectURL lifecycle, folded frame details, preserved test-facing labels.
// Generate/stage/copy semantics also stay covered by frame-ghost.test.mjs;
// follow opt-in and serialized cleanup are exercised below with held HTTP replies.
const raw = '{"project":{"duration_frames":100}}'
const hash = bytes => createHash('sha256').update(bytes).digest('hex')
const snapshot = { content_version: `sha256:${hash(raw)}`, document: { project: { duration_frames: 100 } } }
const cleanResult = () => ({
  file: new File(['png-bytes'], 'framivra-frame-10-0000.png', { type: 'image/png' }),
  certificate: { kind: 'current-composite', frame: 10, content_version: snapshot.content_version, width: 1920, height: 1080 },
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
  let frame = 10, suspended = false, refresh
  function Harness() {
    const [, update] = useState(0); refresh = () => update(n => n + 1)
    return React.createElement(FrameTools, { owner, opened: { snapshot, grant: 'grant' }, sessionId: 'one', frame, suspended, tabSignal: tab.signal, getDraftServices: () => ({}), call: async () => ({}) })
  }
  await act(async () => root.render(React.createElement(Harness)))
  const section = () => document.querySelector('[aria-label="当前合成帧与草稿"]')
  const rerender = async (next, paused) => { if (next !== undefined) frame = next; if (paused !== undefined) suspended = paused; await act(async () => refresh()) }
  t.after(async () => { await act(async () => { tab.abort(); root.unmount(); await Promise.resolve() }); dom.window.close(); for (const [key, descriptor] of Object.entries(saved)) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key] } })
  return { owner, root, section, rerender, calls, act }
}

test('prepare and independent HTML trust synchronously invalidate; no layer mode or automatic render', async t => {
  const f = await fixture(t)
  assert(f.section().querySelector('select') === null, "Expected absent DOM node: f.section().querySelector('select')")
  const prepare = [...f.section().querySelectorAll('input')].find(input => input.parentElement.textContent.includes('允许按需准备当前帧图形'))
  assert(prepare); assert.equal(prepare.checked, false)
  assert(!f.section().textContent.includes('仅信任的工程'))
  const operation = f.owner.begin(new AbortController().signal)
  f.owner.generated = cleanResult()
  await f.act(async () => {
    prepare.click()
    assert.equal(operation.signal.aborted, true); assert.equal(f.owner.generated, undefined)
  })
  assert(f.section().textContent.includes('浏览器允许的文件和网络'))
  assert(f.section().textContent.includes('不是沙箱'))
  const trust = [...f.section().querySelectorAll('input')].find(input => input.parentElement.textContent.includes('仅信任的工程'))
  assert.equal(trust.checked, false, 'prepare is not HTML trust')
  const next = f.owner.begin(new AbortController().signal); f.owner.generated = cleanResult()
  await f.act(async () => { trust.click(); assert.equal(next.signal.aborted, true); assert.equal(f.owner.generated, undefined) })
  await f.act(async () => prepare.click())
  await f.act(async () => prepare.click())
  assert.equal([...f.section().querySelectorAll('input')].find(input => input.parentElement.textContent.includes('仅信任的工程')).checked, false)
  assert.equal(f.owner.generated, undefined)
})
test('UI prepare allows cold SVG without HTML trust; only explicit clicks send bounded opt-in payloads', async t => {
  const f = await fixture(t), requests = []
  const previous = globalThis.fetch
  globalThis.fetch = async (_url, init) => {
    requests.push(JSON.parse(init.body))
    return new Response(JSON.stringify({ ok: false, error: 'FIXTURE_STOP', message: 'request recorded, no render' }), { status: 403 })
  }
  t.after(() => { globalThis.fetch = previous })
  const input = label => [...f.section().querySelectorAll('input')].find(node => node.parentElement.textContent.includes(label))
  const button = () => [...f.section().querySelectorAll('button')].find(node => node.textContent.startsWith('显式生成并验证'))
  await f.act(async () => input('允许写入预览缓存').click())
  const oldButton = button(), oldGenerate = oldButton[Object.keys(oldButton).find(key => key.startsWith('__reactProps$'))].onClick
  await f.act(async () => {
    input('允许按需准备').click()
    oldGenerate() // Retained closure from the previous authorization cannot render.
  })
  assert.deepEqual(requests, [])
  assert.equal(input('仅信任的工程').checked, false)
  assert.equal(button().disabled, false, 'SVG preparation needs no HTML trust')
  await f.act(async () => button().click())
  assert.equal(requests.length, 1)
  const status = f.section().querySelector('.fv-monitor-footer .fv-monitor-status[role="status"]')
  assert(status.textContent.includes('FIXTURE_STOP'))
  assert.equal(status.title, status.textContent, 'full error remains available on hover')
  assert.equal(status.getAttribute('aria-label'), status.textContent)
  assert.equal(requests[0].prepareGfx, true); assert(!Object.hasOwn(requests[0], 'htmlTrust'))
  await f.act(async () => input('仅信任的工程').click())
  assert.equal(requests.length, 1, 'trust never automatically generates')
  await f.act(async () => button().click())
  assert.equal(requests[1].htmlTrust, 'trusted-local-html'); assert.equal(requests[1].prepareGfx, true)
  await f.act(async () => input('允许按需准备').click())
  await f.act(async () => button().click())
  assert(!Object.hasOwn(requests[2], 'prepareGfx')); assert(!Object.hasOwn(requests[2], 'htmlTrust'))
  for (const request of requests) { assert.equal(request.renderConsent, 'cache/preview-only'); assert(!Object.hasOwn(request, 'htmlBlockAddress')) }
})
test('frame visual: stage placeholders, folded frame details, labels and consent order preserved', async t => {
  const f = await fixture(t)
  const section = f.section()
  // Consent stays the first input; all four action labels keep their prefixes.
  assert.equal(section.querySelector('input').type, 'checkbox')
  const labels = [...section.querySelectorAll('button')].map(node => node.textContent)
  for (const prefix of ['显式生成并验证固定帧 PNG', '复制已生成 PNG', '将该帧 + 精确引用加入当前草稿', '显式取消帧并等待回收']) assert(labels.some(text => text.startsWith(prefix)), prefix)
  assert.equal(section.getAttribute('aria-busy'), 'false')
  assert.deepEqual([...section.children].filter(node => node.tagName !== 'STYLE').map(node => node.className), ['fv-frame-tools-stage fv-program-stage', 'fv-monitor-footer'])
  assert(section.querySelector('h3') === null, 'no persistent title or version heading')
  const footer = section.querySelector('.fv-monitor-footer')
  const settings = footer.querySelector('details')
  assert(settings.matches('.fv-monitor-disclosure.fv-monitor-disclosure-up'))
  assert.equal(settings.querySelector('summary').getAttribute('aria-label'), '预览生成设置')
  assert.equal(settings.open, false)
  assert(settings.querySelector('.fv-monitor-popover input'), 'authorization controls live in the folded popover')
  assert.equal(footer.querySelectorAll(':scope > label').length, 0)
  assert(footer.querySelector(':scope > button[aria-label="显式取消帧并等待回收"]'), 'cancellation never requires opening a disclosure')
  // No frame pinned -> concise placeholder, no img.
  await f.rerender(null)
  assert(section.textContent.includes('未固定帧'))
  assert(section.querySelector('img') === null, "Expected absent DOM node: section.querySelector('img')")
  // Frame pinned, nothing generated -> no-render placeholder; prose folded into a closed details.
  await f.rerender(10)
  assert(section.textContent.includes('未生成画面（需显式生成）'))
  const details = section.querySelector('.fv-frame-tools-details')
  assert(details && !details.hasAttribute('open'), 'certificate details exists and starts closed')
  assert(section.querySelector('details pre') === null, 'no raw certificate shown')
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
  assert(f.section().querySelector('.fv-frame-tools-details').textContent.includes('1920 × 1080 PNG'))
  assert(f.section().querySelector('details pre') === null, 'verified frame has a summary rather than raw JSON')
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
  assert(f.section().querySelector('img') === null, "Expected absent DOM node: f.section().querySelector('img')")
  // Unmount of a live preview revokes its URL too.
  f.owner.generated = first
  await f.rerender(10)
  await f.act(async () => { f.root.unmount(); await Promise.resolve() })
  assert.deepEqual(f.calls.at(-1), { op: 'revoke', url: 'blob:frame-3' })
})

test('verified availability status expires with a dirty preview, without hiding later errors', async t => {
  const f = await fixture(t, { withObjectURL: true })
  const previousFetch = globalThis.fetch, previousImage = globalThis.Image
  t.after(() => { globalThis.fetch = previousFetch; if (previousImage) globalThis.Image = previousImage; else delete globalThis.Image })
  let decoded
  const decoding = new Promise(resolve => { decoded = resolve })
  // Explicit image-decoder adapter; this test checks UI state, not native PNG decoding.
  globalThis.Image = class { naturalWidth = 1; naturalHeight = 1; async decode() { decoded() } }
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aCioAAAAASUVORK5CYII=', 'base64')
  globalThis.fetch = async () => new Response(JSON.stringify({
    schema: 'framivra.timeline.frame/v1', operation: 'frame', ok: true, content_version: snapshot.content_version, diagnostics: [],
    image: { state: 'generated', kind: 'current-composite', approximate: false, mime_type: 'image/png', width: 1, height: 1, frame: 10, fps: { num: 30, den: 1 }, content_version: snapshot.content_version, input_fingerprint: 'a'.repeat(64), identity_semantics: 'Fast media stat identity', render_key: 'b'.repeat(64), cache_hit: false, bytes: png.length, sha256: hash(png), inline: { encoding: 'base64', data: png.toString('base64') } },
    reference: { schema: 'framivra.timeline.reference/v1', operation: 'reference', ok: true, content_version: snapshot.content_version, diagnostics: [], document: { raw, content_version: snapshot.content_version }, selection: { global_frame: 10, fps: 30, tracks: [], blocks: [] }, coverage: { complete: true } },
  }))
  const generate = () => f.section().querySelector('[aria-label="显式生成并验证固定帧 PNG"]')
  await f.act(async () => f.section().querySelector('input').click())
  await f.act(async () => { generate().click(); await decoding })
  assert(f.section().querySelector('img'))
  assert.match(f.section().querySelector('[role=status]').textContent, /已验证 PNG/)
  f.owner.editChanged({ dirty: true }); await f.rerender()
  assert(f.section().querySelector('img') === null, "Expected absent DOM node: f.section().querySelector('img')")
  assert(generate().disabled)
  assert.match(f.section().querySelector('[role=alert]').textContent, /拒绝生成和加入草稿/)
  assert.match(f.section().querySelector('[role=status]').textContent, /旧画面已失效/)
  assert(!f.section().textContent.includes('已验证 PNG，未发送'))
  f.owner.editChanged({ dirty: false }); await f.rerender()
  globalThis.fetch = async () => stopReply()
  await f.act(async () => generate().click())
  assert.match(f.section().querySelector('[role=status]').textContent, /FIXTURE_STOP/)
})

function followControls(f) {
  const input = label => [...f.section().querySelectorAll('input')].find(node => node.parentElement.textContent.includes(label))
  return { consent: () => input('允许写入预览缓存'), follow: () => input('随播放头实时生成') }
}
const stopReply = () => new Response(JSON.stringify({ ok: false, error: 'FIXTURE_STOP', message: 'no render fixture' }), { status: 403 })

test('live follow requires both opt-ins, supersession waits for confirmed cleanup, and a render failure disables follow', async t => {
  const f = await fixture(t), controls = followControls(f), requests = []
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const previous = globalThis.fetch
  let releaseCleanup
  globalThis.fetch = (_url, init) => {
    const request = JSON.parse(init.body); requests.push({ ...request, signal: init.signal })
    if (request.op === 'cancel') return new Promise(resolve => { releaseCleanup = () => resolve(new Response('{"ok":true}')) })
    if (requests.filter(item => item.op === 'render').length === 1) return new Promise((_resolve, reject) => init.signal.addEventListener('abort', () => reject(init.signal.reason), { once: true }))
    return Promise.resolve(stopReply())
  }
  t.after(() => { globalThis.fetch = previous })
  const tick = () => f.act(async () => t.mock.timers.tick(121))
  assert(controls.follow().disabled)
  await tick(); assert.deepEqual(requests, [], 'mount never auto-renders')
  await f.act(async () => controls.consent().click())
  await tick(); assert.deepEqual(requests, [], 'cache consent alone is not follow consent')
  await f.act(async () => controls.follow().click())
  await f.rerender(undefined, true); await tick()
  assert.deepEqual(requests, [], 'storage suspension cancels the pending follow timer')
  assert.equal(controls.follow().checked, true, 'suspension preserves the explicit follow choice')
  await f.rerender(undefined, false); await tick()
  assert.deepEqual(requests.map(item => [item.op, item.frame]), [['render', 10]])
  f.owner.invalidate(); await f.rerender(11)
  assert(requests[0].signal.aborted)
  assert.equal(requests[1].op, 'cancel')
  await tick()
  f.owner.invalidate(); await f.rerender(12); await tick()
  assert.deepEqual(requests.map(item => item.op), ['render', 'cancel'], 'no render may race a pending managed-child cleanup')
  await f.rerender(undefined, true)
  await f.rerender(undefined, false); await tick()
  assert.deepEqual(requests.map(item => item.op), ['render', 'cancel'], 'resumed follow must still wait for managed cleanup')
  assert.equal(controls.follow().checked, true)
  await f.act(async () => releaseCleanup())
  assert.deepEqual(requests.map(item => [item.op, item.frame]), [['render', 10], ['cancel', undefined], ['render', 12]], 'suspension and resume before cleanup cannot duplicate the queued frame or retry its failure')
  assert.equal(controls.follow().checked, false, 'automatic render error stops follow')
  f.owner.invalidate(); await f.rerender(13); await tick()
  assert.equal(requests.length, 3, 'pin changes after an error never retry without a new opt-in')
  assert(f.section().textContent.includes('FIXTURE_STOP'))
})

test('failed cleanup latches follow off and blocks new renders until explicit successful cleanup and re-enable', async t => {
  const f = await fixture(t), controls = followControls(f), requests = []
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const previous = globalThis.fetch
  let rejectCleanup, cleanupCount = 0
  globalThis.fetch = (_url, init) => {
    const request = JSON.parse(init.body); requests.push(request)
    if (request.op === 'cancel') {
      if (++cleanupCount === 1) return new Promise(resolve => { rejectCleanup = () => resolve(new Response('{"ok":false,"error":"PROCESS_CLEANUP_FAILED","message":"held child"}', { status: 502 })) })
      return Promise.resolve(new Response('{"ok":true}'))
    }
    if (requests.filter(item => item.op === 'render').length === 1) return new Promise((_resolve, reject) => init.signal.addEventListener('abort', () => reject(init.signal.reason), { once: true }))
    return Promise.resolve(stopReply())
  }
  t.after(() => { globalThis.fetch = previous })
  const tick = () => f.act(async () => t.mock.timers.tick(121))
  await f.act(async () => controls.consent().click())
  await f.act(async () => controls.follow().click()); await tick()
  f.owner.invalidate(); await f.rerender(11); await tick()
  assert.deepEqual(requests.map(item => item.op), ['render', 'cancel'])
  await f.act(async () => rejectCleanup())
  assert.equal(controls.follow().checked, false)
  assert.equal(requests.filter(item => item.op === 'render').length, 1, 'queued work cannot start after failed cleanup')
  await f.act(async () => controls.follow().click()); await tick()
  assert.equal(controls.follow().checked, false, 're-enabling alone cannot clear the cleanup latch')
  assert.equal(requests.filter(item => item.op === 'render').length, 1)
  const cancel = [...f.section().querySelectorAll('button')].find(node => node.getAttribute('aria-label') === '显式取消帧并等待回收')
  await f.act(async () => cancel.click())
  assert(f.section().textContent.includes('已回收；未自动重试'))
  f.owner.invalidate(); await f.rerender(12); await tick()
  assert.equal(requests.filter(item => item.op === 'render').length, 1, 'successful cleanup does not itself re-enable follow')
  await f.act(async () => controls.follow().click()); await tick()
  assert.deepEqual(requests.filter(item => item.op === 'render').map(item => item.frame), [10, 12], 'explicit re-enable after confirmed cleanup admits new work')
})
