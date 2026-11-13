"""引擎内部共享的 wav 读写小工具（下划线名=不被 engines/__init__ 自动发现，仅供显式 import）。

只依赖标准库 + numpy + ffmpeg 子进程（与 engines/__init__ 的模块级依赖约定一致）。
"""

import os
import shutil
import subprocess
import tempfile
import wave

import numpy as np

from .base import EngineError


def _decode_pcm(raw, sampwidth, channels):
    if sampwidth == 2:
        a = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    elif sampwidth == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v > 0x7FFFFF, v - 0x1000000, v)
        a = v.astype(np.float64) / 8388608.0
    elif sampwidth == 4:
        a = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0
    else:
        raise EngineError(f"不支持的采样宽度 {sampwidth}")
    return a.reshape(-1, channels)


def read_wav(path):
    """直读 wav → (audio[n,ch] float64, sr)。仅 PCM；其他编码抛异常。"""
    with wave.open(path, "rb") as w:
        sr, ch, n, sw = w.getframerate(), w.getnchannels(), w.getnframes(), w.getsampwidth()
        raw = w.readframes(n)
    return _decode_pcm(raw, sw, ch), sr


def read_audio(path, sr):
    """任意音频 → (audio[n,ch] float64, sr)。
    wave 可直读且 sr 匹配 → 直读；否则 ffmpeg 解码/重采样到 PCM16 wav 再读。
    """
    try:
        a, got = read_wav(path)
        if got == sr:
            return a, got
    except Exception:
        pass
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path,
                        "-ar", str(sr), "-c:a", "pcm_s16le", tmp.name], check=True)
        a, got = read_wav(tmp.name)
        return a, got
    finally:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)


def to_channels(audio, channels):
    """声道数映射：mono→复制；N>目标→前取/均值折叠；已匹配→原样。"""
    if audio.shape[1] == channels:
        return audio
    if audio.shape[1] == 1:
        return np.repeat(audio, channels, axis=1)
    if channels == 1:
        return audio.mean(axis=1, keepdims=True)
    return audio[:, :channels]


def write_wav_s16(path, audio, sr):
    """audio[n,ch] float64 → PCM16 wav（clip 后 32767 量化，与 synthlib 惯例一致）。"""
    audio = np.clip(np.asarray(audio, dtype=np.float64), -1.0, 1.0)
    pcm = (audio * 32767.0).astype("<i2")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(pcm.shape[1])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def resample_wav_file(path, sr):
    """wav 文件整体重采样（原地，ffmpeg）。sr 已匹配时 no-op。"""
    _, got = read_wav(path)
    if got == sr:
        return
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path,
                        "-ar", str(sr), "-c:a", "pcm_s16le", tmp.name], check=True)
        shutil.move(tmp.name, path)
    finally:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)
