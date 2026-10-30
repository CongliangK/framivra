/** Executable integration probe, invoked only against a freshly extracted ZIP. */
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { runInNewContext } from 'node:vm'
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { JSDOM } from 'jsdom'
import { Context } from '@deepseek-ai/cordis'
import { SlotCore } from '@deepseek-ai/dsh-client-ui-slots'
import { MemoryFs, fixtureSubprocess } from './helpers.mjs'

const [release] = process.argv.slice(2)
const manifest = JSON.parse(await readFile(join(release, 'package.json'), 'utf8'))
const require = createRequire(import.meta.url)
const queue = []
const target = { mode: 'queue', pendingQueue: queue, load: row => queue.push(row) }
const dom = new JSDOM('<div id="preview"></div>', { url: 'http://owner/' })
globalThis.window = dom.window
globalThis.document = dom.window.document
globalThis.IS_REACT_ACT_ENVIRONMENT = true
// This is the SDK package's public browser entry, not a reimplementation of its loader.
const copies = []
const sandbox = { window: { __ModuleLoader__: target }, navigator: { clipboard: { writeText: async text => copies.push(text) } }, TextEncoder, AbortController, AbortSignal, URLSearchParams, console }
runInNewContext(await readFile(require.resolve('@deepseek-ai/dsh-client-modules/client'), 'utf8'), sandbox)
const bootstrap = queue.shift()
const modules = bootstrap.factory(() => { throw new Error('Unexpected bootstrap dependency') })
runInNewContext(await readFile(join(release, 'lib/client.js'), 'utf8'), sandbox)
assert.equal(queue.length, 1)
assert.equal(queue[0].id, manifest.name)
const system = modules.createClientModuleSystem(target, { id: 'bootstrap', exports: {} }, {
  boot: { rev: '0', entries: [], batches: [] }, staticModules: { react: React },
})
const client = await system.import(manifest.name)
assert.equal(await system.import(`${manifest.name}/client`), client)
const hostPlugin = await import(pathToFileURL(join(release, 'lib/index.js')))

