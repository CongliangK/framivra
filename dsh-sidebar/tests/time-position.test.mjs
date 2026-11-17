import test from 'node:test'
import assert from 'node:assert/strict'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { secondsFrame, Transport } from '../src/client/editor-controls.mjs'
import { parseJSON } from '../src/json.mjs'
import { projectClock, formatTimecode, parseTimecode, frameToSeconds, frameFromSeconds } from '../src/client/timecode.mjs'
import { frameQuery } from '../src/client/index.mjs'

const fpsToken = token => parseJSON(`{"fps":${token}}`).fps

test('project clocks preserve exact supported integer and all NTSC identities, with no guessed fallback', () => {
  for (const rate of [24, 25, 30, 48, 50, 60, 100, 120]) {
    assert.deepEqual(projectClock(rate), { num: rate, den: 1, nominal: rate })
    assert.deepEqual(projectClock(fpsToken(`${rate}.0`)), { num: rate, den: 1, nominal: rate })
  }
  for (const [label, nominal] of [['23.976', 24], ['29.97', 30], ['47.952', 48], ['59.94', 60], ['119.88', 120]]) {
    const expected = { num: nominal * 1000, den: 1001, nominal }
    assert.deepEqual(projectClock(fpsToken(label)), expected)
    assert.deepEqual(projectClock(`${nominal * 1000}/1001`), expected)
  }
  for (const rate of [undefined, null, 0, 29.97, 29, '30', fpsToken('30.0000000000000000000001')]) {
    assert.equal(projectClock(rate), null)
    assert.equal(formatTimecode(30, rate), null)
    assert.equal(parseTimecode('00:01:00', rate), null)
    assert.equal(frameToSeconds(30, rate), null)
    assert.equal(frameFromSeconds(1, rate), null)
  }
})

test('timecode is exact, strictly reversible, unlimited-minute nominal non-drop counting', () => {
  for (const rate of [24, 25, 30, 48, 50, 60, 100, 120, '24000/1001', '30000/1001', '48000/1001', '60000/1001', '120000/1001']) {
    const { nominal } = projectClock(rate)
    for (const frame of [0, 1, nominal - 1, nominal, nominal * 60 - 1, nominal * 60, nominal * 3600, Number.MAX_SAFE_INTEGER]) {
      assert.equal(parseTimecode(formatTimecode(frame, rate), rate), frame, `${rate}, ${frame}`)
    }
  }
  assert.equal(formatTimecode(1800, '30000/1001'), '01:00:00')
  assert.equal(formatTimecode(17982, '30000/1001'), '09:59:12', 'no invented drop-frame skipping')
  assert.equal(formatTimecode(360000, 30), '200:00:00', 'minutes never wrap at 60')
  assert.equal(formatTimecode(120, 120), '00:01:000')
  for (const text of ['', '0:00:00', '000:00:00', '00:60:00', '00:00:30', '00:00:000', ' 00:00:00', '00:00:00 ', '+00:00:00', '00:00:0', '00;00;00', '00:01.0:00', '5003999585968:13:02', '9'.repeat(1000)]) assert.equal(parseTimecode(text, 30), null, text)
  for (const frame of [-1, 1.5, '1', Number.MAX_SAFE_INTEGER + 1, NaN]) assert.equal(formatTimecode(frame, 30), null)
})

test('video seconds follow rational clocks and floor only after correcting floating-point boundary noise', () => {
  for (const rate of [24, 25, 30, 48, 50, 60, 100, 120, '24000/1001', '30000/1001', '48000/1001', '60000/1001', '120000/1001']) {
    const { num, den } = projectClock(rate)
    for (const frame of [0, 1, 2, 31, 107999, 10000001, 100000000]) {
      const seconds = frameToSeconds(frame, rate)
      assert.equal(frameFromSeconds(seconds, rate), frame, `${rate}, ${frame}`)
      assert.equal(frameFromSeconds(seconds + 0.49 * den / num, rate), frame, 'not nearest rounding')
      if (frame > 0) assert.equal(frameFromSeconds(seconds - 0.001 * den / num, rate), frame - 1, 'real sub-frame boundary is not rounded forward')
    }
  }
  assert.equal(frameToSeconds(30000, '30000/1001'), 1001)
  for (const value of [-1, NaN, Infinity, '1', Number.MAX_VALUE]) assert.equal(frameFromSeconds(value, 30), null)
  for (const value of [-1, 1.5, '1', Number.MAX_SAFE_INTEGER + 1]) assert.equal(frameToSeconds(value, 30), null)
})

