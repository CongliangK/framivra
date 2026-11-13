import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { KERNEL_CONFIG_FIELDS, KERNEL_CONFIG_GROUPS, KERNEL_CONFIG_LIMITS, checkKernelConfig, updateKernelConfigField } from '../src/kernel-config.mjs'
import { parseJSON, stringifyJSON } from '../src/json.mjs'

const check = value => checkKernelConfig(JSON.stringify(value))
const errorAt = (result, path) => result.errors.some(item => item.path === path)
const warningAt = (result, path) => result.warnings.some(item => item.path === path)

test('template is checked without constructing defaults or loading external components', () => {
  const text = fs.readFileSync(new URL('../../../config.example.json', import.meta.url), 'utf8')
  const result = checkKernelConfig(text)
  assert.equal(result.ok, true, JSON.stringify(result.errors))
  assert.deepEqual(result.warnings, [])
  assert.equal(result.text, text)
  assert.deepEqual(checkKernelConfig('{}').value, {})
  assert.deepEqual(checkKernelConfig('{}').errors, [])
})

test('metadata describes actual consumer layers, types and sources without invented defaults', () => {
  const ids = new Set(KERNEL_CONFIG_GROUPS.map(item => item.id))
  const paths = new Set()
  for (const field of KERNEL_CONFIG_FIELDS) {
    assert.ok(ids.has(field.group), field.path)
    assert.equal(typeof field.path, 'string')
    assert.ok(field.label && field.description && field.source)
    assert.ok(['runtime', 'manifest', 'template'].includes(field.consumption))
    assert.equal(typeof field.nullable, 'boolean')
    assert.equal(Object.hasOwn(field, 'default'), false)
    assert.equal(paths.has(field.path), false)
    paths.add(field.path)
  }
  const field = path => KERNEL_CONFIG_FIELDS.find(item => item.path === path)
  assert.equal(field('bgm.cli').format, 'command')
  assert.equal(field('asr.enabled').consumption, 'runtime')
  for (const path of ['tts.enabled', 'imgen.enabled', 'denoise.enabled']) assert.equal(field(path).consumption, 'template')
  assert.equal(field('comfyui.enabled').consumption, 'manifest')
  assert.equal(field('bgm.enabled').consumption, 'manifest')
})

test('cache mode is a strict global three-value field; missing defaults are never materialized', () => {
  const field = KERNEL_CONFIG_FIELDS.find(item => item.path === 'cache.mode')
  assert.equal(field.group, 'cache')
  assert.equal(field.nullable, false)
  assert.equal(field.consumption, 'runtime')
  assert.deepEqual(field.enum, ['off', 'core', 'aggressive'])
  for (const mode of field.enum) {
    const result = check({ cache: { mode } })
    assert.equal(result.ok, true)
    assert.deepEqual(result.warnings, [])
  }
  for (const mode of ['', 'CORE', ' core ', 'default', false, 0, null, [], {}]) {
    assert.ok(errorAt(check({ cache: { mode } }), 'cache.mode'), JSON.stringify(mode))
  }
  for (const cache of [null, false, 'core', []]) assert.ok(errorAt(check({ cache }), 'cache'))
  for (const text of ['{}', '{ "cache": {} }\n']) {
    assert.equal(checkKernelConfig(text).text, text)
    assert.equal(updateKernelConfigField(text, 'cache.mode', undefined).text, text)
    assert.equal(Object.hasOwn(checkKernelConfig(text).value.cache ?? {}, 'mode'), false)
  }
})

test('cache form edits preserve BGM, cache extensions and unknown exact numeric tokens', () => {
  const text = '{"cache":{"future":9007199254740993},"bgm":{"repo":"~/music","cli":"bgm --flag","enabled":false,"future":1e+100},"future":[-0,1.0000000000000001]}'
  for (const mode of ['off', 'core', 'aggressive']) {
    const result = updateKernelConfigField(text, 'cache.mode', mode)
    assert.equal(result.ok, true)
    const value = parseJSON(result.text), before = parseJSON(text)
    assert.equal(value.cache.mode, mode)
    assert.equal(stringifyJSON(value.bgm), stringifyJSON(before.bgm))
    assert.equal(stringifyJSON(value.future), stringifyJSON(before.future))
    assert.equal(stringifyJSON(value.cache.future), '9007199254740993')
    const reset = updateKernelConfigField(result.text, 'cache.mode', undefined)
    assert.equal(Object.hasOwn(reset.value.cache, 'mode'), false)
    assert.equal(stringifyJSON(reset.value), stringifyJSON(before))
  }
  assert.equal(Object.hasOwn(updateKernelConfigField(text, 'asr.language', 'Chinese').value.cache, 'mode'), false)
  const blocked = updateKernelConfigField('{"cache":false,"bgm":{"repo":"keep"}}', 'cache.mode', 'core')
  assert.equal(blocked.text, '{"cache":false,"bgm":{"repo":"keep"}}')
  assert.equal(blocked.ok, false)
})

