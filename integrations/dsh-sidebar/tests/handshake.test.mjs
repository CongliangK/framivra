import test from 'node:test'
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import React, { act } from 'react'
import { JSDOM } from 'jsdom'
import { stringifyJSON } from '../src/json.mjs'
import { HOST_IDENTITY, HOST_PROTOCOL_OPS, createRequestLogger } from '../src/protocol.mjs'
import { resolveBuildStamp, computeSourceStampFromDir } from '../src/host.mjs'
import { FileBrowser, buildMismatchMessage, setClientBuildStampForTests } from '../src/client/workbench.mjs'
import { PreviewBody } from '../src/client/index.mjs'
import { setup } from './helpers.mjs'

const h = React.createElement

// ---------- Host: hello handshake ----------

test('host handshake: hello answers grant-free with the protocol identity and spawns nothing', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  const response = await f.control({ op: 'hello' })
  assert.equal(response.status, 200)
  const body = await response.json()
  assert.equal(body.ok, true)
  assert.equal(body.schema, 'framivra.handshake/v1')
  assert.deepEqual(body.host, { ...HOST_IDENTITY, build: resolveBuildStamp() })
  assert(HOST_IDENTITY.ops.includes('browse'), 'advertised ops include browse')
  assert.deepEqual(f.subprocess.calls, [], 'hello never spawns vtx')
})

test('host handshake: hello for a ghost session is a session error, not an authorization error', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  const response = await f.host.control(new Request('http://owner/api/framivra/control', { method: 'POST', body: stringifyJSON({ op: 'hello', sessionId: 'ghost' }) }))
  assert.equal(response.status, 404)
  assert.equal((await response.json()).error, 'BAD_SESSION')
})

test('host handshake: open reports the host identity so new-client/old-host mismatches are detectable', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  const opened = await (await f.control({ op: 'open', timeline: 'timeline.json' })).json()
  assert.deepEqual(opened.host, { ...HOST_IDENTITY, build: resolveBuildStamp() })
  assert.match(resolveBuildStamp(), /^src:[0-9a-f]{16}$/, 'source runs carry the deterministic source stamp')
  assert.equal(resolveBuildStamp(), computeSourceStampFromDir(fileURLToPath(new URL('../src', import.meta.url))), 'host stamp matches the documented source recipe')
})

test('host handshake: unknown op without a grant still fails BAD_GRANT — dispatch order unchanged', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  const response = await f.control({ op: 'nosuch' })
  assert.equal(response.status, 403)
  assert.equal((await response.json()).error, 'BAD_GRANT')
})

// ---------- Host: sanitized, rate-bounded failure logging ----------

test('host logging: business rejections emit one sanitized line via the injected sink', async t => {
  const lines = []
  const f = setup({ logger: (kind, line) => lines.push({ kind, line }) }); t.after(() => f.host.dispose())
  const response = await f.control({ op: 'browse', path: '../escape.json' })
  assert.equal(response.status, 400)
  const rejections = lines.filter(entry => entry.kind === 'rejected')
  assert.equal(rejections.length, 1)
  assert.match(rejections[0].line, /^\[framivra-dsh-sidebar\] control op=browse code=BAD_PATH status=400 ms=\d+$/)
  assert(!rejections[0].line.includes('escape'), 'no request path values are logged')
  assert(!rejections[0].line.includes('grant'), 'no grant tokens are logged')
})

test('host logging: cancelled requests are never logged', async t => {
  const lines = []
  const f = setup({ logger: (kind, line) => lines.push({ kind, line }) }); t.after(() => f.host.dispose())
  const controller = new AbortController()
  controller.abort()
  await f.host.control(new Request('http://owner/api/framivra/control', { method: 'POST', signal: controller.signal, body: stringifyJSON({ op: 'open', sessionId: 'one', timeline: 'timeline.json' }) }))
  assert.deepEqual(lines, [])
})

