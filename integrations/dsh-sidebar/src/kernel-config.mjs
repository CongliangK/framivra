import { parseJSON, stringifyJSON } from './json.mjs'

/** Static, browser/Host shared configuration help. No services, files, or plugins are loaded.
 * These are the template and known consumers, NOT a complete kernel schema.
 * Omitted fields stay omitted; nullable fields accept cfg_get's null fallback.
 * cache.mode is a strict three-value contract, with core only when omitted.
 */
export const KERNEL_CONFIG_GROUPS = Object.freeze([
  { id: 'cache', label: '全局缓存' },
  { id: 'external', label: '仓外扩展注册' },
  { id: 'comfyui', label: 'ComfyUI 背景生成' },
  { id: 'asr', label: 'ASR 字幕识别' },
  { id: 'tts', label: 'TTS 占位语音' },
  { id: 'imgen', label: '图像生成' },
  { id: 'bgm', label: 'BGM 配乐' },
  { id: 'denoise', label: '口播降噪' },
].map(Object.freeze))

const template = 'config.example.json'
const loader = 'vtx/pluginlib/config.py'
const asr = 'apps/asr/independence.py; apps/asr/client.py'
const tts = 'apps/tts/tts_cli.py; apps/tts/spec.py'
const imgen = 'apps/imgen/imgen_cli.py'
const field = (path, label, type, description, source, format) => Object.freeze({
  path, group: path.includes('.') ? path.split('.')[0] : 'external', label, type,
  nullable: true, ...(format ? { format } : {}), description, source,
  consumption: source === template ? 'template' : 'runtime',
})
const enabled = (group, label, consumption = 'template', source = template) => Object.freeze({
  ...field(`${group}.enabled`, label, 'boolean',
    consumption === 'runtime'
      ? 'ASR 计算与启动入口的启用门禁；只有 true 或兼容的真值字符串才启用。检查通过不代表服务身份、依赖、许可或显存就绪。'
      : consumption === 'manifest'
        ? '供组件清单或健康标记读取，不代表执行链的统一启停开关。建议使用 JSON true / false。'
        : '模板声明字段；当前没有统一运行时门控。设置此值不保证启用或关闭执行链。建议使用 JSON true / false。', source),
  consumption,
})
const pathField = (path, label, source) => field(path, label, 'string',
  '文件或目录路径；支持 ~/、绝对路径和消费方支持的相对路径。不检查存在性、权限、软链接、归属或依赖。空字符串不等同于未设置。', source, 'path')

