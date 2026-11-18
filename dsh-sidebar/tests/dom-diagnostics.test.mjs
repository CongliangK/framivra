import test from 'node:test'
import assert from 'node:assert/strict'
import { JSDOM } from 'jsdom'
import { domTextExcerpt } from './dom-diagnostics.mjs'

test('DOM diagnostics omit style/script and bound text without reading the entire body', () => {
  const dom = new JSDOM('<body><style>hidden CSS</style><script>hidden JS</script><p>ready <b>message</b></p></body>')
  try {
    const { document } = dom.window
    // The old textContent.slice path concatenates the entire React tree first.
    Object.defineProperty(document.body, 'textContent', { get() { throw new Error('whole-body text must not be read for diagnostics') } })
    assert.equal(domTextExcerpt(document), 'ready message')
    document.querySelector('p').append(document.createTextNode('x'.repeat(1000)))
    assert.equal(domTextExcerpt(document), 'ready message' + 'x'.repeat(287))
  } finally { dom.window.close() }
})
