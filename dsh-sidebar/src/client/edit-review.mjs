import React from 'react'
const h = React.createElement
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
export function StatusReview({ status }) {
  return h('details', { className: 'fv-edit-receipt' }, h('summary', null, '最近内核状态'),
    h('p', null, status.ok === false ? editError(status) : status.pending ? '存在未决操作' : '状态已读取'),
    status.op_id ? h('p', null, `操作 ID：${status.op_id}`) : null,
    status.head ? h('p', null, `当前版本：${status.head}`) : null,
    status.operations?.length ? h('ul', null, status.operations.map(operation => h('li', { key: operation.op_id }, `${operation.op_id} · ${operation.state}`))) : null)
}
