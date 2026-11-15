// Optional integration probe, not a CI selector/gate. Uses the supplied reviewed DSH checkout.
import { mkdtemp, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
const dsh = process.env.SIDEBAR_DSH_ROOT
if (!dsh) throw new Error('Set SIDEBAR_DSH_ROOT to a reviewed conversation-draft DSH checkout (with installed dev dependencies).')
const root = resolve(dsh)
const app = fileURLToPath(new URL('..', import.meta.url))
const dir = await mkdtemp(join(process.env.SIDEBAR_EVIDENCE_DIR || tmpdir(), 'sidebar-conversation-'))
const config = join(dir, 'vitest.config.mts')
await writeFile(config, `import tsconfigPaths from ${JSON.stringify(join(root, 'node_modules/vite-tsconfig-paths/dist/index.js'))}
import { standardDecoratorPlugin, vitestExecArgv } from ${JSON.stringify(join(root, 'vitest.shared.ts'))}
export default {
 root: ${JSON.stringify(app)},
 cacheDir: ${JSON.stringify(join(dir, 'vite-cache'))},
 server: { fs: { allow: [${JSON.stringify(root)}, ${JSON.stringify(app)}] } },
 plugins: [standardDecoratorPlugin(), tsconfigPaths({ projects: [${JSON.stringify(join(root, 'tsconfig.base.json'))}] })],
 resolve: { alias: { '@deepseek-ai/dsh-client-store': ${JSON.stringify(join(root, 'packages/client/store/src/index.ts'))}, '@deepseek-ai/dsh-session/types': ${JSON.stringify(join(root, 'packages/core/session/src/types.ts'))}, '@deepseek-ai/dsh-client-test-runtime/src/assembly/index.ts': ${JSON.stringify(join(root, 'packages/test-support/client-runtime/src/assembly/index.ts'))}, vitest: ${JSON.stringify(join(root, 'node_modules/vitest/dist/index.js'))}, 'sidebar-session-world': ${JSON.stringify(join(root, 'packages/api/session-controller/tests/remote/session.client.ts'))} } },
 test: { include: [${JSON.stringify(join(app, 'tests/conversation.client.spec.ts'))}], environment: 'jsdom', execArgv: vitestExecArgv, maxWorkers: 1, fileParallelism: false, testTimeout: 30000, hookTimeout: 30000 }
}
`)
const result = spawnSync(process.execPath, [join(root, 'node_modules/vitest/vitest.mjs'), 'run', '--config', config], { cwd: root, stdio: 'inherit', env: { ...process.env, NODE_PATH: [join(root, 'packages/test-support/client-runtime/node_modules'), process.env.NODE_PATH].filter(Boolean).join(':') } })
if (result.error) throw result.error
process.exitCode = result.status ?? 1
