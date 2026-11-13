import test from 'node:test'
import assert from 'node:assert/strict'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { stringifyJSON } from '../src/json.mjs'
import { PreviewBody } from '../src/client/index.mjs'

const h = React.createElement
const browseOK = (path, entries = []) => ({ schema: 'framivra.browse/v1', path, entries })
const entry = (path, name, type = 'file') => ({ name, type, path: path ? `${path}/${name}` : name })
const version = `sha256:${'a'.repeat(64)}`
const coverage = { complete: true, scope: 'stored', unexpanded: [] }
const snapshot = { content_version: version, coverage, diagnostics: [], document: { project: { name: 'startup', duration_frames: 30, fps: 30 } } }
const projection = { content_version: version, coverage, diagnostics: [], tracks: [], blocks: [] }

async function fixture(t, respond) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://owner/' })
  const keys = ['window', 'document', 'IS_REACT_ACT_ENVIRONMENT', 'fetch']
  const saved = Object.fromEntries(keys.map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]))
  globalThis.window = dom.window
  globalThis.document = dom.window.document
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const { createRoot } = await import('react-dom/client')
  const { Simulate } = await import('react-dom/test-utils')
  const root = createRoot(document.getElementById('root'))
  const requests = []
  globalThis.fetch = async (_url, init) => {
    const payload = JSON.parse(init.body)
    requests.push({ payload, signal: init.signal })
    let result = await respond?.(payload, init.signal)
    if (result === undefined) {
      if (payload.op === 'browse') result = browseOK(payload.path, payload.path ? [entry(payload.path, 'nested.json')] : [entry('', 'sub', 'directory'), entry('', 'timeline.json')])
      else if (payload.op === 'open') result = { grant: 'startup-grant', snapshot, media: [] }
      else if (payload.op === 'query') result = projection
      else if (payload.op === 'proposal-list') result = { ok: true, proposals: [] }
      else result = { ok: true }
    }
    return new Response(stringifyJSON(result))
  }
  const proto = dom.window.HTMLDialogElement.prototype
  proto.showModal = function () { this.setAttribute('open', '') }
  proto.close = function () { this.removeAttribute('open'); this.dispatchEvent(new dom.window.Event('close')) }
  const signal = new AbortController().signal
  const props = { sessionId: 'one', useTabInfo: () => ({ tab: { signal } }), getDraftServices: () => ({}) }
  t.after(async () => {
    await act(async () => root.unmount())
    dom.window.close()
    for (const [key, descriptor] of Object.entries(saved)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor)
      else delete globalThis[key]
    }
  })
  const click = async element => { assert(element, 'click target exists'); await act(async () => element.click()) }
  const label = name => document.querySelector(`[aria-label="${name}"]`)
  const button = text => [...document.querySelectorAll('button')].find(node => node.textContent === text)
  const change = async (input, value) => act(async () => Simulate.change(input, { target: { value } }))
  const settle = async predicate => {
    for (let i = 0; i < 100; i++) {
      if (predicate()) return
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 1)) })
    }
    assert.fail(`DOM did not settle: ${document.body.textContent.slice(0, 300)}`)
  }
  await act(async () => root.render(h(PreviewBody, props)))
  return { dom, root, props, requests, click, label, button, change, settle, Simulate,
    shell: () => document.querySelector('.fv-workbench'),
    browses: () => requests.filter(({ payload }) => payload.op === 'browse') }
}

