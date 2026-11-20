// A consumer of a single explicit background run. No worker requests, persistent
// storage or independent media clock: decoding and cursor publication stay with
// WindowMonitor. There is one read intent, foreground or adjacent spare; neither
// pausing nor replacing it stops the separately authorized background worker.
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
  async function load(target, play, previous) {
    const controller = pending = Object.assign(new AbortController(), { wantPlay: play, previous })
    const ownsRead = () => live() && pending === controller && !controller.signal.aborted &&
      (!previous || (monitor.owns(previous) && previous.playIntent))
    try {
      const item = await read(target, controller.signal)
      if (!ownsRead()) return
      if (item.contentVersion !== binding.contentVersion || !Number.isSafeInteger(item.start) || !Number.isSafeInteger(item.end) ||
          item.start < 0 || item.start > target || target >= item.end || item.end > binding.duration || item.coverageEnd !== item.end ||
          !/^sha256:[a-f0-9]{64}$/.test(item.inputEpoch) || item.blob?.type !== 'video/mp4' ||
          (previous && (item.start !== previous.end || item.inputEpoch !== previous.inputEpoch))) throw new Error('Invalid program window')
      const media = { ...binding, program: player, operationId: crypto.randomUUID(), frame: item.start, end: item.end, cursor: target,
        inputEpoch: item.inputEpoch, from: previous?.operationId, awaitPlayable: true,
        initialPin: target !== item.start ? { frame: target } : undefined, url: URL.createObjectURL(item.blob) }
      media.decodeTimer = setTimeout(() => monitor.rejectMedia(media, '浏览器未及时解码后台窗口。'), 15_000)
      if (controller.wantPlay) monitor.playOperation = media.operationId
      if (previous) monitor.update({ ...monitor.state, spare: media })
      else monitor.update({ active: media, message: '正在解码后台窗口…' })
    } catch {
      if (!ownsRead()) return
      if (previous) {
        previous.successorFailed = true
        monitor.advance()
      } else monitor.update({ message: '该位置没有可播放的后台窗口。' })
    } finally { if (pending === controller) pending = undefined }
  }
  function locate(target, play) {
    discard()
    if (!live() || !Number.isSafeInteger(target) || target < 0 || target >= binding.duration) return
    monitor.update({ message: '正在准备播放头位置的音画…' })
    void load(target, play)
  }
  const player = {
    live, get wantsPlayback() { return !!pending?.wantPlay },
    invalidate(pin) { if (pin) locate(pin.frame, false); else discard() },
    prefetch(previous) {
      if (!pending) void load(previous.end, false, previous)
    },
    abortPrefetch(media) {
      if (pending?.previous === media) { pending.abort(); pending = undefined }
    },
    pause(media) {
      player.abortPrefetch(media)
      const spare = monitor.state.spare
      if (spare?.program === player && spare.from === media.operationId) monitor.dropSpare(spare)
    },
  }
  const toggle = target => {
    if (!live()) return false
    if (pending && !pending.previous) {
      if (!pending.wantPlay) { pending.wantPlay = true; monitor.refresh(); return true }
      discard(); monitor.update({ message: '播放已暂停；不停止后台渲染。' }); return true
    }
    const media = current()
    if (media?.atEnd && media.node?.ended && !media.waiting && media.end < binding.duration) {
      // An explicit resume at a paused boundary targets the next frame; it
      // must not replay the preceding second or move the canonical cursor.
      media.playIntent = true
      media.successorFailed = false
      monitor.update({ ...monitor.state, message: undefined })
      monitor.advance()
      return true
    }
    // Already decoded (or decoding): delegate pause/play to the same native
    // transport as ordinary windows. Do not issue a second Host render.
    if (media) return false
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
