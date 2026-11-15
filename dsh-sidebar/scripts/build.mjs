#!/usr/bin/env node
/**
 * Standalone client-bundle builder for the OFFICIAL DSH loader contract.
 *
 * Implements the public `@deepseek-ai/dsh-client-modules` lazy-factory
 * contract (validated with dsh-v0.2.2-rc.1) using the public `tsdown` API:
 *
 *   - Output is one lazy CommonJS factory registered through the host-injected
 *     facade `window.__ModuleLoader__.load({ id, factory })`
 *     (packages/client/modules/src/client/manifest.ts: ClientBundleRegistration).
 *     Executing the script only registers the factory; every module-body side
 *     effect stays inside the closure and runs at materialization.
 *   - Only this adapter's required implicit baseline (React) and explicitly
 *     declared `dsh.client.external` requests stay external. No copied list of
 *     private shell modules: React stays shared and is never bundled twice.
 *   - Undeclared `@deepseek-ai/*` runtime imports fail the build (fail closed:
 *     cross-plugin value imports are forbidden; type-only imports are erased).
 *
 * The bundler itself is resolved from this package's devDependencies or, when
 * SIDEBAR_DSH_ROOT points at a DSH checkout with dependencies installed, from
 * that checkout — a build-time tool path only, never a runtime dependency.
 */
import { fileURLToPath, pathToFileURL } from 'node:url'
import { readFileSync, statSync, rmSync } from 'node:fs'
import { resolve, join } from 'node:path'
import { createRequire } from 'node:module'
import { createHash } from 'node:crypto'
import { HOST_LIB_MODULES, CLIENT_MODULES, composeBuildStamp } from '../src/protocol.mjs'

export const packageDir = fileURLToPath(new URL('../', import.meta.url))

/**
 * Deterministic build stamp over the dev/source tree — the EXACT recipe of
 * src/host.mjs computeSourceStampFromDir (same module lists from
 * src/protocol.mjs, same digest joining), so a client bundle built here and a
 * host running from the same source carry the same 'src:<hash>' stamp.
 * Returns null when the stamped module set is not fully present (synthetic
 * build-fixture packages with a minimal src/): those bundles simply carry no
 * stamp and never take part in build comparison. scripts/package.mjs
 * overrides this with a placeholder via options.clientStamp and rewrites it
 * to the final packaged artifact stamp on BOTH sides.
 */
export function computeSourceStamp(dir) {
  const root = resolve(dir)
  const digests = []
  const read = path => {
    try { return readFileSync(join(root, path)) } catch { return null }
  }
  for (const name of HOST_LIB_MODULES) {
    const content = read(join('src', `${name}.mjs`))
    if (content === null) return null
    digests.push(createHash('sha256').update(content).digest('hex'))
  }
  for (const name of CLIENT_MODULES) {
    const content = read(join('src', 'client', `${name}.mjs`))
    if (content === null) return null
    digests.push(createHash('sha256').update(content).digest('hex'))
  }
  return composeBuildStamp('src:', createHash('sha256').update(digests.join('\n')).digest('hex'))
}

/** Minimal implicit client baseline consumed by this adapter; other requests must be declared. */
export const PLATFORM_MODULES = ['react']

/**
 * The exact module-table words one client bundle may request: the official
 * platform seed plus this package's declared `dsh.client.external`.
 * @throws {Error} when the manifest or its external declaration is invalid.
 */
export function clientModuleRequests(dir = packageDir) {
  const manifestPath = resolve(dir, 'package.json')
  let manifest
  try {
    manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
  } catch (error) {
    throw new Error(`client plugin build: cannot read ${manifestPath}`, { cause: error })
  }
  if (typeof manifest !== 'object' || manifest === null
    || !('name' in manifest) || typeof manifest.name !== 'string' || manifest.name.length === 0) {
    throw new Error(`client plugin build: ${manifestPath} declares no nonempty string package name`)
  }
  return new Set([...PLATFORM_MODULES, ...manifestExternals(manifest, manifestPath)])
}

/**
 * Fail-closed purity gate on the EMITTED bundle: every module-table request in
 * the final code must be an exact official platform word or a declared
 * external. (A rolldown resolveId throw is silently swallowed and the module
 * falls back to an external require, so the check must run after emission.)
 * @param {string} code - the emitted client.js source.
 * @param {Set<string>} requested - allowed module-table words.
 * @returns {Set<string>} the exact requests the bundle makes.
 */
