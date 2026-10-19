"""synth 引擎：synthlib（纯 numpy 迷你合成器机架）的引擎包装。

payload（v1）：
    program  synthlib 音色函数名：kick/snare/hat（打击乐，pitch 忽略）
             fm_ep/bass/lead/nylon（调性，pitch→f0）
    notes    [[pitch, beat, dur, vel], ...]（BG-D7 统一记谱；beat/dur 单位=四分音符）
    bpm      默认 120（beat→秒：beat * 60/bpm）
    params   可选，按 program 透传为函数 kwargs
             （fm_ep: ratio/idx/bright_decay；hat: decay；
              nylon: bright/pick/body/decay_ref——拨弦物理模型，2026-08-25
              guitar_test30 轮新增，vel→gain 线性、亮度为轨级参数；…）

实现：synthlib 内部 SR 恒 44100；spec.sr≠44100 时 ffmpeg 重采样落盘。
vel→gain 线性映射 vel/127；总线 = 各触发叠加 → softclip（synthlib.softclip，
与 demo_lofi mixdown 同款防削波）→ 立体声复制。
synthlib 随机源全部种子化（osc("noise") 用 default_rng(int(f0))；nylon 用
splitmix64 整数哈希流——BG-D16 纪律），deterministic＝同 numpy 版本内逐位确定
（default_rng 的 Generator 流跨 numpy 版本不承诺稳定，NEP 19；见 core/space.py
确定性红线说明——splitmix64 部分跨版本稳定，Generator 部分仅同版本内确定）。
"""

import os
import sys

import numpy as np

from .base import (BaseEngine, EngineError, default_out, measure_wav,
                   register_engine)
from . import _wavio

_PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # bgm_gen/

PERC = {"kick", "snare", "hat"}
TONAL = {"fm_ep", "bass", "lead", "nylon"}
SYNTHLIB_SR = 44100


def _midi_hz(pitch):
    return 440.0 * 2 ** ((int(pitch) - 69) / 12.0)


def _import_synthlib():
    if _PKG_DIR not in sys.path:
        sys.path.insert(0, _PKG_DIR)
    import synthlib
    return synthlib


def render_synth_notes(program, notes, bpm=120.0, params=None):
    """纯函数：编谱 → mono numpy 波形（synthlib SR）。供引擎与测试复用。"""
    sl = _import_synthlib()
    if program not in PERC and program not in TONAL:
        raise EngineError(f"synth program 未知：{program}；可用：{sorted(PERC | TONAL)}")
    fn = getattr(sl, program)
    params = dict(params or {})
    spb = 60.0 / bpm

    events = []
    for row in notes:
        pitch, beat, dur, vel = row
        at_s, dur_s = float(beat) * spb, float(dur) * spb
        gain = float(vel) / 127.0
        if program in PERC:
            sig = fn(dur_s, **params) if dur_s > 0 else fn(**params)
        else:
            sig = fn(_midi_hz(pitch), dur_s, **params)
        events.append((at_s, sig * gain))

    if not events:
        raise EngineError("synth notes 为空")
    end = max(at + len(sig) / SYNTHLIB_SR for at, sig in events)
    buf = np.zeros(int(end * SYNTHLIB_SR) + SYNTHLIB_SR)     # 多留 1s 尾巴（同 Track 惯例）
    for at, sig in events:
        i = int(at * SYNTHLIB_SR)
        seg = sig[:len(buf) - i]
        buf[i:i + len(seg)] += seg
    return sl.softclip(buf * 0.9, 1.1)


@register_engine("synth")
class SynthEngine(BaseEngine):
    determinism = "deterministic"
    summary = ("synthlib 程序化音色"
               "（kick/snare/hat/fm_ep/bass/lead/nylon，纯 numpy）")

    def render(self, spec):
        payload = spec.get("payload", {})
        bpm = float(payload.get("bpm", 120.0))
        sr = int(spec.get("sr", 44100))
        channels = int(spec.get("channels", 2))
        out = spec.get("out") or default_out("smoke", self.name, "render.wav")

        mono = render_synth_notes(payload["program"], payload["notes"],
                                  bpm=bpm, params=payload.get("params"))
        audio = np.repeat(mono[:, None], channels, axis=1)
        _wavio.write_wav_s16(out, audio, SYNTHLIB_SR)
        if sr != SYNTHLIB_SR:
            _wavio.resample_wav_file(out, sr)

        m = measure_wav(out)
        return {"path": out, "sr": m["sr"], "channels": m["channels"],
                "duration_s": m["duration_s"], "rms_db": m["rms_db"]}