test('invalid JSON, nonobject roots and nontext inputs receive shape errors without echoing secrets', () => {
  for (const text of ['', '{', '[]', 'null', 'true', '42', '"text"', '{"asr":', '{} trailing', undefined, {}]) {
    const result = checkKernelConfig(text)
    assert.equal(result.ok, false, String(text))
    assert.ok(errorAt(result, '$'))
  }
  const secret = 'SECRET_NEVER_ECHO'
  assert.equal(JSON.stringify(checkKernelConfig(`{"key":"${secret}" broken` ).errors).includes(secret), false)
})

test('known sections, string types and nullable fallback are distinguished', () => {
  for (const value of [false, 1, [], 'x', null]) assert.equal(check({ asr: value }).ok, false)
  for (const value of [true, 4, [], {}]) assert.ok(errorAt(check({ asr: { python: value } }), 'asr.python'))
  assert.equal(check({ asr: { python: null, base_url: null, enabled: null, vram_budget_mib: null, device_map: null } }).ok, true)
  assert.equal(check({ asr: { language: '', python: '', device_map: '' } }).ok, true)
})

test('boolean strings are visible compatibility warnings, not silently rewritten', () => {
  for (const text of ['false', 'YES', 'on', ' true ', 'maybe', '']) {
    const result = check({ asr: { enabled: text }, imgen: { confirm_before_submit: text } })
    assert.equal(result.ok, true)
    assert.ok(warningAt(result, 'asr.enabled'))
    assert.ok(warningAt(result, 'imgen.confirm_before_submit'))
    assert.equal(result.value.asr.enabled, text)
  }
  for (const enabled of [0, 1, [], {}]) assert.ok(errorAt(check({ asr: { enabled } }), 'asr.enabled'))
  for (const enabled of [false, true]) assert.deepEqual(check({ asr: { enabled } }).warnings, [])
})

test('ASR URL constraints mirror static consumer checks and never probe services', () => {
  for (const base_url of ['http://127.0.0.1:18085', 'http://localhost', 'http://[::1]:18085/custom']) assert.equal(check({ asr: { base_url } }).ok, true)
  for (const base_url of ['https://localhost:18085', 'http://localhost:18081', 'http://example.test:018081', 'localhost:18085', 'http:localhost', 'file:///tmp/a', 'http://', 'http://x:99999']) {
    assert.ok(errorAt(check({ asr: { base_url } }), 'asr.base_url'), base_url)
  }
  assert.equal(check({ comfyui: { base_url: 'https://example.invalid' }, imgen: { api_base: 'https://example.invalid/v1' } }).ok, true)
  assert.ok(warningAt(check({ imgen: { api_base: 'https://user:secret@example.invalid' } }), 'imgen.api_base'))
})

test('path checks do not invent existence, absolute-path or enum constraints', () => {
  assert.equal(check({ asr: { python: './not-installed', model_dir: '~/models', device_map: 'future-device' }, tts: { engine: 'future-engine' }, bgm: { cli: 'env SOME=1 /path/bgm --flag "a b"' } }).ok, true)
  assert.ok(errorAt(check({ tts: { repo: 'a\0b' } }), 'tts.repo'))
})

test('ASR resource bounds are positive numeric rather than invented integer bounds', () => {
  for (const vram_budget_mib of [0, -1, false, '12', {}, []]) assert.ok(errorAt(check({ asr: { vram_budget_mib } }), 'asr.vram_budget_mib'))
  for (const vram_budget_mib of [0.1, 1, 1234.5]) assert.equal(check({ asr: { vram_budget_mib } }).ok, true)
  assert.ok(errorAt(check({ asr: { device_map: 1 } }), 'asr.device_map'))
  assert.equal(check({ imgen: { price_per_image: -1 } }).ok, true)
  assert.ok(warningAt(check({ imgen: { price_per_image: '0.2' } }), 'imgen.price_per_image'))
  assert.ok(warningAt(check({ imgen: { price_per_image: 'Infinity' } }), 'imgen.price_per_image'))
  assert.ok(errorAt(check({ imgen: { price_per_image: 'not-a-number' } }), 'imgen.price_per_image'))
})

