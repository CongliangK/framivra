import React from 'react'
import { stringifyJSON } from '../json.mjs'
const h = React.createElement
export const operationNames = {
  'source.add': '添加素材', 'source.remove': '移除素材', 'source.update': '更新素材',
  'track.add': '新建轨道', 'track.remove': '删除轨道', 'track.copy': '复制轨道', 'track.reorder': '排列轨道', 'track.rename': '重命名轨道', 'track.references': '音轨引用',
  'block.insert': '插入片段', 'block.remove': '移除片段', 'block.copy': '复制片段', 'block.move': '移动片段', 'block.relink': '重新链接', 'block.trim': '裁剪片段', 'block.split': '分割片段',
  'parameters.update': '更新属性', 'parameters.unset': '恢复缺省属性', 'project.resize': '工程时长', 'range.ripple': '联动区间'
}
const propertyNames = { opacity: '不透明度', volume: '音量', pan: '声像', text: '字幕', transform: '变换', scale: '缩放', x: '横向位置', y: '纵向位置', fade_in_frames: '淡入帧数', fade_out_frames: '淡出帧数', cache_dependencies: '缓存依赖', gfx_dependency_policy: '图形依赖策略', denoise: '降噪', bgm: '音乐', duck: '压低音量', duck_params: '压低参数' }
const familyNames = { video: '视频轨', audio: '音轨', subtitles: '字幕轨' }
const range = value => Array.isArray(value) ? `${value[0]}–${value[1]} 帧` : ''
const labelFor = (labels, key) => Object.hasOwn(labels, key) ? labels[key] : key
// Describe values without presenting serialized requests or coercing preserved JSON numbers.
function valueLabel(value) {
  if (value == null) return value === null ? '空值' : ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (JSON.isRawJSON?.(value)) return stringifyJSON(value)
  if (Array.isArray(value)) return `${value.length} 项`
  return '复合值'
}
export function describeOperation(operation, { tracks = [], blocks = [], sources = [] } = {}) {
  const block = blocks.find(item => item.data?.id === operation.id)
  const ownTrack = block && tracks.find(item => item.family === block.family && item.track_index === block.track_index)
  const target = tracks.find(item => item.data?.id === (operation.track_id ?? operation.id)) ?? ownTrack
  const source = sources.find(item => item.id === operation.id)
  const trackName = operation.track_id === 'subtitles' || block?.family === 'subtitles' ? '字幕轨' : target?.data?.name ?? (target ? `${familyNames[target.family] ?? '轨道'} ${target.track_index + 1}` : operation.track_id)
  const subject = block?.data?.text || block?.data?.name || block?.data?.src || source?.path || operation.data?.name || operation.data?.path || (operation.id && !target ? operation.id : '')
  const details = [trackName, subject]
  if (operation.op === 'project.resize') details.push(`${operation.duration_frames} 帧`)
  if (operation.at !== undefined) details.push(`${block ? range(block.range) + ' → ' : ''}${operation.at} 帧`)
  else if (operation.start !== undefined) details.push(`${operation.start}–${operation.end} 帧`)
  else if (block) details.push(range(block.range))
  if (operation.op === 'block.insert' || operation.op === 'track.add') {
    const data = operation.op === 'block.insert' ? operation.data : operation.data?.clips?.[0] ?? operation.data?.events?.[0]
    if (data) details.push(data.dst_in !== undefined ? `${data.dst_in}–${data.dst_in + data.dur} 帧` : `${data.in}–${data.out} 帧`)
  }
  if (operation.op === 'track.rename' || operation.op === 'track.copy') details.push(operation.name)
  if (operation.op === 'track.reorder') details.push((operation.ids ?? []).map(id => tracks.find(item => item.data?.id === id)?.data?.name ?? id).join(' → '))
  if (operation.op === 'block.relink') details.push(`→ ${operation.src}`)
  if (operation.op === 'source.update') details.push(operation.set?.path ?? '更新变换')
  if (operation.path) details.push(operation.path.map(key => labelFor(propertyNames, key)).join(' / '), operation.op === 'parameters.update' ? valueLabel(operation.value) : '恢复缺省')
  if (operation.op === 'track.references') details.push(...Object.keys(operation.set ?? {}), ...(operation.unset ?? []).map(key => `移除 ${key}`))
  if (operation.op === 'range.ripple') details.push(operation.mode === 'shift' ? `偏移 ${operation.delta > 0 ? '+' : ''}${operation.delta} 帧` : '删除区间', ...(operation.scope?.track_ids ?? []).map(id => tracks.find(item => item.data?.id === id)?.data?.name ?? id), operation.scope?.subtitles ? '含字幕' : '')
  return { name: Object.hasOwn(operationNames, operation.op) ? operationNames[operation.op] : '扩展操作', detail: details.filter(value => value !== undefined && value !== '').join(' · ') }
}
const fieldNames = { ...propertyNames, id: '对象 ID', data: '新对象', set: '更新值', unset: '移除字段', path: '路径', target: '目标', track_id: '目标轨道 ID', family: '轨道类型', index: '插入位置', name: '名称', ids: '对象 ID 对照 / 轨序', new_id: '新对象 ID', src: '素材引用', src_in: '源起点帧', src_out: '源终点帧', dst_in: '起点帧', dur: '时长帧', in: '起点帧', out: '终点帧', at: '目标帧', start: '起点帧', end: '终点帧', duration_frames: '工程时长帧', fade_policy: '淡化策略', text_policy: '文本策略', texts: '分段文本', scope: '联动范围', track_ids: '轨道 ID', subtitles: '包含字幕', mode: '模式', crossing: '跨界策略', resize: '调整工程时长', delta: '偏移帧数', split_ids: '分割 ID 对照', cascade: '联动移除片段', clips: '视频片段', events: '音频片段', kind: '轨道类型', duck_by: '压低参考轨', duck_strategy: '压低策略', value: '属性值' }
const values = { 'preserve-content': '保留原曲线', 'reapply-edge-fades': '重新应用边缘淡化', 'duplicate-text': '两段复制文本', explicit: '分别指定文本', shift: '平移', delete: '删除区间', reject: '拒绝跨界', cut: '剪切', project: '工程', video: '视频', audio: '音频', subtitles: '字幕', gfx: '图形', aroll: '主视频', broll: '插入视频' }
function ReviewValue({ value, field }) {
  if (JSON.isRawJSON?.(value)) return h('span', null, stringifyJSON(value))
  if (value !== null && typeof value === 'object') {
    const entries = Object.entries(value)
    return entries.length ? h('dl', { className: 'fv-edit-values' }, entries.map(([key, child]) => h('div', { key }, h('dt', null, Array.isArray(value) ? `第 ${Number(key) + 1} 项` : labelFor(fieldNames, key)), h('dd', null, h(ReviewValue, { value: child, field: key }))))) : h('span', null, Array.isArray(value) ? '空列表' : '空对象')
  }
  return h('span', null, typeof value === 'boolean' ? value ? '是' : '否' : typeof value === 'string' ? ['family', 'kind', 'fade_policy', 'text_policy', 'mode', 'crossing', 'target'].includes(field) ? labelFor(values, value) : value : valueLabel(value))
}
export function OperationList({ operations = [], context, onRemove, disabled, label = '待保存操作' }) {
  return h('ol', { className: 'fv-edit-list', 'aria-label': label }, operations.map((operation, index) => {
    const description = describeOperation(operation, context)
    return h('li', { className: 'fv-edit-item', key: index },
      h('div', { className: 'fv-edit-item-body' }, h('strong', { className: 'fv-edit-name' }, description.name), h('span', { className: 'fv-edit-meta' }, description.detail),
        h('details', { className: 'fv-edit-operation-details' }, h('summary', null, '详细参数'), h(ReviewValue, { value: Object.fromEntries(Object.entries(operation).filter(([key]) => key !== 'op')) }))),
      onRemove ? h('button', { type: 'button', disabled, 'aria-label': `移除第 ${index + 1} 项：${description.name}`, onClick: () => onRemove(index) }, '移除') : null)
  }))
}
const errorActions = {
  conflict: '工程已变化，请刷新后重新预检。', STALE_BASE: '工程已变化，请刷新并重新提出修改。',
  PLAN_INVALID: '计划已失效，请重新预检。', PLAN_INVALIDATION_FAILED: '旧计划撤销未确认，请重开工程并授权。',
  WRITE_DENIED: '请先启用编辑。', WRITE_BUSY: '请等待当前操作结束。', STORAGE_BUSY: '存储维护中，请完成维护后再编辑。', FRAME_BUSY: '帧预览正在处理中，请稍后再编辑。',
  BAD_GRANT: '预览授权已失效，请重开工程。', CONFIG_CHANGED: '配置已变化，请重开工程。', PATH_CHANGED: '工程路径已变化，请重开工程。',
  recovery_required: '历史需要恢复，请先读取状态并显式恢复。', outcome_unknown: '结果未知，请保留原操作 ID，先读取状态 / 恢复，不要重试保存。',
  'edit.identity': '对象已失效，请刷新后重新选择。', 'edit.operation': '操作不适用，请检查目标、帧范围与策略。',
  'input.error': '输入不符合要求，请检查表单。', 'idempotency.conflict': '操作 ID 已被使用，请读取状态核对原操作。'
}
export function editError(error) {
  const message = String(error?.message ?? '')
  const fixes = [
    [/gfx continuous/, '图形片段暂不支持裁剪或分割，请改用移动、复制或移除。'],
    [/external subtitles/, '外部字幕只读，请选择内联字幕。'],
    [/cross-family|ordinary\/gfx conversion/, '不能跨轨道类型移动，请选择同类轨道。'],
    [/outward trim|segment must be inside/, '只能向内裁剪，请将边界放在原片段内。'],
    [/split must be strictly inside/, '请将分割帧放在片段内部。'],
    [/cut intersects a local fade/, '裁剪切入淡化区，请调整边界或选择重新应用淡化。'],
    [/cascade must explicitly/, '请选齐使用此素材的全部片段，再确认联动移除。'],
    [/ripple intersects|crossing policy rejects/, '区间穿过片段，请调整范围或明确选择剪切。'],
    [/split_ids must|spanning ripple/, '请选齐跨越区间两端的片段并提供分割确认。'],
    [/reorder requires the complete/, '轨道列表已变化，请重新采用完整轨序。'],
    [/track insertion index outside/, '轨道位置越界，请调整插入位置。'],
    [/cannot unset required/, '此属性为必填项，请修改值而不是移除。'],
    [/overlap/i, '此位置与同轨片段重叠，请移到空位。']
  ]
  const fix = fixes.find(([pattern]) => pattern.test(message))
  if (fix) return fix[1]
  const code = error?.code ?? error?.error ?? error?.payload?.error ?? String(error?.message ?? '').match(/(?:^|\b)([A-Z][A-Z_]+|recovery_required|outcome_unknown|edit\.\w+|conflict)(?=:|\b)/)?.[1]
  return Object.hasOwn(errorActions, code) ? `${errorActions[code]}（${code}）` : message.split('\n')[0] || '无法完成修改，请检查工程后重试。'
}
export function receiptMessage(action, answer) {
  if (answer.ok === false || answer.code === 'outcome_unknown') return editError(answer)
  return { 'authorize-write': '编辑已启用。', 'revoke-write': '写许可已撤销。', plan: '预检通过，等待保存。', commit: '已保存。', undo: '已撤销当前版本。', adopt: '已接管工程并建立历史。', status: answer.pending ? '存在未决操作，请显式恢复。' : '状态已读取。', recover: answer.pending ? '恢复尚未完成，请核对内核历史。' : '已读取恢复后的工程；原操作结果以历史为准。' }[action] ?? '操作完成。'
}
const warningLabel = warning => warning.startsWith('Unknown extensions are preserved') ? '扩展字段原样保留，隐藏引用未核对。' : warning.startsWith('reapply-edge-fades') ? '重新应用边缘淡化会改变原音量曲线。' : warning
export function PlanReview({ plan, context, showOperations = true }) {
  const operations = plan?.submitted_request?.operations ?? []
  const edit = plan?.edit ?? plan?.result?.edit
  return h('div', { className: 'fv-edit-plan' },
    h('strong', null, `预检通过 · ${operations.length} 项操作`),
    edit ? h('p', { className: 'fv-edit-meta' }, `新增 ${edit.created_ids?.length ?? 0} · 修改 ${edit.changed_ids?.length ?? 0} · 移除 ${edit.deleted_ids?.length ?? 0}`) : null,
    showOperations ? h(OperationList, { operations, context, label: '冻结操作' }) : null,
    edit?.affected_ranges?.length ? h('details', null, h('summary', null, `影响范围 · ${edit.affected_ranges.length} 项`), h('ul', { className: 'fv-edit-list' }, edit.affected_ranges.map(item => h('li', { key: item.id }, describeOperation({ op: 'block.move', id: item.id }, context).detail || item.id, ` · ${range(item.before) || '新增'} → ${range(item.after) || '移除'}`)))) : null,
    edit?.warnings?.length ? h('ul', { className: 'fv-edit-meta' }, edit.warnings.map((warning, index) => h('li', { key: index }, warningLabel(warning)))) : null,
    h('p', { className: 'fv-edit-meta' }, '结构预检，不代表素材或渲染验证；保存时再次核对版本。'),
    h('details', { className: 'fv-edit-receipt' }, h('summary', null, '事务标识'), h('p', null, `操作 ID：${plan?.submitted_request?.op_id ?? plan?.op_id ?? '—'}`), h('p', null, `基线：${plan?.submitted_request?.base_revision ?? plan?.base_revision ?? '—'}`)))
}
export function StatusReview({ status }) {
  return h('details', { className: 'fv-edit-receipt' }, h('summary', null, '最近内核状态'),
    h('p', null, status.ok === false ? editError(status) : status.pending ? '存在未决操作' : '状态已读取'),
    status.op_id ? h('p', null, `操作 ID：${status.op_id}`) : null,
    status.head ? h('p', null, `当前版本：${status.head}`) : null,
    status.operations?.length ? h('ul', null, status.operations.map(operation => h('li', { key: operation.op_id }, `${operation.op_id} · ${operation.state}`))) : null)
}
