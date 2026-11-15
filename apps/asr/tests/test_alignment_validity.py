"""Invalid model alignment must not become apparently successful reordered SRT."""
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import asr_cli
import client
import independence
import sidecar_server as server


class AlignmentValidity(unittest.TestCase):
    def test_accepts_shared_token_windows_and_monotone_chunk_boundary(self):
        words = [{"w": "a", "s": 0.0, "e": 1.0},
                 {"w": "b", "s": 0.0, "e": 1.0},
                 {"w": ".", "s": 0.0, "e": 1.0},
                 {"w": "c", "s": 179.9, "e": 180.1},
                 {"w": "d", "s": 180.1, "e": 200.001}]
        before = [dict(w) for w in words]
        server._validate_word_times(words, 200.0)
        self.assertEqual(words, before)
        server._validate_word_times([], None)  # Existing full-mismatch semantics.

    def test_rejects_invalid_windows_without_repair(self):
        for start, end in [(float("nan"), 1), (0, float("inf")), (-0.01, 1),
                           (1, 0), (0, 2.002)]:
            with self.subTest(start=start, end=end):
                with self.assertRaisesRegex(server.RequestError, "时间戳无效") as error:
                    server._validate_word_times([{"w": "a", "s": start, "e": end}], 2)
                self.assertEqual(error.exception.status, 409)
        for duration in (None, float("nan"), float("inf"), -1):
            with self.subTest(duration=duration):
                with self.assertRaisesRegex(server.RequestError, "无法核实音频时长"):
                    server._validate_word_times([{"w": "a", "s": 0, "e": 1}], duration)

    def test_real_chunk_regression_rejected_over_http_before_srt_writes(self):
        # Actual failed synthetic-long experiment: first chunk's aligner ran past
        # its extent, then the next chunk restarted at its correct global offset.
        items = [SimpleNamespace(text="立", start_time=201.28, end_time=201.28),
                 SimpleNamespace(text="这", start_time=176.19, end_time=176.35)]
        words, degraded = server.build_words("立这", [(x.text, x.start_time, x.end_time) for x in items])
        self.assertEqual(degraded, [])  # Text matching alone is not temporal validity.
        with self.assertRaises(server.RequestError):
            server._validate_word_times(words, 214.584)
        model = mock.Mock()
        model.transcribe.return_value = [SimpleNamespace(text="立这", time_stamps=SimpleNamespace(items=items))]
        holder = mock.Mock(aligner_path="fixture-aligner")
        holder.acquire.return_value = model
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "audio.wav"
            audio.write_bytes(b"fixture; duration is explicitly mocked")
            output = root / "out.srt"
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            port = httpd.server_address[1]
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                cfg = {"asr": {"enabled": True, "base_url": "http://127.0.0.1:%d" % port}}
                args = SimpleNamespace(input=str(audio), output=str(output))
                with mock.patch.object(server, "_HOLDER", holder), \
                        mock.patch.object(server, "_probe_duration", return_value=214.584), \
                        mock.patch.object(client, "ensure_sidecar", return_value=True), \
                        mock.patch.object(independence, "attribute_endpoint", return_value={"port": port}), \
                        mock.patch.object(client, "extract_wav16k", return_value=str(audio)), \
                        mock.patch.object(asr_cli, "_tmp_dir_for", return_value=root / "extract"), \
                        mock.patch.object(asr_cli.srt_out, "write_file") as write_srt, \
                        mock.patch.object(asr_cli, "_audit_report_path") as audit_path:
                    with self.assertRaisesRegex(client.ClientError, "时间戳无效") as error:
                        asr_cli.cmd_srt(args, cfg)
                    self.assertEqual(error.exception.status, 409)
                    write_srt.assert_not_called()
                    audit_path.assert_not_called()
                holder.release.assert_called_once()
                self.assertFalse(output.exists())
                self.assertEqual(list(root.glob("*.asr_audit_*.json")), [])
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
