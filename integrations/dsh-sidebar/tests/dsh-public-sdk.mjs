/**
 * Public DSH package-entry resolver for the official-integration tests.
 *
 * The gated tests consume only PUBLIC DSH entries (Cordis runtime, the local
 * subprocess/fs service plugins, system prompt, ToolRuntime, LLM ids, and
 * the browser client module-system bundle). Historically they resolved those
 * through monorepo-relative lib paths under SIDEBAR_DSH_ROOT (vendor/,
 * packages/). The official npm distribution ships the same entries as
 * separately published @deepseek-ai/* packages instead, so this helper
 * resolves entries by real package resolution (createRequire over the
 * target root's package.json, honoring package.json/exports) — never by
 * fabricating a checkout layout or symlinking vendor/packages into an
 * installation.
 *
 * Root shapes, tried in order:
 *  1. monorepo checkout: when vendor/cordis/lib/index.js exists under the
 *     root, the historical relative paths are used unchanged;
 *  2. official npm distribution: entries resolve through the published
 *     package specifiers from the root's node_modules.
 *
 * Test-only helper: not imported by src/ or scripts/, so it never enters the
 * client build or the packaged host build stamp.
 */
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'

const MONOREPO_ENTRIES = {
  cordis: 'vendor/cordis/lib/index.js',
  subprocessLocal: 'packages/subprocess/subprocess-local/lib/index.js',
  fsLocal: 'packages/fs/fs-local/lib/index.js',
  systemPrompt: 'packages/core/system-prompt/lib/index.js',
  tools: 'packages/core/tools/lib/index.js',
  llm: 'packages/llm/llm/lib/index.js',
  clientModulesBrowser: 'packages/client/modules/lib/client.js',
}

const PACKAGE_ENTRIES = {
  cordis: '@deepseek-ai/cordis',
  subprocessLocal: '@deepseek-ai/dsh-subprocess-local',
  fsLocal: '@deepseek-ai/dsh-fs-local',
  systemPrompt: '@deepseek-ai/dsh-system-prompt',
  tools: '@deepseek-ai/dsh-tools',
  llm: '@deepseek-ai/dsh-llm',
  clientModulesBrowser: '@deepseek-ai/dsh-client-modules/client',
}

/** @returns {'monorepo' | 'npm'} which public-entry shape the root provides. */
export function dshEntryShape(root) {
  return existsSync(join(root, MONOREPO_ENTRIES.cordis)) ? 'monorepo' : 'npm'
}

/** Resolve one public entry to an absolute file path (throws if unavailable). */
export function resolveDshEntry(root, name) {
  if (!(name in PACKAGE_ENTRIES)) throw new Error(`unknown DSH public entry: ${name}`)
  if (dshEntryShape(root) === 'monorepo') return join(root, MONOREPO_ENTRIES[name])
  return createRequire(join(root, 'package.json')).resolve(PACKAGE_ENTRIES[name])
}

/** File URL for direct dynamic import. */
export function dshEntryUrl(root, name) {
  return pathToFileURL(resolveDshEntry(root, name)).href
}

/** Dynamic-import one public entry. */
export function importDsh(root, name) {
  return import(dshEntryUrl(root, name))
}
