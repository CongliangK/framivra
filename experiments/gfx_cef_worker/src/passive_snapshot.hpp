// Native OnPaint snapshot demand; independent of CEF for executable tests.
#ifndef VTX_CEF_PASSIVE_SNAPSHOT_HPP_
#define VTX_CEF_PASSIVE_SNAPSHOT_HPP_
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <limits>

namespace vtx_cef {
// Caller owns state.mu. Clear old bytes whenever demand changes: a later native
// paint sequence must never lend freshness to a snapshot from before its floor.
template <class State>
int SetPassiveSnapshotDemandLocked(State& state, bool enabled) {
  state.passive_snapshot_enabled = enabled;
  state.buffer.clear();
  state.width = state.height = 0;
  return state.paints.load(std::memory_order_relaxed);
}

// Caller owns state.mu. This is the fallback path only: signed phase candidates
// and legacy capture have their own gates. Even discarded PET_VIEW paints keep
// their actual native sequence; this function never supplies proof or accepts.
template <class State>
void CapturePassiveSnapshotLocked(State& state, const uint8_t* src,
                                  int width, int height, bool signed_idle) {
  const bool geometry = width > 0 && height > 0 &&
      static_cast<size_t>(width) <= std::numeric_limits<size_t>::max() / 4u /
                                   static_cast<size_t>(height);
  if (!signed_idle && state.passive_snapshot_enabled && src && geometry) {
    const size_t bytes = static_cast<size_t>(width) * height * 4u;
    state.buffer.assign(src, src + bytes);
    state.width = width;
    state.height = height;
    ++state.passive_snapshot_copied_paints;
    state.passive_snapshot_copied_bytes += bytes;
  } else {
    // Also prevent stale bytes if an unarmed signed browser retained a legacy
    // snapshot before sync_machine was attached. Capacity remains reusable.
    state.buffer.clear();
    state.width = state.height = 0;
    ++state.passive_snapshot_dropped_paints;
  }
  state.paints.fetch_add(1, std::memory_order_relaxed);
}
}  // namespace vtx_cef
#endif
