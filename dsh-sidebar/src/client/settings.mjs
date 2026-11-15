import React from 'react'
import { validateVtxExecutable, validateTrustedPath, validateVtxConfigFile, readLogMaxPerMinute } from '../configuration.mjs'
import { KernelDiscovery, KernelConfigEditor } from './kernel-settings.mjs'

const h = React.createElement

/** Scoped styles only (no global selectors). The shared WorkbenchStyles
 *  '.fv-settings' surface may later take over; these rules stay inside this
 *  namespace: system font inherited from the host theme, 13–14px text, 4px
 *  grid, one restrained mint accent, quiet color-mix borders, explicit
 *  focus-visible / disabled / busy states. */
export const SETTINGS_STYLE = `
.fv-settings{--fv-line:color-mix(in srgb,currentColor 20%,transparent);--fv-soft:color-mix(in srgb,currentColor 5%,transparent);font-family:inherit;font-size:14px;line-height:1.55;max-width:960px;min-width:0;color:inherit}
.fv-settings *{box-sizing:border-box}
.fv-settings [hidden]{display:none!important}
.fv-settings h2{font-size:20px;font-weight:600;margin:0 0 4px}
.fv-settings h3{font-size:15px;font-weight:600;margin:0 0 8px}
.fv-settings h4{font-size:14px;margin:0 0 8px}
.fv-settings p{margin:0 0 12px;overflow-wrap:anywhere}
.fv-settings small{display:block;font-size:13px;margin:4px 0 8px}
.fv-settings label{display:block;margin-bottom:6px;font-weight:500}
.fv-settings input:not([type=checkbox]),.fv-settings textarea,.fv-settings select{font:inherit;width:100%;min-height:38px;padding:8px 10px;border-radius:6px;border:1px solid var(--fv-line);background:var(--fv-soft);color:inherit}
.fv-settings option{background:Canvas;color:CanvasText}
.fv-settings textarea{resize:vertical;font-family:ui-monospace,monospace;font-size:13px;line-height:1.6;tab-size:2}
.fv-settings :is(input,textarea,select,button,summary):focus-visible{outline:2px solid currentColor;outline-offset:3px}
.fv-settings input[aria-invalid=true]{border-color:currentColor}
.fv-settings button{font:inherit;font-size:13px;font-weight:500;min-height:36px;padding:7px 12px;border-radius:6px;border:1px solid var(--fv-line);background:transparent;color:inherit;cursor:pointer}
.fv-settings button:hover:not(:disabled){background:var(--fv-soft)}
.fv-settings button:disabled{cursor:not-allowed;border-style:dashed}
.fv-settings button[type=submit]:not(:disabled){background:var(--fv-soft);font-weight:600;border-color:currentColor}
.fv-settings [role=alert]{border-left:2px solid currentColor;padding-left:10px}
.fv-settings .fv-check-ok,.fv-settings .fv-check-bad{border-left:2px solid currentColor;padding:10px 12px;background:var(--fv-soft)}
.fv-settings-nav{display:flex;gap:4px;margin:20px 0 24px;border-bottom:1px solid var(--fv-line)}
.fv-settings-nav button{border:none;border-bottom:2px solid transparent;border-radius:0;padding:10px 18px}
.fv-settings-nav button[aria-pressed=true]{border-bottom-color:currentColor;font-weight:600}
.fv-settings-row{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:12px}
.fv-settings-row>div{min-width:0}
.fv-settings-row>button{flex:none}
.fv-settings-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px}
.fv-settings-muted{font-size:13px}
.fv-settings-path{display:block;overflow-wrap:anywhere;white-space:pre-wrap;font-size:13px;margin:4px 0}
.fv-settings-list{list-style:none;margin:8px 0 16px;padding:0}
.fv-settings-list li{display:flex;gap:16px;justify-content:space-between;align-items:center;padding:12px 0;border-bottom:1px solid var(--fv-line)}
.fv-settings-list li>div{min-width:0}
.fv-settings-list li>button{flex:none}
.fv-settings details{margin:16px 0}
.fv-settings summary{cursor:pointer;font-weight:500;margin-bottom:12px}
.fv-settings-well{padding:16px;background:var(--fv-soft);border-radius:6px;margin:16px 0}
.fv-settings-well dl{margin:12px 0;display:grid;grid-template-columns:80px minmax(0,1fr);gap:8px 12px}
.fv-settings-well dd{margin:0;min-width:0}
.fv-settings-well dt{font-size:13px}
.fv-settings .fv-settings-check{display:flex;gap:10px;align-items:flex-start;margin:16px 0}
.fv-settings-check input{margin-top:4px;flex:none;width:16px;height:16px;accent-color:currentColor}
.fv-settings pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:320px;overflow:auto;font-size:12px;margin:8px 0}
.fv-settings-diff{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.fv-settings-diff>div{min-width:0}
.fv-settings-fields{display:grid;grid-template-columns:1fr 1fr;gap:20px 24px;margin:20px 0}
.fv-settings-field{min-width:0}
.fv-settings-field>button{margin-top:8px}
.fv-settings-connection{padding-top:20px;margin-top:24px;border-top:1px solid var(--fv-line)}
.fv-settings-footer{margin-top:24px;padding-top:16px;border-top:1px solid var(--fv-line)}
@media(max-width:640px){.fv-settings-row{flex-direction:column;gap:8px}.fv-settings-fields,.fv-settings-diff{grid-template-columns:1fr}.fv-settings-list li{align-items:flex-start;flex-direction:column;gap:4px}.fv-settings-nav button{padding:10px 12px}.fv-settings button{min-height:44px}.fv-settings-well dl{grid-template-columns:1fr;gap:2px}}
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
  const [tab, setTab] = React.useState('kernel')
  const diagnosticsTask = React.useRef(null)
  React.useEffect(() => () => diagnosticsTask.current?.abort(), [])
  /** Saved-configuration check outcome. `revision` pins the mirror revision
   *  the verdict was produced under; any later revision change renders it
   *  expired, so a stale outcome can never be presented as valid. */
  const [check, setCheck] = React.useState(null)
  const checking = check?.status === 'busy'
  if (state.status === 'loading') return h('section', { 'aria-label': 'Framivra 插件配置' }, h('p', { role: 'status' }, '正在读取宿主配置…'))
  if (state.status === 'unavailable') return h('section', { 'aria-label': 'Framivra 插件配置' },
    h('p', { role: 'alert' }, '配置命名空间不可用：需要插件处于启用状态并由宿主提供设置服务后才能配置。'))
  const values = state.value ?? {}
  const mirror = { vtxExecutable: values.vtxExecutable ?? '', trustedPath: values.trustedPath ?? '', vtxConfigFile: values.vtxConfigFile ?? '', logMaxPerMinute: String(values.logMaxPerMinute ?? 120) }
  const current = draft ?? mirror
  const editing = draft !== null && Object.keys(mirror).some(key => draft[key] !== mirror[key])
  const stale = draft !== null && state.revision !== draft.revision
  let invalid
  try { validateVtxExecutable(current.vtxExecutable === '' ? undefined : current.vtxExecutable) } catch (error) { invalid = error }
  if (!invalid) { try { validateTrustedPath(current.trustedPath === '' ? undefined : current.trustedPath) } catch (error) { invalid = error } }
  if (!invalid) { try { validateVtxConfigFile(current.vtxConfigFile) } catch (error) { invalid = error } }
  if (!invalid) {
    try {
      if (!/^\d+$/.test(current.logMaxPerMinute)) throw new Error('日志上限须为 0–1000 的整数')
      readLogMaxPerMinute({ logMaxPerMinute: Number(current.logMaxPerMinute) })
    } catch (error) { invalid = error }
  }
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
      const operations = [
        { op: 'set', path: ['vtxExecutable'], value: value.vtxExecutable },
        { op: 'set', path: ['trustedPath'], value: value.trustedPath },
      ]
      if (current.vtxConfigFile !== mirror.vtxConfigFile) operations.push({ op: 'set', path: ['vtxConfigFile'], value: validateVtxConfigFile(current.vtxConfigFile) })
      if (current.logMaxPerMinute !== mirror.logMaxPerMinute) operations.push({ op: 'set', path: ['logMaxPerMinute'], value: Number(current.logMaxPerMinute) })
      accepted = await form.mutate(operations, draft.revision)
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
    if (busy || checking || diagnosticsTask.current) return
    const controller = new AbortController(); diagnosticsTask.current = controller
    setTab('check')
    setCheck({ status: 'busy' })
    let outcome
    try {
      const response = await fetch('/api/framivra/diagnostics', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: '{}', signal: AbortSignal.any([controller.signal, AbortSignal.timeout(35000)]) })
      const value = await response.json().catch(() => null)
      if (!response.ok || !value?.ok) outcome = { status: 'failed', error: value?.error ?? `HTTP_${response.status}`, message: value?.message ?? '宿主诊断接口返回异常。', revision: state.revision }
      else outcome = { status: 'ok', path: value.executable?.path ?? '（未上报）', semantic: value.storeCapabilities?.ok === true, revision: state.revision }
    } catch (error) {
      outcome = { status: 'failed', error: 'NETWORK', message: `无法连接宿主诊断接口：${error?.message ?? error}`, revision: state.revision }
    }
    if (diagnosticsTask.current === controller) diagnosticsTask.current = null
    if (!controller.signal.aborted) setCheck(outcome)
  }
  return h('section', { className: 'fv-settings', 'aria-label': 'Framivra 插件配置' },
    h('style', null, SETTINGS_STYLE),
    h('h2', null, 'VTX 内核与配置'),
    h('p', { className: 'fv-settings-muted' }, '连接内核，再打开视频工程。安装与保存始终由你确认。'),
    h('nav', { className: 'fv-settings-nav', 'aria-label': 'VTX 设置分类' },
      ...[['kernel', '内核'], ['config', '配置文件'], ['check', '检查']].map(([key, label]) => h('button', { key, type: 'button', 'aria-pressed': tab === key, 'aria-controls': `fv-settings-${key}`, onClick: () => setTab(key) }, label))),
    h('form', { onSubmit: save },
      h('div', { id: 'fv-settings-kernel', hidden: tab !== 'kernel' },
        h(KernelDiscovery, { disabled, onSelect: path => edit('vtxExecutable', path), onConfigPath: path => { edit('vtxConfigFile', path); setTab('config') } }),
        h('section', { className: 'fv-settings-connection', 'aria-label': '内核连接' },
          h('h3', null, '当前连接'),
          field('framivra-config-executable', 'VTX 可执行文件', '填写内核绝对路径；留空从受信目录查找 vtx。选用即表示信任该程序，不能位于会话工作区内。', current.vtxExecutable, value => edit('vtxExecutable', value), disabled, invalid && invalid.message.includes('vtxExecutable')),
          h('details', null, h('summary', null, '高级连接设置'),
            field('framivra-config-trustedpath', '受信搜索目录（trustedPath）', '用冒号分隔绝对目录，例如 /usr/local/bin:/usr/bin:/bin。', current.trustedPath, value => edit('trustedPath', value), disabled, invalid && invalid.message.includes('trustedPath')),
            field('framivra-config-logmax', '每分钟诊断日志上限', '0–1000 的整数；0 为关闭。仅记录脱敏错误摘要，不记录配置内容。', current.logMaxPerMinute, value => edit('logMaxPerMinute', value), disabled, false)))),
      h('div', { id: 'fv-settings-config', hidden: tab !== 'config' },
        field('framivra-config-file', '内核配置文件', '独立的绝对 JSON 路径。留空沿用内核默认；指定后通过 VTX_CONFIG 供内核使用，不叠加默认文件。', current.vtxConfigFile, value => edit('vtxConfigFile', value), disabled, invalid && invalid.message.includes('vtxConfigFile')),
        h(KernelConfigEditor, { revision: state.revision, configFile: mirror.vtxConfigFile, writable: state.writable, connectionDirty: editing || busy, onSuggestedPath: path => edit('vtxConfigFile', path) })),
      h('div', { id: 'fv-settings-check', hidden: tab !== 'check' },
        h('h3', null, '检查内核连接'),
        h('p', null, '运行已选内核的只读能力探针，核对可执行文件、时间线预览及语义编辑协议。'),
        h('p', { className: 'fv-settings-muted' }, '这不是完整渲染测试，不会安装 FFmpeg、浏览器或模型。'),
        checking ? h('p', { role: 'status' }, '正在读取内核能力…') : null),
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
      h('div', { className: 'fv-settings-footer' },
        h('p', { className: 'fv-settings-muted' }, '仅宿主管理员可修改。切换内核或配置文件后，已打开的预览与写授权失效，需重新打开工程。'),
        h('div', { className: 'fv-settings-actions' },
          h('button', { type: 'submit', disabled: disabled || !!invalid || !editing }, busy ? '保存中…' : '保存配置'),
          editing ? h('button', { type: 'button', disabled: busy, onClick: () => { setDraft(null); setMessage(null) } }, '放弃修改') : null,
          h('button', { type: 'button', disabled: busy || checking, onClick: runDiagnostics }, checking ? '检查中…' : '检查保存的配置')))))
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
