"""Pure audio filter fragments shared by the render graph and diagnostic stems.

Event-local transforms remain separate from track padding/mixing and duck-key
selection. No source resolution, plugin transforms, loudness gain, limiting,
or output orchestration belongs here: consumers retain their own graphs.
"""


def duck_track_indices(tracks, present):
    """Return active ducks and exact key-index groups mapped to their targets.

    The render graph supplies window-filtered indices; diagnostic stems supply
    full-length active indices. Empty runtime tracks never contribute a key;
    targets without a non-duck key stay audible but do not enter a key group.
    """
    duck = [ti for ti in present if tracks[ti].get("duck")]
    groups = {}
    for ti in duck:
        names = tracks[ti].get("duck_by", ["voice"])
        keys = tuple(ki for ki in present if ki not in duck and tracks[ki]["name"] in names)
        if keys:
            groups.setdefault(keys, []).append(ti)
    return duck, groups


def key_group_chains(labels, groups):
    """Build each distinct key mix once, preserving every original audible label.

    Outputs [keygN] follow groups' iteration order. Consumers retain their own
    float bus / PCM stem boundaries and fan each group out only to its targets.
    """
    chains = []
    for ti, label in labels.items():
        uses = [gi for gi, keys in enumerate(groups) if ti in keys]
        if uses:
            # 修复（2026-09-10，voice-ime BGM v2 轮定位）：asplit=恰 N 路把 sidechain 轨
            # （voice）整轨吞尽，终混仍引用原标签→该 ffmpeg 版本静默解析为空流=口播
            # 整轨缺失（复现：最小三轨 bed+voice+duck bgm）。多分一路并重绑原轨标签，
            # 终混引用恢复有效。
            chains.append(f"{label}asplit={len(uses) + 1}" +
                          "".join(f"[key{gi}t{ti}]" for gi in uses) + label)
    for gi, keys in enumerate(groups):
        # 同族修复：多 sidechain 轨先各自 asplit 保一路原标签再 amix 做 key，
        # 否则 amix 消费原标签后终混同样丢这些轨。
        inputs = "".join(f"[key{gi}t{ti}]" for ti in keys)
        mix = "anull" if len(keys) == 1 else f"amix=inputs={len(keys)}:duration=longest:normalize=0"
        chains.append(f"{inputs}{mix}[keyg{gi}]")
    return chains


def track_mix(c, labels, out):
    """Mix nonempty event labels and pad one track on the shared sample grid."""
    head = (labels[0] if len(labels) == 1 else
            f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:normalize=0,")
    return (f"{head}apad=whole_len={c.dur*c.spf_samples},"
            f"atrim=end_sample={c.dur*c.spf_samples},asetpts=PTS-STARTPTS{out}")


def duck_chains(c, signal, key, out, *, threshold, ratio, attack, release, makeup):
    """Keep private compressor inputs alive through the exact project boundary.

    Call after fanout so padding cannot extend an audible/raw branch. The trim
    immediately after compression bounds both otherwise unbounded input pads.
    """
    padded_signal = f"{out[:-1]}_signal]"
    padded_key = f"{out[:-1]}_key]"
    return [f"{signal}apad{padded_signal}", f"{key}apad{padded_key}",
            f"{padded_signal}{padded_key}sidechaincompress=threshold={threshold}:ratio={ratio}:"
            f"attack={attack}:release={release}:makeup={makeup},"
            f"atrim=end_sample={c.dur*c.spf_samples}{out}"]


def event_chain(c, k, e, *, require_volume=True):
    """Build one validated frame-grid event, clamping fades to its duration."""
    dur = e["out"] - e["in"]; sin = e.get("src_in", 0)
    # fade 钳制 ≤ 事件时长（D-A 2026-10-05：fade>时长 → afade st<0 报错；
    # duck-static 插件已在上游切段时钳制，此处兜底手写 timeline 的同类错）
    fi = min(e["fade_in_frames"], dur) if e.get("fade_in_frames") else 0
    fo = min(e["fade_out_frames"], dur) if e.get("fade_out_frames") else 0
    fl = [f"atrim=start_sample={sin*c.spf_samples}:end_sample={(sin+dur)*c.spf_samples}", "asetpts=PTS-STARTPTS"]
    volume = e["volume"] if require_volume else e.get("volume", 1)
    fl.append(f"volume={volume}")
    if e.get("pan"):
        p = e["pan"]; lg = min(1.5, 1 - p * .5); rg = min(1.5, 1 + p * .5)
        fl.append(f"pan=stereo|c0={lg:.3f}*c0|c1={rg:.3f}*c1")
    if fi: fl.append(f"afade=t=in:st=0:d={fi*c.spf:.6f}")
    if fo:
        fl.append(f"afade=t=out:st={(dur-fo)*c.spf:.6f}:d={fo*c.spf:.6f}")
    fl.append(f"adelay=delays={e['in']/c.fps*1000:.3f}:all=1")
    return f"[{k}:a]" + ",".join(fl) + f"[e{k}]"
