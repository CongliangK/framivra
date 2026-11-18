import React, { useRef, useState } from 'react'
import { parseJSON, stringifyJSON } from '../json.mjs'
const h = React.createElement
const own = (object, key) => Object.prototype.hasOwnProperty.call(object ?? {}, key)
export const savedSetting = (object, key) => {
  if (!own(object, key)) return '未设置'
  const value = object[key]
  if (value === null) return '空值'
  if (value === false || value === true) return value ? '已启用' : '已关闭'
  if (Array.isArray(value)) return `${value.length} 项`
  if (JSON.isRawJSON?.(value)) return stringifyJSON(value)
  if (typeof value === 'object') return '已配置'
  return String(value)
}

/** A deliberately closed UI surface; no root target, arbitrary key or document replacement. */
export function settingsIntent(kind, { text, remove = false, id } = {}) {
  if (kind === 'duration_frames') {
    if (remove || typeof text !== 'string' || !/^[1-9][0-9]*$/.test(text) || !Number.isSafeInteger(Number(text))) throw new Error('时长需要正整数帧（不接受舍入、小数或指数）。')
    return { op: 'project.resize', duration_frames: Number(text) }
  }
  if (!['gfx_dependency_policy', 'cache_dependencies', 'denoise', 'bgm', 'duck_strategy'].includes(kind)) throw new Error('不支持的配置字段。')
  if (['denoise', 'bgm', 'duck_strategy'].includes(kind) && (typeof id !== 'string' || !id)) throw new Error('请先选择受管素材或音轨。')
  if (kind === 'duck_strategy') {
    return { op: 'track.references', id, set: remove ? {} : { duck_strategy: jsonValue(text) }, ...(remove ? { unset: ['duck_strategy'] } : {}) }
  }
  const target = ['denoise', 'bgm'].includes(kind) ? { id } : { target: 'project' }
  if (remove) return { op: 'parameters.unset', ...target, path: [kind] }
  let value
  if (kind === 'gfx_dependency_policy') {
    if (!['legacy', 'strict'].includes(text)) throw new Error('请明确选择 legacy 或 strict。')
    value = text
  } else {
    value = jsonValue(text)
    if (kind === 'cache_dependencies' && (!Array.isArray(value) || value.some(path => typeof path !== 'string'))) throw new Error('依赖必须是路径字符串列表。')
  }
  return { op: 'parameters.update', ...target, path: [kind], value }
}
function jsonValue(text) {
  if (typeof text !== 'string' || new TextEncoder().encode(text).byteLength > 128 * 1024) throw new Error('单项 JSON 值上限 128 KiB。')
  return parseJSON(text)
}
const inputStyle = { display: 'block', width: '100%', boxSizing: 'border-box' }
function SettingRow({ kind, title, object, id, disabled, onChange, onQueue, draft, onDraft }) {
  const saved = own(object, kind) ? object[kind] : undefined
  const initial = kind === 'gfx_dependency_policy' ? (typeof saved === 'string' ? saved : '') : saved === undefined ? '' : stringifyJSON(saved)
  const [text, setText] = useState(draft?.text ?? initial), [error, setError] = useState('')
  const [paths, setPaths] = useState(draft?.paths ?? (Array.isArray(saved) && saved.every(item => typeof item === 'string') ? saved : []))
  const change = value => { onChange(); onDraft({ text: value, paths }); setText(value); setError('') }
  const queue = remove => {
    if (disabled) return
    try { onQueue([settingsIntent(kind, { text: kind === 'cache_dependencies' ? stringifyJSON(paths) : text, id, remove })]); setError('') } catch (error) { setError(error.message) }
  }
  const pathChange = next => { onChange(); onDraft({ text, paths: next }); setPaths(next); setError('') }
  return h('section', { 'aria-label': title, style: { marginBlock: 8, minWidth: 0 } },
    h('strong', null, title),
    h('div', { style: { overflowWrap: 'anywhere' } }, `已保存：${savedSetting(object, kind)}`),
    h('fieldset', { disabled, style: { minWidth: 0 } },
      kind === 'gfx_dependency_policy'
        ? h('label', null, '依赖策略', h('select', { 'aria-label': title, value: text, onChange: e => change(e.target.value), style: inputStyle }, h('option', { value: '' }, '请选择（不自动填默认值）'), ...['legacy', 'strict'].map(value => h('option', { key: value, value }, value))))
        : kind === 'cache_dependencies'
          ? h('div', null, paths.map((path, index) => h('div', { key: index }, h('input', { 'aria-label': `依赖路径 ${index + 1}`, value: path, onChange: e => pathChange(paths.map((old, i) => i === index ? e.target.value : old)), style: inputStyle }), h('button', { type: 'button', onClick: () => pathChange(paths.filter((_, i) => i !== index)) }, '移除此路径'))), h('button', { type: 'button', onClick: () => pathChange(paths.concat('')) }, '添加依赖路径'))
          : h('label', null, kind === 'duration_frames' ? '新时长（整数帧）' : '完整 JSON 值', h(kind === 'duration_frames' ? 'input' : 'textarea', { 'aria-label': title, value: text, inputMode: kind === 'duration_frames' ? 'numeric' : undefined, rows: 3, onChange: e => change(e.target.value), style: inputStyle })),
      h('button', { type: 'button', onClick: () => queue(false) }, `加入批次：${title}`),
      kind !== 'duration_frames' ? h('button', { type: 'button', onClick: () => queue(true) }, `移除字段：${title}`) : null),
    error ? h('p', { role: 'alert' }, error) : null)
}