test('startup: initial mount and layout/disclosure actions perform zero IO; first Browse focuses filter and browses once', async t => {
  const f = await fixture(t)
  assert.equal(f.shell().dataset.view, 'start')
  assert.equal(f.shell().dataset.files, 'false')
  assert.equal(document.querySelector('.fv-files'), null)
  assert.equal(document.activeElement, document.body, 'mount does not steal focus')
  await f.click(f.label('展开工作台'))
  await f.click(f.label('收起工作台'))
  await f.click(f.button('通过路径打开'))
  await f.change(document.querySelector('[name="framivra-timeline"]'), 'draft.json')
  await act(async () => f.root.render(h(PreviewBody, f.props)))
  assert.deepEqual(f.requests, [], 'layout, path disclosure, typing and rerender remain local')
  await f.click(f.button('浏览工作区'))
  await f.settle(() => document.querySelector('.fv-file-list'))
  assert.equal(f.shell().dataset.files, 'true')
  assert.equal(f.shell().dataset.view, 'start')
  assert.equal(document.activeElement, document.querySelector('.fv-file-search'))
  assert.deepEqual(f.requests.map(({ payload }) => payload), [{ op: 'browse', path: '', sessionId: 'one' }])
  const toolbar = document.querySelector('.fv-file-toolbar')
  const scroll = document.querySelector('.fv-file-scroll')
  assert(toolbar.contains(document.querySelector('.fv-file-search')))
  assert(!scroll.contains(toolbar), 'toolbar is outside list scrollport')
  assert(scroll.contains(document.querySelector('.fv-file-list')))
  assert.equal(scroll.getAttribute('tabindex'), '0', 'list region supports direct keyboard scrolling')
})

test('startup: repeated Browse focuses existing subfolder filter without remount, reset or IO; close returns focus to persistent topbar', async t => {
  const f = await fixture(t)
  await f.click(f.button('浏览工作区'))
  await f.settle(() => f.label('打开文件夹 sub'))
  await f.click(f.label('打开文件夹 sub'))
  await f.settle(() => f.label('打开时间线 nested.json'))
  const aside = document.querySelector('.fv-files')
  const filter = document.querySelector('.fv-file-search')
  const list = document.querySelector('.fv-file-list')
  const topbar = f.label('浏览工作区打开时间线')
  await f.change(filter, 'nested')
  const count = f.requests.length
  const abortedBefore = f.browses().map(({ signal }) => signal.aborted)
  await f.click(f.button('浏览工作区'))
  await f.click(f.button('浏览工作区'))
  assert.strictEqual(document.querySelector('.fv-files'), aside)
  assert.strictEqual(document.querySelector('.fv-file-search'), filter)
  assert.strictEqual(document.querySelector('.fv-file-list'), list)
  assert.equal(filter.value, 'nested')
  assert.equal(document.querySelector('.fv-file-location span').textContent, 'sub')
  assert.equal(document.activeElement, filter)
  assert.equal(f.requests.length, count)
  assert.deepEqual(f.browses().map(({ signal }) => signal.aborted), abortedBefore, 'focus requests never abort an existing browse')
  await f.click(f.label('收起项目文件'))
  assert.equal(document.querySelector('.fv-files'), null)
  assert.equal(f.shell().dataset.files, 'false')
  assert.strictEqual(f.label('浏览工作区打开时间线'), topbar)
  assert.equal(document.activeElement, topbar)
  assert.equal(f.requests.length, count, 'close is local')
})

test('startup: repeated Browse while pending does not abort, retry or replace the filter', async t => {
  let resolveBrowse
  const f = await fixture(t, payload => payload.op === 'browse' ? new Promise(resolve => { resolveBrowse = resolve }) : undefined)
  await f.click(f.button('浏览工作区'))
  const filter = document.querySelector('.fv-file-search')
  await f.change(filter, 'keep')
  await f.click(f.button('浏览工作区'))
  await f.click(f.button('浏览工作区'))
  assert.equal(f.browses().length, 1)
  assert.equal(f.browses()[0].signal.aborted, false)
  assert.strictEqual(document.querySelector('.fv-file-search'), filter)
  assert.equal(filter.value, 'keep')
  assert.equal(document.activeElement, filter)
  await act(async () => resolveBrowse(browseOK('', [entry('', 'keep.json')])))
  await f.settle(() => f.label('打开时间线 keep.json'))
  assert.equal(filter.value, 'keep')
  assert.equal(f.browses().length, 1)
})

