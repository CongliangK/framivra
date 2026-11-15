/**
 * Single home for the Host-trusted vtx configuration: defaults, pure
 * validation, and live reads from either plain strings or cordis Volatile
 * fields. Deliberately dependency-free (no node: imports) so the browser
 * client bundle can reuse the exact same validators and copy semantics.
 */

// GUI saves invalidate all readers sharing this live Host config source, even
// when VTX_CONFIG's path is unchanged. No filesystem I/O or secret enters this
// token. External edits are not watched: reopen/re-authorize after editing them.
const configurationEpochs = new WeakMap()
export function invalidateConfiguration(source) {
  if (source && (typeof source === 'object' || typeof source === 'function')) {
    configurationEpochs.set(source, (configurationEpochs.get(source) ?? 0) + 1)
  }
}

export const DEFAULT_VTX_CONFIG_FILE = ''
// Empty selects the kernel's own default; otherwise one explicit absolute JSON file.
export const VTX_CONFIG_FILE_PATTERN = /^(?:|\/[^\x00-\x1f]*\.json|[a-zA-Z]:[\\/][^\x00-\x1f]*\.json)$/
export function validateVtxConfigFile(value) {
  if (value === undefined) return DEFAULT_VTX_CONFIG_FILE
  if (typeof value !== 'string' || !VTX_CONFIG_FILE_PATTERN.test(value) || value.split(/[\\/]/).some(part => part === '..' || part === '.')) {
    throw new ConfigurationError('vtxConfigFile must be empty or an absolute .json file path without traversal or control characters')
  }
  return value
}

export const DEFAULT_VTX_EXECUTABLE = 'vtx'
export const DEFAULT_TRUSTED_PATH = '/usr/local/bin:/usr/bin:/bin'
/** Sanitized-diagnostics rate cap: inclusive bounds for the Host config field.
 *  The ceiling keeps an operator typo or a hostile profile value from turning
 *  the "bounded" logger into an unbounded firehose; 0 legitimately silences. */
export const DEFAULT_LOG_MAX_PER_MINUTE = 120
export const MAX_LOG_MAX_PER_MINUTE = 1000

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

/* Stable identity of execution configuration; grants bind this exactly.
 * JSON escaping keeps values collision-free; the empty config-file default
 * preserves the legacy pair, while an explicit file extends it to a triple. */
export function configurationFingerprint({ vtxExecutable, trustedPath, vtxConfigFile = '' }) {
  // Preserve the historical default fingerprint; an explicit file is a third
  // collision-free component and invalidates every grant minted without it.
  return JSON.stringify(vtxConfigFile ? [vtxExecutable, trustedPath, vtxConfigFile] : [vtxExecutable, trustedPath])
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
  return ['vtxExecutable', 'trustedPath', 'vtxConfigFile'].some(key => {
    const field = source?.[key]
    return field !== undefined && field !== null && ((typeof field === 'object' || typeof field === 'function') && typeof field.get === 'function')
  })
}

/**
 * Read and validate the sanitized-logging rate cap from the plugin config.
 * Same field-reading rules as readConfiguration (plain value or Volatile
 * cell), but deliberately kept OUT of that snapshot: the logging cap is not
 * part of the grant fingerprint, so adjusting it never invalidates open
 * previews. undefined → default; anything but a safe integer within
 * [0, MAX_LOG_MAX_PER_MINUTE] throws CONFIG_INVALID, so no configuration
 * path (schema-typed or directly constructed) can bypass the bound.
 */
export function readLogMaxPerMinute(source) {
  const value = readField(source?.logMaxPerMinute)
  if (value === undefined) return DEFAULT_LOG_MAX_PER_MINUTE
  if (!Number.isSafeInteger(value) || value < 0 || value > MAX_LOG_MAX_PER_MINUTE) {
    throw new ConfigurationError(`logMaxPerMinute must be an integer between 0 and ${MAX_LOG_MAX_PER_MINUTE}`)
  }
  return value
}

/**
 * One consistent validated snapshot of the Host-trusted configuration.
 * Reads plain strings and Volatile cells alike, validates string/absolute
 * semantics on EVERY call, and returns a frozen snapshot plus fingerprint.
 * An empty config file is omitted for backward-compatible default snapshots.
 * @param {{ vtxExecutable?: unknown, trustedPath?: unknown, vtxConfigFile?: unknown }} source plugin config
 * @returns {{ vtxExecutable: string, trustedPath: string, vtxConfigFile?: string, fingerprint: string }} frozen snapshot
 */
export function readConfiguration(source) {
  const snapshot = {
    vtxExecutable: validateVtxExecutable(readField(source?.vtxExecutable)),
    trustedPath: validateTrustedPath(readField(source?.trustedPath)),
  }
  const vtxConfigFile = validateVtxConfigFile(readField(source?.vtxConfigFile))
  // Keep default snapshots wire-compatible with pre-management two-field saves.
  if (vtxConfigFile) snapshot.vtxConfigFile = vtxConfigFile
  snapshot.fingerprint = configurationFingerprint(snapshot)
  const epoch = source && (typeof source === 'object' || typeof source === 'function') ? configurationEpochs.get(source) ?? 0 : 0
  if (epoch) snapshot.fingerprint += `|config-save:${epoch}`
  return Object.freeze(snapshot)
}
