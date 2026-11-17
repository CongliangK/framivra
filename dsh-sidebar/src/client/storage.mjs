import React from 'react'
import { terminalGrantFailure } from './grant-lifecycle.mjs'

const h = React.createElement
export const STORAGE_REFRESH_MS = 10000
const CONFIRM = 'stopped-writers-and-cleanup'
const operations = ['storage-inspect', 'storage-plan', 'storage-clean']
export function storageSupported(opened) {
  const capability = opened?.capabilities?.capabilities?.storage
  return capability?.schema === 'framivra.storage/v1' && capability.request_stdin === true &&
    ['inspect', 'plan', 'clean'].every(op => capability.operations?.includes(op)) &&
    operations.every(op => opened?.host?.ops?.includes(op))
}
export function storageBytes(bytes) {
  if (!Number.isSafeInteger(bytes) || bytes < 0) return '—'
  if (bytes < 1024) return `${bytes} B`
  const units = ['KiB', 'MiB', 'GiB', 'TiB', 'PiB']
  let value = bytes / 1024, index = 0
  while (value >= 1024 && index < units.length - 1) { value /= 1024; index++ }
  return `${value.toFixed(1)} ${units[index]}`
}
const diagnostics = value => value?.diagnostics?.map(item => item.message ?? item.code).filter(Boolean).join('；')
const button = (label, onClick, disabled) => h('button', { type: 'button', onClick, disabled }, label)

/** Visible-only sampling. Cleanup is a separate explicit, single-use consent;
 * no UI path chooses files or interprets cache keys. */