test('host logging: the per-minute cap suppresses flooding and reports the suppressed count', async t => {
  const lines = []
  const f = setup({ logger: (kind, line) => lines.push({ kind, line }), logMaxPerMinute: 2 }); t.after(() => f.host.dispose())
  for (let i = 0; i < 5; i++) await f.control({ op: 'browse', path: '../escape.json' })
  const rejections = lines.filter(entry => entry.kind === 'rejected')
  assert.equal(rejections.length, 2, 'cap holds')
  assert(lines.some(entry => entry.line.includes('failure lines capped at 2/min')), 'cap notice emitted once')
})

test('logging unit: tokens are validated and hostile values never reach the sink raw', () => {
  const lines = []
  const logger = createRequestLogger({ sink: (kind, line) => lines.push({ kind, line }) })
  logger.failure({ route: 'control', op: 'browse path=/etc', code: 'BAD PATH\ninjected', status: 400, durationMs: -5 })
  const line = lines.at(-1).line
  assert.equal(line, '[framivra-dsh-sidebar] control op=? code=REJECTED status=400 ms=0')
  logger.failure({ route: 'control', op: 'browse', code: 'BAD_GRANT', status: 403, durationMs: 12 })
  assert.match(lines.at(-1).line, /op=browse code=BAD_GRANT status=403 ms=12$/)
})

// ---------- Client: legacy-host detection and recalibration ----------

async function mount(t) {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://owner/' })
  globalThis.window = dom.window
  globalThis.document = dom.window.document
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const { createRoot } = await import('react-dom/client')
  const root = createRoot(document.getElementById('root'))
  t.after(async () => { await act(async () => root.unmount()); dom.window.close(); delete globalThis.window; delete globalThis.document })
  const label = name => document.querySelector(`[aria-label="${name}"]`)
  const settle = async (predicate, message) => {
    for (let i = 0; i < 100; i++) {
      if (predicate()) return
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 1)) })
    }
    assert.fail(`DOM did not settle${message ? ` (${message})` : ''}: ${document.body.textContent.slice(0, 300)}`)
  }
  return { root, dom, label, settle }
}

const browseOK = (path, entries) => ({ schema: 'framivra.browse/v1', path, entries })
const file = (parent, name) => ({ name, type: 'file', path: parent ? `${parent}/${name}` : name })

test('client handshake: BAD_GRANT on grant-free browse is verified via hello and reported as HOST_TOO_OLD, not an auth failure', async t => {
  const { root, settle } = await mount(t)
  const calls = []
  const call = async (_sessionId, payload) => {
    calls.push(payload.op)
    throw new Error(`${payload.op === 'hello' ? 'BAD_GRANT' : 'BAD_GRANT'}: Preview grant missing`)
  }
  await act(async () => root.render(h(FileBrowser, { sessionId: 'one', tabSignal: new AbortController().signal, call, onChoose: () => {}, onClose: () => {} })))
  await settle(() => document.body.textContent.includes('HOST_TOO_OLD'))
  assert.deepEqual(calls, ['browse', 'hello'], 'hello probe follows the BAD_GRANT browse')
  assert(document.body.textContent.includes('重启 DSH 宿主'), 'explicit restart guidance')
  assert(!document.body.textContent.includes('授权'), 'no misleading authorization wording beyond the raw code')
})

test('client handshake: a host that answers hello is not mislabeled HOST_TOO_OLD', async t => {
  const { root, settle } = await mount(t)
  const call = async (_sessionId, payload) => {
    if (payload.op === 'hello') return { ok: true, schema: 'framivra.handshake/v1', host: HOST_IDENTITY }
    throw new Error('BAD_GRANT: unexpected refusal')
  }
  await act(async () => root.render(h(FileBrowser, { sessionId: 'one', tabSignal: new AbortController().signal, call, onChoose: () => {}, onClose: () => {} })))
  await settle(() => document.querySelector('[role="alert"]'))
  assert(!document.body.textContent.includes('HOST_TOO_OLD'))
  assert(document.body.textContent.includes('BAD_GRANT'))
})

test('client handshake: advertised host ops without browse fail fast without any browse call', async t => {
  const { root, settle } = await mount(t)
  const calls = []
  const call = async (_sessionId, payload) => { calls.push(payload.op); return browseOK('', []) }
  await act(async () => root.render(h(FileBrowser, { sessionId: 'one', tabSignal: new AbortController().signal, call, onChoose: () => {}, onClose: () => {}, hostOps: ['hello', 'open'] })))
  await settle(() => document.body.textContent.includes('HOST_TOO_OLD'))
  assert.deepEqual(calls, [], 'no browse issued against a host known to lack it')
})

