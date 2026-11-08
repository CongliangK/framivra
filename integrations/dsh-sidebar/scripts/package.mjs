#!/usr/bin/env node
/** Build the official public client factory and atomically publish a distributable ZIP. */
import { readFile, mkdir, writeFile, mkdtemp, copyFile, link, lstat } from 'node:fs/promises'
import { dirname, isAbsolute, join } from 'node:path'
import { spawnSync } from 'node:child_process'
import { buildClient, packageDir } from './build.mjs'

const args = process.argv.slice(2)
if (args.length !== 2 || args[0] !== '--out' || !isAbsolute(args[1]) || !args[1].endsWith('.zip')) {
  throw new Error('Usage: node scripts/package.mjs --out /absolute/new-plugin.zip')
}
const output = args[1]
try {
  await lstat(output)
  throw new Error(`Output already exists: ${output}`)
} catch (error) {
  if (error.code !== 'ENOENT') throw error
}
await mkdir(dirname(output), { recursive: true })
// Stage on the destination filesystem: link() publishes atomically and refuses overwrite.
const work = await mkdtemp(join(dirname(output), '.framivra-sidebar-'))
const stage = join(work, 'package')
await mkdir(join(stage, 'lib'), { recursive: true })
await buildClient(join(work, 'client-build'))
const client = join(work, 'client-build', 'client.js')
// Distribution ZIP omits debug maps/source paths; the factory uses the official loader protocol.
const clientSource = (await readFile(client, 'utf8')).replace(/^\/\/# sourceMappingURL=.*(?:\r?\n|$)/gm, '')
if (!clientSource.trim() || clientSource.includes('sourceMappingURL=') || clientSource.includes('/home/') || clientSource.includes('node_modules/')) {
  throw new Error('Official client factory is empty or contains checkout paths')
}
const manifest = JSON.parse(await readFile(join(packageDir, 'package.json'), 'utf8'))
// Source scripts and all build/test dependencies stay outside the release.
const release = Object.fromEntries(['name', 'version', 'description', 'type', 'main', 'exports', 'engines', 'license', 'files', 'peerDependencies', 'dsh'].map(key => [key, manifest[key]]))
for (const name of ['index', 'configuration', 'host', 'protocol', 'media', 'json', 'bounded', 'agent', 'tools']) {
  const source = await readFile(join(packageDir, 'src', `${name}.mjs`), 'utf8')
  await writeFile(join(stage, 'lib', `${name}.js`), source.replace(/from '(\.\/[^']+)\.mjs'/g, "from '$1.js'"))
}
await writeFile(join(stage, 'package.json'), `${JSON.stringify(release, null, 2)}\n`)
await writeFile(join(stage, 'lib', 'client.js'), clientSource)
for (const name of ['cordis.patch.yml', 'LICENSE', 'README.md']) await copyFile(join(packageDir, name), join(stage, name))
const archive = join(work, 'candidate.zip')
const zip = spawnSync('python3', ['-c', `import pathlib, sys, zipfile
root = pathlib.Path(sys.argv[1])
with zipfile.ZipFile(sys.argv[2], 'x', compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(root.rglob('*')):
        if path.is_file():
            archive.write(path, path.relative_to(root).as_posix())
`, stage, archive], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] })
if (zip.error || zip.status !== 0) throw new Error(`ZIP creation failed: ${zip.error?.message ?? zip.stderr}`)
await link(archive, output)
console.log(JSON.stringify({ output, stage, client, name: manifest.name, version: manifest.version }))
