import React, { useEffect, useId, useRef } from 'react'
import { Icon } from './icons.mjs'

const h = React.createElement

// Shared by the embedded workbench and independently rendered timeline/frame.
export const iconControlCSS = `
.fv-sr-only{position:absolute!important;width:1px!important;height:1px!important;padding:0!important;margin:-1px!important;overflow:hidden!important;clip-path:inset(50%)!important;white-space:nowrap!important;border:0!important}
:is(.fv-workbench,.fv-timeline,.fv-frame-tools) .fv-icon-button{display:inline-flex;align-items:center;justify-content:center;flex:0 0 auto;width:34px;min-width:34px;height:34px;min-height:34px;padding:0;gap:0;border:1px solid transparent;border-radius:5px;background:transparent;color:inherit;cursor:pointer;touch-action:manipulation;list-style:none;vertical-align:middle}
:is(.fv-workbench,.fv-timeline,.fv-frame-tools) .fv-icon-button::-webkit-details-marker{display:none}
:is(.fv-workbench,.fv-timeline,.fv-frame-tools) .fv-icon-button:hover:not(:disabled){background:var(--fv-raised,color-mix(in srgb,currentColor 12%,transparent));border-color:var(--fv-line,transparent)}
:is(.fv-workbench,.fv-timeline,.fv-frame-tools) .fv-icon-button:disabled{opacity:.4;cursor:default}
:is(.fv-workbench,.fv-timeline,.fv-frame-tools) .fv-icon-button:focus-visible{outline:2px solid var(--fv-accent,#8ecab6);outline-offset:2px}
:is(.fv-workbench,.fv-timeline,.fv-frame-tools) .fv-icon-button[aria-pressed=true],:is(.fv-workbench,.fv-timeline,.fv-frame-tools) details[open]>.fv-icon-button{background:color-mix(in srgb,var(--fv-accent,#8ecab6) 16%,transparent);color:var(--fv-accent,#8ecab6);border-color:color-mix(in srgb,var(--fv-accent,#8ecab6) 40%,transparent)}
:is(.fv-workbench,.fv-timeline,.fv-frame-tools) .fv-icon-button[data-variant=primary]{background:var(--fv-accent,#8ecab6);color:#14251f;border-color:var(--fv-accent,#8ecab6)}
:is(.fv-workbench,.fv-timeline,.fv-frame-tools) .fv-icon-button[data-variant=danger]:hover:not(:disabled){color:#f3aaa4;border-color:#88544e}
.fv-icon-tooltip[popover]{position:fixed;inset:auto;margin:0;padding:7px 10px;max-width:min(320px,calc(100vw - 16px));border:1px solid var(--fv-line,#454a54);border-radius:5px;background:var(--fv-raised,#2b2e35);color:var(--fv-text,#eceef2);font:12px/1.5 system-ui,sans-serif;white-space:normal;overflow-wrap:anywhere;box-shadow:0 3px 10px #0005;z-index:1;pointer-events:auto}
.fv-icon-tooltip[hidden]{display:none!important}
@media(pointer:coarse){:is(.fv-workbench,.fv-timeline,.fv-frame-tools) .fv-icon-button{width:44px;min-width:44px;height:44px;min-height:44px}}
`