test('client recalibration: a successful open retries a failed listing at its current folder without swallowing real errors', async t => {
  const { root, settle } = await mount(t)
  const calls = []
  let failures = 1
  const tabSignal = new AbortController().signal
  const call = async (_sessionId, payload) => {
    calls.push(payload.path)
    if (failures-- > 0) throw new Error('TRANSPORT_DOWN: browse unavailable')
    return browseOK('', [file('', 'ok.json')])
  }
  await act(async () => root.render(h(FileBrowser, { sessionId: 'one', tabSignal, call, onChoose: () => {}, onClose: () => {}, recalibrate: 0 })))
  await settle(() => document.querySelector('[role="alert"]'))
  assert(document.body.textContent.includes('TRANSPORT_DOWN'))
  await act(async () => root.render(h(FileBrowser, { sessionId: 'one', tabSignal, call, onChoose: () => {}, onClose: () => {}, recalibrate: 1 })))
  await settle(() => document.querySelector('.fv-file-list'))
  assert.deepEqual(calls, ['', ''], 'recalibration retried the same folder')
  assert(document.body.textContent.includes('ok.json'))
})

test('client recalibration: a healthy listing is not re-browsed, and a lingering message is cleared', async t => {
  const { root, settle } = await mount(t)
  const calls = []
  const tabSignal = new AbortController().signal
  const call = async (_sessionId, payload) => { calls.push(payload.path); return browseOK('', [file('', 'a.json')]) }
  await act(async () => root.render(h(FileBrowser, { sessionId: 'one', tabSignal, call, onChoose: () => {}, onClose: () => {}, recalibrate: 0 })))
  await settle(() => document.querySelector('.fv-file-list'))
  const before = calls.length
  await act(async () => root.render(h(FileBrowser, { sessionId: 'one', tabSignal, call, onChoose: () => {}, onClose: () => {}, recalibrate: 3 })))
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
  assert.equal(calls.length, before, 'ready phase issues no recalibration IO')
})

// ---------- Client: PreviewBody integration ----------

const version = `sha256:${'a'.repeat(64)}`
const coverage = { complete: true, scope: 'stored', unexpanded: [] }
const snapshot = { content_version: version, coverage, diagnostics: [], document: { project: { name: 'p', duration_frames: 30, fps: 30 } } }
const full = { content_version: version, coverage, diagnostics: [], tracks: [], blocks: [] }
const opened = { grant: 'grant', snapshot, media: [] }

test('PreviewBody: a successful open recalibrates a previously failed file listing through the real control path', async t => {
  const { root, dom, settle, label } = await mount(t)
  const requests = []
  let browseFailures = 1
  const oldFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = oldFetch })
  globalThis.fetch = async (_url, init) => {
    const request = JSON.parse(init.body)
    requests.push(request)
    let result
    if (request.op === 'open') result = opened
    else if (request.op === 'query') result = full
    else if (request.op === 'browse') {
      if (browseFailures-- > 0) return new Response(stringifyJSON({ ok: false, error: 'PREVIEW_FAILED', message: 'browse unavailable' }), { status: 500 })
      result = browseOK('', [file('', 'timeline.json')])
    }
    else if (request.op === 'proposal-list') result = { ok: true, proposals: [] }
    else result = { ok: true }
    return new Response(stringifyJSON(result))
  }
  const observers = new Set()
  const sessions = { binding: id => ({ sessionId: id }), list: { getSnapshot: () => ({ byId: { one: '/w' } }), subscribe: fn => { observers.add(fn); return () => observers.delete(fn) } } }
  const uiSession = { adapter: { current: { getSnapshot: () => ({ key: 'one' }), subscribe: fn => { observers.add(fn); return () => observers.delete(fn) } } } }
  const host = { getDraftServices: () => ({ active: true, conversation: {}, sessions, uiSession }), useTabInfo: () => ({ tab: { signal: new AbortController().signal } }) }
  await act(async () => root.render(h(PreviewBody, { ...host, sessionId: 'one', inputActions: {}, useAccess: () => true })))
  await act(async () => label('浏览工作区打开时间线').click())
  await settle(() => document.querySelector('.fv-files [role="alert"]'))
  assert(document.body.textContent.includes('browse unavailable'))
  assert.equal(requests.filter(r => r.op === 'browse').length, 1)
  // open the default timeline through the path form; success must retry the listing
  await act(async () => document.querySelectorAll('form')[0].dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })))
  await settle(() => document.querySelector('.fv-ruler'))
  await settle(() => document.querySelector('.fv-file-list'))
  assert(requests.filter(r => r.op === 'browse').length >= 2, 'open success recalibrated the failed listing')
  assert(document.body.textContent.includes('timeline.json'))
})

