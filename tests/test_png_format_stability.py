"""Mixed image2 formats must not reinitialize a live composition graph."""
import concurrent.futures
import hashlib
import json
import os
import random
import struct
import subprocess
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from unittest import mock

from vtx import frame_source, gfx_cli
from vtx.frame_source import PngSequence


def chunk(tag, data):
    return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)


def filtered(rows, bpp, filters):
    """Independent reference filter encoder from actual pixels, not residual insertion."""
    result = bytearray()
    previous = bytes(len(rows[0]))
    for y, row in enumerate(rows):
        f = filters[y % len(filters)]
        result.append(f)
        for x, v in enumerate(row):
            a = row[x-bpp] if x >= bpp else 0
            b = previous[x]
            c = previous[x-bpp] if x >= bpp else 0
            p = a+b-c
            distances = [abs(p-a), abs(p-b), abs(p-c)]
            paeth = (a, b, c)[distances.index(min(distances))]
            predictor = (0, a, b, (a+b)//2, paeth)[f]
            result.append((v-predictor) % 256)
        previous = row
    return bytes(result)


def png(rows, width, bpp=3, filters=(0,), before=(), after=(), split=False, depth=8):
    raw = filtered(rows, bpp, filters)
    compressed = zlib.compress(raw)
    parts = [compressed[:1], compressed[1:4], compressed[4:]] if split else [compressed]
    return (gfx_cli.PNG_SIGNATURE
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, len(rows), depth, 2 if bpp == 3 else 6, 0, 0, 0))
            + b''.join(chunk(k, b) for k, b in before)
            + b''.join(chunk(b'IDAT', p) for p in parts)
            + b''.join(chunk(k, b) for k, b in after) + chunk(b'IEND', b''))


def chunks(data):
    p, result = 8, []
    while p < len(data):
        n = int.from_bytes(data[p:p+4], 'big')
        result.append((data[p+4:p+8], data[p+8:p+8+n]))
        p += n+12
    return result


def sample(width, height, bpp=3, seed=1):
    rng = random.Random(seed)
    return [bytes(rng.randrange(256) for _ in range(width*bpp)) for _ in range(height)]


def rgba_rows(rows):
    return [b''.join(row[x:x+3]+b'\xff' for x in range(0, len(row), 3)) for row in rows]


