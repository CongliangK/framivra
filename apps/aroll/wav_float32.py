"""Canonical mono IEEE-float32 WAV interchange for A-roll extraction and trim."""
import struct
from pathlib import Path


def read_wav_float32(path):
    """Read the exact uncompressed layout emitted by write_wav_float32."""
    import numpy as np

    raw = Path(path).read_bytes()
    if len(raw) < 44:
        raise ValueError("truncated float32 WAV header")
    (riff, size, wave, fmt, fmt_size, encoding, channels, rate, byte_rate,
     alignment, bits, data, data_size) = struct.unpack_from("<4sI4s4sIHHIIHH4sI", raw)
    if (riff != b"RIFF" or wave != b"WAVE" or fmt != b"fmt " or fmt_size != 16
            or encoding != 3 or channels != 1 or bits != 32 or alignment != 4
            or rate == 0 or byte_rate != rate * 4 or data != b"data"):
        raise ValueError("expected canonical mono IEEE-float32 WAV")
    if size != len(raw) - 8 or data_size != len(raw) - 44 or data_size % 4:
        raise ValueError("invalid float32 WAV payload length")
    return np.frombuffer(raw, dtype="<f4", offset=44).copy(), rate


def write_wav_float32(path, samples, rate):
    """Write canonical mono IEEE-float32 WAV without changing sample rate."""
    if type(rate) is not int or not 0 < rate <= 0xFFFFFFFF // 4:
        raise ValueError("sample rate must be a positive integer fitting WAV byte rate")
    data = samples.astype("<f4").tobytes()
    header = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(data), b"WAVE",
                         b"fmt ", 16, 3, 1, rate, rate * 4, 4, 32, b"data", len(data))
    with open(path, "wb") as stream:
        stream.write(header)
        stream.write(data)