test('seconds plus zero-based offset converts exactly and rejects ambiguous/unsafe tokens', () => {
  assert.equal(secondsFrame('10', '5'), 305)
  assert.equal(secondsFrame('10', '5', parseJSON('{"fps":25.0}').fps), 255)
  assert.throws(() => secondsFrame('10', '5', parseJSON('{"fps":30.0000000000000000000001}').fps))
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
  await act(async () => root.render(React.createElement(Transport, { frame: 0, frameText: '0', duration: 600, fps: parseJSON('{"fps":3e1}').fps, onFrame: value => pins.push(value), onText: () => assert.fail('helper must not change canonical text until explicit conversion'), onQuery: () => assert.fail('no query') })))
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

test('timecode input shares canonical frame text, retains invalid corrections and never queries implicitly', async t => {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://owner/' })
  globalThis.window = dom.window; globalThis.document = dom.window.document; globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const { createRoot } = await import('react-dom/client')
  const { Simulate } = await import('react-dom/test-utils')
  const root = createRoot(document.getElementById('root')), pins = []
  t.after(async () => { await act(async () => root.unmount()); dom.window.close(); delete globalThis.window; delete globalThis.document })
  let setText
  function Controlled() {
    const [text, update] = React.useState('31'); setText = update
    let frame, frameError
    try { frame = frameQuery(text).frame } catch (error) { frameError = error.message }
    return React.createElement(Transport, { frame, frameText: text, frameError, duration: 6000, fps: '30000/1001', onText: value => { pins.push(value); update(value) }, onFrame: update, onQuery: () => assert.fail('input/navigation never queries') })
  }
  await act(async () => root.render(React.createElement(Controlled)))
  const timecode = document.querySelector('[name="framivra-timecode"]'), canonical = document.querySelector('[name="framivra-frame"]')
  const change = value => act(async () => Simulate.change(timecode, { target: { value } }))
  assert.equal(timecode.value, '00:01:01')
  assert.equal(canonical.value, '31')
  assert.equal(canonical.closest('details').open, false, 'integer input remains an advanced tool')
  assert(document.body.textContent.includes('NTSC non-drop'))
  await change('00:02:01')
  assert.equal(canonical.value, '61')
  assert.equal(timecode.value, '00:02:01')
  await change('00:02:30')
  assert.equal(canonical.value, 'timecode:00:02:30')
  assert.throws(() => frameQuery(canonical.value), /BAD_QUERY/, 'invalid timecode cannot silently refer to the old frame')
  assert.equal(timecode.value, '00:02:30')
  assert.equal(timecode.getAttribute('aria-invalid'), 'true')
  await act(async () => root.render(React.createElement(Controlled)))
  assert.equal(timecode.value, '00:02:30', 'unrelated rerender does not roll back an invalid edit')
  await change('00:02:29'); assert.equal(canonical.value, '89')
  await act(async () => setText('1800')); assert.equal(timecode.value, '01:00:00', 'external canonical pin updates timecode')
  await act(async () => setText('1.0')); assert.equal(timecode.value, '', 'invalid integer token is never coerced to a visible valid frame')
  await change(''); assert.equal(canonical.value, ''); assert.deepEqual(frameQuery(canonical.value), {})
  assert.deepEqual(pins, ['61', 'timecode:00:02:30', '89', ''])
  await act(async () => root.render(React.createElement(Transport, { frame: 30, frameText: '30', duration: 600, onText: () => assert.fail('unknown clock must not convert'), onFrame: () => {}, onQuery: () => {} })))
  assert(document.querySelector('[name="framivra-timecode"]').disabled, 'unknown FPS cannot invent a displayed timecode')
  assert(document.body.textContent.includes('时长不可显示'))
  assert.equal(document.querySelector('[name="framivra-seconds"]'), null, 'unknown FPS must not silently enable the 30-fps seconds helper')
  assert.equal(document.querySelector('[name="framivra-frame"]').value, '30', 'exact integer pin remains available')
})
