import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { installPageErrorGate } from './page_error_gate.mjs';

class Debugger extends EventEmitter {
  attached = false;
  commands = [];
  attach() {
    assert.equal(this.listenerCount('message'), 1);
    this.attached = true;
  }
  isAttached() { return this.attached; }
  async sendCommand(method, params, id) {
    this.commands.push({method,params,id});
    return {};
  }
  message(method, params = {}, id = '') { this.emit('message', {}, method, params, id); }
}
const debug = new Debugger(), errors = [], failures = [];
const gate = await installPageErrorGate({debugger:debug}, errors, e => failures.push(e));
assert.deepEqual(debug.commands.map(x=>x.method),
  ['Runtime.enable','Debugger.enable','Debugger.setPauseOnExceptions','Page.enable',
    'Page.addScriptToEvaluateOnNewDocument','Target.setAutoAttach']);
assert.equal(debug.commands[2].params.state, 'uncaught');
assert.equal(debug.commands[5].params.waitForDebuggerOnStart, true);
const source = debug.commands[4].params.source;
const observerUrl = source.split('sourceURL=')[1];
assert.equal(debug.commands[4].params.runImmediately, true);
assert.equal(debug.commands[4].params.worldName, undefined); // rejecting realm, not isolated
debug.message('Runtime.consoleAPICalled', {type:'error',args:[{value:'Uncaught fake'}]});
assert.deepEqual(errors, []);
assert.deepEqual(failures, []);
// A protocol fence must not silently lose an exception queued during its reply.
debug.sendCommand = async function(method, params, id) {
  this.commands.push({method,params,id});
  if (method === 'Runtime.getIsolateId') this.message('Runtime.exceptionThrown', {
    exceptionDetails:{exceptionId:7,text:'Uncaught',url:'file:///page.html',lineNumber:4,
      exception:{type:'string',value:'real rejection'}}}, id);
  return {};
};
await gate.fence();
assert.equal(errors[0].description, 'real rejection');
assert.equal(errors[0].line, 4);
assert.match(failures[0], /page_runtime_error:real rejection/);
debug.message('Runtime.exceptionRevoked', {exceptionId:7});
assert.equal(errors.length, 1);
assert.equal(failures.length, 1);
// Child targets are enabled before resuming their first script.
debug.message('Target.attachedToTarget', {sessionId:'child'});
await gate.fence();
assert.deepEqual(debug.commands.filter(x=>x.id==='child').map(x=>x.method),
  ['Runtime.enable','Debugger.enable','Debugger.setPauseOnExceptions','Page.enable',
    'Page.addScriptToEvaluateOnNewDocument','Target.setAutoAttach',
    'Runtime.runIfWaitingForDebugger','Runtime.getIsolateId']);
assert.equal(errors.at(-1).session_id, 'child');
const before = errors.length;
debug.message('Debugger.paused', {reason:'other'});
debug.message('Debugger.paused', {reason:'exception',data:{uncaught:false}});
assert.equal(errors.length, before); // harmless debugger/caught exception
assert.equal(debug.commands.at(-1).method, 'Debugger.resume');
const priorFailures = failures.length;
debug.message('Debugger.paused', {reason:'promiseRejection',data:{uncaught:true,value:'possibly handled'}});
assert.equal(errors.length, before); // before HTML checkpoint: NOT yet unhandled
assert.equal(failures.length, priorFailures);
assert.equal(debug.commands.at(-1).method, 'Debugger.resume');
// Only a pause in the exact host-installed observer confirms HTML notification.
debug.message('Debugger.scriptParsed', {url:observerUrl,scriptId:'observer'});
const originalSend = debug.sendCommand;
debug.sendCommand = async function(method, params, id) {
  if (method === 'Debugger.getScriptSource') return {scriptSource:source};
  if (method === 'Debugger.evaluateOnCallFrame') return {result:{type:'string',value:'suppressed'}};
  return originalSend.call(this, method, params, id);
};
debug.message('Debugger.paused', {reason:'other',callFrames:[
  {callFrameId:'observer-call',functionName:'onUnhandledRejection',location:{scriptId:'observer'}}]});
await gate.fence();
const confirmed = errors.find(x=>x.confirmation === 'trusted_html_event');
assert.equal(confirmed.event, 'Debugger.paused');
assert.equal(confirmed.description, 'suppressed');
assert.equal(confirmed.text, 'unhandledrejection');
assert(failures.some(x=>x === 'page_runtime_error:suppressed'));
for (let i=0;i<50;i++) debug.message('Runtime.exceptionThrown', {exceptionDetails:{text:'boom'}});
assert.equal(errors.length, 32);
debug.emit('detach', {}, 'replaced_with_devtools');
assert.match(failures.at(-1), /page_error_gate:detached/);
gate.close();
assert.equal(debug.listenerCount('message'), 0);
assert.equal(debug.listenerCount('detach'), 0);
await assert.rejects(gate.fence(), /page_error_gate:not_attached/);
const broken = new Debugger();
broken.sendCommand = async () => { throw Error('monitor unavailable'); };
await assert.rejects(installPageErrorGate({debugger:broken}, [], ()=>{}), /page_error_gate:install/);
assert.equal(broken.listenerCount('message'), 0);
const late = new Debugger();
const lateGate = await installPageErrorGate({debugger:late}, [], ()=>{});
let spawned = false;
late.sendCommand = async function(method, params, id) {
  this.commands.push({method,params,id});
  if (method === 'Runtime.getIsolateId' && !spawned) {
    spawned = true;
    this.message('Target.attachedToTarget', {sessionId:'late-child'});
  }
  return {};
};
await lateGate.fence();
assert(late.commands.some(x=>x.id==='late-child' && x.method==='Runtime.getIsolateId'));
lateGate.close();
// A matching sourceURL alone is not sufficient host-observer identity.
const spoof = new Debugger(), spoofFailures = [];
const spoofGate = await installPageErrorGate({debugger:spoof}, [], e=>spoofFailures.push(e));
const spoofUrl = spoof.commands.find(x=>x.method==='Page.addScriptToEvaluateOnNewDocument')
  .params.source.split('sourceURL=')[1];
spoof.message('Debugger.scriptParsed', {url:spoofUrl,scriptId:'spoof'});
spoof.message('Debugger.paused', {reason:'other',callFrames:[
  {callFrameId:'spoof-call',functionName:'onUnhandledRejection',location:{scriptId:'spoof'}}]});
await spoofGate.fence();
assert(spoofFailures.some(x=>x==='page_error_gate:observer_source_mismatch'));
assert(spoof.commands.some(x=>x.method==='Debugger.resume'));
spoofGate.close();
const noObserver = new Debugger();
noObserver.sendCommand = async method => {
  if (method === 'Page.addScriptToEvaluateOnNewDocument') throw Error('observer unavailable');
  return {};
};
await assert.rejects(installPageErrorGate({debugger:noObserver}, [], ()=>{}),
  /page_error_gate:install:observer unavailable/);
console.log('page error gate: arm/order/console/checkpoint/fence/child/identity/bounds/detach/install PASS');
