import React, { useEffect, useRef, useState } from 'react'
import { Icon } from './icons.mjs'
import { iconControlCSS, ToolButton } from './icon-controls.mjs'
export { Icon } from './icons.mjs'

const h = React.createElement
export const workbenchCSS = `
.fv-shell{height:100%;min-width:0;overflow:auto;container-type:inline-size}
.fv-workbench,.fv-guide{--fv-bg:#181a1e;--fv-surface:#212329;--fv-raised:#2b2e35;--fv-line:#383c44;--fv-text:#e9ebef;--fv-muted:#b1b8c4;--fv-accent:#8cbfae;--fv-tl-accent:#8eb9ed;--fv-tl-surface:#212329;color-scheme:dark;color:var(--fv-text);font:400 13px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.fv-workbench{container:fv-workbench / inline-size;background:var(--fv-bg);padding:0;border:0;box-sizing:border-box;min-width:0;overflow-wrap:anywhere;max-width:none}
.fv-workbench:not([open]){display:flex;position:static;flex-direction:column;width:100%;height:100%;margin:0;max-height:none}
.fv-workbench[open]{display:flex;flex-direction:column;width:min(1560px,96vw);height:94dvh;max-height:94dvh;max-width:96vw;margin:auto;border:1px solid var(--fv-line);border-radius:10px;box-shadow:0 20px 80px #0009}
.fv-workbench::backdrop{background:#0009}
.fv-workbench *,.fv-guide *{box-sizing:border-box}
.fv-workbench ::selection{background:#436c63;color:#fff}
.fv-workbench :is(button,input,select,summary){font:inherit}
.fv-workbench button,.fv-guide{cursor:pointer;touch-action:manipulation}
.fv-workbench button{display:inline-flex;align-items:center;justify-content:center;gap:6px;min-height:34px;padding:6px 10px;border:1px solid var(--fv-line);border-radius:5px;background:var(--fv-surface);color:var(--fv-text);line-height:1.3;transition:background-color .15s,border-color .15s}
.fv-workbench button:hover:not(:disabled){background:var(--fv-raised);border-color:#697483}
.fv-workbench button:disabled{opacity:.45;cursor:not-allowed}
.fv-workbench :is(button,input,select,summary):focus-visible,.fv-guide:focus-visible{outline:2px solid var(--fv-accent);outline-offset:3px}
.fv-workbench input:not([type=checkbox]),.fv-workbench select{min-width:0;max-width:100%;min-height:34px;border:1px solid var(--fv-line);border-radius:4px;padding:6px 8px;background:#17191d;color:var(--fv-text);caret-color:var(--fv-accent)}
.fv-workbench input[type=checkbox]{accent-color:var(--fv-accent);width:16px;height:16px;flex:none}
.fv-workbench label{display:flex;align-items:center;gap:7px;min-width:0}
.fv-workbench :is(h2,h3,p){margin:0}
.fv-workbench h2{font-size:14px;font-weight:600;letter-spacing:-.01em}
.fv-workbench h3{font-size:13px;font-weight:600}
.fv-workbench details{min-width:0}
.fv-workbench summary{cursor:pointer;color:var(--fv-muted);padding:8px 0;min-height:34px}
.fv-workbench pre{font:11px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace;max-height:340px;overflow:auto;background:#17191d;padding:10px;border-radius:4px;white-space:pre-wrap;overflow-wrap:anywhere}
.fv-workbench [role=alert]{color:#f0bc91}
.fv-workbench [role=status]{color:var(--fv-muted)}
.fv-workbench :is(.fv-body,.fv-inspector,.fv-files,.fv-timeline pre){scrollbar-color:#555d6b transparent;scrollbar-width:thin;overscroll-behavior:contain}
.fv-topbar{display:flex;gap:8px;align-items:center;padding:6px 12px;border-bottom:1px solid var(--fv-line);flex:none;min-width:0;position:relative;z-index:3}
.fv-topbar>.fv-path{padding:0;border:0;flex:none}.fv-topbar>.fv-path>summary{list-style:none;white-space:nowrap;min-height:34px;padding:6px 10px;border:1px solid var(--fv-line);border-radius:5px;background:var(--fv-surface);color:var(--fv-text)}.fv-topbar>.fv-path>summary::-webkit-details-marker{display:none}.fv-topbar>.fv-path[open]>summary{border-color:var(--fv-accent)}
.fv-topbar>.fv-path>form{position:absolute;right:12px;top:calc(100% + 4px);width:min(500px,calc(100% - 24px));padding:12px;background:var(--fv-raised);border:1px solid var(--fv-line);border-radius:5px;box-shadow:0 8px 20px #0006;z-index:5}
.fv-brand{color:var(--fv-accent);display:flex;flex:none}
.fv-title{display:flex;align-items:baseline;gap:10px;flex:1;min-width:0;overflow:hidden;white-space:nowrap}
.fv-title h2{flex:0 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fv-subtitle{flex:0 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--fv-muted);font-size:11px}
.fv-topbar .fv-icon-button{width:34px;flex:none;padding:6px}
.fv-topbar button[aria-pressed=true]{background:#344740;border-color:var(--fv-accent)}
.fv-body{flex:1;min-height:0;overflow:auto}
.fv-upper{display:flex;flex-direction:column;min-width:0}
.fv-monitor{min-width:0;background:#141619;padding:12px;position:relative}
.fv-monitor-toolbar,.fv-monitor-footer{position:relative;display:flex;align-items:center;gap:4px;min-width:0;flex:none}
.fv-monitor-toolbar{justify-content:space-between;z-index:2}
.fv-monitor-footer{z-index:1}
.fv-transport,.fv-preview-modes{display:flex;align-items:center;gap:4px;min-width:0}
.fv-preview-modes{flex:none}
.fv-transport .fv-frame-input{display:block;min-width:0}
.fv-transport .fv-frame-input input{width:112px;text-align:center;font:500 13px/1.3 ui-monospace,SFMono-Regular,Consolas,monospace;font-variant-numeric:tabular-nums}
.fv-transport-error{flex:none;color:#f0bc91;font-weight:700;padding-inline:2px}
.fv-duration,.fv-output-provenance{font-size:12px;color:var(--fv-muted);white-space:nowrap}
.fv-duration{font-variant-numeric:tabular-nums;overflow:hidden;text-overflow:ellipsis;min-width:0}
.fv-monitor-status{display:block;min-width:0;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fv-monitor-disclosure>.fv-monitor-popover{position:absolute;right:0;top:calc(100% + 4px);width:min(360px,100%);max-height:min(245px,45dvh);overflow:auto;overscroll-behavior:contain;padding:10px 12px;background:var(--fv-raised);border:1px solid var(--fv-line);border-radius:6px;box-shadow:0 4px 16px #0006}
.fv-monitor-disclosure-up>.fv-monitor-popover{left:0;right:auto;top:auto;bottom:calc(100% + 4px)}
.fv-monitor-popover>*+*,.fv-seconds-helper>*+*{margin-top:6px}
.fv-seconds-helper{border-top:1px solid var(--fv-line);padding-top:6px}
.fv-empty{display:flex;flex:none;flex-direction:column;align-items:flex-start;gap:16px;width:min(100%,480px);color:var(--fv-muted);text-align:left}
.fv-empty>svg{color:var(--fv-muted)}
.fv-empty h3{color:var(--fv-text);font-size:24px;line-height:1.35;text-wrap:balance}
.fv-empty p{font-size:14px;line-height:1.7;text-wrap:pretty}
.fv-start-actions{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-top:8px}
.fv-start-actions .fv-path-action{background:transparent;border-color:transparent;color:var(--fv-muted)}
.fv-workbench .fv-primary{background:#91c6b2;color:#14271f;border-color:#91c6b2;font-weight:600}
.fv-workbench .fv-primary:hover:not(:disabled){background:#b3dec9;border-color:#b3dec9}
.fv-files{display:flex;flex-direction:column;background:var(--fv-surface);border-bottom:1px solid var(--fv-line);padding:12px;min-width:0;min-height:0;overflow:hidden;max-height:330px}
.fv-file-toolbar{display:flex;align-items:center;gap:4px;flex:none;min-width:0;margin-bottom:4px}
.fv-file-toolbar>.fv-file-search{flex:1;min-width:0;width:0;margin:0}
.fv-file-toolbar>.fv-file-location{display:flex;flex:none;margin:0}
.fv-file-location .fv-icon-button svg{transform:rotate(90deg)}
.fv-file-scroll{flex:1;min-height:0;overflow:auto;padding:4px;margin:0 -4px -4px;scrollbar-color:#555d6b transparent;scrollbar-width:thin;overscroll-behavior:contain}
.fv-file-scroll:focus-visible{outline:2px solid var(--fv-accent);outline-offset:-2px}
.fv-panel-heading{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px}
.fv-panel-heading button{min-height:30px;padding:4px 7px}
.fv-file-location{display:flex;align-items:center;gap:6px;color:var(--fv-muted);margin-bottom:8px;min-width:0}
.fv-file-location span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px}
.fv-file-search{width:100%;margin-bottom:8px}
.fv-file-list{list-style:none;padding:0;margin:0;display:grid;gap:2px}
.fv-file-list button{border-color:transparent;background:transparent;justify-content:flex-start;width:100%;text-align:left;min-height:38px}
.fv-file-list button span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fv-file-list svg{color:#b8c8de;flex:none}
.fv-file-list [aria-current=true]{background:#344740;border-color:#6d9a88}
.fv-file-status{font-size:12px;color:var(--fv-muted);padding:8px 0}
.fv-inspector{padding:12px;background:var(--fv-surface);border-top:1px solid var(--fv-line);overflow:auto;min-width:0;max-height:650px}
.fv-inspector[hidden]{display:none}
.fv-inspector-section{border-bottom:1px solid var(--fv-line);padding:0 0 10px;margin-bottom:8px}
.fv-inspector-section>summary{color:var(--fv-text);font-weight:600}
.fv-inspector section{border:0!important;padding:0!important;min-width:0}
.fv-inspector section>*+*{margin-top:10px}
.fv-inspector section>h3{display:none}
.fv-inspector section>button{margin-right:5px;max-width:100%;text-align:left;justify-content:flex-start}
.fv-inspector fieldset{border:1px solid var(--fv-line);border-radius:4px;padding:10px;margin:12px 0;min-width:0}
.fv-inspector fieldset label{display:block;margin:8px 0;font-size:12px;color:var(--fv-muted)}
.fv-inspector fieldset :is(input:not([type=checkbox]),select){display:block;width:100%;margin-top:4px}
.fv-inspector p{font-size:12px;color:var(--fv-muted)}
.fv-inspector legend{font-size:11px;color:var(--fv-muted)}
.fv-timeline-pane{border-top:1px solid var(--fv-line);background:var(--fv-surface);padding:4px 8px 8px;min-width:0}
.fv-data{border-top:1px solid var(--fv-line);margin-top:12px;color:var(--fv-muted);font-size:12px}
.fv-data p{margin:8px 0}
.fv-notice{padding:8px 12px;font-size:12px;border-bottom:1px solid var(--fv-line);white-space:pre-wrap}
.fv-path{padding:0 12px;border-bottom:1px solid var(--fv-line);font-size:12px}
.fv-path form{display:flex;flex-wrap:wrap;gap:8px;padding-bottom:12px}
.fv-path label{flex:1;min-width:160px;flex-wrap:wrap}
.fv-path input{width:100%}
.fv-program-monitor{display:flex;flex-direction:column;min-width:0;min-height:0;gap:8px}
.fv-rendered{display:flex;flex-direction:column;gap:6px;min-height:0}
.fv-program-stage{position:relative;width:100%;aspect-ratio:16/9;max-height:320px;min-height:100px;background:#000;border-radius:6px;overflow:hidden}
.fv-program-stage video{display:block;width:100%;height:100%;object-fit:contain;background:#000}
.fv-program-empty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding:16px;text-align:center;font-size:12px;color:var(--fv-muted)}
.fv-guide{display:flex;width:100%;align-items:center;gap:12px;padding:12px 16px;min-height:48px;border:1px solid #41454c;background:#24262c;border-radius:12px;text-align:left;transition:background-color .15s}
.fv-guide:hover{background:#333740}
.fv-guide svg{color:var(--fv-accent)}
.fv-guide strong{font-size:14px;font-weight:500}
.fv-guide small{font-size:11px;color:var(--fv-muted);margin-left:auto}
.fv-layout-divider{display:none}
.fv-selection-bar,.fv-selection-actions{display:flex;align-items:center;gap:2px;flex:none}
.fv-selection-bar{border-inline-start:1px solid var(--fv-line);padding-inline-start:6px}
.fv-selection-description{display:flex;align-items:center;gap:4px;min-width:0;max-width:130px}
.fv-selection-kind{display:flex;flex:none;color:var(--fv-tl-accent)}
.fv-selection-kind[data-family=audio]{color:#93cfba}.fv-selection-kind[data-family=gfx]{color:#bf9cdd}.fv-selection-kind[data-family=subtitles]{color:#e1be7c}
.fv-selection-description strong{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;font-weight:500}
@media(pointer:coarse){.fv-workbench[open] .fv-layout-divider{min-height:32px}}
@container fv-workbench (max-width:600px){.fv-duration{display:none}}
@container fv-workbench (min-width:860px){

 /* Bound the inline grid as well as the modal: otherwise a long file list
    grows its own scrollport, and overscroll containment traps the wheel. */
 .fv-upper{display:grid;grid-template-columns:var(--fv-files-column,0px) minmax(0,1fr) var(--fv-inspector-column,0px);grid-template-rows:minmax(0,1fr);align-items:stretch;min-height:0;max-height:100%}
 /* Side panels stretch to the monitor row without contributing their intrinsic height. */
 .fv-workbench[data-view=editor] :is(.fv-files,.fv-inspector){contain:size}
 .fv-workbench[data-view=editor]:not([open]) .fv-body{display:flex;flex-direction:column}
 .fv-workbench[data-view=editor]:not([open]) .fv-timeline-pane{flex:1 0 min(300px,42%);min-height:0}
 .fv-workbench[data-view=editor] .fv-timeline-pane{display:flex;flex-direction:column}
 .fv-workbench[data-view=editor] .fv-timeline{display:flex;flex-direction:column;flex:1;min-height:0}
 .fv-workbench[data-view=editor] .fv-timeline .fv-scroll{flex:1;min-height:70px;max-height:none}
 .fv-files{grid-column:1;border:0;border-right:1px solid var(--fv-line);max-height:none}
 .fv-monitor{grid-column:2;padding:16px 24px;overflow:auto}
 .fv-inspector{grid-column:3;border:0;border-left:1px solid var(--fv-line);max-height:none}
 .fv-topbar{padding:6px 16px}
 .fv-workbench[open] .fv-body{display:flex;flex-direction:column;overflow:hidden}
 .fv-workbench[open] .fv-upper{flex:1;min-height:0;overflow:hidden}
 .fv-workbench[open] .fv-monitor{display:flex;flex-direction:column;padding:10px 16px;overflow:auto;min-height:0}
 .fv-workbench[open] .fv-frame-tools{display:flex;flex-direction:column;flex:1;min-height:0}
 .fv-workbench[open] .fv-frame-tools-stage{flex:1;min-height:100px;max-height:none;aspect-ratio:auto}
 .fv-workbench[open] .fv-frame-tools-stage img{width:100%;height:100%;object-fit:contain}
 .fv-workbench[open] .fv-program-monitor{flex:1;min-height:185px}
 .fv-workbench[open] .fv-rendered{flex:1;min-height:0}
 .fv-workbench[open] .fv-program-stage{flex:1;aspect-ratio:auto;max-height:none}
 .fv-workbench[open] .fv-layout-divider{display:flex;align-items:center;justify-content:center;height:12px;flex:none;border-top:1px solid var(--fv-line);cursor:row-resize;color:var(--fv-muted);background:var(--fv-bg);touch-action:none;user-select:none}
 .fv-workbench[open] .fv-layout-divider:hover,.fv-workbench[open] .fv-layout-divider:focus-visible{color:var(--fv-accent);background:var(--fv-raised);outline:2px solid var(--fv-accent);outline-offset:-2px}
 .fv-workbench[open] .fv-timeline-pane{flex:0 0 var(--fv-timeline-share,38%);min-height:300px;overflow:hidden;padding-top:4px}
 .fv-workbench[open] .fv-timeline .fv-help summary,.fv-workbench[open] .fv-data>summary{padding:3px 0;min-height:24px}
 .fv-workbench[open] .fv-data{flex:none}
 .fv-workbench[open] .fv-data[open]{max-height:45%;overflow:auto}
}
.fv-workbench[data-timeline-focus=true] .fv-body{display:flex;flex-direction:column}
.fv-workbench[data-timeline-focus=true] :is(.fv-upper,.fv-layout-divider){display:none}
.fv-workbench[data-timeline-focus=true] :is(.fv-timeline-pane,.fv-timeline){display:flex;flex-direction:column;flex:1;min-height:0}
.fv-workbench[data-timeline-focus=true] .fv-timeline .fv-scroll{flex:1;min-height:70px;max-height:none}
/* Startup is a workspace chooser, not an empty video frame. The 248px file
   column and flexible main share the available height, without a media ratio. */
.fv-workbench[data-view=start] .fv-body{display:flex;flex-direction:column;overflow:hidden}
.fv-workbench[data-view=start] .fv-upper{flex:1;min-height:0}
.fv-workbench[data-view=start] .fv-monitor{display:flex;flex:1;flex-direction:column;align-items:center;justify-content:safe center;min-height:0;overflow:auto;background:var(--fv-bg);padding:32px 24px clamp(32px,10dvh,96px)}
@container fv-workbench (width < 860px){
 .fv-workbench[data-view=start] .fv-files{flex:1;max-height:none}
 .fv-workbench[data-view=start][data-files=true] .fv-monitor{display:none}
}
@container fv-workbench (max-width:420px){.fv-topbar{gap:7px;padding:6px 10px}.fv-subtitle{display:none}.fv-topbar .fv-open-label{display:none}.fv-monitor{padding:10px}.fv-empty h3{font-size:22px}.fv-timeline-pane{padding:4px 6px 6px}}
@media(prefers-reduced-motion:reduce){.fv-workbench *,.fv-guide{transition:none!important}}
.fv-reference-actions{display:flex;align-items:center;flex-wrap:wrap;gap:4px}
${iconControlCSS}
`
export function WorkbenchStyles() { return h('style', null, workbenchCSS) }

