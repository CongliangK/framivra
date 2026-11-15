import test from 'node:test'
import assert from 'node:assert/strict'
import { setup } from './helpers.mjs'

// One-level workspace browse through op 'browse': Host-owned Session cwd,
// canonical containment per entry (resolved symlink targets), bounded
// listings, directories plus .json files only, zero subprocess spawns — a
// file picker, never a second timeline parser (opening goes through 'open').

const browse = (f, path, sessionId = 'one') => f.control({ op: 'browse', path, sessionId })
const body = async response => ({ status: response.status, value: await response.json() })

test('root browse lists directories and .json files with workspace-relative paths', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  const { status, value } = await body(await browse(f, ''))
  assert.equal(status, 200)
  assert.equal(value.ok, true)
  assert.equal(value.schema, 'framivra.browse/v1')
  assert.equal(value.path, '')
  // notes.txt (non-json file) is filtered; sub and timeline.json remain.
  assert.deepEqual(value.entries, [
    { name: 'sub', path: 'sub', type: 'directory' },
    { name: 'timeline.json', path: 'timeline.json', type: 'file' },
  ])
  assert.equal(value.truncated, false)
  assert.equal(value.escaped, 0)
  assert.equal(value.limit, 500)
  // Absent and null paths both mean the workspace root.
  assert.equal((await body(await f.control({ op: 'browse' }))).value.entries.length, 2)
  assert.equal((await body(await browse(f, null))).value.entries.length, 2)
  assert.equal(f.subprocess.calls.length, 0)
})

test('one level down returns joined relative paths', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  const { status, value } = await body(await browse(f, 'sub'))
  assert.equal(status, 200)
  assert.equal(value.path, 'sub')
  assert.deepEqual(value.entries, [{ name: 'nested.json', path: 'sub/nested.json', type: 'file' }])
  assert.equal(f.subprocess.calls.length, 0)
})

test('escaping symlinks are counted and omitted; contained aliases resolve', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  f.fs.files.set('/outside/secret.json', { type: 'file', version: 'secret' })
  f.fs.files.set('/workspace/link.json', { type: 'file', version: 'link' })
  f.fs.aliases.set('/workspace/link.json', '/outside/secret.json')
  f.fs.files.set('/workspace/aliasdir', { type: 'directory', version: 'alias' })
  f.fs.aliases.set('/workspace/aliasdir', '/workspace/sub')
  const { value } = await body(await browse(f, ''))
  assert.equal(value.escaped, 1)
  assert.deepEqual(value.entries.map(entry => entry.name), ['aliasdir', 'sub', 'timeline.json'])
  assert.equal(value.entries.find(entry => entry.name === 'aliasdir').type, 'directory')
  assert.equal(value.entries.some(entry => entry.name === 'link.json'), false)
})

test('malformed browse paths are rejected before any listing or spawn', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  for (const path of ['/etc', '..', '.', 'a/../b', 'a//b', 'a\\b', 'a:b', 'x\0y', 'sub/', 5, {}, true]) {
    const { status, value } = await body(await browse(f, path))
    assert.equal(status, 400, JSON.stringify(path))
    assert.equal(value.error, 'BAD_PATH')
  }
  assert.equal(f.subprocess.calls.length, 0)
})

test('directory alias resolving outside the workspace is PATH_ESCAPE', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  f.fs.files.set('/workspace/outside', { type: 'directory', version: 'outside' })
  f.fs.files.set('/elsewhere/dir', { type: 'directory', version: 'elsewhere' })
  f.fs.aliases.set('/workspace/outside', '/elsewhere/dir')
  const { status, value } = await body(await browse(f, 'outside'))
  assert.equal(status, 403)
  assert.equal(value.error, 'PATH_ESCAPE')
})

test('browsing a file target is NOT_A_DIRECTORY; ghost sessions are BAD_SESSION; unknown op unchanged', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  assert.equal((await body(await browse(f, 'timeline.json'))).status, 400)
  assert.equal((await body(await browse(f, ''))).value.error, undefined) // sanity
  const missing = await body(await browse(f, '', 'missing'))
  assert.equal(missing.status, 404)
  assert.equal(missing.value.error, 'BAD_SESSION')
  // An unknown op without a grant still fails at the grant gate (existing
  // contract); browse must not have swallowed the dispatch order.
  const unknown = await body(await f.control({ op: 'nosuch' }))
  assert.equal(unknown.status, 403)
  assert.equal(unknown.value.error, 'BAD_GRANT')
  assert.equal(f.subprocess.calls.length, 0)
})

test('unaddressable entry names are omitted; .json matching is case-insensitive', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  // Names that relativeBrowsePath could never accept must never be returned —
  // choosing such an entry would only yield BAD_PATH downstream.
  f.fs.files.set('/workspace/.', { type: 'directory', version: 'dot' })
  f.fs.files.set('/workspace/..', { type: 'directory', version: 'dotdot' })
  f.fs.files.set('/workspace/we:ird.json', { type: 'file', version: 'colon' })
  f.fs.files.set('/workspace/a\\b.json', { type: 'file', version: 'backslash' })
  f.fs.files.set('/workspace/nul\0x.json', { type: 'file', version: 'nul' })
  f.fs.files.set('/workspace/UPPER.JSON', { type: 'file', version: 'upper' })
  const { value } = await body(await browse(f, ''))
  const names = value.entries.map(entry => entry.name)
  assert.equal(names.includes('.'), false)
  assert.equal(names.includes('..'), false)
  assert.equal(names.some(name => name.includes(':') || name.includes('\\') || name.includes('\0')), false)
  assert.equal(names.includes('UPPER.JSON'), true)
  assert.equal(value.entries.find(entry => entry.name === 'UPPER.JSON').type, 'file')
})

test('listings are bounded and report truncation', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  f.fs.files.set('/workspace/bulk', { type: 'directory', version: 'bulk' })
  for (let index = 0; index < 600; index++) f.fs.files.set(`/workspace/bulk/f-${index}.json`, { type: 'file', version: String(index) })
  const { value } = await body(await browse(f, 'bulk'))
  assert.equal(value.entries.length, 500)
  assert.equal(value.truncated, true)
  assert.equal(value.limit, 500)
  // Each entry still carries its joined relative path.
  assert.equal(value.entries[0].path.startsWith('bulk/f-'), true)
})

test('cancelled browse surfaces as CANCELLED', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  const controller = new AbortController()
  controller.abort()
  const response = await f.host.control(new Request('http://owner/api/framivra/control', {
    method: 'POST', signal: controller.signal,
    body: JSON.stringify({ sessionId: 'one', op: 'browse', path: '' }),
  }))
  assert.equal(response.status, 499)
  assert.equal((await response.json()).error, 'CANCELLED')
})