test('ASR retired key respects real non-null migration guard', () => {
  for (const ime_root of ['', false, 0, '~/old', {}]) assert.ok(errorAt(check({ asr: { ime_root } }), 'asr.ime_root'))
  assert.equal(check({ asr: { ime_root: null } }).ok, true)
  assert.ok(warningAt(check({ asr: { ime_root: null } }), 'asr.ime_root'))
})

test('external registrations are shape-only, exact-true enabled, with no parent traversal', () => {
  const result = check({ external_apps: ['~/uninstalled', { path: '/not-installed', enabled: false }], external_plugins: [{ path: './plugin', enabled: true, future: 1 }] })
  assert.equal(result.ok, true)
  assert.ok(warningAt(result, 'external_plugins[0].future'))
  assert.ok(warningAt(check({ external_plugins: [{ path: '/plugin', enabled: 'true' }] }), 'external_plugins[0].enabled'))
  for (const entry of [null, false, 1, {}, { path: 1 }]) assert.ok(errorAt(check({ external_plugins: [entry] }), 'external_plugins[0]'))
  assert.ok(errorAt(check({ external_apps: {} }), 'external_apps'))
  assert.ok(errorAt(check({ external_apps: ['../plugin'] }), 'external_apps[0]'))
  assert.ok(errorAt(check({ external_plugins: [{ path: '/x/../y', enabled: false }] }), 'external_plugins[0].path'))
})

test('unknown fields warn once at their boundary, preserve nested JSON, and do not masquerade as full semantic validation', () => {
  const text = '{ "future": {"nested": [1, true, null]}, "asr": {"future_option": {"x": 9}}, "_comment": "notes" }\n'
  const result = checkKernelConfig(text)
  assert.equal(result.ok, true)
  assert.deepEqual(result.warnings.map(item => item.path), ['future', 'asr.future_option'])
  assert.equal(result.text, text)
  assert.deepEqual(result.value.future, { nested: [1, true, null] })
})

test('checking never reformats textarea and form updates preserve every unknown numeric token', () => {
  const tokens = ['9007199254740993', '123456789012345678901234567890', '-9007199254740993', '0.123456789012345678901', '1e+100', '-0', '1e-999', '1e309']
  const text = `{ "unknown": [${tokens.join(',')}], "lookalike": {"rawJSON":"9007199254740993"}, "asr": {"enabled":false} }\n`
  assert.equal(checkKernelConfig(text).text, text)
  const result = updateKernelConfigField(text, 'asr.enabled', true)
  assert.equal(result.ok, true)
  assert.equal(result.text.endsWith('\n'), true)
  const value = parseJSON(result.text)
  assert.equal(value.asr.enabled, true)
  assert.equal(stringifyJSON(value.unknown), `[${tokens.join(',')}]`)
  assert.deepEqual(value.lookalike, { rawJSON: '9007199254740993' })
})

test('no native source/rawJSON support fails closed without rounded form output', () => {
  const raw = JSON.rawJSON
  try {
    JSON.rawJSON = undefined
    const text = '{"future":9007199254740993}'
    const checked = checkKernelConfig(text)
    assert.equal(checked.ok, false)
    assert.match(checked.errors[0].message, /source\/rawJSON/)
    assert.equal(updateKernelConfigField(text, 'asr.enabled', true).text, text)
  } finally { JSON.rawJSON = raw }
})

test('form update adds one override, distinguishes false/empty/null from unset, and never injects template defaults', () => {
  const added = updateKernelConfigField('{}', 'asr.enabled', false)
  assert.deepEqual(added.value, { asr: { enabled: false } })
  const empty = updateKernelConfigField(added.text, 'asr.language', '')
  assert.equal(empty.value.asr.language, '')
  const nullable = updateKernelConfigField(empty.text, 'asr.python', null)
  assert.equal(nullable.value.asr.python, null)
  const removed = updateKernelConfigField(nullable.text, 'asr.enabled', undefined)
  assert.equal(Object.hasOwn(removed.value.asr, 'enabled'), false)
  assert.equal(removed.value.asr.language, '')
  assert.equal(updateKernelConfigField('{}', 'asr.enabled', undefined).text, '{}')
})

