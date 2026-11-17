import test from 'node:test'
import assert from 'node:assert/strict'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { StoragePanel, storageBytes, storageSupported } from '../src/client/storage.mjs'

const h = React.createElement
const version = `sha256:${'a'.repeat(64)}`
const opened = { grant: 'project-A', snapshot: { content_version: version }, host: { ops: ['storage-inspect', 'storage-plan', 'storage-clean'] }, capabilities: { capabilities: { storage: { schema: 'framivra.storage/v1', operations: ['inspect', 'plan', 'clean'], request_stdin: true } } } }
const envelope = operation => ({ schema: 'framivra.storage/v1', operation, ok: true, content_version: version, diagnostics: [] })
const measurement = { ...envelope('inspect'), usage: { project_bytes: 4096, cache_bytes: 1024, out_bytes: 2048, other_bytes: 1024, file_count: 3, symlinks_skipped: 1 }, cache_mode: 'core' }
const preview = { ...envelope('plan'), planToken: 'frozen-host-token', expires_in_ms: 60000, plan: { id: version, count: 1, bytes: 1024, entries: [{ path: 'cache/old.bin', bytes: 1024, category: 'legacy', reason: '旧缓存' }] } }
const button = label => [...document.querySelectorAll('button')].find(node => node.textContent === label)
const text = () => document.body.textContent
const click = async node => { assert(node); await act(async () => node.click()) }
async function setup(t, responder) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://owner/', pretendToBeVisual: true })
  globalThis.window = dom.window; globalThis.document = dom.window.document; globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const { createRoot } = await import('react-dom/client')
  const root = createRoot(document.getElementById('root')), calls = [], busy = [], deaths = []
  const tab = new AbortController()
  const props = { opened, sessionId: 'one', tabSignal: tab.signal, onBusyChange: value => busy.push(value), onGrantDead: (...args) => deaths.push(args), call: async (session, payload, signal) => {
    calls.push({ session, payload, signal })
    return responder ? responder(payload, signal) : payload.op === 'storage-inspect' ? measurement : preview
  } }
  t.after(async () => { await act(async () => root.unmount()); dom.window.close(); delete globalThis.window; delete globalThis.document })
  const render = async overrides => act(async () => root.render(h(StoragePanel, { ...props, ...overrides })))
  return { root, calls, busy, deaths, tab, render }
}

test('storage display uses existing capabilities and explicitly labelled logical bytes', async t => {
  const ui = await setup(t)
  await ui.render()
  assert.deepEqual(ui.calls.map(x => x.payload.op), ['storage-inspect'])
  assert.match(text(), /项目合计4.0 KiB缓存 cache1.0 KiB输出 out2.0 KiB素材及其他1.0 KiB/)
  assert.match(text(), /core.*插件 → Framivra → 配置文件/)
  assert.match(text(), /逻辑字节.*跳过 1 个链接/)
  assert.equal(document.querySelector('input[type=checkbox]'), null)
  assert.deepEqual(ui.busy, [], 'measurement never grants write permission')
  assert.equal(storageBytes(Number.MAX_SAFE_INTEGER + 1), '—')
  assert.equal(storageBytes(0), '0 B')
})

test('unsupported old cores and inactive panes perform no scan or mutation', async t => {
  const ui = await setup(t)
  await ui.render({ opened: { ...opened, capabilities: {} } })
  assert.equal(storageSupported({ ...opened, capabilities: {} }), false)
  assert.match(text(), /当前内核或插件尚不支持空间管理/)
  assert.equal(ui.calls.length, 0)
  await ui.render({ active: false })
  assert.equal(ui.calls.length, 0)
  await ui.render()
  assert.equal(ui.calls.length, 1)
})

test('cleanup requires frozen preview and independent stopped-writers confirmation, once', async t => {
  const ui = await setup(t, payload => payload.op === 'storage-inspect' ? measurement : payload.op === 'storage-plan' ? preview : { ...envelope('clean'), removed: { count: 1, bytes: 1024 }, remaining: { count: 0, bytes: 0 } })
  await ui.render()
  await click(button('预演缓存清理'))
  assert.match(text(), /cache\/old.bin.*旧缓存/)
  assert.equal(button('确认清理').disabled, true)
  await click(button('确认清理'))
  assert.equal(ui.calls.filter(x => x.payload.op === 'storage-clean').length, 0)
  await click(document.querySelector('input[type=checkbox]'))
  await click(button('确认清理'))
  const cleanCalls = ui.calls.filter(x => x.payload.op === 'storage-clean')
  assert.equal(cleanCalls.length, 1)
  assert.deepEqual(cleanCalls[0].payload, { op: 'storage-clean', grant: 'project-A', planToken: 'frozen-host-token', confirm: 'stopped-writers-and-cleanup' })
  assert.equal(button('确认清理'), undefined)
  assert.match(text(), /已回收 1 项（1.0 KiB）/)
  assert.equal(ui.busy.at(-1), false)
})