export const KERNEL_CONFIG_FIELDS = Object.freeze([
  Object.freeze({
    ...field('cache.mode', '缓存档位', 'string',
      'off：不读取或保留可复用缓存，必要临时件用后清理，并非完全无中间文件；core：压缩保留高价值 gfx，合成缓存短暂保留（推荐）；aggressive：保留更多缓存，以磁盘换取复用。缺省为 core。影响使用同一 VTX_CONFIG 的所有工程，不是当前时间线或会话的选项；下次操作读取生效，不改变正在运行的任务，保存不会立即清理旧缓存目录。', 'vtx/cache/policy.py'),
    nullable: false, enum: Object.freeze(['off', 'core', 'aggressive']),
  }),
  field('external_apps', '外部应用', 'array', '条目为路径字符串（默认禁用）或 {"path":"…","enabled":true}。此处只检查形状，不加载或执行扩展，不确认信任或白名单。', `${template}; ${loader}; vtx/pluginlib/registry.py`, 'external-entries'),
  field('external_plugins', '外部插件', 'array', '条目为路径字符串（默认禁用）或 {"path":"…","enabled":true}。此处只检查形状，不加载或执行扩展，不确认信任或白名单。', `${template}; ${loader}; vtx/pluginlib/registry.py`, 'external-entries'),
  enabled('comfyui', 'ComfyUI 清单标记', 'manifest', `${template}; apps/matting/app.json`),
  field('comfyui.base_url', '服务地址', 'string', 'HTTP(S) 服务地址；不连接服务。', template, 'url'),
  enabled('asr', '启用 ASR', 'runtime', asr),
  field('asr.base_url', 'ASR 服务地址', 'string', 'ASR 消费方仅允许 http://host[:port]；禁止输入法专用端口 18081。不验证服务身份，不发起请求。', asr, 'url'),
  pathField('asr.python', '独立 Python 解释器', `${asr}; ${loader}`),
  pathField('asr.model_dir', 'ASR 模型根目录', `${asr}; ${loader}`),
  pathField('asr.aligner_dir', '强制对齐模型目录', `${asr}; ${loader}`),
  field('asr.language', '识别语言', 'string', '传给 ASR 的语言名称；不在此限制语言枚举。', 'apps/asr/asr_cli.py'),
  field('asr.device_map', '设备映射', 'string', '例如 cuda:0、cpu；消费方仅要求字符串，此处不枚举设备、不探测 GPU。', asr),
  field('asr.vram_budget_mib', '显存预算（MiB）', 'number', '消费方要求正数（不要求整数）；此处不检查实际显存可用量。', asr, 'positive'),
  enabled('tts', '启用 TTS'),
  field('tts.engine', '语音引擎', 'string', '模板使用 indextts；实际 gen 的 engine 分支由消费方选择，不在此制造枚举或默认值。', `${template}; ${tts}`),
  field('tts.fallback_engine', '回退引擎', 'string', '模板声明的回退引擎；此检查不保证消费方自动执行回退。', template),
  pathField('tts.repo', 'IndexTTS 仓库', `${tts}; ${loader}`),
  pathField('tts.python', 'TTS Python 解释器', `${tts}; ${loader}`),
  pathField('tts.voice_ref', '参考声音', `${tts}; ${loader}`),
  pathField('tts.edge_bin', 'edge-tts 可执行文件', `${tts}; ${loader}`),
  enabled('imgen', '启用生图'),
  field('imgen.api_base', '生图 API 地址', 'string', 'HTTP(S) API 根地址；不读取凭证，不连接 API。', imgen, 'url'),
  field('imgen.model_default', '默认生图模型', 'string', '模型名称由生图消费方解析，此处不硬编码支持模型列表。', imgen),
  field('imgen.price_per_image', '兼容单张价格', 'number', '旧配置兼容字段，消费方转换为 float；实际按模型单价。不在此添加正数限制。', imgen, 'float-compatible'),
  field('imgen.confirm_before_submit', '提交前确认', 'boolean', '是否在 API 提交前确认；建议使用 JSON true / false。', `${imgen}; ${loader}`),
  enabled('bgm', 'BGM 健康清单标记', 'manifest', `${template}; apps/bgm/app.json`),
  pathField('bgm.repo', '配乐仓库', `plugins/bgm-render/health_check.py; ${loader}`),
  field('bgm.cli', 'BGM CLI 命令行', 'string', '由 shlex 拆分的命令行，可含参数；不是单一文件路径。不在这里解析或执行命令。', `plugins/bgm-render/health_check.py; ${loader}`, 'command'),
  enabled('denoise', '启用降噪'),
])

/** Editor safety limits, not additional kernel semantics. Exceeding any budget fails closed. */
export const KERNEL_CONFIG_LIMITS = Object.freeze({
  maxBytes: 1024 * 1024, maxDepth: 64, maxNodes: 20000, maxDiagnostics: 200, maxDiagnosticPath: 512,
})
class ConfigLimitError extends Error {
  constructor(message, path = '$') { super(message); this.path = path }
}
const safePath = path => path.length > KERNEL_CONFIG_LIMITS.maxDiagnosticPath
  ? path.slice(0, KERNEL_CONFIG_LIMITS.maxDiagnosticPath - 1) + '…' : path
