"""Independent row-layout and malformed-input coverage for native RGB/RGBA PNGs."""
import base64
import json
from pathlib import Path
import struct
import subprocess
import unittest
import zlib

try:
    from .test_pixel_codec_reference import chunk, png_case
except ImportError:
    from test_pixel_codec_reference import chunk, png_case


class PixelCodecRows(unittest.TestCase):
    def test_row_filters_and_input_ownership(self):
        cases = []
        for channels in (3, 4):
            for width in (1, 2, 3, 4, 5, 7, 8, 9, 31, 64, 65):
                for filters in ([0], [1], [2], [3], [4], [4, 2, 0, 3, 1]):
                    png, rgba = png_case(width, 7, channels, filters, width * 101 + filters[0])
                    # Alternate a single IDAT with deliberately unaligned multi-IDAT.
                    data = png[41:-16]
                    if len(cases) % 2:
                        png = png[:33] + chunk(b'IDAT', data[:1]) + chunk(b'IDAT', data[1:]) + chunk(b'IEND', b'')
                    cases.append(dict(width=width, height=7, png=base64.b64encode(png).decode(), rgba=base64.b64encode(rgba).decode()))
        # Every alpha value, including 0/128/255, with every filter. Pixels are
        # constant across rows, exercising predictor ties and modulo wraparound.
        pixels = bytes(v for a in range(256) for v in (255-a, a, (a*17)&255, a))
        for f in range(5):
            raw = bytearray()
            for y in range(2):
                raw.append(f)
                for x, v in enumerate(pixels):
                    a = pixels[x-4] if x >= 4 else 0
                    b = v if y else 0
                    c = pixels[x-4] if y and x >= 4 else 0
                    p = a+b-c
                    distances = [abs(p-a), abs(p-b), abs(p-c)]
                    predictor = [0, a, b, (a+b)//2, (a,b,c)[distances.index(min(distances))]][f]
                    raw.append((v-predictor)&255)
            header = struct.pack('>IIBBBBB', 256, 2, 8, 6, 0, 0, 0)
            png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')
            cases.append(dict(width=256, height=2, png=base64.b64encode(png).decode(), rgba=base64.b64encode(pixels*2).decode()))
        code = """import fs from 'node:fs';import assert from 'node:assert/strict';import {pathToFileURL} from 'node:url';
const {decodeNativePng}=await import(pathToFileURL(process.argv[1]));
for(const c of JSON.parse(fs.readFileSync(0,'utf8'))){
 const png=Buffer.from(c.png,'base64'), before=Buffer.from(png), expected=Buffer.from(c.rgba,'base64');
 assert.deepEqual(decodeNativePng(png,c.width,c.height),expected);
 assert.deepEqual(png,before); // only decoder-private inflate storage may change
 assert.deepEqual(decodeNativePng(png,c.width,c.height),expected);
}console.log('137 row/alpha cases PASS');"""
        self.run_node(code, cases)
        self.assertEqual(len(cases), 137)

    def test_rejection_strength_rgb_and_rgba(self):
        cases = []
        for channels in (3, 4):
            good, _ = png_case(5, 3, channels, [0], 17)
            header = good[8:33]
            length = (5*channels+1)*3
            for raw in (bytes(length-1), bytes(length+1), b'', bytes([5])+bytes(length-1), bytes(length-(5*channels+1))+bytes([255])+bytes(5*channels)):
                png = good[:8]+header+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')
                cases.append(base64.b64encode(png).decode())
            cases.extend(base64.b64encode(good[:n]).decode() for n in (0,7,12,32,40,len(good)-15))
            cases.append(base64.b64encode(good[:8]+header+chunk(b'IDAT',b'bad zlib')+chunk(b'IEND',b'')).decode())
        code = """import fs from 'node:fs';import assert from 'node:assert/strict';import {pathToFileURL} from 'node:url';
const {decodeNativePng}=await import(pathToFileURL(process.argv[1]));
for(const c of JSON.parse(fs.readFileSync(0,'utf8')))assert.throws(()=>decodeNativePng(Buffer.from(c,'base64'),5,3));
console.log('24 malformed payloads rejected');"""
        self.run_node(code, cases)

    def run_node(self, code, cases):
        result = subprocess.run(['node', '--input-type=module', '-e', code, str(Path(__file__).with_name('pixel_codec.mjs'))], input=json.dumps(cases), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
