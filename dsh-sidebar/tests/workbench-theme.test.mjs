import test from 'node:test'
import assert from 'node:assert/strict'
import { JSDOM } from 'jsdom'
import { workbenchCSS } from '../src/client/workbench.mjs'
import { iconControlCSS } from '../src/client/icon-controls.mjs'

// Declaration contracts only. Theme resolution, top-layer inheritance, contrast
// and layout are checked in the real official host, not inferred from JSDOM.
function declarations(css, selector) {
  const dom = new JSDOM('<style></style>')
  try {
    const style = dom.window.document.querySelector('style')
    style.textContent = css
    const rule = [...style.sheet.cssRules].find(rule => rule.selectorText === selector)
    assert(rule, `missing scoped rule: ${selector}`)
    return Object.fromEntries([...rule.style].map(name => [name, rule.style.getPropertyValue(name)]))
  } finally { dom.window.close() }
}

test('workbench and guide consume live host theme aliases without OS detection or a forced dark scheme', () => {
  const root = declarations(workbenchCSS, '.fv-workbench,.fv-guide')
  for (const [local, host] of Object.entries({
    '--fv-bg': '--dsw-alias-bg-base', '--fv-surface': '--dsw-alias-bg-layer-1',
    '--fv-raised': '--dsw-alias-interactive-bg-hover-solid', '--fv-line': '--dsw-alias-border-l2',
    '--fv-text': '--dsw-alias-label-primary', '--fv-muted': '--dsw-alias-label-secondary',
    '--fv-accent': '--dsw-alias-state-business-primary', '--fv-scrollbar': '--dsw-alias-scrollbar-bg-l1',
  })) assert(root[local].startsWith(`var(${host},`), `${local} must follow the host and retain a standalone fallback`)
  assert.equal(root['color-scheme'], 'inherit')
  assert(root.font.includes('var(--dsw-font-family,'))
  assert(!workbenchCSS.includes('prefers-color-scheme'))
  assert.equal(root['--fv-tl-surface'], 'var(--fv-surface)')
  assert.equal(root['--fv-tl-ruler-ink'], 'var(--fv-text)', 'opaque ruler needs theme-matched ink, not its dark-only standalone fallback')
  assert.equal(root['--fv-tl-playhead'], 'var(--dsw-alias-state-error-primary,#ee7272)')
})

test('text and icon primary controls use the host paired fill/foreground, including hover', () => {
  const root = declarations(workbenchCSS, '.fv-workbench,.fv-guide')
  assert.equal(root['--fv-primary'], 'var(--dsw-alias-button-primary-fill,#91c6b2)')
  assert.equal(root['--fv-primary-text'], 'var(--dsw-alias-label-primary-foreground,#14271f)')
  assert.equal(root['--fv-primary-hover'], 'var(--dsw-alias-button-primary-hover,#b3dec9)')
  const text = declarations(workbenchCSS, '.fv-workbench .fv-primary')
  assert.equal(text.background, 'var(--fv-primary)')
  assert.equal(text.color, 'var(--fv-primary-text)')
  const selector = ':is(.fv-workbench,.fv-timeline,.fv-frame-tools) .fv-icon-button[data-variant=primary]'
  const icon = declarations(iconControlCSS, selector)
  assert(icon.background.includes('--dsw-alias-button-primary-fill'))
  assert(icon.color.includes('--dsw-alias-label-primary-foreground'))
  assert(declarations(iconControlCSS, `${selector}:hover:not(:disabled)`).background.includes('--dsw-alias-button-primary-hover'))
})

test('monitor chrome and diagnostics follow the host while output canvas remains dark', () => {
  assert.equal(declarations(workbenchCSS, '.fv-monitor').background, 'var(--fv-bg)')
  const code = declarations(workbenchCSS, '.fv-workbench pre')
  assert.equal(code.background, 'var(--fv-bg)')
  assert(code.font.includes('--ds-font-family-code'))
  assert.equal(declarations(workbenchCSS, '.fv-workbench [role=alert]').color, 'var(--dsw-alias-state-error-primary,#f0bc91)')
  assert.equal(declarations(workbenchCSS, '.fv-output video').background, 'rgb(8, 10, 12)')
  const tip = declarations(iconControlCSS, '.fv-icon-tooltip[popover]')
  assert.equal(tip.background, 'var(--dsw-alias-tooltip-bg,#2b2e35)')
  assert.equal(tip.color, 'var(--dsw-alias-toast-label,#eceef2)')
  assert(tip.font.includes('--dsw-font-family'))
  const danger = declarations(iconControlCSS, ':is(.fv-workbench,.fv-timeline,.fv-frame-tools) .fv-icon-button[data-variant=danger]:hover:not(:disabled)')
  assert.equal(danger.color, 'var(--dsw-alias-state-error-primary,#f3aaa4)')
  assert.equal(danger['border-color'], 'currentcolor')
})

test('only the wide modal transport sticks within its existing monitor scrollport', () => {
  const dom = new JSDOM(`<style>${workbenchCSS}</style>`)
  try {
    const rules = [...dom.window.document.querySelector('style').sheet.cssRules]
    const wide = rules.find(rule => rule.conditionText === 'fv-workbench (min-width:860px)')
    assert(wide, 'wide workbench container rule exists')
    const transport = [...wide.cssRules].find(rule => rule.selectorText === '.fv-workbench[open] .fv-transport')
    assert(transport)
    assert.equal(transport.style.position, 'sticky')
    assert.equal(transport.style.top, '0px')
    assert.equal(transport.style.background, 'var(--fv-bg)', 'sticky controls require opaque theme-matched backing')
    assert(!rules.some(rule => rule.selectorText === '.fv-transport' && rule.style.position), 'narrow stacked layout is unchanged')
  } finally { dom.window.close() }
})
