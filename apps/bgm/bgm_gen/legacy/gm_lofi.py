#!/usr/bin/env python3
"""gm_lofi — 同一份编曲数据（demo_lofi）走"开源巨人"路线：
乐谱 → MIDI(SMF 字节) → libfluidsynth（ctypes，Fedora 自带）→ FluidR3_GM 音色 → wav。
证明：经典音色生态不需要逆向任何商业宿主，命令行管线即插即用。

薄包装版（PLAN.md BG-D4，2026-08-22 R2 重构）：
SMF 写入器上移 engines/midi_util.py、fluidsynth 渲染上移 engines/sf2.py；
argv/cwd 契约与输出路径不变（2026-08-22 迁移后位于 bgm_gen/legacy/，
PYTHONPATH=仓库根；产物 out/gm_lofi_demo.{mid,wav} 相对 cwd）。
"""
from bgm_gen.legacy.demo_lofi import (BPM, BEAT, BARS, PROG, CHORD_HITS,
                                      KICK_STEPS, SNARE_STEPS, SWING, MELODY)
from bgm_gen.synthlib import hz

PPQ = 480
SF2 = "/usr/share/soundfonts/FluidR3_GM.sf2"
OUT_MID = "out/gm_lofi_demo.mid"
OUT_WAV = "out/gm_lofi_demo.wav"


def midi_num(note: str) -> int:
    return round(69 + 12 * __import__("math").log2(hz(note) / 440))


def b(sec: float) -> float:
    """秒 → 拍（与旧 tick(sec)=round(sec/BEAT*PPQ) 换算逐位一致）。"""
    return sec / BEAT


# ── 编曲 → MIDI（数据源与 demo_lofi 完全一致）────────────────────────
def build_tracks():
    from bgm_gen.engines.midi_util import Trk
    chords, bass_tr, mel, drums = Trk(0, prog=4), Trk(2, prog=33), Trk(1, prog=11), Trk(9)
    for bar in range(BARS):
        t0 = bar * 4 * BEAT
        _, voicing, root = PROG[bar % 4]
        for beat, dur, vel in CHORD_HITS:
            for i, n in enumerate(voicing):
                chords.note(midi_num(n), b(t0 + beat * BEAT + i * 0.025), b(dur), int(vel * 88))
        if bar >= 2:
            bass_tr.note(midi_num(root), b(t0), b(1.4), 80)
            bass_tr.note(midi_num(root), b(t0 + 2.5 * BEAT), b(1.5), 72)
            if bar % 4 == 3:
                bass_tr.note(midi_num(root), b(t0 + 3.75 * BEAT), b(0.25), 66)
        for step in KICK_STEPS + ([14] if bar % 4 == 3 else []):
            if bar >= 2:
                drums.note(36, b(t0 + step * BEAT / 4), b(0.3), 118)
        if bar >= 2:
            for step in SNARE_STEPS:
                drums.note(38, b(t0 + step * BEAT / 4), b(0.2), 90)
            if bar % 4 == 3:
                for st, v in [(13, 36), (14, 48), (15, 64)]:
                    drums.note(38, b(t0 + st * BEAT / 4), b(0.1), v)
        for step in range(0, 16, 2):
            swing = SWING if step % 4 == 2 else 0
            drums.note(46 if step == 14 else 42,
                       b(t0 + step * BEAT / 4 + swing), b(0.12),
                       int((64 if step % 4 == 0 else 44) * (0.7 if bar < 2 else 1)))
        if bar in MELODY:
            for beat, dur, n in MELODY[bar]:
                mel.note(midi_num(n), b(t0 + beat * BEAT), b(dur * BEAT), 90)
    return [chords, bass_tr, mel, drums]


def main():
    from bgm_gen.engines.midi_util import write_smf
    from bgm_gen.engines.sf2 import fluidsynth_render
    write_smf(OUT_MID, build_tracks(), bpm=BPM)
    # bank/program=None：音色沿用 MIDI 文件内的 program change（与旧版渲染路径一致）
    fluidsynth_render(OUT_MID, OUT_WAV, SF2, sr=44100, bank=None, program=None, channel=0)
    print(f"ok: {OUT_WAV}  (MIDI: {OUT_MID})")


if __name__ == "__main__":
    main()
