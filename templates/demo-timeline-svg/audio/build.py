#!/usr/bin/env python3
"""Portable audio-r2 premaster: Python >=3.9 standard library, integer DSP only.

The reviewed audio-r1 arrangement is retained, not its Scoreweft timbres or hash.
Fixed waveform/frequency tables, Q32 phase, polynomial envelopes, fixed pan and
integer truncation define the new PCM identity. No random, libm, external tools,
normalization or mastering. Human listening and native audio remain UNRUN.
Running --out writes two WAVs, the authored score, binding and identity JSON.
"""
import argparse
from array import array
import hashlib
import json
from pathlib import Path
import wave

SR, FPS, N, SCALE = 48000, 30, 1440000, 1 << 23
CUE_N = 54400
IDENTITY = 'demo-audio-r2-integer-wavetable-v1'
# Rounded Q15 sin(i*pi/128), frozen literals; no runtime transcendental math.
QUARTER = (
    0, 804, 1608, 2410, 3212, 4011, 4808, 5602, 6393, 7179, 7962,
    8739, 9512, 10278, 11039, 11793, 12539, 13279, 14010, 14732,
    15446, 16151, 16846, 17530, 18204, 18868, 19519, 20159, 20787,
    21403, 22005, 22594, 23170, 23731, 24279, 24811, 25329, 25832,
    26319, 26790, 27245, 27683, 28105, 28510, 28898, 29268, 29621,
    29956, 30273, 30571, 30852, 31113, 31356, 31580, 31785, 31971,
    32137, 32285, 32412, 32521, 32609, 32678, 32728, 32757, 32767,
)
SINE = QUARTER[:-1] + QUARTER[:0:-1] + tuple(-x for x in QUARTER[:-1]) + tuple(-x for x in QUARTER[:0:-1])
# Equal temperament A4=440Hz, rounded once to microhertz and frozen.
FREQUENCIES = {
    31: 48999429, 33: 55000000, 36: 65406391, 38: 73416192,
    45: 110000000, 50: 146832384, 55: 195997718, 57: 220000000,
    61: 277182631, 62: 293664768, 64: 329627557, 66: 369994423,
    69: 440000000, 71: 493883301, 74: 587329536, 76: 659255114,
    78: 739988845,
}
ANCHORS = {'json_origin': 18, 'arrangement_read': 135, 'first_hit': 228,
           'rewrite': 339, 'old_point_pass': 411, 'new_hit': 462,
           'result_carry': 510, 'result_settle': 606, 'brand_transfer': 720,
           'brand_lock': 774, 'end': 900}
# Peak scale, left/right Q10 pan. Nylon left / motif right, others centered.
VOICES = {'nylon': (3400000, 798, 640), 'bass': (480000, 724, 724),
          'kick': (260000, 724, 724), 'motif': (950000, 640, 798),
          'knock': (2400000, 724, 724)}


