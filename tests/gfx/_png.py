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


def png(w=6, h=4, value=0, filter_byte=0):
    return (gfx_cli.PNG_SIGNATURE + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((bytes([filter_byte]) + bytes([value]) * (w * 4)) * h))
            + chunk(b'IEND', b''))
