import test from 'node:test'
import assert from 'node:assert/strict'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { BundleConfigForm, registerBundleConfig, SETTINGS_NAMESPACE, BUNDLE_CONFIG_KEY } from '../src/client/settings.mjs'
const h = React.createElement

// Real React 18 + jsdom mount of the actual BundleConfigForm. The form is an
// in-test store double only (subscribe/getSnapshot/mutate); every state
// transition, revision pinning, guard and payload below is the component's
// own behavior. No Host/kernel claims.

async function mount(t) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://owner/' })
  globalThis.window = dom.window; globalThis.document = dom.window.document; globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const { createRoot } = await import('react-dom/client')
  const root = createRoot(document.getElementById('root'))
  t.after(async () => { await act(async () => root.unmount()); dom.window.close(); delete globalThis.window; delete globalThis.document })
  return { root, dom }
}

const ready = { status: 'ready', value: { vtxExecutable: '/opt/trusted/vtx', trustedPath: '/usr/local/bin:/usr/bin:/bin' }, revision: 7, writable: true }

/** Notifying fake store: setSnapshot pushes mirror updates through the real
 *  useSyncExternalStore subscription, exactly like a Host settings mirror. */
function fakeForm(initial, mutateImpl) {
  const listeners = new Set()
  const calls = []
  const form = {
    calls, listeners,
    setSnapshot(next) { form.snapshot = next; for (const listener of listeners) listener() },
    // This-sensitive methods, like the native ConfigForm (it reads this.store):
    // if the component ever detaches a method from its receiver (the crash the
    // real bundle hit), the assertion below fails — arrows would mask it.
    getSnapshot() { assert.equal(this, form, 'getSnapshot must be called with the form as receiver'); return form.snapshot },
    subscribe(listener) { assert.equal(this, form, 'subscribe must be called with the form as receiver'); listeners.add(listener); return () => listeners.delete(listener) },
    async mutate(ops, revision) {
      assert.equal(this, form, 'mutate must be called with the form as receiver')
      if (form.mutateImpl) return form.mutateImpl(ops, revision)
      calls.push({ ops, revision })
      return revision === form.snapshot.revision
    },
  }
  form.snapshot = initial
  form.mutateImpl = mutateImpl
  return form
}

const input = id => document.getElementById(id)
const type = (node, value) => {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
  act(() => { setter.call(node, value); node.dispatchEvent(new window.Event('input', { bubbles: true })) })
}
const click = node => act(() => node.dispatchEvent(new window.Event('click', { bubbles: true })))
const submit = () => act(async () => {
  const form = document.querySelector('form')
  await act(async () => form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })))
})
const text = () => document.body.textContent
const button = label => [...document.querySelectorAll('button')].find(n => n.textContent === label)

test('settings namespace and slot key match the Host contract', () => {
  assert.equal(SETTINGS_NAMESPACE, 'framivra-readonly')
  assert.equal(BUNDLE_CONFIG_KEY, 'framivra-dsh-sidebar')
})

test('loading and unavailable states are explicit', async t => {
  const { root } = await mount(t)
  await act(async () => root.render(h(BundleConfigForm, { form: fakeForm({ status: 'loading' }) })))
  assert.match(text(), /正在读取宿主配置/)
  const unavailable = fakeForm({ status: 'unavailable' })
  await act(async () => root.render(h(BundleConfigForm, { form: unavailable })))
  assert.match(text(), /配置命名空间不可用/)
})

test('ready form: prefilled values, edit, valid save submits both fields with the first-edit revision; typing clears the saved message', async t => {
  const { root } = await mount(t)
  const form = fakeForm(ready)
  await act(async () => root.render(h(BundleConfigForm, { form })))
  assert.equal(input('framivra-config-executable').value, '/opt/trusted/vtx')
  assert.equal(input('framivra-config-trustedpath').value, '/usr/local/bin:/usr/bin:/bin')
  type(input('framivra-config-executable'), '/other/vtx')
  assert.equal(input('framivra-config-executable').value, '/other/vtx')
  await submit()
  assert.deepEqual(form.calls, [{ ops: [
    { op: 'set', path: ['vtxExecutable'], value: '/other/vtx' },
    { op: 'set', path: ['trustedPath'], value: '/usr/local/bin:/usr/bin:/bin' },
  ], revision: 7 }])
  assert.match(text(), /已保存.*重新打开工程并重新授权/)
  // Draft cleared: the input falls back to the mirror value.
  assert.equal(input('framivra-config-executable').value, '/opt/trusted/vtx')
  // Typing again clears the stale saved message.
  type(input('framivra-config-executable'), '/third/vtx')
  assert.equal(text().includes('已保存'), false)
})

