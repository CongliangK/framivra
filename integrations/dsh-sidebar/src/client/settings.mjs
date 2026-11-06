import React from 'react'
import { validateVtxExecutable, validateTrustedPath } from '../configuration.mjs'

const h = React.createElement

/** Loader entry id (cordis.patch.yml) whose namespace the Host settings service serves. */
export const SETTINGS_NAMESPACE = 'framivra-readonly'
/** Plugin Manager bundle-config slot key: the npm package name. */
export const BUNDLE_CONFIG_KEY = 'framivra-dsh-sidebar'
/** Plugin Manager bundle-config slot name (DSH client contract). */
const BUNDLE_CONFIG_SLOT = 'plugins.bundle.config'

const field = (id, label, hint, value, onChange, disabled, invalid) =>
  h('p', null,
    h('label', { htmlFor: id }, h('strong', null, label)),
    h('input', { id, type: 'text', style: { width: '100%', boxSizing: 'border-box' }, value, disabled, 'aria-invalid': invalid ? 'true' : undefined, onChange: event => onChange(event.target.value) }),
    h('br', null),
    h('small', null, hint))

/**
 * The bundle's own configuration form (Plugins → Framivra). The Host owns the
 * values and writes; this component only drafts, validates locally against the
 * exact Host contract, and submits one atomic mutation pinned to the revision
 * the draft was first edited from (lost-update safe: a later Host-side change
 * can only reject the draft, never be silently overwritten by it). No
 * automatic apply, no automatic resend, no reset of the user's draft on
 * conflict; rebasing onto a newer revision is an explicit, separate action.
 */