test('a form edit can repair one error at a time while keeping other errors and unknown values visible', () => {
  const result = updateKernelConfigField('{"asr":{"language":1,"device_map":4},"future":42}', 'asr.language', 'Chinese')
  assert.equal(result.ok, false)
  assert.equal(result.value.asr.language, 'Chinese')
  assert.ok(errorAt(result, 'asr.device_map'))
  assert.equal(result.value.future, 42)
  const text = '{"asr":7}'
  assert.equal(updateKernelConfigField(text, 'asr.language', 'Chinese').text, text)
})

test('prototype pollution is refused in paths, documents and replacement values without mutation', () => {
  for (const path of ['__proto__.polluted', 'asr.constructor.polluted', 'prototype', 'asr..language', '', ['asr', 'enabled']]) {
    assert.equal(updateKernelConfigField('{}', path, true).text, '{}')
    assert.equal(updateKernelConfigField('{}', path, true).ok, false)
  }
  for (const text of ['{"__proto__":{"polluted":true}}', '{"future":{"constructor":{"prototype":{"polluted":true}}}}']) {
    assert.equal(checkKernelConfig(text).ok, false)
    assert.equal(updateKernelConfigField(text, 'asr.enabled', true).text, text)
  }
  const replacement = JSON.parse('{"__proto__":{"polluted":true}}')
  assert.equal(updateKernelConfigField('{}', 'future', replacement).text, '{}')
  assert.equal({}.polluted, undefined)
})

test('unsafe numeric and non-JSON replacement values are rejected instead of silently rounded or dropped', () => {
  const cyclic = {}; cyclic.self = cyclic
  const accessor = Object.defineProperty({}, 'x', { get() { throw new Error('MUST NOT RUN') }, enumerable: true })
  for (const value of [9007199254740993, NaN, Infinity, 1n, () => {}, Symbol('x'), new Date(), cyclic, accessor, { x: undefined }, [undefined], new Array(2)]) {
    const result = updateKernelConfigField('{}', 'future', value)
    assert.equal(result.ok, false, String(typeof value))
    assert.equal(result.text, '{}')
  }
  const result = updateKernelConfigField('{}', 'future', JSON.rawJSON('9007199254740993'))
  assert.equal(result.ok, true)
  assert.match(result.text, /9007199254740993/)
  const precise = updateKernelConfigField('{}', 'asr.vram_budget_mib', parseJSON('1.0000000000000001'))
  assert.equal(precise.ok, true)
  assert.match(precise.text, /1\.0000000000000001/)
  const extra = [1]; extra.other = 2
  const sparseWithExtra = new Array(2); sparseWithExtra[0] = 1; sparseWithExtra.other = 2
  const hidden = Object.defineProperty({}, 'hidden', { value: 1 })
  for (const value of [extra, sparseWithExtra, hidden, { [Symbol('key')]: 1 }]) {
    assert.equal(updateKernelConfigField('{}', 'future', value).text, '{}')
    assert.equal(updateKernelConfigField('{}', 'future', value).ok, false)
  }
})

const nestedText = depth => '{"x":'.repeat(depth) + '0' + '}'.repeat(depth)
const assertBudgetFailure = (result, text, pattern = /安全上限/) => {
  assert.equal(result.ok, false)
  assert.equal(result.text, text)
  assert.equal(Object.hasOwn(result, 'value'), false)
  assert.ok(result.errors.some(item => pattern.test(item.message)), JSON.stringify(result.errors))
  assert.ok(result.errors.length + result.warnings.length <= KERNEL_CONFIG_LIMITS.maxDiagnostics)
}

test('UTF-8 input budget accepts exact byte boundary and refuses larger text before parsing', () => {
  const text = '{"blob":"' + 'a'.repeat(KERNEL_CONFIG_LIMITS.maxBytes - 11) + '"}'
  assert.equal(new TextEncoder().encode(text).byteLength, KERNEL_CONFIG_LIMITS.maxBytes)
  assert.equal(checkKernelConfig(text).ok, true)
  const oversized = text + ' '
  assertBudgetFailure(checkKernelConfig(oversized), oversized)
  assertBudgetFailure(updateKernelConfigField(oversized, 'asr.enabled', true), oversized)
  const unicode = '{"blob":"' + '汉'.repeat(Math.floor(KERNEL_CONFIG_LIMITS.maxBytes / 3)) + '"}'
  assert.ok(unicode.length < KERNEL_CONFIG_LIMITS.maxBytes)
  assertBudgetFailure(checkKernelConfig(unicode), unicode)
  const parse = JSON.parse
  try {
    JSON.parse = () => { throw new Error('MUST NOT PARSE OVERSIZED INPUT') }
    assertBudgetFailure(checkKernelConfig(oversized), oversized)
  } finally { JSON.parse = parse }
})

