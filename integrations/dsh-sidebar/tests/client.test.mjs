import test from 'node:test'
import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import { setup } from './helpers.mjs'
import { stringifyJSON } from '../src/json.mjs'

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

test('wire -> client decode -> details -> JSON serialization preserves original integer IDs', async t => {
  const f = setup(); t.after(() => f.host.dispose())
  const openResponse = await f.control({ op: 'open', timeline: 'timeline.json' })
  const wireOpen = await openResponse.text()
  assert.equal(openResponse.status, 200, wireOpen)
  const opened = client.decodeControlResponse(wireOpen)
  const response = await f.control({ op: 'query', grant: opened.grant })
  const wireQuery = await response.text()
  assert.equal(response.status, 200, wireQuery)
  const result = client.decodeControlResponse(wireQuery)
  for (const text of [wireOpen, wireQuery, stringifyJSON(opened), stringifyJSON(result), JSON.stringify(client.TimelineView({ snapshot: opened.snapshot, result }))]) {
    assert.ok(text.includes('9007199254740993'))
    assert.equal(text.includes('9007199254740992'), false)
    assert.equal(text.includes('"rawJSON"'), false)
  }
})

test('frame input matches the Host canonical token contract before numeric conversion', () => {
  assert.deepEqual(client.frameQuery(''), {})
  for (const input of ['0', '1', '29', '30', '9007199254740990', '9007199254740991']) {
    const query = client.frameQuery(input)
    assert.equal(String(query.frame), input)
    assert.equal(Number.isSafeInteger(query.frame), true)
  }
  for (const input of ['1.0000000000000001', '9007199254740991.1', '-0', '-1', '+1', '00', '01', '1.0', '1e0', '1e309', '1e-400', '9007199254740992', '9007199254740993', '9'.repeat(4000), ' 1', '1 ', '1\n', '\n', 'NaN', 'Infinity', '０', null, 1, -0]) {
    assert.throws(() => client.frameQuery(input), /BAD_QUERY: Frame.*canonical nonnegative integer/)
  }
})

test('view explicitly limits all fields/precision claims to the vtx-emitted wire', () => {
  const text = JSON.stringify(client.TimelineView({ snapshot, result }))
  assert.ok(text.includes('all vtx-emitted fields'))
  assert.ok(text.includes('wire numeric tokens are preserved'))
  assert.ok(text.includes('Original source numeric/lexical precision is not guaranteed'))
  assert.equal(text.includes('all original fields'), false)
})

test('unsupported browser decoder returns a clear error rather than rounded display', () => {
  const raw = JSON.rawJSON
  try {
    JSON.rawJSON = undefined
    assert.throws(() => client.decodeControlResponse('{"id":9007199254740993}'), /LOSSLESS_JSON_UNSUPPORTED.*Upgrade/)
  } finally { JSON.rawJSON = raw }
})

test('mixed content versions never display mismatched selection or raw document', () => {
  const tree = client.TimelineView({ snapshot, result: { ...result, content_version: 'sha256:two' } })
  assert.equal(tree.props.role, 'alert')
  assert.equal(JSON.stringify(tree).includes('block_raw'), false)
  assert.equal(JSON.stringify(tree).includes('raw_extension'), false)
})

test('independent framivra extension tab and guide registrations dispose cleanly', () => {
  const definitions = [], slots = [], cleanup = [], served = []
  client.apply({ effect: effect => cleanup.push(effect()), sidebarRightTabs: { register: definition => { definitions.push(definition); return () => {} } }, configForms: {
    whileServed: (namespaces, register) => { served.push(namespaces); return register(new Set(namespaces)) },
    get: ns => { assert.equal(ns, 'framivra-readonly'); return { getSnapshot: () => ({ status: 'loading' }), subscribe: () => () => {}, mutate: async () => false } },
  }, slots: {
    inject: (_name, register) => register(),
    register: (definition, Body) => { slots.push({ definition, Body }); return () => {} },
  } })
  assert.equal(definitions[0].kind, 'framivra')
  assert.equal(definitions[0].priority, 'extension')
  assert.deepEqual(slots.map(slot => slot.definition.name), ['sidebar.right.pane.tab', 'sidebar.right.tab.guide.entry', 'plugins.bundle.config'])
  assert.deepEqual(served, [['framivra-readonly']])
  assert.equal(slots[2].definition.key, 'framivra-dsh-sidebar')
  const props = slots[0].definition.inject('session')
  assert.equal(props.sessionId, 'session')
  assert.deepEqual(props.getDraftServices(), { conversation: undefined, sessions: undefined, uiSession: undefined, active: true })
  for (const dispose of cleanup.reverse()) dispose()
  assert.deepEqual(props.getDraftServices(), { conversation: undefined, sessions: undefined, uiSession: undefined, active: false })
})

// Regression against the INSTALLED @deepseek-ai/dsh-client-ui-sidebar-right
// (0.2.2-rc.1) client contract, verified in its shipped lib/types +
// lib/client.js: useTabInfo() returns { sidebar, panel, tab } where tab
// carries the TabRecord (id: TabId) plus the tab-scoped actions, and
// tab.actions.openTab takes a BOOLEAN replaceTab that the runtime resolves
// to the CURRENT occurrence TabId before the controller-level openTabIn
// (TabDomain place()). A raw TabId passed at this seat is silently dropped
// by that translation, so the guide MUST send strict boolean true — that is
// what makes the resolved controller call carry the current tab id.
test('guide entry opens framivra via the tab-scoped boolean replaceTab, resolved by the runtime to the CURRENT TabId', () => {
  const slots = [], cleanup = []
  client.apply({ effect: effect => cleanup.push(effect()), sidebarRightTabs: { register: () => () => {} }, configForms: {
    whileServed: (namespaces, register) => register(new Set(namespaces)),
    get: () => ({ getSnapshot: () => ({ status: 'loading' }), subscribe: () => () => {}, mutate: async () => false }),
  }, slots: {
    inject: (_name, register) => register(),
    register: (definition, Body) => { slots.push({ definition, Body }); return () => {} },
  } })
  for (const dispose of cleanup.reverse()) dispose()
  const guide = slots.find(slot => slot.definition.name === 'sidebar.right.tab.guide.entry')
  assert.ok(guide, 'guide entry registration exists')
  const TAB_ID = 'tab-guide-current'
  let openedTab, rawOpenOptions
  // Stub mirrors the installed runtime translation (lib/client.js place()).
  const useTabInfo = () => ({ tab: { id: TAB_ID, actions: {
    openTab(kind, options = {}) {
      rawOpenOptions = options
      openedTab = { kind, options: { ...(options.replaceTab === true ? { replaceTab: TAB_ID } : {}) } }
    },
  } } })
  const tree = guide.Body({ useTabInfo })
  const button = [tree, ...(tree.children ?? [])].find(node => node?.type === 'button')
  assert.ok(button, 'guide renders its entry button')
  button.props.onClick()
  assert.equal(openedTab.kind, 'framivra')
  assert.equal(rawOpenOptions.replaceTab, true, 'the tab-scoped placement is the BOOLEAN spelling (a raw TabId would be dropped by the runtime translation)')
  assert.equal(openedTab.options.replaceTab, TAB_ID, 'the runtime resolves replaceTab:true to the CURRENT occurrence TabId')
})
