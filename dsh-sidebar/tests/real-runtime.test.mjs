import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdir, mkdtemp, writeFile, readFile, realpath, stat } from 'node:fs/promises'
import { basename, dirname, join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { registerHooks } from 'node:module'
import { TimelineReader } from '../src/protocol.mjs'
import { createPreviewHost } from '../src/host.mjs'
import { stringifyJSON } from '../src/json.mjs'
import { createHash, randomUUID } from 'node:crypto'
import { performReferenceAction } from '../src/client/reference.mjs'
import { importDsh } from './dsh-public-sdk.mjs'

// Opt-in, public built DSH exports + installed vtx. No replacement server or GUI.
const dsh = process.env.SIDEBAR_DSH_ROOT
const vtx = process.env.SIDEBAR_REAL_VTX
const evidence = process.env.SIDEBAR_EVIDENCE_DIR
const enabled = Boolean(dsh && vtx && evidence)
const skip = enabled ? false : 'Set SIDEBAR_DSH_ROOT, SIDEBAR_REAL_VTX and SIDEBAR_EVIDENCE_DIR for real-provider/upstream evidence'

async function provider(t) {
  const { Context } = await importDsh(dsh, 'cordis')
  const { LocalSubprocessRuntime } = await importDsh(dsh, 'subprocessLocal')
  const ctx = new Context()
  const fiber = ctx.plugin(LocalSubprocessRuntime)
  await fiber.await()
  t.after(() => fiber.dispose())
  return ctx.get('subprocess')
}

async function clientView() {
  const source = new URL('../src/client/index.mjs', import.meta.url).href
  const react = `export const createElement=(type,props,...children)=>({type:typeof type==='symbol'?'Fragment':type,props,children});export const useEffect=()=>{};export const useLayoutEffect=()=>{};export const useRef=()=>({});export const useState=()=>[];export default {createElement,Fragment:Symbol('Fragment')};`
  const hook = registerHooks({ resolve(specifier, context, next) {
    if (specifier === 'react' && context.parentURL === source) return { url: `data:text/javascript,${encodeURIComponent(react)}`, shortCircuit: true }
    return next(specifier, context)
  } })
  try { return await import(source) } finally { hook.deregister() }
}

// Test-only local public-fs-shaped adapter. Production never bypasses ctx.fs.
function localFs() {
  return {
    async resolve(path, { cwd = '/' } = {}) {
      const absolute = resolve(cwd, path)
      let key
      try { key = await realpath(absolute) } catch (error) { if (error.code !== 'ENOENT') throw error; key = absolute }
      return { targetKey: key }
    },
    processPath: target => target.targetKey,
    processPathFromHostPath: path => path,
    contains: (a, b) => b.targetKey === a.targetKey || b.targetKey.startsWith(`${a.targetKey}/`),
    async stat(target) {
      try {
        const s = await stat(target.targetKey)
        return { type: s.isDirectory() ? 'directory' : s.isFile() ? 'file' : 'other', version: `${s.ino}:${s.mtimeMs}:${s.size}`, size: s.size }
      } catch (error) { if (error.code !== 'ENOENT') throw error }
    },
  }
}

async function forkExecutable(dir, stall) {
  const executable = join(dir, 'vtx'), pidfile = join(dir, 'child.pid')
  // Pipe handshake ensures the PID and TERM handler exist before parent exit.
  await writeFile(executable, `#!/usr/bin/python3\nimport os,time,json,signal\nr,w=os.pipe()\npid=os.fork()\nif pid==0:\n os.close(r); os.close(1); os.close(2)\n signal.signal(signal.SIGTERM,signal.SIG_IGN)\n open(${JSON.stringify(pidfile)},'w').write(str(os.getpid()))\n os.write(w,b'x'); os.close(w)\n time.sleep(60); os._exit(0)\nos.close(w); os.read(r,1); os.close(r)\nprint(json.dumps({'schema':'framivra.timeline.read/v1','operation':'capabilities','ok':True,'capabilities':{'operations':['inspect','query']}}),flush=True)\n${stall ? 'time.sleep(60)' : ''}\n`, { mode: 0o755 })
  return { executable, pidfile }
}

const timelineText = '{"version":2,"project":{"fps":30,"duration_frames":30},"sources":[],"video_tracks":[],"audio_tracks":[],"subtitles":[{"in":0,"out":30,"text":"x","asset_id":9007199254740993}],"extension":{"asset_id":9007199254740993,"nested":[-9007199254740993,123456789012345678901234567890]}}\n'

test('real installed vtx inspect/query -> real provider -> Host Response -> Client details is lossless', { skip, timeout: 15000 }, async t => {
  await mkdir(evidence, { recursive: true })
  const dir = await mkdtemp(join(evidence, 'real-precision-'))
  await writeFile(join(dir, 'timeline.json'), timelineText)
  const subprocess = await provider(t)
  const session = { id: 'real' }
  const host = createPreviewHost({ subprocess, fs: localFs(), sessions: new Map([['real', session]]), workingDirectory: { get: () => dir } }, { vtxExecutable: vtx })
  t.after(() => host.dispose())
  const control = payload => host.control(new Request('http://test/api/framivra/control', { method: 'POST', body: JSON.stringify({ sessionId: 'real', ...payload }) }))
  const client = await clientView()
  const response = await control({ op: 'open', timeline: 'timeline.json' })
  const wire = await response.text()
  await writeFile(join(dir, 'open-wire.json'), wire)
  assert.equal(response.status, 200, wire)
  const opened = client.decodeControlResponse(wire)
  const query = await control({ op: 'query', grant: opened.grant, query: { family: 'subtitles', frame: 0 } })
  const queryWire = await query.text()
  await writeFile(join(dir, 'query-wire.json'), queryWire)
  assert.equal(query.status, 200, queryWire)
  const result = client.decodeControlResponse(queryWire)
  assert.equal(result.blocks.length, 1)
  const presentation = client.TimelineView({ snapshot: opened.snapshot, result })
  const serialized = stringifyJSON({ opened, result })
  await writeFile(join(dir, 'client-serialized.json'), serialized)
  await writeFile(join(dir, 'client-view.json'), JSON.stringify(presentation, null, 2))
  for (const text of [wire, queryWire, serialized, JSON.stringify(presentation)]) {
    assert.ok(text.includes('9007199254740993'))
    assert.equal(text.includes('9007199254740992'), false)
    assert.equal(text.includes('"rawJSON"'), false)
  }
  // Neither HTTP transport nor the client may turn a numeric ID into a string.
  assert.match(serialized, /"asset_id":9007199254740993/)
  console.log(JSON.stringify({ evidence: dir, vtx, provider: 'LocalSubprocessRuntime', realWireClient: 'PASS' }))
})

test('real reference CLI -> provider -> Host -> copy is source-exact, stale and duplicate fail closed', { skip, timeout: 20000 }, async t => {
  await mkdir(evidence, { recursive: true })
  const dir = await mkdtemp(join(evidence, 'real-reference-'))
  const raw = '{ "in":0, "out":30, "text":"中文 \\u4e2d", "asset_id":9007199254740993, "huge":1e300, "decimal":1.0000000000000001 }'
  const source = ` \n{ "version":2,"project":{"fps":30,"duration_frames":30},"sources":[],"video_tracks":[],"audio_tracks":[],"subtitles":[${raw}] }\n`
  const path = join(dir, 'timeline.json')
  await writeFile(path, source)
  const runtime = await provider(t)
  const host = createPreviewHost({ subprocess: runtime, fs: localFs(), sessions: new Map([['real', {}]]), workingDirectory: { get: () => dir } }, { vtxExecutable: vtx })
  t.after(() => host.dispose())
  const control = payload => host.control(new Request('http://test/api/framivra/control', { method: 'POST', body: JSON.stringify({ sessionId: 'real', ...payload }) }))
  const openedResponse = await control({ op: 'open', timeline: 'timeline.json' })
  assert.equal(openedResponse.status, 200, await openedResponse.clone().text())
  const opened = await openedResponse.json()
  const selection = { kind: 'block', address: '/subtitles/0' }
  const version = `sha256:${createHash('sha256').update(source).digest('hex')}`
  assert.equal(opened.snapshot.content_version, version)
  let copied, reference
  const response = await performReferenceAction({ action: 'raw', selection, contentVersion: version, frame: 0, signal: AbortSignal.timeout(5000), clipboard: { writeText: async text => { copied = text } }, read: async query => {
    const response = await control({ op: 'reference', grant: opened.grant, query })
    const wire = await response.text()
    await writeFile(join(dir, 'reference-wire.json'), wire)
    assert.equal(response.status, 200, wire)
    return reference = JSON.parse(wire)
  } })
  assert.equal(response.status, 'copied')
  assert.equal(copied, raw)
  assert.equal(reference.document.raw, source)
  assert.equal(reference.selection.blocks[0].stable_id, null)
  assert.equal(reference.selection.global_frame, 0)
  assert.equal(reference.selection.fps, 30)
  await writeFile(path, source.replace('中文', '更改'))
  const query = { expected_content_version: version, block_addresses: [selection.address] }
  const stale = await control({ op: 'reference', grant: opened.grant, query })
  const staleBody = await stale.json()
  assert.equal(stale.status, 502)
  assert.equal(staleBody.diagnostics.diagnostics.some(item => item.code === 'reference.stale'), true)
  await writeFile(path, source.replace('"asset_id":9007199254740993', '"text":"duplicate","asset_id":9007199254740993'))
  const duplicate = await control({ op: 'reference', grant: opened.grant, query })
  assert.equal(duplicate.status, 502)
  const managed = { version: 2, project: { fps: 30, duration_frames: 30 }, sources: [], video_tracks: [], audio_tracks: [], subtitles: [{ in: 0, out: 30, text: 'managed', id: 'managed-real-id' }], _vtx: { protocol: 1, project_id: 'project', head: 'head' } }
  await writeFile(path, JSON.stringify(managed))
  const next = await control({ op: 'open', timeline: 'timeline.json' })
  assert.equal(next.status, 200, await next.clone().text())
  const m = await next.json()
  const identified = await control({ op: 'reference', grant: m.grant, query: { expected_content_version: m.snapshot.content_version, block_ids: ['managed-real-id'] } })
  assert.equal(identified.status, 200, await identified.clone().text())
  assert.equal((await identified.json()).selection.blocks[0].stable_id, 'managed-real-id')
  const missing = await control({ op: 'reference', grant: m.grant, query: { expected_content_version: m.snapshot.content_version, block_ids: ['missing-id'] } })
  assert.equal(missing.status, 502)
  assert.equal((await missing.json()).diagnostics.diagnostics.some(item => item.code === 'reference.not_found'), true)
  const result = { version, rawHash: createHash('sha256').update(copied).digest('hex'), exactDocument: true, exactCopy: true, managedId: 'managed-real-id', missingIdStatus: missing.status, staleStatus: stale.status, duplicateStatus: duplicate.status, png: 'not-generated' }
  await writeFile(join(dir, 'result.json'), JSON.stringify(result))
  console.log(JSON.stringify(result))
})

test('real storage inspect -> plan -> explicit clean uses owner token and reports actual protected-file-safe removal', { skip, timeout: 30000 }, async t => {
  await mkdir(evidence, { recursive: true })
  const dir = await mkdtemp(join(evidence, 'real-storage-'))
  const project = join(dir, 'project')
  await mkdir(join(project, 'cache'), { recursive: true })
  await mkdir(join(project, 'out'))
  const stalePath = join(project, 'cache', 'stale.bin')
  const staleBytes = Buffer.from('owned stale cache bytes\n')
  const sourceBytes = Buffer.from('protected source bytes\0\xff', 'latin1')
  const authorBytes = Buffer.from('<svg><!-- author source --></svg>\n')
  const outBytes = Buffer.from('protected final output\n')
  const timeline = Buffer.from(JSON.stringify({ version: 2, project: { fps: 30, width: 1920, height: 1080, duration_frames: 30 }, sources: [{ id: 'source', path: 'cache/source.mov' }], video_tracks: [], audio_tracks: [], subtitles: [] }) + '\n')
  const protectedFiles = new Map([
    [join(project, 'timeline.json'), timeline],
    [join(project, 'cache', 'source.mov'), sourceBytes],
    [join(project, 'cache', 'original.svg'), authorBytes],
    [join(project, 'out', 'final.mp4'), outBytes],
  ])
  await writeFile(stalePath, staleBytes)
  for (const [path, bytes] of protectedFiles) await writeFile(path, bytes)
  const runtime = await provider(t)
  const sessions = new Map([['owner', { id: 'owner' }], ['other', { id: 'other' }]])
  const host = createPreviewHost({ subprocess: runtime, fs: localFs(), sessions, workingDirectory: { get: () => project } }, { vtxExecutable: vtx })
  t.after(() => host.dispose())
  const control = async (name, payload) => {
    const response = await host.control(new Request('http://test/api/framivra/control', { method: 'POST', body: JSON.stringify({ sessionId: 'owner', ...payload }) }))
    const wire = await response.text()
    await writeFile(join(dir, `${name}-wire.json`), wire)
    return { status: response.status, body: JSON.parse(wire) }
  }
  const opened = await control('open', { op: 'open', timeline: 'timeline.json' })
  assert.equal(opened.status, 200, JSON.stringify(opened.body))
  const grant = opened.body.grant
  const version = `sha256:${createHash('sha256').update(timeline).digest('hex')}`
  const inspected = await control('inspect', { op: 'storage-inspect', grant })
  assert.equal(inspected.status, 200, JSON.stringify(inspected.body))
  assert.equal(inspected.body.ok, true, JSON.stringify(inspected.body))
  assert.equal(inspected.body.content_version, version)
  const protectedCacheBytes = sourceBytes.length + authorBytes.length
  assert.deepEqual(inspected.body.usage, { project_bytes: timeline.length + protectedCacheBytes + staleBytes.length + outBytes.length, cache_bytes: protectedCacheBytes + staleBytes.length, out_bytes: outBytes.length, other_bytes: timeline.length, file_count: 5, symlinks_skipped: 0 })
  const planned = await control('plan', { op: 'storage-plan', grant })
  assert.equal(planned.status, 200, JSON.stringify(planned.body))
  assert.equal(planned.body.ok, true, JSON.stringify(planned.body))
  assert.equal(planned.body.content_version, version)
  assert.equal(typeof planned.body.planToken, 'string')
  assert.ok(planned.body.planToken.length > 0)
  assert.equal(planned.body.plan.count, 1)
  assert.equal(planned.body.plan.bytes, staleBytes.length)
  assert.deepEqual(planned.body.plan.entries.map(({ path, bytes }) => ({ path, bytes })), [{ path: 'cache/stale.bin', bytes: staleBytes.length }])
  const clean = { op: 'storage-clean', grant, planToken: planned.body.planToken, confirm: 'stopped-writers-and-cleanup' }
  const other = await control('wrong-owner', { ...clean, sessionId: 'other' })
  assert.equal(other.status, 403, JSON.stringify(other.body))
  const unconfirmed = await control('unconfirmed', { ...clean, confirm: 'cache/preview-only' })
  assert.equal(unconfirmed.status, 403, JSON.stringify(unconfirmed.body))
  const paths = await control('injected-path', { ...clean, request: { paths: ['cache/stale.bin'] } })
  assert.equal(paths.status, 400, JSON.stringify(paths.body))
  assert.deepEqual(await readFile(stalePath), staleBytes)
  for (const [path, bytes] of protectedFiles) assert.deepEqual(await readFile(path), bytes)
  const cleaned = await control('clean', clean)
  assert.equal(cleaned.status, 200, JSON.stringify(cleaned.body))
  assert.equal(cleaned.body.ok, true, JSON.stringify(cleaned.body))
  assert.equal(cleaned.body.content_version, version)
  assert.equal(cleaned.body.plan.id, planned.body.plan.id)
  assert.deepEqual(cleaned.body.removed, { bytes: staleBytes.length, count: 1 })
  assert.deepEqual(cleaned.body.remaining, { bytes: 0, count: 0 })
  await assert.rejects(stat(stalePath), { code: 'ENOENT' })
  const replay = await control('replay', clean)
  assert.equal(replay.status, 409, JSON.stringify(replay.body))
  for (const [path, bytes] of protectedFiles) assert.deepEqual(await readFile(path), bytes)
  const after = await control('after-inspect', { op: 'storage-inspect', grant })
  assert.equal(after.status, 200, JSON.stringify(after.body))
  assert.equal(after.body.ok, true, JSON.stringify(after.body))
  assert.equal(after.body.usage.out_bytes, outBytes.length)
  // Cleanup may create its protected lock sidecar; do not equate total usage delta with reclaimed bytes.
  assert.equal(after.body.usage.cache_bytes, protectedCacheBytes + (await stat(join(project, 'cache', '.cache-policy.lock.lock'))).size)
  const reader = new TimelineReader(runtime)
  t.after(() => reader.dispose())
  const early = await reader.storage(vtx, join(project, 'missing-timeline.json'), 'inspect', undefined, AbortSignal.timeout(5000))
  await writeFile(join(dir, 'early-error-wire.json'), JSON.stringify(early))
  assert.equal(early.schema, 'framivra.storage/v1')
  assert.equal(early.operation, 'inspect')
  assert.equal(early.ok, false)
  assert.equal(early.content_version, null)
  assert.ok(early.diagnostics.length > 0)
  assert.equal(Object.hasOwn(early, 'usage'), false)
  const result = { evidence: dir, vtx, node: process.version, provider: 'LocalSubprocessRuntime', removed: cleaned.body.removed, removedPath: 'cache/stale.bin', protectedBytesUnchanged: true, wrongOwnerStatus: other.status, unconfirmedStatus: unconfirmed.status, injectedPathStatus: paths.status, replayStatus: replay.status, earlyError: early.diagnostics }
  await writeFile(join(dir, 'result.json'), JSON.stringify(result, null, 2))
  console.log(JSON.stringify(result))
})

async function stopped(pid) {
  try {
    const status = await readFile(`/proc/${pid}/stat`, 'utf8')
    // Orphans can be reaped asynchronously by init; a zombie executes no work.
    return status.slice(status.lastIndexOf(')') + 2).startsWith('Z')
  } catch (error) { if (error.code === 'ENOENT') return true; throw error }
}

test('real Host 30-second read deadline returns only after provider range cleanup', { skip, timeout: 40000 }, async t => {
  const runtime = await provider(t)
  const dir = await mkdtemp(join(evidence, 'real-host-deadline-'))
  const project = join(dir, 'project')
  await mkdir(project)
  await writeFile(join(project, 'timeline.json'), timelineText)
  const { executable, pidfile } = await forkExecutable(dir, true)
  const host = createPreviewHost({ subprocess: runtime, fs: localFs(), sessions: new Map([['real', {}]]), workingDirectory: { get: () => project } }, { vtxExecutable: executable })
  t.after(() => host.dispose())
  const started = Date.now()
  const response = await host.control(new Request('http://test/api/framivra/control', { method: 'POST', body: JSON.stringify({ op: 'open', sessionId: 'real', timeline: 'timeline.json' }) }))
  const elapsedMs = Date.now() - started
  const pid = Number(await readFile(pidfile, 'utf8'))
  const payload = await response.json()
  assert.equal(response.status, 499, JSON.stringify(payload))
  assert.equal(payload.error, 'CANCELLED')
  assert.equal(await stopped(pid), true)
  assert.ok(elapsedMs >= 30000 && elapsedMs < 38000, `bounded deadline: ${elapsedMs}`)
  const result = { status: response.status, elapsedMs, pid, afterResponseStopped: true }
  await writeFile(join(dir, 'result.json'), JSON.stringify(result))
  console.log(JSON.stringify(result))
})

// Independently staged and pinned by the isolated Host administrator. The
// operator supplies a qualified 1920×1080 timeline in a separate workspace.
const windowLauncher = process.env.SIDEBAR_PREVIEW_WINDOW_EXECUTABLE
const windowTimeline = process.env.SIDEBAR_PREVIEW_WINDOW_TIMELINE
const skipWindow = skip || (process.platform !== 'linux' || !windowLauncher || !windowTimeline
  ? 'Requires Linux, an approved SIDEBAR_PREVIEW_WINDOW_EXECUTABLE and a separate SIDEBAR_PREVIEW_WINDOW_TIMELINE' : false)
test('real fixed-wheel launcher renders a manual owner window without replacing the read core', { skip: skipWindow, timeout: 45_000 }, async () => {
  const { Context } = await importDsh(dsh, 'cordis')
  const { LocalSubprocessRuntime } = await importDsh(dsh, 'subprocessLocal')
  const { LocalFileSystem } = await importDsh(dsh, 'fsLocal')
  const ctx = new Context()
  const proc = ctx.plugin(LocalSubprocessRuntime)
  const filesystem = ctx.plugin(LocalFileSystem)
  await Promise.all([proc.await(), filesystem.await()])
  const timeline = await realpath(windowTimeline), session = { id: 'manual-window' }
  const host = createPreviewHost({ subprocess: ctx.get('subprocess'), fs: ctx.get('fs'), sessions: { get: id => id === session.id ? session : undefined, list: () => [session] },
    workingDirectory: { get: () => dirname(timeline) } }, { vtxExecutable: vtx, previewWindowExecutable: windowLauncher })
  const request = (route, payload) => new Request(`http://test/api/framivra/${route}`, { method: 'POST', body: JSON.stringify({ sessionId: session.id, ...payload }) })
  const digest = bytes => createHash('sha256').update(bytes).digest('hex')
  try {
    const before = digest(await readFile(timeline))
    const open = await host.control(request('control', { op: 'open', timeline: basename(timeline) }))
    assert.equal(open.status, 200, await open.clone().text())
    const { grant, snapshot, capabilities } = await open.json()
    assert.equal(snapshot.content_version, `sha256:${before}`)
    const capability = capabilities.capabilities.preview_window
    assert.equal(capability?.launcher, 'framivra.preview-window.fixed-wheel/v1')
    const operationId = randomUUID()
    const result = await host.window(request('window', { op: 'render', grant, operationId, frame: 0,
      contentVersion: snapshot.content_version, renderConsent: 'cache/preview-only', prepareInputs: true }))
    if (!result.ok) assert.fail(await result.text())
    assert.equal(result.headers.get('content-type'), 'video/mp4')
    assert.equal(result.headers.get('x-framivra-version'), snapshot.content_version)
    const bytes = Buffer.from(await result.arrayBuffer())
    assert.equal(digest(bytes), result.headers.get('x-framivra-sha256'))
    assert.equal(bytes.subarray(4, 8).toString('ascii'), 'ftyp')
    assert.deepEqual(await (await host.window(request('window', { op: 'cancel', grant, operationId }))).json(), { ok: true, reaped: true })
    assert.equal(digest(await readFile(timeline)), before)
  } finally {
    try { await host.dispose() } finally { await Promise.all([filesystem.dispose(), proc.dispose()]) }
  }
})

// Separate read-only acceptance input from the manual cold-render fixture.
// The operator supplies the independently approved point and its known digest,
// not an archive path; the fixed wheel is the sole archive selector.
const formalTimeline = process.env.SIDEBAR_FORMAL_ARCHIVE_TIMELINE
const formalFrame = process.env.SIDEBAR_FORMAL_ARCHIVE_FRAME
const formalDigest = process.env.SIDEBAR_FORMAL_ARCHIVE_SHA256
const skipFormal = skip || (process.platform !== 'linux' || !windowLauncher || !formalTimeline || !formalFrame || !formalDigest
  ? 'Requires an approved fixed wheel, archive timeline, exact frame and independent media digest' : false)
test('real fixed wheel reads an independently approved archive through the owner Host path', { skip: skipFormal, timeout: 45_000 }, async () => {
  assert.match(formalFrame, /^(0|[1-9][0-9]*)$/); assert(Number.isSafeInteger(Number(formalFrame)))
  assert.match(formalDigest, /^[a-f0-9]{64}$/)
  const { Context } = await importDsh(dsh, 'cordis')
  const { LocalSubprocessRuntime } = await importDsh(dsh, 'subprocessLocal')
  const { LocalFileSystem } = await importDsh(dsh, 'fsLocal')
  const ctx = new Context(), proc = ctx.plugin(LocalSubprocessRuntime), filesystem = ctx.plugin(LocalFileSystem)
  await Promise.all([proc.await(), filesystem.await()])
  const timeline = await realpath(formalTimeline), session = { id: 'approved-archive' }, commands = []
  const runtime = ctx.get('subprocess')
  const subprocess = { resolveExecutable: (...args) => runtime.resolveExecutable(...args), spawn(spec) { commands.push(spec.argv); return runtime.spawn(spec) } }
  const host = createPreviewHost({ subprocess, fs: ctx.get('fs'), sessions: { get: id => id === session.id ? session : undefined, list: () => [session] },
    workingDirectory: { get: () => dirname(timeline) } }, { vtxExecutable: vtx, previewWindowExecutable: windowLauncher })
  const request = (route, payload) => new Request(`http://test/api/framivra/${route}`, { method: 'POST', body: JSON.stringify({ sessionId: session.id, ...payload }) })
  try {
    const before = await readFile(timeline)
    const opened = await host.control(request('control', { op: 'open', timeline: basename(timeline) }))
    assert.equal(opened.status, 200, await opened.clone().text())
    const { grant, snapshot, previewSession } = await opened.json()
    assert.deepEqual(previewSession?.formalArchive, { version: 1 })
    const tuple = { grant, ownerId: previewSession.ownerId, operationId: randomUUID() }
    const result = await host.window(request('window', { op: 'formal-archive-read', ...tuple, frame: Number(formalFrame), contentVersion: snapshot.content_version }))
    if (!result.ok) assert.fail(await result.text())
    assert.equal(result.headers.get('x-framivra-media-kind'), 'approved-formal-archive/v1')
    assert.equal(result.headers.get('x-framivra-start'), formalFrame)
    assert.equal(result.headers.get('x-framivra-input-epoch'), null)
    assert.equal(createHash('sha256').update(Buffer.from(await result.arrayBuffer())).digest('hex'), formalDigest)
    assert.deepEqual(await (await host.window(request('window', { op: 'formal-archive-cancel', ...tuple }))).json(),
      { ok: true, reaped: true, operationId: tuple.operationId, ownerId: tuple.ownerId, openGrant: grant })
    // Use a different in-project start against the same approval and real launcher.
    // Like a new saved version, this must reach the launcher's exact selector check.
    const rejectedTuple = { ...tuple, operationId: randomUUID() }
    const refused = await host.window(request('window', { op: 'formal-archive-read', ...rejectedTuple,
      frame: formalFrame === '0' ? 1 : 0, contentVersion: snapshot.content_version }))
    assert.equal(refused.status, 409, await refused.clone().text())
    assert.equal(refused.headers.get('x-framivra-media-kind'), null)
    const refusal = await refused.json()
    assert.equal(refusal.error, 'ARCHIVE_SELECTOR_MISMATCH')
    assert.deepEqual(Object.keys(refusal).sort(), ['error', 'message', 'ok'])
    assert.deepEqual(await (await host.window(request('window', { op: 'formal-archive-cancel', ...rejectedTuple }))).json(),
      { ok: true, reaped: true, operationId: rejectedTuple.operationId, ownerId: tuple.ownerId, openGrant: grant })
    assert(!commands.some(argv => argv.includes('--render') || argv.includes('--prepare-inputs')))
    assert.deepEqual(await readFile(timeline), before)
  } finally {
    try { await host.dispose() } finally { await Promise.all([filesystem.dispose(), proc.dispose()]) }
  }
})

for (const mode of ['normal', 'cancel', 'deadline', 'unload']) {
  test(`real provider fork + closed stdio: ${mode} awaits the managed range`, { skip, timeout: 15000 }, async t => {
    const runtime = await provider(t)
    const dir = await mkdtemp(join(evidence, `real-provider-${mode}-`))
    const { executable, pidfile } = await forkExecutable(dir, mode !== 'normal')
    const handles = []
    let spawned
    const started = new Promise(resolve => { spawned = resolve })
    const seam = { spawn(spec) {
      const handle = runtime.spawn(spec)
      handles.push(handle)
      // Wait for the actual fork handshake, not a mock spawn callback.
      const poll = setInterval(async () => {
        try { await readFile(pidfile); clearInterval(poll); spawned() } catch { /* bounded by test timeout */ }
      }, 10)
      t.after(() => clearInterval(poll))
      return handle
    } }
    const reader = new TimelineReader(seam)
    t.after(async () => {
      for (const h of handles) h.terminate()
      await Promise.all(handles.map(h => h.waitForExit(AbortSignal.timeout(5000))))
    })
    const controller = new AbortController()
    const reading = reader.read(executable, join(dir, 'timeline.json'), 'capabilities', undefined, controller.signal)
    const settled = reading.then(value => ({ value }), error => ({ error }))
    await started
    const pid = Number(await readFile(pidfile, 'utf8'))
    if (mode === 'cancel') controller.abort()
    if (mode === 'deadline') controller.abort(new DOMException('Preview read deadline exceeded', 'TimeoutError'))
    const disposed = mode === 'unload' ? reader.dispose() : undefined
    const outcome = await settled
    if (disposed) await disposed
    if (mode === 'normal') assert.equal(outcome.value.ok, true)
    else assert.equal(outcome.error.name, mode === 'deadline' ? 'TimeoutError' : 'AbortError')
    const afterRead = await stopped(pid)
    assert.equal(afterRead, true, `descendant ${pid} survived read settlement`)
    await reader.dispose()
    const afterDispose = await stopped(pid)
    assert.equal(afterDispose, true)
    assert.equal(reader.active.size, 0)
    const result = { mode, pid, afterReadStopped: afterRead, afterDisposeStopped: afterDispose, active: reader.active.size }
    await writeFile(join(dir, 'result.json'), JSON.stringify(result))
    console.log(JSON.stringify(result))
  })
}
