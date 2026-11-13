import test from 'node:test'
import assert from 'node:assert/strict'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { BundleConfigForm } from '../src/client/settings.mjs'
import { KernelConfigEditor } from '../src/client/kernel-settings.mjs'

const h = React.createElement
const text = () => document.body.textContent
const button = label => [...document.querySelectorAll('button')].find(node => node.textContent === label)
const click = async node => { assert(node, 'button exists'); await act(async () => node.click()) }
function type(node, value) {
  const ctor = node.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement : node.tagName === 'SELECT' ? window.HTMLSelectElement : window.HTMLInputElement
  act(() => { Object.getOwnPropertyDescriptor(ctor.prototype, 'value').set.call(node, value); node.dispatchEvent(new window.Event(node.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })) })
}
async function setup(t, responder) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://owner/' })
  globalThis.window = dom.window; globalThis.document = dom.window.document; globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const originalFetch = globalThis.fetch, calls = []
  globalThis.fetch = async (url, init) => {
    const payload = JSON.parse(init.body); calls.push({ url, init, payload })
    const value = await responder?.(payload, calls.length)
    return new Response(JSON.stringify(value ?? { ok: true }), { status: value?.ok === false ? 409 : 200 })
  }
  const { createRoot } = await import('react-dom/client')
  const root = createRoot(document.getElementById('root'))
  t.after(async () => { await act(async () => root.unmount()); globalThis.fetch = originalFetch; dom.window.close(); delete globalThis.window; delete globalThis.document })
  return { root, calls }
}
function store(overrides = {}) {
  const listeners = new Set(), calls = []
  return {
    calls, snapshot: { status: 'ready', value: { vtxExecutable: '/opt/vtx/bin/vtx', trustedPath: '/usr/bin:/bin', vtxConfigFile: '/home/owner/.config/framivra/config.json' }, revision: 7, writable: true, ...overrides },
    getSnapshot() { return this.snapshot }, subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn) },
    async mutate(ops, revision) { calls.push({ ops, revision }); this.snapshot = { ...this.snapshot, value: { ...this.snapshot.value, ...Object.fromEntries(ops.map(op => [op.path[0], op.value])) }, revision: revision + 1 }; listeners.forEach(fn => fn()); return true },
  }
}
const readResult = { ok: true, path: '/home/owner/config.json', text: '{"asr":{"enabled":false},"custom":{"large":9007199254740993}}\n', hash: 'sha256:old', exists: true, selectionToken: 'selection-A' }
const editorProps = { revision: 7, configFile: readResult.path, writable: true, connectionDirty: false }
const renderEditor = async root => act(async () => root.render(h(KernelConfigEditor, editorProps)))

test('settings mount performs no discovery, installation, diagnostics or file read', async t => {
  const { root, calls } = await setup(t)
  await act(async () => root.render(h(BundleConfigForm, { form: store() })))
  assert.equal(calls.length, 0)
  assert.match(text(), /VTX 内核与配置/)
  assert.equal(document.querySelector('#fv-settings-kernel').hidden, false)
  assert.equal(document.querySelector('#fv-settings-config').hidden, true)
})

test('scan never executes or saves a candidate; select only drafts a path', async t => {
  const { root, calls } = await setup(t, () => ({ ok: true, candidates: [{ path: '/home/owner/.local/share/pipx/venvs/framivra/bin/vtx', source: 'common-venv' }], warnings: [], suggestedConfigFile: '/home/owner/config.json' }))
  const form = store()
  await act(async () => root.render(h(BundleConfigForm, { form })))
  await click(button('扫描已安装内核'))
  assert.deepEqual(calls.map(item => item.payload), [{ op: 'scan' }])
  await click(button('填入路径'))
  assert.equal(form.calls.length, 0)
  assert.equal(calls.length, 1)
  assert.equal(document.querySelector('#framivra-config-executable').value, '/home/owner/.local/share/pipx/venvs/framivra/bin/vtx')
  await click(button('保存配置'))
  assert.equal(form.calls.length, 1)
  assert.equal(form.calls[0].revision, 7)
})

test('source discovery is labeled unverified and empty scans explain their scope', async t => {
  let candidates = [{ path: '/home/owner/.agents/skills/video/vtx/vtx.py', source: 'video-skill-source' }]
  const { root, calls } = await setup(t, () => ({ ok: true, candidates, warnings: [] }))
  const form = store()
  await act(async () => root.render(h(BundleConfigForm, { form })))
  await click(button('扫描已安装内核'))
  assert.match(text(), /video skill 源码内核 · 未执行验证/)
  await click(button('填入路径'))
  assert.equal(document.querySelector('#framivra-config-executable').value, candidates[0].path)
  assert.equal(form.calls.length, 0)
  candidates = []
  await click(button('扫描已安装内核'))
  assert.match(text(), /不代表本机没有内核/)
  assert.match(text(), /vtx\/vtx.py 的绝对路径/)
  assert.deepEqual(calls.map(item => item.payload), [{ op: 'scan' }, { op: 'scan' }])
})

