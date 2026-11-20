// A consumer of a single explicit background run. No worker requests, persistent
// storage or independent media clock: decoding and cursor publication stay with
// WindowMonitor. Pausing abandons only a pending foreground read, not the run.
export function connectProgramPlayback(monitor, owner, binding, run, frame) {
  const { signal, read } = run
  let pending, closed = false
  const live = () => !closed && !signal.aborted && !binding.tabSignal.aborted && monitor.program === player
  const current = () => monitor.state.active?.program === player ? monitor.state.active : undefined
  function discard() {
    pending?.abort(); pending = undefined
    monitor.playOperation = undefined
    if (current()) monitor.dropMedia({})
  }
  async function locate(target, play) {
    discard()
    if (!live() || !Number.isSafeInteger(target) || target < 0 || target >= binding.duration) return
    const controller = pending = Object.assign(new AbortController(), { wantPlay: play, target })
    monitor.update({ message: '正在准备播放头位置的音画…' })
    try {
      const item = await read(target, controller.signal)
      if (!live() || controller.signal.aborted || pending !== controller) return
      if (item.contentVersion !== binding.contentVersion || !Number.isSafeInteger(item.start) || !Number.isSafeInteger(item.end) ||
          item.start < 0 || item.start > target || target >= item.end || item.end > binding.duration || item.coverageEnd !== item.end ||
          !/^sha256:[a-f0-9]{64}$/.test(item.inputEpoch) || item.blob?.type !== 'video/mp4') throw new Error('Invalid program window')
      const media = { ...binding, program: player, operationId: crypto.randomUUID(), frame: item.start, end: item.end, cursor: target,
        inputEpoch: item.inputEpoch, awaitPlayable: true, initialPin: target !== item.start ? { frame: target } : undefined,
        url: URL.createObjectURL(item.blob) }
      media.decodeTimer = setTimeout(() => { if (monitor.owns(media)) void monitor.cancel('浏览器未及时解码后台窗口。') }, 15_000)
      if (controller.wantPlay) monitor.playOperation = media.operationId
      monitor.update({ active: media, message: '正在解码后台窗口…' })
    } catch {
      if (live() && !controller.signal.aborted && pending === controller) monitor.update({ message: '该位置没有可播放的后台窗口。' })
    } finally { if (pending === controller) pending = undefined }
  }
  const player = {
    live, get wantsPlayback() { return !!pending?.wantPlay },
    invalidate(pin) { if (pin) void locate(pin.frame, false); else discard() },
    ended(media) {
      if (!live() || !monitor.owns(media) || media.end >= binding.duration) return
      void locate(media.end, true)
    },
  }
  const toggle = target => {
    if (!live()) return false
    if (pending) {
      if (!pending.wantPlay) { pending.wantPlay = true; monitor.refresh(); return true }
      discard(); monitor.update({ message: '播放已暂停；不停止后台渲染。' }); return true
    }
    // Already decoded (or decoding): delegate pause/play to the same native
    // transport as ordinary windows. Do not issue a second Host render.
    if (current()) return false
    void locate(target, true); return true
  }
  function stop() {
    if (closed) return
    closed = true
    discard()
    if (monitor.program === player) monitor.program = undefined
    if (owner.programTransport === toggle) owner.programTransport = undefined
    signal.removeEventListener('abort', stop); binding.tabSignal.removeEventListener('abort', stop)
  }
  if (signal.aborted || binding.tabSignal.aborted || monitor.state.job || monitor.state.active) return
  monitor.program = player; owner.programTransport = toggle
  signal.addEventListener('abort', stop, { once: true }); binding.tabSignal.addEventListener('abort', stop, { once: true })
  void locate(frame, true)
  return stop
}
