import React from 'react'
import { boundedText } from '../bounded.mjs'
import { parseJSON } from '../json.mjs'
import { checkKernelConfig, updateKernelConfigField, KERNEL_CONFIG_GROUPS, KERNEL_CONFIG_FIELDS } from '../kernel-config.mjs'

const h = React.createElement
const LIMIT = 1024 * 1024
export async function kernelRequest(payload, signal) {
  const response = await fetch('/api/framivra/kernel-management', {
    method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload), signal,
  })
  const value = JSON.parse(await boundedText(response, LIMIT, signal))
  if (!response.ok || !value?.ok) {
    const error = new Error(value?.message ?? value?.error ?? `请求失败（HTTP ${response.status}）`)
    error.code = value?.code ?? value?.error
    error.suggestedConfigFile = value?.suggestedConfigFile
    throw error
  }
  return value
}
const message = (text, alert = false) => text ? h('p', { role: alert ? 'alert' : 'status', className: 'fv-settings-message' }, text) : null
const pathText = path => h('code', { className: 'fv-settings-path' }, path)
const action = (label, onClick, disabled = false, props = {}) => h('button', { type: 'button', onClick, disabled, ...props }, label)

/** Discovery is filesystem-only. Selecting a candidate drafts an administrator
 * setting; it neither executes the candidate nor silently saves that setting. */
