import { spawn } from 'node:child_process'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createPreviewHost } from '../src/host.mjs'

export class MemoryFs {
  constructor() {
    this.files = new Map([
      ['/workspace', { type: 'directory', version: 'root' }],
      ['/workspace/timeline.json', { type: 'file', version: 'timeline', size: 2 }],
      ['/workspace/out/final.mp4', { type: 'file', version: 'media', size: 160_000 }],
      ['/opt/trusted/vtx', { type: 'file', version: 'vtx' }],
    ])
    this.aliases = new Map()
    this.reads = []
  }
  async resolve(path, { cwd = '/' } = {}) {
    const absolute = resolve(cwd, path)
    const key = this.aliases.get(absolute) ?? absolute
    return { targetKey: key, displayPath: key }
  }
  processPath(target) { return target.targetKey }
  processPathFromHostPath(path) { return path }
  contains(parent, child) { return child.targetKey === parent.targetKey || child.targetKey.startsWith(`${parent.targetKey}/`) }
  async stat(target) { const info = this.files.get(target.targetKey); return info ? { ...info } : undefined }
  async readByteRange(target, { offset, length }, signal) {
    signal.throwIfAborted()
    this.reads.push({ key: target.targetKey, offset, length, signal })
    return Uint8Array.from({ length }, (_, index) => (offset + index) % 251)
  }
}

/** Test adapter only: real Node child running a fixed JSON fixture, not installed vtx/DSH. */
export function fixtureSubprocess() {
  const calls = []
  return {
    calls,
    async resolveExecutable(command, env) { calls.push({ command, env }); return '/opt/trusted/vtx' },
    spawn(spec) {
      calls.push(spec)
      const child = spawn(process.execPath, [fileURLToPath(new URL('./fixture-vtx.mjs', import.meta.url)), ...spec.argv.slice(1)], {
        cwd: '/tmp', env: { PATH: process.env.PATH }, stdio: ['ignore', 'pipe', 'pipe'],
      })
      let stdout = '', stderr = ''
      child.stdout.on('data', chunk => { stdout += chunk })
      child.stderr.on('data', chunk => { stderr += chunk })
      const terminate = () => { child.kill('SIGTERM') }
      spec.signal.addEventListener('abort', terminate, { once: true })
      const done = new Promise((fulfill, reject) => {
        child.on('error', reject)
        child.on('close', (exitCode, signal) => { spec.signal.removeEventListener('abort', terminate); fulfill({ exitCode, signal }) })
      })
      return { done, terminate, collected: { stdout: { readFrom: () => ({ text: stdout, lossy: false }) }, stderr: { readFrom: () => ({ text: stderr, lossy: false }) } } }
    },
  }
}
export function setup(config = {}) {
  const fs = new MemoryFs()
  const sessions = new Map([['one', { id: 'one' }], ['two', { id: 'two' }]])
  const cwd = { value: '/workspace' }
  const subprocess = fixtureSubprocess()
  const ctx = { fs, sessions, workingDirectory: { get: () => cwd.value }, subprocess }
  const host = createPreviewHost(ctx, { vtxExecutable: '/opt/trusted/vtx', ...config })
  const request = data => new Request('http://owner/api/framivra/control', { method: 'POST', body: JSON.stringify({ sessionId: 'one', ...data }) })
  const control = data => host.control(request(data))
  const media = (grant, mediaId, options = {}, sessionId = 'one') => host.media(new Request(`http://owner/api/framivra/media?${new URLSearchParams({ sessionId, grant, mediaId })}`, options))
  return { host, fs, ctx, cwd, subprocess, sessions, control, media }
}