class TestOpaqueConversion(unittest.TestCase):
    def test_every_filter_first_row_pixel_stride_and_multiblock(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'in.png'
            for width in (1, 2, 7, 16):
                for height in (1, 2, 5):
                    for filters in ((0,), (1,), (2,), (3,), (4,), (4, 3, 2, 1, 0)):
                        with self.subTest(width=width, height=height, filters=filters):
                            rows = sample(width, height)
                            data = png(rows, width, filters=filters, split=True)
                            p.write_bytes(data)
                            out = gfx_cli.opaque_rgb_to_rgba(p, width=width, height=height,
                                                           expected_digest=hashlib.sha256(data).hexdigest())
                            decoded = zlib.decompress(b''.join(b for k, b in chunks(out) if k == b'IDAT'))
                            self.assertEqual(decoded, filtered(rgba_rows(rows), 4, filters))
                            p.write_bytes(out)
                            self.assertIsNone(gfx_cli.png_invalid_reason(p, width=width, height=height))

    def test_metadata_order_and_colour_bytes_preserved(self):
        before = [(b'gAMA', struct.pack('>I', 45455)), (b'cHRM', b'\0'*32),
                  (b'iCCP', b'profile\0\0'+zlib.compress(b'profile bytes')),
                  (b'sBIT', b'\x08\x07\x06'), (b'PLTE', b'\0\0\0'),
                  (b'bKGD', b'\0\x01\0\x02\0\x03'), (b'tEXt', b'key\0value')]
        after = [(b'tEXt', b'after\0IDAT')]
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'in.png'
            data = png(sample(2, 2), 2, before=before, after=after, split=True)
            p.write_bytes(data)
            out = gfx_cli.opaque_rgb_to_rgba(p, width=2, height=2,
                                            expected_digest=hashlib.sha256(data).hexdigest())
            actual = [(k,b) for k,b in chunks(out) if k not in (b'IHDR', b'IDAT', b'IEND')]
            expected = [(k,b+b'\x08' if k==b'sBIT' else b) for k,b in before+after]
            self.assertEqual(actual, expected)

    def test_unknown_unsafe_chunk_refused_safe_chunk_keeps_idat_side(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'in.png'
            for tag,allowed in ((b'vpAG',False),(b'vpAg',True)):
                data=png(sample(2,2),2,before=[(tag,b'before')],after=[(b'zzZz',b'after')])
                p.write_bytes(data)
                kwargs=dict(width=2,height=2,expected_digest=hashlib.sha256(data).hexdigest())
                if not allowed:
                    with self.assertRaisesRegex(ValueError,'unsafe-to-copy'):gfx_cli.opaque_rgb_to_rgba(p,**kwargs)
                else:
                    result=chunks(gfx_cli.opaque_rgb_to_rgba(p,**kwargs))
                    names=[k for k,_ in result]
                    self.assertLess(names.index(tag),names.index(b'IDAT'))
                    self.assertGreater(names.index(b'zzZz'),names.index(b'IDAT'))
                    self.assertIn((tag,b'before'),result);self.assertIn((b'zzZz',b'after'),result)

    def test_rejects_corruption_dimensions_digest_trns_and_limits(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'in.png'
            data = png(sample(2, 2), 2)
            for bad in (data[:-1], data[:40]+bytes([data[40]^1])+data[41:],
                        png(sample(2, 2), 2, before=[(b'tRNS', b'\0\x01'*3)])):
                p.write_bytes(bad)
                with self.assertRaises(ValueError):
                    gfx_cli.opaque_rgb_to_rgba(p, width=2, height=2,
                                              expected_digest=hashlib.sha256(bad).hexdigest())
            p.write_bytes(data)
            for w, digest in ((3, hashlib.sha256(data).hexdigest()), (2, 'wrong')):
                with self.assertRaises(ValueError):
                    gfx_cli.opaque_rgb_to_rgba(p, width=w, height=2, expected_digest=digest)
            with mock.patch.object(Path, 'stat', return_value=mock.Mock(st_size=(32<<20)+1)), \
                 mock.patch.object(Path, 'is_file', return_value=True):
                self.assertIn('limit', gfx_cli.png_invalid_reason(p, _chunks=[]))
            huge = gfx_cli.PNG_SIGNATURE + chunk(b'IHDR', struct.pack('>IIBBBBB', 100000, 100000, 8, 2, 0, 0, 0))
            p.write_bytes(huge)
            self.assertIn('limit', gfx_cli.png_invalid_reason(p, _chunks=[]))


class TestStableSource(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.directory = self.root/'original'
        self.directory.mkdir()
        self.source = PngSequence(self.directory, 'test-source', 4, 30, 8, 4)

    def write(self, i, bpp=4, before=()):
        rows = sample(8, 4, bpp, i)
        # Exercise legal transparency and hidden RGB without using colour as ID.
        if bpp == 4:
            rows = [bytes(v if x%4!=3 else (0,64,128,255)[(x//4)%4]
                          for x,v in enumerate(row)) for row in rows]
        data = png(rows, 8, bpp, filters=(0,1,3,4), before=before)
        (self.directory/f'f{i:04d}.png').write_bytes(data)
        return data

    def output(self, start=0, stop=None):
        argv = self.source.input_args(start, stop)
        return Path(argv[-1]).parent, int(argv[3])

    def test_uniform_is_zero_write_and_mixed_only_rgb_is_transcoded(self):
        for bpp in (3,4):
            for i in range(4): self.write(i, bpp)
            self.assertEqual(self.output(), (self.directory, 0))
            self.assertEqual(list(self.root.iterdir()), [self.directory])
        self.write(2, 3)
        originals = {p.name:p.read_bytes() for p in self.directory.iterdir()}
        with mock.patch.object(frame_source, 'opaque_rgb_to_rgba', wraps=gfx_cli.opaque_rgb_to_rgba) as convert:
            out, first = self.output()
            self.assertEqual(convert.call_count, 1)
            self.assertEqual(first, 0)
            stat = {p.name:p.stat().st_mtime_ns for p in out.glob('*.png')}
            self.assertEqual(self.output(), (out,0))
            self.assertEqual(convert.call_count, 1)
            self.assertEqual(stat, {p.name:p.stat().st_mtime_ns for p in out.glob('*.png')})
        for i in (0,1,3): self.assertEqual((out/f'f{i:04d}.png').read_bytes(), originals[f'f{i:04d}.png'])
        self.assertEqual(originals, {p.name:p.read_bytes() for p in self.directory.iterdir()})
        record = json.loads((out/'complete.json').read_text())
        self.assertEqual(record['frame_source']['identity'], 'test-source')
        self.assertNotIn('environment', record)
        self.assertEqual(record['frames'], 4)

    def test_preview_preserves_actual_tail_eof_and_seek_contents(self):
        for i in range(3): self.write(i, 3 if i==1 else 4)
        out, first = self.output(0,1)
        self.assertEqual(first,0)
        self.assertEqual(len(list(out.glob('*.png'))),3)
        self.assertFalse((out/'f0003.png').exists())
        self.assertEqual((out/'f0002.png').read_bytes(), (self.directory/'f0002.png').read_bytes())
        with self.assertRaisesRegex(ValueError, 'f0003'): self.output()
        # Readiness interval may start after absent earlier frames.
        (self.directory/'f0000.png').unlink()
        out, first = self.output(1,2)
        self.assertEqual(first,0)
        self.assertEqual(len(list(out.glob('*.png'))),2)
        self.assertEqual((out/'f0001.png').read_bytes(), (self.directory/'f0002.png').read_bytes())

    def test_complete_source_keeps_extra_actual_eof_frames(self):
        for i in range(5): self.write(i,3 if i==2 else 4)
        out,_=self.output()
        self.assertEqual(len(list(out.glob('*.png'))),5)
        self.assertEqual((out/'f0004.png').read_bytes(), (self.directory/'f0004.png').read_bytes())

    def test_trns_effective_format_and_unsupported_mixed_domain(self):
        for i in range(4): self.write(i,4)
        self.write(1,3,before=[(b'tRNS',b'\0\x01'*3)])
        self.assertEqual(self.output(),(self.directory,0))  # all decode RGBA
        self.write(2,3)
        with self.assertRaisesRegex(ValueError,'tRNS'): self.output()
        self.assertEqual(list(self.root.iterdir()),[self.directory])

    def test_bad_source_and_interrupted_conversion_cannot_complete(self):
        for i in range(4): self.write(i,3 if i==2 else 4)
        (self.directory/'f0003.png').write_bytes(b'bad')
        with self.assertRaises(ValueError): self.output()
        self.assertEqual(list(self.root.iterdir()),[self.directory])
        self.write(3)
        with mock.patch.object(frame_source,'opaque_rgb_to_rgba',side_effect=RuntimeError('interrupted')):
            with self.assertRaisesRegex(RuntimeError,'interrupted'): self.output()
        self.assertEqual(list(self.root.glob('rgba8-*/complete.json')),[])
        out,_=self.output()
        self.assertTrue((out/'complete.json').is_file())

    def test_derived_tamper_rebuilt_source_mutation_changes_key_extra_eof_rejected(self):
        for i in range(4): self.write(i,3 if i==2 else 4)
        out,_=self.output()
        good=(out/'f0000.png').read_bytes()
        (out/'f0000.png').write_bytes((out/'f0001.png').read_bytes())
        self.assertEqual(self.output()[0],out)
        self.assertEqual((out/'f0000.png').read_bytes(),good)
        (out/'f0004.png').write_bytes(good)
        with self.assertRaisesRegex(ValueError,'EOF'): self.output()
        self.write(0,3)
        new,_=self.output()
        self.assertNotEqual(new,out)

    def test_incomplete_marker_cannot_recertify_wrong_pixels(self):
        for i in range(4): self.write(i,3 if i==2 else 4)
        out,_=self.output()
        good=(out/'f0000.png').read_bytes()
        (out/'f0000.png').write_bytes((out/'f0001.png').read_bytes())
        marker=out/'complete.json'
        record=json.loads(marker.read_text());record.pop('sha256')
        marker.write_text(json.dumps(record))
        self.assertEqual(self.output()[0],out)
        self.assertEqual((out/'f0000.png').read_bytes(),good)

    def test_source_change_during_conversion_prevents_completion(self):
        for i in range(4): self.write(i,3 if i==2 else 4)
        original=gfx_cli.opaque_rgb_to_rgba
        def change(*args,**kwargs):
            out=original(*args,**kwargs)
            self.write(0,3)
            return out
        with mock.patch.object(frame_source,'opaque_rgb_to_rgba',side_effect=change):
            with self.assertRaisesRegex(ValueError,'changed'): self.output()
        self.assertEqual(list(self.root.glob('rgba8-*/complete.json')),[])

    def test_warm_source_change_while_waiting_for_page_lock_is_rejected(self):
        for i in range(4): self.write(i,3 if i==2 else 4)
        out,_=self.output()
        validated=threading.Event()
        original=PngSequence._validated
        def signal_validated(source,*args,**kwargs):
            result=original(source,*args,**kwargs)
            validated.set()
            return result
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            with frame_source.fstxn.file_lock(out/'complete.json'):
                with mock.patch.object(PngSequence,'_validated',signal_validated):
                    future=pool.submit(self.output)
                    self.assertTrue(validated.wait(3))
                    self.write(0,3)
            with self.assertRaisesRegex(ValueError,'changed'):future.result(timeout=3)
        self.assertTrue((out/'complete.json').exists())  # old snapshot, never consumed

    def test_warm_eof_append_while_waiting_for_page_lock_is_rejected(self):
        for i in range(4): self.write(i,3 if i==2 else 4)
        out,_=self.output()
        validated=threading.Event();original=PngSequence._validated
        def signal_validated(source,*args,**kwargs):
            result=original(source,*args,**kwargs);validated.set();return result
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            with frame_source.fstxn.file_lock(out/'complete.json'):
                with mock.patch.object(PngSequence,'_validated',signal_validated):
                    future=pool.submit(self.output)
                    self.assertTrue(validated.wait(3));self.write(4)
            with self.assertRaisesRegex(ValueError,'changed'):future.result(timeout=3)

    def test_uniform_unknown_unsafe_is_unchanged_and_unconsumed_prefix_falls_back(self):
        for i in range(4):self.write(i,3,before=[(b'vpAG',b'unknown')])
        self.assertEqual(self.output(),(self.directory,0))
        for i in range(1,4):self.write(i,3 if i==2 else 4)
        out,number=self.output(1)
        self.assertEqual(number,0)
        self.assertEqual(len(list(out.glob('*.png'))),3)
        with self.assertRaisesRegex(ValueError,'unsafe-to-copy'):self.output()

    def test_complete_page_random_seeks_reuse_original_numbering(self):
        self.source=PngSequence(self.directory,'long-source',60,30,8,4)
        for i in range(60): self.write(i,3 if i in (12,32,57) else 4)
        first=None
        for start in range(0,60,5):
            out,number=self.output(start)
            if first is None:first=out
            self.assertEqual(out,first)
            self.assertEqual(number,start)
        self.assertEqual(len(list(self.root.glob('rgba8-*'))),1)
        self.assertEqual(len(list(first.glob('*.png'))),60)

    def test_invalid_unconsumed_prefix_does_not_block_partial_tail(self):
        for i in range(4): self.write(i,3 if i==2 else 4)
        (self.directory/'f0000.png').write_bytes(b'not consumed by seek')
        out,number=self.output(1)
        self.assertEqual(number,0)
        self.assertEqual(len(list(out.glob('*.png'))),3)
        self.assertEqual((out/'f0000.png').read_bytes(),(self.directory/'f0001.png').read_bytes())

    def test_concurrent_consumers_share_one_complete_derivative(self):
        for i in range(4): self.write(i,3 if i==2 else 4)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda _:self.output(),range(2)))
        self.assertEqual(results[0],results[1])
        self.assertEqual(len(list(self.root.glob('rgba8-*/complete.json'))),1)

    @unittest.skipUnless(os.environ.get('VTX_TEST_FFMPEG'),'set VTX_TEST_FFMPEG')
    def test_independent_ffmpeg_rgba_pixels_and_pts(self):
        for i in range(4): self.write(i,3 if i==2 else 4)
        out,_=self.output()
        decoded=[]
        for directory in (self.directory,out):
            decoded.append(subprocess.check_output([os.environ['VTX_TEST_FFMPEG'],'-v','error',
                           '-framerate','30','-i',str(directory/'f%04d.png'),'-pix_fmt','rgba','-f','rawvideo','-']))
        self.assertEqual(decoded[0],decoded[1])
        self.assertEqual(len(decoded[0]),4*8*4*4)

if __name__=='__main__': unittest.main()
