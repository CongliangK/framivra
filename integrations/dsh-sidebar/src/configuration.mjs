/**
 * Single home for the Host-trusted vtx configuration: defaults, pure
 * validation, and live reads from either plain strings or cordis Volatile
 * fields. Deliberately dependency-free (no node: imports) so the browser
 * client bundle can reuse the exact same validators and copy semantics.
 */

export const DEFAULT_VTX_EXECUTABLE = 'vtx'
export const DEFAULT_TRUSTED_PATH = '/usr/local/bin:/usr/bin:/bin'

/** Absolute executable spellings the Host contract accepts; the single pattern
 *  home — src/index.mjs Config references these exact RegExp objects. */
export const VTX_EXECUTABLE_PATTERN = /^(\/.*|[a-zA-Z]:[\\/].*|\\\\.*)$/
/** Colon-separated absolute-only search PATH; every segment must start with '/'.
 *  Single home: src/index.mjs Config references this exact RegExp object. */
export const TRUSTED_PATH_PATTERN = /^\/[^:]*(:\/[^:]*)*$/

export class ConfigurationError extends Error {
  constructor(message) {
    super(message)
    this.name = 'ConfigurationError'
    this.code = 'CONFIG_INVALID'
  }
}

/** Normalize one raw executable spelling; undefined keeps the default. */
export function validateVtxExecutable(value) {
  if (value === undefined) return DEFAULT_VTX_EXECUTABLE
  if (typeof value !== 'string' || (value !== 'vtx' && !VTX_EXECUTABLE_PATTERN.test(value))) {
    throw new ConfigurationError('vtxExecutable must be the literal "vtx" or a host-absolute path; relative paths are rejected')
  }
  return value
}

/** Normalize one raw trusted PATH; undefined keeps the default. */
export function validateTrustedPath(value) {
  if (value === undefined) return DEFAULT_TRUSTED_PATH
  if (typeof value !== 'string' || !TRUSTED_PATH_PATTERN.test(value)) {
    throw new ConfigurationError('trustedPath must be colon-separated absolute host paths; empty or relative segments are rejected')
  }
  return value
}

/** Stable identity of one configuration value pair; grants bind this exactly.
 *  JSON escaping makes the encoding collision-free even for adversarial values
 *  containing separators or quotes: no escaped JSON string can contain the
 *  raw `","` boundary, so pair → text is injective. */
export function configurationFingerprint({ vtxExecutable, trustedPath }) {
  return JSON.stringify([vtxExecutable, trustedPath])
}

/** Read one field: a plain value, or a cordis Volatile/Obs cell via .get().
 *  Only `undefined` means "absent" (→ default); an explicit null is passed
 *  through verbatim so the validators reject it as CONFIG_INVALID instead of
 *  silently falling back to the default. */
function readField(field) {
  if (field === undefined) return undefined
  if (field !== null && (typeof field === 'object' || typeof field === 'function') && typeof field.get === 'function') return field.get()
  return field
}

/** Whether any field of this source is a live Volatile cell rather than a plain string. */
export function isVolatileConfig(source) {
  return ['vtxExecutable', 'trustedPath'].some(key => {
    const field = source?.[key]
    return field !== undefined && field !== null && ((typeof field === 'object' || typeof field === 'function') && typeof field.get === 'function')
  })
}

/**
 * One consistent validated snapshot of the Host-trusted configuration.
 * Reads plain strings and Volatile cells alike, validates string/absolute
 * semantics on EVERY call, and returns a frozen value pair plus fingerprint.
 * @param {{ vtxExecutable?: unknown, trustedPath?: unknown }} source plugin config (apply argument or plain object)
 * @returns {{ vtxExecutable: string, trustedPath: string, fingerprint: string }} frozen snapshot
 */
export function readConfiguration(source) {
  const snapshot = {
    vtxExecutable: validateVtxExecutable(readField(source?.vtxExecutable)),
    trustedPath: validateTrustedPath(readField(source?.trustedPath)),
  }
  snapshot.fingerprint = configurationFingerprint(snapshot)
  return Object.freeze(snapshot)
}
