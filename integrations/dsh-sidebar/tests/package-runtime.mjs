/** Executable integration probe, invoked only against a freshly extracted ZIP. */
import assert from 'node:assert/strict'
import { mkdir, readFile, realpath, symlink } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
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
// The extracted ZIP imports its host-side peer exactly as a real profile does:
// the package is linked into node_modules next to the plugin's files. Resolve
// the peer formally from the plugin workspace, never via a checkout path.
{
  const resolved = await realpath(require.resolve('@deepseek-ai/schemastery/package.json'))
  await mkdir(join(release, 'node_modules/@deepseek-ai'), { recursive: true })
  await symlink(dirname(resolved), join(release, 'node_modules/@deepseek-ai/schemastery'), 'dir').catch(error => {
    if (error.code !== 'EEXIST') throw error
  })
}
const queue = []
const target = { mode: 'queue', pendingQueue: queue, load: row => queue.push(row) }
const dom = new JSDOM('<div id="preview"></div>', { url: 'http://owner/' })
globalThis.window = dom.window
globalThis.document = dom.window.document
globalThis.IS_REACT_ACT_ENVIRONMENT = true
// This is the SDK package's public browser entry, not a reimplementation of its loader.
const copies = []
// The factory runs in a separate realm but mounts in a real browser-like DOM.
// Expose the complete Window/Document, not a loader-only window placeholder.
dom.window.__ModuleLoader__ = target
const sandbox = { window: dom.window, document: dom.window.document, navigator: { clipboard: { writeText: async text => copies.push(text) } }, TextEncoder, TextDecoder, DOMException, AbortController, AbortSignal, URLSearchParams, setTimeout, clearTimeout, console }
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
  'plugins.bundle.config': { kind: 'keyed', scope: 'root' },
} }, () => null)
const types = new Map(), routes = new Map(), requests = [], tools = new Set()
const configSubscribers = new Set()
// A read-only settings carrier adapter, not a replacement for Host persistence.
// The actual settings write/volatile update is exercised through the real GUI.
const configSnapshot = { status: 'ready', value: { vtxExecutable: '/opt/trusted/vtx', trustedPath: '/usr/local/bin:/usr/bin:/bin' }, revision: 1, writable: false }
// This-sensitive methods on purpose: the native ConfigForm reads this.store,
// so this adapter must fail the same way if the client ever detaches a method
// from its receiver when materializing the real ZIP bundle's config UI.
const configForm = {
  getSnapshot() { if (this !== configForm) throw new TypeError('Cannot read properties of undefined (reading \'store\') — lost receiver'); return configSnapshot },
  subscribe(listener) { if (this !== configForm) throw new TypeError('lost receiver on subscribe'); configSubscribers.add(listener); return () => configSubscribers.delete(listener) },
  async mutate() { if (this !== configForm) throw new TypeError('lost receiver on mutate'); throw new Error('Read-only settings adapter cannot write') },
}
let configServing = 0
const fs = new MemoryFs(), subprocess = fixtureSubprocess()
const services = {
  configForms: {
    get: namespace => { assert.equal(namespace, 'framivra-readonly'); return configForm },
    whileServed: (namespaces, register) => {
      assert.deepEqual([...namespaces], ['framivra-readonly'])
      configServing++
      const dispose = register(new Set(namespaces))
      return () => { configServing--; dispose?.() }
    },
  },
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
  // Official-public-service DI prerequisites, provided as deliberately UNAVAILABLE
  // faces — the same minimal shape the official runtime's own browser-plugin
  // tests use (dsh-v0.2.1-rc.2 packages/client/ui-schedule/tests/browser-plugin.client.spec.ts:115
  // `ctx.provide('conversation', {} as never)`; packages/client/ui-sidebar-right/tests/apply.client.spec.ts:79
  // `ctx.provide('uiSession', { adapter: { current } })`). Their presence satisfies
  // the client plugin's `inject` list so real Cordis activation runs; the absent
  // draft/input faces keep the negative DRAFT_UNAVAILABLE path intact (no live
  // conversation controller, no navigation snapshot, no extra control requests).
  conversation: {},
  uiSession: { adapter: { current: null } },
  workingDirectory: { get: () => '/workspace', ensure: async () => '/workspace' }, fs, subprocess,
  tools: { register: definition => { tools.add(definition.name); return () => tools.delete(definition.name) } },
}
const disposers = Object.entries(services).map(([name, service]) => ctx.reflect.provide(name, service))
// Config is enforced by the real fiber path before apply runs: a relative
// executable never reaches the Host.
{
  const invalid = ctx.plugin(hostPlugin, { vtxExecutable: './vtx' })
  await assert.rejects(invalid.await(), error => /vtxExecutable/.test(String(error?.message)))
  await invalid.dispose().catch(() => {})
}
const hostFiber = ctx.plugin(hostPlugin, { vtxExecutable: '/opt/trusted/vtx' })
const clientFiber = ctx.plugin(client)
await hostFiber.await()
await clientFiber.await()
assert.equal(types.get('framivra').id, manifest.name)
assert.equal(types.get('framivra').priority, 'extension')
assert.deepEqual([...routes.keys()].sort(), ['/api/framivra/control', '/api/framivra/diagnostics', '/api/framivra/frame', '/api/framivra/kernel-management', '/api/framivra/media'])
assert.deepEqual(routes.get('/api/framivra/control').methods, ['POST'])
assert.equal(routes.get('/api/framivra/control').requestBody, 'buffered')
assert.deepEqual(routes.get('/api/framivra/diagnostics').methods, ['POST'])
assert.equal(routes.get('/api/framivra/diagnostics').requestBody, 'buffered')
assert.deepEqual(routes.get('/api/framivra/kernel-management').methods, ['POST'])
assert.equal(routes.get('/api/framivra/kernel-management').requestBody, 'buffered')
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
// Tab fixture mirrors the INSTALLED @deepseek-ai/dsh-client-ui-sidebar-right
// (0.2.2-rc.1) runtime contract exactly as verified in its lib/client.js
// (TabDomain tabActions -> place()): the tab-scoped actions' openTab accepts
// a BOOLEAN replaceTab and the runtime itself resolves `true` to the CURRENT
// occurrence TabId for the controller-level openTabIn; a raw TabId passed by
// a tab is silently dropped by that translation, so only the boolean spelling
// can produce a replace here. useTabInfo() carries the tab record (id, signal)
// plus the actions face, per the shipped tabInfoFactory shape.
const TAB_ID = 'tab-guide-current'
let openedTab, rawOpenOptions
const useTabInfo = () => ({ tab: { id: TAB_ID, signal: tab.signal, actions: {
  openTab(kind, options = {}) {
    rawOpenOptions = options
    openedTab = { kind, options: { ...(options.replaceTab === true ? { replaceTab: TAB_ID } : {}) } }
  },
} } })
const root = createRoot(document.getElementById('preview'))
const configuration = core.entries('plugins.bundle.config')[0]
assert.equal(configuration.options.key, manifest.name)
assert.equal(configServing, 1)
await act(async () => root.render(React.createElement(configuration.component, { view: 'page' })))
assert.equal(document.getElementById('framivra-config-executable').value, '/opt/trusted/vtx')
assert.equal(document.getElementById('framivra-config-executable').disabled, true)
assert(document.body.textContent.includes('当前宿主配置只读'))
assert.equal(configSubscribers.size, 1)
await act(async () => root.render(React.createElement(configuration.component, { view: 'summary' })))
assert.equal(document.getElementById('preview').childNodes.length, 0)
assert.equal(configSubscribers.size, 0)
const guide = core.entries('sidebar.right.tab.guide.entry')[0]
await act(async () => root.render(React.createElement(guide.component, { useTabInfo })))
await act(async () => document.querySelector('button').click())
assert.equal(openedTab.kind, 'framivra')
// The plugin sends the tab-scoped BOOLEAN placement (strict true, never a raw
// TabId), and the runtime translation above resolves it to the CURRENT tab id.
assert.equal(rawOpenOptions.replaceTab, true)
assert.equal(openedTab.options.replaceTab, TAB_ID)
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
  assert.deepEqual(requests.filter(x => x.op !== 'proposal-list').map(x => x.op), ['open', 'query'])
  assert.equal(requests.filter(x => x.op === 'proposal-list').length, 1)
  assert.deepEqual([...tools], ['framivra_timeline_read', 'framivra_timeline_propose', 'framivra_proposal_status'])
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
  assert.equal(core.entries('plugins.bundle.config').length, 0)
  assert.equal(configServing, 0)
  assert.equal(configSubscribers.size, 0)
  await hostFiber.dispose()
  assert.equal(routes.size, 0)
  assert.equal(tools.size, 0)
  console.log(JSON.stringify({ pass: true, moduleSystem: 'public SDK browser entry', sharedReactHooks: true, cordisInjection: true, slotLedger: 'public SlotCore; owner/renderer adapter', packagedHostControl: true, uiOpenQueryClose: true, authentication: 'UNRUN' }))
} finally {
  await clientFiber.dispose()
  await hostFiber.dispose()
  disposeOwner()
  for (const dispose of disposers.reverse()) await dispose()
  dom.window.close()
}