test('lost-update safety: mirror advancing never swaps the draft revision; conflict keeps the draft, shows latest values, explicit rebase never submits', async t => {
  const { root } = await mount(t)
  const form = fakeForm(ready)
  await act(async () => root.render(h(BundleConfigForm, { form })))
  type(input('framivra-config-executable'), '/other/vtx')
  // Another administrator writes trustedPath; the mirror updates live under us.
  await act(async () => form.setSnapshot({ status: 'ready', value: { vtxExecutable: '/opt/trusted/vtx', trustedPath: '/custom' }, revision: 8, writable: true }))
  // Latest Host values are visible (native details, wrapped code).
  assert.match(text(), /宿主配置已被其他修改更新/)
  assert.match(text(), /trustedPath = \/custom/)
  assert.match(text(), /revision = 8/)
  await submit()
  // The save still sends the revision captured at first edit — never the new one.
  assert.deepEqual(form.calls.map(call => call.revision), [7])
  assert.deepEqual(form.calls[0].ops.map(op => op.value), ['/other/vtx', '/usr/local/bin:/usr/bin:/bin'])
  assert.match(text(), /宿主拒绝了本次写入/)
  assert.equal(input('framivra-config-executable').value, '/other/vtx', 'draft kept after conflict')
  // Explicit rebase: one user click, only swaps the baseline, no submission.
  click(button('以最新版本重新校验草稿（不提交）'))
  assert.equal(form.calls.length, 1, 'rebase must not submit')
  assert.equal(input('framivra-config-executable').value, '/other/vtx', 'draft content untouched by rebase')
  // The next save is an independent action, now pinned to revision 8.
  await submit()
  assert.deepEqual(form.calls[1], { ops: [
    { op: 'set', path: ['vtxExecutable'], value: '/other/vtx' },
    { op: 'set', path: ['trustedPath'], value: '/usr/local/bin:/usr/bin:/bin' },
  ], revision: 8 })
})

test('a thrown save keeps the draft and reports the failure', async t => {
  const { root } = await mount(t)
  const form = fakeForm(ready, async () => { throw new Error('mirror gone') })
  await act(async () => root.render(h(BundleConfigForm, { form })))
  type(input('framivra-config-trustedpath'), '/custom')
  await submit()
  assert.match(text(), /保存失败：mirror gone。草稿保留/)
  assert.equal(input('framivra-config-trustedpath').value, '/custom')
})

test('invalid input is explained locally and never sent; empty fields submit the defaults', async t => {
  const { root } = await mount(t)
  const form = fakeForm(ready)
  await act(async () => root.render(h(BundleConfigForm, { form })))
  type(input('framivra-config-executable'), 'bin/vtx')
  assert.match(text(), /本地校验未通过/)
  await submit()
  assert.equal(form.calls.length, 0)
  type(input('framivra-config-executable'), '')
  await submit()
  assert.deepEqual(form.calls[0].ops.map(op => op.value), ['vtx', '/usr/local/bin:/usr/bin:/bin'])
})

