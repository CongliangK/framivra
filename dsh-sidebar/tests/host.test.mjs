import test from 'node:test'
import assert from 'node:assert/strict'
import { setup } from './helpers.mjs'
import { apply, Config } from '../src/index.mjs'
import { readConfiguration } from '../src/configuration.mjs'

const validate = Config['~standard'].validate

test('Config: defaults, host-trusted forms, and rejected shapes', async () => {
  // Volatile fields validate to live cells, not plain strings; defaults are
  // asserted through the same configuration home the Host applies path uses.
  const defaults = await validate({})
  assert.deepEqual(readConfiguration(defaults.value), readConfiguration({}))
  assert.equal(Config.dict.vtxExecutable.meta.volatile, true)
  assert.equal(Config.dict.trustedPath.meta.volatile, true)
  for (const vtxExecutable of ['vtx', '/opt/trusted/vtx', '/', 'C:\\tools\\vtx.exe', '\\\\server\\share\\vtx']) {
    const accepted = await validate({ vtxExecutable })
    assert.equal(accepted.issues, undefined, vtxExecutable)
  }
  for (const vtxExecutable of ['./vtx', 'vtxx', 'vtx ', '', ' vtx']) {
    const rejected = await validate({ vtxExecutable })
    assert.notEqual(rejected.issues, undefined, vtxExecutable)
  }
  for (const trustedPath of ['/usr/local/bin:/usr/bin:/bin', '/', '/opt/bin', '/a:/', '/a://deep']) {
    const accepted = await validate({ trustedPath })
    assert.equal(accepted.issues, undefined, trustedPath)
  }
  for (const trustedPath of ['', 'relative/bin', '/a::b', '/a:', ':/a', '/a:rel', '/a: b']) {
    const rejected = await validate({ trustedPath })
    assert.notEqual(rejected.issues, undefined, trustedPath)
  }
})

const open = async fixture => {
  const response = await fixture.control({ op: 'open', timeline: 'timeline.json' })
  assert.equal(response.status, 200, await response.clone().text())
  return response.json()
}

test('real fixture child: open -> inspect -> all tracks -> byte Range -> close', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  const opened = await open(f)
  assert.equal(opened.snapshot.document.extension.preserve, '<script>never HTML</script>')
  assert.equal(opened.snapshot.coverage.complete, false)
  assert.equal(opened.media.length, 1)
  assert.equal(opened.media[0].provenance, 'unknown')
  const query = await f.control({ op: 'query', grant: opened.grant, query: {} })
  const result = await query.json()
  assert.deepEqual(result.tracks.map(track => track.family), ['video', 'video', 'audio', 'audio', 'subtitles'])
  assert.equal(result.blocks.length, 5)
  assert.equal(result.tracks[4].track_index, 0)
  assert.equal(result.tracks[3].data.arbitrary_original_field, 3)
  const inspect = await f.control({ op: 'inspect', grant: opened.grant })
  assert.equal((await inspect.json()).content_version, opened.snapshot.content_version)
  const response = await f.media(opened.grant, opened.media[0].mediaId, { headers: { Range: 'bytes=20-40' } })
  assert.equal(response.status, 206)
  assert.equal(response.headers.get('content-range'), 'bytes 20-40/160000')
  assert.equal(response.headers.get('content-length'), '21')
  assert.deepEqual([...new Uint8Array(await response.arrayBuffer())], Array.from({ length: 21 }, (_, i) => i + 20))
  for (const spec of f.subprocess.calls.filter(call => call.argv)) {
    assert.equal(spec.argv[0], '/opt/trusted/vtx')
    assert.equal(spec.cwd, '/opt/trusted')
    assert.equal(spec.env.PYTHONDONTWRITEBYTECODE, '1')
    assert.equal(spec.env.PYTHONPATH, undefined)
    assert.equal(spec.stdio.stdin, 'ignore')
    assert.equal(spec.graceMs, 1000)
  }
  assert.equal((await f.control({ op: 'close', grant: opened.grant })).status, 200)
  assert.equal((await f.media(opened.grant, opened.media[0].mediaId)).status, 403)
})

test('deny URLs, NUL, traversal, absolute paths and escaping symlinks before spawning', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  for (const timeline of ['/etc/passwd', '../timeline.json', 'https://bad/timeline', 'file:///bad', 'x\0y', 'a/../timeline.json', 'a\\timeline.json']) {
    assert.equal((await f.control({ op: 'open', timeline })).status, 400, timeline)
  }
  f.fs.aliases.set('/workspace/link.json', '/outside/secret')
  assert.equal((await f.control({ op: 'open', timeline: 'link.json' })).status, 403)
  f.fs.files.set('/workspace/pipe.json', { type: 'other' })
  assert.equal((await f.control({ op: 'open', timeline: 'pipe.json' })).status, 400)
  assert.equal(f.subprocess.calls.length, 0)
})

