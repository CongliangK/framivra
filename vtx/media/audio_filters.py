"""Event-local audio filters shared by the render graph and diagnostic stems.

No source resolution, plugin transforms, track mixing, loudness gain, limiting,
or output padding belongs here: consumers retain their own graph orchestration.
"""


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