test('handler guards: read-only, busy (in-flight mutate), and revision-less snapshots never send', async t => {
  const { root } = await mount(t)
  // Read-only mirror: visible notice, and a submit event does nothing.
  const ro = fakeForm({ ...ready, writable: false })
  await act(async () => root.render(h(BundleConfigForm, { form: ro })))
  assert.match(text(), /当前宿主配置只读/)
  type(input('framivra-config-executable'), '/other/vtx')
  await submit()
  assert.equal(ro.calls.length, 0)
  await act(async () => root.render(null)) // fresh mount per stage: no draft may survive a prop change
  // Revision missing: the draft cannot pin a baseline, so save refuses.
  const noRevision = fakeForm({ ...ready, revision: undefined })
  await act(async () => root.render(h(BundleConfigForm, { form: noRevision })))
  type(input('framivra-config-executable'), '/other/vtx')
  await submit()
  assert.equal(noRevision.calls.length, 0)
  await act(async () => root.render(null)) // fresh mount for the busy stage
  // Busy: a hanging mutate means a second submit during the flight is refused.
  let release
  const gate = new Promise(resolve => { release = resolve })
  const busyForm = fakeForm(ready, async (ops, revision) => { busyForm.calls.push({ ops, revision }); await gate; return true })
  await act(async () => root.render(h(BundleConfigForm, { form: busyForm })))
  type(input('framivra-config-executable'), '/other/vtx')
  await submit()
  await submit()
  assert.equal(busyForm.calls.length, 1, 'second submit during an in-flight save is refused')
  await act(async () => release(true))
  assert.match(text(), /已保存/)
})

test('unmount leaves no store subscription behind', async t => {
  const { root } = await mount(t)
  const form = fakeForm(ready)
  await act(async () => root.render(h(BundleConfigForm, { form })))
  assert.equal(form.listeners.size, 1)
  await act(async () => root.render(null))
  assert.equal(form.listeners.size, 0, 'useSyncExternalStore cleanup ran on unmount')
})

test('registerBundleConfig: served-only registration, page view renders the form, disposal chains cleanly', () => {
  const effects = [], injected = [], servedDisposed = [], slotDisposed = [], registered = []
  let servedRegister = null, registerDispose = null
  const form = fakeForm(ready)
  const ctx = {
    effect: fn => { effects.push(fn); const dispose = fn(); return () => dispose?.() },
    configForms: {
      whileServed: (namespaces, register) => {
        assert.deepEqual(namespaces, [SETTINGS_NAMESPACE])
        servedRegister = set => { registerDispose = register(set) }
        return () => { servedDisposed.push(true); registerDispose?.() }
      },
      get: ns => { assert.equal(ns, SETTINGS_NAMESPACE); return form },
    },
    slots: {
      inject: (name, factory) => { injected.push(name); const dispose = factory(); return () => { slotDisposed.push(true); dispose?.() } },
      register: (definition, Body) => { registered.push({ definition, Body }); return () => {} },
    },
  }
  const dispose = registerBundleConfig(ctx)
  assert.equal(injected.includes('plugins.bundle.config'), false) // not yet served
  servedRegister(new Set([SETTINGS_NAMESPACE]))
  assert.deepEqual(injected, ['plugins.bundle.config'])
  assert.deepEqual(registered.map(entry => entry.definition), [{ name: 'plugins.bundle.config', key: 'framivra-dsh-sidebar' }])
  const Body = registered[0].Body
  const page = Body({ view: 'page' })
  assert.equal(page.type, BundleConfigForm)
  assert.equal(page.props.form, form)
  assert.equal(Body({ view: 'summary' }), null)
  dispose()
  assert.deepEqual(servedDisposed, [true]); assert.deepEqual(slotDisposed, [true])
})

// Saved-configuration check (检查保存的配置): the button posts an empty-body
// POST to /api/framivra/diagnostics and renders the verdict. The outcome is
// pinned to the mirror revision; any later revision renders it expired, and a
// visible draft reminds that only the SAVED configuration is ever checked.

const okDiagnostics = { ok: true, schema: 'framivra.diagnostics/v1',
  config: { vtxExecutable: '/opt/trusted/vtx', trustedPath: '/usr/local/bin:/usr/bin:/bin' },
  executable: { path: '/opt/trusted/vtx', regularFile: true },
  capabilities: { schema: 'framivra.timeline.read/v1' },
  storeCapabilities: { ok: false, error: 'CLI_UNSUPPORTED' } }

function stubFetch(t, implement) {
  const calls = []
  const original = globalThis.fetch
  globalThis.fetch = async (url, init) => { calls.push({ url, init }); return implement(url, init) }
  t.after(() => { globalThis.fetch = original })
  return calls
}

