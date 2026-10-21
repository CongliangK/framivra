"""Fresh synthetic timelines and per-test temporary roots; no TestCases."""
import contextlib
import copy
import io
from types import SimpleNamespace
from unittest.mock import patch
from tests import _support


def timeline():
    return {'version': 2, 'project': {'fps': 30, 'width': 160, 'height': 90,
            'duration_frames': 90}, 'sources': [], 'video_tracks': [],
            'audio_tracks': [], 'subtitles': []}


class StageFixture:
    """Shared stage context only; test obligations stay in their owning TestCases."""

    def setUp(self):
        self.root = _support.temporary_root(self)
        self.tl = timeline()
        self.tl['sources'] = [{'id': 's', 'path': 's.wav'}]
        (self.root/'s.wav').touch()
        self.tl['video_tracks'] = [{'name':'v', 'kind':'aroll', 'clips':[
            {'src':'s', 'src_in':0, 'src_out':30, 'dst_in':0, 'dur':30}]}]
        self.tl['audio_tracks'] = [{'name':'voice', 'events':[
            {'src':'s', 'in':0, 'out':30, 'volume':1}]}]
        self.tl['subtitles'] = [{'in':0, 'out':30, 'text':'hello'}]

    def context(self, tl, enabled=True):
        return SimpleNamespace(tl=copy.deepcopy(tl), diagnostics=[] if enabled else None,
            root=self.root, out=self.root, cache=self.root, dur=90, fps=30, spf=1/30,
            dry=True, subtitles_errors=[], receipt=lambda *a, **k:None,
            src_path=lambda sid:self.root/'s.wav',
            probe=lambda path:{'fps':30,'has_video':True,'has_audio':True,'nb_frames':90})

    def run_stage(self, c, stage):
        from vtx import stages
        out=io.StringIO(); error=None
        with contextlib.redirect_stdout(out), patch('vtx.stages._plugin_hooks.run_probe_source',return_value=None):
            try:
                getattr(stages, stage)(c)
            except BaseException as caught:
                error=caught
        return out.getvalue(), error