test('kernel config path and log budget save through revision-pinned ConfigForm only', async t => {
  const { root, calls } = await setup(t)
  const form = store()
  await act(async () => root.render(h(BundleConfigForm, { form })))
  await click(button('配置文件'))
  type(document.querySelector('#framivra-config-file'), '/home/owner/new.json')
  await click(button('保存配置'))
  assert(form.calls[0].ops.some(op => op.path[0] === 'vtxConfigFile' && op.value === '/home/owner/new.json'))
  assert.equal(calls.length, 0)
  await click(button('内核'))
  type(document.querySelector('#framivra-config-logmax'), '0')
  await click(button('保存配置'))
  assert(form.calls[1].ops.some(op => op.path[0] === 'logMaxPerMinute' && op.value === 0))
})

test('install requires preview and explicit trust, then sends only nonce/hash', async t => {
  const { root, calls } = await setup(t, payload => {
    if (payload.op === 'install-preview') return { ok: true, nonce: 'nonce', hash: 'sha256:wheel', wheelPath: payload.wheelPath, targetDir: '/home/owner/kernels/new', pythonPath: '/usr/bin/python3', expiresAt: Date.now() + 10000 }
    if (payload.op === 'install-confirm') return { ok: true, jobId: 'job-1' }
    return { ok: true, jobs: [{ jobId: 'job-1', state: 'succeeded', vtxPath: '/home/owner/kernels/new/bin/vtx', logs: '' }] }
  })
  const form = store()
  await act(async () => root.render(h(BundleConfigForm, { form })))
  type(document.querySelector('#framivra-wheel'), '/trusted/framivra-2.2.0-py3-none-any.whl')
  await click(button('查看安装计划'))
  assert.equal(button('确认安装').disabled, true)
  assert.equal(calls.length, 1)
  await click(document.querySelector('[aria-label="安装确认"] input[type=checkbox]'))
  await click(button('确认安装'))
  assert.deepEqual(calls.find(item => item.payload.op === 'install-confirm').payload, { op: 'install-confirm', nonce: 'nonce', hash: 'sha256:wheel' })
  assert.equal(form.calls.length, 0)
  await click(button('使用这个内核'))
  assert.equal(form.calls.length, 0)
  assert.equal(document.querySelector('#framivra-config-executable').value, '/home/owner/kernels/new/bin/vtx')
})

test('full JSON preserves unknown large integers and requires reviewed confirmed CAS save', async t => {
  const { root, calls } = await setup(t, payload => payload.op === 'config-read' ? readResult : { ok: true, path: readResult.path, hash: 'sha256:new', selectionToken: 'selection-B', needsReload: false, backupPath: '/home/owner/config.backup.json' })
  await renderEditor(root)
  await click(button('读取配置文件'))
  await click(button('完整 JSON'))
  assert.equal(document.querySelector('textarea').value, readResult.text)
  const draft = readResult.text.replace('false', 'true')
  type(document.querySelector('textarea'), draft)
  await click(button('查看修改并保存'))
  assert.equal(button('确认保存文件').disabled, true)
  assert.equal(calls.filter(item => item.payload.op === 'config-save').length, 0)
  await click(document.querySelector('[aria-label="配置保存确认"] input[type=checkbox]'))
  await click(button('确认保存文件'))
  assert.deepEqual(calls.find(item => item.payload.op === 'config-save').payload, { op: 'config-save', text: draft, expectedHash: readResult.hash, selectionToken: readResult.selectionToken, confirmed: true })
  assert.match(text(), /配置文件已保存/)
  type(document.querySelector('textarea'), '{"custom":2}')
  await click(button('查看修改并保存'))
  await click(document.querySelector('[aria-label="配置保存确认"] input[type=checkbox]'))
  await click(button('确认保存文件'))
  const saves = calls.filter(item => item.payload.op === 'config-save')
  assert.equal(saves.length, 2)
  assert.equal(saves[1].payload.selectionToken, 'selection-B')
  assert.equal(saves[1].payload.expectedHash, 'sha256:new')
})