test('unknown cleanup outcome consumes consent and cannot be retried automatically', async t => {
  const ui = await setup(t, payload => payload.op === 'storage-inspect' ? measurement : payload.op === 'storage-plan' ? preview : { ...envelope('clean'), ok: false, outcome_unknown: true })
  await ui.render()
  await click(button('预演缓存清理'))
  await click(document.querySelector('input[type=checkbox]'))
  await click(button('确认清理'))
  assert.match(text(), /结果未确认.*部分文件可能已删除/)
  assert.equal(button('确认清理'), undefined)
  await click(button('刷新空间'))
  assert.match(text(), /结果未确认.*部分文件可能已删除/, 'observation does not resolve an unknown mutation')
  assert.equal(ui.calls.filter(x => x.payload.op === 'storage-clean').length, 1)
  assert.equal(document.querySelector('input[type=checkbox]'), null)
})

test('dirty editing disables preview and cleanup without discarding the plan', async t => {
  const ui = await setup(t)
  await ui.render()
  await click(button('预演缓存清理'))
  await click(document.querySelector('input[type=checkbox]'))
  await ui.render({ disabled: true })
  assert.equal(button('确认清理').disabled, true)
  assert.equal(button('重新生成清理预演').disabled, true)
  assert.match(text(), /cache\/old.bin/)
  await click(button('确认清理'))
  assert.equal(ui.calls.filter(x => x.payload.op === 'storage-clean').length, 0)
})

test('grant death stops scanning and is lifted to parent', async t => {
  const ui = await setup(t, () => { throw new Error('BAD_GRANT: expired') })
  await ui.render()
  assert.deepEqual(ui.deaths, [['BAD_GRANT', 'project-A']])
  assert.equal(button('刷新空间').disabled, true)
  assert.equal(button('预演缓存清理').disabled, true)
  assert.equal(ui.calls.length, 1)
})

test('different timeline version is not presented as current space', async t => {
  const ui = await setup(t, () => ({ ...measurement, content_version: `sha256:${'b'.repeat(64)}` }))
  await ui.render()
  assert.match(text(), /时间线已变化/)
  assert.doesNotMatch(text(), /4.0 KiB/)
})

test('document visibility stops timed samples and cleanup plans expire without deletion', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'Date'] })
  const ui = await setup(t)
  await ui.render()
  await act(async () => t.mock.timers.tick(10000))
  assert.equal(ui.calls.filter(x => x.payload.op === 'storage-inspect').length, 2)
  await act(async () => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' })
    document.dispatchEvent(new window.Event('visibilitychange'))
  })
  await act(async () => t.mock.timers.tick(30000))
  assert.equal(ui.calls.filter(x => x.payload.op === 'storage-inspect').length, 2)
  await act(async () => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
    document.dispatchEvent(new window.Event('visibilitychange'))
  })
  await click(button('预演缓存清理'))
  await click(document.querySelector('input[type=checkbox]'))
  await act(async () => t.mock.timers.tick(60000))
  assert.equal(button('确认清理'), undefined)
  assert.match(text(), /预演已过期/)
  assert.equal(ui.calls.filter(x => x.payload.op === 'storage-clean').length, 0)
})

test('late old-panel completion cannot release new-panel busy ownership', async t => {
  const pending = []
  const ui = await setup(t, payload => payload.op === 'storage-inspect' ? measurement : new Promise(resolve => pending.push(resolve)))
  await ui.render()
  await click(button('预演缓存清理'))
  assert.equal(ui.busy.at(-1), true)
  await ui.render({ key: 'replacement', opened: { ...opened, grant: 'project-B' } })
  await click(button('预演缓存清理'))
  assert.equal(ui.busy.at(-1), true)
  await act(async () => pending[0](preview))
  assert.equal(ui.busy.at(-1), true, 'old finally must not re-enable editing during the new request')
  await act(async () => pending[1](preview))
  assert.equal(ui.busy.at(-1), false)
})

test('hidden pane cancels scan and refuses late result; no overlapping scan', async t => {
  let finish
  const ui = await setup(t, () => new Promise(resolve => { finish = resolve }))
  await ui.render()
  assert.equal(ui.calls.length, 1)
  assert.equal(button('刷新空间').disabled, true)
  await click(button('刷新空间'))
  assert.equal(ui.calls.length, 1)
  await ui.render({ active: false })
  assert.equal(ui.calls[0].signal.aborted, true)
  await act(async () => finish(measurement))
  assert.doesNotMatch(text(), /4.0 KiB/)
})
