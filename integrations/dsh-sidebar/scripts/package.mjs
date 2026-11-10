#!/usr/bin/env node
/** Build the official public client factory and atomically publish a distributable ZIP. */
import { readFile, mkdir, writeFile, mkdtemp, copyFile, link, lstat } from 'node:fs/promises'
import { dirname, isAbsolute, join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { createHash } from 'node:crypto'
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
// Build with a unique placeholder stamp: the final artifact stamp covers the
// client bundle itself, so package.mjs first emits the bundle with a
// placeholder, hashes the normalized artifacts, then rewrites the SAME final
// stamp into BOTH lib/protocol.js (BAKED_BUILD_STAMP literal) and lib/client.js.
const STAMP_PLACEHOLDER = 'FRAMIVRA-PKG-STAMP-PLACEHOLDER'
await buildClient(join(work, 'client-build'), { clientStamp: STAMP_PLACEHOLDER })
const client = join(work, 'client-build', 'client.js')
// Distribution ZIP omits debug maps/source paths; the factory uses the official loader protocol.
const clientSource = (await readFile(client, 'utf8')).replace(/^\/\/# sourceMappingURL=.*(?:\r?\n|$)/gm, '')
if (!clientSource.trim() || clientSource.includes('sourceMappingURL=') || clientSource.includes('/home/') || clientSource.includes('node_modules/')) {
  throw new Error('Official client factory is empty or contains checkout paths')
}
const manifest = JSON.parse(await readFile(join(packageDir, 'package.json'), 'utf8'))
// Source scripts and all build/test dependencies stay outside the release.
const release = Object.fromEntries(['name', 'version', 'description', 'type', 'main', 'exports', 'engines', 'license', 'files', 'peerDependencies', 'dsh'].map(key => [key, manifest[key]]))
const libContents = new Map()
for (const name of ['index', 'configuration', 'host', 'protocol', 'media', 'json', 'bounded', 'agent', 'tools']) {
  const source = await readFile(join(packageDir, 'src', `${name}.mjs`), 'utf8')
  libContents.set(name, source.replace(/from '(\.\/[^']+)\.mjs'/g, "from '$1.js'"))
}
// Artifact stamp: sha256 over every staged lib file (BAKED marker and client
// placeholder still in place — both fixed constants, so the hash is
// reproducible) plus the stripped client bundle. Substitution happens after
// hashing; there is no circular dependency.
const stampHasher = createHash('sha256')
for (const name of [...libContents.keys()].sort()) stampHasher.update(libContents.get(name))
stampHasher.update(clientSource)
const artifactDigest = stampHasher.digest('hex').slice(0, 16)
const artifactStamp = `pkg:${artifactDigest}`
// Rewrite the BAKED_BUILD_STAMP assignment ONLY (never the BUILD_STAMP_MARKER
// constant it is compared against — a global replace would silently re-arm
// the dev fallback branch inside the packaged host).
const bakedAssignment = "export const BAKED_BUILD_STAMP = '__FRAMIVRA_BUILD_STAMP__'"
if (!libContents.get('protocol').includes(bakedAssignment)) throw new Error('package build stamp: BAKED_BUILD_STAMP assignment not found in protocol source')
libContents.set('protocol', libContents.get('protocol').replace(bakedAssignment, `export const BAKED_BUILD_STAMP = '${artifactDigest}'`))
const placeholderLiteral = JSON.stringify(STAMP_PLACEHOLDER)
const placeholderCount = clientSource.split(placeholderLiteral).length - 1
if (placeholderCount !== 1) throw new Error(`package build stamp: expected exactly one client stamp placeholder, found ${placeholderCount}`)
const stampedClientSource = clientSource.split(placeholderLiteral).join(JSON.stringify(artifactStamp))
for (const [name, content] of libContents) await writeFile(join(stage, 'lib', `${name}.js`), content)
await writeFile(join(stage, 'package.json'), `${JSON.stringify(release, null, 2)}\n`)
await writeFile(join(stage, 'lib', 'client.js'), stampedClientSource)
for (const name of ['cordis.patch.yml', 'LICENSE', 'README.md', 'THIRD-PARTY-NOTICES.md']) await copyFile(join(packageDir, name), join(stage, name))
// Self-check: the packaged host must report exactly the artifact stamp from
// its frozen BAKED_BUILD_STAMP, and the client bundle must carry the same
// value — the two sides of the handshake can never ship disagreeing.
{
  const { resolveBuildStamp } = await import(pathToFileURL(join(stage, 'lib', 'host.js')).href)
  const hostStamp = resolveBuildStamp()
  if (hostStamp !== artifactStamp) throw new Error(`package build stamp: packaged host reports ${hostStamp}, expected ${artifactStamp}`)
  if (!stampedClientSource.includes(JSON.stringify(artifactStamp))) throw new Error('package build stamp: client bundle does not carry the artifact stamp')
}
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
