/**
 * Loader combination test for the documented wiring patch.
 *
 * Proves the README's disable+insert example applies cleanly with THIS
 * machine's real loader semantics (@deepseek-ai/cordis-plugin-include's
 * applyEntryPatches) against the REAL @deepseek-ai/dsh-base entry list —
 * no web host is started. The patch is extracted from README.md itself so
 * the documented example can never drift from what is tested.
 */

import { createRequire } from 'node:module'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import assert from 'node:assert/strict'
import { applyEntryPatches, entryListSchema } from '@deepseek-ai/cordis-plugin-include'

// js-yaml and the base bundle are resolved from the real DSH npm tree.
const require = createRequire(import.meta.resolve('@deepseek-ai/dsh-attachment-local'))
const yaml = require('js-yaml')
const baseBundlePath = require.resolve('@deepseek-ai/dsh-base/cordis.patch.yml')

const here = fileURLToPath(new URL('.', import.meta.url))

/** Recursively find an entry by id. */
function findById(entries, id, found = []) {
  for (const entry of entries ?? []) {
    if (Array.isArray(entry) || typeof entry !== 'object' || entry === null) continue
    if (entry.id === id) found.push(entry)
    if (entry.group && Array.isArray(entry.config)) findById(entry.config, id, found)
  }
  return found
}

test('README wiring patch disables the official store and inserts ours under the real loader', async () => {
  // 1. Extract the documented patch block verbatim from README.md.
  const readme = await readFile(`${here}../README.md`, 'utf8')
  const match = readme.match(/<!-- wiring-patch-begin -->([\s\S]*?)<!-- wiring-patch-end -->/)
  assert.ok(match, 'README must contain the wiring-patch markers')
  const patchText = match[1].replace(/```[a-z]*\n?/g, '')
  const patches = yaml.load(patchText, { schema: entryListSchema })
  assert.ok(Array.isArray(patches) && patches.length === 2)

  // 2. Compose exactly as the host boots: base bundle patches applied to the
  //    seed entry list first, then the user (README) patch layer on top.
  const baseText = await readFile(baseBundlePath, 'utf8')
  const basePatches = yaml.load(baseText, { schema: entryListSchema })
  assert.ok(Array.isArray(basePatches))
  const data = applyEntryPatches([], basePatches, () => {})
  assert.ok(Array.isArray(data) && data.length > 0)

  // The official attachment-local entry really exists (nested in a group).
  const official = findById(data, 'attachment-local')
  assert.equal(official.length, 1)
  assert.equal(official[0].name, '@deepseek-ai/dsh-attachment-local')
  assert.notEqual(official[0].disabled, true)

  // 3. Apply the documented patch exactly as the loader does.
  const warnings = []
  const patched = applyEntryPatches(data, patches, (...args) => warnings.push(args.join(' ')))
  assert.deepEqual(warnings, [], 'no patch row may be skipped (guards must match)')

  // 4. The official entry is disabled in place; the original data is untouched.
  const disabled = findById(patched, 'attachment-local')
  assert.equal(disabled.length, 1)
  assert.equal(disabled[0].disabled, true)
  assert.equal(findById(data, 'attachment-local')[0].disabled, undefined, 'input must not be mutated')

  // 5. Our entry is appended at the top level with its config.
  const inserted = findById(patched, 'image-isolation')
  assert.equal(inserted.length, 1)
  assert.equal(inserted[0].name, 'dsh-image-isolation')
  assert.equal(inserted[0].config.workerTimeoutMs, 30000)
  assert.equal(inserted[0].config.workerMaxQueue, 64)
})