/** Guidance shown when the RUNNING host predates the browse/hello ops: an
 *  unknown op reaches the host's grant gate before BAD_OPERATION, so the
 *  client would otherwise see a misleading BAD_GRANT authorization error. */
const HOST_TOO_OLD = 'HOST_TOO_OLD: 正在运行的宿主插件缺少文件浏览能力（宿主可能未随插件更新重启）。请刷新页面；若仍失败，请重启 DSH 宿主后重试。'

/**
 * Deterministic identity of THIS client bundle, baked by scripts/build.mjs
 * (define) and identical to the host stamp of the artifact it shipped with.
 * Null when the source is executed unbundled (unit tests) — then build
 * comparison is skipped rather than guessed. Test-only override below.
 */
export const CLIENT_BUILD_STAMP = typeof __FRAMIVRA_CLIENT_STAMP__ === 'string' ? __FRAMIVRA_CLIENT_STAMP__ : null
let clientBuildStampOverride
export function setClientBuildStampForTests(stamp) { clientBuildStampOverride = stamp }
export function effectiveClientBuildStamp() { return clientBuildStampOverride !== undefined ? clientBuildStampOverride : CLIENT_BUILD_STAMP }

/** Deterministic host/client build verdict with refresh guidance.
 *  Called ONLY when this client carries a build stamp (a real bundle): a
 *  matching stamp passes; a different reported stamp is HOST_BUILD_MISMATCH;
 *  a MISSING or non-string host build is HOST_BUILD_UNVERIFIED — the running
 *  host predates build reporting, so this client cannot know its feature set
 *  and must fail closed rather than guess "probably fine". Unstamped clients
 *  (unbundled source runs, old bundles) never block: null. */
