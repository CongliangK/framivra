/** Opt-in probe of the newly built ZIP's actual public SDK factory (not a fake module). */
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { join } from 'node:path'
import { runInNewContext } from 'node:vm'
import React, { act, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { Simulate } from 'react-dom/test-utils'
import { JSDOM } from 'jsdom'

const [release] = process.argv.slice(2)
assert(release, 'Pass an extracted candidate ZIP directory')
const require = createRequire(import.meta.url)
const manifest = JSON.parse(await readFile(join(release, 'package.json'), 'utf8'))
const queue = [], target = { mode: 'queue', pendingQueue: queue, load: row => queue.push(row) }
const sandbox = { window: { __ModuleLoader__: target }, TextEncoder, AbortController, AbortSignal, URLSearchParams, console }
runInNewContext(await readFile(require.resolve('@deepseek-ai/dsh-client-modules/client'), 'utf8'), sandbox)
const modules = queue.shift().factory(() => { throw new Error('Unexpected bootstrap dependency') })
const bundled = await readFile(join(release, 'lib/client.js'), 'utf8')
runInNewContext(bundled, sandbox)
assert.equal(queue.length, 1)
assert.equal(queue[0].id, manifest.name)
const system = modules.createClientModuleSystem(target, { id: 'bootstrap', exports: {} }, { boot: { rev: '0', entries: [], batches: [] }, staticModules: { react: React } })
const client = await system.import(manifest.name)
const dom = new JSDOM('<div id="root"></div>', { url: 'http://owner/' })
globalThis.window = dom.window; globalThis.document = dom.window.document; globalThis.IS_REACT_ACT_ENVIRONMENT = true
const root = createRoot(document.getElementById('root'))
const version = `sha256:${'a'.repeat(64)}`, coverage = { complete: false, unexpanded: [{ reference: 'external.srt' }] }
const snapshot = { content_version: version, coverage, diagnostics: [], document: { project: { duration_frames: 100, fps: 30 }, extension: JSON.rawJSON('9007199254740993') } }
const track = { family: 'audio', track_index: 0, address: '/audio_tracks/0', data: { name: '音频轨' } }
const result = { content_version: version, coverage, diagnostics: [], tracks: [track], blocks: [[0, 90], [20, 70], [30, 60]].map((range, block_index) => ({ family: 'audio', track_index: 0, block_index, address: `${track.address}/clips/${block_index}`, range, data: { name: `音频${block_index}` } })) }
const selections = []
function Harness() {
  const [frame, setFrame] = useState(''), [selected, setSelected] = useState()
  let canonical, error
  try { canonical = client.frameQuery(frame).frame } catch (e) { error = e.message }
  return React.createElement(React.Fragment, null,
    React.createElement('input', { value: frame, onChange: event => setFrame(event.target.value), 'aria-label': 'Frame input' }),
    error ? React.createElement('p', { role: 'alert' }, error) : null,
    React.createElement(client.TimelineView, { snapshot, result, frame: canonical, onFrame: frame => setFrame(String(frame)), activeSelection: selected, onSelect: value => { selections.push(value); setSelected(value) } }))
}
try {
  await act(async () => root.render(React.createElement(Harness)))
  assert.equal(document.querySelectorAll('.fv-clip').length, 3)
  assert.equal(document.querySelectorAll('.fv-row').length, 3)
  assert(document.querySelector('style').textContent.includes('.fv-timeline .fv-clip'))
  assert.equal(document.querySelector('link[rel="stylesheet"]'), null)
  assert(document.body.textContent.includes('external.srt'))
  assert(document.body.textContent.includes('9007199254740993'))
  const ruler = document.querySelector('.fv-ruler')
  ruler.getBoundingClientRect = () => ({ left: 0, width: 100 })
  await act(async () => Simulate.pointerDown(ruler, { button: 0, clientX: 25, pointerId: 1 }))
  assert.equal(document.querySelector('input').value, '25')
  await act(async () => Simulate.keyDown(ruler, { key: 'ArrowRight', preventDefault() {} }))
  assert.equal(document.querySelector('input').value, '26')
  await act(async () => document.querySelectorAll('.fv-clip')[1].click())
  assert.equal(selections[0].address, result.blocks[1].address)
  assert(document.querySelector('[aria-label="活动引用"]').textContent.includes(result.blocks[1].address))
  await act(async () => Simulate.change(document.querySelector('input'), { target: { value: '1e0' } }))
  assert(document.querySelector('[role="alert"]').textContent.includes('BAD_QUERY'))
  assert(document.body.textContent.includes('未设置（空白或无效输入）'))
  assert.equal(bundled.includes('sourceMappingURL='), false)
  console.log(JSON.stringify({ ok: true, moduleId: manifest.name, audioClipButtons: 3, overlappingDisplayRows: 3, sharedReact: React.version, scopedInlineStyles: true, publicFactory: true, geometryAdapter: true, noBrowserLayoutClaim: true }))
} finally { await act(async () => root.unmount()); system.dispose?.(); dom.window.close() }