export function StoragePanel({ sessionId, opened, tabSignal, call, active = true, disabled = false, onBusyChange, onGrantDead }) {
  const [expanded, setExpanded] = React.useState(true)
  const [visible, setVisible] = React.useState(() => globalThis.document?.visibilityState !== 'hidden')
  const [measurement, setMeasurement] = React.useState(null)
  const [plan, setPlan] = React.useState(null)
  const [confirmed, setConfirmed] = React.useState(false)
  const [busy, setBusy] = React.useState('')
  const [error, setError] = React.useState('')
  const [notice, setNotice] = React.useState('')
  const [outcomeWarning, setOutcomeWarning] = React.useState('')
  const [dead, setDead] = React.useState(false)
  const mounted = React.useRef(true), task = React.useRef(null)
  const latest = React.useRef({ disabled, onBusyChange, onGrantDead })
  latest.current = { disabled, onBusyChange, onGrantDead }
  const supported = storageSupported(opened)
  const version = opened.snapshot.content_version
  const request = async (op, extra = {}) => {
    if (task.current || dead || !supported || tabSignal.aborted) return
    if (op !== 'storage-inspect' && latest.current.disabled) return
    const controller = new AbortController()
    const running = { controller, op }; task.current = running
    setBusy(op); setError('')
    if (op !== 'storage-inspect') latest.current.onBusyChange?.(true)
    const signal = AbortSignal.any([controller.signal, tabSignal])
    try {
      const value = await call(sessionId, { op, grant: opened.grant, ...extra }, signal)
      if (!mounted.current || signal.aborted) return
      if (value.content_version && value.content_version !== version) throw new Error('STORAGE_STALE: 时间线已变化，请刷新保存版本。')
      if (op === 'storage-clean') {
        const removed = value.removed
        setMeasurement(null)
        if (value.ok && removed) {
          setNotice(`已回收 ${removed.count} 项（${storageBytes(removed.bytes)}）。请刷新空间统计；素材、成片和锁文件不会作为本次清理目标。`)
        } else {
          setOutcomeWarning(value.outcome_unknown ? '清理结果未确认。部分文件可能已删除；请先刷新空间、检查项目，再重新生成计划，不要重复提交。' : `清理未全部完成：${diagnostics(value) || '请检查项目后重新生成计划。'}${removed ? ` 已回收 ${removed.count} 项（${storageBytes(removed.bytes)}）。` : ''}`)
        }
      } else {
        if (!value.ok) throw new Error(diagnostics(value) || '空间操作被内核拒绝。')
        if (op === 'storage-inspect') setMeasurement({ ...value, sampledAt: new Date().toLocaleTimeString() })
        else {
          if (!value.planToken || !value.plan || !Number.isSafeInteger(value.expires_in_ms) || value.expires_in_ms <= 0) throw new Error('清理预演缺少确认凭证，请检查内核与插件版本。')
          setPlan({ ...value, deadline: Date.now() + value.expires_in_ms })
          setConfirmed(false); setNotice(''); setOutcomeWarning('')
        }
      }
    } catch (cause) {
      if (!mounted.current) return
      const code = terminalGrantFailure(cause.message)
      if (code) { setDead(true); latest.current.onGrantDead?.(code, opened.grant) }
      if (op === 'storage-clean') setOutcomeWarning(`清理结果未确认，可能已部分执行。请核查空间后重新预演，不要直接重试。${cause.message ?? ''}`)
      else setError(cause.message ?? '读取失败，未执行清理。')
    } finally {
      if (task.current === running) task.current = null
      if (mounted.current) {
        if (op !== 'storage-inspect') latest.current.onBusyChange?.(false)
        setBusy('')
      }
    }
  }
  // Keep timer callbacks current without restarting a poll on every render.
  const inspect = React.useRef()
  inspect.current = () => request('storage-inspect')
  React.useEffect(() => {
    mounted.current = true
    const observe = () => setVisible(document.visibilityState !== 'hidden')
    document.addEventListener('visibilitychange', observe)
    return () => {
      mounted.current = false; task.current?.controller.abort()
      document.removeEventListener('visibilitychange', observe)
      latest.current.onBusyChange?.(false)
    }
  }, [])
  React.useEffect(() => {
    if (!active || !expanded || !visible || !supported || dead || plan || tabSignal.aborted) return
    let stopped = false, timer
    const sample = async () => {
      await inspect.current()
      if (!stopped) timer = setTimeout(sample, STORAGE_REFRESH_MS)
    }
    sample()
    return () => {
      stopped = true; clearTimeout(timer)
      if (task.current?.op === 'storage-inspect') task.current.controller.abort()
    }
  }, [active, expanded, visible, supported, dead, !!plan, tabSignal])
  React.useEffect(() => {
    if (!plan) return
    const timer = setTimeout(() => { setPlan(null); setConfirmed(false); setNotice('清理预演已过期，请重新生成；未执行删除。') }, Math.max(0, plan.deadline - Date.now()))
    return () => clearTimeout(timer)
  }, [plan])
  const clean = () => {
    if (!plan || !confirmed || Date.now() >= plan.deadline || busy || disabled) return
    const planToken = plan.planToken
    // Consume before the request. A transport failure never enables a retry of
    // the old token; only a new explicit preview can authorize another attempt.
    setPlan(null); setConfirmed(false); setNotice('')
    void request('storage-clean', { planToken, confirm: CONFIRM })
  }
  const usage = measurement?.usage
  return h('details', { className: 'fv-inspector-section fv-storage', open: expanded, onToggle: event => setExpanded(event.currentTarget.open) },
    h('summary', null, '空间与缓存'),
    h('style', null, '.fv-storage dl{display:grid;grid-template-columns:1fr auto;gap:6px;margin:8px 0}.fv-storage dd{margin:0;font-variant-numeric:tabular-nums}.fv-storage p,.fv-storage li{overflow-wrap:anywhere}.fv-storage-actions{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}.fv-storage-list{max-height:200px;overflow:auto;padding-left:18px}.fv-storage label{display:flex;gap:6px;align-items:flex-start}.fv-storage small{display:block;margin:8px 0}') ,
    !supported ? h('p', { role: 'status' }, '当前内核或插件尚不支持空间管理，请更新后重新打开时间线；预览与编辑仍可使用。') : h(React.Fragment, null,
      h('p', null, '统计当前时间线所在项目目录；面板可见时每 10 秒刷新，不跟随符号链接，不扫描外部素材。'),
      usage ? h('dl', { 'aria-label': '项目空间统计' },
        h('dt', null, '项目合计'), h('dd', null, storageBytes(usage.project_bytes)),
        h('dt', null, '缓存 cache'), h('dd', null, storageBytes(usage.cache_bytes)),
        h('dt', null, '输出 out'), h('dd', null, storageBytes(usage.out_bytes)),
        h('dt', null, '素材及其他'), h('dd', null, storageBytes(usage.other_bytes))) : h('p', null, busy === 'storage-inspect' ? '正在扫描项目空间…' : '尚无当前空间统计。'),
      measurement ? h('small', null, `${error ? '上次成功采样' : '采样时间'} ${measurement.sampledAt} · 逻辑字节，非独占物理占用；跳过 ${usage.symlinks_skipped} 个链接。`) : null,
      h('p', null, `当前缓存档位：${measurement?.cache_mode ?? '待读取'}。切换请打开“插件 → Framivra → 配置文件”的“缓存档位”。`),
      h('small', null, 'off 关闭复用，core 保留核心缓存，aggressive 保留更多缓存；全局配置下次操作生效，切换不会立即删除旧缓存。'),
      h('div', { className: 'fv-storage-actions' },
        button('刷新空间', () => { setNotice(''); void request('storage-inspect') }, !!busy || dead),
        button(plan ? '重新生成清理预演' : '预演缓存清理', () => { setPlan(null); setConfirmed(false); void request('storage-plan') }, !!busy || disabled || dead)),
      plan ? h('div', null,
        h('p', null, `可回收 ${plan.plan.count} 项，共 ${storageBytes(plan.plan.bytes)}；仅按当前内核普通 GC 规则，非清空整个缓存。`),
        h('pre', { className: 'fv-storage-list', 'aria-label': '清理候选清单', style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' } }, plan.plan.entries.map(entry => `${entry.path} · ${storageBytes(entry.bytes)} · ${entry.reason}`).join('\n')),
        h('label', null, h('input', { type: 'checkbox', checked: confirmed, disabled: !!busy || disabled, onChange: event => setConfirmed(event.target.checked) }), '我已停止本项目所有渲染、预览及其他写入，确认删除上述候选。'),
        h('small', null, '清理不可撤销；确认有效期 60 秒。时间线或候选变化会拒绝执行，回收后重建仍需原素材及依赖。'),
        h('div', { className: 'fv-storage-actions' }, button('确认清理', clean, !confirmed || !!busy || disabled || plan.plan.count === 0), button('取消预演', () => { setPlan(null); setConfirmed(false) }, !!busy))) : null,
      busy && busy !== 'storage-inspect' ? h('p', { role: 'status' }, busy === 'storage-plan' ? '正在生成清理预演…' : '正在清理；不要修改或渲染此项目…') : null,
      notice ? h('p', { role: 'status' }, notice) : null,
      outcomeWarning ? h('p', { role: 'alert' }, outcomeWarning) : null,
      error ? h('p', { role: 'alert' }, error) : null))
}
