import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { ToolButton, ToolSummary, iconControlCSS } from '../src/client/icon-controls.mjs'
import { Icon, iconNodes } from '../src/client/icons.mjs'

const h = React.createElement
async function fixture(t) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://owner/' })
  const saved = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]))
  globalThis.window = dom.window; globalThis.document = dom.window.document; globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const { createRoot } = await import('react-dom/client')
  const { Simulate } = await import('react-dom/test-utils')
  const root = createRoot(document.getElementById('root'))
  const opens = [], closes = []
  dom.window.HTMLElement.prototype.showPopover = function () { opens.push(this); this.dataset.open = 'true' }
  dom.window.HTMLElement.prototype.hidePopover = function () { closes.push(this); delete this.dataset.open }
  t.after(async () => { await act(async () => root.unmount()); dom.window.close(); for (const [key, descriptor] of Object.entries(saved)) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key] } })
  return { dom, root, Simulate, opens, closes, render: async element => act(async () => root.render(element)), button: () => document.querySelector('button'), tip: () => document.querySelector('[role=tooltip]') }
}

test('icon library: all 40 vendored SVGs are structured decorative nodes, no text or external resources', async t => {
  const f = await fixture(t)
  assert.equal(Object.keys(iconNodes).length, 40)
  assert.equal(createHash('sha256').update(JSON.stringify(iconNodes)).digest('hex'), '390ff15e1b2ccdf697ba2a12b1d6eebc46506565b66055067668b89a5971c6d7', 'all 40 node arrays retain their independently verified upstream identity')
  const safeTags = new Set(['path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon'])
  for (const [name, nodes] of Object.entries(iconNodes)) {
    assert(nodes.length > 0, name)
    for (const [tag, attributes] of nodes) {
      assert(safeTags.has(tag), `${name}: trusted primitive only`)
      assert(!Object.keys(attributes).some(key => /^(?:on|href|style|src)/i.test(key)), `${name}: no event or external resource attributes`)
    }
  }
  await f.render(h(Icon, { name: 'camera' }))
  const svg = document.querySelector('svg')
  assert.equal(svg.getAttribute('aria-hidden'), 'true')
  assert.equal(svg.getAttribute('stroke-width'), '2')
  assert.equal(svg.getAttribute('viewBox'), '0 0 24 24')
  assert(svg.querySelector('circle'), 'actual Lucide structured circle is retained')
  assert.equal(svg.textContent, '')
})

test('icon button: native type, accessible hidden name, no visible text, pressed state and explicit activation', async t => {
  const f = await fixture(t), ref = { current: null }, calls = []
  await f.render(h(ToolButton, { icon: 'folder', label: '打开工程', tooltip: '打开工作区内的时间线', 'aria-pressed': true, elementRef: ref, onClick: () => calls.push('open') }))
  const button = f.button()
  assert.equal(button.tagName, 'BUTTON'); assert.equal(button.type, 'button')
  assert.equal(button.getAttribute('aria-label'), '打开工程')
  assert.equal(button.textContent, '打开工程')
  assert(button.querySelector('span.fv-sr-only'), 'persistent label is visually hidden, not removed from assistive tech')
  assert.equal(button.getAttribute('aria-pressed'), 'true')
  assert.equal(button.getAttribute('aria-describedby'), f.tip().id)
  assert.equal(ref.current, button)
  assert.deepEqual(calls, [])
  await act(async () => button.click())
  assert.deepEqual(calls, ['open'])
  assert(iconControlCSS.includes('clip-path:inset(50%)') && iconControlCSS.includes('@media(pointer:coarse)'))
})

test('icon tooltip: focus/hover show top-layer help, Escape dismisses without activating or closing ancestor', async t => {
  const f = await fixture(t), parent = [], actions = []
  await f.render(h('div', { onKeyDown: event => parent.push(event.key) }, h(ToolButton, { icon: 'query', label: '查询', onClick: () => actions.push('query') })))
  await act(async () => f.Simulate.focus(f.button()))
  assert.equal(f.tip().hidden, false); assert(f.opens.includes(f.tip()))
  assert.equal(f.button().hasAttribute('title'), false, 'native tooltip does not duplicate custom help')
  await act(async () => f.Simulate.keyDown(f.button(), { key: 'Escape' }))
  assert.equal(f.tip().hidden, true); assert.deepEqual(parent, []); assert.deepEqual(actions, [])
  assert.equal(f.button().title, '查询')
  await act(async () => f.Simulate.keyDown(f.button(), { key: 'Escape' }))
  assert.deepEqual(parent, ['Escape'], 'subsequent Escape may reach the actual dialog')
})

