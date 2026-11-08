/** Native React DOM events + actual public local fs/subprocess; JSDOM, not Chrome. */
import assert from 'node:assert/strict'
import { readFile, writeFile, mkdir, mkdtemp } from 'node:fs/promises'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'
import { spawnSync } from 'node:child_process'
import { runInNewContext } from 'node:vm'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { fixture } from './manual-env.mjs'
import { parseJSON, stringifyJSON } from '../src/json.mjs'
export { skip } from './manual-env.mjs'
const require = createRequire(import.meta.url)

export async function selectionEnvironment(t, kind) {
  const evidence = process.env.SIDEBAR_EVIDENCE_DIR
  await mkdir(evidence, { recursive: true })
  const dir = await mkdtemp(join(evidence, `selection-${kind}-`)), path = join(dir, 'timeline.json')
  const root = process.env.SIDEBAR_DSH_ROOT
  const imp = name => import(pathToFileURL(join(root, name)))
  const { Context } = await imp('vendor/cordis/lib/index.js')
  const { LocalSubprocessRuntime } = await imp('packages/subprocess/subprocess-local/lib/index.js')
  const { LocalFileSystem } = await imp('packages/fs/fs-local/lib/index.js')
  const ctx = new Context(), sf = ctx.plugin(LocalSubprocessRuntime), ff = ctx.plugin(LocalFileSystem, { cwd: dir })
  await sf.await(); await ff.await()
  const runtime = ctx.get('subprocess'), children = [], specs = [], wire = []
  const subprocess = { resolveExecutable: (...args) => runtime.resolveExecutable(...args), spawn(spec) { specs.push(spec); const child = runtime.spawn(spec); children.push(child); return child } }
  const seed = subprocess.spawn({ argv: [process.env.SIDEBAR_REAL_VTX, '-t', path, 'store', '--request-stdin'], cwd: '/', env: { PYTHONPATH: undefined, PYTHONHOME: undefined, PYTHONDONTWRITEBYTECODE: '1', PYTHONSAFEPATH: '1' }, stdio: { stdin: { data: stringifyJSON({ operation: 'create', op_id: 'selection-seed', base_revision: null, document: fixture() }) }, stdout: { maxBytes: 8388608 }, stderr: { maxBytes: 16384 } }, graceMs: 1000 })
  try { assert.equal((await seed.done).exitCode, 0, seed.collected.stdout.readFrom(0).text + seed.collected.stderr.readFrom(0).text) } finally { seed.terminate(); await seed.waitForExit(AbortSignal.timeout(5000)) }
  let client, createPreviewHost, sandbox
  if (kind === 'zip') {
    const extracted = join(dir, 'extracted')
    const extraction = spawnSync('python3', ['-I', '-B', '-c', 'import zipfile,sys;zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])', process.env.SIDEBAR_MANUAL_ZIP, extracted], { encoding: 'utf8' })
    assert.equal(extraction.status, 0, extraction.stderr)
    ;({ createPreviewHost } = await import(pathToFileURL(join(extracted, 'lib/host.js'))))
    const queue = [], target = { mode: 'queue', pendingQueue: queue, load: row => queue.push(row) }
    sandbox = { window: { __ModuleLoader__: target }, TextEncoder, AbortController, AbortSignal, URLSearchParams, crypto: globalThis.crypto, setTimeout, clearTimeout, console }
    runInNewContext(await readFile(require.resolve('@deepseek-ai/dsh-client-modules/client'), 'utf8'), sandbox)
    const modules = queue.shift().factory(() => { throw Error('unexpected bootstrap dependency') })
    runInNewContext(await readFile(join(extracted, 'lib/client.js'), 'utf8'), sandbox)
    const manifest = JSON.parse(await readFile(join(extracted, 'package.json'), 'utf8'))
    client = await modules.createClientModuleSystem(target, { id: 'bootstrap', exports: {} }, { boot: { rev: '0', entries: [], batches: [] }, staticModules: { react: React } }).import(manifest.name)
  } else {
    client = await import('../src/client/index.mjs')
    ;({ createPreviewHost } = await import('../src/host.mjs'))
  }
  const host = createPreviewHost({ subprocess, fs: ctx.get('fs'), sessions: new Map([['one', { id: 'one' }]]), workingDirectory: { get: () => dir } }, { vtxExecutable: process.env.SIDEBAR_REAL_VTX })
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://owner/', pretendToBeVisual: true })
  const oldFetch = globalThis.fetch, oldNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
  globalThis.window = dom.window; globalThis.document = dom.window.document; globalThis.IS_REACT_ACT_ENVIRONMENT = true
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator })
  const copies = [], pending = new Set(), held = []
  Object.defineProperty(dom.window.navigator, 'clipboard', { configurable: true, value: { writeText: async raw => copies.push(raw) } })
  // Hold responses only, never manufacture a kernel answer. Exercises stale async UI.
  const faults = {}
  globalThis.fetch = (_url, init) => {
    const request = parseJSON(init.body)
    const promise = (async () => {
      const response = await host.control(new Request('http://owner/api/framivra/control', { method: 'POST', body: init.body, signal: init.signal }))
      const text = await response.text(); wire.push({ request, status: response.status, response: parseJSON(text) })
      if (faults.hold?.(request)) await new Promise(resolve => held.push(resolve))
      return new Response(text, { status: response.status })
    })()
    pending.add(promise); promise.finally(() => pending.delete(promise)); return promise
  }
  if (sandbox) { sandbox.fetch = globalThis.fetch; sandbox.navigator = dom.window.navigator }
  const { createRoot } = require('react-dom/client'), reactRoot = createRoot(document.getElementById('root')), tab = new AbortController()
  const button = text => [...document.querySelectorAll('button')].find(node => node.textContent === text)
  async function settle(predicate) { for (let i = 0; i < 1000; i++) { if (predicate()) return; await act(async () => { await new Promise(r => setTimeout(r, 5)) }) } assert.fail(document.body.textContent) }
  async function flush() { for (let i = 0; i < 12; i++) { await act(async () => { if (pending.size && !faults.hold) await Promise.allSettled([...pending]); await new Promise(r => setImmediate(r)) }); if (!pending.size && i > 1) break } }
  async function click(node, init = {}) { if (typeof node === 'string') node = button(node); assert(node, 'missing click target'); assert(!node.disabled, node.textContent); await act(async () => node.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true, cancelable: true, ...init }))); if (!faults.hold) await flush() }
  async function change(title, value) { const node = [...document.querySelectorAll('label')].find(n => n.firstChild?.textContent === title)?.querySelector('input,select'); assert(node, title); await act(async () => { Object.getOwnPropertyDescriptor(node.tagName === 'SELECT' ? dom.window.HTMLSelectElement.prototype : dom.window.HTMLInputElement.prototype, 'value').set.call(node, value); node.dispatchEvent(new dom.window.Event('input', { bubbles: true })); node.dispatchEvent(new dom.window.Event('change', { bubbles: true })) }); await flush() }
  async function event(node, type, props = {}) { await act(async () => { const e = type.startsWith('key') ? new dom.window.KeyboardEvent(type, { bubbles: true, cancelable: true, ...props }) : new dom.window.MouseEvent(type, { bubbles: true, cancelable: true, ...props }); if (type.startsWith('pointer')) Object.defineProperty(e, 'pointerId', { value: props.pointerId ?? 1 }); node.dispatchEvent(e) }); await flush() }
  t.after(async () => { faults.hold = undefined; held.splice(0).forEach(resolve => resolve()); tab.abort(); await flush(); await act(async () => reactRoot.unmount()); await flush(); await host.dispose(); for (const child of children) await child.waitForExit(AbortSignal.timeout(5000)); await sf.dispose(); await ff.dispose(); await writeFile(join(dir, 'wire.json'), stringifyJSON({ wire, processes: children.length, allReaped: true, nativeEvents: true, fs: 'actual public LocalFileSystem', provider: 'actual public LocalSubprocessRuntime', layout: 'JSDOM; not Chrome' }, 2)); dom.window.close(); delete globalThis.window; delete globalThis.document; globalThis.fetch = oldFetch; if (oldNavigator) Object.defineProperty(globalThis, 'navigator', oldNavigator) })
  await act(async () => reactRoot.render(React.createElement(client.PreviewBody, { sessionId: 'one', useTabInfo: () => ({ tab: { signal: tab.signal } }), getDraftServices: () => ({}) })))
  await click('打开 / 刷新'); await settle(() => document.querySelector('.fv-ruler'))
  await click('明确允许本会话向此工程及 core 历史写入'); await settle(() => document.body.textContent.includes('写授权：已明确授予'))
  return { dir, path, button, click, change, event, settle, flush, wire, specs, copies, faults, held,
    queue: () => JSON.parse(document.querySelector('[aria-label="未保存 typed intents"]').textContent),
    clip: index => document.querySelectorAll('.fv-clip')[index],
    read: () => readFile(path, 'utf8'), save: (name, data) => writeFile(join(dir, name), typeof data === 'string' ? data : stringifyJSON(data, 2)),
    async planCommit() { await click('内核预检计划（零写）'); await settle(() => button('确认提交此冻结计划（一次 revision）')); await click('确认提交此冻结计划（一次 revision）'); await settle(() => !button('确认提交此冻结计划（一次 revision）') && !document.querySelector('[aria-label="手动语义编辑"] fieldset').disabled); await flush() },
    async undo() { await click('撤销当前 core head（新 CAS 事务）'); await settle(() => !document.querySelector('[aria-label="手动语义编辑"] fieldset').disabled); await flush() },
  }
}
