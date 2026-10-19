"""Explicit ordinary-file tests for same-transaction PNG fact reuse."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from vtx import frame_source, gfx_cli
from vtx.frame_source import PngSequence
from tests.test_png_format_stability import png, sample


class TestPreparationReuse(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / 'source'
        self.directory.mkdir()
        self.source = PngSequence(self.directory, 'fixture', 4, 30, 8, 4)
        for i in range(4):
            self.write(i)

    def write(self, frame, *, seed=None, width=8):
        data = png(sample(width, 4, 3 if frame == 2 else 4,
                          frame if seed is None else seed), width,
                   3 if frame == 2 else 4, filters=(0, 1, 3, 4))
        self.path(frame).write_bytes(data)
        return data

    def path(self, frame):
        return self.directory / f'f{frame:04d}.png'

    def warm_mutation(self, action):
        output = self.source.input_args()
        marker = Path(output[-1]).parent / gfx_cli.COMPLETE_FILE
        marker_before = marker.read_bytes()
        original = frame_source.inspect_page
        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            action()
            return result
        with mock.patch.object(frame_source, 'inspect_page', side_effect=mutate):
            with self.assertRaises(ValueError):
                self.source.input_args()
        self.assertEqual(marker.read_bytes(), marker_before)

    def test_reuse_is_only_second_gate_and_not_a_persistent_cache(self):
        with mock.patch.object(frame_source, 'png_invalid_reason',
                               wraps=gfx_cli.png_invalid_reason) as parse:
            self.source.input_args()
            self.assertEqual(parse.call_count, 4)
            parse.reset_mock()
            self.source.input_args()
            self.assertEqual(parse.call_count, 4)
        self.path(1).write_bytes(b'corrupt between consumers')
        with self.assertRaisesRegex(ValueError, 'not ready'):
            self.source.input_args()

    def test_homogeneous_and_require_ready_still_use_full_parser(self):
        self.path(2).write_bytes(png(sample(8, 4, 4, 2), 8, 4))
        with mock.patch.object(frame_source, 'png_invalid_reason',
                               wraps=gfx_cli.png_invalid_reason) as parse:
            self.source.require_ready()
            self.assertEqual(parse.call_count, 4)
            output = self.source.input_args()
            self.assertEqual(parse.call_count, 8)
        self.assertEqual(Path(output[-1]).parent, self.directory)
        self.assertEqual(list(self.root.iterdir()), [self.directory])

    def test_valid_source_mutation_and_same_length_crc_change_are_rejected(self):
        self.warm_mutation(lambda: self.write(0, seed=900))
        self.source.input_args()  # explicit rerun may build a new valid derivative
        def crc_change():
            data = self.path(0).read_bytes()
            self.path(0).write_bytes(data[:40] + bytes([data[40] ^ 1]) + data[41:])
        self.warm_mutation(crc_change)

    def test_truncation_dimensions_and_missing_current_are_rejected(self):
        self.warm_mutation(lambda: self.path(0).write_bytes(self.path(0).read_bytes()[:40]))
        self.write(0)
        self.warm_mutation(lambda: self.write(0, width=7))
        self.write(0)
        self.warm_mutation(lambda: self.path(0).unlink())
        self.write(0)
        self.assertTrue(Path(self.source.input_args()[-1]).parent.is_dir())

    def test_eof_append_after_derivative_check_still_rejects(self):
        self.warm_mutation(lambda: self.write(4))

    def test_environment_is_checked_again_and_digest_drift_rejects(self):
        import dataclasses
        original = self.source._validated(0, 4)
        expected = [digest for _, digest, _ in original]
        changed = list(expected)
        changed[1] = '0' * 64
        checker = mock.Mock(side_effect=[expected, changed])
        source = dataclasses.replace(self.source, environment_check=checker)
        with self.assertRaisesRegex(ValueError, 'digest changed'):
            source.input_args()
        self.assertEqual(checker.call_count, 2)
        self.assertEqual(list(self.root.glob('rgba8-*/complete.json')), [])

    def test_read_error_at_second_gate_fails_without_recertifying(self):
        previous = self.source._validated(0, 4)
        original = Path.open
        def unreadable(path, *args, **kwargs):
            if path == self.path(0):
                raise PermissionError('synthetic read error')
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, 'open', unreadable):
            with self.assertRaisesRegex(ValueError, 'synthetic read error'):
                self.source._validated(0, 4, _previous=previous)

    def test_reread_is_bounded_and_growth_is_rejected(self):
        previous = self.source._validated(0, 4)
        original = Path.open
        opened = []
        class GrowingStream:
            def __init__(self, stream):
                self.stream = stream
                self.read_bytes = 0
                self.requests = []
            def __enter__(self): return self
            def __exit__(self, *args): self.stream.close()
            def fileno(self): return self.stream.fileno()
            def read(self, size):
                self.requests.append(size)
                data = self.stream.read(size)
                if not data:
                    data = b'x' * size  # continuously growing mocked stream
                self.read_bytes += len(data)
                return data
        def grow(path, *args, **kwargs):
            stream = original(path, *args, **kwargs)
            if path == self.path(0):
                stream = GrowingStream(stream)
                opened.append(stream)
            return stream
        with mock.patch.object(Path, 'open', grow), \
             mock.patch.object(frame_source, 'png_invalid_reason',
                               wraps=gfx_cli.png_invalid_reason) as parse:
            with self.assertRaisesRegex(ValueError, 'digest changed'):
                self.source._validated(0, 4, _previous=previous)
        self.assertEqual(parse.call_count, 0)
        self.assertEqual(opened[0].read_bytes, self.path(0).stat().st_size + 1)
        self.assertLessEqual(max(opened[0].requests), 65536)


if __name__ == '__main__':
    unittest.main()
