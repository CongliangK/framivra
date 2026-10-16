// Host-only structured exception monitoring. No preload, page globals, console
// parsing, or Node/IPC bridge. Arm the blank target BEFORE navigating to HTML.
export const PAGE_ERROR_GATE = 'cdp-runtime-exceptions/1';

export async function installPageErrorGate(wc, errors, fail) {
  const debug = wc.debugger;
  const sessions = new Set(['']);
  const pending = new Set();
  let closed = false;
  const send = (method, params = {}, id = '') => debug.sendCommand(method, params, id || undefined);
  const broken = reason => fail(`page_error_gate:${reason}`);
  const arm = async id => {
    await send('Runtime.enable', {}, id);
    // Pause child targets at creation: their first script must not run before
    // Runtime is enabled. Recursive attachment includes OOPIFs and workers.
    await send('Target.setAutoAttach', {autoAttach:true, waitForDebuggerOnStart:true, flatten:true}, id);
    if (id) await send('Runtime.runIfWaitingForDebugger', {}, id);
  };
  const onMessage = (_event, method, params, sessionId = '') => {
    if (closed) return;
    if (method === 'Runtime.exceptionThrown') {
      const d = params.exceptionDetails || {};
      // Keep bounded, structured evidence, including primitive rejections and
      // genuine SyntaxError details. exceptionRevoked NEVER clears a failure.
      const record = {event:method, session_id:sessionId, exception_id:d.exceptionId,
        text:String(d.text || '').slice(0,1000), url:String(d.url || '').slice(0,2000),
        line:d.lineNumber, column:d.columnNumber, execution_context_id:d.executionContextId,
        exception_type:d.exception?.className || d.exception?.type,
        description:String(d.exception?.description ?? d.exception?.value ?? d.text ?? '').slice(0,4000),
        stack:d.stackTrace?.callFrames?.slice(0,16)};
      if (errors.length < 32) errors.push(record);
      fail(`page_runtime_error:${record.description || record.text}`);
    } else if (method === 'Target.attachedToTarget') {
      const id = params.sessionId;
      sessions.add(id);
      const task = arm(id).catch(e => broken(`child_monitor:${e.message}`));
      pending.add(task);
      task.finally(() => pending.delete(task));
    } else if (method === 'Target.detachedFromTarget') {
      sessions.delete(params.sessionId);
    } else if (method === 'Inspector.detached' || method === 'Inspector.targetCrashed') {
      broken(method);
    }
  };
  const onDetach = (_event, reason) => { if (!closed) broken(`detached:${reason}`); };
  debug.on('message', onMessage);
  debug.on('detach', onDetach);
  const close = () => {
    closed = true;
    debug.removeListener('message', onMessage);
    debug.removeListener('detach', onDetach);
    // The owner immediately destroys the WebContents, including all targets.
  };
  try {
    debug.attach('1.3');
    await arm('');
  } catch (e) {
    close();
    throw Error(`page_error_gate:install:${e.message}`);
  }
  return {
    close,
    async fence() {
      // An ordered protocol roundtrip drains earlier exception notifications.
      // The caller first yields a renderer task/microtask checkpoint in its
      // isolated world, so promise rejections during capture are reportable.
      while (pending.size) await Promise.all([...pending]);
      for (const id of [...sessions]) {
        if (sessions.has(id)) await send('Runtime.getIsolateId', {}, id);
      }
      if (closed || !debug.isAttached()) throw Error('page_error_gate:not_attached');
    },
  };
}
