import test from 'node:test'
import assert from 'node:assert/strict'
import { selectionEnvironment, skip } from './selection-env.mjs'
const address = index => `/video_tracks/0/clips/${index}`
const clean = text => { const doc = JSON.parse(text); delete doc._vtx; return doc }
const ids = ui => ui.queue().map(op => op.id)
for (const kind of ['source', 'zip']) {
  const options = { skip: skip || (kind === 'zip' && !process.env.SIDEBAR_MANUAL_ZIP && 'Set SIDEBAR_MANUAL_ZIP'), timeout: 120000 }
  test(`${kind}: empty-queue visual A -> details B and reverse delete only reference target; exact business undo`, options, async t => {
    const ui = await selectionEnvironment(t, kind), before = await ui.read()
    await ui.save('before-timeline.json', before)
    for (const [visual, detail, target, untouched] of [[0, 1, 'c2', 'c'], [1, 0, 'c', 'c2']]) {
      assert.deepEqual(ui.queue(), [])
      await ui.click(ui.clip(visual)); await ui.click(`选择块 ${address(detail)}`)
      assert.deepEqual(ui.queue(), [])
      await ui.settle(() => document.querySelector('[aria-label="选中对象 raw JSON"]'))
      const raw = document.querySelector('[aria-label="选中对象 raw JSON"]').textContent
      assert.equal(JSON.parse(raw).id, target)
      await ui.save(`reference-${target}-before-edit.json`, { raw, queue: ui.queue() })
      await ui.change('编辑操作', 'block.remove'); await ui.click('加入未保存批次')
      // Save and commit even on OLD code: disk is an oracle, not just a renderer assertion.
      const queued = ui.queue(); await ui.planCommit()
      const after = await ui.read(), doc = JSON.parse(after), clips = doc.video_tracks[0].clips
      await ui.save(`after-${target}.json`, { queued, doc, removed: target, untouched })
      assert.deepEqual(queued, [{ op: 'block.remove', id: target }])
      assert(!clips.some(c => c.id === target)); assert.deepEqual(clips.find(c => c.id === untouched), clean(before).video_tracks[0].clips.find(c => c.id === untouched))
      await ui.undo(); assert.deepEqual(clean(await ui.read()), clean(before))
    }
  })

  test(`${kind}: detail track clears block group; selected-derived properties/relink/source cascade/move use current target; queued A stays frozen`, options, async t => {
    const ui = await selectionEnvironment(t, kind), before = await ui.read(), controls = []
    await ui.click(ui.clip(0)); await ui.click(ui.clip(1), { ctrlKey: true })
    await ui.click('选择轨道 /video_tracks/1')
    assert(document.body.textContent.includes('选择 0 个块 / g'))
    assert.equal(document.querySelectorAll('.fv-clip[aria-pressed="true"]').length, 0)
    await ui.change('编辑操作', 'track.rename'); await ui.change('轨道新名称', 'renamed'); await ui.click('加入未保存批次')
    assert.deepEqual(ui.queue(), [{ op: 'track.rename', id: 'g', name: 'renamed' }])
    await ui.planCommit(); assert.equal(JSON.parse(await ui.read()).video_tracks[1].name, 'renamed')
    await ui.undo(); assert.deepEqual(clean(await ui.read()), clean(before))
    await ui.click(ui.clip(0)); await ui.change('编辑操作', 'block.remove'); await ui.click('加入未保存批次')
    assert.deepEqual(ids(ui), ['c'])
    await ui.click(`选择块 ${address(1)}`)
    assert.deepEqual(ids(ui), ['c'], 'selection must not retarget an already queued intent')
    await ui.planCommit()
    const queuedDisk = JSON.parse(await ui.read())
    assert(!queuedDisk.video_tracks[0].clips.some(c => c.id === 'c')); assert(queuedDisk.video_tracks[0].clips.some(c => c.id === 'c2'))
    controls.push({ queuedAThenSelectionB: queuedDisk })
    await ui.undo(); assert.deepEqual(clean(await ui.read()), clean(before))
    await ui.click(ui.clip(0)); await ui.change('编辑操作', 'block.remove'); await ui.click('加入未保存批次')
    await ui.click(`选择块 ${address(1)}`); await ui.click('加入未保存批次'); assert.deepEqual(ids(ui), ['c', 'c2'])
    await ui.click('清空未保存批次')
    for (const [op, values, expected] of [
      ['block.relink', [['新 source ID / gfx 文件路径', 's']], { op: 'block.relink', id: 'c2', src: 's' }],
      ['block.move', [['首选块新起点帧（多选保持相对位置）', '65']], { op: 'block.move', id: 'c2', track_id: 'v', at: 65 }],
      ['parameters.update', [['属性值（text 为文本，其余单个 JSON 值）', '0.5']], { op: 'parameters.update', id: 'c2', path: ['opacity'], value: 0.5 }],
    ]) {
      await ui.change('编辑操作', op); for (const [label, value] of values) await ui.change(label, value)
      await ui.click('加入未保存批次'); assert.deepEqual(ui.queue(), [expected]); await ui.planCommit()
      const doc = JSON.parse(await ui.read()), clips = doc.video_tracks[0].clips
      assert.deepEqual(clips.find(c => c.id === 'c'), clean(before).video_tracks[0].clips.find(c => c.id === 'c'))
      const current = clips.find(c => c.id === 'c2')
      if (op === 'block.move') assert.equal(current.dst_in, 65)
      if (op === 'parameters.update') assert.equal(current.opacity, 0.5)
      if (op === 'block.relink') assert.equal(current.src, 's')
      controls.push({ op, expected, doc }); await ui.undo(); assert.deepEqual(clean(await ui.read()), clean(before))
    }
    await ui.change('编辑操作', 'source.remove'); await ui.change('素材 ID', 's')
    const cascade = [...document.querySelectorAll('label')].find(n => n.textContent.includes('确认 cascade')).querySelector('input')
    await ui.click(cascade); await ui.click('加入未保存批次'); assert.deepEqual(ui.queue(), [{ op: 'source.remove', id: 's', cascade: ['c2'] }])
    const diskBeforeRejectedCascade = await ui.read()
    await ui.click('内核预检计划（零写）'); assert.equal(ui.button('确认提交此冻结计划（一次 revision）'), undefined)
    assert.equal(await ui.read(), diskBeforeRejectedCascade, 'kernel must reject incomplete explicit source user set without writing')
    controls.push({ cascade: ui.queue(), plan: ui.wire.filter(x => x.request.op === 'plan').at(-1) }); await ui.click('清空未保存批次')
    // Ripple's selection-derived split checkboxes must not retain hidden old A IDs.
    await ui.click(ui.clip(0)); await ui.change('编辑操作', 'range.ripple'); await ui.change('ripple 模式', 'delete'); await ui.change('跨界策略', 'cut')
    const split = document.querySelectorAll('[aria-label="手动语义编辑"] fieldset fieldset')[1].querySelector('input')
    await ui.click(split); await ui.click(`选择块 ${address(1)}`); await ui.click('加入未保存批次')
    assert.deepEqual(ui.queue()[0].split_ids, {}); await ui.click('清空未保存批次')
    await ui.change('编辑操作', 'parameters.update'); await ui.change('属性目标', 'track'); await ui.click('选择轨道 /video_tracks/1'); await ui.click('加入未保存批次')
    assert.equal(ui.queue()[0].id, 'g'); await ui.save('target-controls.json', { queue: ui.queue(), controls, noImplicitSourceRetarget: true })
  })

  test(`${kind}: Ctrl/Shift, keyboard focus and authoritative refresh retain one selection; stale reference/frame/ghost cannot retarget`, options, async t => {
    const ui = await selectionEnvironment(t, kind), before = await ui.read()
    await ui.click(ui.clip(0)); await ui.click(ui.clip(1), { ctrlKey: true })
    assert(document.body.textContent.includes('选择 2 个块'))
    await ui.change('编辑操作', 'project.resize'); await ui.change('工程新时长帧（不改 FPS/分辨率）', '130'); await ui.click('加入未保存批次'); await ui.planCommit()
    assert.equal(document.querySelectorAll('.fv-clip[aria-pressed="true"]').length, 2, 'visual must retain same stable-ID group as manual after refresh')
    document.querySelector('.fv-ruler').getBoundingClientRect = () => ({ left: 0, width: 1300 })
    document.querySelectorAll('.fv-track').forEach((n, i) => { n.getBoundingClientRect = () => ({ top: i * 100, bottom: (i + 1) * 100 }) })
    await ui.event(ui.clip(0), 'pointerdown', { button: 0, clientX: 0, clientY: 10 }); await ui.event(ui.clip(0), 'pointermove', { clientX: 10, clientY: 10 })
    assert(document.querySelector('[aria-label="未保存拖动 ghost"]'))
    await ui.click(`选择块 ${address(1)}`)
    assert.equal(document.querySelector('[aria-label="未保存拖动 ghost"]'), null)
    await ui.event(ui.clip(0), 'pointerup', { clientX: 10, clientY: 10 }); assert.deepEqual(ui.queue(), [])
    await ui.click(ui.clip(0)); await ui.click(ui.clip(1), { shiftKey: true }); assert(document.body.textContent.includes('选择 2 个块'))
    await ui.click(ui.clip(1), { ctrlKey: true }); assert(document.body.textContent.includes('选择 1 个块'))
    await ui.change('编辑操作', 'block.remove'); await ui.click('加入未保存批次'); assert.deepEqual(ids(ui), ['c']); await ui.click('清空未保存批次')
    ui.clip(1).focus(); await ui.event(ui.clip(1), 'keydown', { key: 'Enter' }); await ui.event(ui.clip(1), 'keyup', { key: 'Enter' }); await ui.click(document.activeElement, { detail: 0 }) // JSDOM lacks the browser's default button activation; native click is its explicit adapter.
    await ui.click('加入未保存批次'); assert.deepEqual(ids(ui), ['c2']); await ui.click('清空未保存批次')
    // Hold A's real reference answer across a B transition; never poke React state.
    ui.faults.hold = request => request.op === 'reference' && request.query.block_addresses?.[0] === address(0)
    await ui.click(ui.clip(0)); await ui.settle(() => ui.held.length === 1)
    await ui.click(`选择块 ${address(1)}`); await ui.settle(() => JSON.parse(document.querySelector('[aria-label="选中对象 raw JSON"]')?.textContent || '{}').id === 'c2')
    ui.faults.hold = undefined; ui.held.splice(0).forEach(resolve => resolve()); await ui.flush()
    assert.equal(JSON.parse(document.querySelector('[aria-label="选中对象 raw JSON"]').textContent).id, 'c2')
    await ui.change('Frame (blank = all) ', '70'); assert.equal(document.querySelector('[aria-label="选中对象 raw JSON"]'), null)
    await ui.click('复制 raw JSON'); assert.equal(JSON.parse(ui.copies.at(-1)).id, 'c2')
    await ui.save('retained-selection.json', { queue: ui.queue(), copies: ui.copies, before: clean(before), saved: clean(await ui.read()), keyboard: 'native key events + explicit native default-click adapter; not Chrome' })
  })
}
