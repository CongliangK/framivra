"""Shared isolated QC fixtures; contains no discoverable TestCase."""
import contextlib
import io
import json
import types
from pathlib import Path
from vtx.inspection import qc

class _FakeContext:
    def __init__(self, root, *, enabled=False, loud=-14, tp=-2, energy=-25,
                 covered=False, dry=False, strict=False, metadata=None):
        self.root = root
        self.out = root / 'out'; self.out.mkdir()
        self.cache = root / 'cache'; self.cache.mkdir()
        (self.out / 'final.mp4').write_bytes(b'mocked movie')
        (self.cache / 'vseg').mkdir()
        (self.cache / 'vseg/seg0_000000_aaaaaaaaaaaa.mp4').write_bytes(b'mocked segment')
        (self.cache / 'vseg/list.txt').write_text("file 'seg0_000000_aaaaaaaaaaaa.mp4'\n")
        self.fps = 30; self.spf = 1 / 30; self.dur = 24
        self.w = 1920; self.h = 1080
        self.strict = strict; self.dry = dry
        self.energy = energy; self.loud = loud; self.tp = tp
        self.metadata = metadata or {}
        self.tl = {
            'video_tracks': [
                {'name': 'bottom', 'kind': 'aroll', 'clips': [
                    {'src': 'b', 'src_in': 10, 'dst_in': 10, 'dur': 14},
                    {'src': 'a', 'src_in': 0, 'dst_in': 0, 'dur': 10}]},
                {'name': 'over', 'kind': 'aroll', 'clips': [
                    {'src': 'a', 'src_in': 0, 'dst_in': 2, 'dur': 2},
                    {'src': 'a', 'src_in': 0, 'dst_in': 5, 'dur': 2, 'scale': 0.5}]},
                {'name': 'gfx', 'kind': 'gfx', 'clips': [
                    {'src': 'gfx/page.html', 'dst_in': 10, 'dur': 4}]}],
            'subtitles': [{'in': 0, 'out': 24}] if covered else [],
            'audio_tracks': [{'name': 'voice', 'events': [{'src': 'a', 'in': 0, 'out': 24}]}]}
        self.receipts = []; self.commands = []
        if enabled:
            self.diagnostics = []

    def receipt(self, *args, **kwargs):
        self.receipts.append((args, kwargs))

    def src_path(self, src):
        return self.root / (src + '.mp4')

    def crop169(self, src):
        return 'crop=1920:1080'

    def probe(self, src):
        return {'w': 1920, 'h': 1080}

    def run(self, cmd, **kwargs):
        self.commands.append(tuple(cmd))
        if cmd[0] == 'ffprobe':
            data = {'streams': [{'nb_frames': str(self.dur), 'width': self.w, 'height': self.h}],
                    'format': {'duration': self.dur * self.spf}}
            if '-count_frames' in cmd:
                data = {'streams': [{'nb_read_frames': str(self.dur)}]}
            data.update(self.metadata)
            return types.SimpleNamespace(stdout=json.dumps(data), stderr='')
        if 'volumedetect' in cmd:
            return types.SimpleNamespace(stdout='', stderr=(
                f'mean_volume: {self.energy} dB' if self.energy is not None else ''))
        if 'rawvideo' in cmd:
            frame = round(float(cmd[cmd.index('-ss') + 1]) * self.fps) if '-ss' in cmd else 0
            source = cmd[cmd.index('-i') + 1]
            overlay = 2 <= frame < 4 or 5 <= frame < 7 or 10 <= frame <= 13
            intensity = 200 if source.endswith('final.mp4') and overlay else 100
            Path(cmd[-1]).write_bytes(bytes([intensity]) * 1296)
            return types.SimpleNamespace(stdout='', stderr='')
        raise AssertionError(cmd)


def _run_qc(context):
    output = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(output):
        try:
            qc.qc(context)
        except SystemExit as error:
            code = error.code
    report_path = context.out / 'qc_report.json'
    report = json.loads(report_path.read_text()) if report_path.exists() else None
    return code, output.getvalue(), report