test('icon tooltip: blur, scroll, resize, document hidden, prop retirement and unmount release live listeners', async t => {
  const f = await fixture(t)
  await f.render(h(ToolButton, { icon: 'copy', label: '复制' }))
  const win = f.dom.window, live = new Map()
  const add = win.addEventListener.bind(win), remove = win.removeEventListener.bind(win)
  win.addEventListener = (type, fn, options) => { if (['scroll', 'resize', 'blur'].includes(type)) live.set(fn, (live.get(fn) ?? 0) + 1); return add(type, fn, options) }
  win.removeEventListener = (type, fn, options) => { if (['scroll', 'resize', 'blur'].includes(type)) live.set(fn, (live.get(fn) ?? 0) - 1); return remove(type, fn, options) }
  const open = async () => { await act(async () => f.Simulate.pointerEnter(f.button())); assert.equal(f.tip().hidden, false); assert.equal([...live.values()].reduce((a, b) => a + b, 0), 3) }
  for (const type of ['scroll', 'resize', 'blur']) {
    await open(); await act(async () => win.dispatchEvent(new win.Event(type)))
    assert.equal(f.tip().hidden, true); assert.equal([...live.values()].reduce((a, b) => a + b, 0), 0)
  }
  await open(); Object.defineProperty(win.document, 'hidden', { configurable: true, value: true })
  await act(async () => win.document.dispatchEvent(new win.Event('visibilitychange')))
  assert.equal(f.tip().hidden, true)
  await open(); await f.render(h(ToolButton, { icon: 'copy', label: '复制新对象' }))
  assert.equal(f.tip().hidden, true); assert.equal(f.button().title, '复制新对象')
  assert.equal([...live.values()].reduce((a, b) => a + b, 0), 0)
  await open(); await f.render(null)
  assert.equal([...live.values()].reduce((a, b) => a + b, 0), 0, 'unmounted help must not retain its window')
})

test('icon tooltip: hoverable help survives travel, pending leave timer cannot outlive its control', async t => {
  const f = await fixture(t)
  await f.render(h(ToolButton, { icon: 'info', label: '说明' }))
  const win = f.dom.window, timers = new Map(), clears = []
  const savedSet = win.setTimeout, savedClear = win.clearTimeout
  win.setTimeout = callback => { const id = Symbol(); timers.set(id, callback); return id }
  win.clearTimeout = id => { clears.push(id); timers.delete(id) }
  t.after(() => { win.setTimeout = savedSet; win.clearTimeout = savedClear })
  await act(async () => f.Simulate.pointerEnter(f.button()))
  await act(async () => f.Simulate.pointerLeave(f.button()))
  assert.equal(timers.size, 1)
  await act(async () => f.Simulate.pointerEnter(f.tip()))
  assert.equal(timers.size, 0); assert.equal(f.tip().hidden, false)
  await act(async () => f.Simulate.pointerLeave(f.tip()))
  assert.equal(timers.size, 1)
  await f.render(null)
  assert.equal(timers.size, 0); assert(clears.length >= 2)
})

test('icon controls: disabled actions refuse activation and retain native help; unsupported popover fails to native title', async t => {
  const f = await fixture(t), calls = []
  await f.render(h(ToolButton, { icon: 'remove', label: '移除片段', tooltip: '请先授予写许可', disabled: true, onClick: () => calls.push('remove') }))
  await act(async () => { f.button().click(); f.Simulate.pointerEnter(f.button()) })
  assert.deepEqual(calls, []); assert.equal(f.tip().hidden, true); assert.equal(f.button().title, '请先授予写许可')
  delete f.dom.window.HTMLElement.prototype.showPopover
  await f.render(h(ToolButton, { icon: 'copy', label: '复制', onClick: () => calls.push('copy') }))
  await act(async () => f.Simulate.focus(f.button()))
  assert.equal(f.tip().hidden, true); assert.equal(f.button().title, '复制')
  await act(async () => f.button().click())
  assert.deepEqual(calls, ['copy'])
})

test('icon disclosure: stays a native first summary with an accessible name, not a button nested in summary', async t => {
  const f = await fixture(t)
  await f.render(h('details', null, h(ToolSummary, { icon: 'path', label: '通过路径打开' }), h('p', null, '路径表单')))
  const details = document.querySelector('details'), summary = details.firstElementChild
  assert.equal(summary.tagName, 'SUMMARY'); assert.equal(summary.getAttribute('aria-label'), '通过路径打开')
  assert.equal(summary.querySelector('button'), null); assert(summary.querySelector('svg'))
  assert.equal(summary.querySelector('span').className, 'fv-sr-only')
})
