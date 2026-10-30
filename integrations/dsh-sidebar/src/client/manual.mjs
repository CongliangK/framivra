import React, { useEffect, useRef, useState } from 'react'
import { parseJSON, stringifyJSON } from '../json.mjs'
const h = React.createElement
const id = () => crypto.randomUUID()
export const EDIT_OPERATIONS = ['source.add','source.remove','source.update','track.add','track.remove','track.copy','track.reorder','track.rename','track.references','block.insert','block.remove','block.copy','block.move','block.relink','block.trim','block.split','parameters.update','parameters.unset','project.resize','range.ripple']
const names = ['添加素材','移除素材（显式 cascade）','更新素材路径/变换','新建轨道（含首块）','删除轨道','复制轨道','排列轨道','重命名轨道','音轨引用','插入块','删除选中块','复制选中块','移动选中块','重新链接块','向内裁剪','播放头分割','更新属性','恢复缺省属性','工程时长','显式轨道集合 ripple']
export function integerToken(text, signed = false) {
  if (typeof text !== 'string' || !(signed ? /^-?(0|[1-9][0-9]*)$/ : /^(0|[1-9][0-9]*)$/).test(text) || text === '-0' || !Number.isSafeInteger(Number(text))) throw new Error('不可编辑：需要精确安全整数帧/位置，不接受舍入、空白、小数或指数')
  return Number(text)
}
const knownProperties = ['transform','scale','x','y','opacity','fade_in_frames','fade_out_frames','cache_dependencies','volume','pan','loop','duck','duck_params','text','gfx_dependency_policy']
/** Only assembles explicit intents. Source windows, phase, fades and references are kernel decisions. */
export function compileIntent(op, v, { result, blocks, track, frame, makeId = id }) {
  const n = key => integerToken(v[key] ?? '')
  const targetTrack = () => v.track_id || track?.data?.id || (track?.family === 'subtitles' ? 'subtitles' : '')
  const one = () => { if (blocks.length !== 1 || typeof blocks[0].data?.id !== 'string') throw new Error('请选择一个受管块'); return blocks[0].data.id }
  const t = () => { if (!track?.data?.id) throw new Error('请选择一个真实受管轨道（字幕虚拟轨不适用）'); return track.data.id }
  const jsonValue = key => parseJSON(v[key])
  const firstData = () => {
    const data = v.family === 'audio' ? { id: makeId(), src: v.src, in: n('start'), out: n('end'), src_in: n('src_in'), volume: jsonValue('volume') } : v.family === 'subtitles' ? { id: makeId(), in: n('start'), out: n('end'), text: v.text } : { id: makeId(), src: v.src, dst_in: n('start'), dur: n('duration'), ...(v.kind === 'gfx' ? {} : { src_in: n('src_in'), src_out: n('src_out') }) }
    return data
  }
  if (!EDIT_OPERATIONS.includes(op)) throw new Error('Unsupported operation')
  switch (op) {
    case 'source.add': return [{ op, data: { id: makeId(), path: v.path } }]
    case 'source.update': return [{ op, id: v.source_id, set: v.source_field === 'transform' ? { transform: jsonValue('value') } : { path: v.path } }]
    case 'source.remove': return [{ op, id: v.source_id, ...(v.cascade ? { cascade: blocks.map(b => b.data.id) } : {}) }]
    case 'track.add': { if (v.family === 'subtitles') throw new Error('不支持额外字幕轨：仅内核虚拟 subtitles'); const data = { id: makeId(), name: v.name, ...(v.family === 'video' ? { kind: v.kind, clips: [firstData()] } : { events: [firstData()] }) }; return [{ op, family: v.family, index: n('index'), data }] }
    case 'track.remove': return [{ op, id: t() }]
    case 'track.copy': { const old = [t(), ...result.blocks.filter(b => b.family === track.family && b.track_index === track.track_index).map(b => b.data.id)]; return [{ op, id: t(), ids: Object.fromEntries(old.map(key => [key, makeId()])), name: v.name, index: n('index') }] }
    case 'track.rename': return [{ op, id: t(), name: v.name }]
    case 'track.reorder': return [{ op, family: v.family, ids: v.order }]
    case 'track.references': return [{ op, id: t(), set: v.unset ? {} : { [v.reference_field]: jsonValue('value') }, ...(v.unset ? { unset: [v.reference_field] } : {}) }]
    case 'block.insert': return [{ op, track_id: targetTrack(), data: firstData() }]
    case 'block.remove': return blocks.map(b => ({ op, id: b.data.id }))
    case 'block.copy': case 'block.move': {
      if (!blocks.length) throw new Error('请选择受管块')
      const at = n('at'), anchor = blocks[0].range[0]
      return blocks.map(b => { const start = at + (b.range[0] - anchor); if (!Number.isSafeInteger(start) || start < 0) throw new Error('批量位置超出安全整数'); return { op, id: b.data.id, track_id: v.track_id || (b.family === 'subtitles' ? 'subtitles' : result.tracks.find(t => t.family === b.family && t.track_index === b.track_index)?.data.id), at: start, ...(op === 'block.copy' ? { new_id: makeId() } : {}) } })
    }
    case 'block.relink': return [{ op, id: one(), src: v.src }]
    case 'block.trim': return [{ op, id: one(), start: n('start'), end: n('end'), fade_policy: v.fade_policy }]
    case 'block.split': {
      if (!Number.isSafeInteger(frame)) throw new Error('请先设置精确整数播放头')
      return [{ op, id: one(), at: frame, new_id: makeId(), fade_policy: v.fade_policy, ...(blocks[0].family === 'subtitles' ? { text_policy: v.text_policy, ...(v.text_policy === 'explicit' ? { texts: [v.text, v.right_text] } : {}) } : {}) }]
    }
    case 'parameters.update': case 'parameters.unset': {
      if (!knownProperties.includes(v.property)) throw new Error('UI 仅支持合同属性白名单；未知扩展保留、不可直接编辑')
      const target = v.property_target === 'project' ? { target: 'project' } : v.property_target === 'source' ? { id: v.source_id } : v.property_target === 'track' ? { id: t() } : { id: one() }
      const value = op === 'parameters.update' ? v.property === 'text' ? v.value : jsonValue('value') : undefined
      if (/frames$/.test(v.property) && op === 'parameters.update') integerToken(v.value)
      return [{ op, ...target, path: [v.property], ...(op === 'parameters.update' ? { value } : {}) }]
    }
    case 'project.resize': return [{ op, duration_frames: n('duration') }]
    case 'range.ripple': return [{ op, scope: { track_ids: v.scope, subtitles: Boolean(v.subtitles) }, mode: v.mode, crossing: v.crossing, resize: Boolean(v.resize), ...(v.mode === 'shift' ? { at: n('at'), delta: integerToken(v.delta, true) } : { start: n('start'), end: n('end'), ...(v.crossing === 'cut' ? { split_ids: Object.fromEntries((v.split_ids ?? []).map(key => [key, makeId()])), text_policy: 'duplicate-text', fade_policy: v.fade_policy } : {}) }) }]
  }
}