def score():
    """Original absolute timings, pitches, durations and velocities in integers.

    Times/durations are milliseconds (48 exact samples/ms). gain_q10 preserves
    event -1/-2/-3 dB and low-nylon -3 dB using frozen rounded ratios.
    """
    events = []

    def add(voice, ms, pitch, velocity=62, duration=280, gain=1024):
        events.append({'voice': voice, 'sample': ms * 48, 'pitch': pitch,
                       'velocity': velocity, 'duration_samples': duration * 48,
                       'gain_q10': gain, 'track': 'timeline_cue' if voice == 'knock' else 'bed'})

    def pl(ms, pitch, velocity=62, duration=280, gain=1024):
        add('nylon', ms, pitch, velocity, duration, gain)

    def bass(ms, pitch=38, velocity=58, duration=320):
        add('bass', ms, pitch, velocity, duration)

    def kick(ms, velocity=58):
        add('kick', ms, 36, velocity, 1200)

    for row in [(120,50,48,600),(600,57,47,500),(1400,64,43,700),
                (2800,69,43,550),(3600,62,56,250),(3860,66,51,220)]:
        pl(*row)
    bass(600,38,43,550)
    for row in [(4100,55,61),(4350,62,48),(4850,64,58),(5100,69,53),
                (5600,57,63),(5850,66,50),(6100,62,56),(6280,69,43)]:
        pl(*row)
    for row in [(4100,31,53),(5100,38,48),(6100,38,46)]:
        bass(*row)
    kick(4100,51); kick(5100,45)
    for frame in (228, 462):
        add('knock', frame * 1000 // FPS, 74, 80, 120)
    for row in [(7830,62,64),(8100,66,50),(8350,69,58),(8850,64,49),(9100,62,53)]:
        pl(*row)
    bass(7850,38,59,400); kick(7850,56)
    add('motif',8600,78,52,260)
    for row in [(9950,57,45,650),(10600,64,40,550),(11300,61,40,400),
                (12000,66,36,700),(14500,69,31,180)]:
        pl(*row, gain=725)
    for row in [(15680,62,66),(15930,66,51),(16180,69,61),(16680,64,52),(16900,66,44)]:
        pl(*row)
    bass(15680,38,61,400); kick(15680,57); bass(16430,45,49,250)
    for row in [(17500,55,62),(17750,62,50),(18000,64,56),(18350,69,48),
                (18750,57,65),(19000,61,52),(19250,64,59),(19630,69,49),(20000,62,57)]:
        pl(*row)
    for row in [(17500,31,58),(18750,33,60),(19500,38,53)]:
        bass(*row)
    for row in [(17750,51),(18750,55),(19500,43)]:
        kick(*row)
    for row in [(18100,78,47),(18600,76,43),(19100,74,48)]:
        add('motif', *row, duration=300)
    for row in [(20350,66,43,800),(21200,57,40,700),(22100,62,41,750),
                (23000,64,38,700),(23650,69,36,500)]:
        pl(*row, gain=813)
    bass(21200,38,39,650)
    for row in [(24150,74,45),(24500,69,40),(24900,66,37)]:
        add('motif', *row, duration=320, gain=913)
    for row in [(25300,50,48,2700),(25370,57,43,2600),(25440,62,40,2500),
                (25510,66,35,2400),(25580,71,30,2300)]:
        pl(*row, gain=913)
    bass(25300,38,44,900)
    return {'format': 'demo-integer-score/v1', 'generator': IDENTITY,
            'sample_rate': SR, 'sample_frames': N, 'anchors_frames': ANCHORS,
            'events': sorted(events, key=lambda e: e['sample']),
            'arrangement_origin': 'audio-r1; Scoreweft aa5e7781952418f5881c41f166f237dc7a24111c; timbres replaced',
            'human_listening': 'UNRUN'}


def sine(phase):
    """Q32 periodic phase -> linearly interpolated Q15 fixed wavetable."""
    phase &= 0xffffffff
    index, fraction = phase >> 24, (phase >> 8) & 65535
    a = SINE[index]
    return a + (SINE[(index + 1) & 255] - a) * fraction // 65536


def note(event):
    """Additive pluck/rounded bass/low kick/bell; finite click-free tails."""
    voice = event['voice']
    duration = event['duration_samples']
    tail = {'nylon': 33600, 'bass': 12000, 'kick': 0,
            'motif': 19200, 'knock': 14400}[voice]
    length = duration + tail
    attack = 384 if voice == 'nylon' else 96
    step = (FREQUENCIES[event['pitch']] << 32) // (SR * 1000000)
    phase = 0
    amplitude = VOICES[voice][0] * event['velocity'] * event['gain_q10'] // (100 * 1024)
    if voice == 'nylon' and event['pitch'] < 55:
        amplitude = amplitude * 725 // 1024
    result = array('i')
    for i in range(length):
        # Squared decay, short linear attack; every voice ends exactly at zero.
        envelope = ((length - 1 - i) * 65536 // length) ** 2 // 65536
        envelope = envelope * min(i, attack) // attack
        fundamental = sine(phase)
        if voice == 'nylon':
            bright = (length - i) * 256 // length
            value = (fundamental * 1024 + sine(phase * 2) * bright
                     + sine(phase * 3) * (bright // 3)) // 1365
        elif voice == 'bass':
            value = (fundamental * 4 + sine(phase * 2)) // 5
        elif voice == 'kick':
            value = fundamental
        else:
            # Bell-like upper partial; cue is distinct but not in the bed.
            ratio = 271 if voice == 'knock' else 200
            bright = (length - i) * 512 // length
            value = (fundamental * 1024 + sine(phase * ratio // 100) * bright) // 1536
        result.append(value * amplitude * envelope // (32768 * 65536))
        # Kick sweeps from 125Hz to 45Hz with integer Q32 increments.
        phase += (((45 * length + 80 * (length - i)) << 32) // (length * SR)
                  if voice == 'kick' else step)
    return result


def render(events):
    left, right = array('i', [0]) * N, array('i', [0]) * N
    for event in events:
        if event['track'] != 'bed':
            continue
        start = event['sample']
        _, pan_l, pan_r = VOICES[event['voice']]
        samples = note(event)
        if start + len(samples) > N - 1600:
            raise ValueError('Authored note exceeds safety tail')
        for offset, value in enumerate(samples):
            left[start + offset] += value * pan_l // 1024
            right[start + offset] += value * pan_r // 1024
    return left, right


def wav24(path, left, right):
    if len(left) != len(right):
        raise ValueError('Stereo length mismatch')
    raw = bytearray(len(left) * 6)
    for i, (lvalue, rvalue) in enumerate(zip(left, right)):
        for channel, value in enumerate((lvalue, rvalue)):
            if not -SCALE < value < SCALE:
                raise ValueError('PCM would clip; no hidden limiter')
            pos = i * 6 + channel * 3
            raw[pos:pos + 3] = (value & 0xffffff).to_bytes(3, 'little')
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(2)
        output.setsampwidth(3)
        output.setframerate(SR)
        output.writeframes(raw)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    out = parser.parse_args().out.resolve()
    native = out / 'native-inputs'
    native.mkdir(parents=True, exist_ok=True)
    data = score()
    left, right = render(data['events'])
    wav24(native / 'bed-premaster.wav', left, right)
    cue_events = [e for e in data['events'] if e['track'] == 'timeline_cue']
    if [e['sample'] for e in cue_events] != [228 * 1600, 462 * 1600]:
        raise ValueError('Cue placement changed')
    cue = note(cue_events[0])
    if cue != note(cue_events[1]) or len(cue) > CUE_N - 1600:
        raise ValueError('Cue reuse/tail contract failed')
    cue.extend([0] * (CUE_N - len(cue)))
    pan = VOICES['knock'][1]
    cue = array('i', (value * pan // 1024 for value in cue))
    wav24(native / 'content-cue.wav', cue, cue)
    dump(out / 'score.json', data)
    dump(out / 'native-binding-fragment.json', {
        'status': 'fragment_only_merge_into_verified_full_timeline_then_validate_and_run_native_audio',
        'sources': [{'id': 'timeline_music_bed', 'path': 'native-inputs/bed-premaster.wav'},
                    {'id': 'same_content_cue', 'path': 'native-inputs/content-cue.wav'}],
        'audio_tracks': [
            {'name': 'bed', 'duck': False, 'events': [
                {'src': 'timeline_music_bed', 'in': 0, 'out': 900, 'volume': 1}]},
            {'name': 'timeline_cue', 'duck': False, 'events': [
                {'src': 'same_content_cue', 'src_in': 0, 'in': frame, 'out': frame + 34, 'volume': 1}
                for frame in (228, 462)]}],
        'note': 'Premaster inputs, not mastered audio; paths relative to this fragment.'})
    identity = {'generator': IDENTITY, 'build_script_sha256': sha(__file__),
                'score_sha256': sha(out / 'score.json'), 'integer_dsp': True,
                'runtime_dependencies': 'Python >=3.9 standard library only',
                'format': {'sample_rate': SR, 'channels': 2, 'pcm_bits': 24,
                           'bed_sample_frames': N, 'cue_sample_frames': CUE_N,
                           'zero_tail_minimum_sample_frames': 1600},
                'human_listening': 'UNRUN', 'native_audio_stage': 'UNRUN',
                'limitations': 'New additive synthetic timbres, not Scoreweft nylon/FM equivalence; no mastering, dither or perceptual quality certification.',
                'output_sha256': {p.name: sha(p) for p in sorted(native.glob('*.wav'))}}
    dump(out / 'audio-identity.json', identity)
    print(json.dumps({'output': str(out), **identity}, indent=2))


if __name__ == '__main__':
    main()
