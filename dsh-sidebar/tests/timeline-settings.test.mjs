import test from 'node:test'
import assert from 'node:assert/strict'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { settingsIntent, savedSetting } from '../src/client/timeline-settings.mjs'
import { ManualEditor } from '../src/client/manual.mjs'
import { stringifyJSON } from '../src/json.mjs'
const h = React.createElement

test('closed settings compiler: exact frames, explicit unset, typed targets, no root edits', () => {
  assert.deepEqual(settingsIntent('duration_frames', { text: '900' }), { op: 'project.resize', duration_frames: 900 })
  for (const text of ['', '0', '-0', '01', '1.0', '1e2', ' 30', '9007199254740992']) assert.throws(() => settingsIntent('duration_frames', { text }))
  assert.deepEqual(settingsIntent('gfx_dependency_policy', { text: 'strict' }), { op: 'parameters.update', target: 'project', path: ['gfx_dependency_policy'], value: 'strict' })
  assert.throws(() => settingsIntent('gfx_dependency_policy', { text: '' }))
  assert.deepEqual(settingsIntent('cache_dependencies', { text: '["a.html","b.ttf"]' }).value, ['a.html', 'b.ttf'])
  assert.throws(() => settingsIntent('cache_dependencies', { text: '[null]' }))
  for (const kind of ['denoise', 'bgm']) {
    for (const [text, value] of [['false', false], ['null', null]]) assert.deepEqual(settingsIntent(kind, { id: 's', text }), { op: 'parameters.update', id: 's', path: [kind], value })
    assert.deepEqual(settingsIntent(kind, { id: 's', remove: true }), { op: 'parameters.unset', id: 's', path: [kind] })
    assert.throws(() => settingsIntent(kind, { text: 'true' }))
  }
  assert.deepEqual(settingsIntent('duck_strategy', { id: 'a', text: '{"voice_track":"voice"}' }), { op: 'track.references', id: 'a', set: { duck_strategy: { voice_track: 'voice' } } })
  assert.deepEqual(settingsIntent('duck_strategy', { id: 'a', remove: true }), { op: 'track.references', id: 'a', set: {}, unset: ['duck_strategy'] })
  for (const kind of ['blender_bridge', 'fps', 'width', '__proto__']) assert.throws(() => settingsIntent(kind, { text: 'true' }))
  assert.throws(() => settingsIntent('denoise', { id: 's', text: ' '.repeat(131073) }))
  assert.equal(savedSetting({}, 'x'), '未设置'); assert.equal(savedSetting({ x: false }, 'x'), 'false'); assert.equal(savedSetting({ x: null }, 'x'), 'null')
})

test('plugin whole JSON preserves unknown nested numeric tokens without compatibility assertions', () => {
  const value = settingsIntent('bgm', { id: 's', text: '{"future":{"n":9007199254740993,"decimal":0.1234567890123456789,"off":false,"nil":null}}' })
  assert.equal(stringifyJSON(value.value), '{"future":{"n":9007199254740993,"decimal":0.1234567890123456789,"off":false,"nil":null}}')
})