export function buildMismatchMessage(hostBuild) {
  const clientStamp = effectiveClientBuildStamp()
  if (!clientStamp) return null
  if (typeof hostBuild === 'string' && hostBuild.trim() !== '') {
    if (hostBuild === clientStamp) return null
    return `HOST_BUILD_MISMATCH: 宿主插件与页面客户端构建不一致（host ${hostBuild.slice(0, 20)} / client ${clientStamp.slice(0, 20)}）。请刷新页面；若仍失败，请重启 DSH 宿主后重试。`
  }
  return `HOST_BUILD_UNVERIFIED: 运行中的宿主未上报构建身份（宿主插件早于本客户端构建，或未随插件更新重启）。请刷新页面；若仍失败，请重启 DSH 宿主后重试。`
}

/** A Host-bound file picker, not a browser upload or a second timeline parser. */
export function FileBrowser({ sessionId, tabSignal, call, onChoose, selectedPath, onClose, recalibrate = 0, hostOps }) {
  const [state, setState] = useState({ path: '', phase: 'loading', entries: [] })
  const [search, setSearch] = useState('')
  const request = useRef()
  const pathRef = useRef('')
  const stateRef = useRef(state)
  stateRef.current = state
  // The open response carries the host protocol ops; if the running host is
  // KNOWN (via handshake identity) to lack browse, fail fast without a call.
  const legacyHost = Array.isArray(hostOps) && !hostOps.includes('browse')
  async function browse(path, preserveSearch = false) {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    const signal = AbortSignal.any([controller.signal, tabSignal])
    if (!preserveSearch) setSearch('')
    pathRef.current = path
    setState(previous => ({ ...previous, path, phase: 'loading', message: undefined }))
    try {
      const result = await call(sessionId, { op: 'browse', path }, signal)
      signal.throwIfAborted()
      if (result?.schema !== 'framivra.browse/v1' || result.path !== path || !Array.isArray(result.entries) || result.entries.length > 500 || result.entries.some(entry =>
        !entry || typeof entry.name !== 'string' || !entry.name || entry.name.includes('/') || entry.name.includes('\\') || entry.name.includes('\0') || !['directory', 'file'].includes(entry.type) || entry.path !== (path ? `${path}/${entry.name}` : entry.name))) {
        throw new Error('BROWSE_INVALID: 文件列表响应不完整，请刷新文件夹。')
      }
      setState({ ...result, phase: 'ready' })
    } catch (error) {
      if (!signal.aborted) {
        let message = error?.message ?? String(error)
        if (/^BAD_GRANT/.test(message)) {
          // browse is grant-free in every host build that knows the op; a
          // BAD_GRANT here means the RUNNING host predates it. Verify with the
          // grant-free hello handshake so the verdict is checkable, then guide
          // the user instead of misreporting an authorization failure.
          try {
            const hello = await call(sessionId, { op: 'hello' }, signal)
            const mismatch = buildMismatchMessage(hello?.host?.build)
            message = mismatch ?? 'BAD_GRANT: 宿主拒绝了浏览请求，请刷新页面后重试。'
          } catch (helloError) {
            if (/^BAD_GRANT/.test(helloError?.message ?? '')) message = HOST_TOO_OLD
          }
        }
        setState(previous => ({ ...previous, phase: 'failed', entries: [], message }))
      }
    }
  }
  useEffect(() => {
    if (legacyHost) {
      setState(previous => ({ ...previous, phase: 'failed', entries: [], message: HOST_TOO_OLD }))
      return
    }
    void browse('')
    return () => request.current?.abort()
  }, [sessionId, tabSignal, legacyHost])
  // Recalibration after a successful project open: retry a failed listing at
  // its current folder (real errors resurface — never swallowed), and clear a
  // lingering failure banner otherwise. A healthy listing issues no new IO.
  const lastRecalibrate = useRef()
  useEffect(() => {
    if (lastRecalibrate.current === recalibrate) return
    lastRecalibrate.current = recalibrate
    if (!recalibrate || legacyHost) return
    if (stateRef.current.phase === 'failed') void browse(pathRef.current, true)
    else setState(previous => (previous.message ? { ...previous, message: undefined } : previous))
  }, [recalibrate])
  const entries = state.entries.filter(entry => entry.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()))
  const parent = state.path.split('/').slice(0, -1).join('/')
  return h('aside', { className: 'fv-files', 'aria-label': '工作区文件选择器' },
    h('div', { className: 'fv-file-toolbar' },
      h('h3', { className: 'fv-sr-only' }, '项目文件'),
      h('div', { className: 'fv-file-location' },
        h('span', { className: 'fv-sr-only', title: state.path || '当前会话工作区' }, state.path || '当前会话工作区'),
        h(ToolButton, { icon: 'back', size: 14, label: '上一级文件夹', tooltip: `上一级文件夹 · 当前目录：${state.path || '当前会话工作区'}`, disabled: !state.path || state.phase === 'loading', onClick: () => void browse(parent) })),
      h('input', { className: 'fv-file-search', type: 'search', value: search, 'aria-label': '筛选当前文件夹', title: `筛选当前文件夹 · ${state.path || '当前会话工作区'}`, name: 'framivra-file-filter', autoComplete: 'off', placeholder: '筛选文件…', onChange: event => setSearch(event.target.value) }),
      h(ToolButton, { icon: 'refresh', size: 15, label: '刷新文件列表', onClick: () => void browse(state.path, true), disabled: state.phase === 'loading' }),
      h(ToolButton, { icon: 'close', size: 15, label: '收起项目文件', onClick: onClose })),
    h('div', { className: 'fv-file-scroll', role: 'region', 'aria-label': '文件列表', tabIndex: 0 },
    state.phase === 'loading' ? h('p', { className: 'fv-file-status', role: 'status' }, '正在读取文件夹…') : null,
    state.phase === 'failed' ? h('div', { className: 'fv-file-status', role: 'alert' }, state.message,
      h('button', { type: 'button', onClick: () => void browse(state.path, true) }, '重试读取文件夹')) : null,
    state.phase === 'ready' ? h(React.Fragment, null,
      h('ul', { className: 'fv-file-list', 'aria-label': '文件与文件夹' }, entries.map(entry => h('li', { key: entry.path, style: { contentVisibility: 'auto', containIntrinsicSize: '38px' } },
        h('button', { type: 'button', title: entry.name, 'aria-label': `${entry.type === 'directory' ? '打开文件夹' : '打开时间线'} ${entry.name}`, 'aria-current': selectedPath === entry.path ? true : undefined,
          onClick: () => { if (entry.type === 'directory') void browse(entry.path); else onChoose(entry.path) } }, h(Icon, { name: entry.type === 'directory' ? 'folder' : 'file', size: 17 }), h('span', null, entry.name))))),
      !entries.length ? h('p', { className: 'fv-file-status', role: 'status' }, search ? '没有匹配的文件。' : '此文件夹没有 JSON 文件或子文件夹。') : null,
      state.truncated ? h('p', { className: 'fv-file-status', role: 'status' }, `目录过大，仅显示前 ${state.limit} 项。请进入子文件夹。`) : null,
      state.escaped ? h('p', { className: 'fv-file-status' }, '已隐藏指向工作区外的链接。') : null) : null,
    h('details', null, h('summary', null, '选择范围'), h('p', { className: 'fv-file-status' }, '仅浏览当前会话工作区，不上传文件。JSON 是否为有效时间线由 vtx 核验；外部工程请先切换会话工作区。'))))
}