// Real Cordis injection + real slot ledger; the owner/renderer and Fetch carrier
// below are adapters, not the complete DSH shell or authentication implementation.
const ctx = new Context()
const core = new SlotCore()
const disposeOwner = core.register({ name: 'root', children: {
  'sidebar.right.pane.tab': { kind: 'keyed', scope: 'session' },
  'sidebar.right.tab.guide.entry': { kind: 'keyed', scope: 'session' },
} }, () => null)
const types = new Map(), routes = new Map(), requests = []
const fs = new MemoryFs(), subprocess = fixtureSubprocess()
const services = {
  slots: {
    inject: (_name, register) => register(),
    register: (options, component) => core.register(options, component),
  },
  sidebarRightTabs: { register(definition) {
    assert.equal(types.has(definition.kind), false)
    types.set(definition.kind, definition)
    return () => types.delete(definition.kind)
  } },
  connection: { fetch: { register(route) {
    assert.equal(routes.has(route.path), false)
    routes.set(route.path, route)
    return () => routes.delete(route.path)
  } } },
  sessions: new Map([['one', { id: 'one' }]]),
  workingDirectory: { get: () => '/workspace' }, fs, subprocess,
}
const disposers = Object.entries(services).map(([name, service]) => ctx.reflect.provide(name, service))
const hostFiber = ctx.plugin(hostPlugin, { vtxExecutable: '/opt/trusted/vtx' })
const clientFiber = ctx.plugin(client)
await hostFiber.await()
await clientFiber.await()
assert.equal(types.get('framivra').id, manifest.name)
assert.equal(types.get('framivra').priority, 'extension')
assert.deepEqual([...routes.keys()].sort(), ['/api/framivra/control', '/api/framivra/frame', '/api/framivra/media'])
assert.deepEqual(routes.get('/api/framivra/control').methods, ['POST'])
assert.equal(routes.get('/api/framivra/control').requestBody, 'buffered')
assert.deepEqual(routes.get('/api/framivra/media').methods, ['GET', 'HEAD'])
assert.equal(routes.get('/api/framivra/media').requestBody, 'buffered')
assert.deepEqual(routes.get('/api/framivra/frame').methods, ['POST'])
assert.equal(routes.get('/api/framivra/frame').requestBody, 'streaming')
const fetchControl = async (path, init) => {
  const request = new Request(new URL(path, 'http://owner/'), init)
  requests.push(JSON.parse(init.body))
  const route = routes.get(new URL(request.url).pathname)
  assert(route.methods.includes(request.method))
  return route.fetch(request)
}
sandbox.fetch = fetchControl
const tab = new AbortController()
let openedKind
const useTabInfo = () => ({ tab: { signal: tab.signal, actions: {
  openTab(kind, options) { openedKind = { kind, options } },
} } })
const root = createRoot(document.getElementById('preview'))
const guide = core.entries('sidebar.right.tab.guide.entry')[0]
await act(async () => root.render(React.createElement(guide.component, { useTabInfo })))
await act(async () => document.querySelector('button').click())
assert.equal(openedKind.kind, 'framivra')
assert.equal(openedKind.options.replaceTab, true)
const entry = core.entries('sidebar.right.pane.tab')[0]
assert.equal(entry.options.key, manifest.name)
await act(async () => root.render(React.createElement(entry.component, { useTabInfo, ...entry.inject('one') })))
const form = () => document.querySelector('form')
const submit = node => node.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }))
const tick = () => new Promise(resolve => setTimeout(resolve, 10))
async function until(predicate) {
  const deadline = Date.now() + 10000
  while (!predicate()) {
    assert(Date.now() < deadline, `Preview did not settle: ${document.body.textContent}`)
    await act(tick)
  }
}
try {
  // Hook state/effects run under the exact same React instance supplied to the loader.
  await act(async () => submit(form()))
  await until(() => document.body.textContent.includes('Complete query envelope'))
  assert.deepEqual(requests.map(x => x.op), ['open', 'query'])
  assert(requests.every(x => x.sessionId === 'one'))
  assert(document.body.textContent.includes('Read-only ·'))
  assert(document.body.textContent.includes('9007199254740993'))
  assert(!document.body.textContent.includes('9007199254740992'))
  assert(document.body.textContent.includes('provenance UNKNOWN'))
  assert(document.querySelector('video').getAttribute('src').startsWith('/api/framivra/media?sessionId=one&grant='))
  const button = label => [...document.querySelectorAll('button')].find(node => node.textContent === label)
  await act(async () => button('选择块 /fixture/0/blocks/0').click())
  await until(() => document.body.textContent.includes('引用就绪。'))
  assert.equal(requests.at(-1).op, 'reference')
  assert.equal(requests.at(-1).query.block_addresses[0], '/fixture/0/blocks/0')
  assert(document.body.textContent.includes('legacy / 无协议稳定 ID'))
  await act(async () => button('复制 raw JSON').click())
  await until(() => copies.length === 1)
  assert.equal(copies[0], '{ "id":9007199254740993, "decimal":1.0000000000000001, "huge":1e400, "text":"原文" }')
  const beforeUnavailable = requests.length
  await act(async () => button('加入当前会话草稿（不发送）').click())
  assert(document.body.textContent.includes('DRAFT_UNAVAILABLE'))
  assert.equal(requests.length, beforeUnavailable)
  const field = document.querySelector('input[inputmode="numeric"]')
  const setValue = Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, 'value').set
  // ReactDOM was imported before jsdom, so use its public test event adapter.
  const { Simulate } = await import('react-dom/test-utils')
  await act(async () => { setValue.call(field, '1.0'); Simulate.change(field, { target: { value: '1.0' } }) })
  const before = requests.length
  await act(async () => submit(document.querySelectorAll('form')[1]))
  assert.equal(requests.length, before)
  assert(document.querySelector('[role="alert"]').textContent.includes('BAD_QUERY'))
  await act(async () => { setValue.call(field, '29'); Simulate.change(field, { target: { value: '29' } }) })
  await act(async () => submit(document.querySelectorAll('form')[1]))
  await until(() => document.body.textContent.includes('Complete query envelope'))
  assert.equal(requests.at(-1).op, 'query')
  assert.equal(requests.at(-1).query.frame, 29)
  assert(subprocess.calls.filter(x => x.argv).every(x => x.argv[0] === '/opt/trusted/vtx'))
  await act(async () => root.unmount())
  await until(() => requests.at(-1)?.op === 'close')
  await clientFiber.dispose()
  assert.equal(types.size, 0)
  assert.equal(core.entries('sidebar.right.pane.tab').length, 0)
  assert.equal(core.entries('sidebar.right.tab.guide.entry').length, 0)
  await hostFiber.dispose()
  assert.equal(routes.size, 0)
  console.log(JSON.stringify({ pass: true, moduleSystem: 'public SDK browser entry', sharedReactHooks: true, cordisInjection: true, slotLedger: 'public SlotCore; owner/renderer adapter', packagedHostControl: true, uiOpenQueryClose: true, authentication: 'UNRUN' }))
} finally {
  await clientFiber.dispose()
  await hostFiber.dispose()
  disposeOwner()
  for (const dispose of disposers.reverse()) await dispose()
  dom.window.close()
}
