/** UI intent only: addresses/ordering come from the same-version kernel query. */
export const emptySelection = () => ({ items: [], primary: undefined, anchor: undefined })
const same = (a, b) => a?.kind === b?.kind && a?.address === b?.address
export function transitionSelection(state, item, event = {}, result) {
  const toggle = event.ctrlKey || event.metaKey
  const ordered = (item.kind === 'block' ? result.blocks : result.tracks).map(value => ({ kind: item.kind, address: value.address }))
  let items
  if (event.shiftKey && state.anchor?.kind === item.kind) {
    const from = ordered.findIndex(value => same(value, state.anchor)), to = ordered.findIndex(value => same(value, item))
    const range = from < 0 || to < 0 ? [item] : ordered.slice(Math.min(from, to), Math.max(from, to) + 1)
    items = toggle ? state.items.concat(range.filter(value => !state.items.some(old => same(old, value)))) : range
  } else items = toggle ? state.items.some(value => same(value, item)) ? state.items.filter(value => !same(value, item)) : state.items.concat(item) : [item]
  // A toggled-off object cannot remain the destructive/reference primary.
  const primary = items.find(value => same(value, item)) ?? items.at(-1)
  return { items, primary, anchor: event.shiftKey && state.anchor ? state.anchor : primary }
}

export function preserveSelection(state, previous, result) {
  const oldItems = [...previous.tracks.map(value => ({ ...value, kind: 'track' })), ...previous.blocks.map(value => ({ ...value, kind: 'block' }))]
  const newItems = [...result.tracks.map(value => ({ ...value, kind: 'track' })), ...result.blocks.map(value => ({ ...value, kind: 'block' }))]
  const preserve = item => {
    const stable = oldItems.find(value => same(value, item))?.data?.id
    const next = typeof stable === 'string' ? newItems.find(value => value.data?.id === stable && value.kind === item?.kind) : undefined
    return next ? { kind: next.kind, address: next.address } : undefined
  }
  const items = state.items.map(preserve).filter(Boolean)
  return { items, primary: preserve(state.primary) ?? items.at(-1), anchor: preserve(state.anchor) }
}