// ---------- Build stamp: determinism, sensitivity, load-time freeze ----------

import { mkdtemp, mkdir, writeFile, readFile, rm, cp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'
import { pathToFileURL } from 'node:url'
import { HOST_LIB_MODULES, CLIENT_MODULES } from '../src/protocol.mjs'
import { DEFAULT_LOG_MAX_PER_MINUTE, MAX_LOG_MAX_PER_MINUTE, readLogMaxPerMinute } from '../src/configuration.mjs'
import { ConfigurationError } from '../src/configuration.mjs'

test('build stamp: the documented recipe is content-sensitive — a same-version edit changes the hash', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'framivra-stamp-'))
  try {
    await mkdir(join(dir, 'client'), { recursive: true })
    for (const name of HOST_LIB_MODULES) await writeFile(join(dir, `${name}.mjs`), `module ${name} rev1\n`)
    for (const name of CLIENT_MODULES) await writeFile(join(dir, 'client', `${name}.mjs`), `module ${name} rev1\n`)
    const before = computeSourceStampFromDir(dir)
    assert.match(before, /^src:[0-9a-f]{16}$/)
    await writeFile(join(dir, 'protocol.mjs'), 'module protocol rev2\n')
    const after = computeSourceStampFromDir(dir)
    assert.notEqual(after, before, 'identical-looking versions with different content must not share a stamp')
  } finally { await rm(dir, { recursive: true, force: true }) }
})

test('build stamp: freezes at module load — replacing the file on disk afterwards never changes the running host identity', async () => {
  const sourceDir = fileURLToPath(new URL('../src', import.meta.url))
  const work = await mkdtemp(join(tmpdir(), 'framivra-freeze-'))
  const copy = join(work, 'src')
  await cp(sourceDir, copy, { recursive: true })
  try {
    const importStamp = `const m = await import(${JSON.stringify(pathToFileURL(join(copy, 'host.mjs')).href)}); process.stdout.write(m.resolveBuildStamp())`
    const first = spawnSync(process.execPath, ['--input-type=module', '-e', importStamp], { encoding: 'utf8' })
    assert.equal(first.status, 0, first.stderr)
    const frozenStamp = first.stdout
    // Same process: load first, THEN rewrite a module on disk, then re-read identity.
    const probe = [
      `const m = await import(${JSON.stringify(pathToFileURL(join(copy, 'host.mjs')).href)})`,
      'const { appendFile } = await import("node:fs/promises")',
      `await appendFile(${JSON.stringify(join(copy, 'protocol.mjs'))}, "// late disk edit\\n")`,
      'process.stdout.write(m.resolveBuildStamp() + "\\n" + m.resolveBuildStamp())',
    ].join('; ')
    const loaded = spawnSync(process.execPath, ['--input-type=module', '-e', probe], { encoding: 'utf8' })
    assert.equal(loaded.status, 0, loaded.stderr)
    const [afterEditInProcess, again] = loaded.stdout.trim().split('\n')
    assert.equal(afterEditInProcess, frozenStamp, 'an already-loaded host keeps its load-time stamp')
    assert.equal(again, frozenStamp, 'the stamp never re-reads disk')
    // A FRESH process on the edited tree reports the new content hash.
    const fresh = spawnSync(process.execPath, ['--input-type=module', '-e', importStamp], { encoding: 'utf8' })
    assert.equal(fresh.status, 0, fresh.stderr)
    assert.notEqual(fresh.stdout, frozenStamp, 'a newly started host reports the edited tree')
  } finally { await rm(work, { recursive: true, force: true }) }
})