export function ManualEditor({ sessionId, opened, result, selection, selected = [], frame, tabSignal, call, onCommitted, onGestureReady }) {
  const [write, setWrite] = useState()
  const [op, setOp] = useState('block.move')
  const [v, setV] = useState({ family: 'video', kind: 'aroll', source_field: 'path', property: 'opacity', property_target: 'block', reference_field: 'duck_by', fade_policy: 'preserve-content', text_policy: 'duplicate-text', mode: 'shift', crossing: 'reject', scope: [], split_ids: [], order: [], start: '0', end: '10', duration: '10', src_in: '0', src_out: '10', volume: '1', at: '0', delta: '1', index: '0', value: '1' })
  const [queue, setQueue] = useState([]), [plan, setPlan] = useState(), [status, setStatus] = useState(), [busy, setBusy] = useState(false), [message, setMessage] = useState('')
  const request = useRef(), generation = useRef(0), live = useRef(true), invalidations = useRef(Promise.resolve())
  const managed = opened.snapshot.document?._vtx?.protocol === 1
  const allSelected = selected.length ? selected : selection ? [selection] : []
  const blocks = result.blocks.filter(b => allSelected.some(s => s.kind === 'block' && s.address === b.address))
  const track = selection?.kind === 'track' ? result.tracks.find(t => t.address === selection.address) : blocks.length ? result.tracks.find(t => t.family === blocks[0].family && t.track_index === blocks[0].track_index) : undefined
  const base = opened.snapshot.content_version
  const payload = (action, extra = {}) => ({ op: action, grant: opened.grant, ...(write ? { writeGrant: write } : {}), ...extra })
  function invalidate() {
    generation.current++; setPlan(undefined)
    // A delayed invalidation must not overtake the next plan. Keep every
    // acknowledgement, fail closed on transport loss, and await this barrier.
    if (write) invalidations.current = Promise.all([invalidations.current, call(sessionId, payload('invalidate-plan'), tabSignal).then(() => undefined, error => error)]).then(outcomes => outcomes.find(Boolean))
  }
  useEffect(() => { invalidate() }, [selection, selected, frame, base])
  useEffect(() => { live.current = true; return () => { live.current = false; request.current?.abort() } }, [])
  useEffect(() => {
    onGestureReady?.(write && managed && !busy ? operations => { invalidate(); setQueue(previous => previous.concat(operations)); setMessage('拖动仅生成未保存 intent；请检查批次并计划。') } : undefined)
    return () => onGestureReady?.(undefined)
  }, [write, managed, busy, base, selected])
  const set = (key, value) => { invalidate(); setV(previous => ({ ...previous, [key]: value })) }
  async function run(action, extra = {}) {
    request.current?.abort()
    const controller = new AbortController(); request.current = controller
    const signal = AbortSignal.any([tabSignal, controller.signal])
    const epoch = generation.current
    const operationId = ['adopt','undo'].includes(action) ? id() : plan?.submitted_request?.op_id
    if (['adopt','undo'].includes(action)) extra = { ...extra, op_id: operationId }
    setBusy(true); setMessage(`正在执行内核命令…${operationId ? ` op_id ${operationId}` : ''}`)
    try {
      if (action === 'plan') {
        const failure = await invalidations.current
        signal.throwIfAborted()
        if (failure) throw new Error('PLAN_INVALIDATION_FAILED: 无法确认旧计划已撤销；请重新打开工程/授予许可')
        if (epoch !== generation.current) throw new Error('PLAN_INVALID: 等待撤销时选择或 intent 已变化')
      }
      const answer = await call(sessionId, payload(action, extra), signal)
      if (!live.current || signal.aborted) return
      if (action === 'authorize-write') { invalidations.current = Promise.resolve(); setWrite(answer.writeGrant) }
      else if (action === 'revoke-write') { setWrite(undefined); invalidate() }
      else if (action === 'plan' && answer.ok && epoch === generation.current) setPlan(answer)
      else if (action === 'status' || action === 'recover') { setStatus(answer); invalidate() }
      else if (['commit','undo','adopt'].includes(action)) { setPlan(undefined); if (answer.ok) { setQueue([]); await onCommitted() } }
      setMessage(stringifyJSON(answer, 2))
    } catch (error) {
      if (live.current) setMessage(`${error.message}\n${['commit','adopt','undo','recover'].includes(action) ? `结果可能未知；原 op_id：${operationId ?? '请读 core status/history'}。不要新建操作重试，显式 status/recover。` : ''}`)
    } finally { if (live.current) setBusy(false) }
  }
  function add() {
    try { const operations = compileIntent(op, v, { result, blocks, track, frame }); if (!operations.length) throw new Error('请选择操作对象'); invalidate(); setQueue(previous => previous.concat(operations)); setMessage('已加入未保存批次；没有写工程。') } catch (error) { setMessage(error.message) }
  }
  const field = (key, title, type = 'text') => h('label', { key, style: { display: 'block' } }, title, h('input', { type, value: v[key] ?? '', onChange: e => set(key, e.target.value), style: { display: 'block', width: '100%', boxSizing: 'border-box' } }))
  const select = (key, title, items) => h('label', { key, style: { display: 'block' } }, title, h('select', { value: v[key] ?? '', onChange: e => set(key, e.target.value), style: { width: '100%' } }, items.map(([value, label]) => h('option', { key: value, value }, label))))
  const check = (key, title) => h('label', { key, style: { display: 'block' } }, h('input', { type: 'checkbox', checked: Boolean(v[key]), onChange: e => set(key, e.target.checked) }), title)
  const sources = opened.snapshot.document.sources ?? []
  const sourceSelect = () => select('source_id', '素材 ID', [['','请选择素材'], ...sources.map(s => [s.id, `${s.id} · ${s.path}`])])
  const trackSelect = () => select('track_id', '目标轨（空=原轨）', [['','保留原轨'], ...result.tracks.map(t => [t.family === 'subtitles' ? 'subtitles' : t.data.id, `${t.family} · ${t.data.name ?? t.address}`])])
  const fade = () => select('fade_policy', '淡入淡出策略（显式）', [['preserve-content','保留内容曲线（无法精确保留则拒绝）'], ['reapply-edge-fades','重新应用边缘 fade（行为变化）']])
  const family = () => select('family', '对象族', [['video','视频 / gfx'],['audio','音频'],['subtitles','内联字幕']])
  const newBlock = () => [family(), v.family === 'video' ? select('kind','视频轨类型', [['aroll','aroll'],['broll','broll'],['gfx','gfx']]) : null, field('src','源 ID / gfx 文件路径（明确引用，不探素材）'), field('start','全局起点帧'), v.family === 'video' ? field('duration','时长帧') : field('end','全局终点帧'), v.family !== 'subtitles' ? field('src_in','源起点帧（显式）') : field('text','字幕文本'), v.family === 'video' && v.kind !== 'gfx' ? field('src_out','源终点帧（显式）') : null, v.family === 'audio' ? field('volume','音量（JSON 数值）') : null]
  let form = []
  if (op.startsWith('source.')) form = [sourceSelect(), op !== 'source.remove' ? field('path','素材路径（不复制媒体）') : check('cascade','确认 cascade 恰为时间轴多选块 ID；内核核对完整用户集合'), op === 'source.update' ? select('source_field','更新字段', [['path','路径'],['transform','transform（单个 JSON 属性）']]) : null, op === 'source.update' && v.source_field === 'transform' ? field('value','transform JSON 属性') : null]
  if (op === 'track.add') form = [field('name','轨道名称'),field('index','数组插入位置'),...newBlock()]
  if (op === 'track.copy') form = [field('name','副本名称'),field('index','数组插入位置')]
  if (op === 'track.rename') form = [field('name','轨道新名称')]
  if (op === 'track.references') form = [select('reference_field','引用字段', [['duck_by','duck_by 轨名 JSON 数组'],['duck_strategy','duck_strategy JSON 属性']]), check('unset','移除该可选引用字段'),field('value','引用值（duck_by 为 JSON 字符串数组，如 ["voice"]）')]
  if (op === 'track.reorder') {
    const order = v.order.length ? v.order : result.tracks.filter(t => t.family === v.family).map(t => t.data.id)
    form = [family(), h('ol', { key: 'order' }, order.map((key,i) => h('li', { key }, key, ...[-1,1].map(d => h('button',{ type:'button',key:d,disabled:i+d<0||i+d>=order.length,onClick:()=>{ const next=[...order]; [next[i],next[i+d]]=[next[i+d],next[i]];set('order',next) }},d<0?'上移':'下移'))))),h('button',{type:'button',key:'useorder',onClick:()=>set('order',order)},'采用此完整轨序')]
  }
  if (op === 'block.insert') form = [trackSelect(), ...newBlock()]
  if (['block.copy','block.move'].includes(op)) form = [trackSelect(),field('at','首选块新起点帧（多选保持相对位置）')]
  if (op === 'block.relink') form = [field('src','新 source ID / gfx 文件路径')]
  if (op === 'block.trim') form = [field('start','新的全局起点帧（仅 inward）'),field('end','新的全局终点帧（仅 inward）'),fade()]
  if (op === 'block.split') form = [h('p',{key:'head'},`分割点 = 精确播放头 ${frame ?? '未设置'}`),fade(),select('text_policy','字幕文本策略', [['duplicate-text','明确复制文本到两半'],['explicit','分别输入两段文本']]),field('text','左段文本'),field('right_text','右段文本')]
  if (op.startsWith('parameters.')) form = [select('property_target','属性目标', [['block','选中块'],['track','选中轨'],['source','素材'],['project','工程']]),sourceSelect(), select('property','白名单属性（不适用对象由内核精确拒绝）',knownProperties.map(p=>[p,p])),op === 'parameters.update' ? field('value','属性值（text 为文本，其余单个 JSON 值）') : null]
  if (op === 'project.resize') form = [field('duration','工程新时长帧（不改 FPS/分辨率）')]
  if (op === 'range.ripple') form = [select('mode','ripple 模式',[['shift','平移'],['delete','删除区间']]),select('crossing','跨界策略',[['reject','跨界拒绝'],['cut','明确剪切（仅 delete）']]),check('resize','同时调整画布时长'),check('subtitles','明确包含内联字幕'),h('fieldset',{key:'scope'},h('legend',null,'显式轨集合（不隐式联动 AV）'), result.tracks.filter(t=>t.data?.id).map(t=>h('label',{key:t.data.id,style:{display:'block'}},h('input',{type:'checkbox',checked:v.scope.includes(t.data.id),onChange:e=>set('scope', e.target.checked?v.scope.concat(t.data.id):v.scope.filter(k=>k!==t.data.id))}),`${t.family} ${t.data.name} ${t.data.id}`))),field('at','shift 起点帧'),field('delta','shift 有符号整数偏移'),field('start','delete 起点帧'),field('end','delete 终点帧'),fade(), h('fieldset',{key:'splits'},h('legend',null,'确认为跨两端块提供右侧新 ID（内核要求恰好枚举）'),blocks.map(b=>h('label',{key:b.data.id,style:{display:'block'}},h('input',{type:'checkbox',checked:v.split_ids.includes(b.data.id),onChange:e=>set('split_ids',e.target.checked?v.split_ids.concat(b.data.id):v.split_ids.filter(k=>k!==b.data.id))}),b.data.id)))]
  return h('section',{'aria-label':'手动语义编辑',style:{border:'1px solid currentColor',padding:8,minWidth:0}},
    h('h3',null,'手动编辑 · 内核计划 / 单批提交'),
    h('p',null,'只编辑受管工程；不渲染、不暗存、不自动 adopt。取消/断线可能结果未知。trim 仅向内；gfx 仅移动/复制/删除，trim/split/crossing ripple 后端不支持；外字幕只读。没有隐式 AV 联动。'),
    h('button',{type:'button',disabled:busy,onClick:()=>void run(write?'revoke-write':'authorize-write')},write?'撤销本会话/工程写许可':'明确允许本会话向此工程及 core 历史写入'),
    h('p',null,`写授权：${write?'已明确授予（非 read grant）':'未授予，保留 read / copy / noDraft 降级'}；选择 ${blocks.length} 个块 / ${track?.data?.id ?? '无真实轨'}`),
    !managed ? h('div',null,h('p',null,'Legacy：只读。显式接管会分配稳定 ID 并创建持久历史；现有未知 ID/不兼容素材 ID 将被内核拒绝，绝不自动改名或迁移。'), h('button',{type:'button',disabled:!write||busy,onClick:()=>void run('adopt',{base_revision:base,confirmation:'stable-ids-and-history'})},'确认稳定 ID / 历史影响并接管 legacy')) : null,
    h('fieldset',{disabled:!write||!managed||busy,style:{minWidth:0}},h('legend',null,'专用操作表单（不是整段 JSON 请求）'),selectOperation(),...form,
      h('button',{type:'button',onClick:add},'加入未保存批次'), h('button',{type:'button',onClick:()=>{invalidate();setQueue([])}},'清空未保存批次')),
    h('pre',{'aria-label':'未保存 typed intents',style:{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}},stringifyJSON(queue,2)),
    h('button',{type:'button',disabled:!write||busy||!queue.length,onClick:()=>void run('plan',{base_revision:base,operations:queue})},'内核预检计划（零写）'),
    plan ? h('div',null,h('strong',null,`未保存计划 · op_id ${plan.submitted_request.op_id}`),h('pre',{style:{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}},stringifyJSON(plan,2)),h('button',{type:'button',disabled:busy,onClick:()=>void run('commit',{planToken:plan.planToken})},'确认提交此冻结计划（一次 revision）')) : null,
    h('button',{type:'button',disabled:!write||busy,onClick:()=>void run('status')},'读取 core status / history（可能初始化历史）'),
    h('button',{type:'button',disabled:!write||busy,onClick:()=>void run('recover')},'显式 core recover（不另建操作）'),
    h('button',{type:'button',disabled:!write||busy||!opened.snapshot.document?._vtx?.head,onClick:()=>void run('undo',{base_revision:base,head:opened.snapshot.document._vtx.head})},'撤销当前 core head（新 CAS 事务）'),
    h('p',null,'Redo：内核未支持，不伪造浏览器逆操作。status/recover 结果是唯一恢复依据；没有浏览器持久 timeline/history。'),
    busy ? h('button',{type:'button',onClick:()=>{request.current?.abort();void call(sessionId,payload('cancel-write'),tabSignal).catch(()=>{})}},'取消并等待子进程回收（写结果可能未知）') : null,
    h('pre',{role:'status',style:{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}},message),status?h('details',null,h('summary',null,'最近 core status / recover'),h('pre',{style:{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}},stringifyJSON(status,2))):null)
  function selectOperation() { return h('label',{key:'operation'},'编辑操作',h('select',{value:op,onChange:e=>{invalidate();setOp(e.target.value)},style:{width:'100%'}}, EDIT_OPERATIONS.map((value,i)=>h('option',{key:value,value},`${names[i]} · ${value}`)))) }
}