test('cache defaults to the recommended core selection without dirtying or writing the document', async t => {
  const { root, calls } = await setup(t, () => readResult)
  await renderEditor(root); await click(button('读取配置文件'))
  const select = document.querySelector('#fv-kernel-cache-mode')
  assert.equal(select.tagName, 'SELECT')
  assert.equal(select.value, 'core')
  assert.deepEqual([...select.options].map(item => item.value), ['off', 'core', 'aggressive'])
  assert.equal(document.querySelector(`label[for="${select.id}"]`).textContent, '缓存档位')
  assert.match(document.getElementById(select.getAttribute('aria-describedby')).textContent, /off.*core.*aggressive.*VTX_CONFIG/)
  assert.match(text(), /尚未显式设置.*不会自动写入配置/)
  assert.equal(button('查看修改并保存').disabled, true)
  await click(button('完整 JSON'))
  assert.equal(document.querySelector('textarea').value, readResult.text)
  assert.deepEqual(calls.map(item => item.payload), [{ op: 'config-read' }])
})

test('cache three-way selection saves globally only through reviewed, confirmed Host CAS', async t => {
  const original = '{"cache":{"future":9007199254740993},"bgm":{"cli":"bgm --keep","enabled":false},"future":1e+100}\n'
  const { root, calls } = await setup(t, payload => payload.op === 'config-read' ? { ...readResult, text: original } : { ok: true, hash: 'sha256:new', selectionToken: 'selection-B' })
  await renderEditor(root); await click(button('读取配置文件'))
  for (const mode of ['off', 'core', 'aggressive']) {
    type(document.querySelector('#fv-kernel-cache-mode'), mode)
    await click(button('完整 JSON'))
    const draft = document.querySelector('textarea').value
    assert.equal(JSON.parse(draft).cache.mode, mode)
    assert.deepEqual(JSON.parse(draft).bgm, { cli: 'bgm --keep', enabled: false })
    assert.match(draft, /9007199254740993/)
    assert.match(draft, /1e\+100/)
    await click(button('常用字段'))
  }
  assert.equal(calls.length, 1)
  await click(button('查看修改并保存'))
  assert.match(text(), /全局配置.*同一 VTX_CONFIG.*不限于当前时间线或会话/)
  assert.equal(button('确认保存文件').disabled, true)
  await click(button('确认保存文件'))
  assert.equal(calls.length, 1)
  await click(document.querySelector('[aria-label="配置保存确认"] input[type=checkbox]'))
  await click(button('确认保存文件'))
  const saved = calls.find(item => item.payload.op === 'config-save')
  assert.equal(saved.url, '/api/framivra/kernel-management')
  assert.equal(saved.init.credentials, 'same-origin')
  assert.deepEqual(Object.keys(saved.payload).sort(), ['confirmed', 'expectedHash', 'op', 'selectionToken', 'text'])
  assert.equal(saved.payload.expectedHash, readResult.hash)
  assert.equal(saved.payload.selectionToken, readResult.selectionToken)
  assert.equal(saved.payload.confirmed, true)
  assert.equal(JSON.parse(saved.payload.text).cache.mode, 'aggressive')
})

test('unrelated form edits do not materialize cache.mode and resetting removes only its override', async t => {
  const { root, calls } = await setup(t, () => readResult)
  await renderEditor(root); await click(button('读取配置文件'))
  type(document.querySelector('#framivra-config-group'), 'asr')
  type(document.querySelector('#fv-kernel-asr-enabled'), 'true')
  await click(button('完整 JSON'))
  assert.equal(Object.hasOwn(JSON.parse(document.querySelector('textarea').value), 'cache'), false)
  await click(button('常用字段'))
  type(document.querySelector('#framivra-config-group'), 'cache')
  type(document.querySelector('#fv-kernel-cache-mode'), 'off')
  await click(document.querySelector('[aria-label="缓存档位：使用默认值"]'))
  assert.equal(document.querySelector('#fv-kernel-cache-mode').value, 'core')
  await click(button('完整 JSON'))
  const value = JSON.parse(document.querySelector('textarea').value)
  assert.deepEqual(value.cache, {})
  assert.equal(value.asr.enabled, true)
  assert.match(document.querySelector('textarea').value, /9007199254740993/)
  assert.equal(calls.length, 1)
})

for (const invalid of ['null', '"future"', 'false']) test(`invalid cache mode ${invalid} is not disguised as default core and cannot save`, async t => {
  const original = `{"cache":{"mode":${invalid}}}`
  const { root, calls } = await setup(t, () => ({ ...readResult, text: original }))
  await renderEditor(root); await click(button('读取配置文件'))
  const select = document.querySelector('#fv-kernel-cache-mode')
  assert.equal(select.value, '')
  assert.equal(select.getAttribute('aria-invalid'), 'true')
  assert.match(select.selectedOptions[0].textContent, /无效/)
  await click(button('完整 JSON'))
  type(document.querySelector('textarea'), original + '\n')
  await click(button('查看修改并保存'))
  assert.match(text(), /cache.mode.*off \/ core \/ aggressive/)
  assert.equal(button('确认保存文件'), undefined)
  await click(button('常用字段'))
  type(document.querySelector('#fv-kernel-cache-mode'), 'core')
  assert.equal(document.querySelector('#fv-kernel-cache-mode').getAttribute('aria-invalid'), null)
  await click(button('查看修改并保存'))
  assert.ok(button('确认保存文件'))
  assert.equal(calls.length, 1)
})

