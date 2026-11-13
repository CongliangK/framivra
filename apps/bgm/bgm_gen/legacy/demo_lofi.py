#!/usr/bin/env python3
"""demo_lofi — 一首 12 小节 lo-fi（78 BPM，D 小调），整首"乐谱"就是下面的明文数据。

与 ACE 生成的 ace15_lofi_test（同 78 BPM、D 小调请求）形成对照：
AI 波形生成 = 参数是"强引导"；这里 = 参数即事实（BPM/调性由代码直接决定）。
"""
import numpy as np
from bgm_gen.synthlib import (SR, Track, hz, kick, snare, hat, fm_ep, bass, lead,
                               reverb, vinyl, mixdown)

BPM, BEAT = 78.0, 60.0 / 78.0
BAR = 4 * BEAT
BARS = 12
DUR = BARS * BAR + 1.5

# ── 和声：D 小调 i–VI–IV–V（每和弦一小节，循环三轮）────────────────
PROG = [
    ("Dm9",   ["D3", "F3", "A3", "C4", "E4"], "D2"),
    ("Bbmaj7", ["Bb2", "D3", "F3", "A3"],     "Bb1"),
    ("Gm7",   ["G2", "Bb2", "D3", "F3"],      "G2"),
    ("A7",    ["A2", "E3", "G3", "C#4"],      "A1"),
]
CHORD_HITS = [(0.0, 2.0, 0.85), (2.5, 1.2, 0.60)]          # (拍, 时值, 力度)

# ── 鼓：16 步网格（0-15），swing 55% ────────────────────────────────
KICK_STEPS = [0, 10]
SNARE_STEPS = [4, 12]
SWING = 0.05 * BEAT                                          # 奇数八分推迟

# ── 旋律：D 小调五声 + 自然小调音，(拍, 时值, 音名)，按 2 小节乐句 ──
MELODY = {  # 从第 5 小节进入
    5:  [(0, .75, "A4"), (.75, .25, "C5"), (1, 1.75, "D5"), (3, .5, "C5"), (3.5, .5, "A4"), (4, 1, "G4"), (5, 1, "F4"), (6, 2, "D4")],
    7:  [(0, .75, "A4"), (.75, .25, "C5"), (1, 1.75, "D5"), (3, .5, "C5"), (3.5, .5, "E5"), (4, 1.5, "F5"), (5.5, .5, "C5"), (6, 2, "A4")],
    9:  [(.5, .5, "F4"), (1, .5, "G4"), (1.5, .5, "A4"), (2, 1, "C5"), (3.5, .5, "A4"), (4, 2, "G4"), (6, 2, "F4")],
    11: [(0, 1.5, "A4"), (2, .5, "C5"), (2.5, .5, "D5"), (3, 3, "C5"), (6, 2, "D4")],
}

rng = np.random.default_rng(42)

def jitter(ms=8.0):
    return rng.uniform(-ms, ms) / 1000.0

drums_k, drums_s, drums_h = Track(DUR), Track(DUR), Track(DUR)
tr_bass, tr_chord, tr_lead, tr_vinyl = Track(DUR), Track(DUR), Track(DUR), Track(DUR)

for bar in range(BARS):
    t0 = bar * BAR
    name, voicing, root = PROG[bar % 4]
    intro = bar < 2                       # 前两小节：鼓组留白，只有 hats

    for step in KICK_STEPS:
        if not intro:
            drums_k.add(kick(), t0 + step * BEAT / 4 + jitter(4), 1.0)
    if not intro and bar % 4 == 3:         # 每四小节末加一脚 push
        drums_k.add(kick(), t0 + 14 * BEAT / 4, 0.8)

    if not intro:
        for step in SNARE_STEPS:
            drums_s.add(snare(), t0 + step * BEAT / 4 + jitter(4), 0.8)
        if bar % 4 == 3:                   # 小 fill
            for i, (st, v) in enumerate([(13, .25), (14, .35), (15, .5)]):
                drums_s.add(snare(0.12), t0 + st * BEAT / 4, v)

    for step in range(0, 16, 2):           # 八分 hats，奇数八分吃 swing
        at = t0 + step * BEAT / 4 + (SWING if step % 4 == 2 else 0) + jitter(6)
        v = (0.9 if step % 4 == 0 else 0.55) * rng.uniform(.85, 1.15) * (0.7 if intro else 1.0)
        drums_h.add(hat(decay=0.09 if step == 14 else 0.03), at, v)

    for beat, dur, vel in CHORD_HITS:      # 电钢琴：块状和弦 + 琶音化 strum
        for i, n in enumerate(voicing):
            tr_chord.add(fm_ep(hz(n), dur), t0 + beat * BEAT + i * 0.025 + jitter(10),
                         vel * rng.uniform(.9, 1.1) * 0.5)

    if not intro:                          # 贝斯跟 kick 的两点
        tr_bass.add(bass(hz(root), 1.4), t0 + jitter(4), 0.55)
        tr_bass.add(bass(hz(root), 1.5), t0 + 2.5 * BEAT, 0.5)
        if bar % 4 == 3:
            tr_bass.add(bass(hz(root), 0.25), t0 + 3.75 * BEAT, 0.45)

    if bar in MELODY:                      # 旋律：第 5 小节起
        for beat, dur, n in MELODY[bar]:
            tr_lead.add(lead(hz(n), dur * BEAT), t0 + beat * BEAT + jitter(12), 0.85)

tr_vinyl.add(vinyl(DUR), 0, 0.5)

tracks = {
    "kick":  drums_k.buf * 1.00,
    "snare": reverb(drums_s.buf, 0.20) * 0.55,
    "hats":  drums_h.buf * 0.28,
    "bass":  tr_bass.buf * 1.0,
    "chords": reverb(tr_chord.buf, 0.18) * 1.0,
    "lead":  reverb(tr_lead.buf, 0.25) * 1.0,
    "vinyl": tr_vinyl.buf,
}
stereo = mixdown(tracks)

# ── 输出：原生 WAV（stdlib wave），响度归一交给 ffmpeg ──────────────
import wave
pcm = (np.clip(stereo, -1, 1) * 32767).astype("<i2")
with wave.open("out/synth_lofi_demo.wav", "wb") as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes(pcm.tobytes())
print(f"ok: out/synth_lofi_demo.wav  {len(pcm)/SR:.2f}s  {BPM} BPM  D minor  {BARS} bars")
