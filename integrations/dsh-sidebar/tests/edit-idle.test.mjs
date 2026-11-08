import test from 'node:test'
import assert from 'node:assert/strict'

/** Composition regression: a read-only proposal poll must not abort an
 * in-flight frame render. Real PreviewBody (which wires ManualEditor's
 * AgentProposals mirror through the shared FrameEpoch), real React 18 DOM
 * rendering, fetch stubbed at the transport seam, poll timers captured at
 * 1500ms so the test never sleeps. */
const version = `sha256:${'a'.repeat(64)}`
const snapshot = { content_version: version, coverage: { complete: true }, document: { project: { duration_frames: 120 }, _vtx: { protocol: 1 } } }
const result = { content_version: version, coverage: { complete: true }, tracks: [], blocks: [] }

test('empty proposal-list polls never abort an in-flight frame render; a real dirty transition still does', { timeout: 20000 }, async t => {
  const { JSDOM } = await import('jsdom')
  const React = (await import('react')).default, { act } = await import('react')
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://owner/' })
  globalThis.window = dom.window; globalThis.document = dom.window.document
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const { createRoot } = await import('react-dom/client'), { Simulate } = await import('react-dom/test-utils')
  const { PreviewBody } = await import('../src/client/index.mjs')
  const root = createRoot(document.getElementById('root'))

  const polls = [], originalSetTimeout = globalThis.setTimeout
  let proposals = [], renderSignal
  // Exercise the actual Fetch stream seam: control responses are byte-bounded
  // before decode, so a text-only mock would bypass the contract under test.
  const respond = payload => new Response(JSON.stringify(payload), { headers: { 'Content-Type': 'application/json' } })
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (url, init = {}) => {
    if (url === '/api/framivra/frame') { renderSignal = init.signal; return new Promise(() => {}) }
    const body = JSON.parse(init.body ?? '{}')
    if (body.op === 'open') return respond({ grant: 'read', snapshot, media: [] })
    if (body.op === 'query') return respond(result)
    if (body.op === 'proposal-list') return respond({ ok: true, proposals })
    return respond({ ok: true })
  }
  // Capture only the 1500ms proposal polls; everything else keeps real timers.
  globalThis.setTimeout = (fn, delay, ...rest) => {
    if (delay === 1500) { polls.push(fn); return polls.length }
    return originalSetTimeout(fn, delay, ...rest)
  }
  t.after(async () => {
    globalThis.setTimeout = originalSetTimeout; globalThis.fetch = originalFetch
    await act(async () => root.unmount())
    dom.window.close(); delete globalThis.window; delete globalThis.document
  })

  const tab = new AbortController()
  const flush = () => act(async () => { await Promise.resolve() })
  const settle = async () => { for (let i = 0; i < 20; i++) await flush() }

  await act(async () => root.render(React.createElement(PreviewBody, { useTabInfo: () => ({ tab: { signal: tab.signal, actions: {} } }) })))
  await act(async () => document.querySelector('form').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })))
  await settle()
  assert(document.querySelector('fieldset'), 'ready state must mount the editor')
  assert.equal(polls.length, 1, 'first 1500ms poll scheduled after the immediate read-only poll')

  // Pin an integer frame and grant cache/preview consent for this render.
  const frameInput = document.querySelector('input[name="framivra-frame"]')
  assert(frameInput, 'canonical frame field must remain available after localization')
  await act(async () => Simulate.change(frameInput, { target: { value: '9' } }))
  const consent = document.querySelector('input[type=checkbox]')
  await act(async () => Simulate.change(consent, { target: { checked: true } }))
  const generate = [...document.querySelectorAll('button')].find(node => node.textContent === '显式生成并验证固定帧 PNG')
  assert.equal(generate.disabled, false)
  await act(async () => generate.click())
  await settle()
  assert(renderSignal, 'frame render fetch started')
  assert.equal(renderSignal.aborted, false)

  // Two more read-only proposal polls answer empty lists (fresh array each
  // time). Neither may abort the render or show the stale/cancelled message.
  for (let round = 0; round < 2; round++) {
    assert(polls.length >= round + 1, `poll ${round + 1} scheduled`)
    await act(async () => { await polls[round]() })
    await settle()
    assert.equal(renderSignal.aborted, false, `read-only poll ${round + 1} must not abort the in-flight render`)
    assert.equal([...document.querySelectorAll('p')].some(node => node.textContent === '操作已失效/取消；未加入草稿。'), false)
  }

  // A genuinely dirty proposal is a real transition: abort, clear, block.
  proposals = [{ proposal_id: 'p1', state: 'pending' }]
  await act(async () => { await polls[2]() })
  await settle()
  assert.equal(renderSignal.aborted, true, 'dirty proposal must abort the in-flight render')
  assert([...document.querySelectorAll('[role=alert]')].some(node => node.textContent.includes('编辑/队列/计划/写结果未解决')), 'frame tools must be blocked while a proposal is pending')
})