const diagnosticBudgets = new WeakMap()
function diagnostics() {
  const errors = [], warnings = []
  const budget = { count: 0 }
  diagnosticBudgets.set(errors, budget)
  diagnosticBudgets.set(warnings, budget)
  return { errors, warnings }
}
function limitIssue(errors, error) {
  // issue() reserves one slot for this hard-stop error across BOTH diagnostic arrays.
  errors.push({ path: safePath(error.path || '$'), message: error.message })
}
const fields = new Map(KERNEL_CONFIG_FIELDS.map(item => [item.path, item]))
const groups = new Set(KERNEL_CONFIG_GROUPS.filter(item => item.id !== 'external').map(item => item.id))
const blockedKeys = new Set(['__proto__', 'prototype', 'constructor'])
const own = (object, key) => Object.hasOwn(object, key)
const isRaw = value => typeof JSON.isRawJSON === 'function' && JSON.isRawJSON(value)
const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value) && !isRaw(value)
const issue = (list, path, message) => {
  const budget = diagnosticBudgets.get(list)
  if (budget && budget.count >= KERNEL_CONFIG_LIMITS.maxDiagnostics - 1) {
    throw new ConfigLimitError(`诊断数量达到 ${KERNEL_CONFIG_LIMITS.maxDiagnostics} 条安全上限；未完成全部检查。`)
  }
  list.push({ path: safePath(path), message })
  if (budget) budget.count++
}
const childPath = (path, key) => path ? `${path}.${key}` : key
const numeric = value => typeof value === 'number' || isRaw(value)
const numberValue = value => isRaw(value) ? Number(value.rawJSON) : value

function readDocument(text) {
  if (typeof text !== 'string') throw new TypeError('CONFIG_TEXT_REQUIRED')
  // UTF-16 length is a cheap lower bound on UTF-8 bytes; avoid encoding huge input.
  if (text.length > KERNEL_CONFIG_LIMITS.maxBytes || new TextEncoder().encode(text).byteLength > KERNEL_CONFIG_LIMITS.maxBytes) {
    throw new ConfigLimitError(`配置原文超过 ${KERNEL_CONFIG_LIMITS.maxBytes} 字节安全上限；原文未修改。`)
  }
  return parseJSON(text)
}

