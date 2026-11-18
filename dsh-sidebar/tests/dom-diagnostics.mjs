// Diagnostics only: never pass live React/jsdom nodes to Node's assertion
// formatter, clone the tree, or concatenate the whole body's text first.
export function domTextExcerpt(document) {
  const { NodeFilter } = document.defaultView
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (node.nodeType === 1) return /^(STYLE|SCRIPT)$/.test(node.tagName) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_SKIP
      return NodeFilter.FILTER_ACCEPT
    },
  })
  let text = '', node
  while (text.length < 300 && (node = walker.nextNode())) text += node.data.slice(0, 300 - text.length)
  return text
}