export function assertBundlePurity(code, requested) {
  const requests = new Set([...code.matchAll(/\brequire\("([^"]+)"\)/g)].map(match => match[1]))
  for (const specifier of requests) {
    if (!requested.has(specifier)) {
      throw new Error(
        `client bundle purity: "${specifier}" is not in the official client module table (PLATFORM_MODULES) or the package's dsh.client.external — `
        + 'cross-plugin value imports are forbidden; declare a non-default module request or collaborate through cordis services '
        + '(type-only imports are erased and never reach this gate)',
      )
    }
  }
  return requests
}
function clientFactoryOutput(id) {
  return {
    banner: chunk => `window.__ModuleLoader__.load({ id: ${JSON.stringify(id)}${chunk.isEntry ? '' : `, chunk: ${JSON.stringify(chunk.fileName)}`}, factory: (require) => {`,
    footer: 'return module.exports; } });',
    intro: 'var module = { exports: {} }; var exports = module.exports;',
  }
}

/** Validate the optional `dsh.client.external` declaration before resolving anything. */
function manifestExternals(manifest, manifestPath) {
  if (!('dsh' in manifest) || manifest.dsh === undefined) return []
  if (typeof manifest.dsh !== 'object' || manifest.dsh === null) {
    throw new Error(`client plugin build: ${manifestPath} dsh must be an object`)
  }
  if (!('client' in manifest.dsh) || manifest.dsh.client === undefined) return []
  const client = manifest.dsh.client
  if (typeof client !== 'object' || client === null) {
    throw new Error(`client plugin build: ${manifestPath} dsh.client must be an object`)
  }
  if (!('external' in client) || client.external === undefined) return []
  const external = client.external
  if (!Array.isArray(external) || external.some(item => typeof item !== 'string' || item.length === 0)) {
    throw new Error(`client plugin build: ${manifestPath} dsh.client.external must be a nonempty-string array`)
  }
  for (const item of external) {
    if (item.startsWith('./') || item.startsWith('../') || item.startsWith('/') || item.startsWith('node:')) {
      throw new Error(`client plugin build: ${manifestPath} dsh.client.external entry ${JSON.stringify(item)} is not a module-table word`)
    }
  }
  return external
}

/**
 * Resolve the public bundler without a runtime DSH dependency.
 * @param {string | undefined} dshRoot - explicit DSH checkout root (SIDEBAR_DSH_ROOT).
 * @returns {Promise<typeof import('tsdown')>} the tsdown module (build + defineConfig).
 */
export async function loadBundler(dshRoot = process.env.SIDEBAR_DSH_ROOT) {
  const bases = [
    ...(dshRoot ? [resolve(dshRoot, 'package.json')] : []),
    resolve(packageDir, 'package.json'),
  ]
  const failures = []
  for (const base of bases) {
    try {
      const entry = createRequire(base).resolve('tsdown')
      return await import(pathToFileURL(entry).href)
    } catch (error) {
      failures.push(`${base}: ${error.code ?? error.message}`)
    }
  }
  throw new Error(
    'client plugin build: cannot resolve the public bundler "tsdown" — install this package\'s devDependencies '
    + `or set SIDEBAR_DSH_ROOT to a DSH checkout with dependencies installed (tried: ${failures.join('; ')})`,
  )
}

/**
 * Build one browser client bundle for the official module table.
 * @param {object} options - package location, entry, and optional output directory.
 * @param {string} options.packageDir - package directory (default: this package).
 * @param {string} options.entry - entry file relative to packageDir (default: src/client/index.mjs).
 * @param {string} [options.outDir] - output directory relative to packageDir (default: lib).
 * @param {string | undefined} [options.dshRoot] - explicit DSH checkout for bundler resolution.
 * @returns {Promise<import('tsdown').UserConfig>} a tsdown configuration (pass with `config: false`).
 * @throws {Error} when the manifest or entry cannot be read or has invalid fields.
 */
export async function clientPluginConfig(options = {}) {
  const dir = resolve(options.packageDir ?? packageDir)
  const manifestPath = resolve(dir, 'package.json')
  let manifest
  try {
    manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
  } catch (error) {
    throw new Error(`client plugin build: cannot read ${manifestPath}`, { cause: error })
  }
  if (typeof manifest !== 'object' || manifest === null
    || !('name' in manifest) || typeof manifest.name !== 'string' || manifest.name.length === 0) {
    throw new Error(`client plugin build: ${manifestPath} declares no nonempty string package name`)
  }
  const id = manifest.name
  const external = manifestExternals(manifest, manifestPath)
  const requested = new Set([...PLATFORM_MODULES, ...external])
  const entry = resolve(dir, options.entry ?? 'src/client/index.mjs')
  try {
    if (!statSync(entry).isFile()) throw new Error('entry is not a file')
  } catch (error) {
    throw new Error(`client plugin build: entry is not a readable file: ${entry}`, { cause: error })
  }
  const mode = process.env.NODE_ENV ?? 'production'
  const { defineConfig } = await loadBundler(options.dshRoot)
  return defineConfig({
    name: `${id}/client`,
    cwd: dir,
    entry: { client: entry },
    outDir: resolve(dir, options.outDir ?? 'lib'),
    format: 'cjs',
    platform: 'browser',
    dts: false,
    sourcemap: true,
    clean: false,
    deps: {
      neverBundle: specifier => requested.has(specifier),
      alwaysBundle: specifier => !requested.has(specifier),
    },
    inputOptions: {
      plugins: [{
        name: 'framivra-client-import-contract',
        resolveId(specifier) {
          // Check before resolution: an installed devDependency must not make
          // an undeclared Host/Client namespace silently inline a private copy.
          if ((specifier.startsWith('@deepseek-ai/') || /^(react|react-dom)(\/|$)/.test(specifier)) && !requested.has(specifier)) {
            throw new Error(`client bundle purity: "${specifier}" is not an implicit baseline or declared dsh.client.external request`)
          }
          return null
        },
      }],
      resolve: { conditionNames: [mode === 'development' ? 'development' : 'production', 'browser', 'import', 'module', 'default'] },
    },
    define: {
      'process.env.NODE_ENV': JSON.stringify(mode),
      'import.meta.env.MODE': JSON.stringify(mode),
      'import.meta.env': JSON.stringify({ MODE: mode }),
      // Client-side build identity: the same deterministic stamp the host
      // reports in hello/open. The default bakes the source stamp; the
      // package flow passes a placeholder here and scripts/package.mjs
      // rewrites it to the final artifact stamp on BOTH sides.
      '__FRAMIVRA_CLIENT_STAMP__': JSON.stringify(options.clientStamp ?? computeSourceStamp(dir) ?? null),
    },
    outputOptions: {
      ...clientFactoryOutput(id),
      entryFileNames: 'client.js',
      codeSplitting: false,
      sourcemapExcludeSources: false,
    },
  })
}

/**
 * Emit the client bundle and its source map without cleaning other files.
 * The emitted `client.js` is purity-checked against the exact module-table
 * words (official platform seed + declared externals); a violation fails the
 * build closed and the offending artifact is removed.
 * @param {string} [outDir] - output directory relative to this package (default: lib).
 * @param {object} [options] - forwarded to {@link clientPluginConfig} (dshRoot, entry, packageDir).
 * @returns {Promise<Set<string>>} the exact module-table requests in the emitted bundle.
 */
export async function buildClient(outDir = 'lib', options = {}) {
  const { build } = await loadBundler(options.dshRoot)
  const dir = resolve(options.packageDir ?? packageDir)
  const results = await build({
    ...(await clientPluginConfig({ packageDir, outDir, ...options })),
    // The library's direct-stdout logger can corrupt an embedding caller's
    // serialized transport (notably node:test). Errors still reject build().
    logLevel: 'silent',
    config: false,
  })
  await Promise.all(results.map(result => result[Symbol.asyncDispose]()))
  const artifact = resolve(dir, outDir, 'client.js')
  const code = readFileSync(artifact, 'utf8')
  try {
    return assertBundlePurity(code, clientModuleRequests(dir))
  } catch (error) {
    // Delete exactly the emitted client.js file — never a recursive delete — after
    // re-verifying the resolved artifact is the expected file under the resolved
    // output directory of the package we just built.
    const expected = resolve(dir, outDir, 'client.js')
    if (artifact !== expected || !statSync(artifact).isFile()) {
      throw new Error(`client bundle purity: refusing to delete unexpected artifact path: ${artifact}`)
    }
    rmSync(artifact, { recursive: false, force: false })
    throw error
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  if (process.argv.length !== 2) throw new Error('Usage: node scripts/build.mjs')
  await buildClient()
}
