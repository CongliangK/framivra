import React from 'react'
import { validateVtxExecutable, validateTrustedPath } from '../configuration.mjs'

const h = React.createElement

/** Scoped styles only (no global selectors). The shared WorkbenchStyles
 *  '.fv-settings' surface may later take over; these rules stay inside this
 *  namespace: system font inherited from the host theme, 13–14px text, 4px
 *  grid, one restrained mint accent, quiet color-mix borders, explicit
 *  focus-visible / disabled / busy states. */
const SETTINGS_STYLE = `
.fv-settings{font-family:inherit;font-size:13px;line-height:1.55;max-width:640px}
.fv-settings h3{font-size:14px;font-weight:600;margin:0 0 8px}
.fv-settings p{margin:0 0 12px}
.fv-settings small{display:block;margin-top:4px;opacity:.75}
.fv-settings label{display:block;margin-bottom:4px;font-weight:500}
.fv-settings input{font:inherit;padding:4px 8px;border-radius:6px;border:1px solid color-mix(in srgb,currentColor 25%,transparent);background:color-mix(in srgb,currentColor 4%,transparent);color:inherit}
.fv-settings input:focus-visible{outline:2px solid var(--fv-settings-accent,#3fbf9f);outline-offset:1px}
.fv-settings input[aria-invalid="true"]{border-color:#e0645a}
.fv-settings input:disabled{opacity:.55}
.fv-settings button{font:inherit;font-size:13px;font-weight:500;padding:6px 12px;margin-right:8px;border-radius:6px;border:1px solid color-mix(in srgb,currentColor 20%,transparent);background:transparent;color:inherit;cursor:pointer;transition:transform .1s cubic-bezier(.23,1,.32,1)}
.fv-settings button:focus-visible{outline:2px solid var(--fv-settings-accent,#3fbf9f);outline-offset:2px}
.fv-settings button:active:not(:disabled){transform:scale(.98)}
.fv-settings button:disabled{opacity:.55;cursor:not-allowed}
.fv-settings button[type="submit"]:not(:disabled){font-weight:600;border-color:color-mix(in srgb,var(--fv-settings-accent,#3fbf9f) 55%,transparent);color:var(--fv-settings-accent,#3fbf9f)}
.fv-settings [role="alert"]{color:#e0645a}
.fv-settings [role="status"]{color:inherit}
.fv-settings .fv-check-ok{border-left:2px solid var(--fv-settings-accent,#3fbf9f);padding-left:8px}
.fv-settings .fv-check-bad{border-left:2px solid #e0645a;padding-left:8px}
@media (prefers-reduced-motion:reduce){.fv-settings button{transition:none}}
`

