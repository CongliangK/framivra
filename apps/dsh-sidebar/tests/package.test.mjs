import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdir, mkdtemp, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'

const script = fileURLToPath(new URL('../scripts/package.mjs', import.meta.url))

test('ZIP has root manifest/ESM entries/license without scripts/dependencies/checkout runtime', async () => {
  const evidence = process.env.SIDEBAR_EVIDENCE_DIR ?? '/tmp/framivra-orchestrate-evidence/dsh-sidebar'
  await mkdir(evidence, { recursive: true })
  const dir = await mkdtemp(join(evidence, 'package-test-'))
  const client = join(dir, 'TEST-ONLY-not-a-client.js'), output = join(dir, 'TEST-ONLY-not-installable.zip')
  await writeFile(client, '// Packaging input fixture only; not a DSH client.\nwindow.testFixture = true;\n')
  const result = spawnSync(process.execPath, [script, '--client', client, '--out', output], { encoding: 'utf8' })
  assert.equal(result.status, 0, result.stderr)
  const inspect = spawnSync('python3', ['-c', `import json, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as archive:
    print(json.dumps({'names': archive.namelist(), 'manifest': json.loads(archive.read('package.json')), 'host': archive.read('lib/index.js').decode()}))
`, output], { encoding: 'utf8' })
  assert.equal(inspect.status, 0, inspect.stderr)
  const data = JSON.parse(inspect.stdout)
  assert.deepEqual(data.names, ['LICENSE', 'README.md', 'cordis.patch.yml', 'lib/client.js', 'lib/host.js', 'lib/index.js', 'lib/json.js', 'lib/media.js', 'lib/protocol.js', 'package.json'])
  for (const key of ['scripts', 'dependencies', 'devDependencies']) assert.equal(Object.hasOwn(data.manifest, key), false)
  assert.equal(data.manifest.main, 'lib/index.js')
  assert.equal(data.manifest.dsh.bundle.patch, './cordis.patch.yml')
  assert.ok(data.host.includes("from './host.js'"))
  assert.equal(data.host.includes('/home/'), false)
  // Execute the packaged Host dependency graph without a source checkout import.
  const { stage } = JSON.parse(result.stdout)
  const runtime = spawnSync(process.execPath, ['--input-type=module', '-e', `
    import { decodeEnvelope } from ${JSON.stringify(join(stage, 'lib/protocol.js'))};
    import { stringifyJSON } from ${JSON.stringify(join(stage, 'lib/json.js'))};
    const text = '{"schema":"framivra.timeline.read/v1","operation":"capabilities","ok":true,"capabilities":{"operations":["inspect","query"]},"extension":9007199254740993}';
    if (!stringifyJSON(decodeEnvelope(text, 'capabilities')).includes('9007199254740993')) process.exit(1);
  `], { encoding: 'utf8' })
  assert.equal(runtime.status, 0, runtime.stderr)
  const overwrite = spawnSync(process.execPath, [script, '--client', client, '--out', output], { encoding: 'utf8' })
  assert.notEqual(overwrite.status, 0)
  assert.ok(overwrite.stderr.includes('FileExistsError'))
})