test('build stamp: packaged artifacts bake one identical stamp into host and client — two-state consistency', { timeout: 120_000 }, async () => {
  const work = await mkdtemp(join(tmpdir(), 'framivra-pkgstamp-'))
  try {
    const output = join(work, 'candidate.zip'), extracted = join(work, 'extracted')
    const result = spawnSync(process.execPath, [fileURLToPath(new URL('../scripts/package.mjs', import.meta.url)), '--out', output], { encoding: 'utf8', timeout: 110_000 })
    assert.equal(result.status, 0, result.stderr + result.stdout)
    const inspect = spawnSync('python3', ['-c', `import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as archive: archive.extractall(sys.argv[2])
`, output, extracted], { encoding: 'utf8' })
    assert.equal(inspect.status, 0, inspect.stderr)
    const protocol = await readFile(join(extracted, 'lib', 'protocol.js'), 'utf8')
    const baked = protocol.match(/export const BAKED_BUILD_STAMP = '([0-9a-f]{16})'/)
    assert(baked, 'packaged protocol.js carries a baked digest')
    const client = await readFile(join(extracted, 'lib', 'client.js'), 'utf8')
    assert(client.includes(`"pkg:${baked[1]}"`), 'the built client bundle carries the SAME packaged stamp')
    // Import the packaged host itself: its frozen identity must equal the artifact stamp.
    const { resolveBuildStamp: packagedStamp } = await import(pathToFileURL(join(extracted, 'lib', 'host.js')).href)
    assert.equal(packagedStamp(), `pkg:${baked[1]}`)
    // The packaged host must NOT re-arm the dev fallback branch.
    assert(!protocol.includes(`export const BAKED_BUILD_STAMP = '__FRAMIVRA_BUILD_STAMP__'`), 'BAKED literal substituted; BUILD_STAMP_MARKER itself stays untouched')
  } finally { await rm(work, { recursive: true, force: true }) }
})

// ---------- logMaxPerMinute configuration bounds ----------

test('logMaxPerMinute: schema-configured bounds — default, valid extremes, and fail-closed rejections', () => {
  assert.equal(readLogMaxPerMinute({}), DEFAULT_LOG_MAX_PER_MINUTE)
  assert.equal(readLogMaxPerMinute(undefined), DEFAULT_LOG_MAX_PER_MINUTE)
  assert.equal(readLogMaxPerMinute({ logMaxPerMinute: 0 }), 0, '0 legitimately silences sanitized lines')
  assert.equal(readLogMaxPerMinute({ logMaxPerMinute: MAX_LOG_MAX_PER_MINUTE }), MAX_LOG_MAX_PER_MINUTE)
  assert.equal(readLogMaxPerMinute({ logMaxPerMinute: { get: () => 7 } }), 7, 'Volatile cells are read like every other config field')
  for (const bad of [-1, MAX_LOG_MAX_PER_MINUTE + 1, 1.5, null, '120']) {
    assert.throws(() => readLogMaxPerMinute({ logMaxPerMinute: bad }), ConfigurationError, `${String(bad)} must fail closed`)
  }
})

// ---------- Client: build mismatch blocks dependent actions ----------