test('invalid cache parent is not shown as an omitted default and remains raw until JSON repair', async t => {
  const original = '{"cache":false,"bgm":{"cli":"keep"}}'
  const { root, calls } = await setup(t, () => ({ ...readResult, text: original }))
  await renderEditor(root); await click(button('读取配置文件'))
  const select = document.querySelector('#fv-kernel-cache-mode')
  assert.equal(select.value, '')
  assert.equal(select.disabled, true)
  assert.equal(select.getAttribute('aria-invalid'), 'true')
  assert.match(select.selectedOptions[0].textContent, /分组无效.*完整 JSON/)
  assert.doesNotMatch(text(), /尚未显式设置/)
  await click(button('完整 JSON'))
  assert.equal(document.querySelector('textarea').value, original)
  assert.equal(calls.length, 1)
})

test('failed or uncertain config save locks retries and keeps draft for explicit reconciliation', async t => {
  let reads = 0
  const { root, calls } = await setup(t, payload => payload.op === 'config-read'
    ? { ...readResult, text: ++reads === 1 ? readResult.text : '{"other":true}', hash: reads === 1 ? 'sha256:old' : 'sha256:other', selectionToken: `selection-${reads}` }
    : { ok: false, error: 'outcome_unknown', message: '保存结果未知' })
  await renderEditor(root)
  await click(button('读取配置文件')); await click(button('完整 JSON'))
  const draft = '{"custom":9007199254740993}'
  type(document.querySelector('textarea'), draft)
  await click(button('查看修改并保存'))
  await click(document.querySelector('[aria-label="配置保存确认"] input[type=checkbox]'))
  await click(button('确认保存文件'))
  assert.equal(document.querySelector('textarea').value, draft)
  assert.equal(button('查看修改并保存').disabled, true)
  assert.match(text(), /不能直接重试/)
  await click(button('读取最新版本并比较'))
  assert.equal(document.querySelector('textarea').value, draft)
  assert.equal(button('确认保存文件').disabled, true)
  assert.equal(calls.filter(item => item.payload.op === 'config-save').length, 1)
})

test('a saved connection revision change keeps old document but disables writing it', async t => {
  const { root, calls } = await setup(t, () => readResult)
  await renderEditor(root); await click(button('读取配置文件')); await click(button('完整 JSON'))
  type(document.querySelector('textarea'), '{"custom":42}')
  await act(async () => root.render(h(KernelConfigEditor, { ...editorProps, revision: 8, configFile: '/home/owner/other.json' })))
  assert.equal(document.querySelector('textarea').value, '{"custom":42}')
  assert.equal(button('查看修改并保存').disabled, true)
  assert.match(text(), /当前文档已过期/)
  assert.equal(calls.length, 1)
})

test('invalid JSON never opens save confirmation or sends config-save', async t => {
  const { root, calls } = await setup(t, () => readResult)
  await renderEditor(root); await click(button('读取配置文件')); await click(button('完整 JSON'))
  type(document.querySelector('textarea'), '{ broken')
  await click(button('查看修改并保存'))
  assert.match(text(), /配置结构未通过/)
  assert.equal(button('确认保存文件'), undefined)
  assert.equal(calls.length, 1)
})

test('numeric form input retains exact source digits instead of Number rounding', async t => {
  const { root } = await setup(t, () => ({ ...readResult, text: '{"asr":{"vram_budget_mib":1.0000000000000001}}' }))
  await renderEditor(root); await click(button('读取配置文件'))
  type(document.querySelector('#framivra-config-group'), 'asr')
  const input = document.querySelector('#fv-kernel-asr-vram_budget_mib')
  assert.equal(input.value, '1.0000000000000001')
  type(input, '9007199254740993')
  assert.equal(button('查看修改并保存').disabled, true)
  await act(async () => input.dispatchEvent(new window.FocusEvent('focusout', { bubbles: true })))
  await click(button('完整 JSON'))
  assert.match(document.querySelector('textarea').value, /9007199254740993/)
})

test('read-only settings cannot scan, install or read sensitive config documents', async t => {
  const { root, calls } = await setup(t)
  await act(async () => root.render(h(BundleConfigForm, { form: store({ writable: false }) })))
  assert.equal(button('扫描已安装内核').disabled, true)
  await click(button('扫描已安装内核'))
  await click(button('配置文件'))
  assert.equal(button('读取配置文件').disabled, true)
  assert.equal(calls.length, 0)
})