test('startup: repeated Browse after a failed listing only focuses; retry stays explicit', async t => {
  let fail = true
  const f = await fixture(t, payload => { if (payload.op === 'browse' && fail) throw new Error('HOST_DOWN: startup test') })
  await f.click(f.button('浏览工作区'))
  await f.settle(() => document.querySelector('.fv-file-scroll [role="alert"]'))
  const filter = document.querySelector('.fv-file-search')
  await f.change(filter, 'keep')
  const before = f.requests.length
  await f.click(f.button('浏览工作区'))
  assert.strictEqual(document.querySelector('.fv-file-search'), filter)
  assert.equal(document.activeElement, filter)
  assert.equal(filter.value, 'keep')
  assert.equal(f.requests.length, before)
  assert.equal(f.browses()[0].signal.aborted, false)
  assert(document.querySelector('.fv-file-scroll [role="alert"]').textContent.includes('HOST_DOWN'))
  fail = false
  await f.click(f.button('重试读取文件夹'))
  await f.settle(() => document.querySelector('.fv-file-list'))
  assert.equal(f.browses().length, 2)
  assert.equal(filter.value, 'keep')
})

test('startup: secondary path action reuses native topbar disclosure and draft, Escape preserves expanded modal', async t => {
  const f = await fixture(t)
  const disclosure = document.querySelector('.fv-topbar > .fv-path')
  const form = disclosure.querySelector('form')
  const input = disclosure.querySelector('input')
  await f.click(f.label('展开工作台'))
  await f.change(input, 'sub/my timeline.json')
  await f.click(f.button('通过路径打开'))
  assert.equal(disclosure.open, true)
  assert.equal(document.activeElement, input)
  assert.equal(input.value, 'sub/my timeline.json')
  await f.click(f.button('通过路径打开'))
  assert.equal(document.querySelectorAll('.fv-path').length, 1)
  assert.equal(document.querySelectorAll('form').length, 1)
  assert.equal(document.querySelectorAll('[name="framivra-timeline"]').length, 1)
  assert.strictEqual(disclosure.querySelector('form'), form)
  assert.strictEqual(disclosure.querySelector('input'), input)
  // Native bubbling KeyboardEvent, rather than calling a React handler directly.
  const escape = new f.dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
  await act(async () => input.dispatchEvent(escape))
  assert.equal(escape.defaultPrevented, true)
  assert.equal(disclosure.open, false)
  assert.equal(document.activeElement, disclosure.querySelector('summary'))
  assert.equal(f.shell().open, true)
  assert.equal(f.shell().getAttribute('aria-modal'), 'true')
  assert.equal(f.shell().dataset.view, 'start')
  assert.deepEqual(f.requests, [])
})

test('startup: data flags distinguish idle, open failure, loading and editor without hiding browser state', async t => {
  let fail = true, resolveOpen
  const f = await fixture(t, payload => {
    if (payload.op === 'open') {
      if (fail) throw new Error('OPEN_FAILED: startup test')
      return new Promise(resolve => { resolveOpen = resolve })
    }
  })
  await f.click(f.button('浏览工作区'))
  await f.settle(() => f.label('打开时间线 timeline.json'))
  await f.click(f.label('打开时间线 timeline.json'))
  await f.settle(() => document.querySelector('.fv-notice[role="alert"]'))
  assert.equal(f.shell().dataset.view, 'start')
  assert.equal(f.shell().dataset.files, 'true')
  assert(document.querySelector('.fv-notice').textContent.includes('OPEN_FAILED'))
  fail = false
  await f.click(f.label('打开时间线 timeline.json'))
  await f.settle(() => resolveOpen)
  assert.equal(f.shell().dataset.view, 'start', 'pending open is not an editor')
  assert.equal(f.shell().dataset.files, 'true')
  await act(async () => resolveOpen({ grant: 'startup-grant', snapshot, media: [] }))
  await f.settle(() => f.shell().dataset.view === 'editor')
  assert.equal(f.shell().dataset.files, 'true')
  assert.equal(document.querySelector('.fv-monitor').getAttribute('aria-label'), '画面预览')
  assert.equal(document.querySelector('.fv-empty'), null)
  await f.click(f.label('收起项目文件'))
  assert.equal(f.shell().dataset.view, 'editor')
  assert.equal(f.shell().dataset.files, 'false')
  assert(!f.requests.some(({ payload }) => ['plan', 'commit', 'render', 'write'].includes(payload.op)), 'startup actions never authorize timeline writes')
})