test('build mismatch: open against a differently-built host fails closed — grant released, no query, explicit guidance', async t => {
  t.after(() => setClientBuildStampForTests(undefined))
  setClientBuildStampForTests('pkg:clientloadedstamp')
  assert.equal(buildMismatchMessage('pkg:clientloadedstamp'), null, 'equal stamps never block')
  // Round 3 semantics: a STAMPED client must fail closed when the host reports
  // no build identity — such a host predates build reporting, so this client
  // cannot know its feature set. (Unstamped clients still never block.)
  for (const missing of [undefined, null, 0, '', '   ']) {
    const unverified = buildMismatchMessage(missing)
    assert(unverified?.startsWith('HOST_BUILD_UNVERIFIED:'), `missing host build ${String(missing)} blocks a stamped client`)
  }
  const message = buildMismatchMessage('pkg:hostrunningstamp')
  assert(message.startsWith('HOST_BUILD_MISMATCH:'))
  assert(message.includes('pkg:hostrunningstam') && message.includes('pkg:clientloade'), 'both identities shown for diagnosis')

  const { root, dom, settle } = await mount(t)
  const requests = []
  const oldFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = oldFetch })
  const mismatchedOpen = { ...opened, host: { plugin: '0.2.0', protocol: 1, ops: ['open', 'close'], build: 'pkg:hostrunningstamp' } }
  globalThis.fetch = async (_url, init) => {
    const request = JSON.parse(init.body)
    requests.push(request)
    let result
    if (request.op === 'open') result = mismatchedOpen
    else result = { ok: true }
    return new Response(stringifyJSON(result))
  }
  const observers = new Set()
  const sessions = { binding: id => ({ sessionId: id }), list: { getSnapshot: () => ({ byId: { one: '/w' } }), subscribe: fn => { observers.add(fn); return () => observers.delete(fn) } } }
  const uiSession = { adapter: { current: { getSnapshot: () => ({ key: 'one' }), subscribe: fn => { observers.add(fn); return () => observers.delete(fn) } } } }
  const host = { getDraftServices: () => ({ active: true, conversation: {}, sessions, uiSession }), useTabInfo: () => ({ tab: { signal: new AbortController().signal } }) }
  await act(async () => root.render(h(PreviewBody, { ...host, sessionId: 'one', inputActions: {}, useAccess: () => true })))
  await act(async () => document.querySelectorAll('form')[0].dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })))
  await settle(() => document.body.textContent.includes('HOST_BUILD_MISMATCH'))
  assert.deepEqual(requests.map(request => request.op), ['open', 'close'], 'mismatch releases the minted grant and issues nothing else')
  assert.equal(requests[1].grant, mismatchedOpen.grant, 'the exact minted grant is closed')
  assert(document.body.textContent.includes('重启 DSH 宿主'), 'explicit restart guidance')
})

test('build mismatch: a matching host build never blocks the open', async t => {
  t.after(() => setClientBuildStampForTests(undefined))
  setClientBuildStampForTests(resolveBuildStamp())
  const { root, dom, settle } = await mount(t)
  const requests = []
  const oldFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = oldFetch })
  globalThis.fetch = async (_url, init) => {
    const request = JSON.parse(init.body)
    requests.push(request)
    let result
    if (request.op === 'open') result = { ...opened, host: { plugin: '0.2.0', protocol: 1, ops: [], build: resolveBuildStamp() } }
    else if (request.op === 'query') result = full
    else if (request.op === 'proposal-list') result = { ok: true, proposals: [] }
    else result = { ok: true }
    return new Response(stringifyJSON(result))
  }
  const observers = new Set()
  const sessions = { binding: id => ({ sessionId: id }), list: { getSnapshot: () => ({ byId: { one: '/w' } }), subscribe: fn => { observers.add(fn); return () => observers.delete(fn) } } }
  const uiSession = { adapter: { current: { getSnapshot: () => ({ key: 'one' }), subscribe: fn => { observers.add(fn); return () => observers.delete(fn) } } } }
  const host = { getDraftServices: () => ({ active: true, conversation: {}, sessions, uiSession }), useTabInfo: () => ({ tab: { signal: new AbortController().signal } }) }
  await act(async () => root.render(h(PreviewBody, { ...host, sessionId: 'one', inputActions: {}, useAccess: () => true })))
  await act(async () => document.querySelectorAll('form')[0].dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })))
  await settle(() => document.querySelector('.fv-ruler'))
  assert(!document.body.textContent.includes('HOST_BUILD_MISMATCH'))
  assert(requests.some(request => request.op === 'query'), 'matching build proceeds to the normal query path')
})