function checkString(value, metadata, errors, warnings) {
  const { path, format } = metadata
  if (format === 'path' || format === 'command') {
    if (value.includes('\0')) issue(errors, path, '路径或命令行不能包含 NUL 字符。')
    return // Do not invent absolute-path/existence/ownership requirements.
  }
  if (format !== 'url') return
  let url
  try {
    if (!/^https?:\/\//i.test(value)) throw new TypeError('URL_SCHEME_REQUIRED')
    url = new URL(value)
  } catch {
    issue(errors, path, '应为带主机名的 HTTP(S) URL。')
    return
  }
  if (!url.hostname || !['http:', 'https:'].includes(url.protocol)) {
    issue(errors, path, '应为带主机名的 HTTP(S) URL。')
  } else if (path === 'asr.base_url') {
    if (url.protocol !== 'http:') issue(errors, path, 'ASR 消费方仅接受 http:// 地址。')
    if (url.port === '18081') issue(errors, path, '18081 是输入法专用端口，ASR 禁止复用；请配置独立服务。')
  }
  if (url.username || url.password) issue(warnings, path, 'URL 含内嵌凭证；请勿在配置中保存或分享密钥。')
}

function checkExternal(value, path, errors, warnings) {
  value.forEach((entry, index) => {
    const at = `${path}[${index}]`
    if (typeof entry === 'string') {
      if (entry.includes('\0')) issue(errors, at, '扩展路径不能包含 NUL 字符。')
      if (entry.split('/').includes('..')) issue(errors, at, '扩展路径禁止包含 .. 路径段。')
      return
    }
    if (!isObject(entry) || typeof entry.path !== 'string') {
      issue(errors, at, '条目应为路径字符串或含字符串 path 的对象。')
      return
    }
    if (entry.path.includes('\0')) issue(errors, `${at}.path`, '扩展路径不能包含 NUL 字符。')
    if (entry.path.split('/').includes('..')) issue(errors, `${at}.path`, '扩展路径禁止包含 .. 路径段。')
    if (own(entry, 'enabled') && typeof entry.enabled !== 'boolean') {
      issue(warnings, `${at}.enabled`, '扩展仅在 enabled 严格等于 true 时启用；其他值会禁用。建议使用布尔值。')
    }
    for (const key of Object.keys(entry)) {
      if (!['path', 'enabled'].includes(key)) issue(warnings, `${at}.${key}`, '未识别的扩展条目字段；将原样保留。')
    }
  })
}

function checkField(value, metadata, errors, warnings) {
  const { path, type, format } = metadata
  if (metadata.enum) {
    if (!metadata.enum.includes(value)) issue(errors, path, `应为 ${metadata.enum.join(' / ')} 之一；删除字段以沿用默认值。`)
    return
  }
  if (value === null) return // cfg_get(None) -> consumer default, not a GUI default.
  if (type === 'boolean') {
    if (typeof value === 'boolean') return
    if (typeof value === 'string') {
      issue(warnings, path, '兼容布尔字符串由消费方转换，不等同于 JSON 布尔值；建议改用 true / false。')
      return
    }
    issue(errors, path, '应为布尔值或 null；非布尔类型可能被消费方忽略或按真值转换。')
    return
  }
  if (type === 'number') {
    if (!numeric(value)) {
      // The imgen compatibility field is passed to Python float(), which also accepts strings.
      if (format === 'float-compatible' && typeof value === 'string' && value.trim() !== '' &&
          (!Number.isNaN(Number(value)) || /^[-+]?(?:inf(?:inity)?|nan)$/i.test(value.trim())) &&
          !/^[-+]?0[xob]/i.test(value.trim())) {
        issue(warnings, path, '消费方可将此数字字符串转换为 float；建议使用 JSON 数字。')
      } else issue(errors, path, '应为数字或 null。')
      return
    }
    const n = numberValue(value)
    if (format === 'positive' && !(n > 0)) issue(errors, path, '消费方要求正数（MiB）。')
    if (!Number.isFinite(n)) issue(warnings, path, '数字超出本运行时有限数值范围；原始数字已保留，消费方可能无法使用。')
    return
  }
  if (type === 'array') {
    if (!Array.isArray(value)) issue(errors, path, '应为数组或 null。')
    else checkExternal(value, path, errors, warnings)
    return
  }
  if (typeof value !== 'string') {
    issue(errors, path, '应为字符串或 null；不匹配的类型可能被消费方忽略。')
    return
  }
  checkString(value, metadata, errors, warnings)
}

function scanUnsafeKeys(value, path, errors) {
  // Iterative DFS keeps only one iterator per depth; no recursive stack or wide work queue.
  const stack = [{ value, path, depth: 0, iterator: null }]
  let nodes = 0
  while (stack.length) {
    const current = stack.at(-1)
    if (current.iterator === null) {
      if (++nodes > KERNEL_CONFIG_LIMITS.maxNodes) throw new ConfigLimitError(`配置节点超过 ${KERNEL_CONFIG_LIMITS.maxNodes} 个安全上限；未完成全部检查。`, current.path)
      if (current.depth > KERNEL_CONFIG_LIMITS.maxDepth) throw new ConfigLimitError(`配置嵌套超过 ${KERNEL_CONFIG_LIMITS.maxDepth} 层安全上限；未完成全部检查。`, current.path)
      if (current.value === null || typeof current.value !== 'object' || isRaw(current.value)) {
        stack.pop()
        continue
      }
      current.iterator = Array.isArray(current.value)
        ? current.value.keys() : Object.keys(current.value)[Symbol.iterator]()
    }
    const next = current.iterator.next()
    if (next.done) { stack.pop(); continue }
    const key = next.value
    const at = safePath(Array.isArray(current.value) ? `${current.path}[${key}]` : childPath(current.path, key))
    if (blockedKeys.has(key)) issue(errors, at, '为防止原型污染，不支持此键名。')
    stack.push({ value: current.value[key], path: at, depth: current.depth + 1, iterator: null })
  }
}

/** Check JSON structure only. ok does NOT certify environment, trust, rendering, or services.
 * value uses native rawJSON for noncanonical numeric tokens; never coerce/stringify via a
 * different JSON implementation. text is returned unchanged, including on failed checks.
 */
export function checkKernelConfig(text) {
  const { errors, warnings } = diagnostics()
  let value
  try { value = readDocument(text) } catch (error) {
    if (error instanceof ConfigLimitError) {
      limitIssue(errors, error)
      return { ok: false, errors, warnings, text }
    }
    const unsupported = error?.name === 'LosslessJSONError'
    issue(errors, '$', unsupported
      ? '当前运行时缺少原生 JSON source/rawJSON 支持；请升级后编辑，避免数字精度丢失。'
      : typeof text !== 'string' ? '配置原文必须为字符串。' : 'JSON 语法无效；请检查引号、逗号、括号及尾随内容。')
    return { ok: false, errors, warnings, text }
  }
  if (!isObject(value)) {
    issue(errors, '$', '配置顶层必须是 JSON 对象（不能是数组、null 或其他值）。')
    return { ok: false, errors, warnings, text }
  }
  try {
    scanUnsafeKeys(value, '', errors)
    for (const [key, entry] of Object.entries(value)) {
      if (key === '_comment' || key === '_comment_external') continue
      if (fields.has(key)) checkField(entry, fields.get(key), errors, warnings)
      else if (groups.has(key)) {
        if (!isObject(entry)) {
          issue(errors, key, '能力分组应为 JSON 对象。')
          continue
        }
        for (const [name, item] of Object.entries(entry)) {
          const path = `${key}.${name}`
          if (path === 'asr.ime_root') {
            // The Python migration guard only errors for non-None. Keep that distinction.
            issue(item === null ? warnings : errors, path, 'ASR 输入法复用键已退役，请删除；仅配置 Framivra 独立资产。')
          } else if (fields.has(path)) checkField(item, fields.get(path), errors, warnings)
          else if (!['purpose', 'howto'].includes(name)) issue(warnings, path, '未识别的配置字段；将原样保留，本检查不覆盖其语义。')
        }
      } else issue(warnings, key, '未识别的配置字段；将原样保留，本检查不覆盖其语义。')
    }
  } catch (error) {
    if (!(error instanceof ConfigLimitError)) throw error
    limitIssue(errors, error)
    return { ok: false, errors, warnings, text }
  }
  return { ok: errors.length === 0, errors, warnings, text, value }
}

function validateReplacement(value, seen = new Set(), budget = { nodes: 0, units: 0 }, depth = 0) {
  if (++budget.nodes > KERNEL_CONFIG_LIMITS.maxNodes) throw new ConfigLimitError('字段值超过节点安全上限；原文未修改。')
  if (depth > KERNEL_CONFIG_LIMITS.maxDepth) throw new ConfigLimitError('字段值超过嵌套深度安全上限；原文未修改。')
  budget.units += typeof value === 'string' ? value.length : isRaw(value) ? value.rawJSON.length : 0
  if (budget.units > KERNEL_CONFIG_LIMITS.maxBytes) throw new ConfigLimitError('字段值超过大小安全上限；原文未修改。')
  if (isRaw(value)) return
  if (typeof value === 'number') {
    if (!Number.isFinite(value) || (Number.isInteger(value) && !Number.isSafeInteger(value))) {
      throw new TypeError('不接受非有限或不可精确表示的数值；请在完整 JSON 原文中编辑该数字。')
    }
    return
  }
  if (value === null || ['string', 'boolean'].includes(typeof value)) return
  if (typeof value !== 'object') throw new TypeError('字段值必须是 JSON 值；仅顶层 undefined 表示删除。')
  if (seen.has(value)) throw new TypeError('字段值不能包含循环引用。')
  const array = Array.isArray(value)
  if (!(array ? [Array.prototype, null] : [Object.prototype, null]).includes(Object.getPrototypeOf(value))) {
    throw new TypeError('字段值必须是普通 JSON 对象或数组。')
  }
  seen.add(value)
  for (const key of Reflect.ownKeys(value)) {
    if (array && key === 'length') continue
    if (typeof key !== 'string') throw new TypeError('字段值不能包含符号键。')
    budget.units += key.length
    if (budget.units > KERNEL_CONFIG_LIMITS.maxBytes) throw new ConfigLimitError('字段值超过大小安全上限；原文未修改。')
    if (blockedKeys.has(key)) throw new TypeError('为防止原型污染，不支持此键名。')
    if (array && (!/^(0|[1-9]\d*)$/.test(key) || Number(key) >= value.length)) {
      throw new TypeError('字段数组不能包含空洞或额外属性。')
    }
    const descriptor = Object.getOwnPropertyDescriptor(value, key)
    if (!descriptor || !('value' in descriptor)) throw new TypeError('字段值不能包含访问器。')
    if (!descriptor.enumerable) throw new TypeError('字段值不能包含非枚举属性。')
    validateReplacement(descriptor.value, seen, budget, depth + 1)
  }
  if (own(value, 'toJSON')) throw new TypeError('字段值不能包含自定义 toJSON。')
  if (array && Object.keys(value).length !== value.length) throw new TypeError('字段数组不能包含空洞或额外属性。')
  seen.delete(value)
}

/** Apply one form edit, preserving unknown values and exact numeric source tokens.
 * Unlike checkKernelConfig, a successful form edit deliberately reserializes the document.
 * undefined deletes only the override (no template defaults/empty parent pruning).
 * Existing invalid fields may be repaired one at a time; ok reports the resulting check.
 * On an unsafe/unrepresentable edit text is unchanged. No persistence occurs here.
 */
export function updateKernelConfigField(text, path, value) {
  const failure = message => ({ ok: false, text, errors: [{ path: typeof path === 'string' ? safePath(path) : '$', message }], warnings: [] })
  if (typeof path !== 'string' || !path || path.length > KERNEL_CONFIG_LIMITS.maxDiagnosticPath ||
      path.split('.').length > KERNEL_CONFIG_LIMITS.maxDepth || path.split('.').some(key => !key || blockedKeys.has(key))) {
    return failure('字段路径必须在长度和深度安全上限内，使用非空点路径且不能包含原型污染键。')
  }
  const initial = checkKernelConfig(text)
  if (!initial.value) return initial // Parsing/tree/diagnostic limits failed: never rewrite a partially checked document.
  const document = initial.value
  const { errors: unsafe } = diagnostics()
  try { scanUnsafeKeys(document, '', unsafe) } catch (error) {
    if (!(error instanceof ConfigLimitError)) throw error
    limitIssue(unsafe, error)
  }
  if (unsafe.length) return { ok: false, text, errors: unsafe, warnings: [] }
  try { if (value !== undefined) validateReplacement(value) } catch (error) { return failure(error.message) }
  const parts = path.split('.')
  let node = document
  for (let index = 0; index < parts.length - 1; index++) {
    const key = parts[index]
    if (!own(node, key)) {
      if (value === undefined) return checkKernelConfig(text) // A missing override stays missing.
      Object.defineProperty(node, key, { value: {}, enumerable: true, configurable: true, writable: true })
    }
    if (!isObject(node[key])) return failure('父字段不是对象；请先在完整 JSON 中修正，避免覆盖已有未知值。')
    node = node[key]
  }
  const key = parts.at(-1)
  if (value === undefined) {
    if (!own(node, key)) return checkKernelConfig(text)
    delete node[key]
  } else Object.defineProperty(node, key, { value, enumerable: true, configurable: true, writable: true })
  let updated
  try { updated = stringifyJSON(document, 2) + (text.endsWith('\n') ? '\n' : '') } catch {
    return failure('字段无法无损序列化；原文未修改。')
  }
  const result = checkKernelConfig(updated)
  // Reformatting or combining a replacement with existing data can exceed a budget.
  // Refuse atomically: never return a newly oversized/partially checked document.
  return result.value ? result : { ...result, text }
}
