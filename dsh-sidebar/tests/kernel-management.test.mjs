import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { homedir } from 'node:os'
import { join, posix, win32 } from 'node:path'
import { createKernelManagement, validateKernelConfigSelection, validateManagedPreviewSelection, INSTALL_SCRIPT, KERNEL_CONFIG_LIMIT, KERNEL_WHEEL_LIMIT, INSTALLATION_LIMIT, managedInstallationPaths } from '../src/kernel-management.mjs'
import { readConfiguration, validateVtxConfigFile, isVolatileConfig } from '../src/configuration.mjs'
import { updateKernelConfigField } from '../src/kernel-config.mjs'
import { TimelineReader } from '../src/protocol.mjs'
import { createPreviewHost } from '../src/host.mjs'
import { MemoryFs as TimelineFs, fixtureSubprocess } from './helpers.mjs'

const digest = data => `sha256:${createHash('sha256').update(data).digest('hex')}`
// The fixture contains both POSIX paths and the host's native managed layout.
const fixturePaths = path => path.startsWith('/') ? posix : win32
class Fs {
  constructor() { this.entries = new Map(); this.links = new Map(); this.reads = []; this.writes = []; this.version = 0; this.add('/', null, 'directory'); this.add(homedir(), null, 'directory'); this.add('/usr/bin/python3', 'system'); this.add('/opt/trusted/bin/vtx', 'kernel') }
  add(path, content, type = 'file') {
    const parent = fixturePaths(path).dirname(path)
    if (parent !== path && !this.entries.has(parent)) this.add(parent, null, 'directory')
    this.entries.set(path, { type, content: content === null ? null : Buffer.from(content), version: `v${++this.version}` })
  }
  async resolve(path) { const key = this.links.get(path) ?? fixturePaths(path).normalize(path); return { targetKey: key, displayPath: key } }
  processPath(target) { return target.targetKey }
  processPathFromHostPath(path) { return path }
  contains(a, b) {
    const paths = fixturePaths(a.targetKey)
    if (paths !== fixturePaths(b.targetKey)) return false
    const rel = paths.relative(a.targetKey, b.targetKey)
    return rel !== '..' && !rel.startsWith(`..${paths.sep}`) && !paths.isAbsolute(rel)
  }
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
    return [...this.entries].filter(([path]) => path !== target.targetKey && fixturePaths(path).dirname(path) === target.targetKey).map(([path, info]) => ({ name: fixturePaths(path).basename(path), type: info.type, target: { targetKey: path } }))
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
  const service = createKernelManagement(ctx, config, { approvedPreviewSuppliers: options.approvedPreviewSuppliers })
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
  assert.equal(result.suggestedConfigFile, managedInstallationPaths().configFile)
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

function managedInstall(env, overrides = {}) {
  const paths = managedInstallationPaths()
  const manifest = { schema: 'framivra.installation/v1', sourceRoot: paths.sourceRoot, commit: 'a'.repeat(40), version: '0.0.1-unknown-compatibility', vtxExecutable: paths.vtxExecutable, pythonExecutable: paths.pythonExecutable, configFile: paths.configFile, installedAt: '2026-11-16T10:00:00Z', profile: 'non-ai', renderer: 'cdp', status: 'ready', ...overrides }
  env.fs.add(paths.sourceRoot, null, 'directory')
  env.fs.add(paths.vtxExecutable, '#!/bin/sh\nexec fixed-venv-vtx "$@"')
  env.fs.add(paths.pythonExecutable, 'venv-python')
  env.fs.add(paths.configFile, '{}\n')
  env.fs.add(paths.manifest, JSON.stringify(manifest))
  return { paths, manifest }
}

test('managed layouts use the same POSIX root and native Windows LOCALAPPDATA/fallback paths', () => {
  for (const platform of ['linux', 'darwin']) {
    const paths = managedInstallationPaths({ platform, home: '/Users/Owner', localAppData: '/ignored' })
    assert.equal(paths.root, '/Users/Owner/.local/share/framivra')
    assert.equal(paths.vtxExecutable, `${paths.root}/bin/vtx`)
    assert.equal(paths.pythonExecutable, `${paths.root}/venv/bin/python`)
    assert.equal(paths.configFile, `${paths.root}/config.json`)
  }
  const paths = managedInstallationPaths({ platform: 'win32', home: 'C:\\Users\\Owner', localAppData: 'D:\\Local Data' })
  assert.equal(paths.root, 'D:\\Local Data\\framivra')
  assert.equal(paths.manifest, `${paths.root}\\installation.json`)
  assert.equal(paths.vtxExecutable, `${paths.root}\\venv\\Scripts\\vtx.exe`)
  assert.equal(paths.pythonExecutable, `${paths.root}\\venv\\Scripts\\python.exe`)
  assert.equal(paths.configFile, `${paths.root}\\config.json`)
  assert.equal(managedInstallationPaths({ platform: 'win32', home: 'C:\\Users\\Owner', localAppData: '' }).root, 'C:\\Users\\Owner\\AppData\\Local\\framivra')
})

test('managed manifest is bounded, first, metadata-only and never executes, migrates or saves', async t => {
  const env = setup(); t.after(() => env.service.dispose())
  const { paths, manifest } = managedInstall(env, { tools: { chrome: { command: ['/evil/browser', '--execute'] } }, command: ['/evil/command'] })
  const result = await env.request({ op: 'scan' })
  assert.equal(result.ok, true)
  assert.deepEqual(result.candidates[0], { path: paths.vtxExecutable, kind: 'executable', source: 'managed-git', installation: { commit: manifest.commit, version: manifest.version, renderer: 'cdp', profile: 'non-ai', configFile: paths.configFile } })
  assert.deepEqual(env.fs.reads, [{ path: paths.manifest, max: INSTALLATION_LIMIT }])
  assert.equal(result.warnings.length, 0)
  assert.equal(env.calls.length, 0)
  assert.equal(env.fs.writes.length, 0)
  assert.equal(env.config.vtxExecutable, '/opt/trusted/bin/vtx')
  assert.equal(env.config.vtxConfigFile, '/settings/config.json')
  assert(!JSON.stringify(result).includes('/evil/'))
})

const syntheticLauncher = '#!/usr/bin/python3 -IBS\n# fixture only\n'
const syntheticSupplier = { schema: 'framivra.preview-supplier/v1', abi: 'framivra.preview-window.fixed-wheel/v1',
  launcherSha256: digest(syntheticLauncher).slice(7), wheelSha256: 'c'.repeat(64), runtimeProjectionSha256: 'd'.repeat(64) }
const syntheticApproval = ['v1', 'a'.repeat(40), syntheticSupplier.launcherSha256,
  syntheticSupplier.wheelSha256, syntheticSupplier.runtimeProjectionSha256].join(':')
function managedPreview(env, patch = {}) {
  const previewSupplier = { ...syntheticSupplier, ...patch }
  const { paths, manifest } = managedInstall(env, { previewSupplier })
  const executable = join(paths.previewRoot, 'vtx-preview')
  const wheelPin = join(paths.previewRoot, 'kernel.sha256')
  env.fs.add(executable, syntheticLauncher)
  env.fs.add(wheelPin, `${previewSupplier.wheelSha256}\n`)
  env.fs.add(join(paths.previewRoot, 'kernel.whl'), 'wheel bytes are the launcher’s responsibility')
  env.fs.add(join(paths.previewRoot, 'runtime'), null, 'directory')
  const pin = ['v1', manifest.commit, previewSupplier.launcherSha256, previewSupplier.wheelSha256, previewSupplier.runtimeProjectionSha256].join(':')
  return { paths, manifest, executable, wheelPin, pin }
}

test('Linux scan enrolls fixed metadata only, and live binding rechecks the saved selection', { skip: process.platform !== 'linux' }, async t => {
  const env = setup({ approvedPreviewSuppliers: [syntheticApproval] }); t.after(() => env.service.dispose())
  const { paths, executable, wheelPin, pin, manifest } = managedPreview(env)
  const result = await env.request({ op: 'scan' })
  assert.equal(result.candidates[0].installation.previewSupplier, pin)
  assert.deepEqual(result.warnings, [])
  assert.deepEqual(env.fs.reads.map(row => row.path), [paths.manifest, executable, wheelPin])
  assert.equal(env.calls.length + env.fs.writes.length, 0)
  assert.equal(env.config.managedPreviewSupplier, undefined)
  const selection = { vtxExecutable: paths.vtxExecutable, vtxConfigFile: paths.configFile, managedPreviewSupplier: pin }
  const signal = new AbortController().signal
  await assert.rejects(validateManagedPreviewSelection(env.ctx, selection, signal), { code: 'PREVIEW_SUPPLIER_UNAPPROVED' })
  assert.equal((await validateManagedPreviewSelection(env.ctx, selection, signal, [syntheticApproval])).executable, executable)
  await assert.rejects(validateManagedPreviewSelection(env.ctx, { ...selection, vtxConfigFile: '/elsewhere/config.json' }, signal, [syntheticApproval]), { code: 'PREVIEW_SUPPLIER_CHANGED' })
  env.fs.add(paths.manifest, JSON.stringify({ ...manifest, commit: '0'.repeat(40) }))
  await assert.rejects(validateManagedPreviewSelection(env.ctx, selection, signal, [syntheticApproval]), { code: 'PREVIEW_SUPPLIER_UNAPPROVED' })
  assert.equal(env.calls.length + env.fs.writes.length, 0)
})

test('bad managed supplier records preserve normal discovery without advertising a window', { skip: process.platform !== 'linux' }, async t => {
  for (const scenario of ['extra', 'abi', 'digest', 'link', 'pin', 'launcher', 'workspace', 'missing', 'oversized']) {
    await t.test(scenario, async st => {
      const env = setup({ approvedPreviewSuppliers: [syntheticApproval] }); st.after(() => env.service.dispose())
      const { paths, executable, wheelPin } = managedPreview(env, {
        ...(scenario === 'extra' && { executable: '/host/never' }),
        ...(scenario === 'abi' && { abi: 'unknown' }),
        ...(scenario === 'digest' && { runtimeProjectionSha256: 'd'.repeat(64) + '\n' }),
      })
      if (scenario === 'link') env.fs.links.set(executable, '/elsewhere/launcher')
      if (scenario === 'pin') env.fs.add(wheelPin, 'e'.repeat(64) + '\n')
      if (scenario === 'launcher') env.fs.add(executable, 'different bytes')
      if (scenario === 'workspace') env.ctx.workingDirectory.get = () => join(paths.previewRoot, 'unsafe-workspace')
      if (scenario === 'missing') env.fs.entries.delete(join(paths.previewRoot, 'runtime'))
      if (scenario === 'oversized') env.fs.add(executable, 'x'.repeat(65 * 1024))
      const result = await env.request({ op: 'scan' })
      assert.equal(result.ok, true)
      assert.equal(result.candidates[0].path, paths.vtxExecutable)
      assert.equal(result.candidates[0].installation.previewSupplier, undefined)
      assert.equal(result.warnings.length, 1)
      assert.equal(env.calls.length + env.fs.writes.length, 0)
    })
  }
})

test('an absent receipt still allows strict discovery of the bare managed launcher', async t => {
  const env = setup(); t.after(() => env.service.dispose())
  const paths = managedInstallationPaths()
  env.fs.add(paths.vtxExecutable, 'bare launcher')
  const result = await env.request({ op: 'scan' })
  assert.equal(result.ok, true)
  assert.deepEqual(result.candidates[0], { path: paths.vtxExecutable, kind: 'executable', source: 'managed-default' })
  assert.deepEqual(result.warnings, [])
  assert.equal(env.fs.reads.length + env.calls.length + env.fs.writes.length, 0)
})

test('preparing receipt suppresses the fixed launcher from current settings and resolved aliases', async t => {
  const env = setup(); t.after(() => env.service.dispose())
  const { paths } = managedInstall(env, { status: 'preparing' })
  const windows = process.platform === 'win32'
  const alias = join(homedir(), '.local', 'bin', windows ? 'vtx.exe' : 'vtx')
  const alternate = windows ? paths.vtxExecutable.toLowerCase().replaceAll('\\', '/') : join(paths.root, 'bin') + '/./vtx'
  env.fs.links.set(alias, paths.vtxExecutable)
  for (const configured of [paths.vtxExecutable, alternate]) {
    env.config.vtxExecutable = configured
    const result = await env.request({ op: 'scan' })
    assert.equal(result.ok, true)
    assert(!result.candidates.some(item => [configured, paths.vtxExecutable, alias].includes(item.path)))
    assert(result.warnings.some(item => item.includes('BAD_INSTALLATION')))
    assert.equal(env.config.vtxExecutable, configured)
  }
  assert.equal(env.calls.length + env.fs.writes.length, 0)
})

test('invalid or oversized manifests cannot offer the managed launcher or hide independent candidates', async t => {
  for (const overrides of [
    { schema: 'other' }, { status: 'installing' }, { renderer: 'cef' }, { profile: 'ai' },
    { commit: 'abcd' }, { commit: 'A'.repeat(40) }, { commit: 'a'.repeat(40) + '\n' }, { commit: ['a'.repeat(40)] }, { version: null },
    { vtxExecutable: '/evil/vtx' }, { sourceRoot: '/workspace/source' },
    { pythonExecutable: '/evil/python' }, { configFile: '/workspace/config.json' },
    { configFile: '$(touch injected).json' },
  ]) {
    const env = setup(); t.after(() => env.service.dispose())
    const { paths } = managedInstall(env, overrides)
    const result = await env.request({ op: 'scan' })
    assert.equal(result.ok, true)
    assert(!result.candidates.some(item => item.path === paths.vtxExecutable || item.installation || item.path.includes('/evil/')))
    assert(result.candidates.some(item => item.path === '/opt/trusted/bin/vtx'))
    assert(result.warnings.length)
    assert.equal(env.calls.length + env.fs.writes.length, 0)
    assert.deepEqual(env.fs.reads.map(item => item.path), [paths.manifest])
  }
  for (const encode of [() => Buffer.alloc(INSTALLATION_LIMIT + 1, 32), () => Buffer.from('{"schema":'), () => Buffer.from([0xff]), manifest => `\uFEFF${JSON.stringify(manifest)}`]) {
    const env = setup(); t.after(() => env.service.dispose())
    const { paths, manifest } = managedInstall(env)
    env.fs.add(paths.manifest, encode(manifest))
    const result = await env.request({ op: 'scan' })
    assert.equal(result.ok, true)
    assert(!result.candidates.some(item => item.path === paths.vtxExecutable))
    assert(result.warnings.length)
    assert(env.fs.reads.every(item => item.max === INSTALLATION_LIMIT))
    assert.equal(env.calls.length + env.fs.writes.length, 0)
  }
})

test('an inaccessible receipt or a missing referenced file is not an absent receipt', async t => {
  const env = setup(); t.after(() => env.service.dispose())
  const { paths } = managedInstall(env)
  env.fs.entries.delete(paths.pythonExecutable)
  let result = await env.request({ op: 'scan' })
  assert.equal(result.ok, true)
  assert(!result.candidates.some(item => item.path === paths.vtxExecutable))
  assert(result.warnings.some(item => item.includes('NOT_FOUND')))
  const original = env.fs.lstat.bind(env.fs)
  env.fs.lstat = async path => {
    if (path === paths.manifest) throw Object.assign(new Error('denied'), { code: 'FS_PERMISSION_DENIED' })
    return original(path)
  }
  result = await env.request({ op: 'scan' })
  assert.equal(result.ok, true)
  assert(!result.candidates.some(item => item.path === paths.vtxExecutable))
  assert(result.warnings.some(item => item.includes('FS_PERMISSION_DENIED')))
  assert.equal(env.fs.reads.length, 1)
  assert.equal(env.calls.length + env.fs.writes.length, 0)
})

test('managed receipt, ancestors, launcher, source, python and config reject symlinks and workspace trust', async t => {
  for (const field of ['root', 'manifest', 'vtxExecutable', 'sourceRoot', 'pythonExecutable', 'configFile']) {
    const env = setup(); t.after(() => env.service.dispose())
    const { paths } = managedInstall(env)
    env.fs.links.set(paths[field], '/untrusted/redirect')
    const result = await env.request({ op: 'scan' })
    assert.equal(result.ok, true)
    assert(!result.candidates.some(item => item.path === paths.vtxExecutable), field)
    if (['root', 'manifest'].includes(field)) assert.equal(env.fs.reads.length, 0)
    assert(result.warnings.length)
    assert.equal(env.calls.length + env.fs.writes.length, 0)
  }
  const env = setup(); t.after(() => env.service.dispose())
  const { paths } = managedInstall(env)
  env.ctx.workingDirectory.get = () => paths.root
  env.config.vtxExecutable = paths.vtxExecutable
  const result = await env.request({ op: 'scan' })
  assert.equal(result.ok, true)
  assert(!result.candidates.some(item => item.path.startsWith(paths.root)))
  assert.equal(env.fs.reads.length, 0)
  assert.equal(env.calls.length + env.fs.writes.length, 0)
})

test('manifest change during read discards receipt and config selection stays source-external', async t => {
  const env = setup(); t.after(() => env.service.dispose())
  const { paths } = managedInstall(env)
  const original = env.fs.readBytes.bind(env.fs)
  env.fs.readBytes = async (...args) => { const data = await original(...args); if (args[0].targetKey === paths.manifest) env.fs.add(paths.manifest, data); return data }
  const result = await env.request({ op: 'scan' })
  assert.equal(result.ok, true)
  assert(!result.candidates.some(item => item.path === paths.vtxExecutable))
  assert(result.warnings.some(item => item.includes('INSTALLATION_CHANGED')))
  env.config.vtxExecutable = paths.vtxExecutable
  env.config.vtxConfigFile = paths.configFile
  assert.equal((await env.request({ op: 'config-read' })).ok, true)
  for (const path of [join(paths.sourceRoot, 'config.json'), join(paths.venv, 'config.json')]) {
    env.config.vtxConfigFile = path
    assert.equal((await env.request({ op: 'config-read' })).error, 'INSTALL_CONFIG_PATH')
  }
  assert.equal(env.calls.length + env.fs.writes.length, 0)
})

test('unset config never guesses legacy kernel files; absent explicit path is a create draft', async () => {
  const unset = setup({ config: { vtxConfigFile: '' } })
  const result = await unset.request({ op: 'config-read' })
  assert.equal(result.error, 'CONFIG_FILE_UNSET')
  assert.equal(result.suggestedConfigFile, managedInstallationPaths().configFile)
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