test('build mismatch: BAD_GRANT browse against a hello-answering host reports the mismatch, not a bare auth error', async t => {
  t.after(() => setClientBuildStampForTests(undefined))
  setClientBuildStampForTests('pkg:clientloadedstamp')
  const { root, settle } = await mount(t)
  const call = async (_sessionId, payload) => {
    if (payload.op === 'hello') return { ok: true, schema: 'framivra.handshake/v1', host: { plugin: '0.2.0', protocol: 1, ops: ['open'], build: 'pkg:hostrunningstamp' } }
    throw new Error('BAD_GRANT: unexpected refusal')
  }
  await act(async () => root.render(h(FileBrowser, { sessionId: 'one', tabSignal: new AbortController().signal, call, onChoose: () => {}, onClose: () => {} })))
  await settle(() => document.body.textContent.includes('HOST_BUILD_MISMATCH'))
  assert(!document.body.textContent.includes('HOST_TOO_OLD'), 'a hello-answering host is not called too old')
})

// ---------- Round 3: unverified-host blocking, volatile log cap, close honesty ----------

test('round 3: a stamped client blocks open against a host that reports NO build identity', async t => {
  t.after(() => setClientBuildStampForTests(undefined))
  setClientBuildStampForTests('pkg:clientloadedstamp')
  const { root, dom, settle } = await mount(t)
  const requests = []
  const oldFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = oldFetch })
  // The compat hole: a host new enough to answer open with host/ops but too
  // old to report build — BAD_GRANT never fires, so only this check blocks.
  const identitylessOpen = { ...opened, host: { plugin: '0.2.0', protocol: 1, ops: ['open', 'close'] } }
  globalThis.fetch = async (_url, init) => {
    const request = JSON.parse(init.body)
    requests.push(request)
    return new Response(stringifyJSON(request.op === 'open' ? identitylessOpen : { ok: true }))
  }
  const observers = new Set()
  const sessions = { binding: id => ({ sessionId: id }), list: { getSnapshot: () => ({ byId: { one: '/w' } }), subscribe: fn => { observers.add(fn); return () => observers.delete(fn) } } }
  const uiSession = { adapter: { current: { getSnapshot: () => ({ key: 'one' }), subscribe: fn => { observers.add(fn); return () => observers.delete(fn) } } } }
  const host = { getDraftServices: () => ({ active: true, conversation: {}, sessions, uiSession }), useTabInfo: () => ({ tab: { signal: new AbortController().signal } }) }
  await act(async () => root.render(h(PreviewBody, { ...host, sessionId: 'one', inputActions: {}, useAccess: () => true })))
  await act(async () => document.querySelectorAll('form')[0].dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })))
  await settle(() => document.body.textContent.includes('HOST_BUILD_UNVERIFIED'))
  assert.deepEqual(requests.map(request => request.op), ['open', 'close'], 'the minted grant is closed and no query/edit/frame follows')
  assert.equal(requests[1].grant, identitylessOpen.grant, 'the exact minted grant is closed')
  assert(!document.body.textContent.includes('未能确认回收'), 'a confirmed close adds no unconfirmed-reclaim note')
})

test('round 3: an unconfirmable grant close is reported honestly — unconfirmed reclaim, no retry', async t => {
  t.after(() => setClientBuildStampForTests(undefined))
  setClientBuildStampForTests('pkg:clientloadedstamp')
  const { root, dom, settle } = await mount(t)
  const requests = []
  const oldFetch = globalThis.fetch
  t.after(() => { globalThis.fetch = oldFetch })
  globalThis.fetch = async (_url, init) => {
    const request = JSON.parse(init.body)
    requests.push(request)
    if (request.op === 'open') return new Response(stringifyJSON({ ...opened, host: { plugin: '0.2.0', protocol: 1, ops: ['open', 'close'], build: 'pkg:hostrunningstamp' } }))
    // close fails once; the client must NOT retry it automatically.
    return Response.error()
  }
  const observers = new Set()
  const sessions = { binding: id => ({ sessionId: id }), list: { getSnapshot: () => ({ byId: { one: '/w' } }), subscribe: fn => { observers.add(fn); return () => observers.delete(fn) } } }
  const uiSession = { adapter: { current: { getSnapshot: () => ({ key: 'one' }), subscribe: fn => { observers.add(fn); return () => observers.delete(fn) } } } }
  const host = { getDraftServices: () => ({ active: true, conversation: {}, sessions, uiSession }), useTabInfo: () => ({ tab: { signal: new AbortController().signal } }) }
  await act(async () => root.render(h(PreviewBody, { ...host, sessionId: 'one', inputActions: {}, useAccess: () => true })))
  await act(async () => document.querySelectorAll('form')[0].dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })))
  await settle(() => document.body.textContent.includes('未能确认回收'))
  assert(document.body.textContent.includes('HOST_BUILD_MISMATCH'), 'the build verdict stays the primary message')
  assert.deepEqual(requests.map(request => request.op), ['open', 'close'], 'exactly one close attempt — no automatic semantic retry')
})

