import test from 'node:test'
import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'

// Presentation/registration test, NOT a DOM/visual/React lifecycle test.
// Resolve React to a tiny element recorder so the source stays dependency-free in tests.
const source = new URL('../src/client/index.mjs', import.meta.url).href
const react = `export const createElement=(type,props,...children)=>({type:typeof type==='symbol'?'Fragment':type,props,children});export const useEffect=()=>{};export const useRef=()=>({});export const useState=()=>[];export default {createElement,Fragment:Symbol('Fragment')};`
const hook = registerHooks({ resolve(specifier, context, next) {
  if (specifier === 'react' && context.parentURL === source) return { url: `data:text/javascript,${encodeURIComponent(react)}`, shortCircuit: true }
  return next(specifier, context)
} })
const client = await import(source)
hook.deregister()

const snapshot = { content_version: 'sha256:one', document: { raw_extension: '<script>never execute</script>' }, diagnostics: [], coverage: { complete: false, scope: 'stored timeline blocks only', unexpanded: ['subtitles_src'] } }
const tracks = ['video', 'video', 'audio', 'audio', 'subtitles'].map((family, index) => ({ family, track_index: index, address: `/t/${index}`, data: { original: index } }))
const result = { content_version: 'sha256:one', tracks, blocks: tracks.map(track => ({ ...track, address: `${track.address}/block`, block_index: 0, range: [0, 30], data: { block_raw: track.track_index } })), diagnostics: [], coverage: snapshot.coverage }

test('view renders all families/tracks/raw fields and incomplete coverage; never HTML injection', () => {
  const tree = client.TimelineView({ snapshot, result })
  const text = JSON.stringify(tree)
  for (const token of ['video / track 0', 'video / track 1', 'audio / track 2', 'audio / track 3', 'subtitles / track 4', 'Incomplete coverage', 'subtitles_src', 'block_raw', 'raw_extension', '<script>never execute</script>']) assert.ok(text.includes(token), token)
  assert.equal(text.includes('dangerouslySetInnerHTML'), false)
})

test('mixed content versions never display mismatched selection or raw document', () => {
  const tree = client.TimelineView({ snapshot, result: { ...result, content_version: 'sha256:two' } })
  assert.equal(tree.props.role, 'alert')
  assert.equal(JSON.stringify(tree).includes('block_raw'), false)
  assert.equal(JSON.stringify(tree).includes('raw_extension'), false)
})

test('independent framivra extension tab and guide registrations dispose cleanly', () => {
  const definitions = [], slots = [], cleanup = []
  client.apply({ effect: effect => cleanup.push(effect()), sidebarRightTabs: { register: definition => { definitions.push(definition); return () => {} } }, slots: {
    inject: (_name, register) => register(),
    register: (definition, Body) => { slots.push({ definition, Body }); return () => {} },
  } })
  assert.equal(definitions[0].kind, 'framivra')
  assert.equal(definitions[0].priority, 'extension')
  assert.deepEqual(slots.map(slot => slot.definition.name), ['sidebar.right.pane.tab', 'sidebar.right.tab.guide.entry'])
  assert.deepEqual(slots[0].definition.inject('session'), { sessionId: 'session' })
  for (const dispose of cleanup.reverse()) dispose()
})
