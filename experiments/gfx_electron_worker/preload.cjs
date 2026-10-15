// preload.cjs — vtx GFX 路线 B：gfx_electron_worker 隔离世界 preload（issue #112）。
//
// 运行形态（与 main.mjs webPreferences 一一对应，工单口径）：
//   sandbox: true（preload 只拿得到受限于 electron 子集的 require）、
//   contextIsolation: true（本文件与页面共享的只有 window 对象本身）、
//   nodeIntegration: false（页面世界零 Node 能力）。
//
// 职责保持最小：页面 load 后经 ipcRenderer 向主进程回传一份「页面自证」——
// 页面真实看到的 f 查询参数与 devicePixelRatio（后者供主进程核对
// setDeviceScaleFactor(1) 是否真的在页面世界生效，进入 truth.json 证据链）。
// 不注入任何 API 到页面世界（contextBridge 未使用）：gfx 页协议本就要求
// 纯 Web 世界，注入反而扩大攻击面。
const { ipcRenderer } = require('electron');

window.addEventListener('load', () => {
  ipcRenderer.send('vtx:page-loaded', {
    f: new URLSearchParams(window.location.search).get('f'),
    devicePixelRatio: window.devicePixelRatio,
    userAgent: navigator.userAgent,
  });
});
