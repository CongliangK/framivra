import test from 'node:test'
import assert from 'node:assert/strict'
import { internalGaps, snapDelta } from '../src/client/direct-edit.mjs'

const block = range => ({ range })
test('internal gaps use the union of a track, not isolated visual subrows', () => {
  assert.deepEqual(internalGaps([[40, 55], [10, 22], [5, 25], [25, 29], [75, 80]].map(block)), [[29, 40], [55, 75]])
})

test('snapping is zoom-relative, uses exact frame edges and respects bounds', () => {
  const base = { delta: 21, range: [20, 40], mode: 'move', neighbors: [block([62, 80])], playhead: undefined, extent: 100, span: 100, width: 600, accepts: delta => delta >= 0 && delta <= 60 }
  assert.deepEqual(snapDelta(base), { delta: 22, frame: 62, distance: 1 })
  assert.deepEqual(snapDelta({ ...base, width: 1200 }), { delta: 21 })
  assert.deepEqual(snapDelta({ ...base, accepts: delta => delta < 22 }), { delta: 21 })
  assert.deepEqual(snapDelta({ ...base, mode: 'right', delta: -9, playhead: 30, accepts: delta => delta <= 0 }), { delta: -10, frame: 30, distance: 1 })
})
