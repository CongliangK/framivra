// Standalone regression test for the sidebar's own client-bundle builder
// (scripts/build.mjs) against the OFFICIAL DSH loader contract.
//
// Contract under test (documented + exported by the official
// @deepseek-ai/dsh-client-modules of dsh-v0.2.1-rc.2):
//   - packages/client/modules/src/client/manifest.ts: ClientBundleRegistration
//     {id, factory(require)} submitted through window.__ModuleLoader__.load;
//   - executing the built script only REGISTERS the lazy factory — no module
//     body side effect may run at script execution;
//   - the only module-table requests are exact official externals
//     (PLATFORM_MODULES seed words / declared dsh.client.external), so React
//     stays the shell's single shared instance and no second React is bundled;
//   - invalid dsh.client.external declarations and undeclared @deepseek-ai/*
//     runtime imports fail the build closed.
//
// When SIDEBAR_DSH_ROOT (or a resolvable official checkout) provides the
// BUILT official module system (packages/client/modules/lib/client.js), the
// test additionally materializes the bundle through that real loader's
// createClientModuleSystem instead of a hand stub.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { buildClient, clientPluginConfig, PLATFORM_MODULES } from '../scripts/build.mjs'

const packageDir = fileURLToPath(new URL('../', import.meta.url))
const dshRoot = process.env.SIDEBAR_DSH_ROOT
const mode = dshRoot ? 'official-loader' : 'stub-loader'
const work = mkdtempSync(join(tmpdir(), 'framivra-client-build-'))
// Owned-root guard: only ever delete inside the freshly-mkdtemp'd `work` dir, and surface (not swallow) failures.
test.after(() => {
  const owned = resolve(work)
  for (const target of [work, join(work, 'lib')]) {
    const resolved = resolve(target)
    if (resolved !== owned && !resolved.startsWith(owned + sep)) throw new Error(`cleanup target outside owned temp root: ${resolved}`)
  }
  rmSync(work, { recursive: true, force: true })
})

const reactStub = {
  createElement: (type, props, ...children) => ({ type: typeof type === 'symbol' ? 'Fragment' : type, props, children }),
  Fragment: Symbol('Fragment'),
  useEffect: () => { throw new Error('unexpected render effect') },
  useRef: () => ({}),
  useState: () => { throw new Error('unexpected render state') },
}

// 1. Build the real client entry with the own builder (bundler via SIDEBAR_DSH_ROOT or devDependencies).
const outDir = join(work, 'lib')
await buildClient(outDir, { dshRoot })
const source = readFileSync(join(outDir, 'client.js'), 'utf8')

