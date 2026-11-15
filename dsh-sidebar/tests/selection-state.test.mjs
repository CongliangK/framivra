import test from 'node:test'
import assert from 'node:assert/strict'
import { emptySelection, transitionSelection, preserveSelection } from '../src/client/selection.mjs'
const b = address => ({ kind: 'block', address })
const result = { tracks: [{ address: '/track', data: { id: 'track' } }], blocks: ['a','b','c'].map(id => ({ address: `/${id}`, data: { id } })) }
test('single/toggle/range transitions keep primary in current group; details replace even a multi-group', () => {
  let state = transitionSelection(emptySelection(), b('/a'), {}, result)
  state = transitionSelection(state, b('/c'), { shiftKey: true }, result)
  assert.deepEqual(state.items, [b('/a'), b('/b'), b('/c')]); assert.deepEqual(state.primary, b('/c'))
  state = transitionSelection(state, b('/c'), { ctrlKey: true }, result)
  assert.deepEqual(state.primary, b('/b'))
  state = transitionSelection(state, b('/a'), { metaKey: true }, result)
  state = transitionSelection(state, b('/b'), { ctrlKey: true }, result)
  assert.equal(state.primary, undefined); assert.deepEqual(state.items, [])
  state = transitionSelection(state, b('/a'), {}, result)
  state = transitionSelection(state, { kind: 'track', address: '/track' }, {}, result)
  assert.deepEqual(state.items, [{ kind: 'track', address: '/track' }]); assert.deepEqual(state.primary, state.items[0])
})
test('stable-ID authoritative refresh moves group/primary/anchor together and drops removed or legacy objects', () => {
  const state = transitionSelection(transitionSelection(emptySelection(), b('/a'), {}, result), b('/b'), { ctrlKey: true }, result)
  const next = { ...result, blocks: [{ address: '/new-a', data: { id: 'a' } }, { address: '/new-b', data: { id: 'b' } }] }
  const saved = preserveSelection(state, result, next)
  assert.deepEqual(saved.items, [b('/new-a'), b('/new-b')]); assert.deepEqual(saved.primary, b('/new-b')); assert.deepEqual(saved.anchor, b('/new-b'))
  const deleted = preserveSelection(saved, next, { ...next, blocks: next.blocks.slice(0, 1) })
  assert.deepEqual(deleted.primary, b('/new-a')); assert.deepEqual(deleted.items, [b('/new-a')])
  assert.deepEqual(preserveSelection(saved, next, { tracks: [], blocks: [] }), emptySelection())
})