/** Native controls, accessible names, and dismissible top-layer help. No IO. */
function IconControl({ as = 'button', icon, label, tooltip = label, size = 18, className = '', elementRef, variant, onKeyDown, onFocus, onBlur, onPointerEnter, onPointerLeave, onPointerDown, disabled, ...props }) {
  const id = useId()
  const node = useRef(), tip = useRef(), teardown = useRef(), timer = useRef()
  const hovering = useRef(false), focused = useRef(false)
  const clearTimer = () => { if (timer.current) { timer.current.window.clearTimeout(timer.current.id); timer.current = undefined } }
  const hide = () => {
    clearTimer(); teardown.current?.(); teardown.current = undefined
    if (tip.current) {
      try { tip.current.hidePopover?.() } catch { /* already dismissed */ }
      tip.current.hidden = true
    }
    if (node.current) node.current.title = node.current.getAttribute('data-fv-tooltip') ?? ''
  }
  const show = () => {
    clearTimer()
    const button = node.current, popup = tip.current, hostWindow = button?.ownerDocument?.defaultView
    if (!button || !popup || !hostWindow || disabled || typeof popup.showPopover !== 'function') return // native title fallback
    const box = button.getBoundingClientRect()
    popup.hidden = false
    try { popup.showPopover() } catch { popup.hidden = true; return }
    const rect = popup.getBoundingClientRect()
    popup.style.left = `${Math.max(8, Math.min(box.left + (box.width - rect.width) / 2, hostWindow.innerWidth - rect.width - 8))}px`
    popup.style.top = `${Math.max(8, box.bottom + 6 + rect.height <= hostWindow.innerHeight - 8 ? box.bottom + 6 : box.top - rect.height - 6)}px`
    button.removeAttribute('title') // avoid a second native tooltip over the popover
    if (teardown.current) return
    const dismiss = () => { hovering.current = false; focused.current = false; hide() }
    const hidden = () => { if (button.ownerDocument.hidden) dismiss() }
    hostWindow.addEventListener('scroll', dismiss, true)
    hostWindow.addEventListener('resize', dismiss)
    hostWindow.addEventListener('blur', dismiss)
    button.ownerDocument.addEventListener('visibilitychange', hidden)
    teardown.current = () => {
      hostWindow.removeEventListener('scroll', dismiss, true)
      hostWindow.removeEventListener('resize', dismiss)
      hostWindow.removeEventListener('blur', dismiss)
      button.ownerDocument.removeEventListener('visibilitychange', hidden)
    }
  }
  const leave = () => {
    if (focused.current || hovering.current) return
    const hostWindow = node.current?.ownerDocument?.defaultView
    if (hostWindow) { clearTimer(); timer.current = { window: hostWindow, id: hostWindow.setTimeout(hide, 140) } }
  }
  useEffect(() => () => { hovering.current = false; focused.current = false; hide() }, [tooltip, disabled])
  return h(React.Fragment, null,
    h(as, {
      ...props, type: as === 'button' ? (props.type ?? 'button') : undefined, disabled: as === 'button' ? disabled : undefined,
      ref: value => { node.current = value; if (typeof elementRef === 'function') elementRef(value); else if (elementRef) elementRef.current = value },
      className: `fv-icon-button ${className}`.trim(), 'data-variant': variant, 'data-fv-tooltip': tooltip,
      title: tooltip, 'aria-label': label, 'aria-describedby': id,
      onFocus: event => { focused.current = true; show(); onFocus?.(event) },
      onBlur: event => { focused.current = false; hide(); onBlur?.(event) },
      onPointerEnter: event => { hovering.current = true; show(); onPointerEnter?.(event) },
      onPointerLeave: event => { hovering.current = false; leave(); onPointerLeave?.(event) },
      onPointerDown: event => { hide(); onPointerDown?.(event) },
      onKeyDown: event => {
        if (event.key === 'Escape' && tip.current && !tip.current.hidden) { hide(); event.preventDefault(); event.stopPropagation() }
        onKeyDown?.(event)
      },
    }, h(Icon, { name: icon, size }), h('span', { className: 'fv-sr-only' }, label)),
    h('span', { id, ref: tip, role: 'tooltip', popover: 'manual', hidden: true, className: 'fv-icon-tooltip',
      onPointerEnter: () => { hovering.current = true; clearTimer() }, onPointerLeave: () => { hovering.current = false; leave() } }, tooltip))
}

export function ToolButton(props) { return h(IconControl, props) }
export function ToolSummary(props) { return h(IconControl, { ...props, as: 'summary' }) }
