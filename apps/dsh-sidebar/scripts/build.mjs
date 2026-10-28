#!/usr/bin/env node
/** Build with the public SDK; no Harness checkout or private loader protocol. */
import { fileURLToPath, pathToFileURL } from 'node:url'
import { clientPluginConfig } from '@deepseek-ai/dsh-client-modules/build'
import { build } from 'tsdown'

export const packageDir = fileURLToPath(new URL('../', import.meta.url))

/** Emit the SDK's client.js and source map without cleaning other files. */
export async function buildClient(outDir = 'lib') {
  const results = await build({
    ...clientPluginConfig({ packageDir, entry: 'src/client/index.mjs', outDir }),
    config: false,
  })
  await Promise.all(results.map(result => result[Symbol.asyncDispose]()))
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  if (process.argv.length !== 2) throw new Error('Usage: node scripts/build.mjs')
  await buildClient()
}
