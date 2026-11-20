"""ASR local media tools preserve diagnostics and failure without an inference server."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import client


class AudioInputDiagnostics(unittest.TestCase):
    def test_bad_media_keeps_utf8_path_and_existing_failure_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / '终😀.mp4'
            source.write_bytes(b'not a container')
            output = root / 'extract'
            message = f'Cannot open {source.name}: invalid input'.encode('utf-8') + b'\xff\n'
            run = subprocess.run

            # A stdlib-only tool double sends real pipe bytes, including one
            # invalid byte. Simulate a legacy default independently of the host.
            def tool(cmd, **options):
                self.assertIn(cmd[0], ('ffmpeg', 'ffprobe'))
                self.assertIn(str(source), cmd)
                options.setdefault('encoding', 'ascii')
                result = run([sys.executable, '-c',
                              f'import sys; sys.stderr.buffer.write({message!r}); sys.exit(1)'],
                             timeout=30, **options)
                self.assertEqual(result.stderr, message.decode('utf-8', errors='replace'))
                return result

            with mock.patch.object(client.subprocess, 'run', side_effect=tool) as media_tool:
                with self.assertRaisesRegex(RuntimeError, r'Cannot open 终😀\.mp4: invalid input�'):
                    client.extract_wav16k(source, output, 'fixture')
                self.assertIsNone(client.probe_duration_s(source))
            self.assertEqual([args.args[0][0] for args in media_tool.call_args_list], ['ffmpeg', 'ffprobe'])
            self.assertEqual(list(output.iterdir()), [])