export function KernelDiscovery({ onSelect, onConfigPath, disabled }) {
  const [scan, setScan] = React.useState(null)
  const [wheel, setWheel] = React.useState('')
  const [preview, setPreview] = React.useState(null)
  const [consent, setConsent] = React.useState(false)
  const [job, setJob] = React.useState(null)
  const [busy, setBusy] = React.useState('')
  const [error, setError] = React.useState('')
  const task = React.useRef(null)
  const mounted = React.useRef(true)
  React.useEffect(() => { mounted.current = true; return () => { mounted.current = false; task.current?.abort() } }, [])
  const run = async (kind, payload, apply) => {
    if (task.current || disabled) return
    const controller = new AbortController(); task.current = controller
    setBusy(kind); setError('')
    try {
      const value = await kernelRequest(payload, AbortSignal.any([controller.signal, AbortSignal.timeout(45000)]))
      if (mounted.current && !controller.signal.aborted) apply(value)
    } catch (e) { if (mounted.current && !controller.signal.aborted) setError(e.message) }
    finally { if (task.current === controller) task.current = null; if (mounted.current) setBusy('') }
  }
  const running = job && ['queued', 'running'].includes(job.state)
  // Poll only the job explicitly started/chosen by this UI. Unmount cancels
  // requests, not a possibly-writing installation; the Host owns that lifetime.
  React.useEffect(() => {
    if (!running) return
    const controller = new AbortController()
    let timer
    const refresh = async () => {
      try {
        const data = await kernelRequest({ op: 'install-status', jobId: job.jobId }, controller.signal)
        const next = data.jobs.find(item => item.jobId === job.jobId)
        if (!controller.signal.aborted && next) { setJob(next); setError('') }
      } catch (e) { if (!controller.signal.aborted) setError(`安装状态暂不可读：${e.message}。请查询状态，不要重复安装。`) }
      if (!controller.signal.aborted) timer = setTimeout(refresh, 2000)
    }
    refresh()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [running, job?.jobId])
  const unavailable = disabled || !!busy
  return h('div', { className: 'fv-kernel-discovery' },
    h('div', { className: 'fv-settings-row' },
      h('div', null, h('h3', null, '已有内核'), h('p', { className: 'fv-settings-muted' }, '扫描常用安装目录，不运行发现的程序。')),
      action(busy === 'scan' ? '扫描中…' : '扫描已安装内核', () => run('scan', { op: 'scan' }, setScan), unavailable)),
    scan ? h(React.Fragment, null,
      scan.candidates.length ? h('ul', { className: 'fv-settings-list', 'aria-label': '发现的 VTX 内核' }, scan.candidates.map(item => h('li', { key: item.path },
        h('div', null, pathText(item.path), h('small', null, `${({ 'current-setting': '当前配置', 'trusted-or-common-bin': '常用命令目录', 'common-venv': '独立 Python 环境' })[item.source] ?? item.source} · 未执行验证`)),
        action('填入路径', () => onSelect(item.path), unavailable)))) : message('未在常用目录找到内核。可手动填写绝对路径，或在下方安装本地包。'),
      scan.warnings?.length ? h('details', null, h('summary', null, '扫描范围与提示'), h('ul', null, scan.warnings.map((item, index) => h('li', { key: index }, typeof item === 'string' ? item : item.message)))) : null,
      scan.suggestedConfigFile ? action('填入推荐配置路径', () => onConfigPath(scan.suggestedConfigFile), unavailable) : null) : null,
    h('details', { className: 'fv-settings-install' },
      h('summary', null, '安装新的 VTX 内核'),
      h('p', null, '从你信任的本地 Framivra wheel 安装到独立环境，不覆盖已有安装，不联网、不安装系统依赖。'),
      h('label', { htmlFor: 'framivra-wheel' }, '安装包绝对路径'),
      h('input', { id: 'framivra-wheel', type: 'text', value: wheel, placeholder: '/绝对路径/framivra-版本-py3-none-any.whl', disabled: unavailable || !!running,
        onChange: event => { setWheel(event.target.value); setPreview(null); setConsent(false); setError('') } }),
      h('div', { className: 'fv-settings-actions' },
        action(busy === 'preview' ? '读取中…' : '查看安装计划', () => run('preview', { op: 'install-preview', wheelPath: wheel }, value => { setPreview(value); setConsent(false) }), unavailable || !!running || !wheel),
        action('查询安装状态', () => run('status', { op: 'install-status', ...(job?.jobId ? { jobId: job.jobId } : {}) }, value => {
          const next = value.jobs.at(-1); setJob(next ?? null); if (!next) setError('本次宿主运行中没有安装任务。')
        }), unavailable)),
      preview ? h('section', { className: 'fv-settings-well', 'aria-label': '安装确认' },
        h('h3', null, '确认安装计划'),
        h('dl', null, h('dt', null, '安装包'), h('dd', null, pathText(preview.wheelPath)),
          h('dt', null, 'SHA-256'), h('dd', null, pathText(preview.hash)),
          h('dt', null, 'Python'), h('dd', null, pathText(preview.pythonPath)),
          h('dt', null, '独立目录'), h('dd', null, pathText(preview.targetDir))),
        h('p', null, '哈希用于核对文件未变，不是官方签名。安装会运行包内代码，请只安装可信来源。'),
        h('label', { className: 'fv-settings-check' }, h('input', { type: 'checkbox', checked: consent, disabled: unavailable || !!running, onChange: event => setConsent(event.target.checked) }), '我信任此安装包，允许创建上述独立环境'),
        action(busy === 'install' ? '提交中…' : '确认安装', () => run('install', { op: 'install-confirm', nonce: preview.nonce, hash: preview.hash }, value => {
          setJob({ jobId: value.jobId, state: 'queued', targetDir: preview.targetDir, logs: '' }); setPreview(null); setConsent(false)
        }), unavailable || !!running || !consent)) : null,
      job ? h('section', { className: 'fv-settings-well', 'aria-label': '内核安装状态' },
        h('h3', null, ({ queued: '等待安装', running: '正在安装', succeeded: '安装完成', failed: '安装未完成', cancelled: '安装已取消' })[job.state] ?? '安装状态未知'),
        job.targetDir ? pathText(job.targetDir) : null,
        running ? message('离开设置页不会取消安装。需要停止时请点击取消，不要重复提交。') : null,
        job.error ? message(typeof job.error === 'string' ? job.error : job.error.message, true) : null,
        job.state === 'succeeded' && job.vtxPath ? h(React.Fragment, null, message('尚未切换当前内核。填入路径后，请保存并检查连接。'),
          action('使用这个内核', () => onSelect(job.vtxPath), unavailable)) : null,
        running ? action('取消安装', () => run('cancel', { op: 'install-status', jobId: job.jobId, cancel: true }, data => { const next = data.jobs.find(item => item.jobId === job.jobId); if (next) setJob(next) }), unavailable) : null,
        job.logs ? h('details', null, h('summary', null, '安装日志'), h('pre', null, job.logs)) : null) : null),
    message(error, true))
}

function fieldValue(value, path) {
  let current = value
  for (const key of path.split('.')) {
    if (!current || typeof current !== 'object' || !Object.hasOwn(current, key)) return undefined
    current = current[key]
  }
  return current
}
function ExactNumberInput({ id, value, disabled, onCommit, onPending }) {
  const shown = value === undefined || value === null ? '' : JSON.isRawJSON(value) ? value.rawJSON : String(value)
  const [draft, setDraft] = React.useState(shown)
  const [error, setError] = React.useState('')
  React.useEffect(() => { setDraft(shown); setError('') }, [shown])
  return h(React.Fragment, null,
    h('input', { id, type: 'text', inputMode: 'decimal', value: draft, disabled,
      placeholder: value === undefined ? '未设置（沿用默认）' : value === null ? 'null（沿用默认）' : '',
      'aria-invalid': error ? 'true' : undefined, 'aria-describedby': `${id}-help`,
      onChange: event => { setDraft(event.target.value); setError(''); onPending(true) },
      onBlur: () => {
        try {
          const next = draft === '' ? undefined : parseJSON(draft)
          if (next !== undefined && typeof next !== 'number' && !JSON.isRawJSON(next)) throw new Error('not numeric')
          onCommit(next); onPending(false); setError('')
        } catch { setError('请填写合法 JSON 数字，或清空以使用默认值。'); onPending(true) }
      } }), error ? message(error, true) : null)
}
function Diagnostics({ validation }) {
  if (!validation) return null
  return h('div', null,
    message(validation.ok ? '配置结构检查通过。未执行外部程序、网络请求或渲染。' : '配置结构未通过，请修正后再保存。', !validation.ok),
    ...[['errors', '需修正'], ['warnings', '提示']].map(([key, title]) => validation[key]?.length ? h('details', { key, open: key === 'errors' },
      h('summary', null, `${title}（${validation[key].length}）`), h('ul', null, validation[key].map((item, index) => h('li', { key: index }, `${item.path || '配置'}：${item.message}`)))) : null))
}

/** Full-document editor, never a timeline editor. Original JSON stays untouched
 * until the user edits; convenience fields preserve unknown values losslessly. */
export function KernelConfigEditor({ revision, configFile, writable, connectionDirty, onSuggestedPath }) {
  const [baseline, setBaseline] = React.useState(null)
  const [text, setText] = React.useState('')
  const [mode, setMode] = React.useState('fields')
  const [group, setGroup] = React.useState('cache')
  const [validation, setValidation] = React.useState(null)
  const [review, setReview] = React.useState(false)
  const [confirmation, setConfirmation] = React.useState(false)
  const [busy, setBusy] = React.useState('')
  const [notice, setNotice] = React.useState('')
  const [error, setError] = React.useState('')
  const [suggested, setSuggested] = React.useState('')
  const [pendingNumbers, setPendingNumbers] = React.useState({})
  const [unknownSave, setUnknownSave] = React.useState(false)
  const [savedNeedsReload, setSavedNeedsReload] = React.useState(false)
  const [fieldReset, setFieldReset] = React.useState(0)
  const task = React.useRef(null)
  const alive = React.useRef(true)
  React.useEffect(() => { alive.current = true; return () => { alive.current = false; task.current?.abort() } }, [])
  // Keep a dirty document across configuration changes. It is visibly stale and
  // unsavable, not silently rebased onto another file or revision.
  React.useEffect(() => { task.current?.abort(); setBusy(''); setReview(false); setConfirmation(false) }, [revision, configFile])
  const stale = baseline && (baseline.revision !== revision || baseline.selectedPath !== configFile)
  const dirty = baseline && (text !== baseline.text || Object.values(pendingNumbers).some(Boolean))
  const disabled = !!busy || connectionDirty || !writable || !!stale || unknownSave
  const numericPending = Object.values(pendingNumbers).some(Boolean)
  const parsed = React.useMemo(() => baseline ? checkKernelConfig(text) : null, [baseline, text])
  const update = next => { setText(next); setValidation(null); setReview(false); setConfirmation(false); setNotice(''); setError('') }
  const run = async (kind, payload, apply) => {
    if (task.current || connectionDirty || !writable) return
    const controller = new AbortController(); task.current = controller; setBusy(kind); setError(''); setNotice('')
    try {
      const value = await kernelRequest(payload, AbortSignal.any([controller.signal, AbortSignal.timeout(45000)]))
      if (alive.current && !controller.signal.aborted) apply(value)
    } catch (e) { if (alive.current && !controller.signal.aborted) {
      setError(e.message); if (e.suggestedConfigFile) setSuggested(e.suggestedConfigFile)
      if (kind === 'save') { setUnknownSave(true); setSavedNeedsReload(false); setReview(false); setConfirmation(false) }
    } }
    finally { if (task.current === controller) task.current = null; if (alive.current) setBusy('') }
  }
  const read = () => run('read', { op: 'config-read' }, value => {
    setBaseline({ ...value, revision, selectedPath: configFile }); setText(value.text); setValidation(null); setReview(false); setConfirmation(false); setPendingNumbers({}); setUnknownSave(false); setSavedNeedsReload(false); setFieldReset(value => value + 1)
    setNotice(value.exists === false ? '配置文件尚不存在。保存时创建；未设置的字段继续使用内核默认值。' : '已读取配置。修改仅保留在草稿，确认后才写入文件。')
  })
  const editField = (field, value) => {
    const result = updateKernelConfigField(text, field.path, value)
    if (result.text !== text || result.ok) { update(result.text); if (!result.ok) setValidation(result) }
    else { setValidation(result); setError('此字段无法安全修改，请在完整 JSON 中修正。') }
  }
  return h('section', { 'aria-label': 'VTX 配置文件编辑器' },
    h('div', { className: 'fv-settings-row' }, h('div', null, h('h3', null, '配置内容'),
      h('p', { className: 'fv-settings-muted' }, '常用字段按组调整；完整 JSON 可编辑全部字段。未设置不等于关闭。')),
      action(busy === 'read' ? '读取中…' : baseline ? '重新读取配置' : '读取配置文件', read, !!busy || connectionDirty || !writable || !!dirty)),
    connectionDirty ? message('连接设置尚未保存，请先保存配置路径。') : null,
    dirty ? message('有未保存的配置草稿。读取另一版本前，请先保留或放弃当前修改。') : null,
    stale ? message('宿主配置已变更，当前文档已过期；草稿保留。请放弃修改后重新读取，不会覆盖新版本。', true) : null,
    !configFile ? h('p', null, '未指定配置文件时，内核沿用自身默认配置；此处不会猜测或覆盖安装目录中的文件。') : pathText(configFile),
    suggested && !configFile ? action('填入推荐配置路径', () => onSuggestedPath(suggested), !!busy || !writable) : null,
    message(notice), message(error, true),
    numericPending ? message('数字输入尚未确认。请离开输入框完成检查，修正错误后再保存。') : null,
    unknownSave ? h('div', null, message(savedNeedsReload ? '文件已保存，但最新目标状态未确认。请重新读取后继续编辑，不要重复保存。' : '本次保存未获得完整成功回执，不能直接重试。草稿保留；读取最新文件并核对后，再决定是否保存。', !savedNeedsReload),
      action('读取最新版本并比较', () => run('reconcile', { op: 'config-read' }, value => {
        setBaseline({ ...value, revision, selectedPath: configFile }); setUnknownSave(false); setSavedNeedsReload(false); setReview(true); setConfirmation(false)
        setNotice(value.text === text ? '最新文件与当前草稿一致，无需重复保存。' : '已读取最新文件。下方比较保留你的草稿；没有自动保存。')
      }), !!busy || connectionDirty || !writable)) : null,
    baseline ? h(React.Fragment, null,
      h('div', { className: 'fv-settings-actions', 'aria-label': '配置编辑方式' },
        action('常用字段', () => setMode('fields'), numericPending, { 'aria-pressed': mode === 'fields' }),
        action('完整 JSON', () => setMode('json'), numericPending, { 'aria-pressed': mode === 'json' })),
      mode === 'fields' ? h('div', null,
        h('label', { htmlFor: 'framivra-config-group' }, '配置分组'),
        h('select', { id: 'framivra-config-group', value: group, disabled: numericPending, onChange: event => setGroup(event.target.value) }, KERNEL_CONFIG_GROUPS.map(item => h('option', { key: item.id, value: item.id }, item.label))),
        h('p', { className: 'fv-settings-muted' }, '字段修改会重新排版 JSON，但保留未知字段与数值。扩展注册等复杂内容请使用完整 JSON。'),
        !parsed?.value ? message('JSON 暂时无法解析，请切换完整 JSON 修正。', true) : h('div', { className: 'fv-settings-fields' }, KERNEL_CONFIG_FIELDS.filter(field => field.group === group).map(field => {
          const value = fieldValue(parsed.value, field.path)
          const id = `fv-kernel-${field.path.replaceAll('.', '-')}`
          const simple = ['string', 'boolean', 'number'].includes(field.type)
          const isRaw = value !== null && typeof value === 'object'
          const renderedValue = value === undefined ? '' : value === null ? '' : isRaw ? '' : String(value)
          const invalidParent = parsed.errors.some(item => item.path === field.group)
          const invalidEnum = field.enum && (invalidParent || (value !== undefined && !field.enum.includes(value)))
          return h('div', { className: 'fv-settings-field', key: field.path },
            h('label', { htmlFor: id }, field.label),
            h('small', { id: `${id}-help` }, field.description),
            field.enum ? h(React.Fragment, null,
              h('select', { id, value: invalidEnum ? '' : value ?? 'core', disabled: disabled || invalidParent, 'aria-describedby': `${id}-help`,
                'aria-invalid': invalidEnum ? 'true' : undefined,
                onChange: event => editField(field, event.target.value) },
                invalidEnum ? h('option', { value: '', disabled: true }, invalidParent ? '缓存分组无效，请在完整 JSON 中修正' : '当前值无效，请选择缓存档位') : null,
                h('option', { value: 'off' }, 'off · 关闭复用'), h('option', { value: 'core' }, 'core · 核心缓存（推荐 / 默认）'), h('option', { value: 'aggressive' }, 'aggressive · 积极缓存')),
              value === undefined && !invalidParent ? h('small', null, '尚未显式设置：当前沿用 core（推荐）。仅显示默认档位，不会自动写入配置。') : null) : field.type === 'boolean' ? h('select', { id, value: value === undefined ? 'unset' : value === null ? 'null' : String(value), disabled, 'aria-describedby': `${id}-help`,
              onChange: event => editField(field, event.target.value === 'unset' ? undefined : event.target.value === 'null' ? null : event.target.value === 'true') },
              h('option', { value: 'unset' }, '未设置（沿用默认）'), h('option', { value: 'true' }, '是'), h('option', { value: 'false' }, '否'), value === null ? h('option', { value: 'null' }, 'null（沿用默认）') : null,
              typeof value === 'string' ? h('option', { value }, `旧式值：${value}`) : null) : field.type === 'number' ? h(ExactNumberInput, { key: `${field.path}-${fieldReset}`, id, value, disabled,
                onCommit: next => editField(field, next), onPending: pending => setPendingNumbers(previous => ({ ...previous, [field.path]: pending })) }) : simple && !isRaw ? h('input', { id, type: 'text', value: renderedValue,
                placeholder: value === undefined ? '未设置（沿用默认）' : value === null ? 'null（沿用默认）' : '', disabled, 'aria-describedby': `${id}-help`,
                onChange: event => editField(field, event.target.value) }) : action('在完整 JSON 中编辑', () => setMode('json'), numericPending),
            value !== undefined ? action('使用默认值', () => editField(field, undefined), disabled, { 'aria-label': `${field.label}：使用默认值` }) : null)
        }))) : h(React.Fragment, null,
        h('label', { htmlFor: 'framivra-config-json' }, '完整配置 JSON'),
        h('textarea', { id: 'framivra-config-json', value: text, readOnly: disabled, spellCheck: false, rows: 16, onChange: event => update(event.target.value), 'aria-describedby': 'fv-json-hint' }),
        h('small', { id: 'fv-json-hint' }, '仅管理员可见；不要在配置中存放 API 密钥。原文读取不会自动格式化。')),
      h(Diagnostics, { validation }),
      h('div', { className: 'fv-settings-actions' },
        action(busy === 'check' ? '检查中…' : '检查配置内容', () => run('check', { op: 'config-check', text }, result => setValidation(result.validation)), disabled || numericPending),
        action('查看修改并保存', () => { const checked = checkKernelConfig(text); setValidation(checked); if (checked.ok) { setReview(true); setConfirmation(false) } }, disabled || !dirty || numericPending),
        dirty || stale ? action('放弃配置草稿', () => { setText(baseline.text); setPendingNumbers({}); setFieldReset(value => value + 1); setValidation(null); setReview(false); setConfirmation(false); setNotice('已放弃本地草稿；此操作不会改动磁盘文件。'); setError('') }, !!busy || unknownSave) : null),
      review ? h('section', { className: 'fv-settings-well', 'aria-label': '配置保存确认' },
        h('h3', null, '确认配置变更'), pathText(baseline.path),
        h('p', null, '先备份原文件，再校验版本并保存。此为全局配置，缓存档位影响使用同一 VTX_CONFIG 的所有工程与调用者，不限于当前时间线或会话。启用外部应用或插件表示信任其代码；保存后需重新打开工程。'),
        h('div', { className: 'fv-settings-diff' }, h('div', null, h('h4', null, '保存前'), h('pre', null, baseline.text)), h('div', null, h('h4', null, '保存后'), h('pre', null, text))),
        h('label', { className: 'fv-settings-check' }, h('input', { type: 'checkbox', checked: confirmation, disabled, onChange: event => setConfirmation(event.target.checked) }), '我已核对修改，确认保存此配置文件'),
        action(busy === 'save' ? '保存中…' : '确认保存文件', () => {
          if (disabled || !confirmation || !review || !dirty) return
          run('save', { op: 'config-save', text, expectedHash: baseline.hash, selectionToken: baseline.selectionToken, confirmed: true }, result => {
            setBaseline({ ...baseline, text, hash: result.hash, selectionToken: result.selectionToken, revision }); setReview(false); setConfirmation(false)
            const needsReload = result.needsReload || !result.selectionToken
            setUnknownSave(needsReload); setSavedNeedsReload(needsReload)
            setNotice(`配置文件已保存。${result.backupPath ? `备份：${result.backupPath}。` : ''}请重新打开工程，使新配置生效。`)
          })
        }, disabled || !confirmation), action('返回编辑', () => { setReview(false); setConfirmation(false) }, !!busy)) : null) : null)
}