test('round 3: a stamped client reports UNVERIFIED (not an auth error) when hello answers without build identity', async t => {
  t.after(() => setClientBuildStampForTests(undefined))
  setClientBuildStampForTests('pkg:clientloadedstamp')
  const { root, settle } = await mount(t)
  const call = async (_sessionId, payload) => {
    if (payload.op === 'hello') return { ok: true, schema: 'framivra.handshake/v1', host: { plugin: '0.2.0', protocol: 1, ops: ['open'] } }
    throw new Error('BAD_GRANT: unexpected refusal')
  }
  await act(async () => root.render(h(FileBrowser, { sessionId: 'one', tabSignal: new AbortController().signal, call, onChoose: () => {}, onClose: () => {} })))
  await settle(() => document.body.textContent.includes('HOST_BUILD_UNVERIFIED'))
  assert(!document.body.textContent.includes('HOST_TOO_OLD'), 'a hello-answering host is not called too old')
})

test('round 3 M1: the volatile log cap is re-read per line and a mid-minute change never resets counting', () => {
  const lines = []
  let cap = 2
  const logger = createRequestLogger({ sink: (kind, line) => lines.push({ kind, line }), maxPerMinute: () => cap })
  for (let i = 0; i < 3; i++) logger.failure({ route: 'control', op: 'browse', code: 'BAD_PATH', status: 400, durationMs: 1 })
  assert.equal(lines.filter(entry => entry.kind === 'rejected').length, 2, 'cap 2 holds')
  cap = 4 // raised mid-minute: the SAME window continues counting — no reset
  for (let i = 0; i < 3; i++) logger.failure({ route: 'control', op: 'browse', code: 'BAD_PATH', status: 400, durationMs: 1 })
  assert.equal(lines.filter(entry => entry.kind === 'rejected').length, 4, 'raising the cap admits at most 2 more, not 4 fresh lines')
  cap = 1 // lowered below the already-spent budget: fully suppressed, no new notice
  const before = lines.length
  for (let i = 0; i < 2; i++) logger.failure({ route: 'control', op: 'browse', code: 'BAD_PATH', status: 400, durationMs: 1 })
  assert.equal(lines.length, before, 'lowering the cap cannot re-open the window')
})

test('round 3 M1: a throwing or invalid cap read skips the line without breaking anything else', () => {
  const lines = []
  let cap = 120
  const logger = createRequestLogger({
    sink: (kind, line) => lines.push({ kind, line }),
    maxPerMinute: () => { if (cap === 'boom') throw new Error('volatile read failed'); return cap },
  })
  logger.failure({ route: 'control', op: 'browse', code: 'BAD_PATH', status: 400, durationMs: 1 })
  cap = 'boom'
  logger.failure({ route: 'control', op: 'browse', code: 'BAD_PATH', status: 400, durationMs: 1 })
  cap = -1 // invalid live value: fail closed to silence
  logger.failure({ route: 'control', op: 'browse', code: 'BAD_PATH', status: 400, durationMs: 1 })
  cap = 120
  logger.failure({ route: 'control', op: 'browse', code: 'BAD_PATH', status: 400, durationMs: 1 })
  assert.equal(lines.filter(entry => entry.kind === 'rejected').length, 2, 'only the two valid-cap lines were logged; no throw escaped')
})

test('round 3 M2: logMaxPerMinute 0 is completely silent — no lines, no cap notice, no rollover summary', async t => {
  const lines = []
  const f = setup({ logger: (kind, line) => lines.push({ kind, line }), logMaxPerMinute: 0 }); t.after(() => f.host.dispose())
  for (let i = 0; i < 5; i++) await f.control({ op: 'browse', path: '../escape.json' })
  assert.deepEqual(lines, [], 'cap=0 emits nothing at all, including the cap notice')
})