test(`[${mode}] built artifact is one lazy ClientBundleRegistration script`, () => {
  assert.ok(source.startsWith('window.__ModuleLoader__.load({'), 'script must open with the loader registration')
  assert.match(source.replace(/^\/\/# sourceMappingURL=.*$/gm, '').trimEnd(), /return module\.exports;\s*\}\s*\}\);\s*$/, 'script must close the factory registration (same closing shape as the official modules bundle)')
  assert.match(source.slice(0, source.indexOf('factory:')), /"framivra-dsh-sidebar"/, 'registration id must be the package name')
  assert.doesNotMatch(source, /\bchunk:/, 'entry registration carries no chunk field')
})

test(`[${mode}] only exact official module-table requests; no second React`, () => {
  const requests = [...source.matchAll(/\brequire\("([^"]+)"\)/g)].map(match => match[1])
  assert.deepEqual(requests, ['react'], 'the single module-table request must be the platform-shared react')
  assert.ok(PLATFORM_MODULES.includes('react'), 'react must be an official platform word')
  for (const marker of ['react.element', 'react.portal', 'react.forward_ref', 'react.lazy']) {
    assert.ok(!source.includes(marker), `bundle must not inline a React copy (${marker})`)
  }
})

// 2. Execute the script through the loader facade: registration only, then lazy materialization.
const registrations = []
const executedWith = {}
{
  const window = { __ModuleLoader__: { mode: 'queue', pendingQueue: registrations, load: registration => registrations.push(registration) } }
  // Script execution must touch ONLY the facade: no fetch/document/timers exist here.
  new Function('window', 'fetch', 'document', source)(window, undefined, undefined)
}

test(`[${mode}] script execution registers exactly one lazy factory; nothing materializes`, () => {
  assert.equal(registrations.length, 1)
  const { id, chunk, factory } = registrations[0]
  assert.equal(id, 'framivra-dsh-sidebar')
  assert.equal(chunk, undefined)
  assert.equal(typeof factory, 'function')
  assert.equal(factory.length, 1, 'factory must take the module-table require')
})

test(`[${mode}] materialization resolves React through the shared module table`, () => {
  const required = []
  const require = specifier => {
    required.push(specifier)
    if (specifier === 'react') return reactStub
    throw new Error(`unexpected module-table request: ${specifier}`)
  }
  const exports = registrations[0].factory(require)
  assert.deepEqual(required, ['react'], 'exactly one shared-table require: react')
  assert.equal(typeof exports.TimelineView, 'function')
  assert.equal(typeof exports.frameQuery, 'function')
  assert.equal(typeof exports.decodeControlResponse, 'function')
  assert.equal(typeof exports.apply, 'function')
  assert.deepEqual(exports.inject, ['slots', 'sidebarRightTabs', 'configForms', 'conversation', 'sessions', 'uiSession'])
  // Shared React identity: the produced tree comes from OUR stub createElement shape.
  const tree = exports.TimelineView({
    snapshot: { content_version: 'v', document: {}, diagnostics: [], coverage: { complete: false, scope: 's', unexpanded: [] } },
    result: { content_version: 'v', tracks: [], blocks: [], diagnostics: [], coverage: { complete: true, scope: 's', unexpanded: [] } },
  })
  assert.ok(tree && typeof tree === 'object' && 'props' in tree && 'children' in tree, 'view tree must be built by the shared-table React instance')
  assert.throws(() => exports.frameQuery('1.0'), /BAD_QUERY/)
})

// 3. Real official loader (only when the official checkout supplies the built module system).
const officialClient = dshRoot ? resolve(dshRoot, 'packages/client/modules/lib/client.js') : null
if (officialClient && exists(officialClient)) {
  test('[official-loader] official ClientModuleSystem imports the built bundle', async () => {
    const facade = makeOfficialFacade()
    // The official modules bundle registers itself through the facade on execution.
    new Function('window', 'fetch', 'document', readFileSync(officialClient, 'utf8'))(facade.window, undefined, undefined)
    assert.equal(facade.pending.length, 1)
    assert.equal(facade.pending[0].id, '@deepseek-ai/dsh-client-modules')
    const boot = {
      rev: 'test-rev',
      entries: [{ id: 'framivra-dsh-sidebar', url: 'plugins/??framivra-dsh-sidebar/client.js&rev=test-rev', rev: 'test-rev' }],
      batches: [{ phase: 'application', url: 'plugins/??framivra-dsh-sidebar/client.js&rev=test-rev', rev: 'test-rev', entries: ['framivra-dsh-sidebar'] }],
    }
    const system = facade.create({ boot, staticModules: { react: reactStub }, loadBundle: async () => {
      new Function('window', 'fetch', 'document', source)(facade.window, undefined, undefined)
    } })
    const exports = await system.import('framivra-dsh-sidebar')
    assert.equal(typeof exports.TimelineView, 'function')
    assert.equal(typeof exports.apply, 'function')
    const tree = exports.TimelineView({
      snapshot: { content_version: 'v', document: {}, diagnostics: [], coverage: { complete: false, scope: 's', unexpanded: [] } },
      result: { content_version: 'v', tracks: [], blocks: [], diagnostics: [], coverage: { complete: true, scope: 's', unexpanded: [] } },
    })
    assert.ok(tree && typeof tree === 'object' && 'props' in tree && 'children' in tree)
  })
} else {
  test.skip('[official-loader] official built module system unavailable — skipped: SIDEBAR_DSH_ROOT does not point at an official checkout with packages/client/modules/lib/client.js built', () => {})
}

/** Facade implementing the documented facade/create() contract against the official modules bundle. */
function makeOfficialFacade() {
  const pending = []
  // One stable facade object: the official system switches it to live mode itself.
  const loader = { mode: 'queue', pendingQueue: pending, load: registration => pending.push(registration), create() { throw new Error('test facade: create must be called by the test, not the bundle') } }
  const window = { __ModuleLoader__: loader }
  const facade = {
    window,
    pending,
    create(options) {
      const index = pending.findIndex(registration => registration.id === '@deepseek-ai/dsh-client-modules')
      if (index < 0) throw new Error('test facade: modules bundle did not register itself')
      const [registration] = pending.splice(index, 1)
      const exports = registration.factory(specifier => { throw new Error(`bootstrap require: ${specifier}`) })
      if (typeof exports?.createClientModuleSystem !== 'function') throw new Error('modules bundle did not export the bootstrap module face')
      return exports.createClientModuleSystem(loader, { id: registration.id, exports }, options)
    },
  }
  return facade
}

function exists(path) {
  try { readFileSync(path); return true } catch { return false }
}

// 4. Fail-closed validation of declarations and imports (real builds on tiny fixtures).
test('invalid dsh.client.external fails the build closed', async () => {
  for (const external of [[42], ['./relative'], ['node:fs'], 'react']) {
    const fixture = fixturePackage({ external })
    await assert.rejects(() => clientPluginConfig({ packageDir: fixture, dshRoot }), /dsh\.client\.external/)
  }
})

test('manifest without a package name fails the build closed', async () => {
  const fixture = fixturePackage({ name: null })
  await assert.rejects(() => clientPluginConfig({ packageDir: fixture, dshRoot }), /no nonempty string package name/)
})

test('undeclared @deepseek-ai runtime import fails the build closed', async () => {
  // NOT a platform word: a plugin package must be declared in dsh.client.external to be requested.
  const fixture = fixturePackage({ entryBody: "import { x } from '@deepseek-ai/dsh-client-ui-sidebar-right'\nexport const y = x\n" })
  await assert.rejects(() => buildViaBuild(fixture, dshRoot), /client bundle purity: "@deepseek-ai\/dsh-client-ui-sidebar-right"/)
})

test('declared dsh.client.external stays an exact module-table request', async () => {
  // A non-baseline plugin package: declaring it in dsh.client.external makes the exact request legal.
  const fixture = fixturePackage({
    external: ['@deepseek-ai/dsh-client-ui-sidebar-right'],
    entryBody: "import { x } from '@deepseek-ai/dsh-client-ui-sidebar-right'\nexport const y = () => x\n",
  })
  const out = await buildViaBuild(fixture, dshRoot)
  const text = readFileSync(join(out, 'client.js'), 'utf8')
  assert.match(text, /"@deepseek-ai\/fixture"/)
  assert.match(text, /require\("@deepseek-ai\/dsh-client-ui-sidebar-right"\)/)
})

async function buildViaBuild(fixtureDir, root) {
  const { buildClient } = await import('../scripts/build.mjs')
  await buildClient('lib', { packageDir: fixtureDir, entry: 'src/index.mjs', dshRoot: root })
  return join(fixtureDir, 'lib')
}

function fixturePackage({ name = '@deepseek-ai/fixture', external = undefined, entryBody = 'export const y = 1\n' } = {}) {
  const safeName = typeof name === 'string' && name.length > 0 ? name : 'unnamed-fixture'
  const dir = join(work, safeName.replace(/[^\w.-]/g, '_'))
  mkdirSync(dir, { recursive: true })
  const manifest = name === undefined ? {} : { name, type: 'module' }
  if (external !== undefined) manifest.dsh = { client: { platform: 'web', external } }
  writeFileSync(join(dir, 'package.json'), JSON.stringify(manifest))
  mkdirSync(join(dir, 'src'), { recursive: true })
  writeFileSync(join(dir, 'src', 'index.mjs'), entryBody)
  return dir
}
