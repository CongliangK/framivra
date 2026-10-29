import { createPreviewHost } from './host.mjs'

export const name = 'framivra-dsh-sidebar'
export const inject = ['connection', 'sessions', 'workingDirectory', 'fs', 'subprocess']

/** The Connection carrier admits owner Peers before these exact handlers run. */
export function apply(ctx, config = {}) {
  const host = createPreviewHost(ctx, config)
  ctx.effect(() => () => host.dispose())
  ctx.effect(() => ctx.connection.fetch.register({
    path: '/api/framivra/control', methods: ['POST'], requestBody: 'buffered', fetch: host.control,
  }))
  ctx.effect(() => ctx.connection.fetch.register({
    path: '/api/framivra/media', methods: ['GET', 'HEAD'], requestBody: 'buffered', fetch: host.media,
  }))
}
