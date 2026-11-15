import test from 'node:test'
import assert from 'node:assert/strict'
import { displayExtent, windowFor, zoomWindow, panWindow, framePercent, clipGeometry, pointerFrame, frameTicks, displayLanes } from '../src/client/viewport.mjs'

const max = Number.MAX_SAFE_INTEGER

test('viewport remains bounded and exact near MAX_SAFE_INTEGER; never enormous CSS widths', () => {
  const full = { start: 0, end: max }
  const last = windowFor(max, max, 4)
  assert.deepEqual(last, { start: max - 4, end: max })
  assert.deepEqual(frameTicks(last), [max - 4, max - 3, max - 2, max - 1, max])
  assert.equal(framePercent(max - 3, last), 25)
  assert.equal(pointerFrame(101, { left: 1, width: 400 }, last), max - 3)
  assert.equal(pointerFrame(900, { left: 1, width: 400 }, last), max - 1)
  assert.equal(pointerFrame(-10, { left: 1, width: 400 }, last), max - 4)
  assert.equal(pointerFrame(1, { left: 0, width: 0 }, last), undefined)
  assert.equal(pointerFrame(NaN, { left: 0, width: 200 }, last), undefined)
  assert.deepEqual(clipGeometry([max - 4, max - 3], last), { left: 0, width: 25 })
  assert.equal(clipGeometry([0, 1], last), null)
  assert.deepEqual(zoomWindow(full, max, false), full)
  assert.deepEqual(panWindow(last, max, 1), last)
  let view = full
  for (let i = 0; i < 54; i++) view = zoomWindow(view, max, true)
  assert.equal(view.end - view.start, 1)
  assert.deepEqual(frameTicks(view), [view.start, view.end])
  assert.throws(() => windowFor(max + 1, 0, 1), /DISPLAY_PRECISION/)
})

test('geometry refuses unsafe raw duration/ranges and preserves input references', () => {
  const duration = JSON.rawJSON('9007199254740993')
  const snapshot = { document: { project: { duration_frames: duration } } }
  const result = { blocks: [] }
  assert.match(displayExtent(snapshot, result).diagnostic, /DISPLAY_PRECISION/)
  assert.equal(snapshot.document.project.duration_frames, duration)
  assert.match(displayExtent({ document: {} }, result).diagnostic, /DISPLAY_PRECISION/)
  assert.match(displayExtent({ document: { project: { duration_frames: 100 } } }, { blocks: [{ range: [99, 101] }] }).diagnostic, /DISPLAY_PRECISION/)
  assert.deepEqual(displayExtent({ document: { project: { duration_frames: 100 } } }, result), { start: 0, end: 100 })
})

test('overlapping audio receives independently selectable display subrows, without mutating kernel order', () => {
  const blocks = [[0, 50], [10, 20], [20, 40], [50, 60]].map((range, block_index) => ({ range, block_index, address: `/audio/${block_index}` }))
  const original = JSON.stringify(blocks)
  const rows = displayLanes(blocks)
  assert.equal(rows.length, 2)
  assert.deepEqual(rows.map(row => row.map(block => block.address)), [['/audio/0', '/audio/3'], ['/audio/1', '/audio/2']])
  assert.equal(JSON.stringify(blocks), original)
})

test('large projections do not use argument spread or derive semantic clip ranges', () => {
  const blocks = Array.from({ length: 140000 }, (_, block_index) => ({ range: [block_index, block_index + 1], block_index }))
  assert.deepEqual(displayExtent({ document: { project: { duration_frames: 140000 } } }, { blocks }), { start: 0, end: 140000 })
  assert.equal(displayLanes(blocks)[0].length, 140000)
})