test('compiled settings run through real pure kernel transform; invalid shrink is rejected without writes', () => {
  const operations = [settingsIntent('duration_frames', { text: '1000' }), settingsIntent('gfx_dependency_policy', { text: 'strict' }), settingsIntent('cache_dependencies', { text: '["font.ttf"]' }), settingsIntent('denoise', { id: 's', text: '{"future":{"off":false,"nil":null,"big":9007199254740993}}' }), settingsIntent('bgm', { id: 's', text: 'false' }), settingsIntent('duck_strategy', { id: 'a', text: '{"voice_track":"voice","unknown":null}' })]
  const script = `import json,sys\nfrom vtx.timeline_edit import transform_timeline, EditError\nops=json.load(sys.stdin)\ndoc={"version":2,"project":{"fps":30,"width":1920,"height":1080,"duration_frames":900},"sources":[{"id":"s","path":"missing.wav","untouched":{"x":False}}],"video_tracks":[],"audio_tracks":[{"id":"a","name":"bed","events":[{"id":"e","src":"s","in":0,"out":90,"volume":1}]},{"id":"v","name":"voice","events":[{"id":"ve","src":"s","in":0,"out":90,"volume":1}]}],"subtitles":[],"_vtx":{"protocol":1,"project_id":"p","head":"h"},"blender_bridge":None,"unknown_root":{"a":[False,None]}}\nfinal,summary=transform_timeline(doc,ops)\nassert doc["project"]["duration_frames"]==900\nassert final["project"]["duration_frames"]==1000\nassert final["project"]["gfx_dependency_policy"]=="strict"\nassert final["project"]["cache_dependencies"]==["font.ttf"]\nassert final["sources"][0]["denoise"]["future"]["big"]==9007199254740993\nassert final["sources"][0]["bgm"] is False\nassert final["sources"][0]["untouched"]==doc["sources"][0]["untouched"]\nassert final["audio_tracks"][0]["duck_strategy"]=={"voice_track":"voice","unknown":None}\nassert final["blender_bridge"] is None and final["unknown_root"]==doc["unknown_root"]\ntry:\n transform_timeline(final,[{"op":"project.resize","duration_frames":1}])\nexcept EditError:\n pass\nelse:\n raise AssertionError("invalid shrink accepted")\nprint("pure kernel settings transform passed")`
  const outcome = spawnSync('python3', ['-B', '-c', script], { cwd: fileURLToPath(new URL('../..', import.meta.url)), input: stringifyJSON(operations), encoding: 'utf8', env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' } })
  assert.equal(outcome.status, 0, outcome.stderr || outcome.error?.message)
  assert.match(outcome.stdout, /pure kernel settings transform passed/)
})

async function mount(t) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://owner/' })
  globalThis.window = dom.window; globalThis.document = dom.window.document; globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const { createRoot } = await import('react-dom/client'), { Simulate } = await import('react-dom/test-utils')
  const root = createRoot(document.getElementById('root'))
  t.after(async () => { await act(async () => root.unmount()); dom.window.close(); delete globalThis.window; delete globalThis.document })
  return { root, Simulate }
}
const button = text => [...document.querySelectorAll('button')].find(node => node.textContent === text)
const input = label => document.querySelector(`input[aria-label="${label}"], select[aria-label="${label}"], textarea[aria-label="${label}"]`) ?? document.querySelector(`[aria-label="${label}"]`)
const snapshot = { content_version: 'v1', document: { project: { fps: 30, width: 1920, height: 1080, duration_frames: 900 }, _vtx: { protocol: 1, head: 'head' }, blender_bridge: false, sources: [{ id: 's', path: 'missing.wav', denoise: false, bgm: null }] } }
const result = { content_version: 'v1', blocks: [], tracks: [{ family: 'audio', data: { id: 'a', name: 'bed', duck_strategy: { voice_track: 'voice' } } }] }

test('ManualEditor settings: no default enqueue, read-only summary, dirty/invalidation and frozen plans', async t => {
  const { root, Simulate } = await mount(t), calls = [], states = []
  const call = async (_session, payload) => {
    calls.push(payload)
    if (payload.op === 'proposal-list') return { ok: true, proposals: [] }
    if (payload.op === 'authorize-write') return { writeGrant: 'explicit' }
    if (payload.op === 'plan') return { ok: true, planToken: 'frozen', submitted_request: { op_id: 'frozen-op', operations: payload.operations } }
    return { ok: true }
  }
  await act(async () => root.render(h(ManualEditor, { sessionId: 'one', opened: { grant: 'read', snapshot }, result, tabSignal: new AbortController().signal, call, onCommitted: () => {}, onEditState: state => states.push(state) })))
  assert.match(input('已保存规格').textContent, /FPS 30.*1920.*1080.*900/)
  assert.match(input('时间线配置').textContent, /根 blender_bridge（只读）：false/)
  assert.match(input('时间线配置').textContent, /denoise：false · bgm：null/)
  assert.equal(input('工程时长').closest('fieldset').disabled, true)
  assert.equal(input('配置素材').disabled, true)
  assert.equal(input('未保存 typed intents').textContent, '[]')
  assert(!states.some(state => state.dirty)); assert(!calls.some(call => ['plan', 'commit', 'frame'].includes(call.op)))
  await act(async () => button('明确允许本会话向此工程及 core 历史写入').click())
  assert.equal(input('工程时长').value, '900'); assert.equal(input('图形依赖策略').value, '')
  assert.equal(input('未保存 typed intents').textContent, '[]', 'authorization and initialization do not enqueue saved/default values')
  await act(async () => Simulate.change(input('工程时长'), { target: { value: '1000' } }))
  assert(states.at(-1).dirty); assert(calls.some(call => call.op === 'invalidate-plan'))
  await act(async () => button('加入批次：工程时长').click())
  await act(async () => button('内核预检计划（零写）').click())
  assert.deepEqual(calls.find(call => call.op === 'plan').operations, [{ op: 'project.resize', duration_frames: 1000 }])
  assert(button('确认提交此冻结计划（一次 revision）'))
  await act(async () => Simulate.change(input('图形依赖策略'), { target: { value: 'strict' } }))
  assert.equal(button('确认提交此冻结计划（一次 revision）'), undefined)
  await act(async () => Simulate.change(input('配置素材'), { target: { value: 's' } }))
  assert.equal(input('素材降噪').value, 'false'); assert.equal(input('音乐源').value, 'null')
  await act(async () => button('加入批次：音乐源').click())
  await act(async () => button('移除字段：素材降噪').click())
  await act(async () => Simulate.change(input('配置音轨'), { target: { value: 'a' } }))
  await act(async () => button('加入批次：静态闪避').click())
  const queued = JSON.parse(input('未保存 typed intents').textContent)
  assert.deepEqual(queued.slice(1), [{ op: 'parameters.update', id: 's', path: ['bgm'], value: null }, { op: 'parameters.unset', id: 's', path: ['denoise'] }, { op: 'track.references', id: 'a', set: { duck_strategy: { voice_track: 'voice' } } }])
  assert(!calls.some(call => ['commit', 'frame', 'adopt'].includes(call.op)))
  await act(async () => Simulate.change(input('音乐源'), { target: { value: '{"unsaved":true}' } }))
  await act(async () => button('添加依赖路径').click())
  await act(async () => Simulate.change(input('依赖路径 1'), { target: { value: 'unsaved.ttf' } }))
  await act(async () => button('清空未保存批次').click())
  assert.equal(input('未保存 typed intents').textContent, '[]')
  assert.equal(input('工程时长').value, '900', 'clear restores saved duration instead of leaving unqueued text')
  assert.equal(input('图形依赖策略').value, '', 'clear restores missing policy without defaults')
  assert.equal(input('依赖路径 1'), null, 'clear discards unqueued dependency paths')
  assert.equal(input('配置素材').value, '', 'clear resets plugin draft target')
  assert.equal(states.at(-1).dirty, false)
  await act(async () => Simulate.change(input('配置素材'), { target: { value: 's' } }))
  assert.equal(input('音乐源').value, 'null', 'clear discards unqueued plugin JSON')
  await act(async () => button('撤销本会话/工程写许可').click())
  assert.equal(input('音乐源').closest('fieldset').disabled, true)
})

test('switching source/audio targets preserves separate unqueued drafts until explicit clear', async t => {
  const { root, Simulate } = await mount(t)
  const call = async (_session, payload) => payload.op === 'proposal-list' ? { ok: true, proposals: [] } : payload.op === 'authorize-write' ? { writeGrant: 'explicit' } : { ok: true }
  const opened = { grant: 'read', snapshot: { ...snapshot, document: { ...snapshot.document, sources: [...snapshot.document.sources, { id: 's2', path: 'other.wav', denoise: null, bgm: false }] } } }
  const twoTracks = { ...result, tracks: [...result.tracks, { family: 'audio', data: { id: 'a2', name: 'other', duck_strategy: null } }] }
  await act(async () => root.render(h(ManualEditor, { sessionId: 'one', opened, result: twoTracks, tabSignal: new AbortController().signal, call, onCommitted: () => {} })))
  await act(async () => button('明确允许本会话向此工程及 core 历史写入').click())
  const change = async (label, value) => await act(async () => Simulate.change(input(label), { target: { value } }))
  await change('配置素材', 's')
  await change('素材降噪', '{"custom":123}')
  await change('音乐源', '{"first":false}')
  await change('配置音轨', 'a')
  await change('静态闪避', '{"voice_track":"draft-voice"}')
  await change('配置素材', 's2')
  assert.equal(input('素材降噪').value, 'null')
  await change('素材降噪', '{"second":null}')
  await change('配置音轨', 'a2')
  await change('静态闪避', 'false')
  await change('配置素材', 's')
  assert.equal(input('素材降噪').value, '{"custom":123}')
  assert.equal(input('音乐源').value, '{"first":false}')
  await change('配置音轨', 'a')
  assert.equal(input('静态闪避').value, '{"voice_track":"draft-voice"}')
  await change('配置素材', 's2')
  assert.equal(input('素材降噪').value, '{"second":null}')
  await change('配置音轨', 'a2')
  assert.equal(input('静态闪避').value, 'false')
  assert.equal(input('未保存 typed intents').textContent, '[]', 'switching never auto-enqueues drafts')
  await act(async () => button('清空未保存批次').click())
  await change('配置素材', 's')
  assert.equal(input('素材降噪').value, 'false')
  assert.equal(input('音乐源').value, 'null')
  await change('配置音轨', 'a')
  assert.equal(input('静态闪避').value, '{"voice_track":"voice"}')
})

test('legacy settings stay disabled after authorization, retaining saved summary without adoption', async t => {
  const { root } = await mount(t), calls = []
  const legacy = { ...snapshot, document: { ...snapshot.document, _vtx: undefined } }
  const call = async (_session, payload) => { calls.push(payload); return payload.op === 'proposal-list' ? { ok: true, proposals: [] } : { writeGrant: 'explicit' } }
  await act(async () => root.render(h(ManualEditor, { sessionId: 'one', opened: { grant: 'read', snapshot: legacy }, result, tabSignal: new AbortController().signal, call, onCommitted: () => {} })))
  await act(async () => button('明确允许本会话向此工程及 core 历史写入').click())
  assert.equal(input('工程时长').closest('fieldset').disabled, true)
  assert.equal(input('配置素材').disabled, true)
  assert.match(input('已保存规格').textContent, /FPS 30/)
  assert(!calls.some(call => ['adopt', 'plan', 'commit'].includes(call.op)))
})

test('confirmed write with failed refresh locks settings; no enqueue can bypass stale lock', async t => {
  const { root, Simulate } = await mount(t), calls = []
  const call = async (_session, payload) => { calls.push(payload); return payload.op === 'proposal-list' ? { ok: true, proposals: [] } : payload.op === 'authorize-write' ? { writeGrant: 'explicit' } : payload.op === 'plan' ? { ok: true, planToken: 'frozen', submitted_request: { op_id: 'op' } } : { ok: true } }
  await act(async () => root.render(h(ManualEditor, { sessionId: 'one', opened: { grant: 'read', snapshot }, result, tabSignal: new AbortController().signal, call, onCommitted: async () => { throw Error('refresh failed') } })))
  await act(async () => button('明确允许本会话向此工程及 core 历史写入').click())
  await act(async () => Simulate.change(input('工程时长'), { target: { value: '1000' } }))
  await act(async () => button('加入批次：工程时长').click())
  await act(async () => button('内核预检计划（零写）').click())
  await act(async () => button('确认提交此冻结计划（一次 revision）').click())
  assert.equal(input('工程时长').closest('fieldset').disabled, true)
  assert.match(document.body.textContent, /内核已确认本次写入/)
  await act(async () => button('加入批次：工程时长').click())
  assert.equal(input('未保存 typed intents').textContent, '[]')
  assert.equal(calls.filter(call => call.op === 'commit').length, 1)
})
