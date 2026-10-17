"""Independent stdlib PNG encoder reference for the native semantic decoder."""
import base64
import binascii
import json
from pathlib import Path
import random
import struct
import subprocess
import unittest
import zlib


def chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', binascii.crc32(kind + data) & 0xffffffff)


def png_case(width, height, channels, filters, seed):
    rng = random.Random(seed)
    pixels = bytes(rng.randrange(256) for _ in range(width * height * channels))
    stride = width * channels
    packed = bytearray()
    for y in range(height):
        f = filters[y % len(filters)]
        packed.append(f)
        for x in range(stride):
            i = y * stride + x
            a = pixels[i-channels] if x >= channels else 0
            b = pixels[i-stride] if y else 0
            c = pixels[i-stride-channels] if y and x >= channels else 0
            if f == 0:
                p = 0
            elif f == 1:
                p = a
            elif f == 2:
                p = b
            elif f == 3:
                p = (a+b) // 2
            else:
                v = a+b-c
                distances = [abs(v-a), abs(v-b), abs(v-c)]
                p = [a, b, c][distances.index(min(distances))]
            packed.append((pixels[i] - p) & 255)
    header = struct.pack('>IIBBBBB', width, height, 8, 6 if channels == 4 else 2, 0, 0, 0)
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(packed)) + chunk(b'IEND', b'')
    rgba = pixels if channels == 4 else b''.join(pixels[i:i+3] + b'\xff' for i in range(0, len(pixels), 3))
    return png, rgba


class PixelCodecReference(unittest.TestCase):
    def test_all_filters_rgb_rgba_and_boundaries(self):
        cases = []
        for channels in (3, 4):
            for width, height in ((1, 1), (2, 5), (65, 7), (127, 9)):
                for f in range(5):
                    png, rgba = png_case(width, height, channels, [f], 1000 + width + f + channels)
                    cases.append({'width': width, 'height': height, 'png': base64.b64encode(png).decode(), 'rgba': base64.b64encode(rgba).decode()})
            png, rgba = png_case(192, 23, channels, [0, 1, 2, 3, 4], 42)
            cases.append({'width': 192, 'height': 23, 'png': base64.b64encode(png).decode(), 'rgba': base64.b64encode(rgba).decode()})
        code = """import fs from 'node:fs';import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {decodeNativePng}=await import(pathToFileURL(process.argv[1]));
const cases=JSON.parse(fs.readFileSync(0,'utf8'));
for(const c of cases){const p=Buffer.from(c.png,'base64');assert.deepEqual(decodeNativePng(p,c.width,c.height),Buffer.from(c.rgba,'base64'));
assert.throws(()=>decodeNativePng(p,c.width+1,c.height));
assert.throws(()=>decodeNativePng(p.subarray(0,p.length-15),c.width,c.height));}
console.log(JSON.stringify({cases:cases.length,exactRGBA:true}));"""
        result = subprocess.run(['node', '--input-type=module', '-e', code, str(Path(__file__).with_name('pixel_codec.mjs'))], input=json.dumps(cases), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['cases'], 42)

    def test_invalid_payload_and_filter(self):
        good, _ = png_case(65, 3, 4, [0], 2)
        header = good[8:33]
        bad = []
        for raw in (b'\x05' + bytes(65*4), b'\x00', bytes((65*4+1)*3+1)):
            bad.append(b'\x89PNG\r\n\x1a\n' + header + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))
        # A full-size payload with an invalid first row filter.
        raw = bytearray((65*4+1)*3); raw[0] = 5
        bad.append(b'\x89PNG\r\n\x1a\n' + header + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))
        code = """import fs from 'node:fs';import assert from 'node:assert/strict';import {pathToFileURL} from 'node:url';
const {decodeNativePng}=await import(pathToFileURL(process.argv[1]));for(const b of JSON.parse(fs.readFileSync(0,'utf8')))assert.throws(()=>decodeNativePng(Buffer.from(b,'base64'),65,3));"""
        result = subprocess.run(['node', '--input-type=module', '-e', code, str(Path(__file__).with_name('pixel_codec.mjs'))], input=json.dumps([base64.b64encode(x).decode() for x in bad]), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
