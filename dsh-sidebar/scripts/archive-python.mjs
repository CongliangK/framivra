import { spawnSync } from 'node:child_process'

export function commandFailure(result) {
  return result.error?.message || result.stderr?.trim() || (result.signal ? `signal ${result.signal}` : `exit status ${result.status}`)
}

/** Select a working Python 3 launcher before running archive code (never retry a partial archive). */
export function spawnArchivePython(code, args, options = {}) {
  const settings = { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], timeout: 60_000, ...options }
  const pythonArgs = ['-I', '-B', '-c']
  if (process.platform !== 'win32') return spawnSync('python3', [...pythonArgs, code, ...args], settings)

  // Windows can resolve python3.exe to a Store alias that starts but exits with
  // no Python runtime. Probe the interpreter, not just whether it can spawn.
  const attempts = []
  for (const [command, prefix] of [['python.exe', []], ['py.exe', ['-3']], ['python3.exe', []]]) {
    const probe = spawnSync(command, [...prefix, ...pythonArgs, 'import sys, zipfile, zlib; sys.exit(sys.version_info.major != 3)'], {
      ...settings, timeout: 5_000,
    })
    if (!probe.error && probe.status === 0) {
      return spawnSync(command, [...prefix, ...pythonArgs, code, ...args], settings)
    }
    attempts.push(`${[command, ...prefix].join(' ')}: ${commandFailure(probe)}`)
  }
  return { error: new Error(`no working Python 3 launcher (${attempts.join('; ')})`) }
}
