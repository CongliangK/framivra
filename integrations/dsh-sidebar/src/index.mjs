import z from '@deepseek-ai/schemastery'
import { createPreviewHost } from './host.mjs'
import { registerTimelineTools } from './tools.mjs'
import { DEFAULT_VTX_EXECUTABLE, DEFAULT_TRUSTED_PATH, DEFAULT_LOG_MAX_PER_MINUTE, MAX_LOG_MAX_PER_MINUTE, VTX_EXECUTABLE_PATTERN, TRUSTED_PATH_PATTERN } from './configuration.mjs'

export const name = 'framivra-dsh-sidebar'
export const inject = ['connection', 'sessions', 'workingDirectory', 'fs', 'subprocess', 'tools']

/**
 * Host-trusted configuration, edited on the plugin's own configuration page
 * (Plugins → framivra-dsh-sidebar). Not accepted through preview requests or
 * tool arguments; only the Host administrator sets it on that page. The
 * executable must not live inside a session workspace. Patterns live in
 * ./configuration.mjs (single home — these schema rules reference the exact
 * same RegExp objects); the Host's own regular-file and isAbsolute checks
 * remain the final arbiter. Both fields are volatile so the Host settings
 * service can serve and persist them on the plugin's namespace
 * (`framivra-readonly`) without remounting: the host half re-reads a fresh
 * snapshot on every operation and binds grants to the exact fingerprint.
 */
export const Config = z.object({
  vtxExecutable: z.union([
    z.const('vtx'),
    z.string().pattern(VTX_EXECUTABLE_PATTERN),
  ]).default(DEFAULT_VTX_EXECUTABLE).volatile().description('Host-trusted vtx executable: the literal "vtx" (searched on trustedPath) or an absolute path on the host. Must not point inside a session workspace.'),
  trustedPath: z.string().pattern(TRUSTED_PATH_PATTERN).default(DEFAULT_TRUSTED_PATH).volatile().description('Host-trusted colon-separated search PATH for the vtx executable. Every segment must be an absolute host path; empty or relative segments are rejected. The separator stays ":" per the Host contract.'),
  // Sanitized-diagnostics rate cap. Bounds and the reader live in
  // ./configuration.mjs (single home); the Host re-validates on every use so
  // even a directly constructed config object cannot bypass the ceiling.
  // Deliberately NOT part of the grant fingerprint: adjusting it never
  // invalidates open previews with CONFIG_CHANGED.
  logMaxPerMinute: z.number().min(0).max(MAX_LOG_MAX_PER_MINUTE).default(DEFAULT_LOG_MAX_PER_MINUTE).volatile().description(`Maximum sanitized diagnostic log lines per minute (0 silences, ceiling ${MAX_LOG_MAX_PER_MINUTE}). Failure one-liners only; never affects grants or previews.`),
})

/** The Connection carrier admits owner Peers before these exact handlers run. */
export function apply(ctx, config = {}) {
  const host = createPreviewHost(ctx, config)
  ctx.effect(() => () => host.dispose())
  registerTimelineTools(ctx, host.agent)
  ctx.effect(() => ctx.connection.fetch.register({
    path: '/api/framivra/control', methods: ['POST'], requestBody: 'buffered', fetch: host.control,
  }))
  // Authenticated Connection Fetch route like the others; it needs no Session
  // and accepts an empty body only (the saved Host configuration is the sole
  // input), so the check can never be turned into an arbitrary spawn.
  ctx.effect(() => ctx.connection.fetch.register({
    path: '/api/framivra/diagnostics', methods: ['POST'], requestBody: 'buffered', fetch: host.diagnostics,
  }))
  ctx.effect(() => ctx.connection.fetch.register({
    path: '/api/framivra/frame', methods: ['POST'], requestBody: 'streaming', fetch: host.frame,
  }))
  ctx.effect(() => ctx.connection.fetch.register({
    path: '/api/framivra/media', methods: ['GET', 'HEAD'], requestBody: 'buffered', fetch: host.media,
  }))
}