/** Actionable Chinese next steps per diagnostic failure code (shared wire codes). */
const CHECK_HINTS = {
  EXECUTABLE_NOT_FOUND: '请将 vtxExecutable 配置为绝对受信路径，或把 vtx 安装到 trustedPath 中的目录后保存再检查。',
  EXECUTABLE_NOT_REGULAR: '请确认路径指向受信目录中的常规可执行文件（而非目录、设备或异常符号项）。',
  UNTRUSTED_EXECUTABLE: 'vtx 位于会话工作区内，禁止执行：请安装到工作区外的受信目录（如 /usr/local/bin）。',
  PROTOCOL_MISMATCH: '已安装 vtx 的协议不满足插件要求：请升级 vtx 后重试。',
  CLI_UNSUPPORTED: '当前 vtx 不支持语义写入（store）；只读预览仍可用。如需语义编辑请升级 vtx。',
  CONFIG_INVALID: '宿主配置值非法：请修正 vtxExecutable / trustedPath 并保存后再检查。',
  CONFIG_CHANGED: '检查期间宿主配置被修改，结果已作废：请重新检查。',
  CANCELLED: '检查被取消或超时：请重试。',
}

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
  /** Saved-configuration check outcome. `revision` pins the mirror revision
   *  the verdict was produced under; any later revision change renders it
   *  expired, so a stale outcome can never be presented as valid. */
  const [check, setCheck] = React.useState(null)
  const checking = check?.status === 'busy'
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
  /** Check the SAVED configuration only (never the draft): the diagnostics
   *  route accepts an empty body and resolves the executable from the Host's
   *  saved trusted values. A configuration the browser claims is never sent. */
  async function runDiagnostics(event) {
    event.preventDefault()
    if (busy || checking) return
    setCheck({ status: 'busy' })
    let outcome
    try {
      const response = await fetch('/api/framivra/diagnostics', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: '{}' })
      const value = await response.json().catch(() => null)
      if (!response.ok || !value?.ok) outcome = { status: 'failed', error: value?.error ?? `HTTP_${response.status}`, message: value?.message ?? '宿主诊断接口返回异常。', revision: state.revision }
      else outcome = { status: 'ok', path: value.executable?.path ?? '（未上报）', semantic: value.storeCapabilities?.ok === true, revision: state.revision }
    } catch (error) {
      outcome = { status: 'failed', error: 'NETWORK', message: `无法连接宿主诊断接口：${error?.message ?? error}`, revision: state.revision }
    }
    setCheck(outcome)
  }
  return h('section', { className: 'fv-settings', 'aria-label': 'Framivra 插件配置' },
    h('style', null, SETTINGS_STYLE),
    h('form', { onSubmit: save },
      h('h3', null, 'Framivra · 宿主信任的 vtx 可执行文件'),
      h('p', null, '此配置仅由宿主管理员在插件管理页修改；预览请求和模型工具不能修改此配置。更改保存后立即生效：所有已打开的预览与写授权按“配置已变更”失效，须重新打开并重新授权。'),
      field('framivra-config-executable', 'vtxExecutable（字面 "vtx" 或宿主绝对路径）', '留空表示默认 "vtx"（在下方 trustedPath 中查找）。相对路径一律拒绝；路径不得位于会话工作区内。', current.vtxExecutable, value => edit('vtxExecutable', value), disabled, invalid && invalid.message.includes('vtxExecutable')),
      field('framivra-config-trustedpath', 'trustedPath（冒号分隔的宿主绝对目录）', '例：/usr/local/bin:/usr/bin:/bin。每段必须是以 / 开头的宿主绝对路径；空段或相对段会被拒绝。', current.trustedPath, value => edit('trustedPath', value), disabled, invalid && !invalid.message.includes('vtxExecutable')),
      invalid ? h('p', { role: 'alert' }, `本地校验未通过，不会发送：${invalid.message}`) : null,
      message ? h('p', { role: message.role }, message.text) : null,
      // The check only ever reads the SAVED configuration; say so while a
      // draft exists instead of letting an old verdict look current.
      editing ? h('p', { role: 'status' }, '检查只针对保存的配置：请先保存草稿，再检查。') : null,
      checking ? h('p', { role: 'status' }, '正在检查保存的配置…') : null,
      !checking && check && state.revision !== check.revision ? h('p', { role: 'alert' }, '检查结果已过期：宿主配置已变更，请重新检查。') : null,
      !checking && !editing && check && state.revision === check.revision && check.status === 'ok' ? h('p', { role: 'status', className: 'fv-check-ok' },
        '检查通过：已保存配置可用。',
        h('br', null), `可执行文件：${check.path}`,
        h('br', null), check.semantic ? '语义编辑（store/v1）可用。' : '语义编辑不可用：只读预览仍可用。') : null,
      !checking && !editing && check && state.revision === check.revision && check.status === 'failed' ? h('p', { role: 'alert', className: 'fv-check-bad' },
        `检查未通过（${check.error}）：${check.message}`,
        CHECK_HINTS[check.error] ? h('br', null) : null,
        CHECK_HINTS[check.error] ?? null) : null,
      stale ? h('details', null,
        h('summary', null, '宿主配置已被其他修改更新：最新值为'),
        h('p', null, h('code', { style: { whiteSpace: 'pre-wrap', wordBreak: 'break-all' } }, `vtxExecutable = ${mirror.vtxExecutable || '（空）'}\ntrustedPath = ${mirror.trustedPath || '（空）'}\nrevision = ${String(state.revision)}`)),
        h('button', { type: 'button', disabled: busy, onClick: () => setDraft(previous => previous && { ...previous, revision: state.revision }) }, '以最新版本重新校验草稿（不提交）')) : null,
      !state.writable ? h('p', { role: 'status' }, '当前宿主配置只读（如由 profile 锁定）；无法在此保存。') : null,
      h('button', { type: 'submit', disabled: disabled || invalid || !editing }, busy ? '保存中…' : '保存配置'),
      editing ? h('button', { type: 'button', disabled: busy, onClick: () => { setDraft(null); setMessage(null) } }, '放弃修改') : null,
      h('button', { type: 'button', disabled: busy || checking, onClick: runDiagnostics }, checking ? '检查中…' : '检查保存的配置')))
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
