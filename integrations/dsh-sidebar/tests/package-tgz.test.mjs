import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdir, mkdtemp, readFile, realpath, stat, symlink } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'

const script = fileURLToPath(new URL('../scripts/package.mjs', import.meta.url))
const runtime = fileURLToPath(new URL('./package-runtime.mjs', import.meta.url))
const evidence = process.env.SIDEBAR_EVIDENCE_DIR ?? '/tmp/framivra-sidebar-tests'
const run = (args, options = {}) => spawnSync(process.execPath, args, {
  encoding: 'utf8', timeout: 60000,
  // Production is for artifacts only; the React test carrier needs act().
  env: { ...process.env, NODE_ENV: args[0] === script ? 'production' : 'test', SIDEBAR_DSH_ROOT: '' }, ...options,
})
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex')
const members = ['LICENSE', 'README.md', 'THIRD-PARTY-NOTICES.md', 'cordis.patch.yml', 'icon.svg',
  'lib/agent.js', 'lib/bounded.js', 'lib/client.js', 'lib/configuration.js', 'lib/host.js',
  'lib/index.js', 'lib/json.js', 'lib/kernel-config.js', 'lib/kernel-management.js', 'lib/media.js', 'lib/protocol.js', 'lib/tools.js',
  'locale/en.json', 'locale/zh.json', 'package.json']

// Additional format coverage; the existing ZIP/runtime/core tests remain unchanged.
test('standard npm tarball has the exact ZIP payload and executes the shared-React SDK factory', async () => {
  await mkdir(evidence, { recursive: true })
  const dir = await mkdtemp(join(evidence, 'package-tgz-'))
  const output = join(dir, 'candidate.tgz'), zip = join(dir, 'candidate.zip'), extracted = join(dir, 'extracted')
  const packed = run([script, '--out', output])
  assert.equal(packed.status, 0, packed.stderr + packed.stdout)
  const report = JSON.parse(packed.stdout.trim())
  const original = await readFile(output)
  assert.equal(report.format, 'tgz')
  assert.equal(report.sha256, sha256(original))
  assert.equal(report.bytes, original.length)
  const zipped = run([script, '--out', zip])
  assert.equal(zipped.status, 0, zipped.stderr + zipped.stdout)
  const inspect = spawnSync('python3', ['-I', '-B', '-c', `import json, pathlib, sys, tarfile, zipfile
root = pathlib.Path(sys.argv[3])
with tarfile.open(sys.argv[1], 'r:gz') as archive, zipfile.ZipFile(sys.argv[2]) as zipped:
    names = []
    for member in archive.getmembers():
        path = pathlib.PurePosixPath(member.name)
        assert member.isfile() and path.parts[0] == 'package'
        assert not path.is_absolute() and '..' not in path.parts
        name = path.relative_to('package').as_posix()
        names.append(name)
        data = archive.extractfile(member).read()
        assert data == zipped.read(name), name
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    assert sorted(names) == sorted(zipped.namelist())
    print(json.dumps(sorted(names)))
`, output, zip, extracted], { encoding: 'utf8', timeout: 30000 })
  assert.equal(inspect.status, 0, inspect.stderr)
  assert.deepEqual(JSON.parse(inspect.stdout), members)
  const manifest = JSON.parse(await readFile(join(extracted, 'package.json'), 'utf8'))
  for (const key of ['private', 'scripts', 'dependencies', 'devDependencies']) assert.equal(Object.hasOwn(manifest, key), false, key)
  assert.equal(manifest.name, 'framivra-dsh-sidebar')
  assert.equal(manifest.version, '0.2.1')
  assert.deepEqual(manifest.publishConfig, { access: 'public', registry: 'https://registry.npmjs.org/' })
  assert.equal(manifest.repository.directory, 'integrations/dsh-sidebar')
  assert.equal(manifest.repository.url, 'git+https://github.com/CongliangK/framivra.git')
  assert.equal(manifest.dsh.bundle.patch, './cordis.patch.yml')
  assert.deepEqual(manifest.engines, { node: '>=24.9.0', dsh: '0.2.2-rc.1' })
  const require = createRequire(import.meta.url)
  await mkdir(join(extracted, 'node_modules/@deepseek-ai'), { recursive: true })
  await symlink(dirname(await realpath(require.resolve('@deepseek-ai/schemastery/package.json'))), join(extracted, 'node_modules/@deepseek-ai/schemastery'), 'dir')
  const host = run(['--input-type=module', '-e', `
    import { apply } from './lib/index.js';
    import { resolveBuildStamp } from './lib/host.js';
    if (typeof apply !== 'function' || resolveBuildStamp() !== ${JSON.stringify(report.build)}) throw Error('Host identity');
  `], { cwd: extracted })
  assert.equal(host.status, 0, host.stderr)
  const client = await readFile(join(extracted, 'lib/client.js'), 'utf8')
  assert(client.includes(JSON.stringify(report.build)))
  for (const forbidden of ['sourceMappingURL=', '/home/', 'node_modules/']) assert(!client.includes(forbidden), forbidden)
  const integration = run([runtime, extracted])
  assert.equal(integration.status, 0, integration.stderr + integration.stdout)
  assert.equal(JSON.parse(integration.stdout.trim().split('\n').at(-1)).uiOpenQueryClose, true)
  const secondDir = await mkdtemp(join(evidence, 'package-tgz-rebuild-'))
  const second = join(secondDir, 'rebuilt.tgz')
  const rebuilt = run([script, '--out', second])
  assert.equal(rebuilt.status, 0, rebuilt.stderr)
  assert.equal(sha256(await readFile(second)), report.sha256, 'same toolchain/input, different staging directory')
  const overwrite = run([script, '--out', output])
  assert.notEqual(overwrite.status, 0)
  assert.match(overwrite.stderr, /Output already exists:/)
  assert.deepEqual(await readFile(output), original)
})

test('npm pack failure leaves no final tarball and arbitrary client injection is refused', async () => {
  await mkdir(evidence, { recursive: true })
  const dir = await mkdtemp(join(evidence, 'package-tgz-failure-'))
  const output = join(dir, 'never-published.tgz')
  const arbitrary = run([script, '--client', '/tmp/arbitrary.js', '--out', output])
  assert.notEqual(arbitrary.status, 0)
  assert.match(arbitrary.stderr, /Usage: node scripts\/package.mjs --out/)
  await assert.rejects(stat(output), { code: 'ENOENT' })
  const failed = run([script, '--out', output], { env: { ...process.env, NODE_ENV: 'production', SIDEBAR_DSH_ROOT: '', PATH: '' } })
  assert.notEqual(failed.status, 0)
  assert.match(failed.stderr, /npm pack failed: spawnSync npm ENOENT/)
  await assert.rejects(stat(output), { code: 'ENOENT' })
})