test('iterative tree depth budget is inclusive, fail-closed and safe for parser stack failures', () => {
  assert.equal(checkKernelConfig(nestedText(KERNEL_CONFIG_LIMITS.maxDepth)).ok, true)
  const beyond = nestedText(KERNEL_CONFIG_LIMITS.maxDepth + 1)
  assertBudgetFailure(checkKernelConfig(beyond), beyond)
  assertBudgetFailure(updateKernelConfigField(beyond, 'asr.enabled', true), beyond)
  const nativeTooDeep = nestedText(10000)
  const result = checkKernelConfig(nativeTooDeep)
  assert.equal(result.ok, false)
  assert.equal(result.text, nativeTooDeep)
  assert.equal(Object.hasOwn(result, 'value'), false)
})

test('tree node budget includes containers and primitive leaves, including unknown subtrees', () => {
  const exact = JSON.stringify({ future: new Array(KERNEL_CONFIG_LIMITS.maxNodes - 2).fill(0) })
  assert.equal(checkKernelConfig(exact).ok, true)
  const beyond = JSON.stringify({ future: new Array(KERNEL_CONFIG_LIMITS.maxNodes - 1).fill(0) })
  assertBudgetFailure(checkKernelConfig(beyond), beyond)
  assertBudgetFailure(updateKernelConfigField(beyond, 'asr.enabled', true), beyond)
})

test('combined diagnostic budget stops checking and never labels a truncated check successful', () => {
  const make = count => JSON.stringify(Object.fromEntries(Array.from({ length: count }, (_, i) => [`future${i}`, false])))
  const exact = checkKernelConfig(make(KERNEL_CONFIG_LIMITS.maxDiagnostics - 1))
  assert.equal(exact.ok, true)
  assert.equal(exact.warnings.length, KERNEL_CONFIG_LIMITS.maxDiagnostics - 1)
  const overflow = make(1000)
  const result = checkKernelConfig(overflow)
  assertBudgetFailure(result, overflow)
  assert.equal(result.errors.length + result.warnings.length, KERNEL_CONFIG_LIMITS.maxDiagnostics)
  const mixed = JSON.stringify({ external_plugins: Array.from({ length: 1000 }, () => ({ path: '/uninstalled', enabled: 'true', extra: 1 })) })
  assertBudgetFailure(checkKernelConfig(mixed), mixed)
  assertBudgetFailure(updateKernelConfigField(overflow, 'asr.enabled', true), overflow)
  const longKey = 'x'.repeat(2000)
  assert.ok(check({ [longKey]: 1 }).warnings.every(item => item.path.length <= KERNEL_CONFIG_LIMITS.maxDiagnosticPath))
})

test('replacement, field path, combined tree and reserialized byte budgets refuse atomically', () => {
  const original = '{"future":9007199254740993}'
  let nested = 1
  for (let index = 0; index <= KERNEL_CONFIG_LIMITS.maxDepth; index++) nested = { x: nested }
  for (const value of [nested, new Array(KERNEL_CONFIG_LIMITS.maxNodes + 1).fill(0), 'a'.repeat(KERNEL_CONFIG_LIMITS.maxBytes + 1), '汉'.repeat(Math.floor(KERNEL_CONFIG_LIMITS.maxBytes / 3))]) {
    const result = updateKernelConfigField(original, 'other', value)
    assert.equal(result.ok, false)
    assert.equal(result.text, original)
    assert.equal(Object.hasOwn(result, 'value'), false)
  }
  for (const path of ['x'.repeat(KERNEL_CONFIG_LIMITS.maxDiagnosticPath + 1), new Array(KERNEL_CONFIG_LIMITS.maxDepth + 1).fill('x').join('.')]) {
    const result = updateKernelConfigField(original, path, true)
    assert.equal(result.ok, false)
    assert.equal(result.text, original)
    assert.ok(result.errors[0].path.length <= KERNEL_CONFIG_LIMITS.maxDiagnosticPath)
  }
  // Each half is below budget; their combined tree must still be rejected.
  const existing = JSON.stringify({ future: new Array(11000).fill(0) })
  const result = updateKernelConfigField(existing, 'other', new Array(11000).fill(0))
  assertBudgetFailure(result, existing)
})
