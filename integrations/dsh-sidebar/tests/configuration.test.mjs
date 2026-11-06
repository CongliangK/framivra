import test from 'node:test'
import assert from 'node:assert/strict'
import {
  DEFAULT_VTX_EXECUTABLE, DEFAULT_TRUSTED_PATH, ConfigurationError,
  validateVtxExecutable, validateTrustedPath, configurationFingerprint,
  isVolatileConfig, readConfiguration, VTX_EXECUTABLE_PATTERN, TRUSTED_PATH_PATTERN,
} from '../src/configuration.mjs'
import { Config } from '../src/index.mjs'
import { TimelineReader } from '../src/protocol.mjs'
import { fixtureSubprocess } from './helpers.mjs'

test('defaults are single-sourced and stable', () => {
  assert.equal(DEFAULT_VTX_EXECUTABLE, 'vtx')
  assert.equal(DEFAULT_TRUSTED_PATH, '/usr/local/bin:/usr/bin:/bin')
  // The exported Cordis Config schema must carry the exact same defaults...
  assert.equal(Config.dict.vtxExecutable.meta.default, DEFAULT_VTX_EXECUTABLE)
  assert.equal(Config.dict.trustedPath.meta.default, DEFAULT_TRUSTED_PATH)
  // ...and compile the exact same pattern text (single pattern home;
  // schemastery re-instantiates the RegExp, so identity cannot be asserted).
  const schemaPattern = Config.dict.vtxExecutable.list.find(schema => schema.meta?.pattern)?.meta.pattern
  assert.equal(`${schemaPattern.flags}:${schemaPattern.source}`, `${VTX_EXECUTABLE_PATTERN.flags}:${VTX_EXECUTABLE_PATTERN.source}`)
  assert.equal(`${Config.dict.trustedPath.meta.pattern.flags}:${Config.dict.trustedPath.meta.pattern.source}`, `${TRUSTED_PATH_PATTERN.flags}:${TRUSTED_PATH_PATTERN.source}`)
})

test('explicit null never silently defaults; it is rejected as CONFIG_INVALID', () => {
  assert.throws(() => readConfiguration({ vtxExecutable: null }), ConfigurationError)
  assert.throws(() => readConfiguration({ trustedPath: null }), ConfigurationError)
  try { readConfiguration({ trustedPath: null }) } catch (error) { assert.equal(error.code, 'CONFIG_INVALID') }
})

test('vtxExecutable validation: literal vtx, POSIX/Windows absolute accepted; relative rejected', () => {
  assert.equal(validateVtxExecutable(undefined), DEFAULT_VTX_EXECUTABLE)
  assert.equal(validateVtxExecutable('vtx'), 'vtx')
  for (const value of ['/usr/local/bin/vtx', '/opt/trusted/vtx', 'C:\\tools\\vtx.exe', '\\\\server\\share\\vtx.exe']) assert.equal(validateVtxExecutable(value), value)
  for (const value of ['bin/vtx', './vtx', 'vtxx', '', ' vtx', 'vtx ', 42, null]) assert.throws(() => validateVtxExecutable(value), ConfigurationError)
})

test('trustedPath validation: colon-separated absolute segments only', () => {
  assert.equal(validateTrustedPath(undefined), DEFAULT_TRUSTED_PATH)
  for (const value of ['/usr/local/bin:/usr/bin:/bin', '/opt/trusted', '/a:/b:/c:/d']) assert.equal(validateTrustedPath(value), value)
  for (const value of ['', 'usr/bin', '/abs:rel', '/abs:', ':/abs', '/abs::/b', 'a', 7]) assert.throws(() => validateTrustedPath(value), ConfigurationError)
})

test('readConfiguration: plain source, defaults, frozen snapshot with fingerprint', () => {
  const empty = readConfiguration(undefined)
  assert.deepEqual({ ...empty }, { vtxExecutable: DEFAULT_VTX_EXECUTABLE, trustedPath: DEFAULT_TRUSTED_PATH, fingerprint: JSON.stringify([DEFAULT_VTX_EXECUTABLE, DEFAULT_TRUSTED_PATH]) })
  assert.equal(Object.isFrozen(empty), true)
  const plain = readConfiguration({ vtxExecutable: '/opt/trusted/vtx', trustedPath: '/opt/trusted' })
  assert.equal(plain.vtxExecutable, '/opt/trusted/vtx')
  assert.equal(plain.fingerprint, JSON.stringify(['/opt/trusted/vtx', '/opt/trusted']))
  assert.equal(plain.fingerprint, configurationFingerprint({ vtxExecutable: '/opt/trusted/vtx', trustedPath: '/opt/trusted' }))
  // Fingerprint is stable for equal pairs and cannot be forged across fields.
  assert.equal(configurationFingerprint({ vtxExecutable: 'a', trustedPath: 'b' }), configurationFingerprint({ vtxExecutable: 'a', trustedPath: 'b' }))
  assert.notEqual(configurationFingerprint({ vtxExecutable: 'a","b', trustedPath: 'c' }), configurationFingerprint({ vtxExecutable: 'a', trustedPath: 'b","c' }))
})

test('readConfiguration reads mutable Volatile-like cells via .get() and revalidates every call', () => {
  let executable = '/opt/trusted/vtx'
  const source = { vtxExecutable: { get: () => executable }, trustedPath: { get: () => '/opt/trusted' } }
  assert.equal(isVolatileConfig(source), true)
  assert.equal(isVolatileConfig({ vtxExecutable: '/x', trustedPath: '/y' }), false)
  assert.equal(isVolatileConfig(undefined), false)
  assert.equal(readConfiguration(source).vtxExecutable, '/opt/trusted/vtx')
  executable = '/other/vtx'
  assert.equal(readConfiguration(source).vtxExecutable, '/other/vtx')
  executable = 'relative/vtx'
  assert.throws(() => readConfiguration(source), ConfigurationError)
})

test('Cordis Config fields are volatile and materialize the defaults as live cells', () => {
  assert.equal(Config.dict.vtxExecutable.meta.volatile, true)
  assert.equal(Config.dict.trustedPath.meta.volatile, true)
  // schemastery returns Volatile cells, exactly what apply() receives from the
  // Host; reading them through the single configuration home yields defaults.
  const cells = Config({})
  assert.equal(isVolatileConfig(cells), true)
  assert.deepEqual({ ...readConfiguration(cells) }, { ...readConfiguration(undefined) })
})

test('TimelineReader without explicit config snapshots the defaults', () => {
  const reader = new TimelineReader(fixtureSubprocess)
  const snapshot = reader.snapshotConfig()
  assert.deepEqual({ vtxExecutable: snapshot.vtxExecutable, trustedPath: snapshot.trustedPath }, { vtxExecutable: DEFAULT_VTX_EXECUTABLE, trustedPath: DEFAULT_TRUSTED_PATH })
})
