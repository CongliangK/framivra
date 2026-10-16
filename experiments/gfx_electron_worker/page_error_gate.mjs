// Host-owned structured monitoring. No preload, page globals, console parsing,
// or Node/IPC bridge. Arm the blank target BEFORE navigating to HTML.
import { randomUUID } from 'node:crypto';
export const PAGE_ERROR_GATE = 'cdp-html-unhandled-exceptions/2';

export async function installPageErrorGate(wc, errors, fail) {
  const debug = wc.debugger;
  const sessions = new Set(['']);
  const pending = new Set();
  const observerScripts = new Map();
  const observerUrl = `vtx-host-unhandled-${randomUUID()}.js`;
  // V8's promiseRejection/uncaught flag is provisional: catch handlers can still
  // be installed by the current task or its microtasks. Observe HTML's actual
  // notification instead, BEFORE page listeners can preventDefault/stopImmediate-
  // Propagation. A closure-only listener adds no page API or host-call binding,
  // and does not mark any Promise handled. Synthetic events cannot reject frames.
  // This must run in the rejecting realm (isolated worlds do not receive it).
  const observerSource = `(() => {
    addEventListener('unhandledrejection', function(event) {
      if (event.isTrusted) { debugger; }
    }, true);
  })();\n//# sourceURL=${observerUrl}`;
  let closed = false, targetEpoch = 0;
  const send = (method, params = {}, id = '') => debug.sendCommand(method, params, id || undefined);
  const broken = reason => fail(`page_error_gate:${reason}`);
  const track = task => {
    const guarded = task.catch(e => broken(e.message));
    pending.add(guarded);
    guarded.finally(() => pending.delete(guarded));
  };
  const recordFailure = record => {
    if (errors.length < 32) errors.push(record);
    fail(`page_runtime_error:${record.description || record.text}`);
  };
  const arm = async (id, type = 'page') => {
    observerScripts.set(id, new Set());
    await send('Runtime.enable', {}, id);
    await send('Debugger.enable', {}, id);
    await send('Debugger.setPauseOnExceptions', {state:'uncaught'}, id);
    if (['page','iframe'].includes(type)) {
      await send('Page.enable', {}, id);
      await send('Page.addScriptToEvaluateOnNewDocument',
        {source:observerSource, runImmediately:true}, id);
    } else if (['worker','shared_worker','service_worker'].includes(type)) {
      const r = await send('Runtime.evaluate', {expression:observerSource}, id);
      if (r.exceptionDetails) throw Error('worker_observer_install');
    } else {
      throw Error(`unsupported_target:${type}`);
    }
    // Children must not run their first script before their observer is armed.
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
      recordFailure(record);
    } else if (method === 'Debugger.scriptParsed' && params.url === observerUrl) {
      observerScripts.get(sessionId)?.add(params.scriptId);
    } else if (method === 'Debugger.paused') {
      const d = params.data || {}, top = params.callFrames?.[0];
      const stack = params.callFrames?.slice(0,16).map(f => ({functionName:f.functionName,
        url:f.url, lineNumber:f.location?.lineNumber, columnNumber:f.location?.columnNumber}));
      if (params.reason === 'exception' && d.uncaught === true) {
        recordFailure({event:method, session_id:sessionId, text:params.reason,
          exception_type:d.className || d.type,
          description:String(d.description ?? d.value ?? params.reason).slice(0,4000), stack});
      }
      track((async () => {
        try {
          if (observerScripts.get(sessionId)?.has(top?.location?.scriptId)) {
            // Do not trust a page sourceURL/debugger statement as confirmation.
            const source = await send('Debugger.getScriptSource', {scriptId:top.location.scriptId}, sessionId);
            if (source.scriptSource !== observerSource) throw Error('observer_source_mismatch');
            const r = await send('Debugger.evaluateOnCallFrame',
              {callFrameId:top.callFrameId, expression:'event.reason', silent:true}, sessionId);
            if (r.exceptionDetails) throw Error('observer_reason_unavailable');
            const reason = r.result || {};
            recordFailure({event:method, session_id:sessionId, text:'unhandledrejection',
              confirmation:'trusted_html_event', exception_type:reason.className || reason.type,
              description:String(reason.description ?? reason.value ?? 'unhandledrejection').slice(0,4000), stack});
          }
          // promiseRejection alone is NOT a failure; only a real HTML event or
          // Runtime.exceptionThrown after the checkpoint establishes unhandled.
        } finally {
          // Also resume harmless page `debugger` statements and caught promises.
          await send('Debugger.resume', {}, sessionId);
        }
      })());
    } else if (method === 'Target.attachedToTarget') {
      const id = params.sessionId;
      sessions.add(id); targetEpoch++;
      track(arm(id, params.targetInfo?.type));
    } else if (method === 'Target.detachedFromTarget') {
      sessions.delete(params.sessionId); observerScripts.delete(params.sessionId); targetEpoch++;
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
      if (closed || !debug.isAttached()) throw Error('page_error_gate:not_attached');
      // An ordered protocol roundtrip drains earlier exception notifications.
      // The caller first yields a renderer task/microtask checkpoint in its
      // isolated world, so promise rejections during capture are reportable.
      let epoch;
      do {
        epoch = targetEpoch;
        while (pending.size) await Promise.all([...pending]);
        for (const id of [...sessions]) {
          if (sessions.has(id)) await send('Runtime.getIsolateId', {}, id);
        }
        // A child can attach during a protocol reply: fence that child's armed
        // Runtime too, rather than accepting a still-paused/unfenced target.
      } while (pending.size || epoch !== targetEpoch);
      if (closed || !debug.isAttached()) throw Error('page_error_gate:not_attached');
    },
  };
}