test('check button posts an empty-body diagnostics request and renders the verdict', async t => {
  const { root } = await mount(t)
  const form = fakeForm(ready)
  const calls = stubFetch(t, () => new Response(JSON.stringify(okDiagnostics), { status: 200 }))
  await act(async () => root.render(h(BundleConfigForm, { form })))
  click(button('检查保存的配置'))
  await act(async () => {})
  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, '/api/framivra/diagnostics')
  assert.equal(calls[0].init.method, 'POST')
  assert.equal(calls[0].init.credentials, 'same-origin')
  assert.equal(calls[0].init.headers['Content-Type'], 'application/json')
  assert.equal(calls[0].init.body, '{}', 'no executable/config may ever be sent from the browser')
  assert.match(text(), /检查通过/)
  assert.match(text(), /可执行文件：\/opt\/trusted\/vtx/)
  assert.match(text(), /语义编辑不可用/)
})

test('semantic-edit availability renders when store capabilities pass', async t => {
  const { root } = await mount(t)
  const form = fakeForm(ready)
  stubFetch(t, () => new Response(JSON.stringify({ ...okDiagnostics, storeCapabilities: { ok: true } }), { status: 200 }))
  await act(async () => root.render(h(BundleConfigForm, { form })))
  click(button('检查保存的配置'))
  await act(async () => {})
  assert.match(text(), /语义编辑（store\/v1）可用/)
})

test('failed check surfaces the wire code, message and actionable hint', async t => {
  const { root } = await mount(t)
  const form = fakeForm(ready)
  stubFetch(t, () => new Response(JSON.stringify({ ok: false, error: 'EXECUTABLE_NOT_FOUND', message: '未在受信 PATH 找到 vtx' }), { status: 424 }))
  await act(async () => root.render(h(BundleConfigForm, { form })))
  click(button('检查保存的配置'))
  await act(async () => {})
  assert.match(text(), /检查未通过（EXECUTABLE_NOT_FOUND）：未在受信 PATH 找到 vtx/)
  assert.match(text(), /绝对受信路径/)
})

test('a revision change expires the previous check outcome', async t => {
  const { root } = await mount(t)
  const form = fakeForm(ready)
  stubFetch(t, () => new Response(JSON.stringify(okDiagnostics), { status: 200 }))
  await act(async () => root.render(h(BundleConfigForm, { form })))
  click(button('检查保存的配置'))
  await act(async () => {})
  assert.match(text(), /检查通过/)
  await act(async () => form.setSnapshot({ status: 'ready', value: { vtxExecutable: '/other/vtx', trustedPath: '/usr/local/bin:/usr/bin:/bin' }, revision: 8, writable: true }))
  assert.match(text(), /检查结果已过期/)
  assert.equal(text().includes('检查通过'), false, 'a stale verdict must never look valid')
})

test('a visible draft explains that the check reads only the saved configuration', async t => {
  const { root } = await mount(t)
  const form = fakeForm(ready)
  stubFetch(t, () => new Response(JSON.stringify(okDiagnostics), { status: 200 }))
  await act(async () => root.render(h(BundleConfigForm, { form })))
  click(button('检查保存的配置'))
  await act(async () => {})
  type(input('framivra-config-executable'), '/other/vtx')
  assert.match(text(), /检查只针对保存的配置：请先保存草稿，再检查。/)
  assert.equal(text().includes('检查通过'), false)
})

test('a failing fetch surfaces a network outcome', async t => {
  const { root } = await mount(t)
  const form = fakeForm(ready)
  stubFetch(t, async () => { throw new Error('boom') })
  await act(async () => root.render(h(BundleConfigForm, { form })))
  click(button('检查保存的配置'))
  await act(async () => {})
  assert.match(text(), /检查未通过（NETWORK）/)
  assert.match(text(), /boom/)
})

test('an in-flight check refuses a second click', async t => {
  const { root } = await mount(t)
  const form = fakeForm(ready)
  let release
  const gate = new Promise(resolve => { release = resolve })
  const calls = stubFetch(t, async () => { await gate; return new Response(JSON.stringify(okDiagnostics), { status: 200 }) })
  await act(async () => root.render(h(BundleConfigForm, { form })))
  click(button('检查保存的配置'))
  await act(async () => {})
  assert.match(text(), /检查中…/)
  click(button('检查中…'))
  await act(async () => {})
  assert.equal(calls.length, 1, 'second click during an in-flight check is refused')
  await act(async () => { release(); await gate })
  assert.match(text(), /检查通过/)
})
