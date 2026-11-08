import React, { useEffect, useRef, useState } from 'react'

const h = React.createElement
const paths = {
  film: ['M3 5h18v14H3z', 'M7 5v14M17 5v14M3 9h4M3 15h4M17 9h4M17 15h4'],
  folder: ['M3 7h7l2 2h9v10H3z', 'M3 7V5h7l2 2'],
  file: ['M6 3h8l4 4v14H6z', 'M14 3v5h4M9 12h6M9 16h6'],
  expand: ['M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5'],
  collapse: ['M3 8h5V3M21 8h-5V3M3 16h5v5M21 16h-5v5'],
  settings: ['M4 7h16M4 17h16M8 4v6M16 14v6'],
  refresh: ['M20 8a8 8 0 1 0 1 6', 'M20 3v5h-5'],
  back: ['M14 5l-7 7 7 7'],
  close: ['M6 6l12 12M6 18L18 6'],
}
export function Icon({ name, size = 18 }) {
  return h('svg', { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.7, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true },
    (paths[name] ?? paths.film).map((d, index) => h('path', { d, key: index })))
}
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
.fv-topbar{display:flex;gap:10px;align-items:center;padding:12px;border-bottom:1px solid var(--fv-line);flex:none;min-width:0}
.fv-brand{color:var(--fv-accent);display:flex;flex:none}
.fv-title{flex:1;min-width:0}
.fv-title h2{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fv-subtitle{display:block;color:var(--fv-muted);font-size:11px}
.fv-topbar .fv-icon-button{width:34px;flex:none;padding:6px}
.fv-topbar button[aria-pressed=true]{background:#344740;border-color:var(--fv-accent)}
.fv-body{flex:1;min-height:0;overflow:auto}
.fv-upper{display:flex;flex-direction:column;min-width:0}
.fv-monitor{min-width:0;background:#141619;padding:12px;position:relative}
.fv-monitor-title{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:10px;color:var(--fv-muted);font-size:12px}
.fv-frame-query{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
.fv-frame-query label{font-size:11px;color:var(--fv-muted);flex:1;flex-wrap:wrap}
.fv-frame-query input{width:88px;font-variant-numeric:tabular-nums}
.fv-frame-query button{font-size:11px;min-height:30px}
.fv-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;min-height:240px;aspect-ratio:16/9;background:#0c0e11;color:var(--fv-muted);border-radius:4px;text-align:center;padding:24px}
.fv-empty>svg{color:#727f8e;margin-bottom:4px}
.fv-empty h3{color:var(--fv-text);font-size:16px}
.fv-empty p{font-size:12px;max-width:32ch}
.fv-workbench .fv-primary{background:#91c6b2;color:#14271f;border-color:#91c6b2;font-weight:600}
.fv-workbench .fv-primary:hover:not(:disabled){background:#b3dec9;border-color:#b3dec9}
.fv-files{background:var(--fv-surface);border-bottom:1px solid var(--fv-line);padding:12px;min-width:0;overflow:auto;max-height:330px}
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
.fv-timeline-pane{border-top:1px solid var(--fv-line);background:var(--fv-surface);padding:12px;min-width:0}
.fv-data{border-top:1px solid var(--fv-line);margin-top:12px;color:var(--fv-muted);font-size:12px}
.fv-data p{margin:8px 0}
.fv-notice{padding:8px 12px;font-size:12px;border-bottom:1px solid var(--fv-line);white-space:pre-wrap}
.fv-path{padding:0 12px;border-bottom:1px solid var(--fv-line);font-size:12px}
.fv-path form{display:flex;flex-wrap:wrap;gap:8px;padding-bottom:12px}
.fv-path label{flex:1;min-width:160px;flex-wrap:wrap}
.fv-path input{width:100%}
.fv-output{margin-top:10px}
.fv-output video{display:block;width:100%;max-height:420px;background:#080a0c}
.fv-output p{font-size:12px;color:var(--fv-muted);margin:8px 0}
.fv-guide{display:flex;width:100%;align-items:center;gap:12px;padding:12px 16px;min-height:48px;border:1px solid #41454c;background:#24262c;border-radius:12px;text-align:left;transition:background-color .15s}
.fv-guide:hover{background:#333740}
.fv-guide svg{color:var(--fv-accent)}
.fv-guide strong{font-size:14px;font-weight:500}
.fv-guide small{font-size:11px;color:var(--fv-muted);margin-left:auto}
@container fv-workbench (min-width:860px){
 .fv-upper{display:grid;grid-template-columns:var(--fv-files-column,0px) minmax(0,1fr) var(--fv-inspector-column,0px);align-items:stretch}
 .fv-files{grid-column:1;border:0;border-right:1px solid var(--fv-line);max-height:none}
 .fv-monitor{grid-column:2;padding:16px 24px;overflow:auto}
 .fv-inspector{grid-column:3;border:0;border-left:1px solid var(--fv-line);max-height:none}
 .fv-topbar{padding:12px 20px}
 .fv-workbench[open] .fv-body{display:flex;flex-direction:column;overflow:hidden}
 .fv-workbench[open] .fv-upper{flex:1;min-height:0;overflow:hidden}
 .fv-workbench[open] .fv-monitor{padding:10px 16px}
 .fv-workbench[open] .fv-frame-query{position:sticky;top:-10px;z-index:4;background:#141619;padding:6px 0;font-size:12px}
 .fv-workbench[open] .fv-frame-query button{font-size:12px}
 .fv-workbench[open] .fv-frame-tools-head{display:none}
 .fv-workbench[open] .fv-frame-tools-stage{max-height:20dvh}
 .fv-workbench[open] .fv-timeline-pane{display:flex;flex-direction:column;flex:0 0 42%;min-height:300px;overflow:hidden}
 .fv-workbench[open] .fv-timeline{display:flex;flex-direction:column;flex:1;min-height:0}
 .fv-workbench[open] .fv-timeline .fv-scroll{flex:1;min-height:70px;max-height:none}
 .fv-workbench[open] .fv-timeline .fv-help summary,.fv-workbench[open] .fv-data>summary{padding:3px 0;min-height:24px}
 .fv-workbench[open] .fv-data{flex:none}
 .fv-workbench[open] .fv-data[open]{max-height:45%;overflow:auto}
}
@container fv-workbench (max-width:420px){.fv-topbar{gap:7px;padding:10px}.fv-subtitle{display:none}.fv-topbar .fv-open-label{display:none}.fv-monitor{padding:10px}.fv-empty{min-height:200px}.fv-timeline-pane{padding:10px}.fv-frame-query label{flex-basis:100%}}
@media(prefers-reduced-motion:reduce){.fv-workbench *,.fv-guide{transition:none!important}}
`
export function WorkbenchStyles() { return h('style', null, workbenchCSS) }

/** A Host-bound file picker, not a browser upload or a second timeline parser. */
export function FileBrowser({ sessionId, tabSignal, call, onChoose, selectedPath, onClose }) {
  const [state, setState] = useState({ path: '', phase: 'loading', entries: [] })
  const [search, setSearch] = useState('')
  const request = useRef()
  async function browse(path) {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    const signal = AbortSignal.any([controller.signal, tabSignal])
    setSearch('')
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
      if (!signal.aborted) setState(previous => ({ ...previous, phase: 'failed', entries: [], message: error.message }))
    }
  }
  useEffect(() => { void browse(''); return () => request.current?.abort() }, [sessionId, tabSignal])
  const entries = state.entries.filter(entry => entry.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()))
  const parent = state.path.split('/').slice(0, -1).join('/')
  return h('aside', { className: 'fv-files', 'aria-label': '工作区文件选择器' },
    h('div', { className: 'fv-panel-heading' }, h('h3', null, '项目文件'),
      h('div', null,
        h('button', { type: 'button', title: '刷新文件列表', 'aria-label': '刷新文件列表', onClick: () => void browse(state.path), disabled: state.phase === 'loading' }, h(Icon, { name: 'refresh', size: 15 })),
        h('button', { type: 'button', title: '收起项目文件', 'aria-label': '收起项目文件', onClick: onClose }, h(Icon, { name: 'close', size: 15 })))),
    h('div', { className: 'fv-file-location' },
      h('button', { type: 'button', title: '上一级文件夹', 'aria-label': '上一级文件夹', disabled: !state.path || state.phase === 'loading', onClick: () => void browse(parent) }, h(Icon, { name: 'back', size: 14 })),
      h('span', { title: state.path || '当前会话工作区' }, state.path || '当前会话工作区')),
    h('input', { className: 'fv-file-search', type: 'search', value: search, 'aria-label': '筛选当前文件夹', name: 'framivra-file-filter', autoComplete: 'off', placeholder: '筛选文件…', onChange: event => setSearch(event.target.value) }),
    state.phase === 'loading' ? h('p', { className: 'fv-file-status', role: 'status' }, '正在读取文件夹…') : null,
    state.phase === 'failed' ? h('div', { className: 'fv-file-status', role: 'alert' }, state.message,
      h('button', { type: 'button', onClick: () => void browse(state.path) }, '重试读取文件夹')) : null,
    state.phase === 'ready' ? h(React.Fragment, null,
      h('ul', { className: 'fv-file-list', 'aria-label': '文件与文件夹' }, entries.map(entry => h('li', { key: entry.path, style: { contentVisibility: 'auto', containIntrinsicSize: '38px' } },
        h('button', { type: 'button', title: entry.name, 'aria-label': `${entry.type === 'directory' ? '打开文件夹' : '打开时间线'} ${entry.name}`, 'aria-current': selectedPath === entry.path ? true : undefined,
          onClick: () => { if (entry.type === 'directory') void browse(entry.path); else onChoose(entry.path) } }, h(Icon, { name: entry.type === 'directory' ? 'folder' : 'file', size: 17 }), h('span', null, entry.name))))),
      !entries.length ? h('p', { className: 'fv-file-status', role: 'status' }, search ? '没有匹配的文件。' : '此文件夹没有 JSON 文件或子文件夹。') : null,
      state.truncated ? h('p', { className: 'fv-file-status', role: 'status' }, `目录过大，仅显示前 ${state.limit} 项。请进入子文件夹。`) : null,
      state.escaped ? h('p', { className: 'fv-file-status' }, '已隐藏指向工作区外的链接。') : null) : null,
    h('details', null, h('summary', null, '选择范围'), h('p', { className: 'fv-file-status' }, '仅浏览当前会话工作区，不上传文件。JSON 是否为有效时间线由 vtx 核验；外部工程请先切换会话工作区。')))
}
