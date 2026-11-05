import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdir, mkdtemp, readFile, stat } from 'node:fs/promises'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'

const script = fileURLToPath(new URL('../scripts/package.mjs', import.meta.url))
const runtime = fileURLToPath(new URL('./package-runtime.mjs', import.meta.url))
const evidence = process.env.SIDEBAR_EVIDENCE_DIR ?? '/tmp/framivra-sidebar-tests'
const run = (args, options = {}) => spawnSync(process.execPath, args, { encoding: 'utf8', timeout: 30000, ...options })

// Build and exercise an actual SDK factory, never a dummy or a source-view substitute.
test('SDK ZIP executes its Host closure and materializes a shared-React sidebar with real UI reads', async () => {
  await mkdir(evidence, { recursive: true })
  const dir = await mkdtemp(join(evidence, 'package-test-'))
  const output = join(dir, 'candidate.zip'), extracted = join(dir, 'extracted')
  const result = run([script, '--out', output])
  assert.equal(result.status, 0, result.stderr + result.stdout)
  const inspect = spawnSync('python3', ['-c', `import json, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as archive:
    print(json.dumps({'names': archive.namelist(), 'manifest': json.loads(archive.read('package.json')), 'patch': archive.read('cordis.patch.yml').decode()}))
    archive.extractall(sys.argv[2])
`, output, extracted], { encoding: 'utf8' })
  assert.equal(inspect.status, 0, inspect.stderr)
  const data = JSON.parse(inspect.stdout)
  assert.deepEqual(data.names, ['LICENSE', 'README.md', 'cordis.patch.yml', 'lib/bounded.js', 'lib/client.js', 'lib/host.js', 'lib/index.js', 'lib/json.js', 'lib/media.js', 'lib/protocol.js', 'package.json'])
  for (const key of ['scripts', 'dependencies', 'devDependencies']) assert.equal(Object.hasOwn(data.manifest, key), false)
  assert.deepEqual(data.manifest.peerDependencies, { react: '18.3.1' })
  assert.equal(data.manifest.main, 'lib/index.js')
  assert.equal(data.manifest.exports['.'], './lib/index.js')
  assert.equal(data.manifest.exports['./client'], './lib/client.js')
  assert.equal(data.manifest.dsh.bundle.patch, './cordis.patch.yml')
  assert(data.patch.includes('framivra-dsh-sidebar'))
  const client = await readFile(join(extracted, 'lib/client.js'), 'utf8')
  for (const forbidden of ['sourceMappingURL=', '/home/', 'node_modules/', 'TEST-ONLY', 'window.testFixture']) assert.equal(client.includes(forbidden), false, forbidden)
  // A new Node process in the extracted ZIP needs neither dependencies nor checkout.
  const host = run(['--input-type=module', '-e', `
    import { apply, inject } from './lib/index.js';
    import { decodeEnvelope } from './lib/protocol.js';
    import { stringifyJSON } from './lib/json.js';
    if (typeof apply !== 'function' || inject.length !== 5) throw Error('Host exports');
    const text = '{"schema":"framivra.timeline.read/v1","operation":"capabilities","ok":true,"capabilities":{"operations":["inspect","query"]},"extension":9007199254740993}';
    if (!stringifyJSON(decodeEnvelope(text, 'capabilities')).includes('9007199254740993')) throw Error('Wire loss');
  `], { cwd: extracted, env: { PATH: process.env.PATH } })
  assert.equal(host.status, 0, host.stderr)
  const integration = run([runtime, extracted])
  assert.equal(integration.status, 0, integration.stderr + integration.stdout)
  assert.equal(JSON.parse(integration.stdout.trim().split('\n').at(-1)).uiOpenQueryClose, true)
  const original = await readFile(output)
  const overwrite = run([script, '--out', output])
  assert.notEqual(overwrite.status, 0)
  assert(overwrite.stderr.includes('Output already exists:'))
  assert.deepEqual(await readFile(output), original)
})

test('packaging cannot inject arbitrary Client input or publish an incomplete ZIP', async () => {
  await mkdir(evidence, { recursive: true })
  const dir = await mkdtemp(join(evidence, 'package-failure-'))
  const output = join(dir, 'never-published.zip')
  const arbitrary = run([script, '--client', '/tmp/arbitrary.js', '--out', output])
  assert.notEqual(arbitrary.status, 0)
  assert(arbitrary.stderr.includes('Usage: node scripts/package.mjs --out'))
  await assert.rejects(stat(output), { code: 'ENOENT' })
  // Late failure after SDK compilation must still leave the final pathname absent.
  const failed = run([script, '--out', output], { env: { ...process.env, PATH: '' } })
  assert.notEqual(failed.status, 0)
  assert(failed.stderr.includes('ZIP creation failed: spawnSync python3 ENOENT'), failed.stderr)
  await assert.rejects(stat(output), { code: 'ENOENT' })
})
