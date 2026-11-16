import test from 'node:test'
import assert from 'node:assert/strict'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { secondsFrame, Transport } from '../src/client/editor-controls.mjs'

test('seconds plus zero-based offset converts exactly and rejects ambiguous/unsafe tokens', () => {
  assert.equal(secondsFrame('10', '5'), 305)
  assert.equal(secondsFrame('0', '0'), 0)
  assert.equal(secondsFrame('300239975158033', '1'), Number.MAX_SAFE_INTEGER)
  for (const pair of [['1','30'], ['-0','0'], ['01','0'], ['1.0','0'], ['1e1','0'], [' 1','0'], ['','0'], ['1','05'], ['300239975158033','2'], [10,'5']]) assert.throws(() => secondsFrame(...pair))
})

test('time helper changes only canonical pin on explicit conversion and never queries/renders', async t => {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://owner/' })
  globalThis.window = dom.window; globalThis.document = dom.window.document; globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const { createRoot } = await import('react-dom/client')
  const { Simulate } = await import('react-dom/test-utils')
  const root = createRoot(document.getElementById('root')), pins = []
  t.after(async () => { await act(async () => root.unmount()); dom.window.close(); delete globalThis.window; delete globalThis.document })
  await act(async () => root.render(React.createElement(Transport, { frame: 0, frameText: '0', duration: 600, onFrame: value => pins.push(value), onText: () => assert.fail('helper must not change canonical text until explicit conversion'), onQuery: () => assert.fail('no query') })))
  const change = async (name, value) => act(async () => Simulate.change(document.querySelector(`[name="${name}"]`), { target: { value } }))
  await change('framivra-seconds', '10'); await change('framivra-offset', '5')
  assert.deepEqual(pins, [])
  assert(document.body.textContent.includes('全局帧 305'))
  const convert = [...document.querySelectorAll('button')].find(button => button.textContent === '换算定位（不渲染）')
  await act(async () => convert.click())
  assert.deepEqual(pins, ['305'])
  await change('framivra-offset', '30'); await act(async () => convert.click())
  assert.deepEqual(pins, ['305'])
  assert(document.querySelector('[role="alert"]').textContent.includes('0–29'))
})