test('grants deny cross-session, changed cwd, absent session, expiry and changed timeline', async t => {
  let now = 0
  const f = setup({ clock: () => now }); t.after(() => f.host.dispose())
  const opened = await open(f)
  assert.equal((await f.control({ op: 'inspect', grant: opened.grant, sessionId: 'two' })).status, 403)
  assert.equal((await f.media(opened.grant, opened.media[0].mediaId, {}, 'two')).status, 403)
  f.cwd.value = '/different'
  assert.equal((await f.control({ op: 'inspect', grant: opened.grant })).status, 403)
  f.cwd.value = '/workspace'
  f.fs.aliases.set('/workspace/timeline.json', '/outside/secret')
  assert.equal((await f.control({ op: 'inspect', grant: opened.grant })).status, 403)
  f.fs.aliases.clear()
  now = 30 * 60 * 1000
  assert.equal((await f.control({ op: 'inspect', grant: opened.grant })).status, 403)
  assert.equal((await f.control({ op: 'open', timeline: 'timeline.json', sessionId: 'missing' })).status, 404)
})

test('remote fs is explicit unsupported; workspace executable is rejected', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  f.fs.processPathFromHostPath = () => undefined
  const remote = await f.control({ op: 'open', timeline: 'timeline.json' })
  assert.equal(remote.status, 422)
  assert.equal((await remote.json()).error, 'REMOTE_UNSUPPORTED')
  f.fs.processPathFromHostPath = path => path
  f.ctx.subprocess.resolveExecutable = async () => '/workspace/vtx'
  const executable = await f.control({ op: 'open', timeline: 'timeline.json' })
  assert.equal(executable.status, 403)
  assert.equal((await executable.json()).error, 'UNTRUSTED_EXECUTABLE')
})

test('HEAD and invalid/multi ranges read zero bytes; streaming is pull-bounded and cancelled', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  const opened = await open(f)
  const id = opened.media[0].mediaId
  const head = await f.media(opened.grant, id, { method: 'HEAD', headers: { Range: 'bytes=-20' } })
  assert.equal(head.status, 206)
  assert.equal(head.headers.get('content-length'), '20')
  assert.equal((await head.arrayBuffer()).byteLength, 0)
  for (const Range of ['bytes=160000-', 'bytes=0-1,3-4', 'items=0-2', 'bytes=-0', 'bytes=9-1']) {
    const response = await f.media(opened.grant, id, { headers: { Range } })
    assert.equal(response.status, 416)
    assert.equal(response.headers.get('content-range'), 'bytes */160000')
  }
  assert.equal(f.fs.reads.length, 0)
  const response = await f.media(opened.grant, id)
  assert.equal(response.status, 200)
  assert.equal(response.headers.get('content-length'), '160000')
  assert.equal(f.fs.reads.length, 0)
  const stream = response.body.getReader()
  assert.equal((await stream.read()).value.byteLength, 65536)
  assert.equal(f.fs.reads.length, 1)
  await stream.cancel()
  assert.equal(f.fs.reads[0].signal.aborted, true)
  assert.equal(f.fs.reads.length, 1)
})

test('media ID/path injection, output symlink escape and changed media fail closed', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  const opened = await open(f)
  const id = opened.media[0].mediaId
  assert.equal((await f.media(opened.grant, '../../etc/passwd')).status, 403)
  const injection = await f.host.media(new Request(`http://owner/api/framivra/media?sessionId=one&grant=${opened.grant}&mediaId=${id}&path=/etc/passwd`))
  assert.equal(injection.status, 400)
  f.fs.aliases.set('/workspace/out/final.mp4', '/outside/secret.mp4')
  assert.equal((await f.media(opened.grant, id)).status, 403)
  f.fs.aliases.clear()
  f.fs.files.get('/workspace/out/final.mp4').version = 'changed'
  assert.equal((await f.media(opened.grant, id)).status, 409)
  assert.equal(f.fs.reads.length, 0)
})

test('DSH mounting uses only exact authenticated Connection Fetch routes and reversible effects', async () => {
  const routes = [], cleanup = [], tools = new Set()
  const ctx = { effect: effect => cleanup.push(effect()), tools: { register: tool => { tools.add(tool.name); return () => tools.delete(tool.name) } }, connection: { fetch: { register: route => { routes.push(route); return async () => {} } } }, subprocess: {} }
  apply(ctx)
  assert.deepEqual(routes.map(route => [route.path, route.methods, route.requestBody]), [
    ['/api/framivra/kernel-management', ['POST'], 'buffered'],
    ['/api/framivra/control', ['POST'], 'buffered'], ['/api/framivra/diagnostics', ['POST'], 'buffered'],
    ['/api/framivra/frame', ['POST'], 'streaming'],
    ['/api/framivra/media', ['GET', 'HEAD'], 'buffered'],
  ])
  assert.deepEqual([...tools], ['framivra_timeline_read', 'framivra_timeline_propose', 'framivra_proposal_status'])
  for (const dispose of cleanup.reverse()) await dispose()
  assert.equal(tools.size, 0)
})
