/**
 * Thin public-API profile-boot/agent-turn harness for the official-integration
 * tests when SIDEBAR_DSH_ROOT points at the official npm distribution.
 *
 * Provenance: mechanical adaptation of the DSH monorepo test-support fixture
 *   packages/test-support/loader-smoke/tests/fixtures/production-profile.ts
 *   packages/test-support/loader-smoke/src/agent-turn.ts
 * (upstream commit 868ae49afa2b77f502ba1c913680660800bce673;
 *  git blob 415dc2c74ac6973c8beb0fd8ffc0cd0e819c66a8 / sha256 bbae1e4074c2dd17… for
 *  production-profile.ts, git blob 596f1682c6e1085d94df9854bfe62f5f54df8ec9 /
 *  sha256 505d034471ffd073… for agent-turn.ts; recorded 2026-11-09).
 *
 * The ONLY semantic change vs the upstream fixture is the parameterized
 * install anchor and root-relative package resolution: the monorepo fixture
 * derives its anchor from its own file location, which would pin the booted
 * runtime to the source checkout; this harness takes the anchor and the DSH
 * root as arguments so the official npm distribution's package.json can be
 * the anchor and its node_modules the module source. Everything else —
 * profile layer composition, overlay loading, module layer synthesis,
 * profileContext facts, fixture-turn inbox/event semantics — is adapted
 * verbatim. All entries used here are PUBLISHED public packages
 * (@deepseek-ai/dsh-app-boot, @deepseek-ai/dsh-llm, @deepseek-ai/cordis):
 * no private kernel interfaces, no stubs, no fabricated checkout layout.
 */
import { writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import { homedir } from 'node:os'

export const UPSTREAM_PROVENANCE = {
  commit: '868ae49afa2b77f502ba1c913680660800bce673',
  productionProfile: { gitBlob: '415dc2c74ac6973c8beb0fd8ffc0cd0e819c66a8', sha256_16: 'bbae1e4074c2dd17' },
  agentTurn: { gitBlob: '596f1682c6e1085d94df9854bfe62f5f54df8ec9', sha256_16: '505d034471ffd073' },
}

function insertedPluginNames(entries) {
  return entries.flatMap((entry) => {
    const children = entry.group && Array.isArray(entry.config)
      ? insertedPluginNames(entry.config)
      : []
    return [entry.name, ...children]
  })
}

function packageName(specifier) {
  if (specifier.startsWith('.') || specifier.startsWith('file:') || specifier.includes(':')) return undefined
  const segments = specifier.split('/')
  return specifier.startsWith('@') ? segments.slice(0, 2).join('/') : segments[0]
}

function overlayModuleLayers(path, patches, require) {
  const packages = new Map()
  const inserted = patches.flatMap(patch => patch.insert ?? [])
  for (const specifier of insertedPluginNames(inserted)) {
    const name = packageName(specifier)
    if (name === undefined || packages.has(name)) continue
    packages.set(name, dirname(require.resolve(`${name}/package.json`)))
  }
  return [...packages].map(([name, packageDir], index) => ({
    packageName: `test-overlay:${index}:${name}`,
    packageDir,
    patchPaths: [path],
    patches: [],
  }))
}

/**
 * Same contract as the upstream bootProductionProfile, with the install
 * anchor (a package.json path) and the DSH root made explicit.
 * @param {object} options - binName / profile / overlayPaths / prepare, as upstream.
 * @param {string} installAnchor - absolute package.json path of the installation to boot.
 * @param {string} root - DSH root whose node_modules provide @deepseek-ai/* packages.
 */
export async function bootProductionProfile(options, installAnchor, root) {
  const require = createRequire(join(root, 'package.json'))
  const { boot, createRuntimeResolution, loadOverlayPatches, loadProfile, PluginPackages } =
    await import(require.resolve('@deepseek-ai/dsh-app-boot'))
  for (const path of options.overlayPaths) {
    if (!path.endsWith('.patch.yml')) {
      throw new Error(`${options.binName}: test overlay path must end in .patch.yml: ${path}`)
    }
  }
  const profile = loadProfile(options.binName, options.profile, installAnchor, undefined, { userLayer: false })
  const rootConfig = join(profile.dir, 'cordis.yml')
  await writeFile(rootConfig, '[]\n')

  const overlays = options.overlayPaths.map(path => loadOverlayPatches(options.binName, path))
  // Overlay-only packages need profile module visibility in plain-Node mode,
  // but these synthetic layers do not contribute Cordis patches.
  const moduleLayers = options.overlayPaths.flatMap((path, index) => (
    overlayModuleLayers(path, overlays[index] ?? [], require)
  ))
  const resolution = await createRuntimeResolution({
    installAnchor,
    profile: { ...profile, layers: [...profile.layers, ...moduleLayers] },
  })
  return boot(
    options.binName,
    rootConfig,
    [
      ...profile.layers.flatMap(layer => layer.patches),
      ...overlays.flat(),
    ],
    async (ctx) => {
      // The launcher's own profile facts, so profile-backed services activate as in production.
      const profileContext = {
        name: options.profile, dir: profile.dir, patchPath: profile.patchPath, installAnchor,
        cwd: process.cwd(), home: process.env['DSH_HOME'] ?? join(homedir(), '.dsh'),
        startedBundles: profile.layers.map(layer => layer.packageName),
        overlays: overlays.flat(), telemetryDisabledEnv: process.env['DSH_TELEMETRY_DISABLED'],
      }
      ctx.provide('profileContext', profileContext)
      await ctx.plugin(PluginPackages, { resolution })
      await options.prepare?.(ctx)
    },
  )
}

/** Fixture-turn driver: same contract as the upstream agent-turn.ts. */
export async function runFixtureTurn(ctx, options, root) {
  const require = createRequire(join(root, 'package.json'))
  const { createUserMessage, expandAssistantStream } = await import(require.resolve('@deepseek-ai/dsh-llm'))

  function addUsage(total, step) {
    const next = {
      inputTokens: (total?.inputTokens ?? 0) + step.inputTokens,
      outputTokens: (total?.outputTokens ?? 0) + step.outputTokens,
    }
    for (const key of ['cacheReadTokens', 'cacheWriteTokens', 'reasoningTokens']) {
      if (total?.[key] !== undefined || step[key] !== undefined) next[key] = (total?.[key] ?? 0) + step[key]
    }
    return next
  }

  function assistantText(event) {
    const blocks = event.data.message.content.filter(block => block.type === 'text')
    return blocks.length === 0 ? undefined : blocks.map(block => block.text).join('')
  }

  async function onlyRootAgent(ctx) {
    const registry = ctx.get('agents')
    if (registry === undefined) throw new Error('fixture turn requires exactly one top-level agent, found 0')
    // Configured agents publish asynchronously (persistence create/resume runs
    // before publication), so a settled Loader does not imply a registered
    // agent yet; wait for the first publication instead of requiring it.
    if (registry.roots().length === 0) {
      await new Promise((resolve) => {
        const dispose = ctx.on('agent/created', () => {
          dispose()
          resolve()
        })
      })
    }
    const agents = registry.roots()
    const [agent] = agents
    if (agent === undefined || agents.length !== 1) {
      throw new Error(`fixture turn requires exactly one top-level agent, found ${agents.length}`)
    }
    return agent
  }

  const agent = await onlyRootAgent(ctx)
  await agent.whenIdle()

  const message = createUserMessage({
    content: [{ type: 'text', text: options.task }],
    source: { kind: 'user' },
  })
  let received = false
  let output = ''
  const usageByStep = new Map()
  const disposeListener = ctx.on('session/event', (session, event) => {
    if (session !== agent.session) return
    if (!received) {
      if (event.type !== 'agent/inbox/spliced'
        || !event.data.inserted.some(inserted => inserted.id === message.id)) return
      received = true
    }
    options.onEvent?.(session.id, event)
    if (event.type === 'assistant/message') {
      output = assistantText(event) ?? output
      if (event.data.usage !== undefined) {
        usageByStep.set(`${event.data.turn}/${event.data.step}`, event.data.usage)
      }
    } else if (event.type === 'assistant/attempt') {
      const usage = expandAssistantStream(event.data.stream)
        .findLast(member => member.chunk.type === 'usage')?.chunk
      if (usage?.type === 'usage') {
        usageByStep.set(`${event.data.turn}/${event.data.step}`, usage.usage)
      }
    }
  })

  try {
    agent.followup(message)
    await agent.whenIdle()
  } finally {
    disposeListener()
  }
  await ctx.sessions.flush(agent.session)
  const usage = [...usageByStep.values()].reduce((total, step) => total === undefined ? step : addUsage(total, step), undefined)
  return {
    type: 'result',
    sessionId: agent.session.id,
    output,
    ...usage === undefined ? {} : { usage },
  }
}
