"""sample 引擎：one-shot 采样触发 → wav（numpy 时间线混合）。

payload（v1，两种等价入口）：
    hits      [{file, at(秒), gain_db(默认 0)}, ...]
    —— 或 BG-D7 记谱法（R2 审查 M3 适配，R3 core/project.py 统一走此路）——
    instrument 采样文件（file 解析规则同下）
    notes     [[pitch, beat, dur(beat), vel(0-127)], ...]（pitch 仅记录用，vel→gain）
    bpm       默认 120；at = beat*60/bpm，gain_db = 20*log10(vel/100)
    length_s  输出总时长（秒；混合结果不足补零、超出截断）

file 解析：绝对路径直通；相对路径相对采样根解析。
采样根 = runtime.json 的 samples_root（doctor 校验；2026-08-22 迁移起显式配置，
当前指向上游私有视频项目 _library/assets_audio/（$MUSIC_SAMPLES_ROOT）——Kenney 全 10 包在 _raw/kenney/…，
LICENSE 见该目录 LICENSE.md；缺键时回退旧的"包父目录/assets_audio"推导——该回退
迁移后已不适用（拼出的路径不存在），仅作旧 runtime.json 的显式报错路径）。

实现：wave 直读（44100 假设外/非 PCM 一律 ffmpeg 解码重采样到 spec.sr）→
numpy 等功率叠加（gain 为 dB）→ PCM16 写 spec.out。

v3 R1-W1：payload 单字段/入口互斥校验由描述符驱动（base.validate_payload，
run_engine 前置；见 descriptors/sample.json invariants）。本引擎保留语义守卫：
文件存在性（resolve_sample）、length_s*sr<=0、hits[i].at 负值。
"""

import math
import os

import numpy as np

from .base import (BaseEngine, EngineError, default_out, measure_wav,
                   register_engine)
from . import _wavio

from ..paths import samples_root as configured_samples_root


def resolve_sample(ref, root):
    p = os.path.expanduser(ref)
    if os.path.isabs(p):
        if not os.path.exists(p):
            raise EngineError(f"采样文件不存在：{p}")
        return p
    cand = os.path.join(root, *p.split("/"))
    if not os.path.exists(cand):
        raise EngineError(f"采样文件不存在（相对 samples_root={root}）：{ref} → {cand}")
    return cand


@register_engine("sample")
class SampleEngine(BaseEngine):
    determinism = "deterministic"
    summary = "one-shot 采样触发（Kenney 等，numpy 时间线混合）"

    def render(self, spec):
        payload = spec.get("payload", {})
        hits = payload.get("hits")
        if not hits and "notes" in payload:
            bpm = float(payload.get("bpm", 120.0)) or 120.0
            spb = 60.0 / bpm
            hits = [{"file": payload["instrument"],
                     "at": float(n[1]) * spb,
                     "gain_db": 20.0 * math.log10(max(float(n[3]), 1.0) / 100.0)}
                    for n in payload["notes"]]
        sr = int(spec.get("sr", 44100))
        channels = int(spec.get("channels", 2))
        out = spec.get("out") or default_out("smoke", self.name, "render.wav")

        n_out = int(round(float(payload["length_s"]) * sr))
        if n_out <= 0:
            raise EngineError(f"length_s 非法：{payload['length_s']}")
        buf = np.zeros((n_out, channels), dtype=np.float64)
        samples_root = configured_samples_root(self.rt)

        for i, hit in enumerate(hits):
            path = resolve_sample(hit["file"], samples_root)
            at = float(hit.get("at", 0.0))
            gain = 10.0 ** (float(hit.get("gain_db", 0.0)) / 20.0)
            audio, _ = _wavio.read_audio(path, sr)
            audio = _wavio.to_channels(audio, channels)
            start = int(round(at * sr))
            if start < 0:
                raise EngineError(f"hits[{i}].at 非法（负值）：{at}")
            if start >= n_out:
                continue
            seg = audio[:n_out - start] * gain
            buf[start:start + len(seg)] += seg

        _wavio.write_wav_s16(out, buf, sr)
        m = measure_wav(out)
        return {"path": out, "sr": m["sr"], "channels": m["channels"],
                "duration_s": m["duration_s"], "rms_db": m["rms_db"]}
