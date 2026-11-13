import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { homedir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { createKernelManagement, validateKernelConfigSelection, INSTALL_SCRIPT, KERNEL_CONFIG_LIMIT, KERNEL_WHEEL_LIMIT } from '../src/kernel-management.mjs'
import { readConfiguration, validateVtxConfigFile, isVolatileConfig } from '../src/configuration.mjs'
import { updateKernelConfigField } from '../src/kernel-config.mjs'
import { TimelineReader } from '../src/protocol.mjs'
import { createPreviewHost } from '../src/host.mjs'
import { MemoryFs as TimelineFs, fixtureSubprocess } from './helpers.mjs'

const digest = data => `sha256:${createHash('sha256').update(data).digest('hex')}`
class Fs {
  constructor() { this.entries = new Map(); this.links = new Map(); this.reads = []; this.writes = []; this.version = 0; this.add('/', null, 'directory'); this.add(homedir(), null, 'directory'); this.add('/usr/bin/python3', 'system'); this.add('/opt/trusted/bin/vtx', 'kernel') }
  add(path, content, type = 'file') {
    if (path !== '/' && !this.entries.has(dirname(path))) this.add(dirname(path), null, 'directory')
    this.entries.set(path, { type, content: content === null ? null : Buffer.from(content), version: `v${++this.version}` })
  }
  async resolve(path) { const key = this.links.get(path) ?? resolve(path); return { targetKey: key, displayPath: key } }
  processPath(target) { return target.targetKey }
  processPathFromHostPath(path) { return path }
  contains(a, b) { return a.targetKey === b.targetKey || b.targetKey.startsWith(a.targetKey === '/' ? '/' : `${a.targetKey}/`) }
  async stat(target) { const info = this.entries.get(target.targetKey); return info && { type: info.type, version: info.version, size: info.content?.byteLength } }
  async lstat(path) { return this.links.has(path) ? { type: 'symlink', version: 'link' } : this.stat({ targetKey: path }) }
  async readBytes(target, signal, max) { signal?.throwIfAborted(); this.reads.push({ path: target.targetKey, max }); const data = this.entries.get(target.targetKey).content; if (data.byteLength > max) throw Object.assign(new Error('large'), { code: 'FS_TOO_LARGE' }); return data }
  async writeText(target, text, expected, signal) {
    signal?.throwIfAborted(); this.writes.push({ path: target.targetKey, text, expected })
    await this.beforeWrite?.(target, text, expected)
    const current = this.entries.get(target.targetKey)
    if ((expected.kind === 'createIfAbsent' && current) || (expected.kind === 'replaceIfVersion' && current?.version !== expected.version)) throw Object.assign(new Error('stale'), { code: 'FS_STALE_VERSION' })
    this.add(target.targetKey, text)
    return { version: this.entries.get(target.targetKey).version, after: text, before: current?.content?.toString() ?? null }
  }
  async listDir(target) {
    return [...this.entries].filter(([path]) => path !== target.targetKey && dirname(path) === target.targetKey).map(([path, info]) => ({ name: path.slice(path.lastIndexOf('/') + 1), type: info.type, target: { targetKey: path } }))
  }
}
function setup(options = {}) {
  const fs = new Fs(), calls = []
  const config = { vtxExecutable: '/opt/trusted/bin/vtx', vtxConfigFile: '/settings/config.json', ...options.config }
  const subprocess = { resolveExecutable: async path => path, spawn(spec) {
    calls.push(spec)
    let fulfill
    const done = options.hold ? new Promise(resolveDone => { fulfill = resolveDone }) : Promise.resolve({ exitCode: options.exitCode ?? 0 })
    const handle = { done, terminated: 0, terminate() { this.terminated++; fulfill?.({ exitCode: null }) }, waitForExit: async () => options.cleanupFails ? false : true,
      collected: { stdout: { readFrom: () => ({ text: options.stdout ?? 'installed', lossy: false }) }, stderr: { readFrom: () => ({ text: options.stderr ?? '', lossy: false }) } } }
    calls.handle = handle
    return handle
  } }
  const ctx = { fs, subprocess, sessions: { list: () => [{ id: 'one' }] }, workingDirectory: { get: () => '/workspace' } }
  const service = createKernelManagement(ctx, config)
  const policy = { mode: options.policyMode ?? 'danger-full-access', workspaceRoot: '/workspace' }
  if (!options.noPolicy) service.setPolicyResolver(() => policy)
  const fetch = data => service.fetch(new Request('http://owner/api/framivra/kernel-management', { method: 'POST', body: JSON.stringify(data) }))
  const rawRequest = async data => { const res = await fetch(data); return { status: res.status, ...await res.json() } }
  // Existing save scenarios use the same explicit read→token handshake as UI.
  // Security tests use rawRequest to deliberately omit or reuse stale tokens.
  const request = async data => {
    if (data.op === 'config-save' && !Object.hasOwn(data, 'selectionToken')) data = { ...data, selectionToken: (await rawRequest({ op: 'config-read' })).selectionToken }
    return rawRequest(data)
  }
  return { fs, calls, service, request, rawRequest, fetch, config, ctx, policy }
}
async function status(env, jobId) {
  // Settle fake capability promises, not a real installation or live DSH process.
  for (let i = 0; i < 40; i++) {
    await new Promise(resolveNext => setImmediate(resolveNext))
    const value = (await env.request({ op: 'install-status', jobId })).jobs[0]
    if (!['queued', 'running'].includes(value.state)) return value
  }
  assert.fail('mock installation did not settle')
}
function wheel(env) { env.fs.add('/downloads/framivra-2.2.0-py3-none-any.whl', 'PKmock owner-trusted wheel'); return '/downloads/framivra-2.2.0-py3-none-any.whl' }

test('explicit VTX_CONFIG extends fingerprints; empty defaults remain exactly backward compatible', () => {
  const defaults = readConfiguration({})
  assert.deepEqual({ ...defaults }, { vtxExecutable: 'vtx', trustedPath: '/usr/local/bin:/usr/bin:/bin', fingerprint: '["vtx","/usr/local/bin:/usr/bin:/bin"]' })
  assert.equal(readConfiguration({ vtxConfigFile: '' }).fingerprint, defaults.fingerprint)
  assert.notEqual(readConfiguration({ vtxConfigFile: '/settings/config.json' }).fingerprint, defaults.fingerprint)
  assert.equal(isVolatileConfig({ vtxConfigFile: { get: () => '' } }), true)
  for (const path of ['relative.json', '/x/../y.json', '/x/./y.json', '/bad.txt', '/bad\0.json', null]) assert.throws(() => validateVtxConfigFile(path))
})

test('reader forwards only the selected VTX_CONFIG and explicitly clears ambient default', async () => {
  for (const selected of ['', '/settings/config.json']) {
    let spawned
    const subprocess = { spawn(spec) { spawned = spec; return { done: Promise.resolve({ exitCode: 0 }), terminate() {}, waitForExit: async () => true, collected: { stdout: { readFrom: () => ({ text: '{}', lossy: false }) } } } } }
    const reader = new TimelineReader(subprocess, { vtxConfigFile: selected }, async () => {})
    await reader.command('/usr/bin/vtx', '/workspace/timeline.json', ['inspect'], new AbortController().signal, JSON.parse)
    assert.equal(spawned.env.VTX_CONFIG, selected || undefined)
    assert(Object.hasOwn(spawned.env, 'VTX_CONFIG'))
    await reader.dispose()
  }
})

test('changing only VTX_CONFIG revokes an old preview grant', async () => {
  const fs = new TimelineFs(), subprocess = fixtureSubprocess()
  let configPath = ''
  const session = { id: 'one' }
  const host = createPreviewHost({ fs, subprocess, sessions: { get: () => session }, workingDirectory: { get: () => '/workspace' } }, { vtxExecutable: '/opt/trusted/vtx', vtxConfigFile: { get: () => configPath } })
  const control = data => host.control(new Request('http://owner/control', { method: 'POST', body: JSON.stringify({ sessionId: 'one', ...data }) }))
  try {
    const opened = await (await control({ op: 'open', timeline: 'timeline.json' })).json()
    configPath = '/settings/other.json'
    const denied = await control({ op: 'inspect', grant: opened.grant })
    assert.equal(denied.status, 409)
    assert.equal((await denied.json()).error, 'CONFIG_CHANGED')
  } finally { await host.dispose() }
})

test('scan reads only bounded common roots and current/trusted paths; never spawns or trusts', async () => {
  const env = setup()
  env.fs.add('/usr/bin/vtx', 'candidate')
  env.fs.add(join(homedir(), '.local/share/uv/tools/framivra/bin/vtx'), 'candidate')
  env.fs.add('/workspace/hidden/bin/vtx', 'not scanned')
  const result = await env.request({ op: 'scan' })
  assert.equal(result.ok, true)
  assert(result.candidates.some(item => item.path === '/usr/bin/vtx'))
  assert(result.candidates.some(item => item.source === 'common-venv'))
  assert(!result.candidates.some(item => item.path.startsWith('/workspace')))
  assert.equal(env.calls.length, 0)
  assert.equal(env.fs.reads.length, 0)
  assert.equal(env.fs.writes.length, 0)
  assert.equal(result.suggestedConfigFile, join(homedir(), '.config/framivra/config.json'))
  assert.equal(env.config.vtxExecutable, '/opt/trusted/bin/vtx')
  await env.service.dispose()
})

test('scan discovers the standard live video skill launcher without executing or trusting it', async () => {
  const env = setup()
  const mounted = join(homedir(), '.agents/skills/video/vtx/vtx.py')
  const source = '/source/framivra/vtx/vtx.py'
  env.fs.add(source, '#!/usr/bin/env python3')
  env.fs.links.set(mounted, source)
  try {
    const result = await env.request({ op: 'scan' })
    assert.equal(result.ok, true)
    assert.deepEqual(result.candidates.filter(item => item.source === 'video-skill-source'), [
      { path: mounted, kind: 'executable', source: 'video-skill-source' },
    ])
    assert.equal(env.calls.length, 0)
    assert.equal(env.fs.reads.length, 0)
    assert.equal(env.fs.writes.length, 0)
    assert.equal(env.config.vtxExecutable, '/opt/trusted/bin/vtx')
  } finally { await env.service.dispose() }
})

test('missing or denied video skill mounts do not hide other kernel candidates', async () => {
  for (const code of [undefined, 'FS_NOT_FOUND', 'FS_SANDBOX_DENIED', 'FS_PERMISSION_DENIED']) {
    const env = setup()
    const mounted = join(homedir(), '.agents/skills/video/vtx/vtx.py')
    const original = env.fs.resolve.bind(env.fs)
    env.fs.resolve = async path => {
      if (path === mounted && code) throw Object.assign(new Error('unavailable'), { code })
      return original(path)
    }
    try {
      const result = await env.request({ op: 'scan' })
      assert.equal(result.ok, true)
      assert(result.candidates.some(item => item.path === '/opt/trusted/bin/vtx'))
      assert(!result.candidates.some(item => item.source === 'video-skill-source'))
      assert.equal(result.warnings.length, code?.includes('DENIED') ? 1 : 0)
      assert.equal(env.calls.length, 0)
      assert.equal(env.fs.writes.length, 0)
    } finally { await env.service.dispose() }
  }
})

test('scan truncates each venv directory and rejects request roots', async () => {
  const env = setup()
  for (let i = 0; i < 40; i++) env.fs.add(join(homedir(), `.virtualenvs/env-${i}/bin/vtx`), 'candidate')
  const result = await env.request({ op: 'scan' })
  assert.equal(result.candidates.filter(item => item.source === 'common-venv').length, 24)
  assert(result.warnings.length)
  assert.equal((await env.request({ op: 'scan', root: '/workspace' })).error, 'BAD_REQUEST')
  await env.service.dispose()
})

test('unset config never guesses legacy kernel files; absent explicit path is a create draft', async () => {
  const unset = setup({ config: { vtxConfigFile: '' } })
  const result = await unset.request({ op: 'config-read' })
  assert.equal(result.error, 'CONFIG_FILE_UNSET')
  assert(result.suggestedConfigFile.endsWith('/.config/framivra/config.json'))
  assert.equal(unset.fs.reads.length, 0)
  const env = setup()
  const absent = await env.request({ op: 'config-read' })
  assert.match(absent.selectionToken, /^[a-f0-9-]{36}$/)
  const { selectionToken, ...draft } = absent
  assert.deepEqual(draft, { status: 200, ok: true, path: '/settings/config.json', text: '{}\n', hash: 'absent', exists: false })
  await unset.service.dispose(); await env.service.dispose()
})

test('config read is bounded raw UTF-8; permissions are not absence', async () => {
  const env = setup(), text = '{ "unknown": 9007199254740993123 }\r\n'
  env.fs.add('/settings/config.json', text)
  const result = await env.request({ op: 'config-read' })
  assert.equal(result.text, text)
  assert.equal(result.hash, digest(text))
  assert.equal(env.fs.reads[0].max, KERNEL_CONFIG_LIMIT)
  env.fs.stat = async () => { throw Object.assign(new Error('denied'), { code: 'FS_PERMISSION_DENIED' }) }
  assert.equal((await env.request({ op: 'config-read' })).error, 'FS_PERMISSION_DENIED')
  await env.service.dispose()
})

test('config management rejects path overrides, symlink ancestors, workspace and install files', async () => {
  const env = setup()
  assert.equal((await env.request({ op: 'config-read', path: '/secret.json' })).error, 'BAD_REQUEST')
  env.fs.links.set('/settings', '/secret')
  assert.equal((await env.request({ op: 'config-read' })).error, 'UNSAFE_PATH')
  env.fs.links.clear(); env.config.vtxConfigFile = '/workspace/config.json'
  assert.equal((await env.request({ op: 'config-read' })).error, 'WORKSPACE_PATH')
  env.config.vtxConfigFile = '/opt/trusted/config.json'
  assert.equal((await env.request({ op: 'config-read' })).error, 'INSTALL_CONFIG_PATH')
  env.config.vtxConfigFile = '/settings/config.json'; env.fs.links.set('/settings/config.json', '/other.json')
  assert.equal((await env.request({ op: 'config-read' })).error, 'UNSAFE_PATH')
  await env.service.dispose()
})

test('save needs explicit confirmation and successful static validation, preserves original raw JSON', async () => {
  const env = setup(), text = '{"future":9007199254740993123123}\n'
  assert.equal((await env.request({ op: 'config-save', text, expectedHash: 'absent' })).error, 'CONFIRM_REQUIRED')
  assert.equal((await env.request({ op: 'config-save', text: '{', expectedHash: 'absent', confirmed: true })).error, 'CONFIG_INVALID')
  assert.equal(env.fs.writes.length, 0)
  const checked = await env.request({ op: 'config-check', text })
  assert.equal(checked.validation.ok, true)
  const saved = await env.request({ op: 'config-save', text, expectedHash: 'absent', confirmed: true })
  assert.equal(saved.ok, true)
  assert.equal(saved.hash, digest(text))
  assert.equal(env.fs.entries.get('/settings/config.json').content.toString(), text)
  assert.deepEqual(env.fs.writes[0].expected, { kind: 'createIfAbsent' })
  await env.service.dispose()
})

test('cache config uses existing global read/confirm/CAS path with BGM and unknown values intact', async () => {
  const env = setup()
  try {
    const original = '{"bgm":{"cli":"bgm --keep","enabled":false},"cache":{"future":9007199254740993},"future":1e+100}\n'
    env.fs.add('/settings/config.json', original)
    const initial = await env.rawRequest({ op: 'config-read' })
    assert.equal(initial.text, original)
    assert.equal(env.fs.writes.length, 0)
    const version = env.fs.entries.get('/settings/config.json').version
    const text = updateKernelConfigField(initial.text, 'cache.mode', 'off').text
    const payload = { op: 'config-save', text, expectedHash: initial.hash, selectionToken: initial.selectionToken }
    assert.equal((await env.rawRequest(payload)).error, 'CONFIRM_REQUIRED')
    assert.equal(env.fs.writes.length, 0)
    const before = readConfiguration(env.config).fingerprint
    const saved = await env.rawRequest({ ...payload, confirmed: true })
    assert.equal(saved.ok, true)
    assert.deepEqual(env.fs.writes[1].expected, { kind: 'replaceIfVersion', version })
    assert.equal(env.fs.entries.get(saved.backupPath).content.toString(), original)
    assert.equal(env.fs.entries.get('/settings/config.json').content.toString(), text)
    assert.match(text, /9007199254740993/)
    assert.match(text, /1e\+100/)
    assert.deepEqual(JSON.parse(text).bgm, { cli: 'bgm --keep', enabled: false })
    assert.notEqual(readConfiguration(env.config).fingerprint, before)
    assert.equal(env.calls.length, 0) // No render, cleanup command or session environment mutation.
  } finally { await env.service.dispose() }
})

test('invalid cache modes fail Host validation before backup or publication', async () => {
  const env = setup()
  try {
    const initial = await env.rawRequest({ op: 'config-read' })
    for (const mode of [null, false, '', 'CORE', 'future']) {
      const text = JSON.stringify({ cache: { mode } })
      const checked = await env.rawRequest({ op: 'config-check', text })
      assert.equal(checked.validation.ok, false)
      assert.ok(checked.validation.errors.some(item => item.path === 'cache.mode'))
      const saved = await env.rawRequest({ op: 'config-save', text, expectedHash: initial.hash, selectionToken: initial.selectionToken, confirmed: true })
      assert.equal(saved.error, 'CONFIG_INVALID')
    }
    assert.equal(env.fs.writes.length, 0)
    assert.equal(env.calls.length, 0)
  } finally { await env.service.dispose() }
})

test('replacement uses authoritative CAS version and unique exclusive backup', async () => {
  const env = setup(), before = '{ "unknown": 1 }\r\n'
  env.fs.add('/settings/config.json', before)
  const version = env.fs.entries.get('/settings/config.json').version
  const saved = await env.request({ op: 'config-save', text: '{"unknown":2}', expectedHash: digest(before), confirmed: true })
  assert.equal(saved.ok, true)
  assert.match(saved.backupPath, /^\/settings\/config.json.backup-[a-f0-9-]+\.json$/)
  assert.equal(env.fs.entries.get(saved.backupPath).content.toString(), before)
  assert.deepEqual(env.fs.writes[0].expected, { kind: 'createIfAbsent' })
  assert.deepEqual(env.fs.writes[1].expected, { kind: 'replaceIfVersion', version })
  await env.service.dispose()
})

test('stale hash and concurrent publication preserve external file and browser draft', async () => {
  const env = setup(); env.fs.add('/settings/config.json', '{}')
  assert.equal((await env.request({ op: 'config-save', text: '{"draft":1}', expectedHash: 'sha256:stale', confirmed: true })).error, 'CONFIG_CONFLICT')
  assert.equal(env.fs.writes.length, 0)
  env.fs.beforeWrite = target => { if (target.targetKey === '/settings/config.json') env.fs.add('/settings/config.json', '{"external":true}') }
  const saved = await env.request({ op: 'config-save', text: '{"draft":1}', expectedHash: digest('{}'), confirmed: true })
  assert.equal(saved.error, 'CONFIG_CONFLICT')
  assert.equal(env.fs.entries.get('/settings/config.json').content.toString(), '{"external":true}')
  await env.service.dispose()
})

test('same-path GUI save invalidates all readers sharing its config source, not other hosts', async () => {
  const env = setup(), independent = readConfiguration({ vtxConfigFile: '/settings/config.json' }).fingerprint
  const reader = new TimelineReader({ spawn() { assert.fail('stale snapshot cannot spawn') } }, env.config)
  const before = reader.snapshotConfig()
  assert.equal((await env.request({ op: 'config-save', text: '{"updated":true}', expectedHash: 'absent', confirmed: true })).ok, true)
  assert.notEqual(reader.snapshotConfig().fingerprint, before.fingerprint)
  assert.equal(readConfiguration({ vtxConfigFile: '/settings/config.json' }).fingerprint, independent)
  await assert.rejects(reader.command('/opt/trusted/bin/vtx', '/workspace/timeline.json', ['inspect'], new AbortController().signal, JSON.parse, undefined, false, false, before), error => error.code === 'CONFIG_CHANGED')
  await env.service.dispose(); await reader.dispose()
})

test('ambiguous post-publication error reports outcome_unknown with hashes, never safe-to-retry failure', async () => {
  const env = setup(), originalWrite = env.fs.writeText.bind(env.fs), before = readConfiguration(env.config).fingerprint
  env.fs.writeText = async (...args) => { await originalWrite(...args); throw Object.assign(new Error('response lost after commit'), { code: 'FS_IO_ERROR' }) }
  const text = '{"updated":true}'
  const saved = await env.request({ op: 'config-save', text, expectedHash: 'absent', confirmed: true })
  assert.equal(saved.code, 'outcome_unknown')
  assert.equal(saved.intendedHash, digest(text))
  assert.equal(saved.expectedHash, 'absent')
  assert.equal(env.fs.entries.get('/settings/config.json').content.toString(), text)
  assert.notEqual(readConfiguration(env.config).fingerprint, before)
  await env.service.dispose()
})

test('validation response is bounded metadata only, never echoes config secrets or parsed document', async () => {
  const env = setup(), text = '{"api_key":"DO_NOT_ECHO_THIS_VALUE"}'
  const checked = await env.request({ op: 'config-check', text })
  assert.equal(checked.validation.text, undefined)
  assert.equal(checked.validation.value, undefined)
  assert(!JSON.stringify(checked).includes('DO_NOT_ECHO_THIS_VALUE'))
  const large = Object.fromEntries(Array.from({ length: 300 }, (_, index) => [`future_${index}`, index]))
  const result = await env.request({ op: 'config-check', text: JSON.stringify(large) })
  // The shared checker now reserves one of its 200 slots for a fail-closed
  // diagnostic-budget error rather than silently discarding remaining issues.
  assert.equal(result.validation.warnings.length + result.validation.errors.length, 200)
  assert.equal(result.validation.ok, false)
  assert.match(result.validation.errors.at(-1).message, /诊断数量.*安全上限/)
  await env.service.dispose()
})

test('saved selection changed during backup prevents publication', async () => {
  const env = setup(); env.fs.add('/settings/config.json', '{}')
  env.fs.beforeWrite = () => { env.config.vtxConfigFile = '/settings/new.json' }
  assert.equal((await env.request({ op: 'config-save', text: '{"draft":1}', expectedHash: digest('{}'), confirmed: true })).error, 'CONFIG_CHANGED')
  assert.equal(env.fs.entries.get('/settings/config.json').content.toString(), '{}')
  await env.service.dispose()
})

test('preview hashes bounded local framivra wheel without executing; confirmation is single use and path-free', async () => {
  const env = setup(), wheelPath = wheel(env)
  const plan = await env.request({ op: 'install-preview', wheelPath })
  assert.equal(plan.ok, true)
  assert.equal(plan.hash, digest(env.fs.entries.get(wheelPath).content))
  assert.equal(env.fs.reads[0].max, KERNEL_WHEEL_LIMIT)
  assert.equal(env.calls.length, 0)
  assert(plan.targetDir.startsWith(join(homedir(), '.local/share/framivra-dsh/kernels/kernel-')))
  assert.equal((await env.request({ op: 'install-confirm', nonce: plan.nonce, hash: plan.hash, targetDir: '/tmp/evil' })).error, 'BAD_REQUEST')
  const confirmed = await env.request({ op: 'install-confirm', nonce: plan.nonce, hash: plan.hash })
  assert.equal(confirmed.ok, true)
  const job = await status(env, confirmed.jobId)
  assert.equal(job.state, 'succeeded')
  assert.equal(job.vtxPath, join(plan.targetDir, 'bin/vtx'))
  assert.equal(env.config.vtxExecutable, '/opt/trusted/bin/vtx')
  assert.equal(env.config.vtxConfigFile, '/settings/config.json')
  assert.equal((await env.request({ op: 'install-confirm', nonce: plan.nonce, hash: plan.hash })).error, 'BAD_NONCE')
  const spec = env.calls[0]
  assert.equal(spec.argv[0], '/usr/bin/python3')
  assert.deepEqual(spec.argv.slice(1, 3), ['-I', '-c'])
  assert.equal(spec.argv[3], INSTALL_SCRIPT)
  assert(!Object.hasOwn(spec, 'shell'))
  assert.equal(spec.env.VTX_CONFIG, undefined)
  assert.equal(Buffer.from(spec.stdio.stdin.data, 'base64').toString(), 'PKmock owner-trusted wheel')
  assert(spec.argv[3].includes("'--no-index', '--no-deps'"))
  assert(env.calls.handle.terminated > 0)
  await env.service.dispose()
})

test('wrong hash burns nonce, tampered wheel never spawns, oversized/non-wheel paths rejected', async () => {
  const env = setup(), wheelPath = wheel(env)
  const plan = await env.request({ op: 'install-preview', wheelPath })
  assert.equal((await env.request({ op: 'install-confirm', nonce: plan.nonce, hash: 'wrong' })).error, 'BAD_NONCE')
  assert.equal((await env.request({ op: 'install-confirm', nonce: plan.nonce, hash: plan.hash })).error, 'BAD_NONCE')
  const next = await env.request({ op: 'install-preview', wheelPath })
  env.fs.add(wheelPath, 'PKtampered')
  const result = await env.request({ op: 'install-confirm', nonce: next.nonce, hash: next.hash })
  const job = await status(env, result.jobId)
  assert.equal(job.error.code, 'WHEEL_CHANGED')
  assert.equal(env.calls.length, 0)
  assert.equal((await env.request({ op: 'install-preview', wheelPath: '/downloads/evil.whl' })).error, 'BAD_WHEEL')
  env.fs.entries.get(wheelPath).content = Buffer.alloc(KERNEL_WHEEL_LIMIT + 1)
  assert.equal((await env.request({ op: 'install-preview', wheelPath })).error, 'FILE_TOO_LARGE')
  await env.service.dispose()
})

test('symlinked wheel and management ancestors are refused', async () => {
  const env = setup(), wheelPath = wheel(env)
  env.fs.links.set(wheelPath, '/evil.whl')
  assert.equal((await env.request({ op: 'install-preview', wheelPath })).error, 'UNSAFE_PATH')
  env.fs.links.clear(); env.fs.links.set(join(homedir(), '.local'), '/tmp/redirect')
  assert.equal((await env.request({ op: 'install-preview', wheelPath })).error, 'UNSAFE_PATH')
  assert.equal(env.calls.length, 0)
  await env.service.dispose()
})

test('install cancellation and unload terminate managed range; logs bounded/sanitized', async () => {
  const env = setup({ hold: true, stdout: '\x1b[31m' + 'x'.repeat(40000), stderr: '\x00secret' }), wheelPath = wheel(env)
  const plan = await env.request({ op: 'install-preview', wheelPath })
  const confirmed = await env.request({ op: 'install-confirm', nonce: plan.nonce, hash: plan.hash })
  for (let i = 0; i < 20 && !env.calls.length; i++) await new Promise(resolveNext => setImmediate(resolveNext))
  const live = await env.request({ op: 'install-status', jobId: confirmed.jobId })
  assert(live.jobs[0].logs.length <= 16384)
  assert(!live.jobs[0].logs.includes('\x1b'))
  assert.equal((await env.request({ op: 'install-status', cancel: true })).error, 'BAD_REQUEST')
  await env.request({ op: 'install-status', jobId: confirmed.jobId, cancel: true })
  assert.equal((await status(env, confirmed.jobId)).state, 'cancelled')
  assert(env.calls.handle.terminated > 0)
  await env.service.dispose()
  assert.equal((await env.request({ op: 'scan' })).error, 'DISPOSED')
})

test('selection token prevents same-hash and absent draft publication across config targets', async () => {
  for (const existing of [false, true]) {
    const env = setup()
    if (existing) { env.fs.add('/settings/config.json', '{}'); env.fs.add('/settings/other.json', '{}') }
    const baseline = await env.rawRequest({ op: 'config-read' })
    env.config.vtxConfigFile = '/settings/other.json'
    const result = await env.rawRequest({ op: 'config-save', text: '{"draftForA":true}', expectedHash: baseline.hash, selectionToken: baseline.selectionToken, confirmed: true })
    assert.equal(result.error, 'CONFIG_SELECTION_CHANGED')
    assert.equal(env.fs.writes.length, 0) // not even a backup of the wrong target
    assert.equal(env.fs.entries.get('/settings/other.json')?.content.toString(), existing ? '{}' : undefined)
    const missing = await env.rawRequest({ op: 'config-save', text: '{}', expectedHash: baseline.hash, confirmed: true })
    assert.equal(missing.error, 'CONFIG_SELECTION_CHANGED')
    await env.service.dispose()
  }
})

test('install fails closed for unknown/read-only/workspace-write Host policy and rechecks before spawn', async () => {
  for (const options of [{ noPolicy: true }, { policyMode: 'read-only' }, { policyMode: 'workspace-write' }]) {
    const env = setup(options), wheelPath = wheel(env)
    assert.equal((await env.request({ op: 'install-preview', wheelPath })).error, 'INSTALL_POLICY_DENIED')
    assert.equal(env.calls.length, 0)
    assert.equal(env.fs.reads.length, 0)
    await env.service.dispose()
  }
  const env = setup(), wheelPath = wheel(env), plan = await env.request({ op: 'install-preview', wheelPath })
  env.policy.mode = 'read-only'
  assert.equal((await env.request({ op: 'install-confirm', nonce: plan.nonce, hash: plan.hash })).error, 'INSTALL_POLICY_DENIED')
  assert.equal(env.calls.length, 0)
  await env.service.dispose()
})

test('every kernel command validates explicit VTX_CONFIG against shared path trust rules', async () => {
  const env = setup(), signal = new AbortController().signal
  env.fs.add('/workspace/config.json', '{}')
  env.config.vtxConfigFile = '/workspace/config.json'
  const reader = new TimelineReader({ spawn() { assert.fail('untrusted config must not spawn') } }, env.config, (snapshot, activeSignal) => validateKernelConfigSelection(env.ctx, snapshot, activeSignal))
  await assert.rejects(reader.command('/opt/trusted/bin/vtx', '/workspace/timeline.json', ['inspect'], signal, JSON.parse), error => error.code === 'WORKSPACE_PATH')
  env.config.vtxConfigFile = '/settings/config.json'; env.fs.add('/settings/config.json', '{}'); env.fs.links.set('/settings/config.json', '/workspace/config.json')
  await assert.rejects(reader.command('/opt/trusted/bin/vtx', '/workspace/timeline.json', ['inspect'], signal, JSON.parse), error => error.code === 'UNSAFE_PATH')
  const withoutValidator = new TimelineReader({ spawn() { assert.fail('no validator must not spawn') } }, env.config)
  await assert.rejects(withoutValidator.command('/opt/trusted/bin/vtx', '/workspace/timeline.json', ['inspect'], signal, JSON.parse), error => error.code === 'CONFIG_TRUST_REQUIRED')
  await reader.dispose(); await withoutValidator.dispose(); await env.service.dispose()
})

test('real PreviewHost diagnostics rejects workspace VTX_CONFIG before any kernel spawn', async () => {
  const env = setup()
  env.fs.add('/workspace/config.json', '{}')
  env.config.vtxConfigFile = '/workspace/config.json'
  const host = createPreviewHost(env.ctx, env.config)
  try {
    const denied = await host.diagnostics(new Request('http://owner/api/framivra/diagnostics', { method: 'POST' }))
    assert.equal(denied.status, 403)
    assert.equal((await denied.json()).error, 'CONFIG_INVALID')
    assert.equal(env.calls.length, 0)
  } finally { await host.dispose(); await env.service.dispose() }
})

test('missing public SessionStore enumeration never silently trusts explicit configuration', async () => {
  const env = setup()
  env.fs.add('/settings/config.json', '{}')
  env.ctx.sessions = { get() {} }
  assert.equal((await env.request({ op: 'config-read' })).error, 'SESSION_STORE_UNAVAILABLE')
  await assert.rejects(validateKernelConfigSelection(env.ctx, readConfiguration(env.config), new AbortController().signal), error => error.code === 'SESSION_STORE_UNAVAILABLE')
  await env.service.dispose()
})

test('dispose waits for an in-flight config publication even when provider ignores abort', async () => {
  const env = setup(), baseline = await env.rawRequest({ op: 'config-read' })
  let release, entered
  const gate = new Promise(resolveGate => { release = resolveGate })
  const started = new Promise(resolveStarted => { entered = resolveStarted })
  const original = env.fs.writeText.bind(env.fs)
  env.fs.writeText = async (target, text, intent) => { entered(); await gate; return original(target, text, intent) }
  const saving = env.rawRequest({ op: 'config-save', text: '{"lateCommit":true}', expectedHash: baseline.hash, selectionToken: baseline.selectionToken, confirmed: true })
  await started
  let disposed = false
  const disposal = env.service.dispose().then(() => { disposed = true })
  await new Promise(resolveNext => setImmediate(resolveNext))
  assert.equal(disposed, false)
  release()
  assert.equal((await saving).ok, true)
  await disposal
  assert.equal(disposed, true)
  assert.equal(env.fs.entries.get('/settings/config.json').content.toString(), '{"lateCommit":true}')
})

test('successful config saves renew target-bound tokens for consecutive edits', async () => {
  const env = setup()
  const initial = await env.rawRequest({ op: 'config-read' })
  const first = await env.rawRequest({ op: 'config-save', text: '{"custom":1}', expectedHash: initial.hash, selectionToken: initial.selectionToken, confirmed: true })
  assert.equal(first.ok, true)
  assert.equal(first.needsReload, false)
  assert.notEqual(first.selectionToken, initial.selectionToken)
  const second = await env.rawRequest({ op: 'config-save', text: '{"custom":2}', expectedHash: first.hash, selectionToken: first.selectionToken, confirmed: true })
  assert.equal(second.ok, true)
  assert.equal(second.needsReload, false)
  assert.equal(env.fs.entries.get('/settings/config.json').content.toString(), '{"custom":2}')
  await env.service.dispose()
})

test('a known applied write stays successful when Host selection changes during publication', async () => {
  const env = setup()
  const initial = await env.rawRequest({ op: 'config-read' })
  env.fs.beforeWrite = target => { if (target.targetKey === '/settings/config.json') env.config.vtxConfigFile = '/settings/other.json' }
  const saved = await env.rawRequest({ op: 'config-save', text: '{"custom":1}', expectedHash: initial.hash, selectionToken: initial.selectionToken, confirmed: true })
  assert.equal(saved.ok, true)
  assert.equal(saved.needsReload, true)
  assert.equal(saved.selectionToken, undefined)
  assert.equal(env.fs.entries.get('/settings/config.json').content.toString(), '{"custom":1}')
  await env.service.dispose()
})

test('failed managed cleanup never reports installed kernel or admits another install', async () => {
  const env = setup({ cleanupFails: true }), wheelPath = wheel(env)
  const plan = await env.request({ op: 'install-preview', wheelPath })
  const confirmed = await env.request({ op: 'install-confirm', nonce: plan.nonce, hash: plan.hash })
  const job = await status(env, confirmed.jobId)
  assert.equal(job.error.code, 'PROCESS_CLEANUP_FAILED')
  assert.equal(job.vtxPath, undefined)
  const next = await env.request({ op: 'install-preview', wheelPath })
  assert.equal((await env.request({ op: 'install-confirm', nonce: next.nonce, hash: next.hash })).error, 'INSTALL_BUSY')
  await assert.rejects(env.service.dispose(), AggregateError)
})