export function BundleConfigForm({ form }) {
  // The native ConfigForm's methods are receiver-sensitive (they read
  // `this.store`), so the unbound `form.subscribe` reference passed straight to
  // useSyncExternalStore would crash. Wrap through stable useCallback arrows
  // that keep `form` as the receiver on every call; per-render .bind() would
  // produce an unstable subscribe identity and resubscribe every render.
  const subscribe = React.useCallback(listener => form.subscribe(listener), [form])
  const getSnapshot = React.useCallback(() => form.getSnapshot(), [form])
  const state = React.useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
  const [draft, setDraft] = React.useState(null)
  const [message, setMessage] = React.useState(null)
  const [busy, setBusy] = React.useState(false)
  if (state.status === 'loading') return h('section', { 'aria-label': 'Framivra 插件配置' }, h('p', { role: 'status' }, '正在读取宿主配置…'))
  if (state.status === 'unavailable') return h('section', { 'aria-label': 'Framivra 插件配置' },
    h('p', { role: 'alert' }, '配置命名空间不可用：需要插件处于启用状态并由宿主提供设置服务后才能配置。'))
  const values = state.value ?? {}
  const mirror = { vtxExecutable: values.vtxExecutable ?? '', trustedPath: values.trustedPath ?? '' }
  const current = draft ?? mirror
  const editing = draft !== null && (draft.vtxExecutable !== mirror.vtxExecutable || draft.trustedPath !== mirror.trustedPath)
  const stale = draft !== null && state.revision !== draft.revision
  let invalid
  try { validateVtxExecutable(current.vtxExecutable === '' ? undefined : current.vtxExecutable) } catch (error) { invalid = error }
  if (!invalid) { try { validateTrustedPath(current.trustedPath === '' ? undefined : current.trustedPath) } catch (error) { invalid = error } }
  const disabled = busy || !state.writable
  const edit = (key, value) => {
    setMessage(null) // typing invalidates the previous outcome message
    setDraft(previous => ({
      ...(previous ?? { ...mirror, revision: state.revision }), // capture expectedRevision once, at first edit
      [key]: value,
    }))
  }
  async function save(event) {
    event.preventDefault()
    // Handler-level guards, not just the disabled button: never send from a
    // busy, read-only, unedited, locally invalid, or revision-less state.
    if (busy || !state.writable || !editing || invalid || draft.revision == null) return
    // Empty input means "the default": send the normalized default, never ''.
    const value = {
      vtxExecutable: validateVtxExecutable(current.vtxExecutable === '' ? undefined : current.vtxExecutable),
      trustedPath: validateTrustedPath(current.trustedPath === '' ? undefined : current.trustedPath),
    }
    setBusy(true); setMessage(null)
    let accepted
    try {
      accepted = await form.mutate([
        { op: 'set', path: ['vtxExecutable'], value: value.vtxExecutable },
        { op: 'set', path: ['trustedPath'], value: value.trustedPath },
      ], draft.revision)
    } catch (error) {
      setBusy(false); setMessage({ role: 'alert', text: `保存失败：${error?.message ?? error}。草稿保留，可重试。` })
      return
    }
    setBusy(false)
    if (accepted) { setDraft(null); setMessage({ role: 'status', text: '已保存。已打开的预览与写授权立即失效：请重新打开工程并重新授权。' }) }
    else setMessage({ role: 'alert', text: '宿主拒绝了本次写入（可能被其他修改抢先）。最新值已重新读取；你的草稿保留，请对照后重试。' })
  }
  return h('section', { 'aria-label': 'Framivra 插件配置' },
    h('form', { onSubmit: save },
      h('h3', null, 'Framivra · 宿主信任的 vtx 可执行文件'),
      h('p', null, '此配置仅由宿主管理员在插件管理页修改；预览请求和模型工具不能修改此配置。更改保存后立即生效：所有已打开的预览与写授权按“配置已变更”失效，须重新打开并重新授权。'),
      field('framivra-config-executable', 'vtxExecutable（字面 "vtx" 或宿主绝对路径）', '留空表示默认 "vtx"（在下方 trustedPath 中查找）。相对路径一律拒绝；路径不得位于会话工作区内。', current.vtxExecutable, value => edit('vtxExecutable', value), disabled, invalid && invalid.message.includes('vtxExecutable')),
      field('framivra-config-trustedpath', 'trustedPath（冒号分隔的宿主绝对目录）', '例：/usr/local/bin:/usr/bin:/bin。每段必须是以 / 开头的宿主绝对路径；空段或相对段会被拒绝。', current.trustedPath, value => edit('trustedPath', value), disabled, invalid && !invalid.message.includes('vtxExecutable')),
      invalid ? h('p', { role: 'alert' }, `本地校验未通过，不会发送：${invalid.message}`) : null,
      message ? h('p', { role: message.role }, message.text) : null,
      stale ? h('details', null,
        h('summary', null, '宿主配置已被其他修改更新：最新值为'),
        h('p', null, h('code', { style: { whiteSpace: 'pre-wrap', wordBreak: 'break-all' } }, `vtxExecutable = ${mirror.vtxExecutable || '（空）'}\ntrustedPath = ${mirror.trustedPath || '（空）'}\nrevision = ${String(state.revision)}`)),
        h('button', { type: 'button', disabled: busy, onClick: () => setDraft(previous => previous && { ...previous, revision: state.revision }) }, '以最新版本重新校验草稿（不提交）')) : null,
      !state.writable ? h('p', { role: 'status' }, '当前宿主配置只读（如由 profile 锁定）；无法在此保存。') : null,
      h('button', { type: 'submit', disabled: disabled || invalid || !editing }, busy ? '保存中…' : '保存配置'),
      editing ? h('button', { type: 'button', disabled: busy, onClick: () => { setDraft(null); setMessage(null) } }, '放弃修改') : null))
}

/**
 * Register the bundle configuration page while the Host serves this plugin's
 * settings namespace. The Plugins page renders `plugins.bundle.config` (keyed
 * by package name) with `view: 'page'` only; the form itself comes from
 * ctx.configForms on the plugin's own Loader entry id, resolved once here so
 * the render callback never reaches back into ctx.
 */
export function registerBundleConfig(ctx) {
  return ctx.effect(() => {
    const form = ctx.configForms.get(SETTINGS_NAMESPACE)
    return ctx.configForms.whileServed([SETTINGS_NAMESPACE], () => ctx.slots.inject(BUNDLE_CONFIG_SLOT, () => ctx.slots.register({
      name: BUNDLE_CONFIG_SLOT, key: BUNDLE_CONFIG_KEY,
    }, ({ view }) => view === 'page'
      ? h(BundleConfigForm, { form })
      : null)))
  })
}
