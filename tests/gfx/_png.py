"""Deterministic PNG fixtures shared by graphics regressions."""
import struct
import zlib
from vtx import gfx_cli

def write_png(path, width=16, height=16, frame=0):
    raw = b''.join(b'\0' + bytes((frame * 7 % 256, y * 11 % 256, 180, frame * 13 % 256)) * width
                   for y in range(height))
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
                     + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))

def chunk(tag, data):
    return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))


def png(w=6, h=4, value=0, filter_byte=0, *, rgba=None):
    if rgba is None:
        rows = (bytes([filter_byte]) + bytes([value]) * (w * 4)) * h
    else:
        assert len(rgba) == w * h * 4
        rows = b''.join(bytes([filter_byte]) + rgba[y*w*4:(y+1)*w*4] for y in range(h))
    return (gfx_cli.PNG_SIGNATURE + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows))
            + chunk(b'IEND', b''))


def unfilter_rgba(w, h, raw):
    """Rebuild RGBA8 scanlines; callers retain their own PNG validation."""
    stride = w * 4 + 1
    out = bytearray(w * h * 4)
    prev = bytearray(w * 4)
    for y in range(h):
        ftype = raw[y * stride]
        row = bytearray(raw[y * stride + 1:(y + 1) * stride])
        if ftype == 1:  # Sub
            for i in range(4, len(row)):
                row[i] = (row[i] + row[i - 4]) & 255
        elif ftype == 2:  # Up
            for i in range(len(row)):
                row[i] = (row[i] + prev[i]) & 255
        elif ftype == 3:  # Average
            for i in range(len(row)):
                left = row[i - 4] if i >= 4 else 0
                row[i] = (row[i] + (left + prev[i]) // 2) & 255
        elif ftype == 4:  # Paeth
            for i in range(len(row)):
                a = row[i - 4] if i >= 4 else 0
                b = prev[i]
                c = prev[i - 4] if i >= 4 else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                row[i] = (row[i] + pred) & 255
        else:
            assert ftype == 0, f"非法定行滤波类型 {ftype}"
        out[y * w * 4:(y + 1) * w * 4] = row
        prev = row
    return bytes(out)


def golden_frame(path, i, tamper=False, *, size=(64, 48)):
    """第 i 帧内容随帧号确定变化；tamper=True 改一个像素（模拟渲染差异）。"""
    from PIL import Image
    im = Image.new("RGBA", size, (i % 256, (i * 7) % 256, (i * 13) % 256, 255))
    if tamper:
        im.putpixel((0, 0), (255, 255, 255, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
