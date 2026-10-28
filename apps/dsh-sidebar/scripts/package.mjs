#!/usr/bin/env node
/** Package an SDK-produced browser artifact. This script never emits a DSH loader. */
import { readFile, mkdir, writeFile, mkdtemp, copyFile, stat } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, isAbsolute, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'

const root = fileURLToPath(new URL('../', import.meta.url))
const args = process.argv.slice(2)
if (args.length !== 4 || args[0] !== '--client' || args[2] !== '--out' || !isAbsolute(args[1]) || !isAbsolute(args[3]) || !args[3].endsWith('.zip')) {
  throw new Error('Usage: node scripts/package.mjs --client /absolute/SDK-output/client.js --out /absolute/new-plugin.zip')
}
const client = resolve(args[1]), output = resolve(args[3])
if (!(await stat(client)).isFile()) throw new Error('SDK browser artifact must be a regular file')
const clientSource = await readFile(client, 'utf8')
if (!clientSource.trim() || clientSource.includes('sourceMappingURL=') || clientSource.includes('/home/') || clientSource.includes('node_modules/')) throw new Error('Browser artifact is empty or contains source-map/checkout paths')
const manifest = JSON.parse(await readFile(join(root, 'package.json'), 'utf8'))
// Deliberate allowlist: source scripts/dependencies/devDependencies never enter the release.
const release = Object.fromEntries(['name', 'version', 'description', 'type', 'main', 'exports', 'engines', 'license', 'files', 'dsh'].map(key => [key, manifest[key]]))
const stage = await mkdtemp(join(tmpdir(), 'framivra-sidebar-release-'))
await mkdir(join(stage, 'lib'))
for (const name of ['index', 'host', 'protocol', 'media', 'json']) {
  const source = await readFile(join(root, 'src', `${name}.mjs`), 'utf8')
  await writeFile(join(stage, 'lib', `${name}.js`), source.replace(/from '(\.\/[^']+)\.mjs'/g, "from '$1.js'"))
}
await writeFile(join(stage, 'package.json'), `${JSON.stringify(release, null, 2)}\n`)
await copyFile(client, join(stage, 'lib', 'client.js'))
for (const name of ['cordis.patch.yml', 'LICENSE', 'README.md']) await copyFile(join(root, name), join(stage, name))
await mkdir(dirname(output), { recursive: true })
// Python stdlib is a build-time dependency only. Mode 'x' refuses to overwrite evidence.
const zip = spawnSync('python3', ['-c', `import pathlib, sys, zipfile
root = pathlib.Path(sys.argv[1])
with zipfile.ZipFile(sys.argv[2], 'x', compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(root.rglob('*')):
        if path.is_file():
            archive.write(path, path.relative_to(root).as_posix())
`, stage, output], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] })
if (zip.error || zip.status !== 0) throw new Error(`ZIP creation failed: ${zip.error?.message ?? zip.stderr}`)
console.log(JSON.stringify({ output, stage, client, name: manifest.name, version: manifest.version }))
