// Deliberately NOT a query engine: fixed read-protocol fixtures, independent of input contents.
const id = JSON.rawJSON('9007199254740993')
const args = process.argv.slice(2)
const operation = args[2]
if (!['capabilities', 'inspect', 'query'].includes(operation)) process.exit(2)
if (operation === 'capabilities') {
  console.log(JSON.stringify({ schema: 'framivra.timeline.read/v1', operation, ok: true, content_version: null, diagnostics: [], capabilities: { operations: ['inspect', 'query', 'capabilities'], read_only: true } }))
} else {
  const document = { extension: { preserve: '<script>never HTML</script>', asset_id: id }, project: { duration: 100, fps: 30 }, subtitles_src: 'external.srt' }
  const tracks = ['video', 'video', 'audio', 'audio', 'subtitles'].map((family, index) => ({ family, track_index: index < 2 ? index : family === 'audio' ? index - 2 : 0, address: `/fixture/${index}`, data: { arbitrary_original_field: index, asset_id: id } }))
  const blocks = tracks.map(track => ({ family: track.family, track_index: track.track_index, block_index: 0, address: `${track.address}/blocks/0`, range: [0, 100], data: { extension: true, text: 'fixed mock block', asset_id: id } }))
  console.log(JSON.stringify({ schema: 'framivra.timeline.read/v1', operation, ok: true, content_version: `sha256:${'a'.repeat(64)}`, diagnostics: [{ level: 'warning', message: 'fixture only' }], coverage: { complete: false, scope: 'stored timeline blocks only', unexpanded: [{ field: 'subtitles_src', reason: 'not expanded' }] }, ...(operation === 'inspect' ? { document } : { tracks, blocks }) }))
}