/** Saved values stay readable without write permission; callbacks only stage ManualEditor intents. */
export function TimelineSettings({ document, tracks = [], disabled, onChange, onQueue }) {
  const [sourceId, setSourceId] = useState(''), [trackId, setTrackId] = useState('')
  // Lazily retain only edited rows, not a mounted editor for every source/track.
  // ManualEditor's saved-version/clear epoch key owns the cache lifetime.
  const drafts = useRef(new Map())
  const sources = (document.sources ?? []).filter(source => typeof source.id === 'string')
  const audio = tracks.filter(track => track.family === 'audio' && typeof track.data?.id === 'string')
  const source = sources.find(source => source.id === sourceId), track = audio.find(track => track.data.id === trackId)
  const project = document.project ?? {}
  const row = (kind, title, object, id) => {
    const key = `${kind}:${id ?? 'project'}`
    return h(SettingRow, { key, kind, title, object, id, disabled, onChange, onQueue, draft: drafts.current.get(key), onDraft: draft => drafts.current.set(key, draft) })
  }
  return h('section', { 'aria-label': '时间线配置', className: 'fv-data' },
    h('h3', null, '时间线配置'),
    h('p', { 'aria-label': '已保存规格', style: { overflowWrap: 'anywhere' } }, `已保存 · FPS ${savedSetting(project, 'fps')} · 尺寸 ${savedSetting(project, 'width')} × ${savedSetting(project, 'height')} · ${savedSetting(project, 'duration_frames')} 帧`),
    h('details', null, h('summary', null, '时长与缓存依赖'),
      row('duration_frames', '工程时长', project), row('gfx_dependency_policy', '图形依赖策略', project), row('cache_dependencies', '缓存依赖', project)),
    h('details', null, h('summary', null, '插件声明与音轨策略'),
      h('p', { style: { overflowWrap: 'anywhere' } }, `Blender 桥接：${savedSetting(document, 'blender_bridge')}`),
      h('details', null, h('summary', null, '已保存素材 / 音轨声明（只读）'),
        sources.map(source => h('p', { key: source.id, style: { overflowWrap: 'anywhere' } }, `${source.id} · 降噪：${savedSetting(source, 'denoise')} · 音乐源：${savedSetting(source, 'bgm')}`)),
        audio.map(track => h('p', { key: track.data.id, style: { overflowWrap: 'anywhere' } }, `${track.data.name ?? track.data.id} · 闪避：${savedSetting(track.data, 'duck_strategy')}`))),
      h('label', null, '配置素材', h('select', { 'aria-label': '配置素材', disabled, value: sourceId, style: inputStyle, onChange: e => { onChange(); setSourceId(e.target.value) } }, h('option', { value: '' }, '请选择素材'), sources.map(source => h('option', { key: source.id, value: source.id }, `${source.id} · ${source.path}`)))),
      source ? [row('denoise', '素材降噪', source, source.id), row('bgm', '音乐源', source, source.id)] : null,
      h('label', null, '配置音轨', h('select', { 'aria-label': '配置音轨', disabled, value: trackId, style: inputStyle, onChange: e => { onChange(); setTrackId(e.target.value) } }, h('option', { value: '' }, '请选择音轨'), audio.map(track => h('option', { key: track.data.id, value: track.data.id }, track.data.name ?? track.data.id)))),
      track ? row('duck_strategy', '静态闪避', track.data, track.data.id) : null))
}
